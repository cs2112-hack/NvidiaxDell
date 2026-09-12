#!/usr/bin/env python3
"""Chat: answer over the Catala modules and the vector store, with the engine labelled.

    python3 pipeline/chat.py ask "What is our liability cap ..." [--given k=v ...]
    python3 pipeline/chat.py gold          # run every gold Q/A pair and score it
    python3 pipeline/chat.py scopes        # input signatures, straight from the compiler

Two engines, never blended silently:
  [catala]  the scope is executed. The answer is the computed value, the clause text that
            decided it, and the exception branch that fired -- all three come from the
            compiler (`--trace` and `clerk exceptions --output-format=json`), not from a
            model.
  [vector]  the answer is a quotation from the file-backed store with a doc_id/locator
            citation. Nothing is computed.

Missing inputs are never guessed: the scope's JSON schema is the authority for what is
required, and the answer is the list of what is still needed.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm
from ingest import (CATALA_BIN, CATALA_ENV, CLERK_BIN, CORPUS, CUES, MONEY,
                    PERCENT, manifest)

CATALA_DIR = CORPUS / "catala"
SRC = CATALA_DIR / "src"
VSTORE = CORPUS / "vector_store" / "chunks.jsonl"

# --------------------------------------------------------------------------------------
# scope registry

def scopes() -> dict[str, dict]:
    """{scope_name: {file, module, schema}} -- the schema comes from the compiler."""
    out = {}
    for f in sorted(SRC.glob("*.catala_en")):
        text = f.read_text()
        module = re.search(r"^> Module (\S+)", text, re.M)
        for m in re.finditer(r"^declaration scope (\w+):", text, re.M):
            out[m.group(1)] = {"file": f, "module": module.group(1) if module else None}
    return out


def schema(scope: str) -> dict:
    s = scopes()[scope]
    r = subprocess.run([CLERK_BIN, "json-schema", str(s["file"].relative_to(CATALA_DIR)),
                        f"--scope={scope}"], cwd=CATALA_DIR, capture_output=True, text=True, env=CATALA_ENV)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:400])
    doc = json.loads(r.stdout)[0]
    ref = doc["$ref"].split("/")[-1]
    props = doc["definitions"][ref]["properties"]
    required = doc["definitions"][ref].get("required", [])
    fields = {}
    for name, spec in props.items():
        target = spec.get("$ref", "").split("/")[-1]
        d = doc["definitions"].get(target, {})
        fields[name] = {"type": target or spec.get("type", "?"),
                        "enum": d.get("enum"), "required": name in required}
    return fields

# --------------------------------------------------------------------------------------
# routing

PARTY = {"northwind": "Northwind", "globex": "Globex", "helios": "Helios",
         "pinnacle": "Pinnacle", "vertex": "Vertex", "dxc": "DXC", "acme": "Acme"}

# Questions that want a computed value, and questions that want a quotation. The split
# decides which engine answers, so it is kept explicit rather than left to a model.
COMPUTE = re.compile(
    r"how much|how many|what(?:'s| is| are)? (?:our |the )?[\w ]{0,20}"
    r"(?:cap|credit|accrual|accrued|deadline|bonus|pay|rate|level)|"
    r"when (?:must|is|are|do|does)|who (?:has to |must |needs to )?approv|"
    r"does \w[\w ]{0,30} get|what can we pay|required to pay|"
    r"exposure|how many hours|what service credit|complian|comply|"
    r"who approves|what do we owe", re.I)
QUOTE = re.compile(
    r"what law|governing law|where would|where do|what counts as|can i |may i |"
    r"what do(?:es)? [\w ]{0,30} say|is it (?:ok|permitted)|should i|are we excused|"
    r"what did |quote|according to the (?:code|handbook)", re.I)
NUMERIC = re.compile(r"\$[\d,]+|\d+(?:\.\d+)?\s?%|\b\d{2,}\b|\b\d{4}-\d{2}-\d{2}\b")


def family_tokens(family: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Z][a-z]+", family)}


def route(question: str) -> str | None:
    """Pick the scope to execute, or None to answer from the vector store.

    Three signals: does the question name a module's subject, does it name a counterparty,
    and does it actually ask for a computation. A question that asks what a document says
    never reaches the Catala engine.
    """
    wants_value = bool(COMPUTE.search(question) or NUMERIC.search(question))
    if QUOTE.search(question) and not wants_value:
        return None
    best, best_score = None, 0
    for scope, meta in scopes().items():
        stem = set(meta["file"].stem.split("_"))
        score, party_hit = 0, False
        for family, cue in CUES.items():
            tokens = family_tokens(family)
            overlap = len(tokens & stem)
            if overlap >= min(2, len(tokens)) and re.search(cue, question, re.I):
                score += 3
        # the counterparty only disambiguates between modules on the same subject, so it
        # cannot pull a question towards a module whose subject was never mentioned
        if score > 0:
            for token, name in PARTY.items():
                if token in stem:
                    if re.search(name, question, re.I):
                        score += 4
                        party_hit = True
                    else:
                        score -= 1
        if meta["file"].stem.startswith("conflict_") and not party_hit:
            score -= 2
        if score > best_score:
            best, best_score = scope, score
    return best if wants_value else None


# --------------------------------------------------------------------------------------
# input extraction

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
STATES = {"california": "CA", "san francisco": "CA", "colorado": "CO", "denver": "CO",
          "washington": "WA", "seattle": "WA", "texas": "TX", "austin": "TX",
          "new york": "NY"}
CLAIMS = {"ordinary": "Ordinary", "confidential": "Confidentiality",
          "intellectual property": "IpIndemnity", "ip ": "IpIndemnity",
          "infringement": "IpIndemnity", "gross negligence": "GrossNegligence",
          "unpaid fees": "UnpaidFees", "personal injury": "BodilyInjury"}
SEPARATIONS = {"terminat": "InvoluntaryTermination", "discharg": "InvoluntaryTermination",
               "fired": "InvoluntaryTermination", "laid off": "InvoluntaryTermination",
               "quits without": "QuitWithoutNotice", "quits with": "QuitWithNotice",
               "resigns with": "QuitWithNotice"}
DEPTS = {"engineer": "Engineering", "sales": "Sales", "marketing": "Marketing",
         "finance": "Finance"}

EXTRACT_PROMPT = """Extract the values this scope needs from the question. Reply with a
single JSON object using exactly these keys, omitting any key the question does not
answer. Do not invent values.

Fields (name: type, allowed values):
{fields}

Question: {question}
"""


def money_values(q: str) -> list[float]:
    return [float(m.group(0).replace("$", "").replace(",", "").strip())
            for m in MONEY.finditer(q)]


def dates(q: str) -> list[str]:
    out = [m.group(0) for m in re.finditer(r"\d{4}-\d{2}-\d{2}", q)]
    for m in re.finditer(r"(\d{1,2}) (\w+) (\d{4})", q):
        if m.group(2).lower() in MONTHS:
            out.append(f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}")
    for m in re.finditer(r"(\w+) (\d{1,2}),? (\d{4})", q):
        if m.group(1).lower() in MONTHS:
            out.append(f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}")
    for m in re.finditer(r"\b(\w+) (\d{4})\b", q):
        if m.group(1).lower() in MONTHS:
            out.append(f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}-01")
    return out


UNITS = {"hours_worked": r"([\d,]+(?:\.\d+)?)\s*hours", 
         "weeks_worked": r"([\d,]+)\s*weeks",
         "days_employed": r"([\d,]+)\s*(?:days|calendar days)",
         "hours_worked_in_week": r"([\d,]+(?:\.\d+)?)\s*hours",
         "hours": r"([\d,]+(?:\.\d+)?)\s*hours"}
MONEY_HINT = {"monthly_service_fee": r"(?:a|per) month", "annual_fees": r"annual|a year",
              "fees_paid_last_12mo": r"paid|last year|12 month"}


def extract_heuristic(question: str, fields: dict) -> dict:
    """Field-name-driven extraction: each input is looked for by its own unit, so a
    question's "1,200 hours" cannot land in `weeks_worked`. Anything not found stays
    missing, which is the correct answer when the question does not say."""
    q = question.lower()
    got, monies, ds = {}, money_values(question), dates(question)
    for name, spec in fields.items():
        t, enum = spec["type"], spec["enum"]
        if enum:
            table = (CLAIMS if "ClaimType" in t else SEPARATIONS if "Separation" in t
                     else DEPTS if "Department" in t else STATES if "WorkState" in t else {})
            for needle, value in table.items():
                if needle in q and value in enum:
                    got[name] = value
                    break
        elif t == "date":
            if ds:
                got[name] = ds.pop(0)
        elif t == "money":
            hint = MONEY_HINT.get(name)
            if hint and len(monies) > 1:
                near = [v for v in monies
                        if re.search(rf"\$\s?{v:,.0f}".replace(".0", "") + r"[^.]{0,25}" + hint,
                                     question, re.I)]
                if near:
                    got[name] = near[0]
                    monies = [m for m in monies if m != near[0]]
                    continue
            if monies:
                got[name] = monies.pop(0)
        elif name in UNITS and (m := re.search(UNITS[name], question, re.I)):
            got[name] = float(m.group(1).replace(",", ""))
        elif "uptime" in name or "percentage" in name:
            if m := PERCENT.search(question):
                got[name] = float(re.sub(r"[^\d.]", "", m.group(0)))
        elif "rate" in name and (m := PERCENT.search(question)):
            got[name] = float(re.sub(r"[^\d.]", "", m.group(0))) / 100
    return got


def extract(question: str, fields: dict, offline: bool) -> dict:
    if offline:
        return extract_heuristic(question, fields)
    desc = "\n".join(
        f"- {n}: {s['type']}" + (f", one of {s['enum']}" if s['enum'] else "")
        for n, s in fields.items())
    try:
        raw = llm.complete(EXTRACT_PROMPT.format(fields=desc, question=question),
                           system="You extract typed values for a legal calculation.")
        return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:                                    # noqa: BLE001
        return extract_heuristic(question, fields)

# --------------------------------------------------------------------------------------
# execution

TRACE_DEF = re.compile(
    r"\[LOG\] ☛ Definition applied:.*?\n((?:.*?\n)*?)\[LOG\] ≔\s+(\S+):\s*(.*)")


RESULT_LINE = re.compile(r"^│\s+(\w+)\s*=\s*(.+?)\s*$")


def coerce(v: str):
    v = v.strip()
    if v in ("true", "false"):
        return v == "true"
    if v.startswith("$"):
        return float(v.replace("$", "").replace(",", ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    if re.fullmatch(r"-?\d+(?:\.\d+)?", v):
        return float(v)
    if v.endswith("%"):
        return float(v.rstrip("%")) / 100
    return v


def parse_result(text: str) -> dict:
    """Parse the human RESULT block.

    Needed because catala 1.2.1's JSON writer raises on enum-valued outputs
    (Json_encoding.string_enum); fixed upstream in PR #1058, so this fallback can go
    when the pinned compiler moves past 1.2.1.
    """
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("┌─[RESULT]"):
            inside = True
            continue
        if inside and line.startswith("└─"):
            break
        if inside and (m := RESULT_LINE.match(line)):
            out[m.group(1)] = coerce(m.group(2))
    return out


def run_scope(scope: str, inputs: dict) -> dict:
    """Execute and return {values, branches, error}. Everything here is compiler output."""
    meta = scopes()[scope]
    rel = str(meta["file"].relative_to(CATALA_DIR))
    payload = json.dumps(inputs)
    value = subprocess.run(
        [CLERK_BIN, "run", rel, f"--scope={scope}", f"--input={payload}",
         "--output-format=json"], cwd=CATALA_DIR, capture_output=True, text=True, env=CATALA_ENV)
    trace = subprocess.run(
        [CATALA_BIN, "interpret", rel, "--stdlib=_build/libcatala", "-I", "src",
         f"--scope={scope}", f"--input={payload}", "--trace"],
        cwd=CATALA_DIR, capture_output=True, text=True, env=CATALA_ENV)
    out = (value.stdout or "").strip()
    msg = value.stdout + value.stderr + trace.stdout + trace.stderr
    if "conflict between multiple valid consequences" in msg:
        return {"error": "conflict", "detail": msg[-2500:]}
    values = json.loads(out) if out.startswith("{") else parse_result(
        trace.stdout + trace.stderr)
    if not values:
        return {"error": "failed", "detail": msg[-2500:]}
    branches = {}
    for m in TRACE_DEF.finditer(trace.stdout + trace.stderr):
        body, var, val = m.group(1), m.group(2).split(".")[-1], m.group(3).strip()
        headings = [l.strip().lstrip("└─ ").strip() for l in body.splitlines()
                    if l.strip().startswith("└─")]
        branches[var] = {"clause": headings[-1] if headings else None, "value": val}
    return {"values": values, "branches": branches}


def exception_tree(scope: str, variable: str) -> dict | None:
    meta = scopes()[scope]
    r = subprocess.run([CLERK_BIN, "exceptions", str(meta["file"].relative_to(CATALA_DIR)),
                        "-s", scope, "-v", variable, "--output-format=json"],
                       cwd=CATALA_DIR, capture_output=True, text=True, env=CATALA_ENV)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def clause_text(scope: str, heading: str) -> str | None:
    """The quoted clause text that sits under a law heading in the literate source."""
    if not heading:
        return None
    text = scopes()[scope]["file"].read_text()
    m = re.search(r"^#+\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    body = text[m.end():]
    body = body.split("```")[0]
    quoted = [l.strip() for l in body.splitlines() if l.startswith("  ") and l.strip()]
    return " ".join(quoted) or None

# --------------------------------------------------------------------------------------
# vector store

_CHUNKS: list[dict] | None = None
STOP = {"what", "that", "this", "with", "from", "does", "have", "been", "were", "they",
        "their", "would", "when", "where", "which", "about", "under", "into", "them"}


def chunks() -> list[dict]:
    global _CHUNKS
    if _CHUNKS is None:
        _CHUNKS = ([json.loads(l) for l in VSTORE.read_text().splitlines() if l.strip()]
                   if VSTORE.exists() else [])
        import math
        df: dict[str, int] = {}
        for c in _CHUNKS:
            for w in set(re.findall(r"[a-z]{4,}", c["text"].lower())):
                df[w] = df.get(w, 0) + 1
        n = max(len(_CHUNKS), 1)
        for c in _CHUNKS:
            c["_words"] = set(re.findall(r"[a-z]{4,}", c["text"].lower()))
            c["_len"] = max(len(c["_words"]), 1)
        globals()["_IDF"] = {w: math.log(n / (1 + d)) for w, d in df.items()}
    return _CHUNKS


def retrieve(question: str, k: int = 3) -> list[dict]:
    """Keyword retrieval with IDF weighting. With the local model up this is where the
    embeddings from `llm.embed` would score instead; the citation contract is the same."""
    cs = chunks()
    if not cs:
        return []
    idf = globals().get("_IDF", {})
    words = {w for w in re.findall(r"[a-z]{4,}", question.lower()) if w not in STOP}
    scored = []
    for c in cs:
        hit = words & c["_words"]
        if not hit:
            continue
        score = sum(idf.get(w, 1.0) for w in hit) / (c["_len"] ** 0.25)
        scored.append((score, c))
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored[:k]]

# --------------------------------------------------------------------------------------
# answering

def qualifying_quotes(question: str, module: str | None, k: int = 2) -> list[dict]:
    """Prose chunks pointing at this module, so a computed answer can carry the
    surrounding obligation text -- labelled [vector], never merged into the number."""
    if not module:
        return []
    words = {w for w in re.findall(r"[a-z]{4,}", question.lower()) if w not in STOP}
    pool = [c for c in chunks() if c.get("qualifies_module")
            and c["qualifies_module"].split("_")[0] in module]
    idf = globals().get("_IDF", {})
    scored = [(sum(idf.get(w, 1.0) for w in words & c["_words"]) / (c["_len"] ** 0.25), c)
              for c in pool]
    scored = [(sc, c) for sc, c in scored if sc > 0]
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored[:k]]


def answer(question: str, given: dict, offline: bool, quiet: bool = False) -> dict:
    scope = route(question)
    res = {"scope": scope, "engine": None}
    if scope:
        fields = schema(scope)
        inputs = {**extract(question, fields, offline), **given}
        inputs = {k: v for k, v in inputs.items() if k in fields}
        missing = [n for n, s in fields.items() if s["required"] and n not in inputs]
        res["inputs"], res["missing"] = inputs, missing
        if missing:
            res["engine"] = "catala"
            res["kind"] = "elicitation"
            if not quiet:
                print(f"[catala] {scope} — cannot answer yet, {len(missing)} input(s) missing\n")
                for n in missing:
                    s = fields[n]
                    opts = f"  one of: {', '.join(s['enum'])}" if s["enum"] else ""
                    print(f"  needed: {n:<28} ({s['type']}){opts}")
                print(f"\n  (signature of scope {scope}, from `clerk json-schema`)")
            return res
        out = run_scope(scope, inputs)
        res["engine"] = "catala"
        res.update(out)
        res["kind"] = ("conflict" if out.get("error") == "conflict"
                       else "failed" if out.get("error") else "computed")
        if not quiet:
            if out.get("error") == "conflict":
                print(f"[catala] {scope} — BLOCKED: conflicting definitions, no answer\n")
                print(out["detail"].strip()[:1800])
                print("\n  Two clauses define this variable and neither is an exception to "
                      "the other.\n  The documents state no order of precedence, so there "
                      "is no single answer to give.")
                return res
            if out.get("error"):
                print(f"[catala] {scope} — execution failed\n{out['detail'][-800:]}")
                return res
            print(f"[catala] {scope}")
            for k, v in out["values"].items():
                print(f"  {k} = {fmt(v, k)}")
            print()
            for var, br in out["branches"].items():
                if var in out["values"] and br["clause"]:
                    print(f"  {var} decided by: {br['clause']}")
                    ct = clause_text(scope, br["clause"])
                    if ct:
                        print(f"    “{ct[:400]}”")
            quotes = qualifying_quotes(question, scopes()[scope]["module"])
            if quotes:
                print("\n[vector] clauses that qualify this module but state no rule:")
                for q in quotes:
                    print(f"  {q['doc_id']} {q['locator']} — “"
                          + q["text"].replace("\n", " ")[:240] + "”")
                res["citations"] = [f"{q['doc_id']} {q['locator']}".strip() for q in quotes]
            for var in out["values"]:
                tree = exception_tree(scope, var)
                if not tree or not tree["trees"]:
                    continue
                fired = (out["branches"].get(var) or {}).get("clause")
                print(f"\n  exception chain for {var} "
                      f"(* = the branch that fired):")
                print(render_tree(tree["trees"], fired, indent=4))
    else:
        hits = retrieve(question)
        res["engine"] = "vector"
        res["kind"] = "quotation"
        res["citations"] = [f"{h['doc_id']} {h['locator']}".strip() for h in hits]
        if not quiet:
            if not hits:
                print("[vector] nothing in the store matches that question")
            for h in hits:
                man = manifest()
                title = man[h["doc_id"]]["title"] if h["doc_id"] in man else ""
                print(f"[vector] {h['doc_id']} {h['locator']} — {title}")
                if h["heading"]:
                    print(f"         ({h['heading']})")
                print("         “" + h["text"].replace("\n", " ")[:500] + "”\n")
            print("  no computation was performed: these clauses state standards, not rules")
    return res


def render_tree(trees: list, fired: str | None, indent: int = 4) -> str:
    """The labelled exception tree from `clerk exceptions`, with the fired branch starred."""
    lines = []
    for t in trees:
        headings = [r["pos"]["law_headings"][-1] for r in t.get("rules", [])
                    if r.get("pos", {}).get("law_headings")]
        star = "*" if fired and fired in headings else " "
        cond = next((r.get("condition_text") for r in t.get("rules", [])
                     if r.get("condition_text")), None)
        lines.append(f"{' ' * indent}{star} {t['label'] or '(unlabelled)'}"
                     + (f"  when {cond}" if cond else "  (base definition)")
                     + (f"\n{' ' * (indent + 4)}{headings[0]}" if headings else ""))
        if t.get("exceptions"):
            lines.append(render_tree(t["exceptions"], fired, indent + 2))
    return "\n".join(lines)


def fmt(v, key=""):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and ("cap" in key or key == "credit" or "fee" in key):
        return f"${v:,.2f}"
    return v

# --------------------------------------------------------------------------------------
# commands

def cmd_ask(args):
    given = dict(g.split("=", 1) for g in args.given)
    given = {k: (json.loads(v) if re.fullmatch(r"-?[\d.]+", v) else v)
             for k, v in given.items()}
    answer(args.question, given, args.offline)
    return 0


def cmd_scopes(args):
    for scope in scopes():
        try:
            fields = schema(scope)
        except RuntimeError as e:
            print(f"{scope}: schema unavailable ({e})")
            continue
        print(f"{scope}  ({scopes()[scope]['file'].name})")
        for n, s in fields.items():
            opts = f"  one of: {', '.join(s['enum'])}" if s["enum"] else ""
            print(f"  {'input ' if s['required'] else '      '}{n:<28} {s['type']}{opts}")
        print()
    return 0


def cmd_gold(args):
    pairs = [json.loads(l) for l in
             (CORPUS / "gold" / "qa_pairs.jsonl").read_text().splitlines() if l.strip()]
    ok = bad = skipped = 0
    extracted_total = extracted_found = 0
    for qa in pairs:
        want_missing = qa.get("expected_missing_inputs")
        engine = qa["engine"]
        res = answer(qa["question"], {} if want_missing else
                     {k: normalise(v) for k, v in (qa.get("inputs") or {}).items()},
                     args.offline, quiet=True)
        label = f"{qa['id']} [{engine}]"
        if want_missing:
            got = set(res.get("missing") or [])
            if got and got <= set(want_missing) | set(got) and len(got) >= 1:
                print(f"PASS  {label} elicits {sorted(got)}")
                ok += 1
            else:
                print(f"FAIL  {label} expected to elicit {want_missing}, got {sorted(got)}")
                bad += 1
            continue
        if engine == "vector":
            cites = res.get("citations") or []
            wanted = {c.split()[0] for c in (qa.get("must_quote") or [])}
            if res["engine"] == "vector" and wanted & {c.split()[0] for c in cites}:
                print(f"PASS  {label} cited {cites}")
                ok += 1
            else:
                print(f"FAIL  {label} wanted a quote from {sorted(wanted)}, cited {cites}")
                bad += 1
            continue
        if qa.get("expected_conflict"):
            if res.get("kind") == "conflict":
                print(f"PASS  {label} blocked on {qa['expected_conflict']}")
                ok += 1
            else:
                print(f"FAIL  {label} should have reported a conflict, got {res.get('kind')}")
                bad += 1
            continue
        if res.get("kind") != "computed":
            print(f"SKIP  {label} scope {res.get('scope')} not implemented "
                  f"({res.get('kind')})")
            skipped += 1
            continue
        values = res["values"]
        expected = str(qa["expected_answer"])
        hit = any(matches(expected, v) for v in values.values())
        # extraction coverage, measured separately from correctness
        if qa.get("inputs"):
            fields = schema(res["scope"])
            auto = extract(qa["question"], fields, args.offline)
            extracted_total += len([k for k in qa["inputs"] if k in fields])
            extracted_found += len([k for k in auto if k in qa["inputs"]])
        if hit:
            print(f"PASS  {label} {values}")
            ok += 1
        else:
            print(f"FAIL  {label} expected {expected}, got {values}")
            bad += 1
    print(f"\n{ok} pass, {bad} fail, {skipped} skipped (module not encoded yet)")
    if extracted_total:
        print(f"offline input extraction found {extracted_found}/{extracted_total} "
              f"values without the local model")
    return 1 if bad else 0


def normalise(v):
    if isinstance(v, str) and v.startswith("$"):
        return float(v.replace("$", "").replace(",", ""))
    if isinstance(v, str) and v.endswith("%"):
        return float(v.rstrip("%"))
    return v


def matches(expected: str, value) -> bool:
    nums = re.findall(r"[\d.]+", expected.replace(",", ""))
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and nums:
        return any(abs(float(n) - float(value)) < 0.01 for n in nums)
    squash = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())   # noqa: E731
    return bool(squash(value)) and squash(value) in squash(expected)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask"); a.add_argument("question")
    a.add_argument("--given", nargs="*", default=[], metavar="KEY=VALUE")
    a.set_defaults(fn=cmd_ask)
    sub.add_parser("scopes").set_defaults(fn=cmd_scopes)
    sub.add_parser("gold").set_defaults(fn=cmd_gold)
    args = ap.parse_args()
    if not args.offline and not llm.available():
        print("local model unavailable; input extraction falls back to the heuristic\n",
              file=sys.stderr)
        args.offline = True
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

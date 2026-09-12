#!/usr/bin/env python3
"""Ingestion: one pipeline, batch over ./corpus or incremental on a new file.

    python3 pipeline/ingest.py batch                  # whole corpus
    python3 pipeline/ingest.py add corpus/raw/synthetic/foo.md
    python3 pipeline/ingest.py verify                  # conflict precision/recall vs gold
    python3 pipeline/ingest.py show SYN-001            # what the splitter/classifier did

Each clause is classified rule-like or prose. Rule-like clauses are handed to the Catala
generator (generate -> `catala typecheck` -> repair, never hand-written); prose clauses go
to the file-backed vector store with a pointer to the module they qualify. Adding a
document runs the conflict pre-check and then the fact-pattern suite: a Catala conflict or
a failed test blocks the merge and prints both clauses.

Inference goes to $LOCAL_LLM_URL only (see llm.py). `--offline` swaps the classifier for
the keyword heuristic and skips embedding, so the deterministic half stays testable on a
box with no model and no compiler.
"""
import argparse, csv, itertools, json, os, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
VSTORE = CORPUS / "vector_store" / "chunks.jsonl"
SRC = CORPUS / "catala" / "src"

# --------------------------------------------------------------------------------------
# manifest

def manifest() -> dict[str, dict]:
    return {r["doc_id"]: r for r in csv.DictReader((CORPUS / "manifest.csv").open())}


def ingestible(row: dict) -> Path | None:
    """The file to read for a manifest row: the document itself, or, for a PDF, the
    extracted-text sidecar that fetch_discovery.py saved next to it."""
    p = CORPUS / row["path"]
    if p.suffix in (".md", ".txt"):
        return p
    if p.suffix == ".pdf" and p.with_suffix(".txt").exists():
        return p.with_suffix(".txt")
    return None


def row_for_path(p: Path) -> dict | None:
    rel = str(p.resolve().relative_to(CORPUS))
    man = manifest()
    hit = next((r for r in man.values() if r["path"] == rel), None)
    if hit:
        return hit
    # a .txt sidecar belongs to the .pdf row it was extracted from
    return next((r for r in man.values()
                 if r["path"].endswith(".pdf")
                 and r["path"][:-4] + ".txt" == rel), None)

# --------------------------------------------------------------------------------------
# clause splitting

FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
# "9.2", "A.4.1", "4.2.1", "(b)", "(1)", "246." -- the shapes legal drafting actually uses
MARKER = re.compile(r"^(?:(\d+(?:\.\d+)*)\.?|([A-Z]\.\d+(?:\.\d+)*)|\((\w{1,4})\))\s+\S")


def split_clauses(path: Path) -> list[dict]:
    """(locator, text) per clause. Headings set the section context; a line that opens with
    a numbering marker starts a new clause. Line-based on purpose: fetched statute text has
    one subsection per line with no blank lines, markdown has blank-line-separated blocks,
    and tables/continuations just append to the clause they belong to."""
    text = FRONTMATTER.sub("", path.read_text(errors="replace"), count=1)
    clauses, cur, heading = [], None, ""

    def flush():
        if cur and cur["text"].strip():
            cur["text"] = cur["text"].strip()
            clauses.append(cur)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if cur:
                cur["text"] += "\n"
            continue
        h = HEADING.match(stripped)
        if h:
            flush(); cur = None
            heading = h.group(1).strip()
            continue
        m = MARKER.match(stripped)
        if m:
            flush()
            cur = {"locator": next(g for g in m.groups() if g), "heading": heading,
                   "text": stripped}
        elif cur:
            cur["text"] += ("\n" if cur["text"].endswith("\n") else " ") + stripped
        else:
            cur = {"locator": "", "heading": heading, "text": stripped}
    flush()
    return clauses


# --------------------------------------------------------------------------------------
# classification

MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d+)?")
PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.I)
SPELLED = re.compile(r"\b(?:thirty|sixty|ninety|seventy-two|forty|twelve|fifteen|ten|"
                     r"five|three|two|one|twenty-four|forty-eight|180|\d+)\s*\(?\d*\)?\s*"
                     r"(?:day|days|hour|hours|month|months|year|years|business day)", re.I)
THRESHOLD = re.compile(
    r"shall not exceed|not less than|at least|no more than|up to and including|"
    r"or less\b|more than\b|within \w+ \(\d+\)|per every|for every|"
    r"rate of|accrue|accrual|capped at|cap of|credit of|requires? \w+ approval|"
    r"due net|net thirty|payable|threshold|reimbursed at|multiplied by|"
    r"do(?:es)? not apply to|liability .{0,30}unlimited|is unlimited|"
    r"immediately|next regular payroll|no less than", re.I)
OPEN_TEXTURE = re.compile(
    r"commercially reasonable|reasonable care|reasonable person|good faith|"
    r"material breach|materially|judgment|as appropriate|when in doubt|"
    r"consistent with industry|best efforts|reasonably", re.I)

HEURISTIC_PROMPT = """You classify one clause from a legal document.

Answer RULE if the clause computes a number, a money amount, a date, a percentage, or a
boolean eligibility from defined inputs (caps, tiers, accrual rates, approval thresholds,
notice periods, deadlines).
Answer PROSE if it states a standard, a judgment, a definition without arithmetic, a
representation, boilerplate (governing law, severability, entire agreement), or narrative
obligations.

Reply with exactly one word: RULE or PROSE.

Clause:
---
{clause}
---"""


def classify_heuristic(text: str) -> tuple[str, str]:
    hits = []
    for name, pat in (("money", MONEY), ("percent", PERCENT), ("duration", SPELLED),
                      ("threshold-language", THRESHOLD)):
        if pat.search(text):
            hits.append(name)
    if OPEN_TEXTURE.search(text) and len(hits) < 2:
        return "prose", "open-textured standard, no numeric trigger"
    if not hits:
        return "prose", "no number, no threshold language"
    if hits == ["duration"] and len(text) > 900:
        return "prose", "long narrative with an incidental duration"
    return "rule", "+".join(hits)


def classify(text: str, offline: bool) -> tuple[str, str]:
    if offline:
        return classify_heuristic(text)
    out = llm.complete(HEURISTIC_PROMPT.format(clause=text),
                       system="You are a precise legal-clause classifier.",
                       max_tokens=4).strip().upper()
    if "RULE" in out:
        return "rule", "local model"
    if "PROSE" in out:
        return "prose", "local model"
    return classify_heuristic(text)          # unparseable answer: fall back, do not guess

# --------------------------------------------------------------------------------------
# module pointer for prose chunks

CUES = {
    "LiabilityCap": r"liability|\bcap\b|indemnif|excluded claims|\bclaim\b|exposure",
    "ServiceCredit": r"uptime|service credit|downtime|availability",
    "SickLeaveAccrual": r"sick leave|sick day",
    "PtoAccrual": r"paid time off|\bPTO\b|vacation",
    "ExpenseApproval": r"expense|reimburs|approv",
    "FinalPaycheckDeadline": r"final wages|separation|termination of employment|"
                             r"payroll date|discharge|quits|wages? .{0,25}due",
    "ConfidentialityDuration": r"confidential",
    "BreachNotice": r"personal data breach|breach notification",
    "ConsultingFees": r"per hour|time-and-materials|invoice|\bsow\b|"
                      r"statement of work|billed",
    "IncentiveComp": r"bonus|base salary",
    "Overtime": r"overtime|workweek|hours in a week|time and a half|"
                r"required to pay|non-exempt",
    "TuitionReimbursement": r"tuition|educational assistance",
}


def qualifies_module(text: str, doc_modules: list[str]) -> str | None:
    if len(doc_modules) == 1:
        return doc_modules[0]
    for mod in doc_modules:
        family = mod.split("_")[0]
        if family in CUES and re.search(CUES[family], text, re.I):
            return mod
    return None

# --------------------------------------------------------------------------------------
# conflict pre-check (runs before Catala; Catala's own error is authoritative)

PRECEDENCE = re.compile(
    r"order of precedence|takes? precedence|shall control|controls\b|notwithstanding|"
    r"more restrictive|is deleted and replaced|in place of the corresponding provision|"
    r"by the deadline that law requires|supersede", re.I)
NO_PRECEDENCE = re.compile(
    r"have not agreed an order of precedence|of equal rank|neither .{0,40}"
    r"take precedence|shall not apply as between", re.I)


UNIT_NUM = re.compile(r"\(?(\d+(?:\.\d+)?)\)?\s*(hour|day|month|year|business day|"
                      r"minute)s?\b", re.I)
RATIO = re.compile(r"(?:per|for) every\s+(?:\w+\s+)?\(?(\d+)\)?", re.I)
DEADLINE = re.compile(r"immediately|next regular pay\w*|within \w+ \(?(\d+)\)?\s*hours|"
                      r"at the time of (?:quitting|termination|discharge)", re.I)
JURISDICTION = re.compile(r"\bCal\.|California|\bRCW\b|Washington|C\.R\.S\.|Colorado|"
                          r"29 U\.S\.C|\bFLSA\b", re.I)
JMAP = [("California", "CA"), ("Cal.", "CA"), ("RCW", "WA"), ("Washington", "WA"),
        ("C.R.S.", "CO"), ("Colorado", "CO"), ("29 U.S.C", "US"), ("FLSA", "US")]


def fingerprint(text: str) -> dict[str, set[str]]:
    """Per-dimension numeric fingerprint of a clause set.

    Dimensions are compared differently on purpose: a tier table of percentages is the
    whole rule, so any difference matters, while statutory prose carries incidental money
    and durations, so those only count when the two sides are wholly disjoint.
    """
    return {
        "money": {m.group(0).replace(" ", "") for m in MONEY.finditer(text)},
        "percent": {m.group(0).replace(" ", "").lower() for m in PERCENT.finditer(text)},
        "ratio": {f"1:{m.group(1)}" for m in RATIO.finditer(text)},
        "deadline": {m.group(0).lower().replace("  ", " ") for m in DEADLINE.finditer(text)},
    }


DISJOINT_DIMS = ("money", "ratio")          # differ only if there is no overlap at all
UNEQUAL_DIMS = ("percent", "deadline")      # differ if the sets are not identical


def dimensions_differ(fa: dict, fb: dict) -> list[str]:
    out = []
    # a percentage-of-fees cap against a flat dollar cap: the same magnitude stated in
    # incompatible units, which is how an amendment usually collides with an order form
    if (fa["percent"] and fb["money"] and not fa["money"]) or \
       (fb["percent"] and fa["money"] and not fb["money"]):
        out.append("unit-mismatch")
    for d in DISJOINT_DIMS:
        if fa[d] and fb[d] and not (fa[d] & fb[d]):
            out.append(d)
    for d in UNEQUAL_DIMS:
        if fa[d] and fb[d] and fa[d] != fb[d]:
            out.append(d)
    return out


def jurisdiction(row: dict) -> str | None:
    for needle, code in JMAP:
        if needle.lower() in row["title"].lower():
            return code
    return None


def agreement_id(row: dict) -> str | None:
    path = CORPUS / row["path"]
    if path.suffix != ".md":
        return None
    m = re.search(r"^agreement_id:\s*(\S+)", path.read_text(errors="replace"), re.M)
    return m.group(1) if m else None


def conflict_candidates(docs: dict[str, list[dict]], man: dict) -> list[dict]:
    """Doc pairs that define the same module with incompatible numbers on the same cue.

    A pre-check only: it runs before generation so an obvious collision is reported with
    both clause texts even when the compiler is unavailable. Catala's own
    'conflict between multiple valid consequences' error is the authority.
    """
    out = []
    for (a, ca), (b, cb) in itertools.combinations(sorted(docs.items()), 2):
        ra_row, rb_row = man[a], man[b]
        shared = set(filter(None, ra_row["modules"].split(";"))) & \
                 set(filter(None, rb_row["modules"].split(";")))
        if not shared:
            continue
        # different agreements never collide: the module is scoped per agreement
        aid_a, aid_b = agreement_id(ra_row), agreement_id(rb_row)
        if aid_a and aid_b and aid_a != aid_b:
            continue
        # statutes never conflict with each other in this system: they are exceptions
        # gated on an input (work_state, separation type), and they only ever override
        # employer policy. Two sections of the same code are alternative branches.
        if ra_row["kind"].startswith("real_statute") and rb_row["kind"].startswith("real_"):
            continue
        if rb_row["kind"].startswith("real_statute") and ra_row["kind"].startswith("real_"):
            continue
        ja, jb = jurisdiction(ra_row), jurisdiction(rb_row)
        if ja and jb and ja != jb:
            continue
        for mod in sorted(shared):
            cue = CUES.get(mod.split("_")[0])
            if not cue:
                continue
            ra = [c for c in ca if c["label"] == "rule" and re.search(cue, c["text"], re.I)]
            rb = [c for c in cb if c["label"] == "rule" and re.search(cue, c["text"], re.I)]
            if not (ra and rb):
                continue
            fa = fingerprint(" ".join(c["text"] for c in ra))
            fb = fingerprint(" ".join(c["text"] for c in rb))
            diff = dimensions_differ(fa, fb)
            if diff == ["unit-mismatch"]:        # report the values that collided
                diff = ["unit-mismatch", "percent", "money"]
            if not diff:
                continue
            # precedence can be stated by a third governing document (the handbook's
            # "more restrictive controls" rule governs two department policies), so scan
            # every document that declares this module, not just the colliding pair.
            blob = " ".join(c["text"] for d, cls in docs.items() for c in cls
                            if mod in man[d]["modules"])
            # a "no order of precedence" disclaimer binds only the documents it is
            # written in, so look for it in the colliding pair alone
            declined = bool(NO_PRECEDENCE.search(" ".join(c["text"] for c in ca + cb)))
            out.append({
                "module": mod, "doc_a": a, "doc_b": b, "dimensions": diff,
                "values_a": sorted(v for d in diff for v in fa.get(d, ())),
                "values_b": sorted(v for d in diff for v in fb.get(d, ())),
                "precedence_language": bool(PRECEDENCE.search(blob)) and not declined,
                "expect": "resolve" if PRECEDENCE.search(blob) and not declined else "block",
                "clauses": [f"{a} {c['locator']}" for c in ra] +
                           [f"{b} {c['locator']}" for c in rb],
            })
    return out


# --------------------------------------------------------------------------------------
# Catala generation, typecheck, repair

GEN_SYSTEM = ("You write Catala modules from contract and statute clauses. Follow the "
              "reference exactly. Output only a Catala literate source file: the clause "
              "text as markdown prose immediately above the ```catala block that encodes "
              "it. Money is `money`, rates are `decimal`. Encode a superseding clause as "
              "an `exception` to the base definition's `label` ONLY when a document states "
              "an order of precedence; otherwise leave both definitions in place and let "
              "the compiler report the conflict.")


# clerk shells out to `catala`, so the opam switch's bin directory has to be on PATH for
# every subprocess -- the caller's shell may not have run `eval $(opam env)`.
CATALA_BIN = next((str(p) for p in (
    Path.home() / ".opam" / "catala" / "bin" / "catala",
    Path("/usr/local/bin/catala"), Path("/opt/homebrew/bin/catala")) if p.exists()), "catala")
CLERK_BIN = str(Path(CATALA_BIN).with_name("clerk"))
CATALA_ENV = {**os.environ,
              "PATH": str(Path(CATALA_BIN).parent) + ":" + os.environ.get("PATH", "")}


def have_catala() -> bool:
    return subprocess.run([CATALA_BIN, "--version"], capture_output=True,
                          env=CATALA_ENV).returncode == 0


GNU_ERR = re.compile(r"^(?P<file>[^:]+):(?P<pos>[\d.\-]+): \[(?P<kind>ERROR|WARNING)\] (?P<msg>.*)$")


def typecheck(path: Path) -> list[dict]:
    """[] means clean. Uses --message-format=gnu so the repair loop can parse it."""
    r = subprocess.run([CATALA_BIN, "typecheck", "--message-format=gnu", str(path)],
                       capture_output=True, text=True, env=CATALA_ENV)
    return [m.groupdict() for line in (r.stdout + r.stderr).splitlines()
            if (m := GNU_ERR.match(line.strip()))]


def generate_module(module: str, clauses: list[dict], doc_id: str, scopes_md: str,
                    attempts: int = 5) -> tuple[Path | None, list[dict], int]:
    """generate -> typecheck -> repair. Returns (path, remaining_errors, attempts_used)."""
    contract = "\n".join(l for l in scopes_md.splitlines() if module in l)
    body = "\n\n".join(f"[{doc_id} {c['locator']}] {c['heading']}\n{c['text']}"
                       for c in clauses)
    prompt = (f"Module name: {module}\n\nScope contract (from SCOPES.md):\n{contract}\n\n"
              f"Clauses to encode:\n{body}\n\nWrite the full .catala_en file.")
    dest = SRC / f"{module.lower()}.catala_en"
    dest.parent.mkdir(parents=True, exist_ok=True)
    errs, used = [], 0
    src = llm.complete(prompt, system=GEN_SYSTEM, with_cheatsheet=True)
    for used in range(1, attempts + 1):
        dest.write_text(strip_fence(src))
        errs = typecheck(dest) if have_catala() else []
        if not errs:
            return dest, [], used
        fixes = "\n".join(f"- {e['pos']}: {e['msg']}" for e in errs if e["kind"] == "ERROR")
        src = llm.complete(
            f"The compiler rejected this file:\n\n{dest.read_text()}\n\n"
            f"Errors:\n{fixes}\n\nReturn the corrected full file. Consult the error table "
            f"in the reference. Do NOT invent an exception priority to silence a "
            f"'conflict between multiple valid consequences' error.",
            system=GEN_SYSTEM, with_cheatsheet=True)
    return dest, errs, used


def strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"\A```[a-z_-]*\n", "", s)
        s = re.sub(r"\n```\s*\Z", "", s)
    return s + "\n"

# --------------------------------------------------------------------------------------
# vector store

def write_chunks(chunks: list[dict], offline: bool) -> None:
    VSTORE.parent.mkdir(parents=True, exist_ok=True)
    if not offline and chunks:
        for i in range(0, len(chunks), 32):
            batch = chunks[i:i + 32]
            for c, e in zip(batch, llm.embed([c["text"] for c in batch])):
                c["embedding"] = e
    VSTORE.write_text("".join(json.dumps(c) + "\n" for c in chunks))

# --------------------------------------------------------------------------------------
# gate

def run_tests() -> tuple[bool, str]:
    if not have_catala():
        return True, "clerk not installed -- fact-pattern suite skipped"
    r = subprocess.run([CLERK_BIN, "test"], cwd=CORPUS / "catala",
                       capture_output=True, text=True, env=CATALA_ENV)
    return r.returncode == 0, (r.stdout + r.stderr)[-4000:]

# --------------------------------------------------------------------------------------
# commands

def process(paths: list[Path], offline: bool, verbose: bool = True) -> tuple[dict, list[dict]]:
    man, docs, chunks = manifest(), {}, []
    for p in paths:
        row = row_for_path(p)
        if not row:
            print(f"skip  {p} (not in manifest.csv)")
            continue
        mods = list(filter(None, row["modules"].split(";")))
        cls = split_clauses(p)
        for c in cls:
            c["label"], c["why"] = classify(c["text"], offline)
            if c["label"] == "prose":
                chunks.append({"doc_id": row["doc_id"], "locator": c["locator"],
                               "heading": c["heading"], "text": c["text"],
                               "qualifies_module": qualifies_module(c["text"], mods),
                               "embedding": None})
        docs[row["doc_id"]] = cls
        n_rule = sum(1 for c in cls if c["label"] == "rule")
        if verbose:
            print(f"ok    {row['doc_id']:<10} {len(cls):>3} clauses  {n_rule:>3} rule-like  "
                  f"{len(cls) - n_rule:>3} prose  modules={row['modules'] or '-'}")
    return docs, chunks


def cmd_batch(args):
    man = manifest()
    paths = [p for r in man.values() if (p := ingestible(r))]
    docs, chunks = process(paths, args.offline)
    write_chunks(chunks, args.offline)
    print(f"\nvector store: {len(chunks)} chunks -> {VSTORE.relative_to(ROOT)}"
          f"{' (no embeddings: offline)' if args.offline else ''}")
    cands = conflict_candidates(docs, man)
    print(f"conflict pre-check: {len(cands)} candidate pairs "
          f"({sum(1 for c in cands if c['expect'] == 'block')} without precedence language)")
    for c in cands:
        print(f"  {c['expect']:<8} {c['module']:<32} {c['doc_a']} vs {c['doc_b']}  "
              f"{c['values_a']} vs {c['values_b']}")
    if not args.offline and have_catala():
        scopes = (CORPUS / "SCOPES.md").read_text()
        for doc_id, cls in docs.items():
            for mod in filter(None, man[doc_id]["modules"].split(";")):
                rules = [c for c in cls if c["label"] == "rule"]
                if rules:
                    path, errs, n = generate_module(mod, rules, doc_id, scopes)
                    print(f"  {mod}: {'clean' if not errs else str(len(errs)) + ' errors'} "
                          f"after {n} attempt(s) -> {path}")
    ok, out = run_tests()
    print(f"\nfact-pattern suite: {'PASS' if ok else 'FAIL'}\n{out}")
    return 0 if ok else 1


def cmd_add(args):
    man = manifest()
    paths = [p for r in man.values() if (p := ingestible(r))]
    new = Path(args.file).resolve()
    row = row_for_path(new)
    print(f"re-scanning {len(paths)} documents to check {row['doc_id'] if row else new.name} "
          f"against the corpus\n")
    docs, chunks = process(paths, args.offline, verbose=False)
    if not row:
        print(f"\nBLOCKED: {new} is not in manifest.csv (add it with a licence first)")
        return 1
    blocking = [c for c in conflict_candidates(docs, man)
                if row["doc_id"] in (c["doc_a"], c["doc_b"]) and c["expect"] == "block"]
    if blocking:
        print(f"\nBLOCKED: {row['doc_id']} conflicts with no stated precedence\n")
        for c in blocking:
            print(f"  module {c['module']}")
            colliding = set(c["values_a"]) | set(c["values_b"])
            for side in ("doc_a", "doc_b"):
                d = c[side]
                for cl in docs[d]:
                    # only the clauses that actually carry the differing values
                    if (cl["label"] == "rule" and f"{d} {cl['locator']}" in c["clauses"]
                            and any(v in fp for fp in fingerprint(cl["text"]).values()
                                    for v in colliding)):
                        print(f"  --- {d} {cl['locator']} ({cl['heading']}) ---")
                        print("      " + cl["text"].replace("\n", "\n      ")[:600])
            print()
        return 1
    write_chunks(chunks, args.offline)
    ok, out = run_tests()
    print(f"\nfact-pattern suite: {'PASS' if ok else 'FAIL'}\n{out}")
    if not ok:
        print("BLOCKED: a fact pattern failed")
        return 1
    print(f"merged {row['doc_id']}")
    return 0


def cmd_verify(args):
    """Score the offline conflict pre-check against gold/conflicts.jsonl."""
    man = manifest()
    docs, _ = process([p for r in man.values() if (p := ingestible(r))], True)
    gold = [json.loads(l) for l in (CORPUS / "gold" / "conflicts.jsonl").read_text().splitlines() if l.strip()]
    cands = conflict_candidates(docs, man)
    found_label = {frozenset((c["doc_a"], c["doc_b"])): c["expect"] for c in cands}
    found_pairs = set(found_label)
    tp = fp = fn = 0
    print()
    for g in gold:
        pair = frozenset((g["doc_a"], g["doc_b"]))
        want = g["expect"] != "no_conflict"
        got = pair in found_pairs
        if want and got:
            tp += 1
            if found_label[pair] != g["expect"]:
                print(f"LABEL {g['id']} {g['doc_a']}/{g['doc_b']}: predicted "
                      f"{found_label[pair]}, gold says {g['expect']}")
        elif want and not got:
            fn += 1
            print(f"MISS  {g['id']} {g['doc_a']}/{g['doc_b']} ({g['module']})")
        elif not want and got:
            fp += 1
            print(f"FALSE {g['id']} {g['doc_a']}/{g['doc_b']} flagged but is not a conflict")
    extra = found_pairs - {frozenset((g["doc_a"], g["doc_b"])) for g in gold}
    for p in extra:
        print(f"EXTRA {'/'.join(sorted(p))} flagged, not in the gold set")
    prec = tp / (tp + fp + len(extra)) if tp + fp + len(extra) else 0
    rec = tp / (tp + fn) if tp + fn else 0
    labels_ok = sum(1 for g in gold if g["expect"] != "no_conflict"
                    and found_label.get(frozenset((g["doc_a"], g["doc_b"]))) == g["expect"])
    print(f"\npre-check vs gold: tp={tp} fp={fp + len(extra)} fn={fn} "
          f"precision={prec:.2f} recall={rec:.2f}  "
          f"block/resolve label correct on {labels_ok}/{tp}")
    return 0


def cmd_show(args):
    man = manifest()
    row = man[args.doc_id]
    for c in split_clauses(ingestible(row)):
        label, why = classify(c["text"], args.offline)
        print(f"[{label:<5}] {row['doc_id']} {c['locator']:<8} {why:<28} "
              f"{c['text'][:90].replace(chr(10), ' ')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true",
                    help="keyword classifier, no embeddings, no generation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("batch").set_defaults(fn=cmd_batch)
    a = sub.add_parser("add"); a.add_argument("file"); a.set_defaults(fn=cmd_add)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    s = sub.add_parser("show"); s.add_argument("doc_id"); s.set_defaults(fn=cmd_show)
    args = ap.parse_args()
    if not args.offline and not llm.available():
        print("local model unavailable; falling back to --offline", file=sys.stderr)
        args.offline = True
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

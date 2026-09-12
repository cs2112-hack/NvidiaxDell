/* Legal Knowledge System — interface.

   No framework, on purpose: this is a local tool served by a standard-library
   HTTP server, and a build step would be one more thing to keep working.

   One rule runs through the whole file: nothing here decides a legal question.
   Every figure comes from /api/run or /api/explain, every quotation comes from
   the corpus verbatim, and the governing rung of a hierarchy comes from the
   interpreter's trace. The interface's whole job is to keep those provenances
   visible and never to blend them. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

/* Render clause text the way the document means it to read.

   Corpus files are hard-wrapped at about 78 columns, so rendering them with
   `white-space: pre-wrap` breaks sentences mid-clause. But an indented
   sub-paragraph list — "(a) Scheduled Maintenance; (b) an Emergency
   Maintenance event…" — is structure, not wrapping, and flattening it would
   destroy the enumeration a lawyer cites by letter. So: blocks separated by a
   blank line become paragraphs, an indented block keeps its own line breaks,
   and an unindented block is reflowed. */
function renderLaw(text, host) {
  for (const block of String(text).split(/\n\s*\n/)) {
    if (!block.trim()) continue;
    const lines = block.split('\n');
    const indented = lines.some((l) => /^\s{2,}\S/.test(l));
    const para = el('p', indented ? 'lawblock indented' : 'lawblock');
    para.textContent = indented
      ? lines.map((l) => l.replace(/\s+$/, '')).join('\n')
      : lines.map((l) => l.trim()).join(' ');
    host.appendChild(para);
  }
  return host;
}

async function api(path, body) {
  const opts = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({ error: 'The server returned something that is not JSON.' }));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status}).`);
  return data;
}

const state = { docs: [], scopes: [], scope: null, open: new Set() };

/* Computed results as a grid of name-and-value pairs.

   These were one concatenated line at display size, which read as a single
   run-on string rather than six separate findings — and a legal figure that
   cannot be read at a glance is not delivered. Name above, value below, value
   in mono because it came out of the machine. */
function renderOutputs(outputs, host) {
  const grid = el('div', 'results');
  for (const [k, v] of Object.entries(outputs)) {
    const cell = el('div', 'result');
    cell.appendChild(el('div', 'resultname', k.replace(/_/g, ' ')));
    const val = el('div', 'resultvalue');
    const text = (v === true) ? 'yes' : (v === false) ? 'no' : String(v);
    val.textContent = text;
    if (v === true || v === false) val.classList.add('is-flag');
    cell.appendChild(val);
    grid.appendChild(cell);
  }
  host.appendChild(grid);
  return host;
}

/* ---------- shell ---------- */

function showView(name) {
  for (const b of document.querySelectorAll('.navlink')) {
    const on = b.dataset.view === name;
    b.toggleAttribute('aria-current', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  }
  for (const v of document.querySelectorAll('.view')) v.hidden = true;
  $('view-' + name).hidden = false;
  if (name === 'rules' && !state.scopes.length) loadScopes();
  if (name === 'intake') loadIntake();
  if (name === 'verify') loadVerify();
}

$('nav').addEventListener('click', (e) => {
  const b = e.target.closest('.navlink');
  if (b) showView(b.dataset.view);
});

/* ---------- corpus rail ---------- */

function renderRail() {
  const host = $('rail');
  host.textContent = '';
  for (const d of state.docs) {
    const wrap = el('div', 'doc');
    const t = el('button', 'doctoggle');
    t.appendChild(el('span', null, d.title));
    const meta = el('span', 'docmeta');
    meta.appendChild(el('span', 'docid', d.doc_id));
    meta.appendChild(el('span', 'docver', `version ${d.version}, effective ${d.effective_date}`));
    const tally = el('span', 'doctally');
    tally.appendChild(el('b', 'n-rule', String(d.labels.RULE)));
    tally.appendChild(document.createTextNode(' executable '));
    if (d.labels.HYBRID) {
      tally.appendChild(el('b', 'n-hybrid', String(d.labels.HYBRID)));
      tally.appendChild(document.createTextNode(' mixed '));
    }
    tally.appendChild(el('b', 'n-prose', String(d.labels.PROSE)));
    tally.appendChild(document.createTextNode(' quoted'));
    meta.appendChild(tally);
    t.appendChild(meta);
    t.onclick = () => {
      if (state.open.has(d.doc_id)) state.open.delete(d.doc_id);
      else state.open.add(d.doc_id);
      renderRail();
    };
    wrap.appendChild(t);

    if (state.open.has(d.doc_id)) {
      const secs = el('div', 'sections');
      for (const s of d.sections) {
        const sec = el('div', 'section');
        sec.appendChild(el('div', 'sectitle', `${s.section_id} ${s.title}`));
        const list = el('div', 'clauselist');
        for (const c of s.clauses) {
          const chip = el('button', 'clausechip is-' + c.label.toLowerCase(), c.clause_id);
          chip.title = `${c.label}${c.module ? ' · ' + c.module : ''}`;
          chip.onclick = () => openClause(c.ref);
          list.appendChild(chip);
        }
        sec.appendChild(list);
        secs.appendChild(sec);
      }
      wrap.appendChild(secs);
    }
    host.appendChild(wrap);
  }
}

/* ---------- ask ---------- */

const ENGINE_WORD = { CATALA: 'Computed', VECTOR: 'Quoted', NONE: 'Not covered' };
const KIND_WORD = {
  'computed': 'by executing a rule',
  'needs-input': 'a rule can answer this, with facts',
  'ambiguous-route': 'more than one rule matches',
  'quotation': 'verbatim from the corpus',
  'caveat': 'qualifies the rule above',
  'refused': 'these facts cannot arise',
  'error': 'the rules do not resolve',
  'no-coverage': 'nothing in the corpus answers this',
};

$('askform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('askfield').value.trim();
  if (!q) return;
  const out = $('answer');
  $('askbtn').disabled = true;
  out.textContent = '';
  out.appendChild(el('p', 'spinner', 'Executing the rules that bear on this…'));
  try {
    const a = await api('/api/ask', { question: q });
    renderAnswer(a);
  } catch (err) {
    out.textContent = '';
    const p = el('div', 'part k-error e-none');
    p.appendChild(el('div', 'parttext', err.message));
    out.appendChild(p);
  } finally {
    $('askbtn').disabled = false;
  }
});

function renderAnswer(a) {
  const out = $('answer');
  out.textContent = '';
  out.classList.remove('is-empty');
  for (const p of a.parts) {
    const box = el('div', `part e-${p.engine.toLowerCase()} k-${p.kind}`);
    const head = el('div', 'partkind');
    head.appendChild(el('span', 'engine', ENGINE_WORD[p.engine] || p.engine));
    head.appendChild(el('span', 'kindword', KIND_WORD[p.kind] || p.kind));
    box.appendChild(head);

    if (p.kind === 'computed' && p.outputs) {
      renderOutputs(p.outputs, box);
      box.appendChild(el('p', 'ranwith', `Executed ${p.scope} on the facts supplied.`));
    } else if (p.kind === 'quotation') {
      const bq = el('blockquote', 'quote');
      renderLaw(p.text.replace(/^[\u201c"]|[\u201d"]$/g, ''), bq);
      box.appendChild(bq);
    } else if (p.kind === 'caveat') {
      const lead = p.text.split('\n')[0];
      const quoted = p.text.slice(lead.length).replace(/^[\s\u201c"]+|[\s\u201d"]+$/g, '');
      box.appendChild(el('div', 'parttext', lead));
      const bq = el('blockquote', 'quote');
      renderLaw(quoted, bq);
      box.appendChild(bq);
    } else {
      box.appendChild(el('div', 'parttext', p.text));
    }

    if (p.citations && p.citations.length) {
      const cites = el('div', 'cites');
      for (const c of p.citations) {
        const ref = c.replace(/\s*\(.*$/, '');
        const b = el('button', 'cite', c);
        b.onclick = () => openClause(ref);
        cites.appendChild(b);
      }
      box.appendChild(cites);
    }

    if (p.scope && (p.kind === 'needs-input' || p.kind === 'computed')) {
      const go = el('button', 'btn ghost', 'Open this rule');
      go.style.marginTop = '10px';
      go.onclick = () => { showView('rules'); selectScope(p.scope); };
      box.appendChild(go);
    }
    out.appendChild(box);
  }
  if (a.engines.length > 1) {
    out.appendChild(el('p', 'blend',
      'This answer used both engines. The computed parts were produced by running the named ' +
      'rule; the quoted parts are the documents’ own words. They are kept apart deliberately.'));
  }
}

const EXAMPLES = [
  ['How much service credit do we owe at 98.5% uptime?', 'computed from the credit table'],
  ['What is the overtime rate beyond 48 hours?', 'computed, once you give the hours'],
  ['Who decides commission disputes?', 'quoted, because the Plan says so in words'],
  ['How long do we keep candidate records?', 'computed from the retention periods'],
  ['What is the sole remedy for a service level failure?', 'quoted from the agreement'],
  ['Can the General Counsel stop a record being deleted?', 'more than one rule bears on it'],
];

function renderEmptyState() {
  const out = $('answer');
  if (out.childElementCount) return;
  const wrap = el('div', 'emptystate');

  const legend = el('div', 'legend');
  const lg = (cls, name, body) => {
    const b = el('div', 'legenditem ' + cls);
    b.appendChild(el('div', 'legendname', name));
    b.appendChild(el('div', 'legendbody', body));
    return b;
  };
  legend.appendChild(lg('e-catala', 'Computed',
    'A figure produced by running the rule that the clauses encode. Never a paraphrase of the code.'));
  legend.appendChild(lg('e-vector', 'Quoted',
    'The documents\u2019 own words, with the clause and line they came from. Never a computation.'));
  wrap.appendChild(legend);

  wrap.appendChild(el('h2', 'h3', 'Questions this corpus can answer'));
  const list = el('div', 'examples');
  for (const [q, why] of EXAMPLES) {
    const b = el('button', 'example');
    b.appendChild(el('span', 'exampleq', q));
    b.appendChild(el('span', 'examplewhy', why));
    b.onclick = () => { $('askfield').value = q; $('askbtn').click(); };
    list.appendChild(b);
  }
  wrap.appendChild(list);
  out.appendChild(wrap);
}

/* ---------- rules ---------- */

async function loadScopes() {
  const host = $('rulesbody');
  host.textContent = '';
  host.appendChild(el('p', 'spinner', 'Reading the rule registry…'));
  const d = await api('/api/scopes');
  state.scopes = d.scopes;
  renderScopeList();
}

function renderScopeList() {
  const host = $('rulesbody');
  host.textContent = '';
  const list = el('div', 'scopelist');
  for (const s of state.scopes) {
    const row = el('button', 'scoperow');
    row.appendChild(el('div', 'scopename', s.key));
    row.appendChild(el('div', 'scopeio',
      (s.encodes.length ? `${s.encodes.length} clauses · ` : '') +
      `${s.inputs.length} facts in, ${s.outputs.length} results out`));
    if (s.judgement_inputs.length) {
      row.appendChild(el('div', 'scopejudge',
        `${s.judgement_inputs.length} judgement${s.judgement_inputs.length > 1 ? 's' : ''} required`));
    } else {
      row.appendChild(el('div', 'scopeio', ''));
    }
    row.onclick = () => selectScope(s.key);
    list.appendChild(row);
  }
  host.appendChild(list);
}

async function selectScope(key) {
  showView('rules');
  const host = $('rulesbody');
  host.textContent = '';
  host.appendChild(el('p', 'spinner', 'Asking the compiler what this rule needs…'));
  const s = await api('/api/scope?key=' + encodeURIComponent(key));
  state.scope = s;
  host.textContent = '';

  const back = el('button', 'btn ghost', 'All rules');
  back.onclick = () => renderScopeList();
  host.appendChild(back);

  host.appendChild(el('h2', 'h2', s.key));

  if (s.clauses.length) {
    const cites = el('div', 'cites');
    for (const c of s.clauses) {
      const b = el('button', 'cite', c.ref);
      b.onclick = () => openClause(c.ref);
      cites.appendChild(b);
    }
    host.appendChild(cites);
  }

  host.appendChild(el('h3', 'h3', 'The facts this rule needs'));

  const fillrow = el('form', 'fillrow');
  const fillq = el('input', 'fillfield');
  fillq.placeholder = 'Or describe the situation in words and let the facts be filled in';
  const fillbtn = el('button', 'btn ghost', 'Fill from a description');
  fillbtn.type = 'submit';
  fillrow.append(fillq, fillbtn);
  const fillnote = el('p', 'hint');
  fillrow.onsubmit = async (ev) => {
    ev.preventDefault();
    const q = fillq.value.trim();
    if (!q) return;
    fillbtn.disabled = true;
    fillnote.textContent = 'Reading the description…';
    try {
      const r = await api('/api/slotfill', { target: s.key, question: q });
      if (r.error) { fillnote.textContent = r.error; return; }
      for (const [k, v] of Object.entries(r.facts)) {
        const node = $('fact-' + k);
        if (node) {
          node.value = (v === true) ? 'true' : (v === false) ? 'false' : String(v);
          node.classList.add('was-filled');
        }
      }
      const n = Object.keys(r.facts).length;
      fillnote.textContent =
        `Filled ${n} fact${n === 1 ? '' : 's'} from your description` +
        (r.omitted.length
          ? `. The description does not state ${r.omitted.join(', ')}, so those were left for you rather than guessed.`
          : '.') +
        ' Check every value before running — nothing has been executed.';
    } catch (err) {
      fillnote.textContent = err.message;
    } finally {
      fillbtn.disabled = false;
    }
  };
  host.appendChild(fillrow);
  host.appendChild(fillnote);

  const grid = el('div', 'factgrid');
  const judge = new Set(s.judgement_inputs);
  for (const name of s.inputs) {
    const ty = s.input_schema[name] || 'unknown';
    const f = el('div', 'field' + (judge.has(name) ? ' judgement' : ''));
    f.appendChild(el('label', 'fieldname', name.replace(/_/g, ' ')));
    f.appendChild(el('span', 'fieldtype', ty));
    let input;
    if (ty === 'boolean') {
      input = el('select');
      for (const v of ['false', 'true']) input.appendChild(el('option', null, v));
    } else {
      input = el('input');
      input.type = ty === 'date' ? 'date' : (ty === 'integer' || ty === 'decimal' || ty === 'money' ? 'text' : 'text');
      input.placeholder = ty === 'date' ? 'yyyy-mm-dd' : ty;
    }
    input.id = 'fact-' + name;
    input.dataset.type = ty;
    f.appendChild(input);
    grid.appendChild(f);
  }
  host.appendChild(grid);

  if (s.judgement_inputs.length) {
    host.appendChild(el('p', 'hint',
      'The judgement fields are marked. The documents do not define them, so nothing in this ' +
      'system will decide them for you — the rule computes what follows once a person has.'));
  }

  const run = el('button', 'btn', 'Run this rule');
  run.onclick = () => runScope(s);
  host.appendChild(run);
  host.appendChild(el('div', null, '')).id = 'runout';
}

function collectFacts(s) {
  const facts = {};
  const missing = [];
  for (const name of s.inputs) {
    const node = $('fact-' + name);
    const ty = node.dataset.type;
    let v = node.value.trim();
    if (v === '') { missing.push(name); continue; }
    if (ty === 'boolean') v = (v === 'true');
    else if (ty === 'integer') v = parseInt(v, 10);
    else if (ty === 'decimal' || ty === 'money') v = parseFloat(v);
    facts[name] = v;
  }
  return { facts, missing };
}

async function runScope(s) {
  const out = $('runout');
  out.textContent = '';
  const { facts, missing } = collectFacts(s);
  if (missing.length) {
    const p = el('div', 'part k-error e-none');
    p.appendChild(el('div', 'parttext',
      `Fill in every fact before running. Still needed: ${missing.join(', ')}. ` +
      `Nothing is guessed, so the rule cannot run without them.`));
    out.appendChild(p);
    return;
  }
  out.appendChild(el('p', 'spinner', 'Executing…'));
  let r;
  try {
    r = await api('/api/explain', { target: s.key, inputs: facts });
  } catch (err) {
    out.textContent = '';
    const p = el('div', 'part k-error e-none');
    p.appendChild(el('div', 'parttext', err.message));
    out.appendChild(p);
    return;
  }
  out.textContent = '';

  if (r.error) {
    const p = el('div', 'part k-error e-none');
    const word = r.error.kind === 'AssertionFailed'
      ? 'These facts cannot arise under the documents, so the rule declines to answer rather than computing from an impossible premise.'
      : r.error.kind === 'ScopeConflict'
      ? 'Two provisions apply to these facts and the documents establish no priority between them. This is a gap in the source, and it needs a person.'
      : r.error.kind === 'NoApplicableRule'
      ? 'No provision applies to these facts, so the documents do not determine an answer.'
      : 'The rule could not be executed.';
    p.appendChild(el('div', 'parttext', word));
    p.appendChild(el('pre', 'conflictdetail', r.error.diagnostic));
    out.appendChild(p);
    return;
  }

  const res = el('div', 'part e-catala k-computed');
  const head = el('div', 'partkind');
  head.appendChild(el('span', 'engine', 'Computed'));
  head.appendChild(el('span', 'kindword', 'by executing a rule'));
  res.appendChild(head);
  renderOutputs(r.outputs, res);
  res.appendChild(el('p', 'ranwith', `Executed ${s.key} on the facts you supplied.`));
  out.appendChild(res);

  for (const h of r.hierarchies) {
    if (h.n_nodes < 2) continue;
    out.appendChild(el('h3', 'h3', `How ${h.variable} was decided`));
    out.appendChild(renderLadder(h));
  }
}

/* The defeasance ladder: each rung sits under and overrides the one above,
   and the rung the interpreter actually took is marked. */
function renderLadder(h) {
  const wrap = el('div', 'ladder');
  const walk = (n, depth) => {
    const rung = el('div', 'rung' + (n.governs ? ' governs' : ''));
    rung.appendChild(el('div', 'rungmark', n.governs ? '◆' : '·'));
    const mid = el('div');
    const lab = el('span', 'runglabel', '　'.repeat(depth) + n.label);
    mid.appendChild(lab);
    const cond = el('span', 'rungcond');
    if (n.conditions.length === 1 && n.conditions[0] === '<unconditional>') {
      cond.appendChild(document.createTextNode('  applies unless something below it does'));
    } else {
      cond.appendChild(document.createTextNode('  when '));
      const b = el('b', null, n.conditions.join(' / '));
      cond.appendChild(b);
    }
    mid.appendChild(cond);
    rung.appendChild(mid);
    rung.appendChild(el('div', 'governsflag', n.governs ? 'governs' : ''));
    wrap.appendChild(rung);
    for (const c of n.exceptions) walk(c, depth + 1);
  };
  for (const t of h.trees) walk(t, 0);
  const note = el('p', 'laddernote');
  if (h.governing_line) {
    note.textContent =
      `Marked from the interpreter's own trace: the definition applied was at line ` +
      `${h.governing_line}` +
      (h.governing_headings.length ? `, under “${h.governing_headings[0]}”` : '') +
      `. The interface does not re-decide which provision governs.`;
  } else {
    note.textContent = 'The trace did not report a definition for this result.';
  }
  wrap.appendChild(note);
  return wrap;
}

/* ---------- clause drawer ---------- */

async function openClause(ref) {
  const body = $('drawerbody');
  body.textContent = '';
  body.appendChild(el('p', 'spinner', 'Fetching the clause…'));
  $('drawer').hidden = false;
  let c;
  try {
    c = await api('/api/clause?ref=' + encodeURIComponent(ref));
  } catch (err) {
    body.textContent = '';
    body.appendChild(el('p', 'parttext', err.message));
    return;
  }
  body.textContent = '';
  body.appendChild(el('div', 'recid', c.ref));
  body.appendChild(el('p', 'recwhy',
    `${c.doc_title} · version ${c.version} · effective ${c.effective_date}`));
  body.appendChild(el('div', 'sectitle', `${c.section_id} ${c.section_title}`));
  renderLaw(c.body, el('div', 'lawtext'));
  const law = el('div', 'lawtext');
  renderLaw(c.body, law);
  body.appendChild(law);

  const dl = el('dl', 'kv');
  const add = (k, v, mono) => {
    dl.appendChild(el('dt', null, k));
    dl.appendChild(el('dd', mono ? 'mono' : null, v));
  };
  add('Triaged as', c.label === 'RULE' ? 'Executable rule'
    : c.label === 'HYBRID' ? 'Rule gated on a judgement' : 'Quoted prose');
  if (c.encoded_by.length) add('Executed by', c.encoded_by.join(', '), true);
  if (c.qualifies.length) add('Qualifies', c.qualifies.join(', '), true);
  if (c.judgement_inputs.length) add('A person must decide', c.judgement_inputs.join(', '), true);
  add('Source', `${c.file}:${c.line}`, true);
  add('Content hash', c.hash, true);
  body.appendChild(dl);

  if (c.encoded_by.length) {
    const b = el('button', 'btn ghost', 'Open the rule that encodes this');
    b.style.marginTop = '16px';
    b.onclick = () => { $('drawer').hidden = true; selectScope(c.encoded_by[0]); };
    body.appendChild(b);
  }
}
$('drawerclose').onclick = () => { $('drawer').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('drawer').hidden = true; });

/* ---------- intake ---------- */

async function loadIntake() {
  const host = $('intakebody');
  host.textContent = '';
  host.appendChild(el('p', 'spinner', 'Reading proposals…'));
  const d = await api('/api/proposals');
  host.textContent = '';
  if (!d.proposals.length) {
    host.appendChild(el('p', 'empty',
      'No documents are waiting. Run “lks ingest <file>” to put one here.'));
    return;
  }
  for (const p of d.proposals) {
    host.appendChild(el('h2', 'h2', p.title || p.doc_id));
    host.appendChild(el('p', 'recwhy',
      `${p.id} · created ${p.created} · ` +
      (p.mergeable ? 'every conflict resolved; ready to merge'
                   : `${p.conflicts.filter(c => c.severity === 'blocking' && !c.resolved).length} blocking conflicts unresolved`)));
    for (const [i, c] of p.conflicts.entries()) {
      const box = el('div', 'conflict' + (c.resolved ? ' resolved' : ''));
      box.appendChild(el('div', 'conflictkind',
        `${c.kind} — ${c.incoming_ref}${c.severity === 'advisory' ? ' (advisory)' : ''}`));
      box.appendChild(el('div', 'conflictdetail', c.detail));
      if (c.resolved) {
        box.appendChild(el('div', 'recwhy', `Resolved by ${c.resolved_by}: ${c.resolution}`));
      } else if (c.severity === 'blocking') {
        const form = el('div', 'resolveform');
        const txt = el('input', 'wide');
        txt.placeholder = 'How is this resolved?';
        const who = el('input');
        who.placeholder = 'Your name';
        const btn = el('button', 'btn', 'Record resolution');
        btn.onclick = async () => {
          if (!txt.value.trim() || !who.value.trim()) return;
          btn.disabled = true;
          try {
            await api('/api/resolve', {
              id: p.id, index: i, resolution: txt.value.trim(), resolved_by: who.value.trim(),
            });
            loadIntake();
          } catch (err) {
            box.appendChild(el('div', 'recwhy', err.message));
            btn.disabled = false;
          }
        };
        form.append(txt, who, btn);
        box.appendChild(form);
      }
      host.appendChild(box);
    }
  }
}

/* ---------- verification ---------- */

async function loadVerify() {
  const host = $('verifybody');
  host.textContent = '';
  host.appendChild(el('p', 'spinner', 'Reading the record…'));
  const d = await api('/api/verification');
  host.textContent = '';

  const gate = el('div', 'gate');
  const item = (num, label, warn) => {
    const b = el('div', 'gateitem' + (warn ? ' warn' : ''));
    b.appendChild(el('div', 'gatenum', String(num)));
    b.appendChild(el('div', 'gatelabel', label));
    return b;
  };
  gate.appendChild(item(`${d.coverage.encoded}/${d.coverage.required}`, 'rule clauses encoded',
    d.coverage.missing.length > 0));
  gate.appendChild(item(d.summary.total, 'defects found and kept as tests'));
  gate.appendChild(item(d.summary.open, 'still failing', d.summary.open > 0));
  gate.appendChild(item(d.document_defects.length, 'defects in the documents themselves'));
  host.appendChild(gate);

  host.appendChild(el('h2', 'h2', 'Defects found in this system'));
  host.appendChild(el('p', 'subhead',
    'Each was found by a reviewer that saw the source clause and the compiled rule but never ' +
    'the reasoning behind it. Each is re-run on every check.'));
  for (const c of d.counterexamples) {
    const box = el('div', 'record ' + c.status);
    const h = el('div');
    h.appendChild(el('span', 'recid', c.id));
    h.appendChild(el('span', 'recstate', c.status === 'fixed' ? 'fixed and re-run' : 'still failing'));
    box.appendChild(h);
    renderLaw(c.fact_pattern, el('div'));
    const fact = el('div', 'recfact'); renderLaw(c.fact_pattern, fact);
    box.appendChild(fact);
    const why = el('div', 'recwhy'); renderLaw(c.source_reasoning, why);
    box.appendChild(why);
    const cites = el('div', 'cites');
    for (const ref of c.citations) {
      const b = el('button', 'cite', ref);
      b.onclick = () => openClause(ref);
      cites.appendChild(b);
    }
    box.appendChild(cites);
    host.appendChild(box);
  }

  if (d.document_defects.length) {
    host.appendChild(el('h2', 'h2', 'Defects in the documents'));
    host.appendChild(el('p', 'subhead',
      'These need amending rather than fixing. No encoding can be correct where the text does ' +
      'not decide the question.'));
    for (const f of d.document_defects) {
      const box = el('div', 'record');
      box.appendChild(el('div', 'recid', f.title));
      const bd = el('div', 'recwhy');
      renderLaw(f.body.replace(/\*\*/g, '').slice(0, 1400), bd);
      box.appendChild(bd);
      host.appendChild(box);
    }
  }
}

/* ---------- boot ---------- */

(async function boot() {
  try {
    const s = await api('/api/state');
    state.docs = s.documents;
    renderRail();
    const ce = s.counterexamples;
    const line = $('statusline');
    line.textContent = '';
    const bit = (t) => line.appendChild(el('span', 'statbit', t));
    bit(`${s.n_modules} rule modules, ${s.n_scopes} scopes`);
    bit(`${s.vector_store.n_chunks} clauses quotable`);
    bit(ce.open === 0
      ? `all ${ce.total} regression tests passing`
      : `${ce.open} of ${ce.total} regression tests failing`);
    if (ce.open > 0) line.classList.add('warn');
    renderEmptyState();
  } catch (err) {
    $('statusline').textContent = err.message;
  }
})();

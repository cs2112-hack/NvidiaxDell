/* Ross — interface.

   Three tools, built on the Ross design system (tokens.css) for people who are
   not engineers: Ask, Risk check and Draft. Each tool is a pipeline of agents
   and checks, and the interface draws that pipeline, because the architecture
   is the reason an answer can be trusted. A guided tour runs over the live
   interface, pointing at the real controls.

   No framework, on purpose: this is a local tool served by a standard-library
   HTTP server, and a build step would be one more thing to keep working.

   Two rules run through the whole file.

   Nothing here decides a legal question. Every figure comes from executing a
   rule, every quotation comes from the corpus verbatim, and every finding was
   reached by the interpreter.

   Nothing here pretends to progress. Every step, attack and verdict drawn in a
   tool is an event the server emitted when it happened, or is read off a
   finished answer; where the interface cannot know how far along something is,
   it shows a clock, never a bar. The tour's animations are the one exception,
   and each is labelled as an example.

   State classes are always prefixed (`is-`, `s-`, `p-`, `t-`, `e-`, `r-`). A
   bare word like `run` once doubled as a block's name, and a status dot took on
   that block's layout mid-run. scripts/ui_check.py drives every job state and
   fails if the layout breaks.

   The specialist views (rules, decision map, intake, verification) are kept at
   /classic/. */

'use strict';

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function add(parent, ...kids) {
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false || k === '') continue;
    parent.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
  }
  return parent;
}

function icon(name, size = 16) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [k, v] of Object.entries({
    viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
  })) s.setAttribute(k, v);
  s.classList.add('icon');
  s.innerHTML = ICONS[name] || '';
  return s;
}

function btn(label, cls, onClick, iconName) {
  const b = el('button', 'btn ' + (cls || 'btn-secondary'));
  b.type = 'button';
  if (iconName) b.appendChild(icon(iconName, 14));
  b.appendChild(document.createTextNode(label));
  if (onClick) b.onclick = onClick;
  return b;
}

function fmtDur(s) {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function ago(epoch) {
  const d = Date.now() / 1000 - epoch;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(epoch * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

const plural = (n, w, p) => `${n} ${n === 1 ? w : (p || w + 's')}`;
const human = (id) => String(id).replace(/_/g, ' ');
const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');
const sentence = (s) => cap(String(s || '').trim()).replace(/([^.!?])$/, '$1.');

function fmtValue(v) {
  if (v === true) return 'yes';
  if (v === false) return 'no';
  if (typeof v === 'number') return v.toLocaleString('en-GB', { maximumFractionDigits: 6 });
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

async function api(path, body) {
  const opts = body !== undefined
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({ error: 'The server returned something that is not JSON.' }));
  if (!r.ok) {
    const e = new Error(data.error || `Request failed (${r.status}).`);
    e.status = r.status;
    throw e;
  }
  return data;
}

/* Browser storage can be missing or refuse; the interface works without it. */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* not remembered */ } },
};

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const narrow = () => window.matchMedia('(max-width: 860px)').matches;

/* Render clause text the way the document means it to read. Corpus files are
   hard-wrapped, so unindented blocks are reflowed; an indented block is an
   enumeration a lawyer cites by letter, and keeps its line breaks. */
function renderLaw(text, host) {
  for (const block of String(text || '').split(/\n\s*\n/)) {
    if (!block.trim()) continue;
    const lines = block.split('\n');
    const indented = lines.some((l) => /^\s{2,}\S/.test(l));
    const p = el('p', indented ? 'indented' : null);
    p.textContent = indented
      ? lines.map((l) => l.replace(/\s+$/, '')).join('\n')
      : lines.map((l) => l.trim()).join(' ');
    host.appendChild(p);
  }
  return host;
}
const lawBlock = (text) => renderLaw(text, el('div', 'law'));
const unquote = (t) => String(t || '').replace(/^[\s“"]+|[\s”"]+$/g, '');

function citeButton(ref) {
  const b = el('button', 'cite', ref);
  b.type = 'button';
  b.title = ref;
  b.onclick = () => openDrawer(clauseDrawer(ref.replace(/\s*\(.*$/, '')));
  return b;
}
function cites(refs) {
  const box = el('div', 'cites');
  for (const r of refs || []) box.appendChild(citeButton(r));
  return box;
}

function kv(rows, cls) {
  const dl = el('dl', 'kv' + (cls ? ' ' + cls : ''));
  for (const [k, v] of rows) {
    if (v === undefined || v === null || v === '') continue;
    add(dl, el('dt', null, k), el('dd', null, v));
  }
  return dl;
}

function notice(text, kind) {
  const n = el('div', 'notice' + (kind ? ' is-' + kind : ''));
  add(n, icon(kind === 'bad' ? 'octagon-x' : kind === 'warn' ? 'triangle-alert' : 'info', 15), el('p', null, text));
  return n;
}

function waiting(text, clockNode) {
  return add(el('p', 'waiting'), icon('loader', 14), text, clockNode);
}

function emptyState(iconName, title, body) {
  return add(el('div', 'empty'), icon(iconName, 22), el('div', 'empty-t', title), body && el('div', 'empty-d', body));
}

function section(label, ...kids) {
  return add(el('section', 'd-sec'), el('span', 'label', label), ...kids);
}

/* ============================================================ vocabulary */

const ROLES = {
  search: { label: 'Document search', icon: 'search', body: 'Finds the passages that bear on a question and quotes them word for word.' },
  engine: { label: 'Rule engine', icon: 'cpu', body: 'Runs the rules encoded from your documents. Its results are exact and repeatable.' },
  agent: { label: 'AI agent', icon: 'bot', body: 'A language model running on this machine. It reads, proposes and drafts. It never has the last word.' },
  person: { label: 'You', icon: 'user', body: 'Supply the judgements your documents leave open, and decide what happens next.' },
  check: { label: 'Automatic check', icon: 'list-checks', body: 'A mechanical test with one right answer, such as whether a quote appears word for word.' },
};

function roleTag(key) {
  const r = ROLES[key];
  return add(el('span', 'role r-' + key), icon(r.icon, 12), r.label);
}

/* The pipeline behind each tool. The same definitions draw the tour's examples
   and the live views, so what the tour shows is what the tools do. */
const PIPES = {
  ask: [
    { key: 'understand', title: 'Understand the question', roles: ['search'],
      body: 'Splits a compound question into parts and matches each part against every rule and every clause.' },
    { key: 'facts', title: 'Gather the facts', roles: ['person', 'agent'],
      body: 'A rule needs facts about your case. Ross asks for any that are missing; an agent can fill them in from a description, for you to check.' },
    { key: 'execute', title: 'Run the rule', roles: ['engine'],
      body: 'The rule engine executes the rule on those facts. The figure it returns is computed, never estimated.' },
    { key: 'quote', title: 'Quote the wording', roles: ['search'],
      body: 'Clauses that answer in words, or qualify a result, are quoted exactly with their reference.' },
    { key: 'label', title: 'Label every part', roles: [],
      body: 'Each part of the answer says whether it was computed or quoted. The two are never blended.' },
  ],
  check: [
    { key: 'operations', title: 'Compare what we did with what we wrote', roles: ['engine'],
      body: 'Runs the policy over every past decision on record, and flags each one the policy would not have produced.' },
    { key: 'rivalries', title: 'Check rules against each other', roles: ['engine'],
      body: 'Runs pairs of rules that answer the same question, and flags any that disagree.' },
    { key: 'reverify', title: 'Re-check every finding', roles: ['engine'],
      body: 'Runs every open finding again. One that no longer reproduces is flagged rather than kept.' },
    { key: 'fleet', title: 'Send in the agents', roles: ['agent', 'engine'],
      body: 'Five agents each play an opponent and propose a claim. The rule engine runs every claim, and only claims that land become findings.' },
  ],
  draft: [
    { key: 'retrieve', title: 'Find precedent', roles: ['search'],
      body: 'Finds the clauses and documents closest to your request, to draft from.' },
    { key: 'draft', title: 'Write the document', roles: ['agent'],
      body: 'A drafting agent writes the document in the house style.' },
    { key: 'catala', title: 'Encode and test the rules', roles: ['agent', 'engine'],
      body: 'An encoding agent turns each rule into code; the rule engine compiles it and tests every branch (G1 to G4). A failure goes back with the exact error.' },
    { key: 'screen', title: 'Independent review', roles: ['agent'],
      body: 'Three reviewer agents check logic, language and consistency without seeing each other’s work. A confirmed problem sends the draft back to be rewritten.' },
    { key: 'roundtrip', title: 'Rebuild from the words alone', roles: ['agent', 'engine'],
      body: 'A fresh agent that never saw the code rebuilds the rules from the English. Both versions must behave the same (G5).' },
    { key: 'issue', title: 'Issue the PDF', roles: ['person'],
      body: 'Only a draft that passed every step is issued. It joins your documents only when a person adds it.' },
  ],
  watch: [
    { key: 'scan', title: 'Look for new documents', roles: ['check'],
      body: 'Every few seconds, reads the corpus index and the intake in MongoDB. A document counts as new or changed only when its clauses change, so re-indexing wakes nothing.' },
    { key: 'compare', title: 'Find the closest clauses', roles: ['search'],
      body: 'Every clause of the new document is scored against every clause of every other document. None is skipped for looking unrelated.' },
    { key: 'attack', title: 'Look for contradictions', roles: ['agent'],
      body: 'Two AI agents, blind to each other, read each clause beside its nearest rivals and report pairs that cannot both be followed.' },
    { key: 'verify', title: 'Check every quote', roles: ['check'],
      body: 'A claim is kept only if both of its quotes appear word for word, in two different documents. Anything else is discarded, with the reason.' },
    { key: 'decide', title: 'Record it for a person', roles: ['person'],
      body: 'A claim raised by both agents is marked as such. A check that did not finish is retried, and is never reported as clean.' },
  ],
};

const STEP_ICON = { pass: 'check', blocked: 'octagon-x', running: 'loader', waiting: 'circle-dashed', idle: 'circle-dashed', skipped: 'minus' };

/* A state is { status, meta, summary, subs, loop, extra }. The description is
   shown until a step has something real to say. */
function renderPipe(host, defs, states = {}) {
  host.textContent = '';
  defs.forEach((d, i) => {
    const s = states[d.key] || { status: 'idle' };
    const li = el('li', 'step s-' + s.status);
    li.dataset.key = d.key;
    const mark = el('span', 'step-mark');
    mark.appendChild(icon(STEP_ICON[s.status] || 'circle-dashed', 12));
    li.appendChild(add(el('div', 'step-gutter'), mark, el('span', 'step-rail')));
    const body = el('div', 'step-body');
    body.appendChild(add(el('div', 'step-head'), el('span', 'step-n', String(i + 1).padStart(2, '0')), el('span', 'step-title', d.title)));
    if ((d.roles || []).length) body.appendChild(add(el('div', 'step-roles'), d.roles.map(roleTag)));
    const quiet = s.status === 'idle' || s.status === 'waiting';
    if (quiet && !s.summary && d.body) body.appendChild(el('p', 'step-desc', d.body));
    if (s.meta) body.appendChild(add(el('p', 'step-meta'), s.meta));
    if (s.summary) body.appendChild(el('p', 'step-summary', s.summary));
    if (s.loop) body.appendChild(add(el('p', 'step-loop'), icon('rotate-ccw', 12), s.loop));
    if (s.subs && s.subs.length) {
      const ul = el('ul', 'subs');
      for (const sub of s.subs) {
        ul.appendChild(add(el('li', 'sub s-' + sub.status), icon(STEP_ICON[sub.status] || 'circle-dashed', 12),
          add(el('div'), el('div', 'sub-title', sub.title), sub.summary && el('div', 'sub-sum', sub.summary))));
      }
      body.appendChild(ul);
    }
    if (s.extra) body.appendChild(s.extra);
    li.appendChild(body);
    host.appendChild(li);
  });
}

/* Change one step's state in place, so its colour cross-fades. */
function setStep(ol, key, status, summary, loop) {
  const li = ol.querySelector(`[data-key="${key}"]`);
  if (!li) return;
  li.className = (li.classList.contains('mini-step') ? 'mini-step' : 'step') + ' s-' + status;
  const mark = li.querySelector('.step-mark');
  mark.textContent = '';
  mark.appendChild(icon(STEP_ICON[status] || 'circle-dashed', 12));
  const body = li.querySelector('.step-body') || li.lastElementChild;
  if (summary !== undefined) {
    let p = li.querySelector('.step-summary, .mini-verdict');
    if (!p) p = body.appendChild(el('p', 'step-summary'));
    p.textContent = summary;
  }
  if (loop) {
    let l = li.querySelector('.step-loop');
    if (!l) l = body.appendChild(el('p', 'step-loop'));
    l.textContent = '';
    add(l, icon('rotate-ccw', 12), loop);
  }
}

const S = {
  view: null, state: null, agent: null,
  scopes: [], unmapped: [], queue: [], summary: null, queueError: '',
  live: null, offline: '', newIds: new Set(), filter: '', round: null, rounds: '6',
  turns: [], scopeCache: new Map(), draftUnavailable: '', transcriptOpen: false,
};

/* ================================================================= shell */

const VIEWS = {
  ask: {
    title: 'Ask your documents',
    sub: () => (S.state
      ? `${plural(S.state.documents.length, 'document')} · ${plural(S.state.n_scopes, 'rule')} · every answer computed or quoted`
      : 'every answer computed or quoted'),
  },
  check: { title: 'Risk check', sub: () => 'agents propose claims · the rule engine decides which land' },
  draft: { title: 'Draft a document', sub: () => 'precedent → draft → encode → review → roundtrip → issue' },
  watch: {
    title: 'Document watch',
    sub: () => (W.data && !W.error
      ? `MongoDB checked every ${W.data.interval}s · ${plural(watchSummary(W.data).live.length, 'document')} watched`
      : 'new documents in MongoDB, checked against all the others'),
  },
};
/* Addresses from earlier interfaces keep working. */
const LEGACY = { exposure: 'check', generate: 'draft' };
const TOUR_HASHES = new Set(['welcome', 'tour']);
const CLASSIC = new Set(['rules', 'documents', 'intake', 'checks']);

function showView(name) {
  if (TOUR_HASHES.has(name)) {
    showView(S.view || 'ask');
    if (T.i < 0) startTour();
    return;
  }
  if (LEGACY[name]) name = LEGACY[name];
  if (CLASSIC.has(name)) { location.href = '/classic/#' + name; return; }
  if (!VIEWS[name]) name = 'ask';
  const changed = S.view !== name;
  S.view = name;
  for (const b of document.querySelectorAll('.nav-item[data-view]')) {
    if (b.dataset.view === name) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  }
  for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== 'view-' + name;
  if (location.hash.slice(1) !== name) history.replaceState(null, '', location.pathname + location.search + '#' + name);
  $('toptitle').textContent = VIEWS[name].title;
  $('topsub').textContent = VIEWS[name].sub();
  document.title = `${VIEWS[name].title} · Ross`;
  if (T.i < 0) closeSide();
  if (name === 'check') renderCheck();
  if (name === 'draft') { renderDraft(); loadRuns(); }
  if (name === 'ask') renderAskPanel();
  if (name === 'watch') { renderWatch(); loadWatch().then(scheduleWatch); }
  if (changed) {
    window.scrollTo(0, 0);
    if (name === 'ask' && T.i < 0 && !narrow()) setTimeout(() => $('askfield').focus(), 0);
  }
}

function openSide() {
  $('side').classList.add('is-open');
  $('menubtn').setAttribute('aria-expanded', 'true');
  if (!document.querySelector('.side-scrim')) {
    const sc = el('div', 'scrim side-scrim');
    sc.onclick = closeSide;
    document.body.appendChild(sc);
  }
}
function closeSide() {
  $('side').classList.remove('is-open');
  $('menubtn').setAttribute('aria-expanded', 'false');
  const sc = document.querySelector('.side-scrim');
  if (sc) sc.remove();
}

function jobWord(kind) {
  return {
    sweep: 'running a risk check', fleet: 'running agent attacks', generate: 'drafting a document',
    letter: 'writing a demand letter', close: 'proposing a fix',
    'doc-watch': 'checking a new document for contradictions',
  }[kind] || 'running a job';
}

function renderStatus() {
  const b = $('status');
  b.textContent = '';
  let state = '';
  let text = 'Connecting';
  let go = null;
  b.title = '';
  if (S.offline) {
    state = 'is-bad'; text = 'Server unreachable'; b.title = S.offline;
  } else if (S.live) {
    state = 'is-run';
    if (S.live.kind === 'sweep' || S.live.kind === 'fleet') {
      go = 'check';
      const r = latestRound(fold(jobs.check));
      text = r ? `Risk check · attack ${r.n} of ${r.of}` : 'Risk check running';
    } else if (S.live.kind === 'generate') {
      go = 'draft'; text = 'Drafting a document';
    } else if (S.live.kind === 'doc-watch') {
      go = 'watch'; text = 'Checking a new document';
    } else {
      go = 'check'; text = cap(jobWord(S.live.kind));
    }
  } else if (S.agent) {
    if (S.agent.available) { state = 'is-ok'; text = 'Local model ready'; }
    else if (S.agent.error && !S.agent.model) { text = 'Model status unknown'; b.title = S.agent.error; }
    else { state = 'is-bad'; text = 'Local model offline'; }
  }
  b.title = b.title || (S.agent && S.agent.model ? `${text}. ${S.agent.model} on this machine.` : text);
  add(b, el('span', 'dot' + (state ? ' ' + state : '')), el('span', 'status-text', text));
  b.onclick = () => showView(go || S.view || 'ask');
  for (const n of document.querySelectorAll('.nav-item .nav-live')) n.remove();
  if (go) {
    const item = document.querySelector(`.nav-item[data-view="${go}"]`);
    if (item) item.appendChild(el('span', 'nav-live'));
  }
  $('navcount').textContent = S.summary && S.summary.open ? String(S.summary.open) : '';
}

function renderSideFoot() {
  const host = $('sidefoot');
  host.textContent = '';
  const line = (text, cls) => el('div', cls || null, text);
  const a = S.agent;
  if (a && a.model) host.appendChild(line(`model · ${a.model} (local)${a.available ? '' : ', offline'}`, a.available ? null : 'foot-bad'));
  else if (a) host.appendChild(line('model · status could not be read', 'foot-bad'));
  host.appendChild(line('rules · Catala, executed'));
  if (S.state) {
    host.appendChild(line(`corpus · ${plural(S.state.documents.length, 'document')} · ${S.state.n_scopes} rules`));
    if (S.state.vector_store) host.appendChild(line(`search · ${S.state.vector_store.n_chunks} passages`));
  }
  host.appendChild(line('no remote calls', 'foot-ok'));
  const classic = el('a', 'side-classic');
  classic.href = '/classic/';
  add(classic, icon('terminal', 13), 'Specialist views (classic console)');
  host.appendChild(classic);
}

function renderDocs() {
  const host = $('doclist');
  host.textContent = '';
  for (const d of S.state.documents) {
    const b = el('button', 'doc-link');
    b.type = 'button';
    b.title = d.title;
    add(b, icon('file-text', 13), el('span', 'doc-name', d.title), el('span', 'doc-n', String(d.n_clauses)));
    b.onclick = () => { closeSide(); openDrawer(docDrawer(d), true); };
    host.appendChild(add(el('li'), b));
  }
}

/* ================================================================ drawer */

const drawer = { stack: [], lastFocus: null, finding: null };

function openDrawer(render, fresh) {
  if ($('drawer').hidden) drawer.lastFocus = document.activeElement;
  if (fresh) drawer.stack = [];
  drawer.stack.push(render);
  paintDrawer();
}

function paintDrawer() {
  const render = drawer.stack[drawer.stack.length - 1];
  const body = $('drawerbody');
  body.textContent = '';
  body.scrollTop = 0;
  $('drawereyebrow').textContent = '';
  $('drawertitle').textContent = '';
  $('drawerback').hidden = drawer.stack.length < 2;
  drawer.finding = null;
  $('scrim').hidden = false;
  $('drawer').hidden = false;
  render(body, (eyebrow, title) => {
    $('drawereyebrow').textContent = eyebrow || '';
    $('drawertitle').textContent = title || '';
  });
  $('drawerclose').focus();
}

function closeDrawer() {
  if ($('drawer').hidden) return;
  $('drawer').hidden = true;
  $('scrim').hidden = true;
  drawer.stack = [];
  drawer.finding = null;
  if (drawer.lastFocus && document.contains(drawer.lastFocus)) drawer.lastFocus.focus();
}

const LABEL_USE = {
  RULE: ['Runs as a rule', 'Ross can compute results from this clause.', 't-ink'],
  HYBRID: ['Rule with a judgement', 'Ross computes the result once a person supplies a judgement the documents leave open.', 't-review'],
  PROSE: ['Quoted only', 'Ross quotes this clause, but never computes from it.', ''],
};

function clauseDrawer(ref) {
  return async (body, setHead) => {
    setHead('Clause', ref);
    body.appendChild(waiting('Reading the clause…'));
    let c;
    try {
      c = await api('/api/clause?ref=' + encodeURIComponent(ref));
    } catch (e) {
      body.textContent = '';
      body.appendChild(notice(e.message, 'bad'));
      return;
    }
    body.textContent = '';
    setHead(`${c.doc_id} · ${c.section_title}`, c.ref);
    body.appendChild(section('The clause, verbatim', lawBlock(c.body)));
    const use = LABEL_USE[c.label];
    if (use) {
      const u = el('div', 'use');
      add(u, add(el('div'), add(el('span', 'badge ' + use[2]), use[0]), el('p', null, use[1])));
      const sec = section('How Ross uses it', u);
      if ((c.encoded_by || []).length) sec.appendChild(add(el('div', 'label-after'), kv([['Encoded in', c.encoded_by.join(', ')]])));
      if ((c.judgement_inputs || []).length) {
        sec.appendChild(add(el('p', 'judge-note label-after'), roleTag('person'),
          `Needs your judgement on: ${c.judgement_inputs.map(human).join(', ')}.`));
      }
      if ((c.qualifies || []).length) sec.appendChild(add(el('div', 'label-after'), kv([['Qualifies', c.qualifies.join(', ')]])));
      body.appendChild(sec);
    }
    body.appendChild(section('Where it is', kv([
      ['Document', c.doc_title], ['Version', c.version], ['In force from', c.effective_date], ['Source', `${c.file}, line ${c.line}`],
    ], 'kv-plain')));
    body.appendChild(add(el('div', 'd-sec row'), btn('Ask about this clause', 'btn-primary btn-sm', () => {
      closeDrawer();
      showView('ask');
      $('askfield').value = `What does ${c.ref} say?`;
      autosize();
      $('askfield').focus();
    }, 'message-square-text')));
  };
}

function docDrawer(d) {
  return (body, setHead) => {
    setHead(`${d.doc_id} · version ${d.version}`, d.title);
    body.appendChild(kv([
      ['Owner', d.owner], ['In force from', d.effective_date], ['Jurisdiction', d.jurisdiction], ['Clauses', String(d.n_clauses)],
    ], 'kv-plain'));
    const L = d.labels || {};
    const total = (L.RULE || 0) + (L.HYBRID || 0) + (L.PROSE || 0) || 1;
    const bar = el('div', 'labelbar');
    const legend = el('ul', 'lb-legend');
    for (const [k, cls] of [['RULE', 'lb-rule'], ['HYBRID', 'lb-hybrid'], ['PROSE', 'lb-prose']]) {
      const s = el('span', cls);
      s.style.width = `${(100 * (L[k] || 0)) / total}%`;
      bar.appendChild(s);
      legend.appendChild(add(el('li'), el('i', cls), `${L[k] || 0} ${LABEL_USE[k][0].toLowerCase()}`));
    }
    body.appendChild(section('How much of it Ross can run', bar, legend));
    const list = el('div', 'sec-list');
    for (const sec of d.sections || []) {
      list.appendChild(el('p', 'sec-title', `${sec.section_id} · ${sec.title}`));
      for (const c of sec.clauses) {
        const row = el('button', 'clause-row');
        row.type = 'button';
        const use = LABEL_USE[c.label];
        add(row, el('span', 'mono', c.clause_id), use && add(el('span', 'badge ' + use[2]), use[0]), icon('chevron-right', 14));
        row.onclick = () => openDrawer(clauseDrawer(c.ref));
        list.appendChild(row);
      }
    }
    body.appendChild(section('Clauses', list));
  };
}

/* ========================================================== agent runtime

   One job runs at a time on the server, because the local model serves one
   request at a time. A tracker follows the latest job of each kind, so the risk
   check still shows its last run while a document is being drafted. */

class Tracker {
  constructor(name) { this.name = name; this.reset(); }
  reset() {
    this.job = null; this.events = []; this.lines = [];
    this.sinceE = 0; this.sinceL = 0; this.receivedAt = 0; this.pulling = false; this.attaching = false;
  }
  get running() { return !!(this.job && this.job.status === 'running'); }
  async attach(id) {
    if (this.job && this.job.id === id) return this.pull();
    this.reset();
    this.job = { id, status: 'running', seconds: 0, params: {}, kind: '' };
    this.attaching = true;
    return this.pull();
  }
  async pull() {
    if (!this.job || this.pulling) return null;
    this.pulling = true;
    try {
      const id = this.job.id;
      const prev = this.attaching ? null : this.job.status;
      const j = await api(`/api/exposure/job?id=${id}&since=${this.sinceL}&since_events=${this.sinceE}`);
      if (!this.job || this.job.id !== id) return null;
      const fresh = j.events || [];
      this.events.push(...fresh);
      this.lines.push(...(j.lines || []));
      this.sinceE = j.n_events;
      this.sinceL = j.n_lines;
      delete j.events;
      delete j.lines;
      this.job = j;
      this.receivedAt = performance.now();
      this.attaching = false;
      const moreLines = (j.lines || []).length > 0;
      return { fresh, changed: fresh.length > 0 || moreLines || prev !== j.status, finished: prev === 'running' && j.status !== 'running' };
    } finally {
      this.pulling = false;
    }
  }
  t() {
    const j = this.job;
    if (!j) return 0;
    return (j.seconds || 0) + (j.status === 'running' ? (performance.now() - this.receivedAt) / 1000 : 0);
  }
}

const jobs = { check: new Tracker('check'), draft: new Tracker('draft'), agent: new Tracker('agent'), watch: new Tracker('watch') };
/* Each job kind has its own tracker. A contradiction check is neither a risk
   check nor a finding's agent job, and an unknown kind is shown as neither. */
function trackerFor(kind) {
  if (kind === 'generate') return 'draft';
  if (kind === 'letter' || kind === 'close') return 'agent';
  if (kind === 'doc-watch') return 'watch';
  if (kind === 'sweep' || kind === 'fleet') return 'check';
  return null;
}

function clock(tracker, fromT) {
  const s = el('span', 'clock');
  s.dataset.t = tracker;
  s.dataset.from = String(fromT || 0);
  s.textContent = fmtDur(jobs[tracker].t() - (fromT || 0));
  return s;
}
function askClock(since) {
  const s = el('span', 'clock');
  s.dataset.since = String(since);
  s.textContent = fmtDur((performance.now() - since) / 1000);
  return s;
}
function tick() {
  for (const c of document.querySelectorAll('.clock[data-t]')) {
    const t = jobs[c.dataset.t];
    if (t && t.running) c.textContent = fmtDur(t.t() - Number(c.dataset.from));
  }
  for (const c of document.querySelectorAll('.clock[data-since]')) {
    c.textContent = fmtDur((performance.now() - Number(c.dataset.since)) / 1000);
  }
}

const FINAL = new Set(['died', 'known', 'landed', 'malformed', 'unanswered', 'stopped']);

/* Fold a job's event stream into the state of each stage and each attack. */
function fold(t) {
  const m = { plan: null, stages: new Map(), rounds: new Map(), verifies: [], failed: null };
  for (const e of t.events) {
    if (e.type === 'plan') m.plan = e;
    else if (e.type === 'stage') {
      const s = { ...(m.stages.get(e.key) || {}), ...e };
      if (e.status === 'running') s.startT = e.t;
      else {
        s.endT = e.t;
        if (s.startT === undefined) s.startT = e.t;
        delete s.note;
      }
      m.stages.set(e.key, s);
    } else if (e.type === 'round') {
      const r = { ...(m.rounds.get(e.n) || {}), ...e };
      if (e.phase === 'proposing') r.startT = e.t;
      if (FINAL.has(e.phase)) r.endT = e.t;
      m.rounds.set(e.n, r);
    } else if (e.type === 'verify') m.verifies.push(e);
    else if (e.type === 'failed') m.failed = e;
  }
  /* A job that has ended cannot still be running anything, so whatever it left
     open is settled here and a failure never reads as work in progress. */
  const j = t.job;
  if (j && j.status !== 'running' && !t.attaching) {
    const endT = m.failed ? m.failed.t : (j.seconds || 0);
    for (const s of m.stages.values()) {
      if (s.status !== 'running') continue;
      s.status = j.status === 'failed' ? 'failed' : 'done';
      s.endT = endT;
      delete s.note;
    }
    for (const r of m.rounds.values()) {
      if (FINAL.has(r.phase)) continue;
      r.phase = 'stopped';
      r.why = m.failed ? m.failed.error : 'the job ended before a verdict';
      r.plain_why = m.failed ? 'The check stopped before this attack reached a verdict.' : 'The check ended before this attack reached a verdict.';
      r.endT = endT;
    }
  }
  return m;
}

function latestRound(m) {
  let best = null;
  for (const r of m.rounds.values()) if (!best || r.n > best.n) best = r;
  return best;
}

async function loop() {
  const prevLive = S.live ? S.live.id : null;
  try {
    const r = await api('/api/exposure/job');
    S.live = r.running || null;
    S.offline = '';
    if (S.live) {
      const name = trackerFor(S.live.kind);
      const t = name && jobs[name];
      if (t && (!t.job || t.job.id !== S.live.id)) onJob(name, await t.attach(S.live.id));
    }
    for (const name of Object.keys(jobs)) {
      if (jobs[name].running) onJob(name, await jobs[name].pull());
    }
  } catch (err) {
    S.offline = err.message;
  }
  if (prevLive !== (S.live ? S.live.id : null)) {
    if (S.view === 'check') renderCheck();
    if (S.view === 'draft') renderDraftForm();
  }
  renderStatus();
  setTimeout(loop, S.live ? 1500 : 4000);
}

function onJob(name, u) {
  if (!u) return;
  if (name === 'check') {
    for (const e of u.fresh) {
      if (e.type === 'round' && e.phase === 'landed' && e.recorded) {
        S.newIds.add(e.recorded);
        refreshQueueSoon();
      }
      if (e.type === 'stage' && e.key === 'operations' && e.status === 'done' && (e.recorded || []).length) {
        for (const id of e.recorded) S.newIds.add(id);
        refreshQueueSoon();
      }
    }
    if (u.changed && S.view === 'check') renderCheck();
  }
  if (name === 'draft') {
    if (u.changed && S.view === 'draft') { renderDraft(); loadRuns(); }
    else if (u.finished) loadRuns();
  }
  if (name === 'agent') renderAgentJob();
  if (name === 'watch') {
    if (u.changed && S.view === 'watch') { renderWatchStatus(); renderWatchPipe(); renderWatchNow(); }
    if (u.finished) loadWatch();
  }
  if (u.finished) refreshQueueSoon();
}

let queueTimer = null;
function refreshQueueSoon() {
  clearTimeout(queueTimer);
  queueTimer = setTimeout(refreshQueue, 300);
}

async function refreshQueue() {
  try {
    const q = await api('/api/exposure/queue');
    S.queue = q.items || [];
    S.summary = q.summary;
    S.queueError = '';
  } catch (err) {
    S.queueError = err.message;
  }
  if (S.view === 'check') { renderCheckStats(); renderFindings(); renderFleet(jobs.check, fold(jobs.check)); }
  renderStatus();
}

async function refreshScopes() {
  try {
    const s = await api('/api/exposure/scopes');
    S.scopes = s.scopes || [];
    S.unmapped = s.unmapped || [];
  } catch (err) { /* the findings still render, without units */ }
}

async function loadAgent() {
  try {
    S.agent = await api('/api/agent');
  } catch (err) {
    S.agent = { available: false, model: '', error: err.message, fleet: [], writers: [] };
  }
}

/* =================================================================== ask */

const EXAMPLES = [
  ['How much service credit do we owe at 98.5% uptime?', 'Computed from the credit table', 'e-catala'],
  ['What is the overtime rate beyond 48 hours?', 'Computed, once you give the hours', 'e-catala'],
  ['Who decides commission disputes?', 'Quoted, because the plan says it in words', 'e-vector'],
  ['How long do we keep candidate records?', 'Computed from the retention periods', 'e-catala'],
  ['What is the sole remedy for a service level failure?', 'Quoted from the agreement', 'e-vector'],
  ['Can the General Counsel stop a record being deleted?', 'More than one rule bears on it', 'e-catala'],
];

const ENGINE = {
  CATALA: ['e-catala', 'Computed · rule engine'],
  VECTOR: ['e-vector', 'Quoted · from the documents'],
  NONE: ['e-none', 'Not answered'],
};

const KIND_WORD = {
  computed: 'worked out by running the rule on your facts',
  'needs-input': 'a rule can answer this once it has the facts',
  'ambiguous-route': 'more than one rule could answer this',
  quotation: 'the documents’ own words',
  caveat: 'qualifies the rule above, without changing its result',
  refused: 'these facts cannot arise under the documents',
  error: 'the rules do not settle this',
  'no-coverage': 'nothing in your documents answers this',
};

const TYPE_HINT = {
  boolean: 'Yes or no', integer: 'A whole number', decimal: 'A number; decimals allowed',
  money: 'An amount of money', date: 'A date', duration: 'A length of time',
};

/* The label says what the engine did for this part, not only which engine. */
function engineLabel(engine, kind) {
  if (engine === 'CATALA' && kind === 'needs-input') return el('span', 'engine e-none', 'Rule found · needs facts');
  if (engine === 'CATALA' && kind !== 'computed') return el('span', 'engine e-none', 'Rule engine · not settled');
  const [c, w] = ENGINE[engine] || ['e-none', engine];
  return el('span', 'engine ' + c, w);
}

function askEmpty() {
  const box = el('div', 'ask-empty');
  add(box, el('p', 'eyebrow', 'Ask'), el('h2', null, 'What do your documents say?'),
    el('p', 'lede', 'Ask in plain English. A question about a figure is answered by running the rule; a question about wording is answered by quoting the clause. Each part of the answer says which it is.'));
  const list = el('div', 'examples');
  for (const [q, why, cls] of EXAMPLES) {
    const b = el('button', 'example');
    b.type = 'button';
    add(b, el('span', 'example-q', q), add(el('span', 'example-why ' + cls), el('span', 'example-sq'), why));
    b.onclick = () => ask(q);
    list.appendChild(b);
  }
  add(box, el('p', 'label', 'Try one of these'), add(el('div', 'label-after'), list));
  return box;
}

let turnSeq = 0;

function renderThread() {
  const host = $('thread');
  if (!S.turns.length) {
    host.textContent = '';
    host.appendChild(askEmpty());
    return;
  }
  const empty = host.querySelector('.ask-empty');
  if (empty) empty.remove();
  for (const t of S.turns) {
    if (!t.node) {
      t.node = turnNode(t);
      host.appendChild(t.node);
    }
  }
}

function replaceTurn(t) {
  const old = t.node;
  t.node = turnNode(t);
  if (old && old.parentNode) old.replaceWith(t.node);
  else renderThread();
}

async function ask(question, extra = {}) {
  question = String(question || '').trim();
  if (!question) return;
  if (S.view !== 'ask') showView('ask');
  const turn = {
    id: ++turnSeq, question, scope: extra.scope || null, inputs: extra.inputs || null,
    status: 'pending', startedAt: performance.now(),
  };
  S.turns.push(turn);
  renderThread();
  renderAskPanel();
  turn.node.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
  $('askbtn').disabled = true;
  try {
    const body = { question };
    if (turn.scope) body.scope = turn.scope;
    if (turn.inputs) body.inputs = turn.inputs;
    turn.answer = await api('/api/ask', body);
    turn.status = 'done';
  } catch (e) {
    turn.status = 'error';
    turn.error = e.message;
  }
  turn.seconds = (performance.now() - turn.startedAt) / 1000;
  $('askbtn').disabled = false;
  replaceTurn(turn);
  renderAskPanel();
}

function turnNode(t) {
  const wrap = el('article', 'turn');
  const bubble = el('div', 'q-bubble');
  if (t.inputs) bubble.appendChild(el('span', 'q-with', 'Again, with the facts you supplied'));
  bubble.appendChild(document.createTextNode(t.question));
  wrap.appendChild(add(el('div', 'q'), bubble));

  const a = el('div', 'a');
  const head = el('div', 'a-head');
  const mark = el('img');
  mark.src = '/brand/icon-transparent-blue.png';
  mark.alt = '';
  add(head, mark, el('span', 'a-who', 'Ross'));
  a.appendChild(head);

  if (t.status === 'pending') {
    head.appendChild(add(el('span', 'a-meta'), 'working · ', askClock(t.startedAt)));
    a.appendChild(waiting('Matching the question to your rules and clauses, then running a rule or quoting the wording.'));
  } else if (t.status === 'error') {
    a.appendChild(notice(t.error, 'bad'));
    a.appendChild(add(el('div', 'row label-after'), btn('Try again', 'btn-secondary btn-sm', () => ask(t.question, { scope: t.scope, inputs: t.inputs }), 'rotate-ccw')));
  } else {
    const parts = t.answer.parts || [];
    head.appendChild(el('span', 'a-meta', `${plural(parts.length, 'part')} · ${fmtDur(t.seconds)}`));
    for (const p of parts) a.appendChild(partNode(p, t));
    if (parts.some((p) => p.kind === 'computed') && parts.some((p) => p.engine === 'VECTOR')) {
      a.appendChild(el('p', 'blend', 'This answer used both engines. The computed part came from running a rule; the quoted part is the documents’ own words. They are kept apart on purpose.'));
    }
    const steps = el('details', 'a-steps');
    const ol = el('ol', 'pipe');
    renderPipe(ol, PIPES.ask, askStates(t.answer));
    add(steps, add(el('summary'), icon('chevron-down', 14), 'How Ross got to this answer'), ol);
    a.appendChild(steps);
  }
  wrap.appendChild(a);
  return wrap;
}

function partNode(p, t) {
  const box = el('section', 'part');
  box.appendChild(add(el('div', 'part-head'), engineLabel(p.engine, p.kind), el('span', 'part-kind', KIND_WORD[p.kind] || p.kind)));
  if (p.kind === 'computed' && p.outputs) {
    box.appendChild(computedNode(p, t));
  } else if (p.kind === 'needs-input') {
    box.appendChild(missingNode(p, t));
  } else if (p.kind === 'quotation') {
    box.appendChild(passageNode(p.text, p.citations, p.score));
  } else if (p.kind === 'caveat') {
    const lead = p.text.split('\n')[0];
    box.appendChild(passageNode(p.text.slice(lead.length), p.citations, null, 'Qualifies the rule above. It is not part of the computation.'));
  } else {
    box.appendChild(notice(p.text, p.kind === 'no-coverage' ? '' : 'warn'));
    if ((p.citations || []).length) box.appendChild(add(el('div', 'label-after'), cites(p.citations)));
  }
  return box;
}

function passageNode(text, citations, score, note) {
  const box = el('div', 'passage');
  const top = el('div', 'passage-top');
  top.appendChild(icon('quote', 12));
  for (const c of citations || []) {
    const b = citeButton(c);
    b.className = 'cite-quiet';
    top.appendChild(b);
  }
  if (note) top.appendChild(el('span', 'passage-note', note));
  if (score !== null && score !== undefined) top.appendChild(el('span', 'passage-score', `match ${Number(score).toFixed(2)}`));
  add(box, top, lawBlock(unquote(text)));
  return box;
}

function computedNode(p, t) {
  const tr = el('div', 'trace');
  const outs = el('div', 'trace-row outs');
  for (const [k, v] of Object.entries(p.outputs)) {
    outs.appendChild(add(el('div', 'out'), el('div', 'out-v', fmtValue(v)), el('div', 'out-k', human(k))));
  }
  tr.appendChild(outs);
  const rule = el('div', 'trace-row');
  add(rule, el('div', 'label', 'The rule that ran'), add(el('div', 'trace-rule'), icon('git-branch', 13), el('span', null, p.scope)));
  if ((p.citations || []).length) add(rule, el('div', 'label', 'Clauses it encodes'), cites(p.citations));
  tr.appendChild(rule);
  if (p.inputs && Object.keys(p.inputs).length) {
    tr.appendChild(add(el('div', 'trace-row'), el('div', 'label', 'The facts it ran on'),
      kv(Object.entries(p.inputs).map(([k, v]) => [cap(human(k)), fmtValue(v)]))));
  }
  const acts = el('div', 'trace-row trace-acts');
  acts.appendChild(btn('Change the facts', 'btn-secondary btn-sm', () => {
    const host = el('div');
    acts.replaceWith(add(el('div', 'trace-row'), host));
    factsForm(host, p.scope, t.question, { prefill: p.inputs || {} });
  }, 'rotate-ccw'));
  tr.appendChild(acts);
  return tr;
}

function missingNode(p, t) {
  const box = el('div', 'missing');
  add(box, add(el('div', 'missing-head'), icon('circle-help', 15), el('span', null, 'Can’t work this out yet: facts missing'), el('span', 'mono', p.scope)));
  const body = el('div', 'missing-body');
  const need = (p.inputs && p.inputs.missing) || [];
  body.appendChild(el('p', 'missing-lede', `The rule that answers this needs ${plural(need.length, 'fact')} about your situation. Ross will not guess them. Fill them in and the rule runs.`));
  const host = el('div');
  body.appendChild(host);
  if ((p.citations || []).length) add(body, el('div', 'label label-after', 'Clauses this rule encodes'), add(el('div', 'label-after'), cites(p.citations)));
  box.appendChild(body);
  factsForm(host, p.scope, t.question, { prefill: t.inputs || {} });
  return box;
}

async function getScope(key) {
  if (!S.scopeCache.has(key)) S.scopeCache.set(key, api('/api/scope?key=' + encodeURIComponent(key)));
  try {
    return await S.scopeCache.get(key);
  } catch (e) {
    S.scopeCache.delete(key);
    throw e;
  }
}

let fieldSeq = 0;

async function factsForm(host, scopeKey, question, { prefill = {} } = {}) {
  host.textContent = '';
  host.appendChild(waiting('Reading what this rule needs…'));
  let s;
  try {
    s = await getScope(scopeKey);
  } catch (e) {
    host.textContent = '';
    host.appendChild(notice(e.message, 'bad'));
    return;
  }
  host.textContent = '';
  const form = el('form', 'factsform');

  const desc = el('details', 'describe');
  const dtext = el('textarea', 'field');
  dtext.rows = 3;
  dtext.placeholder = 'Describe the case in your own words. The agent fills in only what you state, and leaves the rest for you.';
  dtext.setAttribute('aria-label', 'Describe the situation');
  const dbtn = btn('Fill in the facts', 'btn-secondary btn-sm', null, 'bot');
  const dnote = el('p', 'hint');
  add(desc, add(el('summary'), icon('bot', 14), 'Describe the situation instead, and let an agent fill these in'),
    add(el('div', 'describe-body'), dtext, add(el('div', 'row'), dbtn), dnote));

  const grid = el('div', 'facts');
  const judge = new Set(s.judgement_inputs || []);
  const fields = {};
  for (const name of s.inputs) {
    const ty = s.input_schema[name] || 'text';
    const f = el('div', 'fact');
    const id = `fact-${++fieldSeq}`;
    const lab = el('label', null, cap(human(name)));
    lab.htmlFor = id;
    let input;
    if (ty === 'boolean') {
      input = el('select', 'field');
      for (const [v, w] of [['', 'Choose…'], ['true', 'Yes'], ['false', 'No']]) {
        const o = el('option', null, w);
        o.value = v;
        input.appendChild(o);
      }
    } else {
      input = el('input', 'field');
      input.type = ty === 'date' ? 'date' : 'text';
      if (['integer', 'decimal', 'money'].includes(ty)) input.inputMode = 'decimal';
      input.autocomplete = 'off';
    }
    input.id = id;
    input.name = name;
    input.dataset.type = ty;
    if (prefill[name] !== undefined && prefill[name] !== null && typeof prefill[name] !== 'object') input.value = String(prefill[name]);
    const clear = () => input.classList.remove('was-filled');
    input.addEventListener('input', clear);
    input.addEventListener('change', clear);
    add(f, lab, el('span', 'fact-hint', TYPE_HINT[ty] || cap(human(ty))), input);
    if (judge.has(name)) {
      f.appendChild(add(el('p', 'judge-note'), roleTag('person'), 'The documents don’t define this, so only a person decides it.'));
    }
    grid.appendChild(f);
    fields[name] = input;
  }

  const err = el('p', 'form-error');
  err.setAttribute('role', 'alert');
  const run = el('button', 'btn btn-primary');
  run.type = 'submit';
  add(run, icon('play', 14), 'Run the rule');
  add(form, desc, grid, err, add(el('div', 'row'), run));
  host.appendChild(form);

  dbtn.onclick = async () => {
    const q = dtext.value.trim();
    if (!q) { dtext.focus(); return; }
    dbtn.disabled = true;
    dnote.textContent = '';
    dnote.appendChild(waiting('The fact-filling agent is reading your description…'));
    try {
      const r = await api('/api/slotfill', { target: s.key, question: q });
      dnote.textContent = '';
      if (r.error) { dnote.textContent = r.error; return; }
      let n = 0;
      for (const [k, v] of Object.entries(r.facts || {})) {
        if (!fields[k]) continue;
        fields[k].value = String(v);
        fields[k].classList.add('was-filled');
        n += 1;
      }
      dnote.textContent = `The agent filled in ${plural(n, 'fact')}, highlighted in brass.`
        + ((r.omitted || []).length ? ` Your description doesn’t state ${r.omitted.map(human).join(', ')}, so those are left for you.` : '')
        + ' Check each one: nothing has run yet.';
    } catch (e) {
      dnote.textContent = e.message;
    } finally {
      dbtn.disabled = false;
    }
  };

  form.onsubmit = (ev) => {
    ev.preventDefault();
    err.textContent = '';
    const facts = {};
    const missing = [];
    for (const [name, node] of Object.entries(fields)) {
      const ty = node.dataset.type;
      let v = node.value.trim();
      if (v === '') { missing.push(name); continue; }
      if (ty === 'boolean') v = v === 'true';
      else if (ty === 'integer' || ty === 'decimal' || ty === 'money') {
        const n = Number(v.replace(/[£$€%,\s]/g, ''));
        if (!Number.isFinite(n) || (ty === 'integer' && !Number.isInteger(n))) {
          err.textContent = `${cap(human(name))} must be ${ty === 'integer' ? 'a whole number' : 'a number'}.`;
          node.focus();
          return;
        }
        v = n;
      }
      facts[name] = v;
    }
    if (missing.length) {
      err.textContent = `Still needed: ${missing.map(human).join(', ')}. Ross doesn’t guess missing facts.`;
      fields[missing[0]].focus();
      return;
    }
    ask(question, { scope: s.key, inputs: facts });
  };
}

/* The steps are read off the finished answer. While the question is out every
   step waits and a clock runs, because the answer arrives in one piece. */
function askStates(a) {
  const parts = a.parts || [];
  const cat = parts.filter((p) => p.engine === 'CATALA');
  const vec = parts.filter((p) => p.engine === 'VECTOR');
  const computed = cat.filter((p) => p.kind === 'computed');
  const needs = cat.filter((p) => p.kind === 'needs-input');
  const stuck = parts.filter((p) => ['refused', 'error', 'ambiguous-route'].includes(p.kind));
  const rules = new Set(cat.map((p) => p.scope).filter(Boolean)).size;
  const st = {};
  if (!cat.length && !vec.length) {
    st.understand = { status: 'blocked', summary: 'Nothing in your documents covers this question.' };
  } else {
    const found = [rules && `matched ${plural(rules, 'rule')}`, vec.length && `found ${plural(vec.length, 'passage')}`].filter(Boolean);
    st.understand = { status: 'pass', summary: found.length ? cap(found.join(' and ')) + '.' : 'Routed.' };
  }
  if (needs.length) st.facts = { status: 'blocked', summary: `Waiting for you: ${plural(((needs[0].inputs || {}).missing || []).length, 'fact')} to fill in.` };
  else if (computed.length) st.facts = { status: 'pass', summary: `${plural(Object.keys(computed[0].inputs || {}).length, 'fact')} supplied.` };
  else st.facts = { status: 'skipped', summary: 'No rule needed facts for this question.' };
  if (computed.length) st.execute = { status: 'pass', summary: `Ran ${computed.map((p) => p.scope).join(', ')}.` };
  else if (stuck.length) st.execute = { status: 'blocked', summary: cap(KIND_WORD[stuck[0].kind]) + '.' };
  else if (needs.length) st.execute = { status: 'waiting', summary: 'Runs as soon as the facts are in.' };
  else st.execute = { status: 'skipped', summary: 'No rule applies. The answer comes from the wording.' };
  st.quote = vec.length
    ? { status: 'pass', summary: `${plural(vec.length, 'passage')} quoted word for word.` }
    : { status: 'skipped', summary: 'No passage needed quoting.' };
  const kinds = [computed.length && 'computed', needs.length && 'waiting for facts', stuck.length && 'not settled', vec.length && 'quoted'].filter(Boolean);
  st.label = {
    status: parts.length ? 'pass' : 'skipped',
    summary: kinds.length > 1
      ? `${cap(kinds.join(', ').replace(/, ([^,]*)$/, ' and $1'))}: each part labelled, kept apart.`
      : kinds.length ? `Labelled ${kinds[0]}.` : 'Labelled as not answered.',
  };
  return st;
}

function renderAskPanel() {
  const host = $('askpanel');
  host.textContent = '';
  const t = S.turns[S.turns.length - 1];
  host.appendChild(el('p', 'eyebrow', 'How Ross answers'));
  const lede = el('p', 'panel-lede');
  let states = {};
  if (!t) {
    lede.textContent = 'Every question passes through these steps. After you ask, this panel shows what happened at each one.';
  } else if (t.status === 'pending') {
    add(lede, 'Working on your question · ', askClock(t.startedAt));
    for (const d of PIPES.ask) states[d.key] = { status: 'waiting' };
  } else if (t.status === 'error') {
    lede.textContent = 'Your last question did not reach an answer.';
  } else {
    lede.textContent = `Your last question, answered in ${fmtDur(t.seconds)}:`;
    states = askStates(t.answer);
  }
  host.appendChild(lede);
  const ol = el('ol', 'pipe');
  renderPipe(ol, PIPES.ask, states);
  host.appendChild(ol);
  host.appendChild(el('p', 'eyebrow panel-l2', 'Who does what'));
  const who = el('div', 'who');
  for (const k of ['search', 'engine', 'agent', 'person']) who.appendChild(add(el('div'), roleTag(k), el('p', null, ROLES[k].body)));
  host.appendChild(who);
}

function autosize() {
  const f = $('askfield');
  f.style.height = 'auto';
  f.style.height = Math.min(f.scrollHeight + 2, 180) + 'px';
}

/* ============================================================ risk check */

const ARCH = {
  employment: { name: 'Employment lawyer', lead: 'An employee’s lawyer' },
  customer: { name: 'Customer’s counsel', lead: 'A customer’s counsel' },
  regulator: { name: 'Regulator', lead: 'A regulator' },
  auditor: { name: 'Auditor', lead: 'An auditor' },
  contractor: { name: 'Departing contractor', lead: 'A departing contractor' },
};
const archName = (k) => (ARCH[k] ? ARCH[k].name : cap(k));
const archLead = (k) => (ARCH[k] ? ARCH[k].lead : cap(k));

const KLASS = {
  DIVERGENCE: 'What we did differs from what we wrote',
  CONFLICT: 'Two provisions apply and neither takes priority',
  SILENCE: 'The documents say nothing about this case',
  REFUSAL: 'The policy refuses to answer',
  CONTRADICTION: 'Two rules give two answers',
  ADVERSE: 'A reading of our documents that costs us',
};
const KLASS_SHORT = {
  DIVERGENCE: 'Practice differs', CONFLICT: 'Conflict', SILENCE: 'Gap',
  REFUSAL: 'Refusal', CONTRADICTION: 'Contradiction', ADVERSE: 'Adverse reading',
};

/* What an attack's outcome means, in words. Newer servers send `plain_why`;
   an older server sends only the technical reason, which is not shown as the
   explanation for an outcome that is not a failure. */
const OUTCOME = {
  died: 'No exposure: the rule held.',
  known: 'Risk found, already on your list.',
  landed: 'New risk found.',
  malformed: 'The agent’s reply could not be used, so nothing ran.',
  unanswered: 'The model did not answer in time, so nothing ran.',
  stopped: 'Stopped before a verdict.',
};
const OUTCOME_DETAIL = {
  died: 'The rule gave an answer, and no known claim against the company relies on it.',
};
function outcomeDetail(r) {
  if (r.plain_why) return sentence(r.plain_why);
  if (OUTCOME_DETAIL[r.phase]) return OUTCOME_DETAIL[r.phase];
  return r.why ? sentence(r.why) : '';
}

function renderCheck() {
  const t = jobs.check;
  const m = fold(t);
  renderCheckLaunch(t, m);
  renderCheckStats();
  renderCheckPipe(t, m);
  renderCheckNow(t, m);
  renderFleet(t, m);
  renderFindings();
}

/* Seconds per agent attack, measured on this machine in the last finished
   check. With nothing measured, no estimate is offered. */
function perAttackSeconds(t, m) {
  if (!t.job || t.running) return 0;
  const done = [...m.rounds.values()].filter((r) => FINAL.has(r.phase) && r.phase !== 'stopped' && r.startT !== undefined && r.endT !== undefined);
  if (!done.length) return 0;
  return done.reduce((a, r) => a + (r.endT - r.startT), 0) / done.length;
}

function renderCheckLaunch(t, m) {
  const host = $('checklaunch');
  host.textContent = '';
  const j = t.job;
  if (t.running) {
    const r = latestRound(m);
    add(host, el('p', 'eyebrow', 'Check in progress'),
      el('h3', 'launch-title', j.kind === 'fleet' ? 'Agents are attacking one rule' : 'A risk check is running'),
      add(el('p', 'launch-meta'), icon('clock', 13), 'Running for ', clock('check', 0), r ? ` · attack ${r.n} of ${r.of}` : ''),
      el('p', 'launch-note', 'You can leave this page. The check carries on, and this view picks it up again.'));
    return;
  }
  add(host, el('p', 'eyebrow', 'Start a check'), el('h3', 'launch-title', 'Test your documents against realistic claims'));
  const per = perAttackSeconds(t, m);
  const sel = el('select', 'field');
  sel.id = 'checkrounds';
  for (const [n, name, what] of [[0, 'Quick', 'rule checks only, no agents'], [3, 'Short', '3 agent attacks'], [6, 'Standard', '6 agent attacks'], [12, 'Thorough', '12 agent attacks']]) {
    const o = el('option', null, `${name}: ${what}` + (n && per ? `, about ${fmtDur(per * n)}` : ''));
    o.value = String(n);
    sel.appendChild(o);
  }
  sel.value = S.rounds;
  sel.onchange = () => { S.rounds = sel.value; };
  const lab = el('label', 'launch-field');
  lab.htmlFor = 'checkrounds';
  add(lab, el('span', 'field-label', 'How thorough'), sel);
  const go = el('button', 'btn btn-primary');
  go.type = 'button';
  add(go, icon('play', 14), 'Start risk check');
  const note = el('p', 'launch-note');
  const err = el('p', 'form-error');
  err.setAttribute('role', 'alert');
  if (S.live) {
    go.disabled = true;
    note.textContent = `The local model is busy ${jobWord(S.live.kind)}. A check can start when it finishes.`;
  } else if (S.agent && !S.agent.available) {
    note.textContent = 'The local model is offline, so the agents are skipped and only the rule checks run.';
  } else if (per) {
    note.textContent = `On this machine an agent attack took about ${fmtDur(per)} last time. You can leave the page while it runs.`;
  } else {
    note.textContent = 'Each agent attack takes several minutes on the local model. You can leave the page while it runs.';
  }
  go.onclick = async () => {
    go.disabled = true;
    err.textContent = '';
    try {
      const job = await api('/api/exposure/run', { kind: 'sweep', rounds: Number(sel.value) });
      S.newIds.clear();
      S.round = null;
      S.live = job;
      onJob('check', await jobs.check.attach(job.id));
      renderCheck();
      renderStatus();
    } catch (e) {
      err.textContent = e.message;
      go.disabled = false;
    }
  };
  add(host, lab, add(el('div', 'row'), go), note, err);
  if (j) {
    host.appendChild(el('p', 'launch-last', j.status === 'done'
      ? `Last check finished ${ago(j.started + j.seconds)}, in ${fmtDur(j.seconds)}.`
      : `Last check stopped after ${fmtDur(j.seconds)}${j.error ? ': ' + j.error : '.'}`));
  }
}

function renderCheckStats() {
  const host = $('checkstats');
  host.textContent = '';
  const s = S.summary;
  const stat = (n, label, sub) => add(el('div', 'stat'), el('div', 'stat-n', String(n)), el('div', 'stat-l', label), el('div', 'stat-s', sub));
  if (!s) {
    host.appendChild(add(el('div', 'stat'), S.queueError ? notice(S.queueError, 'bad') : waiting('Loading findings…')));
    return;
  }
  add(host,
    stat(s.open, s.open === 1 ? 'open finding' : 'open findings', 'each one reproduced by running the rule'),
    stat(s.costed, s.costed === 1 ? 'has a figure attached' : 'have a figure attached', 'the amount is computed, never estimated'),
    stat(S.scopes.length, S.scopes.length === 1 ? 'rule under test' : 'rules under test', S.unmapped.length ? `${S.unmapped.length} more not yet covered` : 'every rule is covered'));
}

function renderCheckPipe(t, m) {
  const host = $('checkpipe');
  const j = t.job;
  const meta = $('checkpipemeta');
  meta.textContent = '';
  if (j) {
    if (t.running) add(meta, 'running · ', clock('check', 0));
    else meta.textContent = `last run ${ago(j.started + j.seconds)} · took ${fmtDur(j.seconds)}`;
  }
  const states = {};
  if (j) {
    for (const d of PIPES.check) {
      const s = m.stages.get(d.key);
      if (!s) {
        states[d.key] = t.running ? { status: 'waiting', meta: 'Waits for the step before it' } : { status: 'skipped', summary: 'Not part of this run.' };
        continue;
      }
      const st = { status: { running: 'running', done: 'pass', failed: 'blocked', skipped: 'skipped' }[s.status] || 'idle', summary: s.summary };
      if (s.status === 'running') {
        st.meta = add(el('span'), s.note && !m.rounds.size ? cap(s.note) + ' · ' : 'Running for ', clock('check', s.startT));
      } else if (s.status === 'done') {
        const took = (s.endT || 0) - (s.startT || 0);
        st.meta = took < 1 ? 'Done in under a second' : `Done in ${fmtDur(took)}`;
      } else if (s.status === 'failed' && !s.summary) {
        st.summary = 'Stopped here.';
      }
      if (d.key === 'reverify' && (m.verifies.length || s.total)) {
        const ticks = el('div', 'ticks');
        const total = s.total || m.verifies.length;
        for (let i = 0; i < total; i++) {
          const v = m.verifies[i];
          const tk = el('span', 'tick' + (v ? (v.ok ? ' is-ok' : ' is-stale') : ''));
          tk.title = v ? `${v.id}: ${v.why}` : 'not yet re-run';
          ticks.appendChild(tk);
        }
        st.extra = ticks;
      }
      if (d.key === 'fleet' && (m.rounds.size || s.total)) st.extra = attackStrip(t, m, s);
      states[d.key] = st;
    }
  }
  renderPipe(host, PIPES.check, states);
  if (m.failed) host.appendChild(add(el('li', 'label-after'), notice(`The check stopped: ${m.failed.error}`, 'bad')));
}

const PHASE_WORD = {
  proposing: 'agent drafting a claim', adjudicating: 'rule engine testing the claim', died: 'no exposure, the rule held',
  known: 'risk already on the list', landed: 'new risk found', malformed: 'no usable claim',
  unanswered: 'the model did not answer in time', stopped: 'stopped when the check ended',
};

function attackStrip(t, m, stage) {
  const wrap = el('div', 'attacks');
  const planned = stage.total || (m.plan && m.plan.rounds) || (t.job.params || {}).rounds || 0;
  const total = Math.max(planned, m.rounds.size);
  const strip = el('div', 'strip');
  for (let n = 1; n <= total; n++) {
    const r = m.rounds.get(n);
    const phase = r ? r.phase : 'pending';
    const b = el('button', 'atk p-' + phase + (S.round === n ? ' is-sel' : ''));
    b.type = 'button';
    b.title = r ? `Attack ${n}: ${archName(r.archetype)}, ${PHASE_WORD[phase] || phase}` : `Attack ${n}: not yet run`;
    b.setAttribute('aria-label', b.title);
    b.disabled = !r;
    b.onclick = () => { S.round = S.round === n ? null : n; renderCheck(); };
    strip.appendChild(b);
  }
  const fin = [...m.rounds.values()].filter((r) => FINAL.has(r.phase));
  const landed = fin.filter((r) => r.phase === 'landed').length;
  const legend = el('ul', 'legend');
  for (const [cls, w] of [['p-died', 'No exposure'], ['p-landed', 'New risk'], ['p-known', 'Already listed'], ['p-malformed', 'No usable claim'], ['p-proposing', 'In progress'], ['', 'Not yet run']]) {
    legend.appendChild(add(el('li'), el('i', cls), w));
  }
  add(wrap, strip, el('p', 'strip-count', `${fin.length} of ${total} attacks finished · ${plural(landed, 'new risk')} · select an attack to read it`), legend);
  return wrap;
}

function renderCheckNow(t, m) {
  const host = $('checknow');
  host.textContent = '';
  const latest = latestRound(m);
  const r = S.round ? m.rounds.get(S.round) : latest;
  if (!r) {
    const fs = m.stages.get('fleet');
    host.appendChild(fs && fs.status === 'running'
      ? waiting('Loading the model into memory. The first attack starts when it is in: ', clock('check', fs.startT))
      : el('p', 'now-empty', 'When a check runs, each agent attack appears here as it happens: the claim an agent proposes, and what the rule engine found when it ran that claim.'));
    return;
  }
  const top = el('div', 'now-top');
  top.appendChild(el('p', 'eyebrow', `Attack ${r.n} of ${r.of}` + (S.round && latest && S.round !== latest.n ? ' · an earlier attack' : '')));
  if (!FINAL.has(r.phase) && t.running) top.appendChild(add(el('p', 'now-clock'), clock('check', r.startT)));
  else if (r.endT !== undefined && r.startT !== undefined) top.appendChild(el('p', 'now-clock', `took ${fmtDur(r.endT - r.startT)}`));
  top.appendChild(add(el('h4', 'now-title'), `${archLead(r.archetype)} tests `, el('span', 'mono', r.scope)));
  host.appendChild(top);

  const ph = r.phase;
  const status = ph === 'proposing' ? ['running', 'waiting', 'waiting']
    : ph === 'adjudicating' ? ['pass', 'running', 'waiting']
      : ['malformed', 'unanswered'].includes(ph) ? ['blocked', 'skipped', 'skipped']
        : ph === 'stopped' ? [r.narrative ? 'pass' : 'blocked', r.narrative ? 'blocked' : 'skipped', 'skipped']
          : ph === 'landed' ? ['pass', 'pass', 'blocked'] : ['pass', 'pass', 'pass'];
  let verdict = OUTCOME[ph] || '';
  if (ph === 'landed' && r.recorded) verdict = `New risk found, listed as ${r.recorded}.`;
  const mini = el('ol', 'mini');
  [['The agent proposes a claim', 'agent'], ['The rule engine runs it', 'engine'], ['Verdict', null]].forEach(([title, role], i) => {
    const mark = el('span', 'step-mark');
    mark.appendChild(icon(STEP_ICON[status[i]], 12));
    mini.appendChild(add(el('li', 'mini-step s-' + status[i]), mark,
      add(el('div'), el('span', 'mini-title', title), role && roleTag(role), i === 2 && verdict && el('span', 'mini-verdict', verdict))));
  });
  host.appendChild(mini);
  if (FINAL.has(ph)) {
    const detail = outcomeDetail(r);
    if (detail && detail !== verdict) host.appendChild(el('p', 'now-why', detail));
  }
  if (r.narrative) {
    add(host, el('p', 'label now-l', FINAL.has(ph) ? 'The claim, in the agent’s words' : 'The claim, in the agent’s words · not yet checked'),
      el('blockquote', 'claim', r.narrative));
  }
  if ((r.citations || []).length) host.appendChild(add(el('div', 'label-after'), cites(r.citations)));
  if (r.facts && Object.keys(r.facts).length) {
    const d = el('details', 'facts-d');
    add(d, add(el('summary'), icon('chevron-down', 14), 'The facts the rule engine ran'),
      kv(Object.entries(r.facts).map(([k, v]) => [cap(human(k)), fmtValue(v)])));
    host.appendChild(d);
  }
  const acts = el('div', 'row follow');
  if (ph === 'landed' && r.recorded) acts.appendChild(btn(`Open ${r.recorded}`, 'btn-primary btn-sm', () => openFinding(r.recorded), 'arrow-right'));
  if (S.round && latest && S.round !== latest.n) acts.appendChild(btn('Follow the latest attack', 'btn-ghost btn-sm', () => { S.round = null; renderCheck(); }));
  if (acts.childElementCount) host.appendChild(acts);
}

function renderFleet(t, m) {
  const host = $('fleet');
  host.textContent = '';
  const fleet = S.agent && S.agent.fleet && S.agent.fleet.length ? S.agent.fleet : Object.keys(ARCH).map((k) => ({ key: k, purpose: '' }));
  const latest = latestRound(m);
  const active = t.running && latest && !FINAL.has(latest.phase) ? latest : null;
  const tally = {};
  for (const r of m.rounds.values()) {
    const x = tally[r.archetype] || (tally[r.archetype] = { tried: 0, landed: 0 });
    if (FINAL.has(r.phase)) x.tried += 1;
    if (r.phase === 'landed' || r.phase === 'known') x.landed += 1;
  }
  const found = {};
  for (const e of S.queue) {
    const o = e.origin || '';
    if (o.startsWith('fleet:')) (found[o.slice(6)] = found[o.slice(6)] || []).push(e.id);
  }
  const when = t.running ? 'this check' : 'the last check';
  for (const a of fleet) {
    const on = active && active.archetype === a.key;
    const x = tally[a.key];
    const f = found[a.key] || [];
    host.appendChild(add(el('article', 'agent' + (on ? ' is-on' : '')),
      add(el('div', 'agent-top'), icon('bot', 15), el('h4', 'agent-name', archName(a.key))),
      el('p', 'agent-purpose', a.purpose),
      add(el('p', 'agent-state'), el('span', 'dot' + (on ? ' is-run' : '')), on ? (active.phase === 'proposing' ? 'Drafting a claim' : 'Waiting on the rule engine') : 'Idle'),
      el('p', 'agent-tally', (x && x.tried ? `${plural(x.tried, 'attack')} in ${when} · ${x.landed} landed` : `No attacks in ${when}`) + (f.length ? ` · found ${f.join(', ')}` : ''))));
  }
}

function amountText(e) {
  if (e.amount === null || e.amount === undefined || e.amount === '') return null;
  const n = Number(e.amount);
  if (!Number.isFinite(n)) return String(e.amount);
  const sc = S.scopes.find((s) => s.key === e.scope);
  if (sc && sc.unit === 'money') return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return n.toLocaleString('en-GB', { maximumFractionDigits: 6 });
}

function amountUnit(e) {
  const sc = S.scopes.find((s) => s.key === e.scope);
  const unit = sc && sc.unit && sc.unit !== 'money' ? sc.unit + ', ' : '';
  return `${unit}at stake on ${e.population || 'one case'}`;
}

function originWord(e) {
  const o = e.origin || '';
  if (o.startsWith('fleet:')) return `${archName(o.slice(6))}, an agent in the risk check`;
  if (o.startsWith('operations:')) return `Comparing past decisions with the policy (decision ${o.slice(11)})`;
  if (o === 'web') return 'Tested by hand in the classic console';
  return o || 'Not recorded';
}

function renderFindings() {
  const sel = $('findfilter');
  if (!sel.options.length) {
    const any = el('option', null, 'Every kind of finding');
    any.value = '';
    sel.appendChild(any);
    for (const [k, w] of Object.entries(KLASS)) {
      const o = el('option', null, w);
      o.value = k;
      sel.appendChild(o);
    }
    sel.onchange = () => { S.filter = sel.value; renderFindings(); };
  }
  sel.value = S.filter;
  const host = $('findings');
  host.textContent = '';
  if (S.queueError) host.appendChild(notice(S.queueError, 'bad'));
  const items = S.queue.filter((e) => !S.filter || e.klass === S.filter);
  if (!items.length) {
    host.appendChild(S.queue.length
      ? emptyState('shield-check', 'Nothing of this kind', 'Choose another kind of finding above.')
      : emptyState('shield-check', 'No findings', 'Run a risk check. Any claim that holds up under your documents is listed here.'));
    return;
  }
  for (const e of items) {
    const row = el('button', 'finding');
    row.type = 'button';
    const amt = amountText(e);
    add(row,
      add(el('span', 'finding-top'), add(el('span', 'badge t-flag'), el('span', 'bdot'), KLASS_SHORT[e.klass] || e.klass),
        el('span', 'mono finding-id', e.id), S.newIds.has(e.id) && el('span', 'badge t-review', 'New this check')),
      el('span', 'finding-head', cap(human(e.headline))),
      add(el('span', 'finding-foot'), el('span', 'mono', e.scope), e.found && el('span', null, `found ${e.found}`)),
      add(el('span', 'finding-amt'), amt && el('span', 'finding-amt-v', amt), el('span', 'finding-amt-l', amt ? 'at stake' : 'no figure')));
    row.onclick = () => openFinding(e.id);
    host.appendChild(row);
  }
}

function openFinding(id) {
  openDrawer(findingDrawer(id), true);
}

function findingDrawer(id) {
  return (body, setHead) => {
    const e = S.queue.find((x) => x.id === id);
    if (!e) {
      setHead(id, 'Finding');
      body.appendChild(notice('This finding is not on the list any more. Reload the page to see the current list.', 'warn'));
      return;
    }
    drawer.finding = id;
    setHead(`${e.id} · found ${e.found || 'on an unrecorded date'}`, KLASS[e.klass] || e.klass);
    body.appendChild(el('p', 'd-lede', cap(human(e.headline))));
    const amt = amountText(e);
    const fig = el('div', 'figure');
    if (amt) add(fig, el('div', 'figure-v', amt), el('div', 'figure-l', amountUnit(e)));
    else fig.appendChild(el('div', 'figure-l', 'No figure is attached to this finding.'));
    if (e.amount_basis) fig.appendChild(el('p', 'figure-basis mono', e.amount_basis));
    body.appendChild(fig);

    const proof = el('ol', 'pipe');
    const fromAgent = (e.origin || '').startsWith('fleet:');
    const outs = Object.entries(e.outputs || {});
    renderPipe(proof, [
      { key: 'found', title: 'Found', roles: [fromAgent ? 'agent' : 'engine'] },
      { key: 'ran', title: 'Confirmed by running the rule', roles: ['engine'] },
      { key: 'kept', title: 'Re-checked on every run', roles: ['engine'] },
    ], {
      found: { status: 'pass', summary: originWord(e) },
      ran: { status: 'pass', summary: outs.length ? `${e.scope} computed ${outs.map(([k, v]) => `${human(k)} ${fmtValue(v)}`).join(', ')}.` : `${e.scope} reached this outcome on the facts below.` },
      kept: { status: 'pass', summary: 'A finding that stops reproducing is flagged, not kept.' },
    });
    body.appendChild(section('How this finding was confirmed', proof));

    if (e.narrative) body.appendChild(section('What happened', lawBlock(e.narrative)));
    if (e.why) body.appendChild(section('Why it is a risk', lawBlock(e.why)));
    body.appendChild(section('Details', kv([['Who could raise it', cap(e.archetype)], ['What the rule decides about', e.population], ['Rule', e.scope]], 'kv-plain')));
    const refs = [...new Set([...(e.citations || []), ...(e.clause_chain || []).flatMap((g) => g.clause_refs || [])])];
    if (refs.length) body.appendChild(section('Clauses involved', cites(refs)));
    if (Object.keys(e.facts || {}).length) {
      body.appendChild(section('The facts it was run on', kv(Object.entries(e.facts).map(([k, v]) => [cap(human(k)), fmtValue(v)]))));
    }
    if (outs.length) body.appendChild(section('What the policy computes', kv(outs.map(([k, v]) => [cap(human(k)), fmtValue(v)]))));
    if (e.diagnostic) body.appendChild(section('Diagnostic', el('pre', 'diag', e.diagnostic)));
    if (e.demand_letter) {
      body.appendChild(section('The letter, from the other side',
        el('p', 'hint', 'Written by an agent from a finding the rule engine had already confirmed. Every figure in it was computed; the agent was not allowed to produce one.'),
        add(el('div', 'label-after'), el('div', 'letter', e.demand_letter))));
    }

    const letter = btn(e.demand_letter ? 'Write the letter again' : 'Write the demand letter', 'btn-secondary btn-sm', null, 'file-pen-line');
    const fix = btn('Propose a fix and test it', 'btn-secondary btn-sm', null, 'rotate-ccw');
    letter.disabled = !!S.live || e.headline_direction === 'more';
    fix.disabled = !!S.live;
    const err = el('p', 'form-error');
    const start = async (kind) => {
      err.textContent = '';
      letter.disabled = true;
      fix.disabled = true;
      try {
        const job = await api('/api/exposure/run', { kind, id: e.id });
        S.live = job;
        onJob('agent', await jobs.agent.attach(job.id));
        renderStatus();
      } catch (x) {
        err.textContent = x.message;
        letter.disabled = e.headline_direction === 'more';
        fix.disabled = false;
      }
    };
    letter.onclick = () => start('letter');
    fix.onclick = () => start('close');
    let note;
    if (e.headline_direction === 'more') note = 'No letter for this one: the decision was more generous than the policy, so nobody has a claim. It is still evidence that the company does not apply its own reading.';
    else if (S.live) note = `Available when the local model is free. It is busy ${jobWord(S.live.kind)}.`;
    else note = 'Each takes a few minutes on the local model. A proposed fix is only a proposal: Ross applies it to the rule, re-runs every check, reports what changed, and puts the rule back.';
    const jobHost = el('div');
    jobHost.id = 'agentjob';
    body.appendChild(section('Hand it to an agent', el('p', 'hint', note), add(el('div', 'row label-after'), letter, fix), err, jobHost));
    renderAgentJob();
  };
}

function renderAgentJob() {
  const host = $('agentjob');
  if (!host) return;
  host.textContent = '';
  const t = jobs.agent;
  const j = t.job;
  if (!j || (j.params || {}).id !== drawer.finding) return;
  const m = fold(t);
  const defs = [...m.stages.values()].map((s) => ({ key: s.key, title: s.title, roles: s.key === 'measure' ? ['engine'] : ['agent'] }));
  const states = {};
  for (const s of m.stages.values()) {
    states[s.key] = {
      status: SUB_STATUS[s.status] || 'idle',
      meta: s.status === 'running' ? add(el('span'), 'Running for ', clock('agent', s.startT)) : null,
      summary: s.summary,
    };
  }
  const ol = el('ol', 'pipe');
  renderPipe(ol, defs, states);
  host.appendChild(add(el('div', 'label-after'), ol));
  if (!m.stages.size && t.running) host.appendChild(waiting('Starting the agent…'));
  if (j.status === 'failed') host.appendChild(notice(j.error, 'bad'));
  if (j.status !== 'done' || !j.result) return;
  const r = j.result;
  if (j.kind === 'letter' && r.letter) host.appendChild(section('The letter, from the other side', el('div', 'letter', r.letter)));
  if (j.kind === 'close' && r.edit) {
    host.appendChild(section('The amendment to the document', lawBlock(r.edit.clause_amendment)));
    host.appendChild(section('Why', lawBlock(r.edit.rationale)));
    host.appendChild(section('The change to the rule',
      el('pre', 'diag', String(r.edit.old).split('\n').map((l) => '- ' + l).join('\n') + '\n' + String(r.edit.new).split('\n').map((l) => '+ ' + l).join('\n'))));
    host.appendChild(section('What the re-run showed', notice((r.report || []).join('\n'), r.ok ? '' : 'warn')));
  }
}

/* ================================================================= draft */

const SUB_STATUS = { running: 'running', done: 'pass', failed: 'blocked' };
const STAGE_WORD = {
  retrieve: 'finding precedent', draft: 'drafting', catala: 'the rule checks', encode: 'the rule checks',
  screen: 'the independent review', roundtrip: 'the roundtrip check', issue: 'issuing', aborted: 'stopped by hand',
};
const stageWord = (s) => (s ? STAGE_WORD[String(s).split(/[-\s]/)[0]] || human(s) : 'an unrecorded step');

function renderDraft() {
  renderDraftForm();
  renderDraftLive();
}

function renderDraftForm() {
  const b = $('draftbtn');
  const mine = jobs.draft.running;
  b.disabled = !!S.live || !!S.draftUnavailable;
  $('drafthint').textContent = S.draftUnavailable
    || (mine ? 'A draft is in progress. Follow its steps below.'
      : S.live ? `The local model is busy ${jobWord(S.live.kind)}. Drafting can start when it finishes.`
        : 'A draft takes 20 to 60 minutes on the local model. You can leave this page; the run carries on, and this view picks it up again.');
}

async function startDraft(ev) {
  ev.preventDefault();
  const err = $('drafterror');
  err.textContent = '';
  const request = $('draftrequest').value.trim();
  if (request.length < 12) {
    err.textContent = 'Describe the document in a sentence or more.';
    $('draftrequest').focus();
    return;
  }
  const num = (id, d) => Number($(id).value) || d;
  const body = {
    request,
    catala_attempts: num('draftattempts', 3),
    screen_rounds: num('draftrounds', 2),
    no_roundtrip: $('draftskiprt').checked,
    emit_failed_pdf: $('draftfailedpdf').checked,
  };
  if ($('draftdate').value) body.effective_date = $('draftdate').value;
  $('draftbtn').disabled = true;
  try {
    const job = await api('/api/generate', body);
    S.live = job;
    onJob('draft', await jobs.draft.attach(job.id));
    renderDraft();
    renderStatus();
    $('draftlivecard').scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
  } catch (e) {
    err.textContent = e.message;
    renderDraftForm();
  }
}

function renderDraftLive() {
  const t = jobs.draft;
  const j = t.job;
  const m = fold(t);
  const head = $('drafthead');
  const out = $('draftout');
  const meta = $('draftlivemeta');
  head.textContent = '';
  out.textContent = '';
  meta.textContent = '';
  if (!j) {
    $('draftlive-eyebrow').textContent = 'The pipeline';
    $('draftlive-title').textContent = 'How a request becomes a document';
    head.appendChild(el('p', 'panel-note', 'Every draft passes these six steps. When a check fails, the work goes back a step to be fixed. Nothing is issued until every step passes.'));
    renderPipe($('draftpipe'), PIPES.draft, {});
    return;
  }
  $('draftlive-eyebrow').textContent = t.running ? 'Drafting now' : 'The latest draft';
  $('draftlive-title').textContent = t.running ? 'Following the draft, step by step' : 'How the latest draft went';
  if (t.running) add(meta, 'running · ', clock('draft', 0));
  else meta.textContent = `finished ${ago(j.started + j.seconds)} · took ${fmtDur(j.seconds)}`;
  const req = (j.params || {}).request;
  if (req) head.appendChild(add(el('div', 'draft-request'), el('span', 'label', 'The request'), el('blockquote', 'claim', req + (req.length >= 200 ? '…' : ''))));

  /* Stage keys carry their attempt: draft-2, catala-1-3, screen-2, roundtrip-1.
     Each belongs to one step of the pipeline, and its attempts are listed under it. */
  const groups = {};
  for (const s of m.stages.values()) {
    const k = s.key.split('-')[0];
    (groups[k] = groups[k] || []).push(s);
  }
  const states = {};
  for (const d of PIPES.draft) {
    if (d.key === 'issue') continue;
    const subs = groups[d.key];
    if (!subs) {
      states[d.key] = t.running ? { status: 'waiting' } : { status: 'skipped', summary: 'Not reached in this run.' };
      continue;
    }
    const last = subs[subs.length - 1];
    const st = { status: SUB_STATUS[last.status] || 'idle' };
    if (last.status === 'running') st.meta = add(el('span'), 'Running for ', clock('draft', last.startT));
    if (subs.length === 1) st.summary = last.summary;
    else st.subs = subs.map((s) => ({ title: s.title, status: SUB_STATUS[s.status] || 'idle', summary: s.summary }));
    if (d.key === 'draft' && subs.length > 1) st.loop = `Rewritten ${plural(subs.length - 1, 'time')} after a failed check`;
    if (d.key === 'catala' && subs.length > 1) st.loop = `${subs.length} encoding attempts`;
    states[d.key] = st;
  }
  if (j.status === 'done' && j.result) {
    const r = j.result;
    states.issue = r.passed
      ? { status: 'pass', summary: `Issued: ${r.title || r.name}.` }
      : { status: 'blocked', summary: `Not issued. It stopped at ${stageWord(r.failure_stage)}.` };
  } else if (j.status === 'failed') {
    states.issue = { status: 'blocked', summary: 'The run stopped before a verdict.' };
  } else {
    states.issue = { status: 'waiting' };
  }
  renderPipe($('draftpipe'), PIPES.draft, states);
  if (t.running && !m.stages.size) out.appendChild(add(el('div', 'label-after'), waiting('Starting the pipeline…')));
  if (j.status === 'failed') out.appendChild(add(el('div', 'label-after'), notice(j.error, 'bad')));
  if (j.status === 'done' && j.result) {
    const r = j.result;
    out.appendChild(verdict(r.passed, r.title, r.failure_stage, r.failure_reason, r.name, !!r.pdf_url));
    out.appendChild(add(el('div', 'row label-after'), btn('Read the full record', 'btn-ghost btn-sm', () => openDrawer(runDrawer(r.name), true), 'file-text')));
  }
  if (t.lines.length) {
    const det = el('details', 'transcript');
    det.open = S.transcriptOpen;
    det.addEventListener('toggle', () => { S.transcriptOpen = det.open; });
    add(det, add(el('summary'), icon('chevron-down', 14), `Transcript, ${plural(t.lines.length, 'line')}`), el('pre', 'diag', t.lines.slice(-300).join('\n')));
    out.appendChild(det);
  }
}

function verdict(passed, title, stage, reason, name, hasPdf) {
  const box = el('div', 'verdict ' + (passed ? 'is-issued' : 'is-not'));
  const b = el('div', 'verdict-body');
  b.appendChild(el('p', 'verdict-title', passed ? (title || name) : `Stopped at ${stageWord(stage)}`));
  if (!passed && reason) b.appendChild(el('p', 'verdict-reason', reason));
  if (hasPdf) {
    const a = el('a', 'btn ' + (passed ? 'btn-primary' : 'btn-secondary') + ' btn-sm');
    a.href = '/api/generate/pdf?name=' + encodeURIComponent(name);
    a.target = '_blank';
    a.rel = 'noopener';
    add(a, icon('file-down', 14), passed ? 'Open the PDF' : 'Open the failed draft, stamped as failing');
    b.appendChild(a);
  }
  add(box, el('p', 'verdict-w', passed ? 'Issued' : 'Not issued'), b);
  return box;
}

async function loadRuns() {
  const host = $('draftruns');
  let d;
  try {
    d = await api('/api/generate/runs');
    S.draftUnavailable = '';
  } catch (e) {
    host.textContent = '';
    if (e.status === 404 && /No endpoint/.test(e.message)) {
      S.draftUnavailable = 'This server was started before drafting was added. Restart it with ./scripts/start.sh to use this tool.';
      host.appendChild(notice(S.draftUnavailable, 'warn'));
    } else {
      host.appendChild(notice(e.message, 'bad'));
    }
    renderDraftForm();
    return;
  }
  renderDraftForm();
  host.textContent = '';
  if (!d.runs.length) {
    host.appendChild(emptyState('file-pen-line', 'Nothing drafted yet', 'Drafts you start are listed here, issued or not.'));
    return;
  }
  const liveName = jobs.draft.running ? (jobs.draft.job.progress || {}).name : null;
  for (const r of d.runs) {
    const b = el('button', 'draftrun');
    b.type = 'button';
    const word = !r.finished ? (r.name === liveName ? 'Drafting now' : 'Unfinished') : r.passed ? 'Issued' : 'Not issued';
    const tone = !r.finished ? 't-review' : r.passed ? 't-cited' : 't-flag';
    add(b,
      add(el('span', 'draftrun-top'), add(el('span', 'badge ' + tone), el('span', 'bdot'), word), el('span', 'draftrun-when', ago(r.modified))),
      el('span', 'draftrun-title', r.title || r.request || r.name),
      el('span', 'draftrun-meta', r.finished
        ? (r.passed ? `${r.doc_id} · ${fmtDur(r.seconds)}` : `Stopped at ${stageWord(r.failure_stage)} · ${fmtDur(r.seconds)}`)
        : r.name));
    b.disabled = !r.finished;
    b.onclick = () => openDrawer(runDrawer(r.name), true);
    host.appendChild(b);
  }
}

const SCREEN_GROUPS = [
  ['confirmed', 'Confirmed by running the rule'],
  ['agreed', 'Raised by more than one reviewer'],
  ['open_questions', 'Judgement calls left open'],
  ['discarded', 'Discarded'],
];

function runDrawer(name) {
  return async (body, setHead) => {
    setHead(name, 'Draft record');
    body.appendChild(waiting('Reading the run’s record…'));
    let d;
    try {
      d = await api('/api/generate/run?name=' + encodeURIComponent(name));
    } catch (e) {
      body.textContent = '';
      body.appendChild(notice(e.message, 'bad'));
      return;
    }
    body.textContent = '';
    const r = d.run;
    const s = d.summary || {};
    setHead([r.doc_id, name].filter(Boolean).join(' · '), r.title || 'Untitled draft');
    body.appendChild(verdict(r.passed, r.title, r.failure_stage, r.failure_reason, name, !!s.has_pdf));
    body.appendChild(section('The request', el('blockquote', 'claim', r.prompt)));
    body.appendChild(section('The run', kv([
      ['Drafted by', r.model], ['Encoding attempts', String(r.catala_iterations || 0)],
      ['Review rounds', String(r.screen_rounds || 0)], ['Took', fmtDur(r.seconds || 0)],
    ], 'kv-plain')));

    const gates = [...((r.gates && r.gates.gates) || []), ...(r.g5 ? [r.g5] : [])];
    if (gates.length) {
      const table = el('table', 'gates');
      const hr = table.appendChild(el('thead')).appendChild(el('tr'));
      for (const h of ['Check', 'Result', 'Detail']) hr.appendChild(el('th', null, h));
      const tb = table.appendChild(el('tbody'));
      for (const g of gates) {
        tb.appendChild(add(el('tr'), add(el('td'), el('span', 'mono', g.id), ` ${g.name || ''}`),
          el('td', g.skipped ? 'g-skip' : g.ok ? 'g-ok' : 'g-no', g.skipped ? 'Skipped' : g.ok ? 'Passed' : 'Failed'),
          el('td', null, g.detail || '')));
      }
      body.appendChild(section('Rule checks', add(el('div', 'table-wrap'), table)));
    } else {
      body.appendChild(section('Rule checks', el('p', 'hint', 'No rule check ran on this draft.')));
    }

    (r.screens || []).forEach((sc, i) => {
      const sec = section(`Review, round ${i + 1}`,
        add(el('div', 'row'), add(el('span', 'badge ' + (sc.passed ? 't-cited' : 't-flag')), el('span', 'bdot'), sc.verdict)),
        el('p', 'hint label-after', (sc.agents || []).map((a) => `${a.agent}: ${a.ok ? plural((a.findings || []).length, 'finding') : 'unusable, ' + a.error} (${fmtDur(a.seconds)})`).join(' · ')));
      for (const [key, label] of SCREEN_GROUPS) {
        const fs = sc[key] || [];
        if (!fs.length) continue;
        const g = el('details', 'transcript');
        g.open = key !== 'discarded';
        g.appendChild(add(el('summary'), icon('chevron-down', 14), `${label}, ${fs.length}`));
        for (const f of fs) {
          g.appendChild(add(el('div', 'rf'),
            add(el('div', 'rf-top'), el('span', 'badge', human(String(f.kind).toLowerCase())), el('span', 'mono', (f.clause_ids || []).join(', ')),
              el('span', null, `from ${f.agent}` + ((f.agreed_with || []).length ? `, agreed by ${f.agreed_with.join(', ')}` : ''))),
            el('p', 'rf-sum', f.summary),
            f.quote && el('p', 'rf-meta', `“${f.quote}”`),
            f.verified_by && el('p', 'rf-meta', `Verified: ${f.verified_by}`),
            f.discard_reason && el('p', 'rf-meta', `Discarded: ${f.discard_reason}`),
            f.fix && el('p', 'rf-meta', `Suggested fix: ${f.fix}`)));
        }
        sec.appendChild(g);
      }
      body.appendChild(sec);
    });

    if ((r.context || []).length) {
      body.appendChild(section('Precedent it drafted from', kv(r.context.map((c) => [c.ref, `${c.kind}${c.score === null || c.score === undefined ? '' : ` · match ${Number(c.score).toFixed(2)}`}`]))));
    }
    if (d.report) {
      const det = el('details', 'transcript');
      add(det, add(el('summary'), icon('chevron-down', 14), 'The full report'), el('pre', 'diag', d.report));
      body.appendChild(add(el('div', 'd-sec'), det));
    }
  };
}

/* ======================================================== document watch

   The contradiction watch runs inside the server, around the clock
   (lks.doc_watch). This page reads its status and its stored checks from
   /api/watch every ten seconds while it is open. A check in progress is an
   ordinary server job of kind `doc-watch`; it reports progress as transcript
   lines, so the steps drawn for it are read off those lines and nothing else. */

const W = { data: null, error: null, limit: 10, filter: '', timer: null, loading: false, flash: null };

const WATCH_STATUS = {
  pending: ['Waiting to be checked', 't-review'],
  checking: ['Being checked now', 't-review'],
  checked: ['Checked', 't-cited'],
  incomplete: ['Check did not finish', 't-flag'],
  baseline: ['Baseline', ''],
  unreadable: ['Could not be read', 't-flag'],
  removed: ['Removed', ''],
};
const WATCH_FILTERS = [
  ['', 'Every document'], ['attention', 'Needs attention'], ['waiting', 'Waiting or being checked'],
  ['checked', 'Checked'], ['baseline', 'Baseline'], ['removed', 'Removed'],
];

const isoEpoch = (iso) => (iso ? Date.parse(iso) / 1000 : 0);
const when = (epoch) => new Date(epoch * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
function sinceScan(epoch) {
  const d = Date.now() / 1000 - epoch;
  return d < 60 ? `${Math.max(1, Math.round(d))} seconds ago` : ago(epoch);
}

async function loadWatch() {
  if (W.loading) return;
  W.loading = true;
  try {
    W.data = await api(`/api/watch?limit=${W.limit}`);
    W.error = null;
  } catch (e) {
    W.error = e;
  } finally {
    W.loading = false;
  }
  renderWatchNav();
  if (S.view === 'watch') {
    renderWatch();
    $('topsub').textContent = VIEWS.watch.sub();
  }
}

function scheduleWatch() {
  clearTimeout(W.timer);
  W.timer = setTimeout(async () => {
    if (S.view !== 'watch') return;
    await loadWatch();
    scheduleWatch();
  }, 10000);
}

function watchSummary(d) {
  const docs = d.documents || [];
  const findings = (x) => (x.last_check || {}).findings || 0;
  return {
    docs,
    live: docs.filter((x) => x.status !== 'removed'),
    waiting: docs.filter((x) => x.status === 'pending' || x.status === 'checking'),
    baseline: docs.filter((x) => x.status === 'baseline'),
    flagged: docs.filter((x) => x.status !== 'removed' && findings(x) > 0),
    corroborated: docs.reduce((a, x) => a + (x.status !== 'removed' ? ((x.last_check || {}).corroborated || 0) : 0), 0),
    attention: docs.filter((x) => x.status === 'incomplete' || x.status === 'unreadable' || (x.status !== 'removed' && findings(x) > 0)),
  };
}

function watchUnavailable() {
  const e = W.error;
  if (!e) return '';
  if (e.status === 404 && /No endpoint/.test(e.message)) {
    return 'This server was started before the document watch was added. Restart it with ./scripts/start.sh, and the watch starts with it.';
  }
  if (e.status === 503) {
    return 'The document watch is turned off in this server (it was started with LKS_DOC_WATCH=0). Restart it without that setting to watch MongoDB around the clock.';
  }
  return `The watch’s status could not be read: ${e.message}`;
}

/* What the watch is waiting on, in words: [headline, explanation, show the raw reason]. */
function watchWaiting(w) {
  if (!w) return null;
  if (/^another job/.test(w)) {
    return ['Waiting for the model', 'A document is ready to be checked. It starts as soon as the local model finishes its current job.', false];
  }
  if (/^the local model/.test(w)) {
    return ['Waiting for the model', 'A document is ready to be checked, but the local model is not available yet.', true];
  }
  const sync = /^an index sync in progress(?: \((\d+) of (\d+) chunks present\))?/.exec(w);
  if (sync) {
    const progress = sync[1] ? ` (${sync[1]} of ${sync[2]} pieces are in place)` : '';
    return ['Waiting for the index to finish updating', `The document index is being rebuilt right now${progress}. The watch waits until it is complete, so a half-built index is never mistaken for new documents.`, false];
  }
  if (/^an index/.test(w)) {
    return ['Waiting for the index', 'The watch records what is already in MongoDB from the document index, and that index has not been built yet. It is built when the system starts.', false];
  }
  return ['Waiting', 'The watch cannot take its next step yet. The reason is below.', true];
}

function renderWatchNav() {
  const n = W.data && !W.error ? watchSummary(W.data).attention.length : 0;
  $('watchcount').textContent = n ? String(n) : '';
  $('watchcount').title = n ? `${plural(n, 'document')} need attention` : '';
}

function renderWatch() {
  renderWatchNotice();
  renderWatchStatus();
  renderWatchStats();
  renderWatchPipe();
  renderWatchNow();
  renderWatchChecks();
  renderWatchDocs();
}

function renderWatchNotice() {
  const host = $('watchnotice');
  host.textContent = '';
  const msg = watchUnavailable();
  if (msg) host.appendChild(add(el('div', 'watch-notice'), notice(msg, W.error.status === 404 || W.error.status === 503 ? 'warn' : 'bad')));
  if (W.flash) host.appendChild(add(el('div', 'watch-notice'), notice(W.flash.text, W.flash.kind)));
}

function renderWatchStatus() {
  const host = $('watchstatus');
  host.textContent = '';
  host.appendChild(el('p', 'eyebrow', 'Status'));
  const d = W.data;
  if (!d && !W.error) {
    host.appendChild(add(el('div', 'label-after'), waiting('Reading the watch’s status…')));
    return;
  }
  if (W.error) {
    add(host, add(el('h3', 'watch-state'), el('span', 'dot is-bad'), 'Not available'), el('p', 'launch-note', watchUnavailable()));
    return;
  }
  const mongoOk = d.mongo === 'connected';
  let state = 'is-ok';
  let word = 'Watching';
  let note = `Nothing is waiting. A new document is picked up within ${d.interval} seconds of arriving.`;
  if (!d.running) {
    state = 'is-bad'; word = 'Not running';
    note = 'The watch has stopped inside this server. Restart the server to start it again.';
  } else if (!mongoOk) {
    state = 'is-bad'; word = 'Cannot reach MongoDB';
    note = `The watch keeps trying every ${d.interval} seconds and carries on when MongoDB is back.`;
  } else if (jobs.watch.running) {
    state = 'is-run'; word = 'Checking a document';
    note = 'A new document is being checked now. Its progress is under Right now.';
  } else if (d.waiting_on) {
    state = 'is-run';
    [word, note] = watchWaiting(d.waiting_on);
  } else if (!d.scans) {
    state = 'is-run'; word = 'Starting';
    note = 'The first look at MongoDB is about to run.';
  }
  add(host, add(el('h3', 'watch-state'), el('span', 'dot ' + state), word), el('p', 'launch-note', note));
  host.appendChild(kv([
    ['Looks at MongoDB', `every ${d.interval} seconds`],
    ['Last look', d.last_scan ? sinceScan(d.last_scan) : 'not yet'],
    ['Looks since the server started', Number(d.scans || 0).toLocaleString('en-GB')],
    ['MongoDB', mongoOk ? 'connected' : String(d.mongo || 'unknown')],
    ['Agents per check', `${d.attackers} · ${d.model}`],
  ], 'kv-plain watch-kv'));
  const waitingOn = watchWaiting(d.waiting_on);
  if (waitingOn && waitingOn[2]) host.appendChild(add(el('div', 'label-after'), notice(`Waiting on ${d.waiting_on}`, 'warn')));
  if (d.last_error) host.appendChild(add(el('div', 'label-after'), notice(`Last problem: ${d.last_error}`, 'warn')));
}

function renderWatchStats() {
  const host = $('watchstats');
  host.textContent = '';
  const stat = (n, label, sub) => add(el('div', 'stat'), el('div', 'stat-n', String(n)), el('div', 'stat-l', label), el('div', 'stat-s', sub));
  if (!W.data || W.error || W.data.mongo !== 'connected') {
    add(host, stat('—', 'documents watched', 'shown once the watch can read MongoDB'),
      stat('—', 'waiting to be checked', ''), stat('—', 'with possible contradictions', ''));
    return;
  }
  const s = watchSummary(W.data);
  add(host,
    stat(s.live.length, s.live.length === 1 ? 'document watched' : 'documents watched',
      s.baseline.length ? `${s.baseline.length} already there when the watch started` : 'in the corpus index and the intake'),
    stat(s.waiting.length, 'waiting to be checked', s.waiting.length ? 'checked one at a time, oldest first' : 'nothing in the queue'),
    stat(s.flagged.length, s.flagged.length === 1 ? 'with a possible contradiction' : 'with possible contradictions',
      s.flagged.length ? `${plural(s.corroborated, 'finding')} raised by both agents` : 'none found so far'));
}

function watchVerdict(c) {
  if (c.complete === false) return ['Did not finish', 't-review'];
  const n = (c.findings || []).length;
  if (n) return [plural(n, 'possible contradiction'), 't-flag'];
  if (!('n_clauses' in c)) return ['No check', ''];
  return ['No contradiction found', 't-cited'];
}

function watchVerdictText(c) {
  if (c.complete === false) {
    return `The check did not finish: ${plural((c.errors || []).length, 'agent run')} gave no usable reply. An unfinished check is never reported as clean; the document is tried again.`;
  }
  if (!('n_clauses' in c)) return sentence(c.verdict || 'The document left MongoDB before it was checked.');
  const n = (c.findings || []).length;
  const both = (c.findings || []).filter((f) => f.status === 'corroborated').length;
  if (n) {
    return `${cap(plural(n, 'possible contradiction'))} with existing documents${both ? `, ${both} raised by both agents` : ''}. ${n === 1 ? 'It needs' : 'They need'} a person to decide what ${n === 1 ? 'it means' : 'they mean'}.`;
  }
  return `Compared with ${plural(c.n_docs_compared || 0, 'other document')}. No claim of a contradiction survived the quote check.`;
}

function watchCheckStates(c) {
  if (!('n_clauses' in c)) return { compare: { status: 'skipped', summary: sentence(c.verdict || 'Not checked.') } };
  return {
    compare: { status: 'pass', summary: `${plural(c.n_clauses || 0, 'clause')} scored against ${Number(c.n_clauses_compared || 0).toLocaleString('en-GB')} clauses in ${plural(c.n_docs_compared || 0, 'other document')}.` },
    attack: c.complete === false
      ? { status: 'blocked', summary: `${plural((c.errors || []).length, 'agent run')} gave no usable reply.` }
      : { status: 'pass', summary: `${plural(c.attackers || 0, 'agent')} read ${plural(c.batches || 0, 'batch', 'batches')} of clauses, blind to each other.` },
    verify: { status: 'pass', summary: `${plural((c.findings || []).length, 'claim')} kept; ${(c.discarded || []).length} discarded.` },
    decide: { status: c.complete === false ? 'blocked' : 'pass', summary: watchVerdictText(c) },
  };
}

function renderWatchPipe() {
  const host = $('watchpipe');
  const meta = $('watchpipemeta');
  meta.textContent = '';
  const t = jobs.watch;
  let states = {};
  if (t.job && t.running) {
    add(meta, 'checking now · ', clock('watch', 0));
    let batch = null;
    for (const line of t.lines) {
      const m = /batch (\d+)\/(\d+) · attacker (\d+)\/(\d+) · attempt (\d+)/.exec(line);
      if (m) batch = m;
    }
    states.scan = { status: 'pass', summary: `Found ${(t.job.params || {}).doc_id || 'a new document'}.` };
    if (batch) {
      const [, b, bs, a, as, attempt] = batch;
      states.compare = { status: 'pass', summary: 'Nearest clauses found in every other document.' };
      states.attack = { status: 'running', meta: `Batch ${b} of ${bs} · agent ${a} of ${as}` + (attempt !== '1' ? ` · attempt ${attempt}` : '') };
    } else {
      states.compare = { status: 'running', meta: 'Scoring every clause against the document base' };
    }
  } else {
    const last = W.data && !W.error ? (W.data.checks || [])[0] : null;
    if (last) {
      meta.textContent = `last check ${ago(last.started)}`;
      states = { scan: { status: 'pass', summary: `Found ${last.doc_id}.` }, ...watchCheckStates(last) };
    }
  }
  renderPipe(host, PIPES.watch, states);
}

function renderWatchNow() {
  const host = $('watchnow');
  host.textContent = '';
  const t = jobs.watch;
  if (t.job && t.running) {
    const label = String(t.job.label || '').replace(/^contradiction check:\s*/, '') || (t.job.params || {}).doc_id;
    add(host, el('p', 'eyebrow', 'Checking now'), el('h4', 'now-title', label),
      add(el('p', 'launch-meta'), icon('clock', 13), 'Running for ', clock('watch', 0)));
    const lines = t.lines.filter((l) => l.trim()).slice(-6);
    if (lines.length) host.appendChild(el('pre', 'diag watch-lines', lines.join('\n')));
    else host.appendChild(add(el('div', 'label-after'), waiting('Preparing the document and scoring its clauses…')));
    host.appendChild(el('p', 'hint label-after', 'A check takes several minutes on the local model. Other jobs wait for it, and it waits for them.'));
    return;
  }
  const c = W.data && !W.error ? (W.data.checks || [])[0] : null;
  if (c) {
    const [word, tone] = watchVerdict(c);
    add(host, el('p', 'eyebrow', 'Most recent check'), el('h4', 'now-title', c.title || c.doc_id),
      el('p', 'launch-meta', `${when(c.started)} · took ${fmtDur(c.seconds)}`),
      add(el('div', 'row label-after'), add(el('span', 'badge ' + tone), el('span', 'bdot'), word)),
      el('p', 'now-why label-after', watchVerdictText(c)),
      add(el('div', 'row follow'), btn('Read the check', 'btn-secondary btn-sm', () => openDrawer(watchCheckDrawer(c), true), 'file-text')));
    return;
  }
  host.appendChild(el('p', 'now-empty', W.error
    ? 'Nothing to show until the watch is running.'
    : 'No document has been checked yet. When a new document arrives in MongoDB, its check appears here while it runs.'));
}

function renderWatchChecks() {
  const host = $('watchchecks');
  host.textContent = '';
  const d = W.data;
  if (!d || W.error) {
    host.appendChild(emptyState('history', 'No history to show', 'Past checks are listed here once the watch is running.'));
    return;
  }
  if (d.checks_error) host.appendChild(add(el('div', 'watch-more'), notice(`Past checks could not be read: ${d.checks_error}`, 'bad')));
  const checks = d.checks || [];
  if (!checks.length) {
    if (!d.checks_error) host.appendChild(emptyState('history', 'No checks yet', 'When a new document arrives in MongoDB and has been checked, the result is listed here.'));
    return;
  }
  for (const c of checks) {
    const row = el('button', 'finding');
    row.type = 'button';
    const [word, tone] = watchVerdict(c);
    const n = (c.findings || []).length;
    add(row,
      add(el('span', 'finding-top'), add(el('span', 'badge ' + tone), el('span', 'bdot'), word),
        el('span', 'mono finding-id', c.doc_id), el('span', 'finding-when', when(c.started))),
      el('span', 'finding-head', c.title || c.doc_id),
      add(el('span', 'finding-foot'),
        'n_clauses' in c && el('span', null, `compared with ${plural(c.n_docs_compared || 0, 'document')}, ${Number(c.n_clauses_compared || 0).toLocaleString('en-GB')} clauses`),
        el('span', null, `${c.attackers ? plural(c.attackers, 'agent') + ' · ' : ''}took ${fmtDur(c.seconds)}`)),
      add(el('span', 'finding-amt'), el('span', 'finding-amt-v', String(n)), el('span', 'finding-amt-l', n === 1 ? 'finding' : 'findings')));
    row.onclick = () => openDrawer(watchCheckDrawer(c), true);
    host.appendChild(row);
  }
  if (checks.length >= W.limit) {
    host.appendChild(add(el('div', 'watch-more'), btn('Show older checks', 'btn-ghost btn-sm', () => { W.limit += 20; loadWatch(); }, 'chevron-down')));
  }
}

function watchMatches(x, f) {
  if (!f) return true;
  if (f === 'attention') return x.status === 'incomplete' || x.status === 'unreadable' || (x.status !== 'removed' && ((x.last_check || {}).findings || 0) > 0);
  if (f === 'waiting') return x.status === 'pending' || x.status === 'checking';
  return x.status === f;
}

function renderWatchDocs() {
  const sel = $('watchfilter');
  if (!sel.options.length) {
    for (const [v, w] of WATCH_FILTERS) {
      const o = el('option', null, w);
      o.value = v;
      sel.appendChild(o);
    }
    sel.onchange = () => { W.filter = sel.value; renderWatchDocs(); };
  }
  sel.value = W.filter;
  const host = $('watchdocs');
  host.textContent = '';
  const d = W.data;
  if (!d || W.error) {
    host.appendChild(emptyState('database', 'Nothing to show', 'Tracked documents are listed here once the watch is running.'));
    return;
  }
  if (d.mongo !== 'connected') {
    host.appendChild(emptyState('database', 'MongoDB is not reachable', 'The list of tracked documents is kept in MongoDB. It comes back when the connection does.'));
    return;
  }
  const all = d.documents || [];
  if (!all.length) {
    host.appendChild(emptyState('database', 'No documents tracked yet', /^an index/.test(d.waiting_on || '')
      ? 'The documents already in MongoDB are recorded as the baseline once the document index has been built.'
      : d.scans
        ? 'MongoDB holds no documents for the watch to track.'
        : 'The first look records the documents already in MongoDB as the baseline. It runs a few seconds after the server starts.'));
    return;
  }
  const docs = all.filter((x) => watchMatches(x, W.filter));
  if (!docs.length) {
    host.appendChild(emptyState('database', 'Nothing of this kind', 'Choose another filter above.'));
    return;
  }
  for (const x of docs) host.appendChild(watchDocRow(x));
}

function watchDocRow(x) {
  const lc = x.last_check;
  const [word, tone] = x.status === 'checked' && lc && lc.findings > 0
    ? ['Possible contradiction', 't-flag']
    : (WATCH_STATUS[x.status] || [cap(x.status || 'unknown'), '']);
  const main = el('div', 'wdoc-main');
  add(main,
    add(el('div', 'finding-top'), add(el('span', 'badge ' + tone), el('span', 'bdot'), word), el('span', 'mono finding-id', x.doc_id)),
    el('p', 'wdoc-title', x.title || x.doc_id),
    el('p', 'wdoc-meta', [
      x.source === 'intake' ? 'added through the intake' : 'in the corpus index',
      x.n_clauses !== undefined && plural(x.n_clauses, 'clause'),
      x.changed_at && `${x.status === 'removed' ? 'removed' : 'last changed'} ${ago(isoEpoch(x.changed_at))}`,
    ].filter(Boolean).join(' · ')));
  if (x.status === 'baseline') main.appendChild(el('p', 'wdoc-note', 'Already in MongoDB when the watch started, so it has not been checked.'));
  if (x.status === 'removed') main.appendChild(el('p', 'wdoc-note', 'No longer in MongoDB.'));
  if (lc && x.status !== 'baseline') {
    const text = lc.complete === false ? 'The last check did not finish.'
      : lc.findings ? `The last check found ${plural(lc.findings, 'possible contradiction')}${lc.corroborated ? `, ${lc.corroborated} raised by both agents` : ''}.`
        : 'The last check found no contradiction.';
    main.appendChild(el('p', 'wdoc-note', text));
  }
  if (x.error && ['incomplete', 'unreadable', 'pending'].includes(x.status)) {
    main.appendChild(el('p', 'wdoc-error', x.status === 'unreadable' ? `Why it could not be read: ${x.error}` : `Last problem: ${x.error}`));
  }
  const side = el('div', 'wdoc-side');
  if (x.attempts) side.appendChild(el('span', 'wdoc-attempts', plural(x.attempts, 'attempt')));
  const c = x.check_id && W.data ? (W.data.checks || []).find((k) => k.id === x.check_id) : null;
  if (c) side.appendChild(btn('Read the check', 'btn-ghost btn-sm', () => openDrawer(watchCheckDrawer(c), true), 'file-text'));
  if (['checked', 'incomplete', 'baseline'].includes(x.status)) {
    const again = btn(x.status === 'baseline' ? 'Check now' : 'Check again', 'btn-secondary btn-sm', null, 'refresh-cw');
    again.onclick = () => recheckDocument(x, again);
    side.appendChild(again);
  }
  return add(el('div', 'wdoc'), main, side);
}

async function recheckDocument(x, button) {
  button.disabled = true;
  try {
    await api('/api/watch/recheck', { key: x.key });
    W.flash = { text: `${x.title || x.doc_id} is queued. It is checked when the local model is free.`, kind: '' };
    await loadWatch();
  } catch (e) {
    W.flash = { text: e.message, kind: 'bad' };
    button.disabled = false;
    renderWatchNotice();
  }
}

function watchFinding(f, c) {
  const both = f.status === 'corroborated';
  const existing = el('div', 'wpair-label');
  add(existing, el('span', 'label', 'Existing'), citeButton(f.corpus_ref));
  return add(el('article', 'wpair'),
    add(el('div', 'wpair-head' + (both ? ' is-both' : '')), icon(both ? 'users' : 'bot', 14),
      el('span', null, both ? 'Raised by both agents' : 'Raised by one agent')),
    add(el('div', 'wpair-grid'),
      add(el('div', 'wpair-side'), add(el('div', 'wpair-label'), el('span', 'label', 'New'), el('span', 'mono', `${c.doc_id} ${f.clause_id}`)),
        add(el('div', 'law'), el('p', null, `“${f.quote}”`)), (f.doc_title || c.title) && el('p', 'wpair-src', f.doc_title || c.title)),
      add(el('div', 'wpair-side'), existing,
        add(el('div', 'law'), el('p', null, `“${f.corpus_quote}”`)), f.corpus_title && el('p', 'wpair-src', f.corpus_title))),
    add(el('div', 'wpair-foot'), kv([['A situation both apply to', f.facts], ['What conflicts', f.summary]], 'kv-plain')));
}

function watchCheckDrawer(c) {
  return (body, setHead) => {
    const [word] = watchVerdict(c);
    setHead(`${c.doc_id} · checked ${when(c.started)}`, c.title || c.doc_id);
    const tone = c.complete === false ? 'is-pending' : (c.findings || []).length ? 'is-not' : 'is-issued';
    body.appendChild(add(el('div', 'verdict ' + tone), el('p', 'verdict-w', word),
      add(el('div', 'verdict-body'), el('p', 'verdict-reason', watchVerdictText(c)))));
    const proof = el('ol', 'pipe');
    renderPipe(proof, PIPES.watch.filter((d) => d.key !== 'scan'), watchCheckStates(c));
    body.appendChild(section('How this check ran', proof));
    if ((c.errors || []).length) body.appendChild(section('What did not finish', notice(c.errors.join('\n'), 'warn')));
    const fs = c.findings || [];
    if (fs.length) {
      body.appendChild(section(`Possible contradictions, ${fs.length}`,
        el('p', 'hint', 'Each pair was checked: both quotes appear word for word in the clauses named. Whether they really conflict is for a person to decide.'),
        ...fs.map((f) => watchFinding(f, c))));
    }
    if ((c.discarded || []).length) {
      const det = el('details', 'transcript');
      det.appendChild(add(el('summary'), icon('chevron-down', 14), `Discarded claims, ${c.discarded.length}`));
      for (const f of c.discarded) {
        det.appendChild(add(el('div', 'rf'),
          el('div', 'rf-top mono', `${f.clause_id} vs ${f.corpus_ref}`),
          f.summary && el('p', 'rf-sum', f.summary),
          el('p', 'rf-meta', `Discarded: ${f.discard_reason}`)));
      }
      body.appendChild(add(el('div', 'd-sec'),
        el('p', 'hint', 'Claims that failed the quote check, usually because a quote was not in the clause it named. They are kept so you can see what was thrown away.'), det));
    }
    body.appendChild(section('Details', kv([
      ['Document key', c.key], ['Clauses in the document', c.n_clauses !== undefined ? String(c.n_clauses) : ''],
      ['Agents', c.attackers ? `${c.attackers}, ${plural(c.batches || 0, 'batch', 'batches')}` : ''],
      ['Model', c.model], ['Took', fmtDur(c.seconds)], ['Check id', c.id],
    ], 'kv-plain')));
    if ((c.attacks_tried || []).length) {
      const det = el('details', 'transcript');
      add(det, add(el('summary'), icon('chevron-down', 14), 'What the agents tried'),
        add(el('ul', 'hint'), c.attacks_tried.map((a) => el('li', null, a))));
      body.appendChild(add(el('div', 'd-sec'), det));
    }
  };
}

/* ================================================================== tour

   A spotlight over the live interface. Each step names a real control; if that
   control is not on screen at this width (the steps panel is hidden on narrow
   screens), the card stands on its own in the middle instead. The examples
   inside the card are animations of what a tool does, played once and marked
   as examples: they are not a job and do not touch the server. */

const TOUR = [
  { key: 'intro', eyebrow: 'Welcome to Ross', title: 'Rules are executed, not read', ast: true,
    body: 'Ross turns the rules in your company’s documents into something a computer can run, so an answer is worked out from the rule itself instead of guessed. This one-minute tour shows the three tools and how each one checks its own work.',
    footnote: 'Every answer names where it came from, and a person still decides.', next: 'Start the tour' },
  { key: 'tools', view: 'ask', target: '#toolsnav', side: 'right', eyebrow: 'The tools',
    title: 'Three tools, one set of documents',
    body: 'Ask questions, check for risks, and draft new documents. Each tool is a chain of steps, and every step is done by one of these:', demo: 'roles' },
  { key: 'ask', view: 'ask', target: '#askform', side: 'top', eyebrow: 'Ask',
    title: 'Ask in plain English',
    body: 'A question about a figure is answered by running the rule; a question about wording is answered by quoting the clause. If a rule needs facts about your case, Ross asks for them.', demo: 'ask' },
  { key: 'askpanel', view: 'ask', target: '#askpanel', side: 'left', eyebrow: 'Ask',
    title: 'Every answer shows how it was reached',
    body: 'This panel follows your last question through each step. Every part of an answer carries one of these labels:', demo: 'labels' },
  { key: 'check', view: 'check', target: '#checklaunch', side: 'bottom', eyebrow: 'Risk check',
    title: 'Let agents look for claims against you',
    body: 'Five AI agents each play an opponent: an employee’s lawyer, a customer’s counsel, a regulator, an auditor and a departing contractor. The rule engine runs every claim they propose.', demo: 'attack' },
  { key: 'checkpipe', view: 'check', target: '#checkpipecard', side: 'right', eyebrow: 'Risk check',
    title: 'Only claims that hold up are kept',
    body: 'A check runs these steps in order, and you can watch each one live. A claim becomes a finding only if it lands when the rule is run, so an empty list is good news.', demo: 'check' },
  { key: 'draft', view: 'draft', target: '#draftform', side: 'bottom', eyebrow: 'Draft',
    title: 'Describe a document, get a checked draft',
    body: 'Agents draft it from your closest documents. It must pass the rule checks and an independent review, and a failed check sends it back to be fixed.', demo: 'draft' },
  { key: 'watch', view: 'watch', target: '#watchstatus', side: 'bottom', eyebrow: 'Document watch',
    title: 'New documents are checked around the clock',
    body: 'Whenever a document arrives in MongoDB, two AI agents read it against everything you already have. A contradiction is kept only if its quotes appear word for word, and a person reviews it.', demo: 'watch' },
  { key: 'docs', view: 'draft', target: '#sidedocs', side: 'right', eyebrow: 'Your documents',
    title: 'Every clause, one click away',
    body: 'Select a document to read its clauses and see which ones Ross can run. A clause reference anywhere in an answer or a finding opens the same way.' },
  { key: 'status', view: 'ask', target: '#status', side: 'bottom', eyebrow: 'Always in view',
    title: 'What the local model is doing',
    body: 'This shows whether the model on this machine is ready or busy, and takes you to whatever is running. A long check or draft carries on if you leave the page.' },
  { key: 'done', view: 'ask', eyebrow: 'You’re ready', title: 'Where would you like to start?',
    body: 'You can take this tour again at any time from Take the tour.', finish: true },
];

const T = { i: -1, timers: [], target: null, lastFocus: null, ro: null, raf: 0 };

function isShown(node) {
  if (!node || node.closest('[hidden]')) return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function startTour(at = 0) {
  closeDrawer();
  T.lastFocus = document.activeElement;
  const root = $('tour');
  const firstShow = root.hidden;
  root.hidden = false;
  if (firstShow) $('tourcard').classList.add('is-still');
  goTour(at);
}

function endTour() {
  clearDemo();
  if (T.ro) T.ro.disconnect();
  $('tour').hidden = true;
  T.i = -1;
  T.target = null;
  closeSide();
  store.set('ross.toured', '1');
  if (T.lastFocus && document.contains(T.lastFocus) && T.lastFocus !== document.body) T.lastFocus.focus();
}

function goTour(i) {
  if (i < 0 || i >= TOUR.length) return;
  clearDemo();
  T.i = i;
  const st = TOUR[i];
  if (st.view && S.view !== st.view) showView(st.view);
  const target = st.target ? document.querySelector(st.target) : null;
  const inSide = !!(target && target.closest('.side'));
  if (narrow() && inSide) openSide(); else closeSide();
  T.target = target;
  if (target && !inSide && isShown(target)) {
    if (narrow()) {
      target.scrollIntoView({ block: 'start', inline: 'nearest' });
      if (!target.closest('.ask')) window.scrollBy(0, -(56 + 12));
    } else {
      target.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
  }
  renderTourCard(st);
  if (T.ro) T.ro.disconnect();
  if (window.ResizeObserver) {
    T.ro = new ResizeObserver(() => placeTourSoon());
    T.ro.observe($('tourcard'));
    if (target) T.ro.observe(target);
  }
  placeTour();
  /* The sidebar slides in on narrow screens; place again once it has. */
  T.timers.push(setTimeout(placeTour, 260));
  T.timers.push(setTimeout(() => $('tourcard').classList.remove('is-still'), 30));
  const next = $('tourcard').querySelector('[data-tour-next]');
  if (next) next.focus({ preventScroll: true });
}

function renderTourCard(st) {
  const card = $('tourcard');
  card.textContent = '';
  card.classList.remove('is-in');
  void card.offsetWidth;
  card.classList.add('is-in');
  const n = T.i;
  const inner = el('div', 'tour-inner');
  const close = el('button', 'icon-btn');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close the tour');
  close.appendChild(icon('x', 16));
  close.onclick = endTour;
  const progress = el('div', 'tour-progress');
  progress.setAttribute('aria-hidden', 'true');
  for (let k = 0; k < TOUR.length; k++) progress.appendChild(el('span', k < n ? 'is-past' : k === n ? 'is-now' : ''));
  add(inner, add(el('div', 'tour-top'), el('p', 'eyebrow', `${st.eyebrow} · ${n + 1} of ${TOUR.length}`), close), progress);
  const title = el('h2', 'tour-title');
  title.id = 'tourtitle';
  if (st.ast) add(title, 'Rules are executed, ', el('em', null, 'not read'), el('span', 'ast', '*'));
  else title.textContent = st.title;
  inner.appendChild(title);
  inner.appendChild(el('p', 'tour-body', st.body));
  if (st.footnote) inner.appendChild(add(el('p', 'tour-body'), el('span', 'ast', '* '), st.footnote));
  if (st.demo) {
    const demo = el('div', 'tour-demo');
    const stage = el('div');
    const replay = el('button', 'btn btn-ghost btn-sm');
    replay.type = 'button';
    add(replay, icon('rotate-ccw', 12), 'Play again');
    replay.onclick = () => { clearDemo(); playDemo(st.demo, stage); };
    if (narrow()) {
      const open = btn('Play the example', 'btn-secondary btn-sm', () => {
        open.remove();
        add(demo, add(el('div', 'tour-demo-head'), el('span', 'label', 'Example'), replay), stage);
        playDemo(st.demo, stage);
        placeTour();
      }, 'play');
      demo.appendChild(open);
    } else {
      add(demo, add(el('div', 'tour-demo-head'), el('span', 'label', 'Example'), replay), stage);
      playDemo(st.demo, stage);
    }
    inner.appendChild(demo);
  }
  if (st.finish) inner.appendChild(tourFinish());
  const nav = el('div', 'tour-nav');
  const back = btn('Back', 'btn-ghost btn-sm', () => goTour(n - 1));
  const skip = btn('Skip the tour', 'btn-ghost btn-sm', endTour);
  const next = btn(st.finish ? 'Finish' : st.next || 'Next', 'btn-primary btn-sm', () => (st.finish ? endTour() : goTour(n + 1)));
  next.dataset.tourNext = '1';
  add(nav, n > 0 ? back : skip, el('span', 'tour-gap'), el('span', 'tour-keys', '← → to move · Esc to close'), next);
  inner.appendChild(nav);
  card.appendChild(inner);
}

function tourFinish() {
  const box = el('div', 'tour-finish');
  const go = (label, view, iconName) => btn(label, 'btn-secondary', () => {
    endTour();
    showView(view);
    if (view === 'ask') $('askfield').focus();
  }, iconName);
  add(box, go('Ask a question', 'ask', 'message-square-text'), go('Run a risk check', 'check', 'shield-check'), go('Draft a document', 'draft', 'file-pen-line'));
  const gloss = el('details', 'tour-gloss');
  add(gloss, add(el('summary'), icon('chevron-down', 14), 'Words you’ll see'), kv([
    ['Clause', 'A numbered paragraph of a document, like MSA-SCH4 L-4.3.'],
    ['Rule', 'One or more clauses Ross has turned into something it can run.'],
    ['Fact', 'A detail about your situation that a rule needs.'],
    ['Judgement', 'A question the documents leave open. Only a person answers it.'],
    ['Finding', 'A claim against the company that holds up when the rule is run.'],
    ['Issued', 'A drafted document that passed every check.'],
  ], 'kv-plain'));
  box.appendChild(gloss);
  return box;
}

/* Placement is synchronous: reading the card's size forces layout, so the
   numbers are current. Scroll and resize only ask for it once per frame. */
function placeTourSoon() {
  if (T.i < 0) return;
  cancelAnimationFrame(T.raf);
  T.raf = requestAnimationFrame(placeTour);
}

function placeTour() {
  if (T.i < 0) return;
  {
    const st = TOUR[T.i];
    const card = $('tourcard');
    const spot = $('tourspot');
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const m = 12;
    let r = null;
    if (T.target && isShown(T.target)) {
      const b = T.target.getBoundingClientRect();
      const pad = 6;
      r = { left: Math.max(4, b.left - pad), top: Math.max(4, b.top - pad), right: Math.min(vw - 4, b.right + pad), bottom: Math.min(vh - 4, b.bottom + pad) };
      if (r.right - r.left < 8 || r.bottom - r.top < 8) r = null;
    }
    if (r) {
      Object.assign(spot.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.right - r.left}px`, height: `${r.bottom - r.top}px` });
      spot.classList.remove('is-empty');
    } else {
      Object.assign(spot.style, { left: `${vw / 2}px`, top: `${vh / 2}px`, width: '0px', height: '0px' });
      spot.classList.add('is-empty');
    }
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, Math.max(lo, hi)));
    let x;
    let y;
    if (!r) {
      x = (vw - cw) / 2;
      y = (vh - ch) / 2;
    } else if (narrow()) {
      /* A phone has room for the card above or below the control, not beside
         it. The card goes wherever it leaves the start of the control in view:
         a tall control is read from its top. */
      x = m;
      const head = Math.min(r.bottom - r.top, 140);
      const bottomY = vh - ch - m;
      if (r.top + head <= bottomY) y = bottomY;
      else if (r.top >= m + ch + 8) y = m;
      else y = bottomY;
    } else {
      const gap = 14;
      const pos = {
        right: [r.right + gap, (r.top + r.bottom - ch) / 2],
        left: [r.left - gap - cw, (r.top + r.bottom - ch) / 2],
        bottom: [(r.left + r.right - cw) / 2, r.bottom + gap],
        top: [(r.left + r.right - cw) / 2, r.top - gap - ch],
      };
      /* The preferred side if the card fits there without covering the
         control; otherwise whichever side covers the least of it. */
      const covered = (px, py) => {
        const w = Math.min(px + cw, r.right) - Math.max(px, r.left);
        const h = Math.min(py + ch, r.bottom) - Math.max(py, r.top);
        return w > 0 && h > 0 ? w * h : 0;
      };
      let best = null;
      for (const side of [st.side, 'right', 'bottom', 'left', 'top']) {
        if (!side) continue;
        const px = clamp(pos[side][0], m, vw - cw - m);
        const py = clamp(pos[side][1], m, vh - ch - m);
        const area = covered(px, py);
        if (!best || area < best.area) best = { px, py, area };
        if (area === 0) break;
      }
      x = best.px;
      y = best.py;
    }
    card.style.left = `${Math.round(clamp(x, m, vw - cw - m))}px`;
    card.style.top = `${Math.round(clamp(y, m, vh - ch - m))}px`;
  }
}

function clearDemo() {
  for (const id of T.timers) clearTimeout(id);
  T.timers = [];
}

function playDemo(key, stage) {
  stage.textContent = '';
  const steps = DEMOS[key](stage) || [];
  if (reducedMotion()) {
    for (const [, fn] of steps) fn();
    return;
  }
  let at = 0;
  for (const [ms, fn] of steps) {
    at += ms;
    T.timers.push(setTimeout(fn, at));
  }
}

const reveal = (node) => () => node.classList.add('is-in');

function miniSteps(defs) {
  const ol = el('ol', 'mini mini-compact');
  for (const [key, title, role] of defs) {
    const li = el('li', 'mini-step s-waiting');
    li.dataset.key = key;
    const mark = el('span', 'step-mark');
    mark.appendChild(icon('circle-dashed', 12));
    add(li, mark, add(el('div'), el('span', 'mini-title', title), role && roleTag(role), el('span', 'mini-verdict')));
    ol.appendChild(li);
  }
  return ol;
}

const DEMOS = {
  roles(stage) {
    const rows = ['agent', 'engine', 'search', 'person'].map((k) => add(el('div', 'tour-row demo-in'), roleTag(k), el('span', null, ROLES[k].body)));
    add(stage, ...rows);
    return rows.map((row, i) => [i ? 320 : 150, reveal(row)]);
  },
  ask(stage) {
    const ol = el('ol', 'pipe pipe-compact');
    renderPipe(ol, PIPES.ask, Object.fromEntries(PIPES.ask.map((d) => [d.key, { status: 'waiting' }])));
    const result = add(el('div', 'tour-result demo-in'),
      add(el('p', 'tour-result-v'), el('span', 'tour-num', '2,000'), 'credit amount, from running the rule'),
      el('span', 'engine e-catala', 'Computed · rule engine'), el('span', 'engine e-vector', 'Quoted · from the documents'));
    add(stage, el('p', 'tour-q', '“How much service credit do we owe at 98.5% uptime, on a monthly charge of 10,000?”'), ol, result);
    const seq = [];
    for (const [k, sum] of [
      ['understand', 'Matched 1 rule and found 1 passage.'], ['facts', '4 facts supplied.'],
      ['execute', 'Ran ServiceCredits.ServiceCredit.'], ['quote', '1 passage quoted word for word.'],
      ['label', 'Computed and quoted parts, kept apart.']]) {
      seq.push([380, () => setStep(ol, k, 'running')], [520, () => setStep(ol, k, 'pass', sum)]);
    }
    seq.push([260, reveal(result)]);
    return seq;
  },
  labels(stage) {
    const rows = [
      ['e-catala', 'Computed · rule engine', 'A figure produced by running the rule. Exact, and the same every time.'],
      ['e-vector', 'Quoted · from the documents', 'The documents’ own words, with the clause they came from.'],
      ['e-none', 'Rule found · needs facts', 'Ross asks for what it needs to know. It never guesses.'],
    ].map(([cls, label, text]) => add(el('div', 'tour-row demo-in'), el('span', 'engine ' + cls, label), el('span', null, text)));
    add(stage, ...rows);
    return rows.map((row, i) => [i ? 420 : 150, reveal(row)]);
  },
  attack(stage) {
    const mini = miniSteps([['propose', 'The agent proposes a claim', 'agent'], ['run', 'The rule engine runs it', 'engine'], ['verdict', 'Verdict', null]]);
    const claim = el('blockquote', 'claim demo-in', '“Availability was 94.5% this month. The credit should not be capped at 30% of the charge.”');
    const strip = el('div', 'strip');
    const atks = [0, 1, 2].map(() => strip.appendChild(el('span', 'atk')));
    add(stage, add(el('p', 'tour-q'), 'A customer’s counsel tests ', el('span', 'mono', 'ServiceCredits.ServiceCredit')), mini, claim,
      add(el('div', 'tour-strip'), strip, el('span', 'strip-count', 'three attacks: no exposure, a new risk, no exposure')));
    return [
      [350, () => setStep(mini, 'propose', 'running')], [500, reveal(claim)], [700, () => setStep(mini, 'propose', 'pass')],
      [300, () => setStep(mini, 'run', 'running')], [900, () => setStep(mini, 'run', 'pass')],
      [300, () => { setStep(mini, 'verdict', 'pass', 'No exposure: the rule held.'); atks[0].className = 'atk p-died'; }],
      [650, () => { atks[1].className = 'atk p-landed'; }], [450, () => { atks[2].className = 'atk p-died'; }],
    ];
  },
  check(stage) {
    const ol = el('ol', 'pipe pipe-compact');
    renderPipe(ol, PIPES.check, Object.fromEntries(PIPES.check.map((d) => [d.key, { status: 'waiting' }])));
    stage.appendChild(ol);
    const seq = [];
    for (const [k, sum, ms] of [
      ['operations', '14 past decisions run; 8 differ from the policy.', 500], ['rivalries', '1 pair of rules run; they agree.', 500],
      ['reverify', '9 of 9 findings still reproduce.', 500], ['fleet', '3 attacks; 1 new risk found.', 1200]]) {
      seq.push([320, () => setStep(ol, k, 'running')], [ms, () => setStep(ol, k, 'pass', sum)]);
    }
    return seq;
  },
  watch(stage) {
    const ol = el('ol', 'pipe pipe-compact');
    renderPipe(ol, PIPES.watch, Object.fromEntries(PIPES.watch.map((d) => [d.key, { status: 'waiting' }])));
    stage.appendChild(ol);
    const step = (k, st, sum) => () => setStep(ol, k, st, sum);
    return [
      [300, step('scan', 'running')], [500, step('scan', 'pass', 'Found TRAVEL-2026, a new document with 18 clauses.')],
      [300, step('compare', 'running')], [700, step('compare', 'pass', 'Scored against 312 clauses in 6 documents.')],
      [300, step('attack', 'running')], [1300, step('attack', 'pass', '2 agents read 4 batches of clauses.')],
      [300, step('verify', 'running')], [600, step('verify', 'pass', '3 claims kept, 2 discarded: quotes not found.')],
      [300, step('decide', 'pass', '1 possible contradiction, raised by both agents, for a person to review.')],
    ];
  },
  draft(stage) {
    const ol = el('ol', 'pipe pipe-compact');
    renderPipe(ol, PIPES.draft, Object.fromEntries(PIPES.draft.map((d) => [d.key, { status: 'waiting' }])));
    stage.appendChild(ol);
    const step = (k, s, sum, loop) => () => setStep(ol, k, s, sum, loop);
    return [
      [300, step('retrieve', 'running')], [450, step('retrieve', 'pass', '11 clauses from 3 documents.')],
      [300, step('draft', 'running')], [650, step('draft', 'pass', 'A 12-clause policy.')],
      [300, step('catala', 'running')], [750, step('catala', 'blocked', 'G1: the encoding does not compile.')],
      [700, step('draft', 'running', 'Sent back with the compiler’s own words.', 'Rewritten once after a failed check')],
      [700, step('draft', 'pass', 'Redrafted.')],
      [300, step('catala', 'running', 'Second attempt.')], [700, step('catala', 'pass', 'G1 to G4 passed.')],
      [300, step('screen', 'running')], [700, step('screen', 'pass', 'No confirmed findings.')],
      [300, step('roundtrip', 'running')], [700, step('roundtrip', 'pass', 'Both encodings behave the same.')],
      [300, step('issue', 'pass', 'Issued: document.pdf.')],
    ];
  },
};

/* ================================================================== boot */

function hydrateIcons(root) {
  for (const n of root.querySelectorAll('[data-icon]')) {
    if (n.dataset.iconDone) continue;
    n.dataset.iconDone = '1';
    n.prepend(icon(n.dataset.icon, Number(n.dataset.size) || 16));
  }
}

(async function boot() {
  hydrateIcons(document);
  $('menubtn').appendChild(icon('menu', 18));
  $('drawerclose').appendChild(icon('x', 18));
  $('drawerback').appendChild(icon('arrow-left', 18));

  document.addEventListener('click', (e) => {
    if (e.target.closest('#tour')) return;
    const tour = e.target.closest('[data-tour]');
    if (tour) {
      e.preventDefault();
      closeSide();
      startTour();
      return;
    }
    const v = e.target.closest('[data-view]');
    if (v) {
      e.preventDefault();
      showView(v.dataset.view);
      return;
    }
    const tipClose = e.target.closest('[data-tip-close]');
    if (tipClose) {
      const tip = tipClose.closest('.tip');
      tip.hidden = true;
      store.set('ross.tip.' + tip.dataset.tip, '1');
    }
  });
  for (const tip of document.querySelectorAll('.tip')) tip.hidden = !!store.get('ross.tip.' + tip.dataset.tip);

  $('menubtn').onclick = () => ($('side').classList.contains('is-open') ? closeSide() : openSide());
  $('drawerclose').onclick = closeDrawer;
  $('scrim').onclick = closeDrawer;
  $('drawerback').onclick = () => { drawer.stack.pop(); paintDrawer(); };
  document.addEventListener('keydown', (e) => {
    if (T.i >= 0) {
      if (e.key === 'Escape') { e.preventDefault(); endTour(); return; }
      const inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''));
      if (e.key === 'ArrowRight' && !inField) { e.preventDefault(); if (TOUR[T.i].finish) endTour(); else goTour(T.i + 1); return; }
      if (e.key === 'ArrowLeft' && !inField) { e.preventDefault(); goTour(T.i - 1); return; }
      if (e.key === 'Tab') {
        const focusable = [...$('tourcard').querySelectorAll('button, [href], summary, input, select, textarea')].filter((n) => !n.disabled && isShown(n));
        if (focusable.length) {
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (!$('tourcard').contains(document.activeElement)) { e.preventDefault(); first.focus(); }
          else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
      return;
    }
    if (e.key === 'Escape') { closeDrawer(); closeSide(); }
  });
  window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
  window.addEventListener('resize', placeTourSoon);
  document.addEventListener('scroll', placeTourSoon, true);

  $('askform').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = $('askfield').value;
    if (!q.trim() || $('askbtn').disabled) return;
    $('askfield').value = '';
    autosize();
    ask(q);
  });
  $('askfield').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $('askform').requestSubmit();
    }
  });
  $('askfield').addEventListener('input', autosize);
  $('draftform').addEventListener('submit', startDraft);

  renderThread();
  renderSideFoot();
  const hash = location.hash.slice(1);
  const asked = new URLSearchParams(location.search).get('q');
  showView(hash || 'ask');
  /* A first visit starts the tour over the real interface. */
  if (!TOUR_HASHES.has(hash) && !asked && !store.get('ross.toured')) startTour();

  const loadedState = api('/api/state').then((s) => {
    S.state = s;
    renderDocs();
    renderSideFoot();
    $('topsub').textContent = VIEWS[S.view].sub();
    placeTour();
  }).catch((e) => {
    $('doclist').textContent = '';
    $('doclist').appendChild(el('li', 'side-empty', 'Documents unavailable: ' + e.message));
  });

  await Promise.allSettled([loadAgent(), refreshScopes(), refreshQueue(), loadWatch()]);
  renderSideFoot();

  /* Pick up whatever the server is doing, or the last thing of each kind it
     did, so no tool is blank after a reload during a run. */
  try {
    for (const j of (await api('/api/exposure/jobs')).jobs) {
      const name = trackerFor(j.kind);
      if (name && !jobs[name].job) await jobs[name].attach(j.id);
    }
  } catch (e) { /* the loop reports an unreachable server */ }
  if (S.view === 'check') renderCheck();
  if (S.view === 'draft') { renderDraft(); loadRuns(); }
  renderStatus();
  loop();
  setInterval(tick, 1000);
  setInterval(async () => {
    if (S.view !== 'watch') loadWatch();
    if (S.live) return;
    await loadAgent();
    renderStatus();
    renderSideFoot();
  }, 30000);

  await loadedState;
  /* A question in the address bar is asked on arrival, so an answer can be sent
     to a colleague as a link. It is re-run on arrival, from today's documents. */
  if (asked) ask(asked);
})();

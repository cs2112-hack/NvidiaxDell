/* Ross — interface.

   No framework, on purpose: this is a local tool served by a standard-library
   HTTP server, and a build step would be one more thing to keep working.

   Two rules run through the whole file.

   Nothing here decides a legal question. Every figure comes from executing a
   rule, every quotation comes from the corpus verbatim, and the provision that
   governed comes from the interpreter's own trace.

   Nothing here pretends to progress. Every stage, attack and verdict drawn on
   the exposure screen is an event the server emitted when it happened. Where
   the interface is waiting and cannot know how far along something is — a
   model drafting, a rule executing — it shows a clock, never a bar. */

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function add(parent, ...kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    parent.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  }
  return parent;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

/* Rule names are long single words. A zero-width space after each dot lets
   them wrap at "Module." rather than mid-word. */
const breakable = (s) => String(s).replace(/\./g, '.​');

function fmtDur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function ago(epoch) {
  const s = Date.now() / 1000 - epoch;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
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

/* Render clause text the way the document means it to read.

   Corpus files are hard-wrapped at about 78 columns, so rendering them with
   `white-space: pre-wrap` breaks sentences mid-clause. But an indented
   sub-paragraph list is structure, not wrapping, and flattening it would
   destroy the enumeration a lawyer cites by letter. So blocks separated by a
   blank line become paragraphs, an indented block keeps its line breaks, and
   an unindented block is reflowed. */
function renderLaw(text, host) {
  for (const block of String(text || '').split(/\n\s*\n/)) {
    if (!block.trim()) continue;
    const lines = block.split('\n');
    const indented = lines.some((l) => /^\s{2,}\S/.test(l));
    const p = el('p', indented ? 'law-p indented' : 'law-p');
    p.textContent = indented
      ? lines.map((l) => l.replace(/\s+$/, '')).join('\n')
      : lines.map((l) => l.trim()).join(' ');
    host.appendChild(p);
  }
  return host;
}

function lawBlock(text, cls) {
  return renderLaw(text, el('div', 'law' + (cls ? ' ' + cls : '')));
}

function citeButton(ref, label) {
  const b = el('button', 'cite', label || ref);
  b.type = 'button';
  b.onclick = () => openClause(ref.replace(/\s*\(.*$/, ''));
  return b;
}

function cites(refs) {
  const box = el('div', 'cites');
  for (const r of refs || []) box.appendChild(citeButton(r));
  return box;
}

function kv(rows, mono) {
  const dl = el('dl', 'kv');
  for (const [k, v, isMono] of rows) {
    if (v === undefined || v === null || v === '') continue;
    dl.appendChild(el('dt', null, k));
    dl.appendChild(el('dd', (isMono ?? mono) ? 'mono' : null, v));
  }
  return dl;
}

function notice(text, kind) {
  return el('p', 'notice' + (kind ? ' is-' + kind : ''), text);
}

function waiting(text) {
  const p = el('p', 'waiting');
  add(p, el('span', 'waiting-dot'), text);
  return p;
}

const state = { docs: [], meta: null, scopes: [], scope: null, open: new Set() };

/* ================================================================ shell */

const VIEWS = ['exposure', 'generate', 'ask', 'rules', 'documents', 'intake', 'checks'];

function showView(name) {
  if (!VIEWS.includes(name)) name = 'exposure';
  for (const b of $('nav').querySelectorAll('button')) {
    if (b.dataset.view === name) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  }
  for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== 'view-' + name;
  /* Views are addressable, so a colleague can be sent straight to one. */
  if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name);
  if (name === 'rules' && !state.scopes.length) loadScopes();
  if (name === 'intake') loadIntake();
  if (name === 'checks') loadChecks();
  if (name === 'documents') renderDocuments();
  if (name === 'generate') buildGenerate();
}

/* ========================================================= agent runtime

   One job runs at a time on the server, because the local model serves one
   request at a time. The interface watches it from any view: the pulse in the
   bar polls for a running job, attaches to it, and pulls its events. */

const ARCH = {
  employment: { name: "Claimant's employment lawyer", lead: "The claimant's employment lawyer" },
  customer: { name: "Customer's counsel", lead: "The customer's counsel" },
  regulator: { name: 'Regulator', lead: 'The regulator' },
  auditor: { name: 'Auditor', lead: 'The auditor' },
  contractor: { name: 'Departing contractor', lead: 'The departing contractor' },
};
const archName = (k) => (ARCH[k] ? ARCH[k].name : k);

const KLASS = {
  DIVERGENCE: 'What we did differs from what we wrote',
  CONFLICT: 'Two provisions apply and neither takes priority',
  SILENCE: 'The documents say nothing about this case',
  REFUSAL: 'The policy refuses to answer',
  CONTRADICTION: 'Two rules give two answers',
  ADVERSE: 'A reading of our documents that costs us',
};

const SWEEP = [
  ['operations', 'Reconcile what we did against what we wrote',
    'Runs the policy over every decision on record and flags each one it would not have produced.'],
  ['rivalries', 'Execute both sides of every rivalry',
    'Runs pairs of rules that answer the same question and flags any disagreement.'],
  ['reverify', 'Re-execute every finding on the queue',
    'Runs every open finding again. One that no longer reproduces is flagged, not kept.'],
  ['fleet', 'Send the fleet against the rules',
    'Five adversarial agents propose claims, and the executor decides which of them land.'],
];

const FINAL = new Set(['died', 'known', 'landed', 'malformed', 'unanswered', 'stopped']);
const NOTHING_TO_JUDGE = new Set(['malformed', 'unanswered', 'stopped']);

const rt = {
  job: null, events: [], lines: [], sinceE: 0, sinceL: 0, receivedAt: 0,
  attaching: false, pulling: false, offline: '',
  agent: null, scopes: [], unmapped: [], problems: [],
  queue: [], summary: null, newIds: new Set(),
};
const ui = { round: null, finding: null, pane: 'findings', built: {}, filter: '' };

function resetRun() {
  rt.job = null; rt.events = []; rt.lines = []; rt.sinceE = 0; rt.sinceL = 0;
  ui.round = null;
}

async function attach(id) {
  if (rt.job && rt.job.id === id) return pull();
  resetRun();
  rt.job = { id, status: 'running', seconds: 0, params: {}, kind: '' };
  rt.attaching = true;
  return pull();
}

async function pull() {
  if (!rt.job || rt.pulling) return;
  rt.pulling = true;
  try {
    const id = rt.job.id;
    const prev = rt.attaching ? null : rt.job.status;
    const j = await api(`/api/exposure/job?id=${id}&since=${rt.sinceL}&since_events=${rt.sinceE}`);
    if (!rt.job || rt.job.id !== id) return;
    const fresh = j.events || [];
    rt.events.push(...fresh);
    rt.lines.push(...(j.lines || []));
    rt.sinceE = j.n_events;
    rt.sinceL = j.n_lines;
    rt.job = j;
    rt.receivedAt = performance.now();
    rt.attaching = false;
    for (const e of fresh) onEvent(e);
    if (fresh.length || prev !== j.status) renderRun();
    if (prev === 'running' && j.status !== 'running') onJobFinished(j);
  } finally {
    rt.pulling = false;
  }
}

async function loop() {
  try {
    if (rt.job && rt.job.status === 'running') {
      await pull();
    } else {
      const r = await api('/api/exposure/job');
      if (r.running && (!rt.job || rt.job.id !== r.running.id)) await attach(r.running.id);
    }
    rt.offline = '';
  } catch (err) {
    rt.offline = err.message;
  }
  renderPulse();
  setTimeout(loop, rt.job && rt.job.status === 'running' ? 1200 : 4000);
}

function onEvent(e) {
  if (e.type === 'round' && e.phase === 'landed' && e.recorded) {
    rt.newIds.add(e.recorded);
    scheduleQueueRefresh();
  }
  if (e.type === 'stage' && e.key === 'operations' && e.status === 'done' && (e.recorded || []).length) {
    for (const id of e.recorded) rt.newIds.add(id);
    scheduleQueueRefresh();
  }
}

function onJobFinished(j) {
  if (j.kind === 'generate' && ui.genBuilt) loadGenRuns();
  scheduleQueueRefresh();
  /* The server drops its cached maps when a job ends, because a closing edit
     or a new finding can move a border. The map pane is rebuilt on next view. */
  ui.built.map = false;
  if (ui.built.history) loadHistory($('pane-history'));
}

let queueTimer = null;
function scheduleQueueRefresh() {
  clearTimeout(queueTimer);
  queueTimer = setTimeout(refreshQueue, 300);
}

async function refreshQueue() {
  try {
    const q = await api('/api/exposure/queue');
    rt.queue = q.items || [];
    rt.summary = q.summary;
  } catch (err) {
    rt.queueError = err.message;
  }
  renderTabs();
  if (ui.built.findings) { renderQueue(); renderSheet(); }
  renderRoster();
}

async function refreshScopes() {
  const s = await api('/api/exposure/scopes');
  rt.scopes = s.scopes || [];
  rt.unmapped = s.unmapped || [];
  rt.problems = s.problems || [];
}

async function loadAgent() {
  try {
    rt.agent = await api('/api/agent');
  } catch (err) {
    rt.agent = { available: false, error: err.message, fleet: [], writers: [] };
  }
}

function liveT() {
  const j = rt.job;
  if (!j) return 0;
  return (j.seconds || 0) + (j.status === 'running' ? (performance.now() - rt.receivedAt) / 1000 : 0);
}

function clock(fromT) {
  const s = el('span', 'clock');
  s.dataset.from = String(fromT || 0);
  s.textContent = fmtDur(liveT() - (fromT || 0));
  return s;
}

function tickClocks() {
  if (!rt.job || rt.job.status !== 'running') return;
  const now = liveT();
  for (const c of document.querySelectorAll('.clock')) {
    c.textContent = fmtDur(now - Number(c.dataset.from));
  }
}

/* Fold the event stream into the state of each stage and each attack. */
function runModel() {
  const m = { plan: null, stages: new Map(), rounds: new Map(), verifies: [], failed: null };
  for (const e of rt.events) {
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
      if (e.phase === 'adjudicating') r.adjT = e.t;
      if (FINAL.has(e.phase)) r.endT = e.t;
      m.rounds.set(e.n, r);
    } else if (e.type === 'verify') m.verifies.push(e);
    else if (e.type === 'failed') m.failed = e;
  }
  /* A job that has ended cannot still be running anything. Whatever it left
     open is settled here, so a failure never reads as work in progress. */
  const j = rt.job;
  if (j && j.status !== 'running' && !rt.attaching) {
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

const isRunning = () => !!(rt.job && rt.job.status === 'running');

const EMPTY_MODEL = { plan: null, stages: new Map(), rounds: new Map(), verifies: [], failed: null };

/* The watcher follows whichever job the server is running. A document being
   drafted is not an exposure job, so the engine shows itself idle meanwhile. */
const expoJob = () => (rt.job && rt.job.kind !== 'generate' ? rt.job : null);

function renderRun() {
  const all = runModel();
  const m = expoJob() ? all : EMPTY_MODEL;
  renderEngineTitle(m);
  renderLaunch();
  renderLoop(m);
  renderColonnade(m);
  renderNow(m);
  renderRoster(m);
  renderSheetJob(all);
  renderGenLive(all);
  renderGenForm();
  renderPulse(all);
}

function renderPulse(m) {
  const p = $('pulse');
  const t = $('pulsetext');
  p.className = 'pulse';
  if (rt.offline) {
    p.classList.add('is-offline');
    t.textContent = 'Server unreachable';
    p.title = rt.offline;
    return;
  }
  const j = rt.job;
  if (j && j.status === 'running') {
    p.classList.add('is-running');
    m = m || runModel();
    if (j.kind === 'sweep' || j.kind === 'fleet') {
      const fs = m.stages.get('fleet');
      const r = latestRound(m);
      if (fs && fs.status === 'running') {
        t.textContent = r ? `Attack ${r.n} of ${r.of}` : 'Loading the model';
      } else {
        t.textContent = 'Sweep running';
      }
    } else {
      t.textContent = j.kind === 'letter' ? 'Writing a letter'
        : j.kind === 'generate' ? 'Drafting a document' : 'Measuring a fix';
    }
    return;
  }
  if (!rt.agent) { t.textContent = 'Agents'; return; }
  if (rt.agent.available) {
    t.textContent = 'Agents idle';
  } else {
    p.classList.add('is-warn');
    t.textContent = 'Model offline';
  }
}

/* ====================================================== exposure: engine */

function renderEngineTitle(m) {
  const j = expoJob();
  let text = 'Five agents are ready to sue this company.';
  if (j && j.status === 'running') {
    if (j.kind === 'letter') text = 'An agent is writing the letter the other side would send.';
    else if (j.kind === 'close') text = 'An agent is looking for the smallest amendment that closes a finding.';
    else {
      const fs = m.stages.get('fleet');
      const r = latestRound(m);
      if (fs && fs.status === 'running') {
        if (!r) text = 'The fleet is loading the model.';
        else if (r.phase === 'proposing') text = `${ARCH[r.archetype] ? ARCH[r.archetype].lead : r.archetype} is drafting a claim against ${breakable(r.scope)}.`;
        else if (r.phase === 'adjudicating') text = `The executor is running that claim against ${breakable(r.scope)}.`;
        else text = 'The fleet is between attacks.';
      } else {
        text = 'Checking the record before the fleet goes in.';
      }
    }
  }
  const h = $('engine-title');
  if (h.textContent !== text) h.textContent = text;
}

function buildLaunch() {
  const host = $('launch');
  host.textContent = '';
  const row = el('div', 'launch-row');
  const go = el('button', 'btn btn-primary btn-l', 'Start autonomous sweep');
  go.type = 'button';
  go.id = 'sweepbtn';
  const lab = el('label', 'stepper');
  lab.appendChild(el('span', 'stepper-label', 'Attacks'));
  const minus = el('button', 'stepper-btn', '−');
  minus.type = 'button';
  minus.setAttribute('aria-label', 'Fewer attacks');
  const n = el('input', 'stepper-input');
  n.id = 'sweeprounds';
  n.type = 'number'; n.min = '0'; n.max = '50'; n.value = '6';
  n.setAttribute('aria-label', 'Number of attacks');
  const plus = el('button', 'stepper-btn', '+');
  plus.type = 'button';
  plus.setAttribute('aria-label', 'More attacks');
  minus.onclick = () => { n.value = String(Math.max(0, (Number(n.value) || 0) - 1)); renderLaunchHint(); };
  plus.onclick = () => { n.value = String(Math.min(50, (Number(n.value) || 0) + 1)); renderLaunchHint(); };
  n.oninput = renderLaunchHint;
  add(lab, minus, n, plus);
  add(row, go, lab);
  host.appendChild(row);
  host.appendChild(el('p', 'launch-hint', '')).id = 'launchhint';

  go.onclick = () => startJob({ kind: 'sweep', rounds: Math.max(0, Math.min(50, Number(n.value) || 0)) });

  const det = el('details', 'single');
  det.appendChild(el('summary', null, 'Attack one rule instead'));
  const form = el('div', 'single-form');
  const scopeSel = el('select', 'field');
  scopeSel.id = 'singlescope';
  scopeSel.setAttribute('aria-label', 'Rule to attack');
  const archSel = el('select', 'field');
  archSel.setAttribute('aria-label', 'Agent');
  const all = el('option', null, 'All five agents, in turn'); all.value = ''; archSel.appendChild(all);
  for (const [k, a] of Object.entries(ARCH)) {
    const o = el('option', null, a.name); o.value = k; archSel.appendChild(o);
  }
  const rounds = el('input', 'field field-short');
  rounds.type = 'number'; rounds.min = '1'; rounds.max = '50'; rounds.value = '3';
  rounds.setAttribute('aria-label', 'Number of attacks');
  const goOne = el('button', 'btn', 'Attack this rule');
  goOne.type = 'button';
  goOne.id = 'singlebtn';
  goOne.onclick = () => startJob({
    kind: 'fleet', scope: scopeSel.value, archetype: archSel.value,
    rounds: Math.max(1, Math.min(50, Number(rounds.value) || 3)),
  });
  add(form, scopeSel, archSel, rounds, goOne);
  det.appendChild(form);
  host.appendChild(det);
  host.appendChild(el('p', 'launch-error', '')).id = 'launcherror';
}

function fillScopeSelect() {
  const sel = $('singlescope');
  if (!sel) return;
  sel.textContent = '';
  for (const sc of rt.scopes) {
    const o = el('option', null, `${sc.key}, deciding ${sc.population}`);
    o.value = sc.key;
    sel.appendChild(o);
  }
}

function renderLaunchHint() {
  const hint = $('launchhint');
  if (!hint) return;
  if (isRunning()) {
    hint.textContent = 'One job runs at a time, because the local model serves one request at a time. It stops after its last attack.';
    return;
  }
  const n = Number(($('sweeprounds') || {}).value) || 0;
  hint.textContent = n === 0
    ? 'With no attacks, the sweep runs its three deterministic stages. They take seconds and need no model.'
    : `The first three stages take seconds. Each attack then takes about a minute on this machine, so allow roughly ${n} minute${n === 1 ? '' : 's'}.`;
}

function renderLaunch() {
  const go = $('sweepbtn');
  const one = $('singlebtn');
  if (!go) return;
  const busy = isRunning();
  go.disabled = busy;
  if (one) one.disabled = busy;
  go.textContent = busy
    ? (rt.job.kind === 'sweep' ? 'Sweep running' : 'Another job is running')
    : 'Start autonomous sweep';
  renderLaunchHint();
}

async function startJob(payload) {
  const err = $('launcherror');
  if (err) err.textContent = '';
  try {
    const j = await api('/api/exposure/run', payload);
    rt.newIds.clear();
    await attach(j.id);
    renderPulse();
    setTimeout(loop, 1200);
    return j;
  } catch (e) {
    if (err && (payload.kind === 'sweep' || payload.kind === 'fleet')) err.textContent = e.message;
    throw e;
  }
}

function renderModelLine() {
  const host = $('modelline');
  const a = rt.agent;
  host.textContent = '';
  if (!a) return;
  if (a.available) {
    const iso = { sandbox: 'enforced by a sandbox', 'sandbox-unproven': 'a sandbox is installed but not yet proven', prompt: 'by instruction only' };
    add(host, el('span', 'dot dot-ok'),
      `Local model ${a.model} is loaded on this machine. Agent isolation: ${iso[a.isolation] || a.isolation}.`);
  } else {
    add(host, el('span', 'dot dot-warn'),
      'The local model is not reachable, so a sweep runs its three deterministic stages and skips the fleet.');
  }
}

function renderLoop(m) {
  const host = $('loop');
  const head = $('loophead');
  host.textContent = '';
  const j = expoJob();
  const desc = Object.fromEntries(SWEEP.map(([k, , d]) => [k, d]));

  let stages;
  if (!j) stages = SWEEP.map(([key, title]) => ({ key, title }));
  else if (m.plan) stages = m.plan.stages;
  else stages = [...m.stages.values()].map((s) => ({ key: s.key, title: s.title }));

  if (!j) head.textContent = 'Each sweep runs four stages, in order';
  else {
    head.textContent = '';
    const label = j.kind === 'sweep' ? 'This sweep' : (j.label ? j.label.charAt(0).toUpperCase() + j.label.slice(1) : 'This job');
    if (j.status === 'running') add(head, `${label} has been running for `, clock(0));
    else if (j.status === 'done') add(head, `${label} finished ${ago(j.started + j.seconds)}, in ${fmtDur(j.seconds)}`);
    else add(head, `${label} failed after ${fmtDur(j.seconds)}`);
  }

  if (j && !stages.length) {
    host.appendChild(el('li', 'stage s-running')).appendChild(waiting('Starting…'));
    return;
  }

  for (const st of stages) {
    const s = m.stages.get(st.key);
    let status = !j ? 'idle' : s ? s.status : (j.status === 'running' ? 'waiting' : 'unreached');
    const li = el('li', 'stage s-' + status);
    li.appendChild(el('span', 'stage-mark'));
    const body = el('div', 'stage-body');
    body.appendChild(el('h3', 'stage-title', st.title));
    const meta = el('p', 'stage-meta');
    if (status === 'idle') meta.textContent = desc[st.key] || '';
    else if (status === 'waiting') meta.textContent = 'Waiting for the stage before it';
    else if (status === 'unreached') meta.textContent = 'Not reached';
    else if (status === 'running') add(meta, 'Running for ', clock(s.startT));
    else if (status === 'skipped') meta.textContent = 'Skipped';
    else if (status === 'failed') meta.textContent = 'Failed';
    else {
      const d = (s.endT || 0) - (s.startT || 0);
      meta.textContent = d < 1 ? 'Done in under a second' : `Done in ${fmtDur(d)}`;
    }
    body.appendChild(meta);
    /* A note describes the wait before a stage's first real event, so it
       goes once the fleet's first attack has started. */
    const noteStale = st.key === 'fleet' && m.rounds.size > 0;
    if (s && s.note && !noteStale) body.appendChild(waiting(s.note.charAt(0).toUpperCase() + s.note.slice(1)));
    if (s && s.summary) body.appendChild(el('p', 'stage-summary', s.summary));

    if (st.key === 'reverify' && s && (m.verifies.length || s.total)) {
      const ticks = el('div', 'ticks');
      const total = s.total || m.verifies.length;
      for (let i = 0; i < total; i++) {
        const v = m.verifies[i];
        const t = el('span', 'tick' + (v ? (v.ok ? ' is-ok' : ' is-stale') : ''));
        t.title = v ? `${v.id}: ${v.why}` : 'not yet executed';
        ticks.appendChild(t);
      }
      body.appendChild(ticks);
    }
    if (st.key === 'fleet' && s && s.status === 'running') {
      const done = [...m.rounds.values()].filter((r) => FINAL.has(r.phase)).length;
      body.appendChild(el('p', 'stage-count', `${done} of ${s.total || (m.plan && m.plan.rounds) || '?'} attacks finished`));
    }
    li.appendChild(body);
    host.appendChild(li);
  }
  if (m.failed) {
    const li = el('li', 'stage s-failed');
    li.appendChild(el('span', 'stage-mark'));
    const body = el('div', 'stage-body');
    add(body, el('h3', 'stage-title', 'The job stopped'), el('p', 'stage-summary', m.failed.error));
    li.appendChild(body);
    host.appendChild(li);
  }
}

/* ================================================= exposure: the colonnade

   The brand mark is a column whose flutes are a bar chart. Each attack is one
   flute between a capital and a base, and its height is how far it got: an
   attack that died at the executor barely rises, one that landed reaches the
   capital. Unrun attacks stand as outlines, so the length of the run is
   visible before it starts. */

const PHASE_WORD = {
  pending: 'not yet run',
  proposing: 'the agent is drafting',
  adjudicating: 'the executor is running it',
  died: 'no exposure: the rule handled it',
  malformed: 'the agent gave an unusable reply',
  unanswered: 'the model did not answer in time',
  stopped: 'stopped when the job ended',
  known: 'landed on a hole already queued',
  landed: 'landed and was queued',
};

function plannedRounds(m) {
  const fs = m.stages.get('fleet');
  if (fs && fs.total) return fs.total;
  if (m.plan) return m.plan.rounds || 0;
  if (rt.job && rt.job.kind === 'fleet') return rt.job.params.rounds || 0;
  const r = latestRound(m);
  return r ? r.of : 0;
}

function renderColonnade(m) {
  const host = $('flutes');
  host.textContent = '';
  const total = plannedRounds(m);
  const count = $('attackcount');
  const rounds = [...m.rounds.values()];
  const fin = rounds.filter((r) => FINAL.has(r.phase));
  const landed = fin.filter((r) => r.phase === 'landed').length;
  const known = fin.filter((r) => r.phase === 'known').length;

  if (!total) {
    $('colonnade').classList.add('is-empty');
    const skipped = m.stages.get('fleet');
    host.appendChild(el('p', 'flutes-empty', skipped && skipped.status === 'skipped'
      ? `The fleet did not run: ${skipped.summary}.`
      : 'No attacks have run since the server started. Start a sweep and each attack rises here as it is proposed and judged.'));
    count.textContent = '';
    return;
  }
  $('colonnade').classList.remove('is-empty');
  const died = fin.filter((r) => r.phase === 'died').length;
  const empty = fin.filter((r) => NOTHING_TO_JUDGE.has(r.phase)).length;
  count.textContent = fin.length
    ? `${fin.length} of ${total} finished: ${landed} landed and queued, ${known} landed on a known hole, ${died} found no exposure` +
      (empty ? `, ${empty} produced nothing to judge` : '')
    : `${total} planned`;

  const sel = ui.round || (latestRound(m) || {}).n;
  for (let n = 1; n <= total; n++) {
    const r = m.rounds.get(n);
    const phase = r ? r.phase : 'pending';
    const f = el('button', `flute p-${phase}` + (n === sel && r ? ' is-sel' : ''));
    f.type = 'button';
    const who = r ? `${archName(r.archetype)} against ${r.scope}` : '';
    f.setAttribute('aria-label', `Attack ${n}${who ? ', ' + who : ''}: ${PHASE_WORD[phase]}`);
    f.title = `Attack ${n}${who ? '\n' + who : ''}\n${PHASE_WORD[phase]}`;
    f.appendChild(el('span', 'flute-shaft'));
    if (r) f.onclick = () => { ui.round = n; renderColonnade(runModel()); renderNow(runModel()); };
    else f.disabled = true;
    host.appendChild(f);
  }
}

function renderNow(m) {
  const host = $('now');
  host.textContent = '';
  const latest = latestRound(m);
  const r = ui.round ? m.rounds.get(ui.round) : latest;
  const fs = m.stages.get('fleet');

  if (!r) {
    if (fs && fs.status === 'running') {
      host.appendChild(waiting('Loading the model’s weights into memory. The first attack starts when they are in.'));
    }
    host.hidden = !host.childElementCount;
    return;
  }
  host.hidden = false;

  const side = el('div', 'now-side');
  add(side, el('p', 'now-n', `Attack ${r.n} of ${r.of}`),
    el('p', 'now-scope', breakable(r.scope)),
    el('p', 'now-agent', archName(r.archetype)));
  if (!FINAL.has(r.phase) && isRunning()) add(side, el('p', 'now-clock')).appendChild(clock(r.startT));
  else if (r.endT !== undefined && r.startT !== undefined) side.appendChild(el('p', 'now-clock', `took ${fmtDur(r.endT - r.startT)}`));
  if (ui.round && latest && ui.round !== latest.n) {
    const follow = el('button', 'btn btn-ghost btn-s', 'Follow the latest attack');
    follow.type = 'button';
    follow.onclick = () => { ui.round = null; renderColonnade(runModel()); renderNow(runModel()); };
    side.appendChild(follow);
  }

  const main = el('div', 'now-main');
  const lead = ARCH[r.archetype] ? ARCH[r.archetype].lead : r.archetype;
  let head;
  if (r.phase === 'proposing') head = `${lead} is drafting a claim.`;
  else if (r.phase === 'adjudicating') head = `${lead} proposed this claim. The executor is running it now.`;
  else if (r.phase === 'died') head = `No exposure: ${r.plain_why || r.why}.`;
  else if (r.phase === 'malformed') head = r.plain_why || 'The agent’s reply could not be used, so nothing was executed.';
  else if (r.phase === 'unanswered') head = 'The model did not answer in time, so nothing was proposed or executed.';
  else if (r.phase === 'stopped') head = 'Stopped before a verdict, because the job ended.';
  else if (r.phase === 'known') head = `Landed, on a hole already on the queue. ${cap(r.why)}.`;
  else if (r.phase === 'landed') head = `Landed, and queued as ${r.recorded}. ${cap(r.why)}.`;
  main.appendChild(el('h3', 'now-head p-' + r.phase, head));

  if (r.phase === 'proposing') {
    main.appendChild(waiting('The model is writing a fact pattern from the clauses and the rule’s interface. Nothing reaches the queue until the executor has run it.'));
  }
  if (NOTHING_TO_JUDGE.has(r.phase) && r.why) main.appendChild(el('p', 'now-why mono', r.why));
  if (r.narrative) {
    main.appendChild(el('p', 'now-label', FINAL.has(r.phase)
      ? 'The claim, in the agent’s words'
      : 'The claim, in the agent’s words, not yet checked'));
    main.appendChild(el('blockquote', 'now-claim', r.narrative));
  }
  if (r.contends) main.appendChild(el('p', 'now-contends', r.contends));
  if ((r.citations || []).length) main.appendChild(cites(r.citations));
  if (r.facts) {
    const facts = el('p', 'now-facts mono');
    facts.textContent = Object.entries(r.facts).map(([k, v]) => `${k} ${JSON.stringify(v)}`).join('   ');
    main.appendChild(facts);
  }
  if (r.phase === 'landed' && r.recorded) {
    const b = el('button', 'btn btn-brass', `Open ${r.recorded}`);
    b.type = 'button';
    b.onclick = () => { showPane('findings'); openFinding(r.recorded); $('tabs').scrollIntoView({ behavior: 'smooth' }); };
    main.appendChild(b);
  }
  add(host, side, main);
}

const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');

/* ======================================================= exposure: fleet */

function renderRoster(m) {
  const host = $('roster');
  if (!host) return;
  m = m || runModel();
  host.textContent = '';
  const fleet = (rt.agent && rt.agent.fleet && rt.agent.fleet.length)
    ? rt.agent.fleet
    : Object.keys(ARCH).map((k) => ({ key: k, purpose: '' }));
  const latest = latestRound(m);
  const active = isRunning() && latest && !FINAL.has(latest.phase) ? latest : null;

  const found = {};
  for (const e of rt.queue) {
    const k = (e.origin || '').startsWith('fleet:') ? e.origin.slice(6) : null;
    if (k) (found[k] = found[k] || []).push(e.id);
  }
  const tally = {};
  for (const r of m.rounds.values()) {
    const t = tally[r.archetype] = tally[r.archetype] || { tried: 0, landed: 0 };
    if (FINAL.has(r.phase)) t.tried += 1;
    if (r.phase === 'landed' || r.phase === 'known') t.landed += 1;
  }

  for (const a of fleet) {
    const on = active && active.archetype === a.key;
    const col = el('article', 'agent' + (on ? ' is-active' : ''));
    col.appendChild(el('h3', 'agent-name', archName(a.key)));
    col.appendChild(el('p', 'agent-purpose', a.purpose));
    const st = el('p', 'agent-state');
    if (on) add(st, el('span', 'dot dot-live'), active.phase === 'proposing' ? 'Drafting a claim' : 'Waiting on the executor');
    else add(st, el('span', 'dot'), 'Idle');
    col.appendChild(st);
    const t = tally[a.key];
    col.appendChild(el('p', 'agent-tally', t && t.tried
      ? `${plural(t.tried, 'attack')} this run, ${t.landed} landed`
      : 'No attacks this run'));
    const f = found[a.key] || [];
    col.appendChild(el('p', 'agent-found', f.length ? `Found ${f.join(', ')}` : 'Nothing on the queue yet'));
    host.appendChild(col);
  }

  const writers = $('writers');
  writers.textContent = '';
  const w = (rt.agent && rt.agent.writers) || [];
  if (w.length) {
    const words = {
      'fleet-demand': ['Letter writer', 'Writes a finding up as the letter it would arrive as. It runs only on a finding the executor already accepted, and may not produce a figure of its own.'],
      remedy: ['Remedy', 'Proposes the smallest amendment that closes a finding. The amendment is applied to the real module, rebuilt, measured against every map and test, then put back.'],
    };
    for (const r of w) {
      const [name, body] = words[r.name] || [r.name, r.purpose];
      const row = el('div', 'writer');
      add(row, el('h3', 'writer-name', name), el('p', 'writer-body', body));
      writers.appendChild(row);
    }
  }
}

/* ==================================================== exposure: workspace */

const PANES = [
  ['findings', 'Findings', buildFindings],
  ['map', 'Decision map', buildMap],
  ['probe', 'Probe a case', buildProbe],
  ['watchers', 'Watchers', buildWatchers],
  ['history', 'Run history', loadHistory],
];

function buildWork() {
  const tabs = $('tabs');
  const panes = $('panes');
  tabs.textContent = '';
  panes.textContent = '';
  for (const [key] of PANES) {
    const b = el('button', 'tab');
    b.type = 'button';
    b.id = 'tab-' + key;
    b.dataset.pane = key;
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-controls', 'pane-' + key);
    b.onclick = () => showPane(key);
    tabs.appendChild(b);
    const p = el('section', 'pane');
    p.id = 'pane-' + key;
    p.setAttribute('role', 'tabpanel');
    p.setAttribute('aria-labelledby', 'tab-' + key);
    p.hidden = true;
    panes.appendChild(p);
  }
  renderTabs();
  showPane(ui.pane);
}

function renderTabs() {
  for (const [key, title] of PANES) {
    const b = $('tab-' + key);
    if (!b) continue;
    b.textContent = '';
    b.appendChild(document.createTextNode(title));
    if (key === 'findings' && rt.summary) b.appendChild(el('span', 'tab-count', String(rt.summary.total)));
    b.setAttribute('aria-selected', String(ui.pane === key));
  }
}

function showPane(key) {
  ui.pane = key;
  renderTabs();
  for (const [k, , build] of PANES) {
    const p = $('pane-' + k);
    p.hidden = k !== key;
    if (k === key && !ui.built[k]) {
      ui.built[k] = true;
      build(p);
    }
  }
}

/* ---- findings */

function amountText(e) {
  if (e.amount === null || e.amount === undefined) return null;
  const sc = rt.scopes.find((s) => s.key === e.scope);
  const unit = (sc && sc.unit) || '';
  const n = Number(e.amount);
  if (!isFinite(n)) return String(e.amount);
  if (unit === 'money') return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${n}`;
}

function amountUnit(e) {
  const sc = rt.scopes.find((s) => s.key === e.scope);
  const unit = (sc && sc.unit) || '';
  if (unit === 'money' || !unit) return e.population ? `on ${e.population}` : '';
  return `${unit}, on ${e.population || 'one case'}`;
}

function originWord(e) {
  const o = e.origin || '';
  if (o.startsWith('fleet:')) return `${archName(o.slice(6))}, in the fleet`;
  if (o.startsWith('operations:')) return `The operations watcher, from decision ${o.slice(11)}`;
  if (o === 'web') return 'Probed by hand in this interface';
  return o || 'Unrecorded';
}

function buildFindings(host) {
  host.textContent = '';
  const top = el('div', 'pane-top');
  top.appendChild(el('p', 'pane-intro',
    'Every finding here reached a bad outcome when the rule was executed on its facts. None is a model’s opinion that something looks wrong. Decisions already made sort first, because they have already happened.'));
  const filt = el('select', 'field');
  filt.setAttribute('aria-label', 'Show findings of one kind');
  const any = el('option', null, 'Every kind of finding'); any.value = ''; filt.appendChild(any);
  for (const [k, w] of Object.entries(KLASS)) {
    const o = el('option', null, w); o.value = k; filt.appendChild(o);
  }
  filt.value = ui.filter;
  filt.onchange = () => { ui.filter = filt.value; renderQueue(); };
  top.appendChild(filt);
  host.appendChild(top);
  const grid = el('div', 'findings');
  const list = el('div', 'qlist'); list.id = 'qlist';
  const sheet = el('article', 'sheet'); sheet.id = 'sheet';
  add(grid, list, sheet);
  host.appendChild(grid);
  renderQueue();
  if (!ui.finding && rt.queue.length) ui.finding = rt.queue[0].id;
  renderSheet();
}

function renderQueue() {
  const host = $('qlist');
  if (!host) return;
  host.textContent = '';
  const items = rt.queue.filter((e) => !ui.filter || e.klass === ui.filter);
  if (!items.length) {
    host.appendChild(el('p', 'empty', rt.queue.length
      ? 'Nothing of this kind is on the queue.'
      : 'The queue is empty. Start a sweep to fill it.'));
    return;
  }
  for (const e of items) {
    const b = el('button', 'qitem k-' + e.klass.toLowerCase()
      + (ui.finding === e.id ? ' is-sel' : '') + (rt.newIds.has(e.id) ? ' is-new' : ''));
    b.type = 'button';
    const top = el('span', 'qitem-top');
    add(top, el('span', 'qitem-id', e.id), el('span', 'qitem-klass', KLASS[e.klass] || e.klass));
    if (rt.newIds.has(e.id)) top.appendChild(el('span', 'qitem-new', 'New this run'));
    b.appendChild(top);
    b.appendChild(el('span', 'qitem-head', e.headline));
    const foot = el('span', 'qitem-foot');
    const amt = amountText(e);
    if (amt) foot.appendChild(el('span', 'qitem-amt', amt));
    foot.appendChild(el('span', 'qitem-scope', e.scope));
    b.appendChild(foot);
    b.onclick = () => openFinding(e.id);
    host.appendChild(b);
  }
}

function openFinding(id) {
  ui.finding = id;
  if (!ui.built.findings) { showPane('findings'); }
  renderQueue();
  renderSheet();
  if (window.matchMedia('(max-width: 960px)').matches) $('sheet').scrollIntoView({ behavior: 'smooth' });
}

function renderSheet() {
  const host = $('sheet');
  if (!host) return;
  host.textContent = '';
  const e = rt.queue.find((x) => x.id === ui.finding);
  if (!e) {
    host.appendChild(el('p', 'sheet-empty', 'Choose a finding to read it in full.'));
    return;
  }
  const head = el('header', 'sheet-head');
  add(head, el('p', 'sheet-id', `${e.id}, found ${e.found || 'on an unrecorded date'}`),
    el('p', 'sheet-klass', KLASS[e.klass] || e.klass),
    el('h2', 'sheet-title', e.headline));
  host.appendChild(head);

  const amt = amountText(e);
  const figure = el('div', 'sheet-figure');
  if (amt) {
    add(figure, el('p', 'sheet-amount', amt), el('p', 'sheet-unit', amountUnit(e)));
  } else {
    figure.appendChild(el('p', 'sheet-unit', 'Not costed'));
  }
  if (e.amount_basis) figure.appendChild(el('p', 'sheet-basis', e.amount_basis));
  host.appendChild(figure);

  host.appendChild(kv([
    ['Who would bring it', cap(e.archetype)],
    ['What the rule decides', e.population],
    ['Rule', e.scope, true],
    ['Found by', originWord(e)],
    ['Predicate', e.predicate, true],
  ]));

  if (e.narrative) {
    host.appendChild(el('h3', 'sheet-h', 'The fact pattern'));
    host.appendChild(lawBlock(e.narrative));
  }
  host.appendChild(el('h3', 'sheet-h', 'The theory'));
  host.appendChild(lawBlock(e.why));
  if ((e.citations || []).length) host.appendChild(cites(e.citations));

  host.appendChild(el('h3', 'sheet-h', 'The facts it was executed on'));
  host.appendChild(kv(Object.entries(e.facts || {}).map(([k, v]) => [k.replace(/_/g, ' '), JSON.stringify(v), true])));
  if (Object.keys(e.outputs || {}).length) {
    host.appendChild(el('h3', 'sheet-h', 'What the policy computes'));
    host.appendChild(kv(Object.entries(e.outputs).map(([k, v]) => [k.replace(/_/g, ' '), String(v), true])));
  }
  if ((e.clause_chain || []).length) {
    host.appendChild(el('h3', 'sheet-h', 'Which provision decided what'));
    host.appendChild(chain(e.clause_chain));
  }
  if (e.diagnostic) host.appendChild(el('pre', 'diag', e.diagnostic));

  if (e.demand_letter) {
    host.appendChild(el('h3', 'sheet-h', 'The letter, from the other side'));
    host.appendChild(el('p', 'sheet-note', 'Drafted by the local model from a finding the executor had already accepted. Every figure in it was computed; the model was forbidden to produce one.'));
    host.appendChild(el('div', 'letter', e.demand_letter));
  }

  host.appendChild(el('h3', 'sheet-h', 'Hand it to an agent'));
  const acts = el('div', 'sheet-acts');
  const letter = el('button', 'btn btn-ink', e.demand_letter ? 'Draft the letter again' : 'Draft the demand letter');
  letter.type = 'button';
  const fix = el('button', 'btn btn-ink-ghost', 'Propose a fix and measure it');
  fix.type = 'button';
  const busy = isRunning();
  letter.disabled = busy || e.headline_direction === 'more';
  fix.disabled = busy;
  const out = el('div', 'sheet-job');
  out.id = 'sheetjob';
  const errp = el('p', 'sheet-error', '');
  letter.onclick = () => startJob({ kind: 'letter', id: e.id }).catch((x) => { errp.textContent = x.message; });
  fix.onclick = () => startJob({ kind: 'close', id: e.id }).catch((x) => { errp.textContent = x.message; });
  add(acts, letter, fix);
  host.appendChild(acts);
  if (e.headline_direction === 'more') {
    host.appendChild(el('p', 'sheet-note', 'No letter for this one. The decision was more generous than the policy provides, so nobody has a claim. It is evidence that the company does not apply the reading it would need to refuse someone else.'));
  } else if (busy) {
    host.appendChild(el('p', 'sheet-note', 'Available when the running job finishes. The local model serves one job at a time.'));
  } else {
    host.appendChild(el('p', 'sheet-note', 'Each takes a minute or two on the local model. The fix is only a proposal; what follows it is measured by applying it to the real module, rebuilding, re-running everything, and putting the module back.'));
  }
  add(host, errp, out);
  renderSheetJob();
}

function renderSheetJob(m) {
  const host = $('sheetjob');
  if (!host) return;
  host.textContent = '';
  const j = rt.job;
  if (!j || !['letter', 'close'].includes(j.kind) || (j.params || {}).id !== ui.finding) return;
  m = m || runModel();
  const ol = el('ol', 'ministeps');
  for (const s of m.stages.values()) {
    const li = el('li', 'ministep s-' + s.status);
    li.appendChild(el('span', 'stage-mark'));
    const b = el('div');
    b.appendChild(el('p', 'ministep-title', s.title));
    if (s.status === 'running') add(b.appendChild(el('p', 'ministep-meta')), 'Running for ', clock(s.startT));
    else if (s.summary) b.appendChild(el('p', 'ministep-meta', s.summary));
    li.appendChild(b);
    ol.appendChild(li);
  }
  if (!m.stages.size && j.status === 'running') host.appendChild(waiting('Starting the agent…'));
  host.appendChild(ol);
  if (j.status === 'failed') host.appendChild(el('p', 'sheet-error', j.error));
  if (j.status !== 'done' || !j.result) return;
  const r = j.result;
  if (j.kind === 'letter' && r.letter) {
    host.appendChild(el('h3', 'sheet-h', 'The letter, from the other side'));
    host.appendChild(el('div', 'letter', r.letter));
  }
  if (j.kind === 'close' && r.edit) {
    host.appendChild(el('h3', 'sheet-h', 'The amendment to the document'));
    host.appendChild(lawBlock(r.edit.clause_amendment));
    host.appendChild(el('h3', 'sheet-h', 'Why'));
    host.appendChild(lawBlock(r.edit.rationale));
    host.appendChild(el('h3', 'sheet-h', 'The edit to the encoding'));
    host.appendChild(el('pre', 'diag',
      String(r.edit.old).split('\n').map((l) => '- ' + l).join('\n') + '\n' +
      String(r.edit.new).split('\n').map((l) => '+ ' + l).join('\n')));
    const box = el('div', 'report' + (r.ok ? ' is-ok' : ' is-warn'));
    for (const row of r.report || []) box.appendChild(el('p', null, row));
    host.appendChild(box);
  }
}

function chain(rows) {
  const box = el('div', 'chain');
  for (const g of rows) {
    const row = el('div', 'chain-row');
    row.appendChild(el('span', 'chain-var', String(g.variable).replace(/_/g, ' ')));
    const refs = el('span', 'chain-refs');
    if (!(g.clause_refs || []).length) refs.appendChild(el('span', 'muted', 'no clause attributed'));
    for (const ref of g.clause_refs || []) refs.appendChild(citeButton(ref));
    row.appendChild(refs);
    box.appendChild(row);
  }
  return box;
}

/* ---- decision map

   Regions are outcome classes, borders are the clauses that separate them, and
   area is how many cases fall in each. Squarified so area reads as area.
   Nothing here computes anything: every region came from the same partition
   the command line prints. */

const mapS = { scope: null, map: null, region: null };

const OUTCOME = {
  computed: 'Decided',
  conflict: 'Our documents contradict each other',
  silent: 'The corpus is silent',
  refused: 'The policy refuses to answer',
};

function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((s, it) => s + it.weight, 0) || 1;
  const rest = items.slice().sort((a, b) => b.weight - a.weight);
  const scale = (w * h) / total;
  const worst = (row, len) => {
    if (!row.length || len <= 0) return Infinity;
    const s = row.reduce((a, r) => a + r.weight * scale, 0);
    const mx = Math.max(...row.map((r) => r.weight * scale));
    const mn = Math.min(...row.map((r) => r.weight * scale));
    return Math.max((len * len * mx) / (s * s), (s * s) / (len * len * mn));
  };
  while (rest.length) {
    const horizontal = w >= h;
    const len = horizontal ? h : w;
    const row = [];
    while (rest.length) {
      const next = row.concat([rest[0]]);
      if (row.length && worst(next, len) > worst(row, len)) break;
      row.push(rest.shift());
    }
    const s = row.reduce((a, r) => a + r.weight * scale, 0);
    const thick = len > 0 ? s / len : 0;
    let off = 0;
    for (const r of row) {
      const side = s > 0 ? (r.weight * scale) / thick : 0;
      out.push(horizontal
        ? { item: r, x, y: y + off, w: thick, h: side }
        : { item: r, x: x + off, y, w: side, h: thick });
      off += side;
    }
    if (horizontal) { x += thick; w -= thick; } else { y += thick; h -= thick; }
    if (w <= 0.01 || h <= 0.01) break;
  }
  return out;
}

function buildMap(host) {
  host.textContent = '';
  const top = el('div', 'pane-top');
  top.appendChild(el('p', 'pane-intro',
    'The rule as one decision function. Each region is a set of cases the interpreter decided the same way, each border is the clause that separates two regions, and area is how many cases fall in each.'));
  const sel = el('select', 'field');
  sel.setAttribute('aria-label', 'Rule to map');
  for (const sc of rt.scopes) {
    const o = el('option', null, sc.key); o.value = sc.key; sel.appendChild(o);
  }
  const area = el('select', 'field');
  area.id = 'areamode';
  area.setAttribute('aria-label', 'What area measures');
  for (const [v, w] of [['cases', 'Area is executed cases'], ['people', 'Area is real decisions on record']]) {
    const o = el('option', null, w); o.value = v; area.appendChild(o);
  }
  const controls = el('div', 'pane-controls');
  add(controls, sel, area);
  top.appendChild(controls);
  host.appendChild(top);

  const legend = el('ul', 'map-legend');
  for (const [cls, word] of [['o-computed', OUTCOME.computed], ['o-conflict', OUTCOME.conflict],
    ['o-silent', OUTCOME.silent], ['o-refused', OUTCOME.refused], ['exposed', 'A finding lands here']]) {
    add(legend.appendChild(el('li')), el('span', 'sw ' + cls), word);
  }
  host.appendChild(legend);
  const meta = el('div', 'map-meta'); meta.id = 'mapmeta';
  const canvas = el('div', 'mapcanvas'); canvas.id = 'mapcanvas';
  const region = el('div', 'region'); region.id = 'regionbody';
  add(host, meta, canvas, region);

  if (rt.unmapped.length) {
    const det = el('details', 'unmapped');
    det.appendChild(el('summary', null, `${rt.unmapped.length} rules are invisible to the engine, because nobody has declared who they decide about`));
    det.appendChild(el('p', null, 'The engine cannot tell a real case from an impossible one for these, so it reports nothing about them rather than guessing. Declare them in exposure/domains.yaml. This limits what is found, never whether a finding is true.'));
    det.appendChild(el('p', 'mono', rt.unmapped.join('   ')));
    host.appendChild(det);
  }

  sel.onchange = () => loadMap(sel.value);
  area.onchange = drawMap;
  const first = mapS.scope || (rt.scopes.find((s) => s.predicates > 0) || rt.scopes[0] || {}).key;
  if (first) { sel.value = first; loadMap(first); }
  window.addEventListener('resize', () => { if (!$('pane-map').hidden) drawMap(); });
}

async function loadMap(scope) {
  mapS.scope = scope;
  const host = $('mapcanvas');
  host.textContent = '';
  host.style.height = '';
  host.appendChild(waiting('Executing the rule across its grid. A rule seen for the first time takes a while.'));
  $('regionbody').textContent = '';
  let m;
  try {
    m = await api('/api/exposure/map?scope=' + encodeURIComponent(scope));
  } catch (err) {
    host.textContent = '';
    host.appendChild(notice(err.message, 'warn'));
    return;
  }
  if (mapS.scope !== scope) return;
  mapS.map = m;
  const meta = $('mapmeta');
  meta.textContent = '';
  meta.appendChild(el('p', 'map-line',
    `${plural(m.summary.regions, 'region')} over ${plural(m.summary.cells, 'executed case')}, deciding ${m.population}` +
    (m.incoherent ? `. ${plural(m.incoherent, 'grid point')} described nobody and were not executed.` : '.')));
  if ((m.elided || []).length) {
    meta.appendChild(notice(`Flattened to stay under the cell ceiling: ${m.elided.join('; ')}. This is a map of that slice.`, 'warn'));
  }
  const axes = el('div', 'axes');
  for (const [name, vals] of Object.entries(m.axes)) {
    const a = el('div', 'axis');
    add(a, el('span', 'axis-name', name.replace(/_/g, ' ')),
      el('span', 'axis-vals mono', vals.map((v) => JSON.stringify(v)).join('  ')),
      el('span', 'axis-why', (m.axis_reasons[name] || []).join('; ')));
    axes.appendChild(a);
  }
  const det = el('details', 'axes-wrap');
  det.appendChild(el('summary', null, 'Where the grid came from'));
  det.appendChild(axes);
  meta.appendChild(det);
  drawMap();
  const pick = m.regions.find((r) => (r.exposures || []).length) || m.regions.find((r) => r.outcome !== 'computed') || m.regions[0];
  if (pick) showRegion(pick.id);
}

function drawMap() {
  const host = $('mapcanvas');
  const m = mapS.map;
  if (!host || !m) return;
  host.textContent = '';
  const usePeople = m.regions.some((r) => r.population > 0) && ($('areamode') || {}).value === 'people';
  const items = m.regions.map((r) => ({ r, weight: Math.max(usePeople ? r.population : r.cases, 0.25) }));
  const W = Math.max(host.getBoundingClientRect().width || 900, 280);
  const H = W < 600 ? 360 : 440;
  for (const t of squarify(items, 0, 0, W, H)) {
    const r = t.item.r;
    const cls = ['tile', 'o-' + r.outcome];
    if ((r.exposures || []).length) cls.push('exposed');
    if (r.id === mapS.region) cls.push('is-sel');
    const d = el('button', cls.join(' '));
    d.type = 'button';
    d.style.cssText = `left:${t.x}px;top:${t.y}px;width:${Math.max(t.w - 3, 1)}px;height:${Math.max(t.h - 3, 1)}px`;
    d.setAttribute('aria-label', `${r.id}, ${OUTCOME[r.outcome] || r.outcome}, ${r.cases} cases, ${r.population} on record`);
    d.title = `${r.id}: ${OUTCOME[r.outcome] || r.outcome}\n${r.cases} cases, ${r.population} on record\n${(r.clause_refs || []).join(', ')}`;
    if (t.w > 64 && t.h > 36) {
      d.appendChild(el('span', 'tile-id', r.id));
      if (t.h > 56) d.appendChild(el('span', 'tile-sub', r.population > 0 ? `${r.population} on record` : `${r.cases} cases`));
      if (t.h > 84 && (r.clause_refs || []).length) d.appendChild(el('span', 'tile-ref', r.clause_refs[0]));
    }
    d.onclick = () => showRegion(r.id);
    host.appendChild(d);
  }
  host.style.height = H + 'px';
}

function showRegion(id) {
  mapS.region = id;
  for (const t of document.querySelectorAll('#mapcanvas .tile')) t.classList.remove('is-sel');
  drawMap();
  const r = mapS.map.regions.find((x) => x.id === id);
  const host = $('regionbody');
  host.textContent = '';
  if (!r) return;
  const head = el('div', 'region-head');
  add(head, el('h3', 'region-id', r.id), el('span', 'badge o-' + r.outcome, OUTCOME[r.outcome] || r.outcome));
  host.appendChild(head);
  const rows = [
    ['Cases in this region', String(r.cases), true],
    ['Real decisions on record', String(r.population), true],
  ];
  if (r.measure && r.value_low !== null) {
    rows.push([r.measure.replace(/_/g, ' '), r.value_low === r.value_high ? r.value_low : `${r.value_low} to ${r.value_high}`, true]);
  }
  rows.push(['An example', Object.entries(r.exemplar).map(([k, v]) => `${k} ${JSON.stringify(v)}`).join('   '), true]);
  host.appendChild(kv(rows));
  if (r.outcome !== 'computed' && r.diagnostic) host.appendChild(el('pre', 'diag', r.diagnostic));
  host.appendChild(el('h4', 'h4', 'Which provision decided what'));
  host.appendChild(chain(r.governing));
  if ((r.exposures || []).length) {
    host.appendChild(el('h4', 'h4', 'Findings that land here'));
    for (const eid of r.exposures) {
      const e = rt.queue.find((x) => x.id === eid);
      const b = el('button', 'linkrow', `${eid}: ${e ? e.headline : ''}`);
      b.type = 'button';
      b.onclick = () => { showPane('findings'); openFinding(eid); };
      host.appendChild(b);
    }
  }
  const probeBtn = el('button', 'btn btn-ghost btn-s', 'Probe this region by hand');
  probeBtn.type = 'button';
  probeBtn.onclick = () => { probe.fillFrom = r.exemplar; probe.scope = mapS.scope; showPane('probe'); loadProbeSchema(mapS.scope); };
  host.appendChild(probeBtn);

  const det = el('details', 'whatif');
  det.appendChild(el('summary', null, 'Move a border'));
  det.appendChild(el('p', 'pane-intro', 'Replace a passage of the module with another. The edit is applied to the real file, the project is rebuilt, every mapped rule is executed again and the file is put back. What comes back is measured, not predicted, so it takes a while.'));
  const form = el('form', 'whatif-form');
  const oldT = el('textarea', 'field mono');
  oldT.placeholder = 'The exact passage to replace, as it appears in the module';
  oldT.setAttribute('aria-label', 'Passage to replace');
  const newT = el('textarea', 'field mono');
  newT.placeholder = 'What it becomes';
  newT.setAttribute('aria-label', 'Replacement passage');
  const go = el('button', 'btn', 'Apply, measure and redraw');
  go.type = 'submit';
  const out = el('div');
  add(form, oldT, newT, go);
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    out.textContent = '';
    out.appendChild(waiting('Applying the edit, rebuilding, executing every mapped rule again…'));
    go.disabled = true;
    try {
      const d = await api('/api/exposure/whatif', { scope: mapS.scope, old: oldT.value, new: newT.value });
      renderWhatIf(d, out);
      await loadMap(mapS.scope);
    } catch (err) {
      out.textContent = '';
      out.appendChild(notice(err.message, 'warn'));
    } finally {
      go.disabled = false;
    }
  };
  add(det, form, out);
  host.appendChild(det);
}

function renderWhatIf(d, out) {
  out.textContent = '';
  const box = el('div', 'report' + (d.ok ? ' is-ok' : ' is-warn'));
  for (const row of d.report) box.appendChild(el('p', null, row));
  if (!d.typecheck_ok && d.typecheck_diagnostic) box.appendChild(el('pre', 'diag', d.typecheck_diagnostic));
  out.appendChild(box);
  for (const diff of d.diffs) {
    out.appendChild(el('h4', 'h4', diff.summary));
    const tbl = el('div', 'moves');
    for (const c of diff.changes.slice(0, 40)) {
      const row = el('div', 'move');
      add(row, el('span', 'move-facts mono', JSON.stringify(c.inputs)), el('span', 'move-kind', c.kind),
        el('span', 'move-vals mono', c.before === null && c.after === null ? '' : `${c.before} to ${c.after}`));
      tbl.appendChild(row);
    }
    if (diff.changes.length > 40) tbl.appendChild(el('p', 'muted', `and ${diff.changes.length - 40} more`));
    out.appendChild(tbl);
  }
}

/* ---- probe

   Not for finding anything. It is so somebody can satisfy themselves, by hand,
   that the executor is deciding rather than agreeing: put in a case the policy
   handles correctly and watch it die; move one fact and watch it land; put in
   somebody who cannot exist and watch it refuse. The form shows the realisable
   range beside each field and does not enforce it, because the executor's
   refusal is the thing worth seeing. */

const probe = { scope: null, schema: null, fields: {}, fillFrom: null };

function buildProbe(host) {
  host.textContent = '';
  const top = el('div', 'pane-top');
  top.appendChild(el('p', 'pane-intro',
    'Put a case to the executor yourself. A case the policy handles correctly dies; move one fact and it may land; a person who cannot exist is refused, with the reason quoted from the declared domain.'));
  const sel = el('select', 'field');
  sel.id = 'probescope';
  sel.setAttribute('aria-label', 'Rule to probe');
  for (const sc of rt.scopes) {
    const o = el('option', null, sc.key); o.value = sc.key; sel.appendChild(o);
  }
  sel.onchange = () => { probe.fillFrom = null; loadProbeSchema(sel.value); };
  top.appendChild(sel);
  host.appendChild(top);
  const form = el('div', 'probe'); form.id = 'probeform';
  host.appendChild(form);
  const first = probe.scope || mapS.scope || (rt.scopes[0] || {}).key;
  if (first) { sel.value = first; loadProbeSchema(first); }
}

function probeField(f) {
  const row = el('div', 'pfield' + (f.judgement ? ' is-judgement' : ''));
  const id = 'probe-' + f.name;
  const lab = el('label', 'plabel', f.name.replace(/_/g, ' '));
  lab.htmlFor = id;
  row.appendChild(lab);
  let input;
  if (f.type === 'boolean') {
    input = el('select', 'field');
    for (const [v, w] of [['false', 'no'], ['true', 'yes']]) {
      const o = el('option', null, w); o.value = v; input.appendChild(o);
    }
  } else if (f.kind === 'enum' && (f.enum || []).length) {
    input = el('select', 'field');
    for (const v of f.enum) { const o = el('option', null, v); o.value = v; input.appendChild(o); }
  } else if (f.kind === 'struct') {
    input = el('textarea', 'field mono');
    input.value = JSON.stringify((f.probe && f.probe[0]) || {});
  } else {
    input = el('input', 'field');
    if (f.type === 'date') input.placeholder = 'YYYY-MM-DD';
    /* Start from a value the compiler's own borders name, so the form can be
       put to the executor as it stands and then moved one fact at a time. */
    const start = Array.isArray(f.probe) && f.probe.length ? f.probe[0]
      : Array.isArray(f.realisable) && f.realisable.length ? f.realisable[0] : null;
    if (start !== null && typeof start !== 'object') input.value = String(start);
  }
  input.id = id;
  input.dataset.type = f.type;
  input.dataset.kind = f.kind || '';
  row.appendChild(input);
  probe.fields[f.name] = input;
  const hint = el('p', 'phint');
  const bits = [f.type];
  if (f.realisable) bits.push(`realisable ${JSON.stringify(f.realisable)}`);
  if (f.probe && f.kind !== 'struct') bits.push(`borders at ${JSON.stringify(f.probe)}`);
  if (f.judgement) bits.push('a judgement a person must supply');
  hint.appendChild(el('span', 'phint-type', bits.join(', ')));
  if (f.note) hint.appendChild(el('span', 'phint-note', f.note));
  row.appendChild(hint);
  return row;
}

async function loadProbeSchema(scope) {
  probe.scope = scope;
  probe.fields = {};
  const sel = $('probescope');
  if (sel) sel.value = scope;
  const host = $('probeform');
  if (!host) return;
  host.textContent = '';
  host.appendChild(waiting('Reading the rule’s interface from the compiler…'));
  let sc;
  try {
    sc = await api('/api/exposure/schema?scope=' + encodeURIComponent(scope));
  } catch (err) {
    host.textContent = '';
    host.appendChild(notice(err.message, 'warn'));
    return;
  }
  probe.schema = sc;
  host.textContent = '';
  host.appendChild(el('p', 'probe-about',
    `${scope} decides ${sc.population || 'one case'}. It encodes ${sc.encodes.join(', ') || 'no clauses'}, and ${plural(sc.predicates.length, 'opponent’s theory', 'opponents’ theories')} watch it.`));
  const form = el('div', 'pfields');
  for (const f of sc.fields) form.appendChild(probeField(f));
  host.appendChild(form);
  if (sc.coherence.length) {
    const box = el('div', 'coherence');
    box.appendChild(el('p', 'coherence-head', 'Facts that must also hold together, or the case describes nobody'));
    for (const c of sc.coherence) box.appendChild(el('p', null, `${c.holds}: ${c.note}`));
    host.appendChild(box);
  }
  const bar = el('div', 'probe-bar');
  const go = el('button', 'btn btn-primary', 'Put it to the executor');
  go.type = 'button';
  go.onclick = () => runProbe(false);
  const rec = el('button', 'btn btn-ghost', 'Put it, and queue it if it lands');
  rec.type = 'button';
  rec.onclick = () => runProbe(true);
  add(bar, go, rec);
  host.appendChild(bar);
  host.appendChild(el('div', 'probe-out')).id = 'probeout';
  const fill = probe.fillFrom || (mapS.map && mapS.map.scope === scope && mapS.map.regions[0] ? mapS.map.regions[0].exemplar : null);
  if (fill) fillProbe(fill);
}

function fillProbe(facts) {
  for (const [name, input] of Object.entries(probe.fields)) {
    if (!(name in facts)) continue;
    const v = facts[name];
    input.value = (typeof v === 'object' && v !== null) ? JSON.stringify(v) : String(v);
  }
}

function probeValues() {
  const facts = {};
  const bad = [];
  for (const [name, input] of Object.entries(probe.fields)) {
    const raw = String(input.value).trim();
    const ty = input.dataset.type;
    if (raw === '') { bad.push(`${name} is empty, and the rule cannot run without it.`); continue; }
    if (ty === 'boolean') { facts[name] = raw === 'true'; continue; }
    if (input.dataset.kind === 'struct') {
      try { facts[name] = JSON.parse(raw); } catch (e) { bad.push(`${name}: ${e.message}`); }
      continue;
    }
    if (ty === 'integer' || ty === 'decimal' || ty === 'money') {
      const n = Number(raw);
      if (!isFinite(n)) { bad.push(`${name}: ${raw} is not a number.`); continue; }
      facts[name] = ty === 'integer' ? Math.trunc(n) : n;
      continue;
    }
    facts[name] = raw;
  }
  return { facts, bad };
}

async function runProbe(record) {
  const out = $('probeout');
  out.textContent = '';
  const { facts, bad } = probeValues();
  if (bad.length) {
    for (const b of bad) out.appendChild(notice(b, 'warn'));
    return;
  }
  out.appendChild(waiting('Executing the rule on these facts…'));
  let v;
  try {
    v = await api('/api/exposure/adjudicate', { scope: probe.scope, facts, record: !!record });
  } catch (err) {
    out.textContent = '';
    out.appendChild(notice(err.message, 'warn'));
    return;
  }
  out.textContent = '';
  const box = el('div', 'verdict ' + (v.landed ? 'is-landed' : 'is-died'));
  box.appendChild(el('p', 'verdict-word', v.landed ? 'Landed' : 'Died'));
  const body = el('div', 'verdict-body');
  if (v.landed) body.appendChild(el('p', 'verdict-klass', KLASS[v.class] || v.class));
  body.appendChild(el('p', 'verdict-why', cap(v.why)));
  if (v.predicate) body.appendChild(el('p', 'verdict-meta', `Theory ${v.predicate}`));
  if (v.amount !== null) body.appendChild(el('p', 'verdict-amount', v.amount));
  if (v.amount_basis) body.appendChild(el('p', 'verdict-meta', v.amount_basis));
  if (v.recorded) {
    body.appendChild(el('p', 'verdict-meta', `Queued as ${v.recorded}.`));
    rt.newIds.add(v.recorded);
    scheduleQueueRefresh();
  }
  if (v.already_known) body.appendChild(el('p', 'verdict-meta', 'Not queued again: this hole is already on the queue, reached from other facts.'));
  box.appendChild(body);
  out.appendChild(box);
  if (Object.keys(v.outputs || {}).length) {
    out.appendChild(el('h4', 'h4', 'What the policy computed'));
    out.appendChild(renderOutputs(v.outputs));
  }
  if (v.diagnostic) {
    out.appendChild(el('h4', 'h4', 'What the compiler said'));
    out.appendChild(el('pre', 'diag', v.diagnostic));
  }
  if ((v.governing || []).length) {
    out.appendChild(el('h4', 'h4', 'Which provision decided what'));
    out.appendChild(chain(v.governing));
  }
}

/* ---- watchers */

function buildWatchers(host) {
  host.textContent = '';
  host.appendChild(el('p', 'pane-intro',
    'The deterministic half of the engine. These run in every sweep and need no model. They are shown here in full, as they stand right now.'));
  for (const [title, fn] of [
    ['Our own operations', loadOperations],
    ['Rulings', loadHoldings],
    ['Two rules, one question', loadContradictions],
  ]) {
    const sec = el('section', 'watch');
    sec.appendChild(el('h3', 'h3', title));
    const box = el('div');
    sec.appendChild(box);
    host.appendChild(sec);
    fn(box);
  }
}

async function loadOperations(host) {
  host.appendChild(waiting('Executing the policy over every decision on record…'));
  let d;
  try { d = await api('/api/exposure/operations'); } catch (err) {
    host.textContent = ''; host.appendChild(notice(err.message, 'warn')); return;
  }
  host.textContent = '';
  host.appendChild(el('p', 'watch-lede',
    `${plural(d.decisions, 'decision')} on record. ${d.agreeing} agree with the policy as written; ${plural(d.divergences.length, 'divergence')} do not. Each is a decision this company made, executed against its own rules. A sweep puts new ones on the queue and never adds one twice.`));
  const list = el('div', 'divs');
  for (const v of d.divergences) {
    const row = el('div', 'div-row ' + (v.generosity === 'less' ? 'is-against' : 'is-for'));
    const top = el('p', 'div-top');
    add(top, el('span', 'qitem-id', v.id),
      el('span', 'div-kind', v.generosity === 'less' ? 'A claim against us'
        : v.generosity === 'more' ? 'Evidence against our own reading'
          : v.error ? 'The policy cannot answer' : 'A difference'),
      el('span', 'div-date', v.decided_on));
    add(row, top, el('p', 'div-subject', v.subject), el('p', 'div-head', v.headline), cites(v.clause_refs),
      el('p', 'div-src', `${v.scope}, from ${v.source}`));
    list.appendChild(row);
  }
  host.appendChild(list);
}

async function loadHoldings(host) {
  host.appendChild(waiting('Running every proposed ruling over the map…'));
  let d;
  try { d = await api('/api/exposure/holdings'); } catch (err) {
    host.textContent = ''; host.appendChild(notice(err.message, 'warn')); return;
  }
  host.textContent = '';
  if (!d.holdings.length) {
    host.appendChild(el('p', 'empty', 'No ruling has been formalised yet. Put one in exposure/holdings/incoming/ and run lks exposure holding with the file and the rule.'));
    return;
  }
  host.appendChild(el('p', 'watch-lede',
    'A formalised ruling is proposed, never live. Nothing is judged against it until a person moves it into exposure/predicates.yaml, but what it would do is computable now.'));
  for (const h of d.holdings) {
    const refused = h.impact.degenerate || h.impact.inverted;
    const box = el('div', 'record');
    const head = el('div', 'record-head');
    add(head, el('span', 'qitem-id', h.id), el('span', 'badge ' + (refused ? 'o-refused' : 'o-computed'),
      h.impact.inverted ? 'Contends against the claimant, so creates no exposure'
        : h.impact.degenerate ? 'Degenerate: it catches cases but changes nothing'
          : 'Proposed, not live'));
    add(box, head, el('h4', 'record-title', h.title), lawBlock(h.holding, 'law-dark'));
    box.appendChild(el('p', 'record-meta mono', `when ${h.when.join(' and ')}` +
      (h.contends.output ? `; requires ${h.contends.output} = ${h.contends.value}` : '')));
    box.appendChild(el('p', 'record-summary', h.impact.summary));
    const tbl = el('div', 'moves');
    for (const r of h.impact.regions) {
      const row = el('div', 'move');
      add(row, el('span', 'move-facts mono', `${r.id}  ${JSON.stringify(r.exemplar)}`),
        el('span', 'move-kind', `${r.cells}/${r.of} cases`),
        el('span', 'move-vals', `${r.population} on record` + (r.contends_no_change ? `, ${r.contends_no_change} already comply` : '')));
      tbl.appendChild(row);
    }
    box.appendChild(tbl);
    host.appendChild(box);
  }
}

async function loadContradictions(host) {
  host.appendChild(waiting('Executing both sides of every declared rivalry…'));
  let d;
  try { d = await api('/api/exposure/contradictions'); } catch (err) {
    host.textContent = ''; host.appendChild(notice(err.message, 'warn')); return;
  }
  host.textContent = '';
  host.appendChild(el('p', 'watch-lede',
    `${plural(d.rivalries.length, 'pair')} of rules are declared to answer the same question. Both sides are executed over the first rule’s whole grid, and any disagreement is a contradiction between two of our own documents.`));
  for (const r of d.rivalries) {
    const mine = d.contradictions.filter((c) => c.left === r.left && c.right === r.right);
    const box = el('div', 'record');
    const head = el('div', 'record-head');
    add(head, el('span', 'record-pair mono', `${r.left}  and  ${r.right}`),
      el('span', 'badge ' + (mine.length ? 'o-refused' : 'o-computed'),
        mine.length ? plural(mine.length, 'contradiction') : 'They agree across the whole grid'));
    add(box, head, lawBlock(r.question, 'law-dark'));
    for (const c of mine) add(box, el('p', 'record-summary', c.headline), el('p', 'record-meta mono', JSON.stringify(c.facts)));
    host.appendChild(box);
  }
}

/* ---- run history */

async function loadHistory(host) {
  host.textContent = '';
  host.appendChild(el('p', 'pane-intro',
    'Jobs this server has run, newest first. Runs are held in memory and forgotten on restart; the findings they produced are on disk and are not.'));
  let d;
  try { d = await api('/api/exposure/jobs'); } catch (err) {
    host.appendChild(notice(err.message, 'warn')); return;
  }
  if (!d.jobs.length) {
    host.appendChild(el('p', 'empty', 'Nothing has run since the server started.'));
    return;
  }
  const list = el('div', 'history');
  for (const j of d.jobs) {
    const row = el('div', 'hist s-' + j.status);
    const r = j.result || {};
    let what = '';
    if (j.kind === 'sweep') what = `${plural((r.found || []).length, 'new finding')} from the fleet, ${(r.recorded || []).length} from operations, ${(r.stale || []).length} no longer reproducing`;
    else if (j.kind === 'fleet') what = `${plural((r.found || []).length, 'new finding')} from ${plural(r.rounds || 0, 'attack')}`;
    else if (j.kind === 'letter') what = r.id ? `Letter drafted for ${r.id}` : '';
    else if (j.kind === 'close') what = r.id ? `Fix measured for ${r.id}: ${r.ok ? 'it holds' : 'it breaks something'}` : '';
    else if (j.kind === 'generate') what = r.name ? (r.passed ? `Issued: ${r.title || r.name}` : `Not issued: failed at ${r.failure_stage || 'an unrecorded stage'}`) : '';
    if (j.status === 'failed') what = j.error;
    if (j.status === 'running') what = 'Running now';
    const main = el('div', 'hist-main');
    add(main, el('p', 'hist-label', cap(j.label)), el('p', 'hist-what', what));
    const meta = el('p', 'hist-meta', `${ago(j.started)}, ${fmtDur(j.seconds)}`);
    const show = el('button', 'btn btn-ghost btn-s', rt.job && rt.job.id === j.id ? 'On the engine' : 'Show on the engine');
    show.type = 'button';
    show.disabled = !!(rt.job && rt.job.id === j.id);
    show.onclick = async () => {
      await attach(j.id);
      if (j.kind === 'generate') showView('generate');
      else $('engine').scrollIntoView({ behavior: 'smooth' });
      loadHistory(host);
    };
    add(row, main, meta, show);
    list.appendChild(row);
  }
  host.appendChild(list);
}

/* ============================================================= generate

   The same shape of job as a sweep: one background run on the local model,
   emitting a stage event as each gate opens and closes, drawn in the order
   they arrive because a blocked screen loops back to a redraft. The rule that
   matters here is the system's own: a run that did not pass every gate is not
   a document. It is shown as not issued, and a PDF rendered from it is offered
   only as a rejected draft. */

const GEN_STAGES = [
  ['Retrieve precedent', 'Finds the clauses and documents in the corpus closest to the request, to draft from.'],
  ['Draft the document', 'The local model writes the document and the Catala that encodes it.'],
  ['Catala gates, G1 to G4', 'The encoding must compile and pass every executed check. A failure goes back to the drafter with the compiler’s own words.'],
  ['Review screen', 'Three reviewers attack the draft’s language, its consistency with the corpus, and its logic. A confirmed finding sends it back for a redraft.'],
  ['Roundtrip, G5', 'An agent that never saw the encoding writes it again from the English, and the two must behave the same.'],
  ['Issue the PDF', 'Only a draft that passed every gate is issued.'],
];

function buildGenerate() {
  if (!ui.genBuilt) {
    ui.genBuilt = true;
    $('genform').addEventListener('submit', startGenerate);
  }
  renderGenLive();
  renderGenForm();
  loadGenRuns();
}

function renderGenForm() {
  const btn = $('genbtn');
  if (!btn) return;
  const busy = isRunning();
  btn.disabled = busy;
  $('genhint').textContent = busy
    ? (rt.job.kind === 'generate'
      ? 'A draft is in progress. Its stages are below.'
      : 'Another job is using the local model. Drafting can start when it finishes.')
    : 'A run takes 20 to 60 minutes on the local model. You can leave this page; the run carries on, and this view picks it up again.';
}

async function startGenerate(ev) {
  ev.preventDefault();
  const err = $('generror');
  err.textContent = '';
  const request = $('genrequest').value.trim();
  if (request.length < 12) {
    err.textContent = 'Describe the document in a sentence or more.';
    return;
  }
  const num = (id, d) => Number($(id).value) || d;
  const body = {
    request,
    catala_attempts: num('genattempts', 3),
    screen_rounds: num('genrounds', 2),
    no_roundtrip: $('genskiprt').checked,
    emit_failed_pdf: $('genfailedpdf').checked,
  };
  if ($('gendate').value) body.effective_date = $('gendate').value;
  try {
    const j = await api('/api/generate', body);
    await attach(j.id);
    renderPulse();
    setTimeout(loop, 1200);
    $('genlive').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    err.textContent = e.message;
  }
}

function genStageLi(title, status, s, desc) {
  const li = el('li', 'stage s-' + status);
  li.appendChild(el('span', 'stage-mark'));
  const body = el('div', 'stage-body');
  body.appendChild(el('h3', 'stage-title', title));
  const meta = el('p', 'stage-meta');
  const took = s ? (s.endT || 0) - (s.startT || 0) : 0;
  if (status === 'idle') meta.textContent = desc;
  else if (status === 'running') add(meta, 'Running for ', clock(s.startT));
  else if (status === 'failed') meta.textContent = `Failed after ${fmtDur(took)}`;
  else meta.textContent = took < 1 ? 'Done in under a second' : `Done in ${fmtDur(took)}`;
  body.appendChild(meta);
  if (s && s.summary) body.appendChild(el('p', 'stage-summary', s.summary));
  li.appendChild(body);
  return li;
}

function genVerdict(passed, title, stage, reason, name, hasPdf) {
  const box = el('div', 'gen-verdict ' + (passed ? 'is-issued' : 'is-not'));
  box.appendChild(el('p', 'gen-verdict-word', passed ? 'Issued' : 'Not issued'));
  const body = el('div', 'gen-verdict-body');
  body.appendChild(el('p', 'gen-verdict-title', passed ? (title || name) : (stage ? `Failed at ${stage}` : 'The run did not finish')));
  if (!passed && reason) body.appendChild(el('p', 'gen-verdict-reason', reason));
  if (hasPdf) {
    const a = el('a', 'btn ' + (passed ? 'btn-primary' : 'btn-ghost'),
      passed ? 'Open the PDF' : 'Open the rejected draft, stamped as failing');
    a.href = '/api/generate/pdf?name=' + encodeURIComponent(name);
    a.target = '_blank';
    a.rel = 'noopener';
    body.appendChild(a);
  }
  box.appendChild(body);
  return box;
}

function renderGenLive(m) {
  const host = $('genlive');
  if (!host) return;
  host.textContent = '';
  const j = rt.job && rt.job.kind === 'generate' ? rt.job : null;
  if (!j) {
    host.appendChild(el('h2', 'loop-head', 'How a request becomes an issued document'));
    const ol = el('ol', 'loop');
    for (const [t, d] of GEN_STAGES) ol.appendChild(genStageLi(t, 'idle', null, d));
    host.appendChild(ol);
    return;
  }
  m = m || runModel();
  const head = el('div', 'genlive-head');
  const meta = el('p', 'loop-head');
  if (j.status === 'running') add(meta, 'Running for ', clock(0));
  else meta.textContent = `Finished ${ago(j.started + j.seconds)}, after ${fmtDur(j.seconds)}`;
  add(head, el('h2', 'h3 genlive-title', j.status === 'running' ? 'Drafting now' : 'The latest draft'), meta);
  host.appendChild(head);
  const req = (j.params || {}).request;
  if (req) host.appendChild(el('blockquote', 'now-claim gen-request', req + (req.length >= 200 ? '…' : '')));
  const ol = el('ol', 'loop');
  for (const s of m.stages.values()) ol.appendChild(genStageLi(s.title, s.status, s));
  host.appendChild(ol);
  if (j.status === 'running' && !m.stages.size) host.appendChild(waiting('Starting the pipeline…'));
  if (j.status === 'failed') host.appendChild(notice(j.error, 'warn'));
  if (j.status === 'done' && j.result) {
    const r = j.result;
    host.appendChild(genVerdict(r.passed, r.title, r.failure_stage, r.failure_reason, r.name, !!r.pdf_url));
    const open = el('button', 'btn btn-ghost btn-s', 'Read the full record of this run');
    open.type = 'button';
    open.onclick = () => openGenRun(r.name);
    host.appendChild(open);
  }
  if (rt.lines.length) {
    const det = el('details', 'transcript');
    det.open = !!ui.genTranscriptOpen;
    det.addEventListener('toggle', () => { ui.genTranscriptOpen = det.open; });
    det.appendChild(el('summary', null, `Transcript, ${plural(rt.lines.length, 'line')}`));
    det.appendChild(el('pre', 'diag', rt.lines.slice(-300).join('\n')));
    host.appendChild(det);
  }
}

async function loadGenRuns() {
  const host = $('genruns');
  if (!host) return;
  let d;
  try {
    d = await api('/api/generate/runs');
  } catch (e) {
    host.textContent = '';
    host.appendChild(notice(e.message, 'warn'));
    return;
  }
  host.textContent = '';
  if (!d.runs.length) {
    host.appendChild(el('p', 'empty', 'Nothing has been drafted yet.'));
    return;
  }
  const liveName = isRunning() && rt.job.kind === 'generate' ? (rt.job.progress || {}).name : null;
  for (const r of d.runs) {
    const b = el('button', 'genrun' + (ui.genRun === r.name ? ' is-sel' : ''));
    b.type = 'button';
    const top = el('span', 'genrun-top');
    const word = !r.finished ? (r.name === liveName ? 'Drafting now' : 'Unfinished')
      : r.passed ? 'Issued' : 'Not issued';
    add(top, el('span', 'badge ' + (!r.finished ? '' : r.passed ? 'is-issued' : 'is-not'), word),
      el('span', 'genrun-when', ago(r.modified)));
    b.appendChild(top);
    b.appendChild(el('span', 'genrun-title', r.title || r.request || r.name));
    b.appendChild(r.finished
      ? el('span', 'genrun-meta', r.passed
        ? `${r.doc_id}, ${fmtDur(r.seconds)}`
        : `Failed at ${r.failure_stage || 'an unrecorded stage'}, after ${fmtDur(r.seconds)}`)
      : el('span', 'genrun-meta mono', r.name));
    b.disabled = !r.finished;
    b.onclick = () => openGenRun(r.name);
    host.appendChild(b);
  }
}

async function openGenRun(name) {
  ui.genRun = name;
  loadGenRuns();
  const host = $('gendetail');
  host.textContent = '';
  host.appendChild(waiting('Reading the run’s record…'));
  host.scrollIntoView({ behavior: 'smooth', block: 'start' });
  let d;
  try {
    d = await api('/api/generate/run?name=' + encodeURIComponent(name));
  } catch (e) {
    host.textContent = '';
    host.appendChild(notice(e.message, 'warn'));
    return;
  }
  const r = d.run;
  const s = d.summary || {};
  host.textContent = '';
  add(host, el('p', 'gen-id', name), el('h2', 'h2 gen-title', r.title || 'Untitled draft'),
    el('p', 'gen-meta', [r.doc_id, r.model && `drafted by ${r.model}`,
      `${plural(r.catala_iterations || 0, 'encoding')} attempted`,
      plural(r.screen_rounds || 0, 'screen round'), fmtDur(r.seconds || 0)].filter(Boolean).join(', ')),
    genVerdict(r.passed, r.title, r.failure_stage, r.failure_reason, name, !!s.has_pdf));

  host.appendChild(el('h3', 'h3', 'The request'));
  host.appendChild(el('blockquote', 'now-claim gen-request', r.prompt));

  const gates = [...((r.gates && r.gates.gates) || []), ...(r.g5 ? [r.g5] : [])];
  host.appendChild(el('h3', 'h3', 'Gates'));
  if (!gates.length) {
    host.appendChild(el('p', 'empty', 'No gate ran on this draft.'));
  } else {
    const wrap = el('div', 'table-wrap');
    const t = el('table', 'gates');
    const hr = t.appendChild(el('thead')).appendChild(el('tr'));
    for (const h of ['Gate', 'Result', 'Detail']) hr.appendChild(el('th', null, h));
    const tb = t.appendChild(el('tbody'));
    for (const g of gates) {
      const tr = el('tr', g.skipped ? 'is-skip' : g.ok ? 'is-pass' : 'is-fail');
      add(tr.appendChild(el('td')), el('span', 'mono', g.id), ` ${g.name}`);
      tr.appendChild(el('td', 'gate-state', g.skipped ? 'Skipped' : g.ok ? 'Passed' : 'Failed'));
      tr.appendChild(el('td', null, g.detail || ''));
      tb.appendChild(tr);
    }
    wrap.appendChild(t);
    host.appendChild(wrap);
  }

  (r.screens || []).forEach((sc, i) => {
    const hd = el('div', 'screen-head');
    add(hd, el('h3', 'h3', `Review screen, round ${i + 1}`),
      el('span', 'badge ' + (sc.passed ? 'o-computed' : 'o-refused'), sc.verdict));
    host.appendChild(hd);
    host.appendChild(el('p', 'gen-agents', (sc.agents || []).map((a) =>
      `${a.agent}: ${a.ok ? plural(a.findings.length, 'finding') : 'unusable, ' + a.error} (${a.enforcement}, ${fmtDur(a.seconds)})`).join('; ')));
    for (const [key, label] of [['confirmed', 'Confirmed by execution'], ['agreed', 'Agreed by more than one reviewer'],
      ['open_questions', 'Judgement calls left open'], ['discarded', 'Discarded']]) {
      const fs = sc[key] || [];
      if (!fs.length) continue;
      const g = el('details', 'fgroup fg-' + key);
      g.open = key !== 'discarded';
      g.appendChild(el('summary', null, `${label}, ${fs.length}`));
      for (const f of fs) {
        const row = el('div', 'finding');
        const top = el('p', 'finding-top');
        add(top, el('span', 'finding-kind', String(f.kind).replace(/_/g, ' ').toLowerCase()),
          el('span', 'mono', (f.clause_ids || []).join(', ')),
          el('span', null, `from ${f.agent}` + ((f.agreed_with || []).length ? `, agreed by ${f.agreed_with.join(', ')}` : '')));
        row.appendChild(top);
        row.appendChild(el('p', 'finding-summary', f.summary));
        if (f.quote) row.appendChild(el('p', 'finding-quote', `“${f.quote}”`));
        if (f.verified_by) row.appendChild(el('p', 'finding-meta', `Verified: ${f.verified_by}`));
        if (f.discard_reason) row.appendChild(el('p', 'finding-meta', `Discarded: ${f.discard_reason}`));
        if (f.fix) row.appendChild(el('p', 'finding-meta', `Suggested fix: ${f.fix}`));
        g.appendChild(row);
      }
      host.appendChild(g);
    }
  });

  if ((r.context || []).length) {
    host.appendChild(el('h3', 'h3', 'Precedent it drafted from'));
    const list = el('div', 'moves');
    for (const c of r.context) {
      const row = el('div', 'move');
      add(row, el('span', 'mono', c.ref), el('span', 'move-kind', c.kind),
        el('span', 'move-vals', c.score === null || c.score === undefined ? '' : `score ${Number(c.score).toFixed(3)}`));
      list.appendChild(row);
    }
    host.appendChild(list);
  }
  if (d.report) {
    const det = el('details', 'transcript');
    det.appendChild(el('summary', null, 'The full report'));
    det.appendChild(el('pre', 'diag report-md', d.report));
    host.appendChild(det);
  }
}

/* ================================================================== ask */

const ENGINE_WORD = { CATALA: 'Computed', VECTOR: 'Quoted', NONE: 'Not covered' };
const KIND_WORD = {
  computed: 'by executing a rule',
  'needs-input': 'a rule can answer this, given facts',
  'ambiguous-route': 'more than one rule matches',
  quotation: 'verbatim from the corpus',
  caveat: 'qualifies the rule above',
  refused: 'these facts cannot arise',
  error: 'the rules do not resolve it',
  'no-coverage': 'nothing in the corpus answers this',
};

function renderOutputs(outputs) {
  const grid = el('div', 'results');
  for (const [k, v] of Object.entries(outputs)) {
    const cell = el('div', 'result');
    cell.appendChild(el('p', 'result-name', k.replace(/_/g, ' ')));
    cell.appendChild(el('p', 'result-value' + (v === true || v === false ? ' is-flag' : ''),
      v === true ? 'yes' : v === false ? 'no' : String(v)));
    grid.appendChild(cell);
  }
  return grid;
}

function partBox(engine, kind) {
  const box = el('div', `part e-${String(engine).toLowerCase()} k-${kind}`);
  const head = el('div', 'part-head');
  add(head, el('span', 'mark', ''), el('span', 'part-engine', ENGINE_WORD[engine] || engine),
    el('span', 'part-kind', KIND_WORD[kind] || kind));
  box.appendChild(head);
  return box;
}

$('askform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('askfield').value.trim();
  if (!q) return;
  const out = $('answer');
  $('askbtn').disabled = true;
  out.textContent = '';
  const w = waiting('Routing the question, then executing or quoting: ');
  const t0 = performance.now();
  const c = el('span', 'plainclock', '0s');
  w.appendChild(c);
  const iv = setInterval(() => { c.textContent = fmtDur((performance.now() - t0) / 1000); }, 500);
  out.appendChild(w);
  try {
    renderAnswer(await api('/api/ask', { question: q }));
  } catch (err) {
    out.textContent = '';
    out.appendChild(notice(err.message, 'warn'));
  } finally {
    clearInterval(iv);
    $('askbtn').disabled = false;
  }
});

function renderAnswer(a) {
  const out = $('answer');
  out.textContent = '';
  for (const p of a.parts) {
    const box = partBox(p.engine, p.kind);
    if (p.kind === 'computed' && p.outputs) {
      box.appendChild(renderOutputs(p.outputs));
      box.appendChild(el('p', 'part-note', `Executed ${p.scope} on the facts given.`));
    } else if (p.kind === 'quotation') {
      box.appendChild(renderLaw(p.text.replace(/^[“"]|[”"]$/g, ''), el('blockquote', 'law quote')));
    } else if (p.kind === 'caveat') {
      const lead = p.text.split('\n')[0];
      const quoted = p.text.slice(lead.length).replace(/^[\s“"]+|[\s”"]+$/g, '');
      box.appendChild(el('p', 'part-text', lead));
      box.appendChild(renderLaw(quoted, el('blockquote', 'law quote')));
    } else {
      box.appendChild(el('p', 'part-text', p.text));
    }
    if ((p.citations || []).length) box.appendChild(cites(p.citations));
    if (p.scope && (p.kind === 'needs-input' || p.kind === 'computed')) {
      const go = el('button', 'btn btn-ghost btn-s', 'Open this rule');
      go.type = 'button';
      go.onclick = () => selectScope(p.scope);
      box.appendChild(go);
    }
    out.appendChild(box);
  }
  if (a.engines.length > 1) {
    out.appendChild(el('p', 'blend', 'This answer used both engines. The computed parts came from running the named rule; the quoted parts are the documents’ own words. They are kept apart deliberately.'));
  }
}

const EXAMPLES = [
  ['How much service credit do we owe at 98.5% uptime?', 'Computed from the credit table'],
  ['What is the overtime rate beyond 48 hours?', 'Computed, once you give the hours'],
  ['Who decides commission disputes?', 'Quoted, because the plan says so in words'],
  ['How long do we keep candidate records?', 'Computed from the retention periods'],
  ['What is the sole remedy for a service level failure?', 'Quoted from the agreement'],
  ['Can the General Counsel stop a record being deleted?', 'More than one rule bears on it'],
];

function renderAskEmpty() {
  const out = $('answer');
  if (out.childElementCount) return;
  const legend = el('div', 'engines');
  for (const [cls, name, body] of [
    ['e-catala', 'Computed', 'A figure produced by running the rule the clauses encode. Never a paraphrase of the code.'],
    ['e-vector', 'Quoted', 'The documents’ own words, with the clause they came from. Never a computation.'],
  ]) {
    const b = el('div', 'engine-card ' + cls);
    add(b, el('span', 'mark'), el('h3', 'engine-name', name), el('p', 'engine-body', body));
    legend.appendChild(b);
  }
  out.appendChild(legend);
  out.appendChild(el('h2', 'h3', 'Questions this corpus can answer'));
  const list = el('div', 'examples');
  for (const [q, why] of EXAMPLES) {
    const b = el('button', 'example');
    b.type = 'button';
    add(b, el('span', 'example-q', q), el('span', 'example-why', why));
    b.onclick = () => { $('askfield').value = q; $('askform').requestSubmit(); };
    list.appendChild(b);
  }
  out.appendChild(list);
}

/* ================================================================ rules */

async function loadScopes() {
  const host = $('rulesbody');
  host.textContent = '';
  host.appendChild(waiting('Reading the rule registry…'));
  try {
    state.scopes = (await api('/api/scopes')).scopes;
  } catch (err) {
    host.textContent = '';
    host.appendChild(notice(err.message, 'warn'));
    return;
  }
  renderScopeList();
}

function renderScopeList() {
  const host = $('rulesbody');
  host.textContent = '';
  const mapped = new Set(rt.scopes.map((s) => s.key));
  const list = el('div', 'scopelist');
  for (const s of state.scopes) {
    const row = el('button', 'scoperow');
    row.type = 'button';
    add(row, el('span', 'scoperow-name', s.key),
      el('span', 'scoperow-io', `${s.encodes.length ? plural(s.encodes.length, 'clause') + ', ' : ''}${plural(s.inputs.length, 'fact')} in, ${plural(s.outputs.length, 'result')} out`),
      el('span', 'scoperow-flags', [
        s.judgement_inputs.length ? plural(s.judgement_inputs.length, 'judgement') + ' required' : '',
        mapped.has(s.key) ? 'watched by the exposure engine' : '',
      ].filter(Boolean).join('; ')));
    row.onclick = () => selectScope(s.key);
    list.appendChild(row);
  }
  host.appendChild(list);
}

async function selectScope(key) {
  showView('rules');
  const host = $('rulesbody');
  host.textContent = '';
  host.appendChild(waiting('Asking the compiler what this rule needs…'));
  let s;
  try {
    s = await api('/api/scope?key=' + encodeURIComponent(key));
  } catch (err) {
    host.textContent = '';
    host.appendChild(notice(err.message, 'warn'));
    return;
  }
  state.scope = s;
  host.textContent = '';
  const back = el('button', 'btn btn-ghost btn-s', 'All rules');
  back.type = 'button';
  back.onclick = () => (state.scopes.length ? renderScopeList() : loadScopes());
  host.appendChild(back);
  host.appendChild(el('h2', 'h2 scope-title', s.key));
  if (s.clauses.length) host.appendChild(cites(s.clauses.map((c) => c.ref)));

  host.appendChild(el('h3', 'h3', 'The facts this rule needs'));
  const fill = el('form', 'fillrow');
  const fillq = el('input', 'field');
  fillq.placeholder = 'Or describe the situation, and an agent fills in the facts for you to check';
  fillq.setAttribute('aria-label', 'Describe the situation');
  const fillbtn = el('button', 'btn btn-ghost', 'Fill from a description');
  fillbtn.type = 'submit';
  add(fill, fillq, fillbtn);
  const fillnote = el('p', 'hint');
  fill.onsubmit = async (ev) => {
    ev.preventDefault();
    const q = fillq.value.trim();
    if (!q) return;
    fillbtn.disabled = true;
    fillnote.textContent = '';
    fillnote.appendChild(waiting('The slot-filling agent is reading the description…'));
    try {
      const r = await api('/api/slotfill', { target: s.key, question: q });
      if (r.error) { fillnote.textContent = r.error; return; }
      for (const [k, v] of Object.entries(r.facts)) {
        const node = $('fact-' + k);
        if (node) { node.value = String(v); node.classList.add('was-filled'); }
      }
      const n = Object.keys(r.facts).length;
      fillnote.textContent = `The agent filled ${plural(n, 'fact')}, marked in brass.` +
        (r.omitted.length ? ` The description does not state ${r.omitted.join(', ')}, so those are left for you rather than guessed.` : '') +
        ' Check every value before running. Nothing has been executed.';
    } catch (err) {
      fillnote.textContent = err.message;
    } finally {
      fillbtn.disabled = false;
    }
  };
  add(host, fill, fillnote);

  const grid = el('div', 'factgrid');
  const judge = new Set(s.judgement_inputs);
  for (const name of s.inputs) {
    const ty = s.input_schema[name] || 'unknown';
    const f = el('div', 'fact' + (judge.has(name) ? ' is-judgement' : ''));
    const lab = el('label', 'fact-name', name.replace(/_/g, ' '));
    lab.htmlFor = 'fact-' + name;
    add(f, lab, el('span', 'fact-type', ty + (judge.has(name) ? ', a judgement' : '')));
    let input;
    if (ty === 'boolean') {
      input = el('select', 'field');
      for (const v of ['false', 'true']) input.appendChild(el('option', null, v));
    } else {
      input = el('input', 'field');
      input.placeholder = ty === 'date' ? 'YYYY-MM-DD' : ty;
    }
    input.id = 'fact-' + name;
    input.dataset.type = ty;
    input.addEventListener('input', () => input.classList.remove('was-filled'));
    f.appendChild(input);
    grid.appendChild(f);
  }
  host.appendChild(grid);
  if (s.judgement_inputs.length) {
    host.appendChild(el('p', 'hint', 'The judgement fields are marked. The documents do not define them, so nothing in this system decides them for you. The rule computes what follows once a person has.'));
  }
  const run = el('button', 'btn btn-primary', 'Run this rule');
  run.type = 'button';
  run.onclick = () => runScope(s);
  host.appendChild(run);
  host.appendChild(el('div', 'runout')).id = 'runout';
}

async function runScope(s) {
  const out = $('runout');
  out.textContent = '';
  const facts = {};
  const missing = [];
  for (const name of s.inputs) {
    const node = $('fact-' + name);
    const ty = node.dataset.type;
    let v = node.value.trim();
    if (v === '') { missing.push(name); continue; }
    if (ty === 'boolean') v = v === 'true';
    else if (ty === 'integer') v = parseInt(v, 10);
    else if (ty === 'decimal' || ty === 'money') v = parseFloat(v);
    facts[name] = v;
  }
  if (missing.length) {
    out.appendChild(notice(`Fill in every fact before running. Still needed: ${missing.join(', ')}. Nothing is guessed.`, 'warn'));
    return;
  }
  out.appendChild(waiting('Executing…'));
  let r;
  try {
    r = await api('/api/explain', { target: s.key, inputs: facts });
  } catch (err) {
    out.textContent = '';
    out.appendChild(notice(err.message, 'warn'));
    return;
  }
  out.textContent = '';
  if (r.error) {
    const word = {
      AssertionFailed: 'These facts cannot arise under the documents, so the rule declines to answer rather than computing from an impossible premise.',
      ScopeConflict: 'Two provisions apply to these facts and the documents establish no priority between them. This is a gap in the source, and it needs a person.',
      NoApplicableRule: 'No provision applies to these facts, so the documents do not determine an answer.',
    }[r.error.kind] || 'The rule could not be executed.';
    const box = partBox('NONE', 'error');
    add(box, el('p', 'part-text', word), el('pre', 'diag', r.error.diagnostic));
    out.appendChild(box);
    return;
  }
  const box = partBox('CATALA', 'computed');
  add(box, renderOutputs(r.outputs), el('p', 'part-note', `Executed ${s.key} on the facts you gave.`));
  out.appendChild(box);
  for (const h of r.hierarchies) {
    if (h.n_nodes < 2) continue;
    out.appendChild(el('h3', 'h3', `How ${h.variable.replace(/_/g, ' ')} was decided`));
    out.appendChild(renderLadder(h));
  }
}

/* The defeasance ladder: each rung sits under and overrides the one above,
   and the rung the interpreter actually took is marked from its own trace. */
function renderLadder(h) {
  const wrap = el('div', 'ladder');
  const walk = (n, depth) => {
    const rung = el('div', 'rung' + (n.governs ? ' is-governing' : ''));
    rung.style.setProperty('--depth', depth);
    const mid = el('div', 'rung-body');
    mid.appendChild(el('span', 'rung-label', n.label));
    const cond = el('span', 'rung-cond');
    if (n.conditions.length === 1 && n.conditions[0] === '<unconditional>') cond.textContent = 'applies unless something below it does';
    else add(cond, 'when ', el('code', null, n.conditions.join(' / ')));
    mid.appendChild(cond);
    rung.appendChild(mid);
    if (n.governs) rung.appendChild(el('span', 'rung-flag', 'Governed'));
    wrap.appendChild(rung);
    for (const c of n.exceptions) walk(c, depth + 1);
  };
  for (const t of h.trees) walk(t, 0);
  wrap.appendChild(el('p', 'ladder-note', h.governing_line
    ? `Marked from the interpreter’s own trace: the definition applied was at line ${h.governing_line}` +
      (h.governing_headings.length ? `, under “${h.governing_headings[0]}”` : '') +
      '. The interface does not re-decide which provision governs.'
    : 'The trace did not report a definition for this result.'));
  return wrap;
}

/* ============================================================ documents */

function renderDocuments() {
  const host = $('docsbody');
  host.textContent = '';
  if (state.meta) {
    const m = state.meta;
    host.appendChild(el('p', 'docs-meta',
      `${plural(state.docs.length, 'document')}, ${plural(m.n_modules, 'rule module')} with ${plural(m.n_scopes, 'rule')}, and ${plural(m.vector_store.n_chunks, 'clause')} indexed for quotation.`));
  }
  if (!state.docs.length) {
    host.appendChild(waiting('Reading the corpus…'));
    return;
  }
  for (const d of state.docs) {
    const open = state.open.has(d.doc_id);
    const art = el('article', 'doc' + (open ? ' is-open' : ''));
    const t = el('button', 'doc-toggle');
    t.type = 'button';
    t.setAttribute('aria-expanded', String(open));
    const shaft = el('span', 'doc-shaft');
    shaft.setAttribute('aria-hidden', 'true');
    for (const [cls, n] of [['s-rule', d.labels.RULE], ['s-hybrid', d.labels.HYBRID], ['s-prose', d.labels.PROSE]]) {
      if (!n) continue;
      const seg = el('span', 'doc-seg ' + cls);
      seg.style.flexGrow = String(n);
      shaft.appendChild(seg);
    }
    const body = el('span', 'doc-body');
    add(body, el('span', 'doc-title', d.title),
      el('span', 'doc-meta', `${d.doc_id}, version ${d.version}, effective ${d.effective_date}, owned by ${d.owner}`),
      el('span', 'doc-tally', `${d.labels.RULE} run as rules, ${d.labels.HYBRID} gated on a judgement, ${d.labels.PROSE} quoted only`));
    add(t, shaft, body);
    t.onclick = () => {
      if (open) state.open.delete(d.doc_id); else state.open.add(d.doc_id);
      renderDocuments();
    };
    art.appendChild(t);
    if (open) {
      const secs = el('div', 'doc-sections');
      for (const s of d.sections) {
        const sec = el('div', 'doc-section');
        sec.appendChild(el('h3', 'doc-sectitle', `${s.section_id} ${s.title}`));
        const chips = el('div', 'chips');
        for (const c of s.clauses) {
          const chip = el('button', 'chip is-' + c.label.toLowerCase(), c.clause_id);
          chip.type = 'button';
          chip.title = c.label === 'RULE' ? `Runs as a rule in ${c.module}` : c.label === 'HYBRID' ? `A rule gated on a judgement, in ${c.module}` : 'Quoted prose';
          chip.onclick = () => openClause(c.ref);
          chips.appendChild(chip);
        }
        sec.appendChild(chips);
        secs.appendChild(sec);
      }
      art.appendChild(secs);
    }
    host.appendChild(art);
  }
}

/* ============================================================== drawer */

async function openClause(ref) {
  const body = $('drawerbody');
  body.textContent = '';
  body.appendChild(waiting('Fetching the clause…'));
  $('drawer').hidden = false;
  $('drawerclose').focus();
  let c;
  try {
    c = await api('/api/clause?ref=' + encodeURIComponent(ref));
  } catch (err) {
    body.textContent = '';
    body.appendChild(notice(err.message, 'warn'));
    return;
  }
  body.textContent = '';
  add(body, el('p', 'drawer-ref', c.ref), el('h2', 'drawer-title', `${c.section_id} ${c.section_title}`),
    el('p', 'drawer-doc', `${c.doc_title}, version ${c.version}, effective ${c.effective_date}`),
    lawBlock(c.body, 'law-sheet'));
  body.appendChild(kv([
    ['Triaged as', c.label === 'RULE' ? 'An executable rule' : c.label === 'HYBRID' ? 'A rule gated on a judgement' : 'Quoted prose'],
    ['Executed by', c.encoded_by.join(', '), true],
    ['Qualifies', c.qualifies.join(', '), true],
    ['A person must decide', c.judgement_inputs.join(', '), true],
    ['Source', `${c.file}, line ${c.line}`, true],
    ['Content hash', c.hash, true],
  ]));
  if (c.encoded_by.length) {
    const b = el('button', 'btn btn-ink', 'Open the rule that encodes this');
    b.type = 'button';
    b.onclick = () => { $('drawer').hidden = true; selectScope(c.encoded_by[0]); };
    body.appendChild(b);
  }
}

/* ============================================================== intake */

async function loadIntake() {
  const host = $('intakebody');
  host.textContent = '';
  host.appendChild(waiting('Reading proposals…'));
  let d;
  try { d = await api('/api/proposals'); } catch (err) {
    host.textContent = ''; host.appendChild(notice(err.message, 'warn')); return;
  }
  host.textContent = '';
  if (!d.proposals.length) {
    host.appendChild(el('p', 'empty', 'No documents are waiting. Run lks ingest with a file to put one here.'));
    return;
  }
  for (const p of d.proposals) {
    const blocking = p.conflicts.filter((c) => c.severity === 'blocking' && !c.resolved).length;
    const sec = el('section', 'proposal');
    add(sec, el('h2', 'h2', p.title || p.doc_id),
      el('p', 'proposal-meta', `${p.id}, created ${p.created}. ` + (p.mergeable
        ? 'Every conflict is resolved, so this document is ready to merge.'
        : `${plural(blocking, 'blocking conflict')} still need a person to resolve ${blocking === 1 ? 'it' : 'them'}.`)));
    for (const [i, c] of p.conflicts.entries()) {
      const box = el('div', 'record' + (c.resolved ? ' is-resolved' : ''));
      const head = el('div', 'record-head');
      add(head, el('span', 'record-kind', c.kind.replace(/_/g, ' ').toLowerCase()),
        el('span', 'qitem-id', c.incoming_ref),
        el('span', 'badge ' + (c.resolved ? 'o-computed' : c.severity === 'advisory' ? 'o-silent' : 'o-refused'),
          c.resolved ? 'Resolved' : c.severity === 'advisory' ? 'Advisory' : 'Blocking'));
      add(box, head, el('p', 'record-summary', c.detail));
      if (c.resolved) {
        box.appendChild(el('p', 'record-meta', `Resolved by ${c.resolved_by}: ${c.resolution}`));
      } else if (c.severity === 'blocking') {
        const form = el('form', 'resolve');
        const txt = el('input', 'field');
        txt.placeholder = 'How is this resolved?';
        txt.setAttribute('aria-label', 'Resolution');
        const who = el('input', 'field');
        who.placeholder = 'Your name';
        who.setAttribute('aria-label', 'Your name');
        const btn = el('button', 'btn', 'Record the resolution');
        btn.type = 'submit';
        form.onsubmit = async (ev) => {
          ev.preventDefault();
          if (!txt.value.trim() || !who.value.trim()) return;
          btn.disabled = true;
          try {
            await api('/api/resolve', { id: p.id, index: i, resolution: txt.value.trim(), resolved_by: who.value.trim() });
            loadIntake();
          } catch (err) {
            box.appendChild(notice(err.message, 'warn'));
            btn.disabled = false;
          }
        };
        add(form, txt, who, btn);
        box.appendChild(form);
      }
      sec.appendChild(box);
    }
    host.appendChild(sec);
  }
}

/* ============================================================== checks */

async function loadChecks() {
  const host = $('checksbody');
  host.textContent = '';
  host.appendChild(waiting('Reading the record…'));
  let d;
  try { d = await api('/api/verification'); } catch (err) {
    host.textContent = ''; host.appendChild(notice(err.message, 'warn')); return;
  }
  host.textContent = '';
  const facts = el('dl', 'tally');
  const t = (num, label, warn) => {
    const div = el('div', 'tally-item' + (warn ? ' is-warn' : ''));
    add(div, el('dt', null, label), el('dd', null, String(num)));
    facts.appendChild(div);
  };
  t(`${d.coverage.encoded} of ${d.coverage.required}`, 'Rule clauses encoded', d.coverage.missing.length > 0);
  t(d.summary.total, 'Defects found and kept as tests');
  t(d.summary.open, 'Still failing', d.summary.open > 0);
  t(d.document_defects.length, 'Defects in the documents themselves');
  host.appendChild(facts);

  host.appendChild(el('h2', 'h2', 'Defects found in this system'));
  host.appendChild(el('p', 'watch-lede', 'Each was found by a reviewer that saw the source clause and the compiled rule but never the reasoning behind it. Each is run again on every check.'));
  for (const c of d.counterexamples) {
    const box = el('div', 'record');
    const head = el('div', 'record-head');
    add(head, el('span', 'qitem-id', c.id), el('span', 'badge ' + (c.status === 'fixed' ? 'o-computed' : 'o-refused'),
      c.status === 'fixed' ? 'Fixed and run again' : 'Still failing'));
    add(head, el('span', 'now-agent', c.rule));
    add(box, head, el('h3', 'record-title', c.headline || c.plain), el('p', 'watch-lede', c.plain));
    if (c.why_it_matters) box.appendChild(el('p', 'watch-lede', `Why it matters, in the reviewer’s words: ${c.why_it_matters}`));
    add(box, lawBlock(c.fact_pattern, 'law-dark'), lawBlock(c.source_reasoning, 'law-dark law-quiet'), cites(c.citations));
    host.appendChild(box);
  }
  if (d.document_defects.length) {
    host.appendChild(el('h2', 'h2', 'Defects in the documents'));
    host.appendChild(el('p', 'watch-lede', 'These need amending rather than fixing. No encoding can be right where the text does not decide the question.'));
    for (const f of d.document_defects) {
      const box = el('div', 'record');
      add(box, el('h3', 'record-title', f.title), lawBlock(f.body.replace(/\*\*/g, '').slice(0, 1400), 'law-dark law-quiet'));
      host.appendChild(box);
    }
  }
}

/* ================================================================ boot */

(async function boot() {
  $('nav').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-view]');
    if (b) showView(b.dataset.view);
  });
  $('pulse').onclick = () => {
    showView(isRunning() && rt.job.kind === 'generate' ? 'generate' : 'exposure');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  $('drawerclose').onclick = () => { $('drawer').hidden = true; };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('drawer').hidden = true; });
  window.addEventListener('hashchange', () => showView(location.hash.slice(1)));

  buildLaunch();
  renderRun();
  renderAskEmpty();
  showView(location.hash.slice(1) || 'exposure');

  const loadedState = api('/api/state').then((s) => {
    state.docs = s.documents;
    state.meta = s;
    if (!$('view-documents').hidden) renderDocuments();
  }).catch(() => {});

  await Promise.allSettled([loadAgent(), refreshScopes(), refreshQueue()]);
  renderModelLine();
  fillScopeSelect();
  buildWork();
  renderRoster();

  /* Pick up whatever the server is doing, or the last thing it did, so the
     engine is never blank after a reload during a run. */
  try {
    const jobs = (await api('/api/exposure/jobs')).jobs;
    if (jobs.length) await attach(jobs[0].id);
  } catch (e) { /* the loop below reports an unreachable server */ }
  renderRun();
  loop();
  setInterval(tickClocks, 1000);
  setInterval(async () => { if (!isRunning()) { await loadAgent(); renderModelLine(); renderPulse(); } }, 30000);

  await loadedState;
  /* A question in the address bar is asked on arrival, so an answer can be
     handed to a colleague as a link. It is re-executed on arrival, so what
     they read came from today's corpus. */
  const asked = new URLSearchParams(location.search).get('q');
  if (asked) {
    showView('ask');
    $('askfield').value = asked;
    $('askform').requestSubmit();
  }
})();

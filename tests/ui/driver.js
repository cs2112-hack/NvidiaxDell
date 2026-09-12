/* UI check driver.

   Injected into the page by scripts/ui_check.py, after app.js; the app never
   serves it. It plays a scenario's steps against the live interface, measures
   the layout after each `check` step and at the end, and writes a JSON report
   into the page for the harness to read back out of the DOM.

   The layout rules are generic on purpose. A scenario says which state to put
   the interface in; this file decides whether that state is broken, the same
   way for every view, so a new state gets the same scrutiny as an old one. */

(async () => {
  const sc = window.__UI_SCENARIO__;
  const report = { scenario: sc.name, width: innerWidth, failures: [], problems: [], errors: [] };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fail = (msg) => report.failures.push(msg);

  const shown = (e) => {
    if (!e || !e.isConnected || e.closest('[hidden]')) return false;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const find = (sel, text) => [...document.querySelectorAll(sel)].find((e) => shown(e) && (!text || e.textContent.includes(text)));
  const desc = (e) => {
    const cls = typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '';
    const txt = (e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return `<${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${cls}>${txt ? ` "${txt}"` : ''}`;
  };

  async function waitFor(sel, text, ms = 20000) {
    for (let waited = 0; waited <= ms; waited += 100) {
      const e = find(sel, text);
      if (e) return e;
      await sleep(100);
    }
    return null;
  }

  function setValue(e, value) {
    const proto = e.tagName === 'SELECT' ? HTMLSelectElement.prototype : e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, value);
    e.dispatchEvent(new Event('input', { bubbles: true }));
    e.dispatchEvent(new Event('change', { bubbles: true }));
  }

  /* ------------------------------------------------------------ layout */

  function clipped(node, box) {
    for (let p = node.parentElement; p && p !== box; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX !== 'visible' || cs.overflow !== 'visible') return true;
    }
    return false;
  }

  function layoutProblems() {
    const out = [];
    const vw = innerWidth;
    const sw = document.documentElement.scrollWidth;
    if (sw > vw + 1) out.push(`the page scrolls sideways (${sw}px wide in a ${vw}px window)`);

    const top = document.querySelector('.top');
    if (top) {
      const tr = top.getBoundingClientRect();
      if (Math.round(tr.height) !== 56) out.push(`the top bar is ${Math.round(tr.height)}px tall, not 56px`);
      const kids = [...top.children].filter(shown).map((k) => [k, k.getBoundingClientRect()]);
      for (const [k, r] of kids) {
        if (r.left < tr.left - 1 || r.right > tr.right + 1) out.push(`${desc(k)} sticks out of the top bar`);
        if (r.top < tr.top - 1 || r.bottom > tr.bottom + 1) out.push(`${desc(k)} is taller than the top bar`);
      }
      for (let i = 0; i < kids.length; i++) {
        for (let j = i + 1; j < kids.length; j++) {
          const [a, ra] = kids[i];
          const [b, rb] = kids[j];
          const overlap = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
          if (overlap > 1) out.push(`${desc(a)} overlaps ${desc(b)} in the top bar by ${Math.round(overlap)}px`);
        }
      }
      const status = top.querySelector('.status');
      if (status && shown(status) && status.scrollWidth > status.clientWidth + 1) out.push('the status control overflows its own box');
    }

    for (const d of document.querySelectorAll('.dot, .bdot, .nav-live, .example-sq')) {
      if (!shown(d)) continue;
      const r = d.getBoundingClientRect();
      if (r.width > 10 || r.height > 10) out.push(`${desc(d)} should be a dot but is ${Math.round(r.width)}x${Math.round(r.height)}px`);
    }
    for (const m of document.querySelectorAll('.step-mark, .atk, .tick')) {
      if (!shown(m)) continue;
      const r = m.getBoundingClientRect();
      if (r.width > 32 || r.height > 34) out.push(`${desc(m)} lost its fixed size (${Math.round(r.width)}x${Math.round(r.height)}px)`);
    }
    for (const c of document.querySelectorAll('.badge, .engine, .role, .cite, .status, .btn-sm, .nav-count')) {
      if (!shown(c) || c.closest('.verdict')) continue;
      const r = c.getBoundingClientRect();
      if (r.height > 36) out.push(`${desc(c)} wrapped onto more than one line (${Math.round(r.height)}px tall)`);
    }

    const boxes = '.card, .agent, .mini-step, .finding, .draftrun, .tour-card, .drawer-body, .top, .composer, .missing, .trace, .passage, .verdict, .notice, .stat, .step-body, .q-bubble, .tip';
    const seen = new Set();
    for (const box of document.querySelectorAll(boxes)) {
      if (!shown(box)) continue;
      const br = box.getBoundingClientRect();
      for (const child of box.querySelectorAll('*')) {
        if (!shown(child) || child.closest('pre, .table-wrap, svg') || child.tagName === 'OPTION') continue;
        const cr = child.getBoundingClientRect();
        if (cr.right > br.right + 2 || cr.left < br.left - 2) {
          if (clipped(child, box)) continue;
          const key = desc(child);
          if (seen.has(key)) continue;
          seen.add(key);
          out.push(`${key} spills out of ${desc(box).slice(0, 60)} by ${Math.round(Math.max(cr.right - br.right, br.left - cr.left))}px`);
        }
      }
    }
    return out.slice(0, 25);
  }

  function tourProblems() {
    const out = [];
    const card = document.querySelector('.tour-card');
    if (!shown(card)) return ['the tour card is not on screen'];
    const r = card.getBoundingClientRect();
    if (r.left < -1 || r.top < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1) {
      out.push(`the tour card is partly off screen (${Math.round(r.left)},${Math.round(r.top)} to ${Math.round(r.right)},${Math.round(r.bottom)})`);
    }
    const st = TOUR[T.i];
    const target = st.target && document.querySelector(st.target);
    const spot = document.querySelector('.tour-spot');
    if (target && shown(target) && !spot.classList.contains('is-empty')) {
      const s = spot.getBoundingClientRect();
      const t = target.getBoundingClientRect();
      const vis = { left: Math.max(t.left, 0), top: Math.max(t.top, 0), right: Math.min(t.right, innerWidth), bottom: Math.min(t.bottom, innerHeight) };
      if (vis.right > vis.left && vis.bottom > vis.top) {
        if (s.right < vis.left || s.left > vis.right || s.bottom < vis.top || s.top > vis.bottom) out.push(`step "${st.key}": the spotlight misses ${st.target}`);
        /* A card may brush the control it explains, but not hide it. On a
           phone a tall control cannot be shown whole beside a card, so there
           the rule is that the start of the control stays in view. */
        if (innerWidth > 860) {
          const w = Math.min(r.right, vis.right) - Math.max(r.left, vis.left);
          const h = Math.min(r.bottom, vis.bottom) - Math.max(r.top, vis.top);
          const share = w > 0 && h > 0 ? (w * h) / ((vis.right - vis.left) * (vis.bottom - vis.top)) : 0;
          if (share > 0.25) out.push(`step "${st.key}": the card covers ${Math.round(share * 100)}% of ${st.target}`);
        } else {
          const headBottom = Math.min(t.bottom, t.top + 140);
          const overlap = Math.min(r.bottom, headBottom) - Math.max(r.top, Math.max(t.top, 0));
          if (t.top < 0 || t.top > innerHeight - 40) out.push(`step "${st.key}": the start of ${st.target} is off screen`);
          else if (overlap > 20) out.push(`step "${st.key}": the card hides the start of ${st.target} (${Math.round(overlap)}px)`);
        }
      } else {
        out.push(`step "${st.key}": ${st.target} is not scrolled into view`);
      }
    }
    /* Scrolling a control into view must not move the fixed frame. */
    const top = document.querySelector('.top').getBoundingClientRect();
    if (Math.abs(top.top) > 1) out.push(`step "${st.key}": the top bar has moved ${Math.round(top.top)}px from the top of the window`);
    const side = document.querySelector('.side');
    if (innerWidth > 860 && Math.abs(side.getBoundingClientRect().top) > 1) out.push(`step "${st.key}": the sidebar has moved ${Math.round(side.getBoundingClientRect().top)}px down`);
    return out;
  }

  /* ----------------------------------------------------------- actions */

  const actions = {
    async wait(sel, text) { if (!(await waitFor(sel, text))) fail(`never appeared: ${sel}${text ? ` containing "${text}"` : ''}`); },
    async click(sel, text) {
      const e = await waitFor(sel, text);
      if (!e) return fail(`nothing to click: ${sel}${text ? ` containing "${text}"` : ''}`);
      if (e.disabled) return fail(`${desc(e)} is disabled`);
      e.click();
      await sleep(80);
    },
    async fill(sel, value) {
      const e = await waitFor(sel);
      if (!e) return fail(`no field ${sel}`);
      setValue(e, value);
    },
    async fillFact(label, value) {
      const lab = await waitFor('.fact label', label);
      if (!lab) return fail(`no fact field labelled "${label}"`);
      setValue(document.getElementById(lab.htmlFor), value);
    },
    async submit(sel) {
      const f = await waitFor(sel);
      if (!f) return fail(`no form ${sel}`);
      f.requestSubmit();
      await sleep(80);
    },
    async key(key) {
      (document.activeElement || document).dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      await sleep(80);
    },
    sleep: (ms) => sleep(ms),
    async gone(sel) {
      for (let w = 0; w < 5000; w += 100) { if (!find(sel)) return; await sleep(100); }
      fail(`still on screen: ${sel}`);
    },
    async disabled(sel) {
      const e = await waitFor(sel);
      if (!e) return fail(`no ${sel}`);
      for (let w = 0; w < 5000; w += 100) { if (e.disabled) return; await sleep(100); }
      fail(`${desc(e)} should be disabled`);
    },
    async check(label) {
      await sleep(300);
      for (const p of layoutProblems()) report.problems.push(`${label}: ${p}`);
    },
    async tourWalk() {
      if (!(await waitFor('.tour-card'))) return fail('the tour never opened');
      const total = TOUR.length;
      for (let i = 0; i < total; i++) {
        await sleep(700);
        if (T.i !== i) { fail(`the tour is on step ${T.i + 1}, expected ${i + 1}`); break; }
        for (const p of [...tourProblems(), ...layoutProblems().filter((x) => /tour/.test(x))]) report.problems.push(`tour step ${i + 1} (${TOUR[i].key}): ${p}`);
        const next = find('[data-tour-next]');
        if (!next) { fail(`tour step ${i + 1} has no Next button`); break; }
        next.click();
      }
      await sleep(300);
      if (!document.getElementById('tour').hidden) fail('the tour did not close after its last step');
    },
  };

  window.addEventListener('error', (e) => report.errors.push(`${e.message} (${(e.filename || '').split('/').pop()}:${e.lineno})`));

  if (!(await waitFor('#status .status-text'))) fail('the app never rendered its status');
  await sleep(sc.settle || 1500);
  for (const [name, ...args] of sc.steps || []) {
    if (!actions[name]) { fail(`unknown step ${name}`); continue; }
    try { await actions[name](...args); } catch (e) { fail(`step ${name} threw ${e && e.message}`); }
  }
  await sleep(400);
  for (const p of layoutProblems()) report.problems.push(`at the end: ${p}`);
  report.errors.push(...(window.__UI_ERRORS__ || []));

  const pre = document.createElement('pre');
  pre.id = '__ui_report__';
  pre.hidden = true;
  pre.textContent = JSON.stringify(report);
  document.body.appendChild(pre);
})();

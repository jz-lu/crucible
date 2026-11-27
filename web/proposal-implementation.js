/* Implementation follows a recorded decision; it never activates a release. */
(() => {
  'use strict';
  const labels = {awaiting_review:'Awaiting review', awaiting_implementation:'Awaiting implementation', in_progress:'In progress', completed:'Completed', changes_requested:'Changes requested', declined:'Not recommended', retracted:'Retracted'};
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const label = status => labels[status] || 'Progress unavailable';
  const dateLabel = value => typeof value === 'number' && Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}) : '';
  const endpoint = (kind, id) => '/api/proposal-implementations/' + encodeURIComponent(kind) + '/' + encodeURIComponent(id);
  function evidenceLink(value) {
    if (typeof value !== 'string' || !value.trim()) throw Error('Add an HTTPS evidence link or an exact note citation.');
    const base = new URL(window.location.href), link = new URL(value.trim(), base.origin + '/');
    if (link.username || link.password) throw Error('Evidence links must not contain credentials.');
    if (link.origin === base.origin && link.hash.startsWith('#notebook?')) {
      const fields = new URLSearchParams(link.hash.slice(10)), revision = Number(fields.get('revision'));
      if (fields.has('loc') && (fields.size !== 5 || !fields.has('passage') || !/^P\.[1-9][0-9]{0,9}$/.test(fields.get('loc') || '') || Number(fields.get('loc').slice(2)) > 2147483647)) throw Error('Use the copied loc link with its exact source.');
      if (link.pathname !== '/' || link.search || !fields.get('note') || !Number.isSafeInteger(revision) || revision < 1 || !/^[a-f0-9]{64}$/.test(fields.get('sha256') || '')) throw Error('Use the exact note link from “Cite this note”, including its revision and content hash.');
      const allowed = new Set(['note','revision','sha256','location','note_section','statement','argument','passage','loc']);
      if ([...fields.keys()].some(key => !allowed.has(key) || fields.getAll(key).length !== 1) || ['location','note_section','statement','argument','passage'].filter(key => fields.has(key)).length > 1) throw Error('Use one exact note or passage citation.');
      if (fields.has('passage') && !/^p1-(?:b|s-[a-z][a-z0-9-]{0,63})-(?:0|[1-9][0-9]{0,4})-[1-9][0-9]{0,4}$/.test(fields.get('passage'))) throw Error('Use a passage citation copied from its saved note.');
      return '/#notebook?' + fields;
    }
    if (link.protocol !== 'https:' || link.port && link.port !== '443') throw Error('External evidence needs an HTTPS link on its standard port.');
    return link.href;
  }
  function valid(record, kind, id, compact = false) {
    return record && record.kind === kind && record.target_id === id && Object.hasOwn(labels, record.status) &&
      typeof record.eligible === 'boolean' && typeof record.can_update === 'boolean' &&
      (!record.eligible || typeof record.decision_id === 'string' && record.decision_id && (compact || /^[a-f0-9]{64}$/.test(record.decision_sha256 || '')));
  }
  function mount(host, {initial, kind, targetId, api, current, onChange = () => {}}) {
    if (!valid(initial, kind, targetId)) return;
    const box = node('section', undefined, 'proposal-implementation'); host.append(box);
    let record = initial, request = 0;
    const present = () => current() && box.isConnected;
    const linkList = evidence => {
      const list = node('ul', undefined, 'implementation-evidence');
      for (const item of evidence || []) {
        try {
          const href = evidenceLink(item.url), link = node('a', item.label || 'Evidence'); link.href = href;
          if (!href.startsWith('/#')) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
          const row = node('li'); row.append(link); list.append(row);
        } catch { list.append(node('li', 'Evidence link unavailable')); }
      }
      return list;
    };
    function render() {
      if (!present()) return;
      box.replaceChildren();
      const heading = node('div', undefined, 'implementation-heading');
      heading.append(node('h3', 'Implementation'), node('span', label(record.status), 'implementation-status'));
      box.append(heading);
      if (record.eligible) {
        const steps = node('ol', undefined, 'implementation-steps'); steps.setAttribute('aria-label', 'Implementation progress');
        for (const status of ['awaiting_implementation','in_progress','completed']) {
          const step = node('li', label(status)); if (status === record.status) step.setAttribute('aria-current', 'step'); steps.append(step);
        }
        box.append(steps);
      }
      if (record.responsible?.handle) box.append(node('p', 'Responsible · ' + record.responsible.handle, 'implementation-meta'));
      else if (record.actor?.handle) box.append(node('p', ['Updated by ' + record.actor.handle, dateLabel(record.updated_at)].filter(Boolean).join(' · '), 'implementation-meta'));
      const events = Array.isArray(record.events) ? record.events : [], latest = events.find(event => event.id === record.head_event_id);
      if (latest) {
        box.append(node('p', latest.summary, 'implementation-update'));
        if (latest.evidence?.length) box.append(linkList(latest.evidence));
      }
      if (events.length > 1) {
        const history = node('details', undefined, 'implementation-history'); history.append(node('summary', 'Earlier updates'));
        for (const event of events.filter(value => value.id !== record.head_event_id).reverse()) {
          const entry = node('article'); entry.append(node('strong', label(event.status)), node('p', event.summary));
          if (event.actor?.handle) entry.append(node('p', ['Recorded by ' + event.actor.handle,dateLabel(event.created_at)].filter(Boolean).join(' · '), 'implementation-meta'));
          if (event.evidence?.length) entry.append(linkList(event.evidence)); history.append(entry);
        }
        box.append(history);
      }
      box.append(node('p', record.eligible ? 'Tracks delivery of the approved next step. Completion does not certify research, activate a challenge or award points.' :
        events.length ? 'Earlier progress remains recorded. This decision is no longer eligible for implementation updates.' :
          'Implementation starts after an independent approval of this proposal.', 'implementation-scope'));
      if (record.can_update === true && record.eligible === true && Array.isArray(record.events)) editor();
    }
    function editor() {
      const details = node('details', undefined, 'implementation-editor'); details.append(node('summary', record.status === 'completed' ? 'Reopen implementation' : 'Update implementation'));
      const form = node('form'), controls = [];
      function field(title, tag, name) {
        const wrap = node('label', title), input = node(tag); input.name = name; input.setAttribute('name', name); controls.push(input); wrap.append(input); form.append(wrap); return input;
      }
      const status = field('Next step', 'select', 'implementation-status');
      for (const value of record.status === 'in_progress' ? ['in_progress','completed'] : ['in_progress']) {
        const caption = value === 'completed' ? 'Mark completed' : record.status === 'completed' ? 'Reopen · in progress' : record.status === 'in_progress' ? 'Record progress' : 'Start implementation';
        const option = node('option', caption); option.value = value; status.append(option);
      }
      status.value = 'in_progress';
      const summary = field('Update', 'textarea', 'implementation-summary'); summary.rows = 3; summary.maxLength = 4000; summary.required = true;
      const evidenceTitle = field('Evidence title', 'input', 'implementation-evidence-title'); evidenceTitle.maxLength = 200;
      const evidenceURL = field('Evidence link', 'input', 'implementation-evidence-url'); evidenceURL.type = 'text'; evidenceURL.maxLength = 2000;
      evidenceURL.placeholder = 'HTTPS link or exact note citation';
      form.append(node('p', 'Completion needs an update and evidence. Each update records the reviewer who made it.', 'implementation-meta'));
      const error = node('p', '', 'form-error'); error.setAttribute('role', 'alert');
      const submit = node('button', 'Save progress', 'button button-quiet'); submit.type = 'submit'; form.append(error, submit); details.append(form); box.append(details);
      let pending = null, busy = false;
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy || !present()) return;
        try {
          if (!pending) {
            if (!summary.value.trim()) throw Error('Describe the implementation update.');
            if (status.value === 'completed' && (!evidenceTitle.value.trim() || !evidenceURL.value.trim())) throw Error('Completion needs an evidence title and link.');
            if (Boolean(evidenceTitle.value.trim()) !== Boolean(evidenceURL.value.trim())) throw Error('Include both the evidence title and link.');
            pending = {decision_id:record.decision_id, expected_event_id:record.head_event_id || null, status:status.value, summary:summary.value.trim(),
              evidence:evidenceURL.value.trim() ? [{label:evidenceTitle.value.trim(),url:evidenceLink(evidenceURL.value)}] : [], idempotency_key:crypto.randomUUID()};
          }
          busy = true; submit.disabled = true; controls.forEach(control => { control.disabled = true; }); error.textContent = '';
          const result = await api(endpoint(kind, targetId) + '/events', pending);
          if (!present()) return;
          const receipt = result.event;
          if (!valid(result.implementation, kind, targetId) || result.implementation.decision_id !== pending.decision_id ||
              !receipt?.id || receipt.id === pending.expected_event_id ||
              receipt?.status !== pending.status || receipt?.summary !== pending.summary ||
              !Array.isArray(receipt.evidence) || JSON.stringify(receipt.evidence.map(item => ({label:item.label,url:item.url}))) !== JSON.stringify(pending.evidence)) throw Error('The progress receipt could not be confirmed.');
          record = result.implementation; pending = null; render(); onChange(record);
        } catch (failure) {
          if (!present()) return;
          const uncertain = busy && (!failure.status || failure.status >= 500);
          error.textContent = failure.message + (uncertain ? ' Retry to confirm the same update.' : '');
          if (!uncertain) { pending = null; controls.forEach(control => { control.disabled = false; }); }
          submit.textContent = uncertain ? 'Retry update' : 'Save progress';
          if (failure.status === 409) {
            controls.forEach(control => { control.disabled = true; }); submit.disabled = true;
            const refresh = node('button', 'Refresh progress', 'text-button'); refresh.type = 'button'; refresh.addEventListener('click', load); error.append(' ', refresh);
          }
        } finally { if (present()) { busy = false; if (!error.querySelector('button')) submit.disabled = false; } }
      });
    }
    async function load() {
      const token = ++request;
      if (!present() || !record.decision_id) return;
      try {
        const result = await api(endpoint(kind, targetId) + '?' + new URLSearchParams({decision_id:record.decision_id}));
        if (!present() || token !== request) return;
        if (!valid(result.implementation, kind, targetId) || result.implementation.decision_id !== record.decision_id) throw Error('Implementation progress could not be verified.');
        record = result.implementation; render();
      } catch (error) {
        if (!present() || token !== request) return;
        if ([401,403,404].includes(error.status)) { box.replaceChildren(node('p', 'Implementation progress is no longer available.', 'implementation-meta')); return; }
        box.querySelector('.implementation-read-error')?.remove();
        const message = node('p', 'Progress could not be refreshed. ', 'implementation-read-error');
        const retry = node('button', 'Try again', 'text-button'); retry.type = 'button'; retry.addEventListener('click', load); message.append(retry); box.append(message);
      }
    }
    render(); if (!Array.isArray(initial.events) && initial.decision_id) void load();
  }
  window.CrucibleImplementation = Object.freeze({mount, label, valid, evidenceLink});
})();

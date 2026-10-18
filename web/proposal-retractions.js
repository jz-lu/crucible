/* Explicit author decisions. Authored note tags cannot manufacture this state. */
(() => {
  'use strict';
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  function sourceFromLink(value) {
    if (!value.trim()) return null;
    const base = new URL(window.location.href), link = new URL(value, base.origin + '/');
    if (link.origin !== base.origin || link.pathname !== '/' || link.username || link.password || !link.hash.startsWith('#notebook?')) throw Error('Use an exact Crucible note link from “Cite this note”.');
    const fields = new URLSearchParams(link.hash.slice(10));
    const revision = Number(fields.get('revision'));
    if (!fields.get('note') || !Number.isSafeInteger(revision) || revision < 1 || revision > 100 || !/^[a-f0-9]{64}$/.test(fields.get('sha256') || '')) throw Error('The supporting link needs a note ID, revision and content hash. Use “Cite this note”.');
    return {note_id:fields.get('note'), revision, content_sha256:fields.get('sha256')};
  }
  function mount(host, {record, kind, api, current, onSuccess}) {
    if (record.retracted) {
      const box = node('section', undefined, 'proposal-retraction');
      box.append(node('strong', 'Retracted'));
      if (record.retraction?.reason) box.append(node('p', record.retraction.reason, 'proposal-retraction-reason'));
      const ref = record.retraction?.supporting_note;
      if (ref?.note_id && Number.isSafeInteger(ref.revision) && /^[a-f0-9]{64}$/.test(ref.content_sha256 || '')) {
        const link = node('a', 'Read supporting note →', 'text-button');
        link.href = '#notebook?' + new URLSearchParams({note:ref.note_id,revision:String(ref.revision),sha256:ref.content_sha256}); box.append(link);
      }
      box.append(node('p', 'Withdrawn by the proposer. The original evidence and review history remain available.', 'field-hint'));
      host.append(box); return;
    }
    if (record.can_retract !== true) return;
    const details = node('details', undefined, 'proposal-retraction');
    details.append(node('summary', 'Retract proposal'));
    const form = node('form');
    form.append(node('p', 'Withdraw this proposal if you no longer support it. Explain what changed and link the relevant evidence. Retraction ends this request; the note and earlier reviews remain available. A later proposal needs a new request.', 'field-hint'));
    const reasonLabel = node('label', 'Reason for retraction'), reason = node('textarea');
    reason.name = 'retraction-reason'; reason.setAttribute('name', reason.name); reason.value = ''; reason.required = true; reason.maxLength = 4000; reason.rows = 4; reasonLabel.append(reason);
    const sourceLabel = node('label', 'Supporting note link (optional)'), source = node('input');
    source.name = 'retraction-source'; source.setAttribute('name', source.name); source.value = ''; source.type = 'text'; source.maxLength = 2048; source.placeholder = 'Paste the exact link from Cite this note'; sourceLabel.append(source);
    const error = node('p', '', 'form-error'); error.setAttribute('role', 'alert');
    const submit = node('button', 'Retract proposal', 'button button-quiet'); submit.type = 'submit';
    form.append(reasonLabel, sourceLabel, error, submit); details.append(form); host.append(details);
    let pending = null, busy = false;
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy || !current()) return;
      try {
        if (!pending) {
          if (!reason.value.trim()) throw Error('Explain why you are withdrawing this proposal.');
          const supporting = sourceFromLink(source.value);
          pending = {reason:reason.value.trim(), ...(supporting ? {supporting_note:supporting} : {}), idempotency_key:crypto.randomUUID(),
            ...(kind === 'scheme' ? {expected_revision:record.revision,content_sha256:record.content_sha256} : {packet_sha256:record.packet_sha256})};
        }
        busy = true; submit.disabled = true; reason.disabled = source.disabled = true; error.textContent = '';
        const path = kind === 'scheme' ? '/api/protocol/proposals/' : '/api/notebook/research-requests/';
        const result = await api(path + encodeURIComponent(record.id) + '/retraction', pending);
        if (!current()) return;
        const receipt = kind === 'scheme' ? result.proposal : result.request;
        if (receipt?.id !== record.id || receipt.retracted !== true) throw Error('The retraction receipt could not be confirmed.');
        pending = null; await onSuccess();
      } catch (failure) {
        if (!current()) return;
        const uncertain = busy && (!failure.status || failure.status >= 500);
        error.textContent = failure.message + (uncertain ? ' Retry to confirm the same retraction.' : '');
        if (!uncertain) { pending = null; reason.disabled = source.disabled = false; }
        submit.textContent = uncertain ? 'Retry retraction' : 'Retract proposal';
      } finally { if (current()) { busy = false; submit.disabled = false; } }
    });
  }
  window.CrucibleRetractions = Object.freeze({mount});
})();

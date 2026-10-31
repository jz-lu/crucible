/* Bounty reads and receipts use the application's authenticated transport. */
(() => {
  'use strict';
  const idPattern = /^[0-9a-f-]{36}$/i, hashPattern = /^[0-9a-f]{64}$/;
  const byteSize = value => new TextEncoder().encode(JSON.stringify(value)).length;
  const denied = error => [401, 403, 404, 410].includes(error?.status);
  function validateTask(value, id) {
    if (!value || !idPattern.test(value.id) || id && value.id !== id ||
        typeof value.title !== 'string' || typeof value.summary !== 'string' ||
        !Number.isSafeInteger(value.version) || value.version < 1 || !hashPattern.test(value.terms_sha256) ||
        !Number.isInteger(value.scope) || value.scope < 1 || value.scope > 5 ||
        !Array.isArray(value.background_tags) || value.background_tags.some(tag => typeof tag !== 'string') ||
        !value.capabilities || typeof value.capabilities !== 'object') throw Error('The bounty response could not be verified.');
    return value;
  }
  function validatePolicy(value) {
    const policy = value?.policy;
    if (!policy || !['ready', 'pending'].includes(policy.status) || typeof policy.id !== 'string' || !policy.id) throw Error('The bounty policy could not be verified.');
    if (policy.status === 'ready' && (!Number.isSafeInteger(policy.monthly_units) || policy.monthly_units < 0 || !['proposal_cost_units', 'boost_cost_units', 'points_per_boost'].every(key => Number.isSafeInteger(policy[key]) && policy[key] > 0))) throw Error('The bounty allowance terms could not be verified.');
    const initial = policy.proposal_reward_points ?? policy.default_base_reward_points;
    if (policy.status === 'ready' && (!Number.isSafeInteger(initial) || initial < 1)) throw Error('The initial bounty reward could not be verified.');
    return value;
  }
  function parseReferences(text, {single = false} = {}) {
    let value;
    try { value = JSON.parse(text); } catch { value = text.trim(); }
    const values = Array.isArray(value) ? value : [value];
    if (!values.length || values.length > 12 || single && values.length !== 1) throw Error(single ? 'Use one exact contribution note.' : 'Use one to twelve exact source notes.');
    return values.map(item => {
      if (typeof item === 'string' || item?.url) {
        const origin = new URL(window.location.href);
        let url;
        try { url = new URL(typeof item === 'string' ? item : item.url, origin); } catch { throw Error('Use the exact citation link from this notebook.'); }
        if (url.origin !== origin.origin || url.pathname !== '/' || !url.hash.startsWith('#notebook?')) throw Error('Use an exact note citation from this Crucible website.');
        const pin = new URLSearchParams(url.hash.slice('#notebook?'.length));
        if (!/^[1-9][0-9]*$/.test(pin.get('revision') || '')) throw Error('The note citation must pin a revision.');
        item = {note_id: pin.get('note'), revision: Number(pin.get('revision')), content_sha256: pin.get('sha256')};
      }
      const noteId = item?.note_id || item?.noteId || item?.id;
      if (typeof noteId !== 'string' || !noteId || noteId.length > 108 ||
          !Number.isSafeInteger(item.revision) || item.revision < 1 || !hashPattern.test(item.content_sha256 || '')) {
        throw Error('Each citation needs its note ID, revision and content SHA-256. Use Cite this note in the notebook.');
      }
      return {note_id: noteId, revision: item.revision, content_sha256: item.content_sha256};
    });
  }
  function createClient(api, current) {
    const cache = new Map(), pending = new Map();
    let epoch = 0;
    function invalidateReads() { epoch++; cache.clear(); pending.clear(); }
    function snapshot(path) {
      const entry = cache.get(path);
      if (!current() || !entry || Date.now() - entry.at > 300000) { cache.delete(path); return null; }
      cache.delete(path); cache.set(path, entry); return entry;
    }
    function remember(path, value) {
      const size = byteSize(value);
      if (size > 512 * 1024) return;
      cache.delete(path); cache.set(path, {value, size, at: Date.now()});
      let total = [...cache.values()].reduce((sum, entry) => sum + entry.size, 0);
      while (cache.size > 16 || total > 2 * 1024 * 1024) {
        const first = cache.keys().next().value; total -= cache.get(first).size; cache.delete(first);
      }
    }
    function read(path, validate, {refresh = false} = {}) {
      const old = snapshot(path);
      if (!refresh && old && Date.now() - old.at < 10000) return Promise.resolve(old.value);
      if (pending.has(path)) return pending.get(path);
      const owner = epoch;
      const task = (async () => {
        if (!current()) throw Object.assign(Error('This session is no longer active.'), {status: 403});
        const result = validate(await api(path, undefined, {readRecovery: true}));
        if (!current() || epoch !== owner) throw Object.assign(Error('This view is no longer active.'), {retired: true});
        remember(path, result); return result;
      })();
      pending.set(path, task);
      task.catch(error => { if (current() && epoch === owner && denied(error)) invalidateReads(); })
        .finally(() => { if (pending.get(path) === task) pending.delete(path); });
      return task;
    }
    function listPath({status = 'open', sort = 'concrete', page = 1, review = false} = {}) {
      return (review ? '/api/bounties/review-queue?' : '/api/bounties?') + new URLSearchParams({
        ...(review ? {} : {status, sort}), offset: String((page - 1) * 6), limit: '6'});
    }
    function validateList(value) {
      if (!Array.isArray(value?.bounties) || value.bounties.length > 6 ||
          !Number.isSafeInteger(value.pagination?.offset) || value.pagination.offset < 0 ||
          value.pagination.limit !== 6 || typeof value.pagination.has_more !== 'boolean') throw Error('The bounty list could not be verified.');
      value.bounties.forEach(item => validateTask(item)); return validatePolicy(value);
    }
    function noteSearchPath({query = '', after = null} = {}) {
      if (typeof query !== 'string' || query.length > 200 || after !== null && (typeof after !== 'string' || after.length > 768)) throw Error('Use up to 200 characters to search notes.');
      return '/api/notebook/notes?' + new URLSearchParams({limit: '25', scope: 'all', include_curated: 'true',
        ...(query.trim() ? {q: query.trim()} : {}), ...(after ? {after} : {})});
    }
    function validateNotes(value, maximum = 25) {
      if (!Array.isArray(value?.notes) || value.notes.length > maximum ||
          value.next_after !== null && (typeof value.next_after !== 'string' || !value.next_after || value.next_after.length > 768)) throw Error('The note search response could not be verified.');
      const ids = new Set(), notes = [];
      for (const note of value.notes) {
        if (!note || typeof note !== 'object') throw Error('The note search response could not be verified.');
        if (note.deletion || note.publication_status === 'withdrawn') continue;
        const revision = Array.isArray(note.revisions) ? note.revisions.find(item => item.revision === note.revision) : null;
        if (typeof note.id !== 'string' || !note.id || note.id.length > 108 || ids.has(note.id) ||
            !Number.isSafeInteger(note.revision) || note.revision < 1 || !hashPattern.test(note.content_sha256 || '') ||
            revision?.content_sha256 !== note.content_sha256 || typeof revision?.content?.title !== 'string' ||
            note.number != null && (!Number.isSafeInteger(note.number) || note.number < 1)) throw Error('The note search response could not be verified.');
        ids.add(note.id);
        notes.push({note_id: note.id, revision: note.revision, content_sha256: note.content_sha256,
          number: note.number ?? null, title: revision.content.title, publication_status: note.publication_status});
      }
      return {notes, next_after: value.next_after};
    }
    const detailPath = id => {
      if (!idPattern.test(id)) throw Error('This bounty link is invalid.');
      return '/api/bounties/' + id;
    };
    const validateDetail = id => value => { validateTask(value?.bounty, id); if (!value.bounty.terms) throw Error('The bounty brief is missing.'); return validatePolicy(value); };
    async function write(path, body, id) {
      if (!current()) throw Object.assign(Error('This session is no longer active.'), {status: 403});
      const result = await api(path, body);
      if (!current()) throw Object.assign(Error('This view is no longer active.'), {retired: true});
      validateDetail(id)(result); invalidateReads(); return result;
    }
    return {
      invalidateReads,
      cachedList: options => snapshot(listPath(options))?.value,
      cachedDetail: id => snapshot(detailPath(id))?.value,
      list: (options, settings) => read(listPath(options), value => {
        validateList(value);
        if (value.pagination.offset !== ((options?.page || 1) - 1) * 6) throw Error('The bounty response names another page.');
        return value;
      }, settings),
      detail: (id, settings) => read(detailPath(id), validateDetail(id), settings),
      notes: (options, settings) => read(noteSearchPath(options), validateNotes, settings),
      noteIndex: () => read('/api/notebook/notes?projection=index&include_curated=true', value => {
        if (value?.format !== 'crucible-notebook-summary-index-v1' || value.scope !== 'all' || value.include_curated !== true ||
            typeof value.complete !== 'boolean') throw Error('The note index could not be verified.');
        return validateNotes(value, 500);
      }),
      allowance: settings => read('/api/me/bounty-allowance', value => {
        if (!Number.isSafeInteger(value?.allowance?.available_units) || value.allowance.available_units < 0 ||
            !Number.isSafeInteger(value.allowance.monthly_units) || value.allowance.monthly_units < 0) throw Error('The allowance response could not be verified.');
        return validatePolicy(value);
      }, settings),
      create: body => write('/api/bounties', body),
      revise: (id, body) => write(detailPath(id) + '/revisions', body, id),
      admission: (id, body) => write(detailPath(id) + '/admission-requests', body, id),
      withdraw: (id, body) => write(detailPath(id) + '/withdrawals', body, id),
      cancel: (id, body) => write(detailPath(id) + '/cancellations', body, id),
      boost: (id, body) => write(detailPath(id) + '/boosts', body, id),
      submit: (id, body) => write(detailPath(id) + '/submissions', body, id),
      admissionDecision: (id, body) => write(detailPath(id) + '/admission-decisions', body, id),
      decision: (id, submissionId, body) => {
        if (!idPattern.test(submissionId)) throw Error('This contribution link is invalid.');
        return write(detailPath(id) + '/submissions/' + submissionId + '/decisions', body, id);
      },
      bytes: byteSize
    };
  }
  window.CrucibleBountyAPI = {createClient, parseReferences};
})();

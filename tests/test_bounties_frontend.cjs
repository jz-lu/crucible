// Synthetic records test the production component; no fixtures ship as assets.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const {parseHTML, flush} = require('./frontend-dom.cjs');
const id = '11111111-1111-4111-8111-111111111111';
const second = '22222222-2222-4222-8222-222222222222';
const note = '33333333-3333-4333-8333-333333333333';
const hash = 'a'.repeat(64);
const pin = {note_id: note, revision: 2, content_sha256: hash};
const policy = {id: 'bounty-test-v1', status: 'ready', monthly_units: 10, proposal_cost_units: 1, boost_cost_units: 1, points_per_boost: 100, default_base_reward_points: 100};
const wallet = {available_units: 10, monthly_units: 10, policy_id: policy.id, policy_status: 'ready', pending_grant_units: 0, version: 1};
function task(identifier = id, more = {}) {
  const terms = {title: 'Reproduce a bounded result', summary: 'A synthetic test brief.', background_tags: ['Reproduction'], scope: 5, completion_criteria: 'Reproduce the specified outcome.', verification_plan: 'Independent comparison of exact outputs.', source_notes: [pin]};
  return {id: identifier, ...terms, terms, status: 'open', version: 1, terms_sha256: hash, owner: {id: second, handle: 'poster'}, reward_points: 100, pending_claim_count: 0,
    capabilities: {edit: false, request_admission: false, withdraw: false, boost: true, submit: true, review_admission: false}, submissions: [], decisions: [], award: null, ...more};
}
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
function fixture({responder, reviewOnly = false} = {}) {
  const document = parseHTML('<body><section id="host" class="nb-root nb-bounties-panel"></section></body>');
  const proto = Object.getPrototypeOf(document.createElement('input'));
  for (const name of ['name', 'type']) if (!Object.getOwnPropertyDescriptor(proto, name)) Object.defineProperty(proto, name, {get() {return this.getAttribute(name) || '';}, set(value) {this.setAttribute(name, value);}});
  const listeners = new Map();
  const window = {location: {href: 'https://crucible.example/'}, navigator: {}, addEventListener(name, fn) {listeners.set(name, [...(listeners.get(name) || []), fn]);}, removeEventListener(name, fn) {listeners.set(name, (listeners.get(name) || []).filter(item => item !== fn));}};
  let current = true, routed = Promise.resolve(), model;
  const requests = [], routes = [];
  const sandbox = vm.createContext({window, document, URL, URLSearchParams, TextEncoder, crypto, setTimeout, clearTimeout, structuredClone});
  for (const file of ['bounties-api.js', 'bounties.js']) vm.runInContext(fs.readFileSync('web/notebook/' + file, 'utf8'), sandbox, {filename: file});
  const api = async (path, body, options) => {
    const url = new URL(path, window.location.href); requests.push({path, url, body, options});
    const custom = responder?.(url, body, requests);
    if (custom !== undefined) return custom;
    if (url.pathname === '/api/me/bounty-allowance') return {allowance: wallet, policy};
    if (url.pathname === '/api/bounties' || url.pathname === '/api/bounties/review-queue') return {bounties: [task()], pagination: {offset: Number(url.searchParams.get('offset')), limit: 6, has_more: false}, policy};
    if (url.pathname === '/api/bounties/' + id) return {bounty: task(), policy};
    throw Error('Unexpected test API call: ' + path);
  };
  const client = window.CrucibleBountyAPI.createClient(api, () => current);
  const host = document.querySelector('#host');
  model = window.CrucibleBounties.mount(host, {client, current: () => current, canWarm: () => false, reviewOnly,
    navigate(route) {routes.push(route); routed = model.show(new URLSearchParams(route.split('?')[1]));}});
  return {window, document, host, requests, routes, model, client, api, listeners,
    show: query => model.show(new URLSearchParams(query)), routed: () => routed,
    revoke() {current = false; model.destroy();}, close() {model.destroy();}};
}
const button = (host, text) => host.querySelectorAll('button').find(node => node.textContent === text);
function click(host, text) {const node = button(host, text); assert.ok(node, text); node.click(); return node;}
function fill(host, name, value) {const input = host.querySelector('[name="' + name + '"]'); assert.ok(input, name); input.value = value; input.dispatchEvent({type: 'input'}); return input;}
const submit = host => host.querySelector('form').dispatchEvent({type: 'submit', preventDefault() {}});
function sourceNote(identifier = note, number = 1, more = {}) {
  const revision = more.revision || 2, digest = more.content_sha256 || hash;
  return {id: identifier, number, revision, content_sha256: digest, publication_status: 'approved',
    revisions: [{revision, content_sha256: digest, content: {title: more.title || 'Alpha source note', claim: 'A synthetic source.', tags: []}}], ...more};
}
const sourceOptions = f => f.host.querySelectorAll('[role="option"]');
const sourceTags = f => f.host.querySelectorAll('.bty-source-id').map(node => node.textContent);
function focusSources(f) { const input = f.host.querySelector('[name="bounty-sources"]'); input.focus(); input.dispatchEvent({type: 'focus'}); return input; }
const searchPause = () => new Promise(resolve => setTimeout(resolve, 210));
let passed = 0, complete = false;
async function test(name, run) {await run(); console.log('PASS ' + name); passed++;}
process.once('beforeExit', () => {if (!complete && !process.exitCode) {console.error('Bounty checks did not finish'); process.exitCode = 1;}});
(async () => {
  await test('cold board is neutral; warm same-query returns retain cards and focus; sorting stays server-owned', async () => {
    const pending = deferred(); let first = true;
    const f = fixture({responder: url => url.pathname === '/api/bounties' && first ? (first = false, pending.promise) : undefined});
    try {
      const initial = f.show('view=bounties'); await flush();
      assert.equal(f.host.querySelectorAll('[data-bounty]').length, 0);
      assert.doesNotMatch(f.host.textContent, /Example data|Loading|No open bounties yet/);
      pending.resolve({bounties: [task()], pagination: {offset: 0, limit: 6, has_more: false}, policy}); await initial;
      const card = f.host.querySelector('[data-bounty]'); card.focus();
      await f.show('view=bounties'); assert.equal(f.host.querySelector('[data-bounty]'), card); assert.equal(f.document.activeElement, card);
      assert.equal(f.requests.filter(r => r.url.pathname === '/api/bounties').length, 1);
      for (const sort of ['open', 'points-high', 'points-low']) await f.show('view=bounties&sort=' + sort);
      assert.deepEqual(f.requests.filter(r => r.url.pathname === '/api/bounties').map(r => r.url.searchParams.get('sort')), ['concrete', 'open', 'points-high', 'points-low']);
      assert.equal(f.host.querySelector('input[type="search"]'), null);
      assert.match(f.host.textContent, /Monthly bounty allowance10 of 10 remaining/);
    } finally {f.close();}
  });
  await test('read ownership rejects late completions and a failed optional preparation does not poison retries', async () => {
    const old = deferred(); let calls = 0;
    const f = fixture({responder: url => url.pathname === '/api/bounties' ? ++calls === 1 ? old.promise : {bounties: [task(second)], pagination: {offset: 0, limit: 6, has_more: false}, policy} : undefined});
    try {
      const first = f.client.list({page: 1}); const joined = f.client.list({page: 1}); assert.equal(first, joined);
      f.client.invalidateReads();
      const fresh = await f.client.list({page: 1}); assert.equal(fresh.bounties[0].id, second);
      old.resolve({bounties: [task()], pagination: {offset: 0, limit: 6, has_more: false}, policy});
      await assert.rejects(first, error => error.retired); assert.equal(f.client.cachedList({page: 1}).bounties[0].id, second);
      f.client.invalidateReads();
      await assert.rejects(f.client.list({page: 2}), /another page/);
      assert.equal(f.client.cachedList({page: 2}), undefined);
    } finally {f.close();}
  });
  await test('note citations accept the actual notebook link and citation JSON without accepting another website', async () => {
    const f = fixture();
    try {
      const parse = f.window.CrucibleBountyAPI.parseReferences;
      const link = '/#notebook?' + new URLSearchParams({note, revision: '2', sha256: hash});
      assert.deepEqual(JSON.parse(JSON.stringify(parse(link))), [pin]);
      assert.deepEqual(JSON.parse(JSON.stringify(parse(JSON.stringify({title: 'Source', url: link, scope: '', relation: 'cites'})))), [pin]);
      assert.throws(() => parse('https://other.example' + link), /this Crucible website/);
      assert.throws(() => parse(JSON.stringify({...pin, revision: 1.5})), /revision/);
      assert.throws(() => parse('/#notebook?note=' + note), /pin a revision/);
    } finally {f.close();}
  });
  await test('the main page keeps Bounties as its title across collections', async () => {
    const f = fixture();
    try {
      for (const status of ['open', 'mine', 'past']) {
        await f.show('status=' + status);
        assert.equal(f.host.querySelector('h1').textContent, 'Bounties');
        assert.equal(f.host.querySelector('.bty-count').textContent, status === 'mine' ? 'Your activity' : status === 'past' ? 'Past bounties' : 'Open tasks');
      }
    } finally { f.close(); }
  });
  await test('the source picker opens on focus and adds, removes and saves multiple exact pins', async () => {
    const alpha = sourceNote(), beta = sourceNote(second, 2, {title: 'Beta source note'});
    let saved;
    const f = fixture({responder: (url, body) => {
      if (url.pathname === '/api/notebook/notes') return {notes: [alpha, beta], next_after: null};
      if (url.pathname === '/api/bounties' && body) { saved = task(id, {...body.terms, terms: body.terms, status: 'draft'}); return {bounty: saved, policy}; }
      if (url.pathname === '/api/bounties/' + id && saved) return {bounty: saved, policy};
    }});
    try {
      await f.show('compose=bounty');
      assert.equal(f.requests.some(r => r.url.pathname === '/api/notebook/notes'), false);
      const input = focusSources(f); await flush();
      assert.equal(input.getAttribute('role'), 'combobox'); assert.equal(input.getAttribute('aria-expanded'), 'true');
      assert.equal(sourceOptions(f).length, 2);
      sourceOptions(f)[0].click(); await flush();
      sourceOptions(f)[0].click(); await flush();
      assert.deepEqual(sourceTags(f), ['#0001', '#0002']);
      assert.equal(sourceOptions(f).length, 0);
      f.host.querySelectorAll('button').find(node => node.getAttribute('aria-label') === 'Remove source #0001, revision 2').click(); await flush();
      assert.deepEqual(sourceTags(f), ['#0002']);
      sourceOptions(f)[0].click(); await flush();
      for (const [name, value] of [['bounty-title','A task'],['bounty-summary','A summary'],['bounty-completion','Criteria'],['bounty-verification','Verification']]) fill(f.host, name, value);
      submit(f.host); await flush(); await f.routed();
      const pins = JSON.parse(JSON.stringify(f.requests.find(r => r.body).body.terms.source_notes));
      assert.deepEqual(pins, [{note_id: second, revision: 2, content_sha256: hash}, pin]);
      const read = f.requests.find(r => r.url.pathname === '/api/notebook/notes');
      assert.equal(read.url.searchParams.get('limit'), '25'); assert.equal(read.url.searchParams.get('include_curated'), 'true');
    } finally { f.close(); }
  });
  await test('source search is server-owned, ignores an older response and supports keyboard selection', async () => {
    const old = deferred(), beta = sourceNote(second, 2, {title: 'Beta source note'});
    const f = fixture({responder: url => url.pathname === '/api/notebook/notes'
      ? url.searchParams.get('q') === 'Beta' ? {notes: [beta], next_after: null} : old.promise : undefined});
    try {
      await f.show('compose=bounty'); const input = focusSources(f);
      fill(f.host, 'bounty-sources', 'Beta'); await searchPause(); await flush();
      assert.equal(sourceOptions(f).length, 1); assert.match(sourceOptions(f)[0].textContent, /Beta/);
      old.resolve({notes: [sourceNote()], next_after: null}); await flush();
      assert.equal(sourceOptions(f).length, 1); assert.match(sourceOptions(f)[0].textContent, /Beta/);
      input.dispatchEvent({type: 'keydown', key: 'ArrowDown'});
      assert.equal(input.getAttribute('aria-activedescendant'), sourceOptions(f)[0].id);
      input.dispatchEvent({type: 'keydown', key: 'Enter'}); await flush();
      assert.deepEqual(sourceTags(f), ['#0002']);
      input.dispatchEvent({type: 'keydown', key: 'Escape'});
      assert.equal(input.getAttribute('aria-expanded'), 'false');
      assert.equal(f.requests.filter(r => r.body).length, 0);
    } finally { f.close(); }
  });
  await test('note search failure has an explicit retry and preserves selected tags', async () => {
    let fail = true;
    const f = fixture({responder: url => {
      if (url.pathname !== '/api/notebook/notes') return;
      if (url.searchParams.get('q') === 'Beta') {
        if (fail) return Promise.reject(Object.assign(Error('Search temporarily unavailable'), {status: 503}));
        return {notes: [sourceNote(second, 2, {title: 'Beta source note'})], next_after: null};
      }
      return {notes: [sourceNote()], next_after: null};
    }});
    try {
      await f.show('compose=bounty'); focusSources(f); await flush(); sourceOptions(f)[0].click(); await flush();
      fill(f.host, 'bounty-sources', 'Beta'); await searchPause(); await flush();
      assert.match(f.host.textContent, /Search temporarily unavailable/); assert.deepEqual(sourceTags(f), ['#0001']);
      fail = false; click(f.host, 'Try note search again'); await flush();
      assert.equal(sourceOptions(f).length, 1); sourceOptions(f)[0].click(); await flush();
      assert.deepEqual(sourceTags(f), ['#0001', '#0002']);
    } finally { f.close(); }
  });
  await test('source note paging appends results without duplicating notes', async () => {
    const f = fixture({responder: url => url.pathname === '/api/notebook/notes' ? url.searchParams.has('after')
      ? {notes: [sourceNote(), sourceNote(second, 2, {title: 'Beta source note'})], next_after: null}
      : {notes: [sourceNote()], next_after: 'next-source-page'} : undefined});
    try {
      await f.show('compose=bounty'); focusSources(f); await flush(); click(f.host, 'Show more notes'); await flush();
      assert.equal(sourceOptions(f).length, 2);
      assert.equal(f.requests.filter(r => r.url.pathname === '/api/notebook/notes').at(-1).url.searchParams.get('after'), 'next-source-page');
    } finally { f.close(); }
  });
  await test('editing resolves source ID labels without replacing an older selected revision', async () => {
    const latest = sourceNote(note, 8, {revision: 3, content_sha256: 'b'.repeat(64)});
    const f = fixture({responder: (url, body) => {
      if (url.pathname === '/api/notebook/notes') return {format: 'crucible-notebook-summary-index-v1', scope: 'all', include_curated: true, complete: true, notes: [latest], next_after: null};
      if (url.pathname === '/api/bounties/' + id || url.pathname.endsWith('/revisions')) return {bounty: task(id, {status: 'draft', capabilities: {edit: true}}), policy};
    }});
    try {
      await f.show('bounty=' + id + '&compose=bounty'); await flush();
      assert.deepEqual(sourceTags(f), ['#0008']);
      submit(f.host); await flush(); await f.routed();
      assert.deepEqual(JSON.parse(JSON.stringify(f.requests.find(r => r.body).body.terms.source_notes)), [pin]);
    } finally { f.close(); }
  });
  await test('late note results cannot restore a revoked picker', async () => {
    const pending = deferred();
    const f = fixture({responder: url => url.pathname === '/api/notebook/notes' ? pending.promise : undefined});
    await f.show('compose=bounty'); focusSources(f); await flush(); f.revoke();
    pending.resolve({notes: [sourceNote()], next_after: null}); await flush();
    assert.equal(f.host.textContent, ''); assert.equal(sourceOptions(f).length, 0);
  });
  await test('the picker enforces twelve sources and freezes tag removal for an uncertain save', async () => {
    const notes = Array.from({length: 13}, (_, index) => sourceNote('source-' + index, index + 1, {title: 'Source ' + (index + 1)}));
    const f = fixture({responder: (url, body) => url.pathname === '/api/notebook/notes' ? {notes, next_after: null}
      : url.pathname === '/api/bounties' && body ? Promise.reject(Object.assign(Error('Save reply lost'), {status: 0})) : undefined});
    try {
      await f.show('compose=bounty'); focusSources(f); await flush();
      for (let index = 0; index < 12; index++) { sourceOptions(f)[0].click(); await flush(); }
      assert.equal(sourceTags(f).length, 12); assert.equal(sourceOptions(f).length, 0);
      assert.match(f.host.textContent, /12 notes selected/);
      fill(f.host, 'bounty-title', 'Task'); fill(f.host, 'bounty-summary', 'Summary'); fill(f.host, 'bounty-completion', 'Criteria'); fill(f.host, 'bounty-verification', 'Plan');
      submit(f.host); await flush();
      const remove = f.host.querySelector('.bty-source-remove'); assert.equal(remove.disabled, true); remove.click();
      assert.equal(sourceTags(f).length, 12); assert.equal(f.host.querySelector('[name="bounty-sources"]').disabled, true);
      assert.ok(button(f.host, 'Retry the same request'));
    } finally { f.close(); }
  });
  await test('saving a private proposal makes no reservation; admission costs use a freshly confirmed version and policy', async () => {
    let draft;
    const f = fixture({responder: (url, body) => {
      if (url.pathname === '/api/bounties' && body) {draft = task(id, {...body.terms, terms: body.terms, status: 'draft', capabilities: {edit: true, request_admission: true, withdraw: true}}); return {bounty: draft, policy};}
      if (url.pathname === '/api/bounties') return {bounties: draft && url.searchParams.get('status') === 'mine' ? [draft] : [], pagination: {offset: 0, limit: 6, has_more: false}, policy};
      if (url.pathname === '/api/bounties/' + id && draft) return {bounty: draft, policy};
      if (url.pathname.endsWith('/admission-requests')) {draft = {...draft, status: 'pending', version: 2, capabilities: {withdraw: true}}; return {bounty: draft, policy};}
    }});
    try {
      await f.show('view=bounties&compose=bounty');
      fill(f.host, 'bounty-title', 'A real task-shaped fixture'); fill(f.host, 'bounty-summary', 'Bounded scope'); fill(f.host, 'bounty-completion', 'Exact criteria'); fill(f.host, 'bounty-verification', 'Independent evidence');
      submit(f.host); await flush(); await f.routed();
      assert.equal(f.requests.filter(r => r.body).length, 1);
      assert.equal(f.requests.find(r => r.body).path, '/api/bounties');
      assert.match(f.host.textContent, /Draft/);
      click(f.host, '← Your activity'); await f.routed();
      assert.match(f.host.querySelector('[data-bounty="' + id + '"]').textContent, /Draft/);
      // A direct task link also starts on the default open collection.
      await f.show('view=bounties&bounty=' + id + '&page=3');
      click(f.host, 'Submit for review'); await flush();
      assert.match(f.host.textContent, /Reserve 1 of your monthly bounty allowance/);
      assert.match(f.host.textContent, /original month/);
      assert.match(f.host.textContent, /100-point reward/);
      submit(f.host); await flush(); await f.routed();
      const request = f.requests.find(r => r.url.pathname.endsWith('/admission-requests'));
      assert.equal(request.body.policy_id, policy.id); assert.equal(request.body.confirm_cost_units, 1);
      assert.equal(request.body.expected_version, 1); assert.equal(request.body.terms_sha256, hash);
      assert.equal(new URLSearchParams(f.model.route().split('?')[1]).get('page'), null);
      click(f.host, '← Your activity'); await f.routed();
      assert.match(f.host.querySelector('[data-bounty="' + id + '"]').textContent, /Submitted for review/);
    } finally {f.close();}
  });
  await test('uncertain boost retries its exact payload and cannot silently become a second spend', async () => {
    let attempts = 0;
    const f = fixture({responder: (url, body) => {
      if (url.pathname.endsWith('/boosts')) {
        if (++attempts === 1) return Promise.reject(Object.assign(Error('Synthetic connection interruption'), {status: 0}));
        return {bounty: task(id, {version: 2, reward_points: 200}), policy};
      }
    }});
    try {
      await f.show('view=bounties&bounty=' + id); click(f.host, 'Boost this bounty'); await flush();
      submit(f.host); await flush();
      assert.ok(button(f.host, 'Retry the same request'));
      submit(f.host); await flush(); await f.routed();
      const writes = f.requests.filter(r => r.url.pathname.endsWith('/boosts'));
      assert.equal(new URLSearchParams(f.model.route().split('?')[1]).get('status'), null);
      assert.equal(writes.length, 2); assert.deepEqual(writes[0].body, writes[1].body);
      assert.equal(writes[0].body.units, 1); assert.equal(writes[0].body.policy_id, policy.id);
    } finally {f.close();}
  });
  await test('explicit action panels focus their heading and cancel returns to the trigger without refresh theft', async () => {
    const f = fixture({responder: url => url.pathname === '/api/bounties/' + id ? {bounty: task(id, {
      capabilities: {boost: true, request_admission: true, cancel: true, withdraw: true}}), policy} : undefined});
    try {
      await f.show('view=bounties&bounty=' + id);
      for (const [trigger, heading, confirmation] of [
        ['Boost this bounty', 'Confirm bounty boost', 'Confirm boost'],
        ['Submit for review', 'Request bounty admission', 'Submit for review'],
        ['Cancel bounty', 'Cancel bounty', 'Confirm cancellation'],
        ['Withdraw proposal', 'Withdraw bounty proposal', 'Confirm withdrawal']
      ]) {
        button(f.host, trigger).focus(); click(f.host, trigger); await flush();
        const panel = f.host.querySelector('.bty-action-panel');
        assert.equal(f.document.activeElement, panel.querySelector('h3'));
        assert.equal(f.document.activeElement.textContent, heading);
        const confirm = button(panel, confirmation); confirm.focus();
        await f.show('view=bounties&bounty=' + id);
        assert.equal(f.document.activeElement, confirm, 'Quiet reads must not steal action focus.');
        click(panel, 'Cancel');
        assert.equal(f.document.activeElement, button(f.host, trigger));
        assert.equal(f.host.querySelector('.bty-action-panel'), null);
      }
    } finally {f.close();}
  });
  await test('a rejection after a lost boost reply cannot mint a replacement spend', async () => {
    let attempts = 0;
    const f = fixture({responder: url => {
      if (url.pathname.endsWith('/boosts')) {
        attempts++;
        if (attempts < 3) return Promise.reject(Object.assign(Error('Synthetic interrupted retry'), {status: attempts === 1 ? 0 : 409}));
        return {bounty: task(id, {version: 2, reward_points: 200}), policy};
      }
    }});
    try {
      await f.show('view=bounties&bounty=' + id); click(f.host, 'Boost this bounty'); await flush();
      submit(f.host); await flush(); submit(f.host); await flush();
      assert.ok(button(f.host, 'Retry the same request'));
      submit(f.host); await flush(); await f.routed();
      const writes = f.requests.filter(r => r.url.pathname.endsWith('/boosts'));
      assert.equal(writes.length, 3);
      assert.deepEqual(writes[0].body, writes[1].body);
      assert.deepEqual(writes[0].body, writes[2].body);
    } finally {f.close();}
  });
  await test('a definite first rejection allows a deliberate new boost request', async () => {
    let attempts = 0;
    const f = fixture({responder: url => {
      if (url.pathname.endsWith('/boosts')) {
        if (++attempts === 1) return Promise.reject(Object.assign(Error('Synthetic stale version'), {status: 409}));
        return {bounty: task(id, {version: 2, reward_points: 200}), policy};
      }
    }});
    try {
      await f.show('view=bounties&bounty=' + id); click(f.host, 'Boost this bounty'); await flush();
      submit(f.host); await flush();
      assert.equal(button(f.host, 'Retry the same request'), undefined);
      submit(f.host); await flush(); await f.routed();
      const writes = f.requests.filter(r => r.url.pathname.endsWith('/boosts'));
      assert.equal(writes.length, 2);
      assert.notEqual(writes[0].body.idempotency_key, writes[1].body.idempotency_key);
    } finally {f.close();}
  });
  await test('a revoked component cannot paint old data, preserve drafts or perform writes', async () => {
    const pending = deferred();
    const f = fixture({responder: url => url.pathname === '/api/bounties' ? pending.promise : undefined});
    const loading = f.show('view=bounties'); await flush(); f.revoke();
    pending.resolve({bounties: [task()], pagination: {offset: 0, limit: 6, has_more: false}, policy}); await loading;
    assert.equal(f.host.textContent, '');
    await assert.rejects(f.client.boost(id, {units: 1}), error => error.status === 403);
    assert.equal(f.requests.filter(r => r.body).length, 0);
    assert.equal(f.listeners.get('beforeunload').length, 0);
  });
  await test('missing or denied detail has a retry and never substitutes another bounty', async () => {
    const f = fixture({responder: url => url.pathname === '/api/bounties/' + id ? Promise.reject(Object.assign(Error('Bounty unavailable'), {status: 404})) : undefined});
    try {await f.show('view=bounties&bounty=' + id); assert.match(f.host.textContent, /Bounty unavailable/); assert.ok(button(f.host, 'Try again')); assert.equal(f.host.querySelector('.bty-detail'), null);} finally {f.close();}
  });
  await test('review UI consumes separate server capabilities and never displays editable base awards', async () => {
    const f = fixture({reviewOnly: true, responder: url => url.pathname === '/api/bounties/' + id ? {bounty: task(id, {status: 'pending', capabilities: {review_admission: true}}), policy} : undefined});
    try {await f.show('section=bounties&bounty=' + id); assert.match(f.host.textContent, /Admission review/); assert.equal(f.host.querySelector('[name="admission-base"]'), null); assert.equal(button(f.host, 'Boost this bounty'), undefined);} finally {f.close();}
  });
  await test('invalidation stays passive before selection and after hiding; warming never shows a board', async () => {
    const f = fixture();
    try {
      f.model.invalidateReads(); assert.equal(f.requests.length, 0);
      assert.equal(f.model.route(), '#bounties');
      await f.show('view=bounties'); f.model.hide();
      const count = f.requests.length; f.model.invalidateReads(); await flush(); assert.equal(f.requests.length, count);
    } finally {f.close();}
  });
  await test('transport invalidation during a write retains its successful receipt and navigation', async () => {
    let f;
    f = fixture({responder: (url, body) => {
      if (url.pathname === '/api/bounties' && body) {
        f.model.invalidateReads();
        return {bounty: task(id, {status: 'draft', capabilities: {edit: true}}), policy};
      }
    }});
    try {
      await f.show('view=bounties&compose=bounty');
      fill(f.host, 'bounty-title', 'Task'); fill(f.host, 'bounty-summary', 'Summary'); fill(f.host, 'bounty-completion', 'Criteria'); fill(f.host, 'bounty-verification', 'Plan');
      submit(f.host); await flush(); await f.routed();
      assert.ok(f.routes.at(-1).includes('bounty=' + id));
      assert.equal(f.requests.filter(r => r.body).length, 1);
      assert.match(f.host.textContent, /Private bounty draft saved/);
    } finally {f.close();}
  });
  await test('a successful hidden save shows its receipt when returning instead of duplicating a proposal', async () => {
    const pending = deferred();
    const f = fixture({responder: (url, body) => url.pathname === '/api/bounties' && body ? pending.promise : undefined});
    try {
      await f.show('view=bounties&compose=bounty');
      fill(f.host, 'bounty-title', 'Task'); fill(f.host, 'bounty-summary', 'Summary'); fill(f.host, 'bounty-completion', 'Criteria'); fill(f.host, 'bounty-verification', 'Plan');
      submit(f.host); await flush(); await f.show('view=bounties');
      pending.resolve({bounty: task(id, {status: 'draft'}), policy}); await flush();
      await f.show('view=bounties&compose=bounty');
      assert.match(f.host.textContent, /saved while you were browsing/);
      assert.equal(button(f.host, 'Save private draft'), undefined);
      click(f.host, 'View bounty'); await f.routed();
      assert.equal(new URLSearchParams(f.model.route().split('?')[1]).get('status'), 'mine');
      assert.equal(f.requests.filter(r => r.body).length, 1);
    } finally {f.close();}
  });
  await test('an uncertain boost keeps its identity when a new task version arrives before retry', async () => {
    let version = 1, attempts = 0;
    const f = fixture({responder: (url, body) => {
      if (url.pathname.endsWith('/boosts')) {
        if (++attempts === 1) {version = 2; return Promise.reject(Object.assign(Error('Reply lost'), {status: 0}));}
        return {bounty: task(id, {version}), policy};
      }
      if (url.pathname === '/api/bounties/' + id) return {bounty: task(id, {version}), policy};
    }});
    try {
      await f.show('view=bounties&bounty=' + id); click(f.host, 'Boost this bounty'); await flush();
      submit(f.host); await flush();
      await f.show('view=bounties'); await f.show('view=bounties&bounty=' + id);
      click(f.host, 'Boost this bounty'); await flush();
      assert.ok(button(f.host, 'Retry the same request')); submit(f.host); await flush(); await f.routed();
      const writes = f.requests.filter(r => r.url.pathname.endsWith('/boosts'));
      assert.equal(writes.length, 2); assert.deepEqual(writes[0].body, writes[1].body); assert.equal(writes[1].body.expected_version, 1);
    } finally {f.close();}
  });
  await test('withdrawn exact notes are tombstones and do not become replacement links', async () => {
    const f = fixture({responder: url => url.pathname === '/api/bounties/' + id ? {bounty: task(id, {sources: [{...pin, unavailable: true}]}), policy} : undefined});
    try {
      await f.show('view=bounties&bounty=' + id + '&detail=notes');
      const ref = f.host.querySelector('.bty-note-ref'); assert.match(ref.textContent, /Unavailable note/); assert.equal(ref.getAttribute('href'), null);
    } finally {f.close();}
  });
  await test('missing reward-policy terms fail closed before showing a spending action', async () => {
    const f = fixture({responder: url => url.pathname === '/api/bounties/' + id ? {bounty: task(), policy:{status:'ready',id:'incomplete',monthly_units:10}} : undefined});
    try {
      await f.show('view=bounties&bounty=' + id);
      assert.equal(button(f.host, 'Boost this bounty'), undefined);
      assert.match(f.host.textContent, /terms could not be verified/);
    } finally {f.close();}
  });
  await test('a valid zero monthly allowance pauses new allocation without hiding the board', async () => {
    const f = fixture({responder: url => url.pathname === '/api/bounties' ? {bounties:[task()],pagination:{offset:0,limit:6,has_more:false},policy:{...policy,monthly_units:0}} : undefined});
    try {await f.show('view=bounties'); assert.equal(f.host.querySelectorAll('[data-bounty]').length, 1);} finally {f.close();}
  });
  await test('agent prompt names the exact task and requires separate explicit allowance authority', async () => {
    const f = fixture(); let copied;
    f.window.navigator.clipboard = {writeText: async value => copied = value};
    try {
      await f.show('view=bounties'); click(f.host, 'Copy agent prompt'); await flush();
      assert.match(copied, /bounty list/); assert.match(copied, /--scope bounties:read --scope bounties:write/);
      assert.match(copied, /separately approved bounties:spend scope/); assert.match(copied, /--confirm-allowance/);
      await f.show('view=bounties&bounty=' + id);
      button(f.host.querySelector('.bty-actions'), 'Copy agent prompt').click(); await flush();
      assert.ok(copied.includes('bounty show ' + id));
      assert.equal(f.requests.filter(r => r.body).length, 0);
    } finally {f.close();}
  });
  complete = true; console.log(passed + ' bounty production frontend checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

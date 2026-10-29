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
  await test('saving a private proposal makes no reservation; admission costs use a freshly confirmed version and policy', async () => {
    let draft;
    const f = fixture({responder: (url, body) => {
      if (url.pathname === '/api/bounties' && body) {draft = task(id, {...body.terms, terms: body.terms, status: 'draft', capabilities: {edit: true, request_admission: true, withdraw: true}}); return {bounty: draft, policy};}
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
      click(f.host, 'Submit for review'); await flush();
      assert.match(f.host.textContent, /Reserve 1 of your monthly bounty allowance/);
      assert.match(f.host.textContent, /original month/);
      assert.match(f.host.textContent, /100-point reward/);
      submit(f.host); await flush(); await f.routed();
      const request = f.requests.find(r => r.url.pathname.endsWith('/admission-requests'));
      assert.equal(request.body.policy_id, policy.id); assert.equal(request.body.confirm_cost_units, 1);
      assert.equal(request.body.expected_version, 1); assert.equal(request.body.terms_sha256, hash);
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
      assert.equal(writes.length, 2); assert.deepEqual(writes[0].body, writes[1].body);
      assert.equal(writes[0].body.units, 1); assert.equal(writes[0].body.policy_id, policy.id);
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
      assert.equal(f.model.route(), '#notebook?view=bounties');
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
  complete = true; console.log(passed + ' bounty production frontend checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

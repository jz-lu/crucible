const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'researcher-a', handle: 'alice'};
const blank = () => ({affiliation: '', position: '', display_name: '', website: '', revision: 0, updated_at: null});
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};
const state = app => vm.runInContext('state', app.sandbox);
const editor = app => vm.runInContext('researcherEditor', app.sandbox);
const ownReads = app => app.requests.filter(r => r.url.pathname === '/api/me/profile' && r.options.method === 'GET');
async function boot({handle = 'alice', initial = blank(), ownerRead, write} = {}) {
  let saved = initial;
  const puts = [];
  const app = await createApp({schemes: [], user, hash: '#person/' + handle, responder: (url, options, envelope) => {
    if (url.pathname.startsWith('/api/people/')) return envelope({person: {handle: url.pathname.split('/').at(-1), points: 0,
      accepted_proposal_count: 0, records: [], recent_recoveries: [], researcher_profile: saved}});
    if (url.pathname === '/api/me/profile') {
      if (options.method === 'PUT') {
        const payload = JSON.parse(options.body); puts.push(payload);
        if (write) return write(payload, envelope, app);
        saved = {...payload, revision: saved.revision + 1, updated_at: 100};
        return envelope({profile: saved});
      }
      return ownerRead ? ownerRead(envelope, saved) : envelope({profile: saved});
    }
  }});
  // The shared DOM double predates removable beforeunload ownership.
  app.window.removeEventListener = (type, callback) => app.window.listeners.set(type, (app.window.listeners.get(type) || []).filter(fn => fn !== callback));
  let key = 0; app.sandbox.crypto.randomUUID = () => `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`;
  return {app, puts, saved: value => {saved = value;}};
}
async function open(app) {app.$('#edit-researcher-profile').click(); await flush();}
function input(app, selector, value) {app.$(selector).value = value; app.$(selector).dispatchEvent({type: 'input', bubbles: true});}
async function save(app) {app.$('#researcher-profile-form').dispatchEvent({type: 'submit'}); await flush();}
function http(app, status, code) {
  const original = app.sandbox.fetch;
  app.sandbox.fetch = (path, options) => options.method === 'PUT' ? Promise.resolve({ok: false, status,
    json: async () => ({detail: 'Saved details changed.', code})}) : original(path, options);
  return () => {app.sandbox.fetch = original;};
}
let passed = 0;
async function test(name, run) {await run(); console.log('PASS ' + name); passed++;}
(async () => {
  await test('header marks the actual destination: own profile, another profile, leaderboard and about', async () => {
    const {app} = await boot();
    const selected = () => ['challenge', 'people', 'notebook', 'research', 'about'].filter(kind => app.$('#nav-' + kind).classList.contains('selected'));
    assert.deepEqual(selected(), []);
    assert.equal(app.$('#nav-people').getAttribute('aria-current'), null);
    assert.equal(app.$('#account-handle').getAttribute('aria-current'), 'page');
    await app.navigate('#person/bob');
    assert.deepEqual(selected(), []);
    assert.equal(app.$('#account-handle').getAttribute('aria-current'), null);
    await app.navigate('#people');
    assert.deepEqual(selected(), ['people']);
    assert.equal(app.$('#account-handle').classList.contains('selected'), false);
    await app.navigate('#about');
    assert.deepEqual(selected(), ['about']);
    assert.equal(app.$('#nav-people').getAttribute('aria-current'), null);
    await app.navigate('#person/alice');
    assert.deepEqual(selected(), []);
    assert.equal(app.$('#account-handle').classList.contains('selected'), true);
  });
  await test('member detail renders safe optional text without replacing the username or fetching owner data', async () => {
    const {app} = await boot({handle: 'bob', initial: {...blank(), display_name: '<img src=x>', affiliation: 'Example Institute', position: 'Researcher', website: 'https://www.example.org/a'}});
    assert.equal(app.$('#person-title').textContent, 'bob');
    const summary = app.$('#researcher-profile-summary');
    assert.match(summary.textContent, /<img src=x>Example Institute · Researcher/);
    assert.equal(summary.querySelector('img'), null);
    assert.equal(summary.querySelector('a').href, 'https://www.example.org/a');
    assert.equal(summary.querySelector('a').rel, 'noopener noreferrer nofollow');
    assert.match(summary.textContent, /Self-reported/);
    assert.equal(app.$('#edit-researcher-profile').hidden, true); assert.equal(ownReads(app).length, 0);
  });
  await test('empty metadata adds no blank biography and fresh owner read starts only on explicit Edit', async () => {
    const {app} = await boot();
    assert.equal(app.$('#researcher-profile-summary').hidden, true); assert.equal(ownReads(app).length, 0);
    await open(app); assert.equal(ownReads(app).length, 1);
    assert.equal(app.$('#researcher-profile-editor').hidden, false);
    assert.equal(app.$('#researcher-affiliation').disabled, false);
  });
  await test('public and authorized cold profile bodies reserve only quiet space, without premature score controls', async () => {
    const pending = deferred();
    const app = await createApp({schemes: [], user, hash: '#person/alice', responder: (url, options, envelope) =>
      url.pathname === '/api/session' ? envelope({user, csrf_token:'fixture-csrf', auth:{github_enabled:true}}) : url.pathname === '/api/people/alice' ? pending.promise : undefined});
    const preview = app.$('#person-content').querySelector('.person-preview');
    assert.ok(preview); assert.equal(preview.textContent, ''); assert.equal(preview.children.length, 0);
    assert.equal(preview.getAttribute('aria-hidden'), 'true'); assert.equal(app.$('#person-title').textContent, 'alice');
    const initial = app.$('[data-pending-layout="profile"]'); assert.equal(initial.textContent, ''); assert.equal(initial.children.length, 0);
    assert.equal(app.$('#account-sign-in').hidden, false); assert.equal(app.$('#agent-connections').hidden, false);
    pending.resolve(app.envelope({person: {handle:'alice',points:0,accepted_proposal_count:0,records:[],recent_recoveries:[],researcher_profile:{...blank(),affiliation:'Example Institute'}}})); await flush();
    assert.equal(app.$('#person-content').querySelector('.person-preview'), null);
    assert.match(app.$('#researcher-profile-summary').textContent, /Example Institute/);
  });
  await test('unchanged cached profile restores metadata after navigation and preserves focused metadata on refresh', async () => {
    const {app} = await boot({initial: {...blank(), affiliation: 'Example Institute', website: 'https://example.org', revision: 1}});
    const stats = app.$('#person-content').firstChild;
    await app.navigate('#about'); assert.equal(app.$('#researcher-profile-summary').hidden, true);
    await app.navigate('#person/alice');
    assert.equal(app.$('#person-content').firstChild, stats, 'The unchanged scores still reuse their original DOM');
    assert.equal(app.$('#researcher-profile-summary').hidden, false);
    assert.match(app.$('#researcher-profile-summary').textContent, /Example Institute/);
    const link = app.$('#researcher-profile-summary').querySelector('a'); link.focus();
    await app.sandbox.loadPerson('alice', {preserve: true});
    assert.equal(app.$('#researcher-profile-summary').querySelector('a'), link);
    assert.equal(app.document.activeElement, link);
  });
  await test('a forbidden profile refresh removes cached researcher metadata with its score reader', async () => {
    const {app} = await boot({initial: {...blank(), affiliation: 'Example Institute', revision: 1}});
    const original = app.sandbox.fetch;
    app.sandbox.fetch = (path, options) => path.startsWith('/api/people/') ? Promise.resolve({ok:false,status:404,json:async()=>({detail:'Profile unavailable.'})}) : original(path, options);
    await app.sandbox.loadPerson('alice', {preserve: true});
    assert.equal(app.$('#researcher-profile-summary').hidden, true);
    assert.equal(app.$('#researcher-profile-summary').textContent, '');
  });
  await test('typed draft and input focus survive quiet score refresh and ordinary navigation', async () => {
    const {app, saved} = await boot(); await open(app);
    const inputNode = app.$('#researcher-affiliation'); input(app, '#researcher-affiliation', 'Draft institution'); inputNode.focus();
    saved({...blank(), display_name: 'Saved elsewhere'});
    await app.sandbox.loadPerson('alice', {preserve: true});
    assert.equal(app.document.activeElement, inputNode); assert.equal(inputNode.value, 'Draft institution');
    await app.navigate('#about'); assert.equal(app.$('#researcher-profile-editor').hidden, true);
    await app.navigate('#person/alice'); assert.equal(inputNode.value, 'Draft institution');
    assert.equal(app.$('#edit-researcher-profile').textContent, 'Continue editing');
    assert.equal(ownReads(app).length, 1);
  });
  await test('Save is explicit, CSRF-bound, duplicate guarded and sends only the exact profile contract', async () => {
    const gate = deferred();
    const {app, puts} = await boot({write: () => gate.promise}); await open(app);
    input(app, '#researcher-affiliation', ' Institute '); input(app, '#researcher-position', ' Fellow ');
    app.$('#researcher-profile-form').dispatchEvent({type: 'submit'});
    app.$('#researcher-profile-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(puts.length, 1); assert.deepEqual(Object.keys(puts[0]).sort(), ['affiliation','display_name','expected_revision','idempotency_key','position','website']);
    assert.equal(puts[0].affiliation, 'Institute'); assert.equal(puts[0].expected_revision, 0);
    const request = app.requests.find(r => r.options.method === 'PUT'); assert.equal(request.options.headers['X-CSRF-Token'], 'test-csrf');
    assert.equal(app.$('#researcher-affiliation').disabled, true);
    gate.resolve(app.envelope({profile: {...blank(), affiliation: 'Institute', position: 'Fellow', revision: 1}})); await flush();
    assert.equal(app.$('#researcher-profile-status').textContent, 'Profile saved.'); assert.equal(editor(app).guarded, false);
  });
  await test('transport uncertainty is never auto-replayed and an explicit retry uses the identical key and payload', async () => {
    let attempts = 0;
    const {app, puts} = await boot({write: (payload, envelope) => {
      if (++attempts === 1) throw new TypeError('interrupted');
      return envelope({profile: {...blank(), affiliation: payload.affiliation, revision: 1}});
    }}); await open(app); input(app, '#researcher-affiliation', 'Institute'); await save(app);
    assert.equal(puts.length, 1); assert.equal(app.$('#save-researcher-profile').textContent, 'Retry save');
    assert.equal(app.$('#researcher-affiliation').disabled, true); assert.equal(editor(app).guarded, true);
    app.$('#close-researcher-profile').click(); await open(app); assert.equal(ownReads(app).length, 1);
    await save(app); assert.deepEqual(puts[1], puts[0]); assert.equal(editor(app).pending, null);
  });
  await test('a revision conflict refreshes saved context, retains the draft and requires a new explicit save', async () => {
    const {app, saved, puts} = await boot(); await open(app);
    input(app, '#researcher-affiliation', 'My draft');
    saved({...blank(), affiliation: 'Other editor', revision: 4});
    const restore = http(app, 409, 'profile_revision_conflict'); await save(app);
    assert.equal(app.$('#researcher-affiliation').value, 'My draft');
    assert.match(app.$('#researcher-profile-conflict').textContent, /Other editor/);
    assert.equal(app.$('#save-researcher-profile').textContent, 'Save my changes');
    assert.equal(ownReads(app).length, 2); assert.equal(puts.length, 0);
    restore(); await save(app); assert.equal(puts[0].expected_revision, 4);
    assert.match(puts[0].idempotency_key, /000000000002$/);
  });
  await test('wrong revision or mismatched successful response cannot confirm the write or replace its draft', async () => {
    for (const response of [{...blank(), affiliation: 'Draft', revision: 0}, {...blank(), affiliation: 'Other value', revision: 1}]) {
      const {app, puts} = await boot({write: (_, envelope) => envelope({profile: response})}); await open(app);
      input(app, '#researcher-affiliation', 'Draft'); await save(app);
      assert.equal(app.$('#researcher-affiliation').value, 'Draft'); assert.equal(app.$('#researcher-profile-status').textContent, '');
      assert.equal(editor(app).pending.idempotency_key, puts[0].idempotency_key);
      assert.equal(app.$('#save-researcher-profile').textContent, 'Retry save');
      assert.match(app.$('#researcher-profile-error').textContent, /could not be confirmed/);
    }
  });
  await test('failed conflict refresh keeps draft and blocks a stale revision write until explicit refresh succeeds', async () => {
    let failRead = false;
    const {app, saved} = await boot({ownerRead: (envelope, profile) => {if (failRead) throw new TypeError('offline'); return envelope({profile});}});
    await open(app); input(app, '#researcher-affiliation', 'Draft'); failRead = true;
    const restore = http(app, 409, 'profile_revision_conflict'); await save(app);
    assert.equal(app.$('#save-researcher-profile').disabled, true); assert.equal(app.$('#researcher-affiliation').value, 'Draft');
    assert.equal(app.$('#reload-researcher-profile').textContent, 'Retry refresh');
    failRead = false; saved({...blank(), affiliation: 'Latest', revision: 2}); restore();
    app.$('#reload-researcher-profile').click(); await flush();
    assert.equal(app.$('#researcher-affiliation').value, 'Draft'); assert.equal(app.$('#save-researcher-profile').disabled, false);
  });
  await test('account changes clear drafts and reject a late owner response', async () => {
    const gate = deferred(); const {app} = await boot({ownerRead: () => gate.promise});
    app.$('#edit-researcher-profile').click(); await flush();
    app.sandbox.setSession(app.envelope({user: {id: 'other', handle: 'bob'}, csrf_token: 'other-csrf'}));
    gate.resolve(app.envelope({profile: {...blank(), affiliation: 'Private old draft', revision: 2}})); await flush();
    assert.equal(app.$('#researcher-affiliation').value, ''); assert.equal(app.$('#researcher-profile-editor').hidden, true);
    assert.equal(app.$('#researcher-profile-summary').textContent, ''); assert.equal(editor(app).guarded, false);
  });
  await test('late save after route departure cannot paint another profile and retains its exact retry', async () => {
    const gate = deferred(); const {app, puts} = await boot({write: () => gate.promise}); await open(app);
    input(app, '#researcher-affiliation', 'Saved maybe'); await save(app); await app.navigate('#person/bob');
    gate.resolve(app.envelope({profile: {...blank(), affiliation: 'Saved maybe', revision: 1}})); await flush();
    assert.equal(app.$('#person-title').textContent, 'bob'); assert.equal(app.$('#researcher-profile-summary').hidden, true);
    assert.equal(editor(app).pending.idempotency_key, puts[0].idempotency_key);
  });
  await test('same-account session rotation preserves draft but retires the old save response', async () => {
    const gate = deferred(); const {app} = await boot({write: () => gate.promise}); await open(app);
    input(app, '#researcher-affiliation', 'Draft'); await save(app);
    app.sandbox.setSession(app.envelope({user, csrf_token: 'rotated'}));
    gate.resolve(app.envelope({profile: {...blank(), affiliation: 'Draft', revision: 1}})); await flush();
    assert.equal(app.$('#researcher-profile-editor').hidden, true); assert.equal(app.$('#researcher-profile-status').textContent, '');
    assert.equal(app.$('#researcher-affiliation').value, 'Draft'); assert.ok(editor(app).pending);
  });
  await test('unsafe URLs and overlong or multiline fields cannot issue a save', async () => {
    const {app, puts} = await boot(); await open(app);
    for (const value of ['javascript:alert(1)', 'https://user:pass@example.org', 'https://example.org/ path', 'https:\\example.org']) {
      input(app, '#researcher-website', value); await save(app); assert.equal(puts.length, 0);
    }
    input(app, '#researcher-website', '');
    for (const value of ['x'.repeat(201), 'two\nlines']) {input(app, '#researcher-affiliation', value); await save(app); assert.equal(puts.length, 0);}
  });
  await test('beforeunload guard exists only for an unsaved draft and explicit discard preserves cancellation', async () => {
    const {app} = await boot(); await open(app); assert.equal(editor(app).guarded, false);
    input(app, '#researcher-affiliation', 'Draft'); assert.equal(editor(app).guarded, true);
    let event = {type: 'beforeunload'}; app.window.dispatchEvent(event); assert.equal(event.defaultPrevented, true);
    app.window.confirm = () => false; app.$('#reload-researcher-profile').click(); await flush(); assert.equal(app.$('#researcher-affiliation').value, 'Draft');
    app.window.confirm = () => true; app.$('#reload-researcher-profile').click(); await flush(); assert.equal(editor(app).guarded, false);
    event = {type: 'beforeunload'}; app.window.dispatchEvent(event); assert.notEqual(event.defaultPrevented, true);
  });
  console.log(`${passed} researcher profile frontend checks passed.`);
})().catch(error => {console.error(error); process.exitCode = 1;});

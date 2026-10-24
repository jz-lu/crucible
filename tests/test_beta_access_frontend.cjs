const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'beta-user', handle: 'alice'};
const scheme = {id: 'fixture', version: '1', name: 'Private scheme', next_profile_id: 'tier-1',
  profiles: [{id: 'tier-1', instance_size: 1, shared_secret_bytes: 32, parameters: {}, encoding: {shared_secret_bytes: 32}, correctness: {}}],
  size_parameter: {meaning: 'Fixture'}, protocol: {}, profile_metrics: {'tier-1': {public_key_bytes: 10}}};
const access = status => ({mode: 'beta', status, allowed: status === 'active'});
const readState = app => vm.runInContext('state', app.sandbox);
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const paths = app => app.requests.map(request => request.url.pathname);
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }

(async () => {
  await test('bootstrap waits for session before requesting any protected data', async () => {
    const gate = deferred();
    const app = await createApp({schemes: [scheme], responder: url => url.pathname === '/api/session' ? gate.promise : undefined});
    assert.deepEqual(paths(app), ['/api/session']);
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.$('.primary-nav').hidden, true);
    gate.resolve(app.envelope({user: null, csrf_token: 'csrf', access: access('anonymous')}));
    await flush();
    assert.deepEqual(paths(app), ['/api/session']);
    assert.match(app.$('#access-title').textContent, /Private beta/);
    assert.equal(app.$('#register-mode').hidden, true);
    assert.equal(app.document.querySelectorAll('script').some(script => /notebook/.test(script.getAttribute('src') || '')), false);
    assert.equal(app.document.querySelectorAll('link').some(link => /notebook/.test(link.getAttribute('href') || '')), false);
  });

  await test('pending and revoked deep links stay gated, including notebook and connection routes', async () => {
    for (const status of ['pending', 'revoked', 'suspended']) for (const hash of ['#notebook', '#people', '#connect?code=ABCD-EFGH']) {
      const app = await createApp({schemes: [scheme], user, hash, responder: (url, options, envelope) =>
        url.pathname === '/api/session' ? envelope({user, csrf_token: 'csrf', access: access(status)}) : undefined});
      assert.deepEqual(paths(app), ['/api/session']);
      assert.equal(app.$('#access-workspace').hidden, false);
      assert.equal(app.$('#account-handle').hidden, true);
      assert.equal(app.$('#history-button').hidden, true);
      assert.equal(readState(app).returnRoute, hash);
      await app.sandbox.synchronizeSession();
      assert.deepEqual(paths(app), ['/api/session', '/api/session']);
      assert.equal(app.document.querySelectorAll('script').some(script => /notebook/.test(script.getAttribute('src') || '')), false);
    }
  });

  await test('invitation signup verifies the fixed mailbox before catalogue access', async () => {
    let status = 'anonymous', signedIn = false;
    const app = await createApp({schemes: [scheme], hash: '#join?token=invitation_token', responder: (url, options, envelope) => {
      const body = options.body && JSON.parse(options.body);
      if (url.pathname === '/api/session') return envelope({user: signedIn ? user : null, csrf_token: 'csrf', access: access(status)});
      if (url.pathname === '/api/auth/register') {
        assert.deepEqual(body, {handle: 'alice', password: 'a long test password', invitation_token: 'invitation_token'});
        signedIn = true; status = 'pending';
        return envelope({user, csrf_token: 'csrf', access: access(status)});
      }
      if (url.pathname === '/api/access/invitations/verify/start') {
        assert.deepEqual(body, {token: 'invitation_token'});
        assert.equal(options.headers['X-CSRF-Token'], 'csrf');
        return {masked_recipient: 'a***@example.org', expires_at: 2000000000};
      }
      if (url.pathname === '/api/access/invitations/redeem') {
        assert.deepEqual(body, {token: 'invitation_token', code: '123456'});
        status = 'active'; return {ok: true};
      }
    }});
    assert.equal(app.$('#access-register').hidden, false);
    app.$('#access-register').click();
    app.$('#auth-handle').value = 'alice'; app.$('#auth-password').value = 'a long test password';
    app.$('#auth-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.$('#access-verification').hidden, false);
    assert.equal(paths(app).includes('/api/schemes'), false);
    app.$('#access-send-code').click(); await flush();
    assert.match(app.$('#access-delivery').textContent, /a\*\*\*@example.org/);
    assert.equal(app.$('#access-code-form').hidden, false);
    app.$('#access-code').value = '123456'; app.$('#access-code-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.location.hash, '#challenge');
    assert.equal(app.$('#access-workspace').hidden, true);
    assert.match(app.$('#kem-catalogue').textContent, /Private scheme/);
    assert.equal(readState(app).invitationToken, null);
    assert.equal(paths(app).filter(path => path === '/api/schemes').length, 1);
  });

  await test('existing member sign-in resumes the requested route', async () => {
    let signedIn = false;
    const app = await createApp({schemes: [scheme], hash: '#scheme/fixture/1', responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return envelope({user: null, csrf_token: 'csrf', access: access('anonymous')});
      if (url.pathname === '/api/auth/login') { signedIn = true; return envelope({user, csrf_token: 'csrf', access: access('active')}); }
    }});
    app.$('#access-sign-in').click(); app.$('#auth-handle').value = 'alice'; app.$('#auth-password').value = 'a long test password';
    app.$('#auth-form').dispatchEvent({type: 'submit'}); await flush();
    assert.ok(signedIn);
    assert.equal(app.location.hash, '#scheme/fixture/1');
    assert.equal(app.$('#overview-workspace').hidden, false);
  });

  await test('sign-out discards protected state and reloads a clean shell once', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#challenge', responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return envelope({user, csrf_token: 'csrf', access: access('active')});
      if (url.pathname === '/api/auth/logout') return envelope({user: null, csrf_token: 'anon', access: access('anonymous')});
    }});
    let reloads = 0; app.window.location.reload = () => { reloads++; };
    app.$('#logout-button').click(); await flush();
    assert.equal(reloads, 1);
    assert.equal(readState(app).schemes.length, 0);
    assert.equal(app.$('#kem-catalogue').textContent, '');
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.$('#access-workspace').hidden, false);
    app.sandbox.setSession(app.envelope({user: null, access: access('anonymous')}));
    assert.equal(reloads, 1);
  });

  await test('revocation invalidates an in-flight catalogue response and stops round polling', async () => {
    let status = 'active';
    const app = await createApp({schemes: [scheme], user, hash: '#challenge', responder: (url, options, envelope) =>
      url.pathname === '/api/session' ? envelope({user, csrf_token: 'csrf', access: access(status)}) : undefined});
    const gate = deferred(), original = app.sandbox.fetch;
    app.sandbox.fetch = (url, options) => url.startsWith('/api/schemes') ? gate.promise : original(url, options);
    const pending = app.sandbox.refreshProfiles();
    status = 'revoked';
    await app.sandbox.synchronizeSession();
    gate.resolve({ok: true, status: 200, json: async () => app.envelope({schemes: [scheme]})});
    assert.equal(await pending, false);
    assert.equal(readState(app).schemes.length, 0);
    const count = app.requests.length;
    app.sandbox.updateClock(); await app.sandbox.refreshRound();
    assert.equal(app.requests.length, count);
  });

  await test('challenge generation timeout exceeds ordinary reads', async () => {
    const app = await createApp({schemes: [scheme]});
    const deadlines = []; app.sandbox.setTimeout = (callback, delay) => { deadlines.push(delay); return 1; };
    app.sandbox.fetch = async () => ({ok: true, status: 200, json: async () => ({})});
    await app.sandbox.api('/api/challenges', {profile_id: 'tier-1'});
    await app.sandbox.api('/api/schemes');
    assert.deepEqual(deadlines, [30000, 20000]);
  });
  console.log(`\n${passed} private beta frontend checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../web/bootstrap.js'), 'utf8');
const user = {id: 'fixture-user', handle: 'alice'};
const session = (envelope, status = 'active') => envelope({user: status === 'anonymous' ? null : user,
  csrf_token: 'csrf', access: {mode: 'beta', status, allowed: status === 'active'}});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const paths = app => app.requests.map(request => request.url.pathname);
function isolated(fetch) {
  const timers = new Map(); let next = 1;
  const sandbox = {window: {}, fetch, AbortController,
    setTimeout(callback, delay) { assert.equal(delay, 20000); const id = next++; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); }};
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return {sandbox, timers};
}
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('the small external async script starts a same-origin no-store session read before any UI dependency', async () => {
    const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
    assert.match(html, /<script src="\/bootstrap\.js" async><\/script>/);
    assert.ok(html.indexOf('/bootstrap.js') < html.indexOf('/vendor/katex/katex.min.css'));
    assert.ok(html.indexOf('/bootstrap.js') < html.indexOf('/app.js'));
    const calls = [], gate = deferred();
    const {sandbox, timers} = isolated((url, options) => { calls.push({url, options}); return gate.promise; });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/session');
    assert.equal(calls[0].options.method, 'GET');
    assert.equal(calls[0].options.credentials, 'same-origin');
    assert.equal(calls[0].options.cache, 'no-store');
    assert.deepEqual({...calls[0].options.headers}, {Accept: 'application/json'});
    // Re-evaluation while pending cannot duplicate the initial request.
    vm.runInContext(source, sandbox);
    assert.equal(calls.length, 1);
    gate.resolve({ok: true}); await sandbox.window.CrucibleSessionBootstrap.response;
    assert.equal(timers.size, 1, 'deadline remains until API body handling finishes');
    sandbox.window.CrucibleSessionBootstrap.finish();
    assert.equal(timers.size, 0);
  });
  await test('bootstrap-first initialization consumes one response and subsequent checks fetch fresh cookies', async () => {
    let status = 'active';
    const app = await createApp({schemes: [], hash: '#about', bootstrap: true, responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return session(envelope, status);
    }});
    assert.deepEqual(paths(app), ['/api/session']);
    assert.equal(app.$('#about-workspace').hidden, false);
    assert.deepEqual({...app.window.CrucibleSessionBootstrap}, {consumed: true});
    status = 'revoked';
    await app.sandbox.synchronizeSession();
    assert.deepEqual(paths(app), ['/api/session', '/api/session']);
    assert.equal(app.$('#access-workspace').hidden, false);
  });
  await test('app-first initialization marks fallback before a late bootstrap download can duplicate it', async () => {
    const gate = deferred();
    const app = await createApp({schemes: [], hash: '#about', responder: url => url.pathname === '/api/session' ? gate.promise : undefined});
    assert.deepEqual(paths(app), ['/api/session']);
    vm.runInContext(source, app.sandbox);
    assert.deepEqual(paths(app), ['/api/session']);
    gate.resolve(session(app.envelope)); await flush();
    assert.equal(app.$('#about-workspace').hidden, false);
  });
  await test('a pending bootstrap cannot expose the workspace or start protected fetches', async () => {
    const gate = deferred();
    const app = await createApp({schemes: [], hash: '#notebook', bootstrap: true,
      responder: url => url.pathname === '/api/session' ? gate.promise : undefined});
    assert.deepEqual(paths(app), ['/api/session']);
    assert.equal(app.$('#access-workspace').hidden, false);
    assert.equal(app.$('#notebook-workspace').hidden, true);
    assert.equal(app.document.querySelectorAll('script').some(script => (script.src || '').startsWith('/notebook/')), false);
    gate.resolve(session(app.envelope, 'anonymous')); await flush();
    assert.deepEqual(paths(app), ['/api/session']);
    assert.match(app.$('#access-title').textContent, /Private beta/);
  });
  await test('failed early network read uses the ordinary error UI and a fresh retry succeeds', async () => {
    let reads = 0;
    const app = await createApp({schemes: [], hash: '#about', bootstrap: true, responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') {
        if (reads++ === 0) throw new Error('Synthetic failure');
        return session(envelope);
      }
    }});
    assert.equal(app.$('#global-message-text').textContent, '');
    assert.match(app.$('#access-description').textContent, /Access could not be checked/);
    assert.equal(app.$('#access-retry').textContent, 'Try again');
    await app.sandbox.initialize();
    assert.equal(reads, 2);
    assert.equal(app.$('#about-workspace').hidden, false);
    assert.equal(app.$('#global-message-text').textContent, '');
  });
  await test('the early deadline aborts a stalled fetch without an unhandled rejection', async () => {
    const {sandbox, timers} = isolated((url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('Synthetic abort')));
    }));
    const boot = sandbox.window.CrucibleSessionBootstrap;
    [...timers.values()][0](); await flush();
    assert.equal(boot.controller.signal.aborted, true);
    await assert.rejects(boot.response, /Synthetic abort/);
    boot.finish(); assert.equal(timers.size, 0);
  });
  await test('bootstrap HTTP and body failures retain ordinary API classification and clear their deadline', async () => {
    const app = await createApp({schemes: [], hash: '#about'});
    for (const mode of ['http', 'body', 'timeout']) {
      let finished = 0; const controller = new AbortController();
      if (mode === 'timeout') controller.abort();
      app.window.CrucibleSessionBootstrap = {controller, finish: () => finished++, response: Promise.resolve({
        ok: mode !== 'http', status: mode === 'http' ? 429 : 200,
        json: async () => { if (mode !== 'http') throw new Error('Bad body'); return {detail: 'Sign-in is busy. Please retry shortly.'}; }
      })};
      const count = app.requests.length;
      await assert.rejects(app.sandbox.api('/api/session', undefined, {initialSession: true}), error => {
        assert.equal(error.status, mode === 'http' ? 429 : 0);
        assert.match(error.message, mode === 'http' ? /Sign-in is busy/ : mode === 'body' ? /unreadable response/ : /took too long/);
        return true;
      });
      assert.equal(finished, 1);
      assert.equal(app.requests.length, count);
      assert.deepEqual({...app.window.CrucibleSessionBootstrap}, {consumed: true});
    }
  });
  console.log(`\n${passed} early session checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

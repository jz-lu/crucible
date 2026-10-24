const assert = require('node:assert/strict');
const {createApp} = require('./frontend-dom.cjs');
const user = {id: 'fixture-alice', handle: 'alice'};
const secretDiagnostic = 'raw-response-or-exception-must-not-escape';
function brokenBody(name, options = {}) {
  return {ok: true, status: 200, headers: {get: () => options.type || 'application/json'},
    json: async () => { throw Object.assign(new Error(secretDiagnostic), {name}); },
    text: async () => { throw Object.assign(new Error(secretDiagnostic), {name}); }, ...options};
}
async function fixture(response) {
  const app = await createApp({schemes: [], user, hash: '#about'});
  app.sandbox.setSession(app.envelope({user, csrf_token: 'fixture-csrf', access: {mode: 'beta', status: 'active', allowed: true}}));
  const requests = [];
  app.sandbox.fetch = async (path, options) => { requests.push({path, options}); return response; };
  return {...app, calls: requests};
}
async function rejectsCode(task, code, message) {
  await assert.rejects(task, error => {
    assert.equal(error.status, 0); assert.equal(error.code, code);
    assert.match(error.message, message); assert.equal(error.message.includes(secretDiagnostic), false);
    return true;
  });
}
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('interrupted response bodies are not mislabeled as malformed JSON or retried', async () => {
    for (const name of ['TypeError', 'AbortError']) {
      const app = await fixture(brokenBody(name));
      await rejectsCode(app.sandbox.api('/api/schemes?projection=map'), 'response_interrupted', /connection was interrupted/);
      assert.equal(app.calls.length, 1); assert.equal(app.calls[0].options.method, 'GET');
    }
  });
  await test('HTML pages and followed redirects get fixed safe classifications without body logging', async () => {
    for (const redirected of [false, true]) {
      const app = await fixture(brokenBody('SyntaxError', {type: 'text/html; charset=utf-8', redirected}));
      await rejectsCode(app.sandbox.api('/api/session'), redirected ? 'unexpected_response_redirect' : 'unexpected_response_page', /unexpected page/);
      assert.equal(app.calls.length, 1);
    }
  });
  await test('malformed JSON stays a visible error rather than being silently retried', async () => {
    const app = await fixture(brokenBody('SyntaxError'));
    await rejectsCode(app.sandbox.api('/api/session'), 'invalid_json_response', /unreadable response/);
    assert.equal(app.calls.length, 1);
  });
  await test('already-read or locked response bodies are not mistaken for network interruption', async () => {
    for (const state of [{bodyUsed: true}, {body: {locked: true}}, {bodyUsed: true, type: 'text/html'}]) {
      const app = await fixture(brokenBody('TypeError', state));
      await rejectsCode(app.sandbox.api('/api/session'), 'response_already_read', /unreadable response/);
    }
  });
  await test('bootstrap deadline remains distinct and its timer is finished exactly once', async () => {
    const app = await fixture(brokenBody('TypeError')), controller = new AbortController();
    let finished = 0; controller.abort();
    app.window.CrucibleSessionBootstrap = {controller, response: Promise.resolve(brokenBody('AbortError')), finish: () => finished++};
    await rejectsCode(app.sandbox.api('/api/session', undefined, {initialSession: true}), 'response_timeout', /took too long/);
    assert.equal(finished, 1); assert.equal(app.calls.length, 0);
  });
  await test('writes with interrupted responses are never automatically replayed', async () => {
    const app = await fixture(brokenBody('TypeError')), body = {title: 'synthetic write'};
    await rejectsCode(app.sandbox.api('/api/notebook/notes', body), 'response_interrupted', /connection was interrupted/);
    assert.equal(app.calls.length, 1); assert.equal(app.calls[0].options.method, 'POST');
    assert.equal(app.calls[0].options.body, JSON.stringify(body));
  });
  await test('raw downloads retain their exact text without new JSON or content-type restrictions', async () => {
    const value = '<synthetic exact text>\n';
    const app = await fixture({ok: true, status: 200, headers: {get: () => 'text/html'}, text: async () => value,
      json: () => { throw Error('Raw download must not be parsed'); }});
    assert.equal(await app.sandbox.api('/api/challenges/fixture/download', undefined, {raw: true}), value);
    assert.equal(app.calls.length, 1);
  });
  await test('non-JSON HTTP errors retain their original status-specific handling', async () => {
    for (const [status, message] of [[401, /session has ended/], [403, /could not be authorized/], [429, /Too many requests/], [503, /could not be completed/]]) {
      const app = await fixture(brokenBody('SyntaxError', {ok: false, status, type: 'text/html'}));
      await assert.rejects(app.sandbox.api('/api/auth/github/pending'), error => error.status === status && message.test(error.message));
      assert.equal(app.calls.length, 1);
    }
  });
  await test('a completed protected body cannot restore access revoked while it was pending', async () => {
    let resolve; const body = new Promise(done => {resolve = done;});
    const app = await fixture({ok: true, status: 200, json: () => body});
    const pending = app.sandbox.api('/api/schemes?projection=map');
    app.sandbox.setSession(app.envelope({user: null, csrf_token: 'revoked', access: {mode: 'beta', status: 'anonymous', allowed: false}}));
    resolve({schemes: []});
    await assert.rejects(pending, error => error.status === 403);
    assert.equal(app.calls.length, 1);
  });
  console.log(`\n${passed} API response checks passed.`);
})().catch(error => {console.error(error); process.exitCode = 1;});

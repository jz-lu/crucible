const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'alice', handle: 'alice'};
const admitted = {admitted: true, reason: null};
const profile = {id: 'stage-1', instance_size: 1, parameter_bits: 1, stage_number: 1,
  shared_secret_bytes: 32, parameters: {}, encoding: {shared_secret_bytes: 32}, correctness: {}};
const scheme = {id: 'fixture', version: '2', name: 'Fixture KEM', profiles: [profile],
  next_profile_id: profile.id, protocol: {}, generation_admission: admitted, size_parameter: {meaning: 'Fixture'}};
const challengeRoute = '#challenge/fixture/2?profile=stage-1&view=challenge';
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return {promise, resolve}; };
const posts = app => app.requests.filter(request => request.options.method === 'POST');
async function boot({selected = scheme, route = '#scheme/fixture/2', pendingSession = null} = {}) {
  const server = {roundId: 1000, second: 1200, sessionGate: pendingSession, allowed: true};
  server.envelope = payload => ({server_time: server.roundId * 3600 + server.second,
    round: {id: server.roundId, starts_at: server.roundId * 3600, ends_at: (server.roundId + 1) * 3600}, ...payload});
  server.session = () => server.envelope({user, csrf_token: 'fixture-csrf',
    access: {mode: 'beta', allowed: server.allowed, status: server.allowed ? 'active' : 'revoked'}});
  const app = await createApp({hash: route, responder: (url) => {
    if (url.pathname === '/api/session') return server.sessionGate?.promise || server.session();
    if (url.pathname === '/api/schemes') return server.envelope({schemes: [selected]});
    if (url.pathname === '/api/protocol/benchmarks') return server.envelope({method: {id: 'fixture-timings'}, report: null});
    if (url.pathname === '/api/leaderboard') return server.envelope({entries: []});
    if (url.pathname === '/api/challenges') return server.envelope({challenge: null, generation_admission: admitted,
      round_started: false, round_started_round_id: server.roundId});
  }});
  return {app, server};
}
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('overview clock precedes View challenges, uses unique IDs, and does not read or issue an instance', async () => {
    const {app} = await boot();
    const clock = app.$('#overview-clock'), button = app.$('#overview-challenges');
    assert.equal(clock.hidden, false);
    assert.equal(clock.parentElement, button.parentElement);
    assert.ok(clock.parentElement.children.indexOf(clock) < clock.parentElement.children.indexOf(button));
    assert.equal(app.$('#overview-countdown').textContent, '40:00');
    assert.equal(app.$('#overview-countdown').textContent, app.$('#countdown').textContent);
    assert.equal(app.document.querySelectorAll('#overview-countdown').length, 1);
    assert.equal(app.document.querySelectorAll('#countdown').length, 1);
    assert.match(clock.textContent, /Round ends in/);
    assert.equal(clock.closest('[aria-live]'), null, 'The per-second display must not announce every tick');
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/challenges').length, 0);
    assert.equal(posts(app).length, 0);
  });
  await test('both locations use the same server-derived deadline and urgent state', async () => {
    const {app, server} = await boot();
    server.second = 3590;
    app.sandbox.absorbClock(server.envelope({}));
    for (const id of ['#overview-countdown', '#countdown']) {
      assert.equal(app.$(id).textContent, '00:10');
      assert.equal(app.$(id).classList.contains('urgent'), true);
    }
    assert.match(app.$('#overview-countdown').getAttribute('aria-label'), /10 seconds until the shared round ends/);
    await app.navigate(challengeRoute);
    assert.equal(app.$('#scheme-clock').hidden, false);
    assert.equal(app.$('#overview-clock').hidden, true);
    assert.equal(posts(app).length, 0, 'Following View challenges remains a read-only navigation');
  });
  await test('at zero the clocks wait for a confirmed new round instead of inventing a new hour or generating', async () => {
    const {app, server} = await boot();
    const gate = deferred(); server.sessionGate = gate;
    server.second = 3600;
    app.sandbox.absorbClock(server.envelope({}));
    await flush();
    assert.equal(app.$('#overview-countdown').textContent, '00:00');
    assert.equal(app.$('#countdown').textContent, '00:00');
    assert.equal(posts(app).length, 0);
    server.roundId++; server.second = 1200; server.sessionGate = null;
    gate.resolve(server.session()); await flush();
    assert.equal(app.$('#overview-countdown').textContent, '40:00');
    assert.equal(app.$('#countdown').textContent, '40:00');
    assert.equal(app.$('#overview-countdown').classList.contains('urgent'), false);
    assert.equal(posts(app).length, 0);
  });
  await test('pending access never displays a live overview countdown or requests private data', async () => {
    const gate = deferred(), {app, server} = await boot({pendingSession: gate});
    assert.equal(app.$('#overview-clock').hidden, true);
    assert.equal(app.$('#overview-countdown').textContent, '—:—');
    assert.deepEqual(app.requests.map(request => request.url.pathname), ['/api/session']);
    server.allowed = false; gate.resolve(server.session()); await flush();
    assert.equal(app.$('#overview-clock').hidden, true);
    assert.equal(posts(app).length, 0);
  });
  await test('missing round and revoked access clear a previously displayed countdown', async () => {
    const {app, server} = await boot();
    vm.runInContext('state.round = null', app.sandbox);
    app.sandbox.updateClock();
    assert.equal(app.$('#overview-clock').hidden, true);
    assert.equal(app.$('#overview-countdown').textContent, '—:—');
    app.sandbox.absorbClock(server.envelope({}));
    assert.equal(app.$('#overview-clock').hidden, false);
    server.allowed = false; app.sandbox.setSession(server.session());
    app.sandbox.updateClock();
    assert.equal(app.$('#overview-clock').hidden, true);
    assert.equal(app.$('#overview-countdown').textContent, '—:—');
  });
  await test('unavailable, archived and empty releases do not advertise an actionable round deadline', async () => {
    const unavailable = {...scheme, generation_admission: {admitted: false, reason: 'release_not_admitted'}};
    const archived = {...scheme, version: '1', generation_admission: {admitted: false, reason: 'archived_release'}};
    for (const options of [{selected: unavailable},
      {selected: {...scheme, previous_releases: [archived]}, route: '#scheme/fixture/1'},
      {selected: {...scheme, profiles: [], next_profile_id: null}}]) {
      const {app} = await boot(options);
      assert.equal(app.$('#overview-clock').hidden, true);
      assert.equal(posts(app).length, 0);
    }
  });
  console.log(`\n${passed} shared round-clock checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

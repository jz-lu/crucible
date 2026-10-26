// The real app runs against an HTML-backed DOM and explicitly held responses.
// These are state/ownership checks, not browser layout or hosted timing claims.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');

const alice = {id: 'alice', handle: 'alice'};
const admitted = {admitted: true, reason: null};
const denied = {admitted: false, reason: 'release_not_admitted'};
const profiles = ['stage-1', 'stage-2'].map((id, index) => ({
  id, instance_size: index + 1, parameter_bits: index + 1, stage_number: index + 1,
  stage_label: 'Stage ' + (index + 1), shared_secret_bytes: 32,
  encoding: {shared_secret_bytes: 32}, parameters: {}, correctness: {}
}));
const route = (profile = 'stage-1', view = 'challenge') =>
  `#challenge/fixture/2?profile=${profile}&view=${view}`;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const posts = app => app.requests.filter(request =>
  request.url.pathname === '/api/challenges' && request.options.method === 'POST');
const pendingIntent = app => vm.runInContext('state.challengeStartIntent', app.sandbox);

async function boot({admission = admitted} = {}) {
  const server = {user: alice, csrf: 'alice-csrf', roundId: 1000, lookups: [], issues: []};
  server.envelope = (payload, round = server.roundId) => ({
    server_time: round * 3600 + 1200,
    round: {id: round, starts_at: round * 3600, ends_at: (round + 1) * 3600},
    ...payload
  });
  server.session = () => server.envelope({user: server.user, csrf_token: server.csrf,
    access: {mode: 'beta', status: server.user ? 'active' : 'anonymous', allowed: Boolean(server.user)}});
  server.challenge = (profile = 'stage-1', status = 'open', round = server.roundId) => ({
    id: 'instance-' + profile, scheme_id: 'fixture', scheme_version: '2', profile_id: profile,
    round_id: round, status, issued_at: round * 3600 + 1200, ends_at: (round + 1) * 3600,
    public: {encoding: {shared_secret_bytes: 32}},
    submission: status === 'solved' ? {status: 'correct'} : status === 'incorrect' ? {status: 'incorrect'} : null
  });
  const app = await createApp({hash: route(), responder: async (url, options) => {
    if (url.pathname === '/api/session') return server.session();
    if (url.pathname === '/api/schemes') return server.envelope({schemes: [{
      id: 'fixture', version: '2', name: 'Fixture KEM', profiles,
      protocol: {construction: 'Fixture'}, next_profile_id: 'stage-1',
      size_parameter: {meaning: 'Fixture'}, generation_admission: admission
    }]});
    if (url.pathname !== '/api/challenges') return undefined;
    const round = server.roundId;
    const gate = deferred();
    if (options.method === 'POST') {
      const payload = JSON.parse(options.body);
      server.issues.push({gate, payload});
      await gate.promise;
      return server.envelope({challenge: server.challenge(payload.profile_id, 'open', round),
        round_started: true, round_started_round_id: round}, round);
    }
    const lookup = {gate, profile: url.searchParams.get('profile_id'), round};
    server.lookups.push(lookup);
    const result = await gate.promise;
    return server.envelope({challenge: null, generation_admission: admitted,
      round_started: false, round_started_round_id: round, ...result}, round);
  }});
  assert.equal(server.lookups.length, 1, 'Cold challenge starts one summary read');
  return {app, server};
}

async function finishLookup(server, index = 0, payload = {}) {
  server.lookups[index].gate.resolve(payload);
  await flush();
}
async function clickGet(app) { app.$('#issue-button').click(); await flush(); }
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }

(async () => {
  await test('Ready appears before the cold lookup without claiming an existing instance or generating', async () => {
    const {app, server} = await boot();
    assert.equal(app.$('#challenge-empty').hidden, false);
    assert.equal(app.$('#empty-title').textContent, 'Ready?');
    assert.equal(app.$('#issue-button').disabled, false);
    assert.equal(app.$('#challenge-loading').classList.contains('sr-only'), true,
      'The status announcement must not replace the visible action card');
    assert.equal(app.$('#challenge-active').hidden, true);
    assert.equal(app.$('#manual-instance-tools').hidden, true);
    assert.equal(app.$('#use-agent').disabled, true);
    assert.equal(app.$('#open-submission').disabled, true);
    assert.equal(posts(app).length, 0);
    assert.equal(pendingIntent(app), null);
    await finishLookup(server);
    assert.equal(posts(app).length, 0, 'A copied challenge route does not grant a first issue');
  });

  await test('Get queues one explicit action, absorbs the lookup wait, and issues only after confirmed empty', async () => {
    const {app, server} = await boot();
    await clickGet(app);
    assert.equal(app.$('#challenge-empty').hidden, false);
    assert.match(app.$('#issue-button').textContent, /Preparing/);
    assert.equal(app.$('#issue-button').disabled, true);
    assert.ok(pendingIntent(app));
    app.$('#issue-button').click();
    await app.sandbox.issueChallenge(); // A repeated invocation must also deduplicate.
    assert.equal(posts(app).length, 0);
    assert.equal(server.lookups.length, 1);
    await finishLookup(server);
    assert.equal(posts(app).length, 1);
    assert.equal(server.issues.length, 1);
    assert.deepEqual(server.issues[0].payload, {profile_id: 'stage-1', scheme_id: 'fixture', scheme_version: '2'});
    const request = posts(app)[0];
    assert.equal(request.options.credentials, 'same-origin');
    assert.equal(request.options.cache, 'no-store');
    assert.equal(request.options.headers['X-CSRF-Token'], 'alice-csrf');
    assert.equal(app.$('#issue-button').disabled, true);
    server.issues[0].gate.resolve(); await flush();
    assert.equal(app.$('#challenge-active').hidden, false);
    assert.equal(app.$('#challenge-empty').hidden, true);
    assert.equal(posts(app).length, 1);
  });

  await test('queued Get opens existing open or answered instances without a POST', async () => {
    for (const status of ['open', 'solved', 'incorrect']) {
      const {app, server} = await boot();
      await clickGet(app);
      await finishLookup(server, 0, {challenge: server.challenge('stage-1', status), round_started: true});
      assert.equal(posts(app).length, 0, status);
      assert.equal(app.$('#challenge-active').hidden, false, status);
      assert.equal(pendingIntent(app), null, status);
      assert.match(app.$('#challenge-status').textContent,
        status === 'solved' ? /Recovered/ : status === 'incorrect' ? /Incorrect/ : /Open/);
    }
  });

  await test('exact denial or malformed admission cancels a queued Get without generation', async () => {
    for (const exact of [denied, {admitted: 'true'}]) {
      const {app, server} = await boot();
      await clickGet(app);
      await finishLookup(server, 0, {generation_admission: exact});
      assert.equal(posts(app).length, 0);
      assert.equal(pendingIntent(app), null);
      assert.equal(app.$('#issue-button').disabled, true);
      assert.equal(app.$('#empty-title').textContent, 'Challenges not enabled');
    }
  });

  await test('failed lookup cancels the queued action and an explicit lookup retry does not replay it', async () => {
    const {app, server} = await boot();
    await clickGet(app);
    server.lookups[0].gate.reject(new Error('Synthetic lookup failure'));
    await flush();
    assert.equal(posts(app).length, 0);
    assert.equal(pendingIntent(app), null);
    assert.equal(app.$('#challenge-error').hidden, false);
    app.$('#retry-challenge').click(); await flush();
    assert.equal(server.lookups.length, 2);
    await finishLookup(server, 1);
    assert.equal(posts(app).length, 0, 'Retrying the read is not a new generation action');
    assert.equal(app.$('#issue-button').disabled, false);
  });

  for (const change of ['account', 'csrf', 'revoked', 'round', 'profile', 'view', 'workspace']) {
    await test(`queued Get cannot survive a ${change} ownership change`, async () => {
      const {app, server} = await boot();
      await clickGet(app);
      if (change === 'profile') await app.navigate(route('stage-2'));
      else if (change === 'view') await app.navigate(route('stage-1', 'protocol'));
      else if (change === 'workspace') await app.navigate('#about');
      else {
        if (change === 'account') server.user = {id: 'bob', handle: 'bob'};
        if (change === 'csrf') server.csrf = 'rotated-csrf';
        if (change === 'revoked') server.user = null;
        if (change === 'round') server.roundId++;
        app.sandbox.setSession(server.session());
      }
      await finishLookup(server);
      // Session rotation can schedule a new, read-only lookup. Finish only the
      // requests now present; a late prior response may never carry its intent.
      const later = server.lookups.slice(1);
      for (const lookup of later) lookup.gate.resolve({});
      await flush();
      assert.equal(posts(app).length, 0, change);
      assert.equal(pendingIntent(app), null, change);
      assert.equal(app.$('#challenge-active').hidden, true, change);
    });
  }

  await test('cached existing instance remains visible but actions stay guarded during revalidation', async () => {
    const {app, server} = await boot();
    const existing = server.challenge();
    await finishLookup(server, 0, {challenge: existing, round_started: true});
    assert.equal(app.$('#manual-instance-tools').hidden, false);
    await app.navigate('#about'); await app.navigate(route());
    assert.equal(server.lookups.length, 2);
    assert.equal(app.$('#challenge-active').hidden, false);
    assert.equal(app.$('#challenge-empty').hidden, true);
    assert.equal(app.$('#challenge-active').getAttribute('aria-busy'), 'true');
    assert.equal(app.$('#manual-instance-tools').hidden, true);
    assert.equal(app.$('#use-agent').disabled, true);
    assert.equal(app.$('#open-submission').disabled, true);
    assert.equal(posts(app).length, 0);
    await finishLookup(server, 1, {challenge: existing, round_started: true});
    assert.equal(app.$('#manual-instance-tools').hidden, false);
    assert.equal(app.$('#challenge-active').getAttribute('aria-busy'), 'false');
    assert.equal(posts(app).length, 0);
  });
  console.log(`\n${passed} challenge readiness checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

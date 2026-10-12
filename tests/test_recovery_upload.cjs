// Run with `node tests/test_recovery_upload.cjs`. Every challenge and response is a local fixture.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const scheme = {id: 'fixture-kem', version: '1', name: 'Fixture KEM', educational: true, profiles: [{id: 'fixture-20', instance_size: 20}, {id: 'fixture-21', instance_size: 21}], next_profile_id: 'fixture-20'};
const route = profile => `#challenge/fixture-kem/1?profile=${profile}&view=challenge`;
const formFields = ['candidate', 'method-select', 'method-name', 'method-pseudocode', 'hardware', 'workers', 'wall-seconds', 'precomputation', 'peak-memory', 'compute-notes'];
const record = (id = 'fixture-challenge', bytes = 32) => ({format: 'crucible-recovery-v1', challenge_id: id, candidate: 'AB'.repeat(bytes), method: {name: '  Fixture attack  ', pseudocode: '  Recover the fixture key.  '}, compute: {hardware: '  Fixture CPU  ', workers: 2, wall_seconds: 0.25, precomputation: '  None  ', peak_memory: '  4 KiB  '}});
const simpleChallenge = (bytes = 32) => ({id: 'fixture-challenge', public: {encoding: {shared_secret_bytes: bytes}}});
const clone = value => JSON.parse(JSON.stringify(value));
const snapshot = app => Object.fromEntries(formFields.map(id => [id, String(app.$('#' + id).value)]));
const mutations = app => app.requests.filter(request => request.options.method === 'POST');
async function fixtureApp({methods, bytes = 32, mutationResponder} = {}) {
  const app = await createApp({schemes: [scheme], user: {id: 'fixture-user', handle: 'fixture'}, hash: route('fixture-20'), responder: (url, options, envelope) => {
    if (options.method === 'POST' && mutationResponder) return mutationResponder(url, options, envelope);
    if (url.pathname === '/api/challenges') {
      const clock = envelope({});
      const selected = url.searchParams.get('profile_id');
      return envelope({challenge: {id: selected === 'fixture-20' ? 'fixture-challenge' : 'second-challenge', profile_id: selected, round_id: clock.round.id, issued_at: clock.server_time, ends_at: clock.round.ends_at, status: 'open', public: {encoding: {shared_secret_bytes: bytes}}}});
    }
    if (url.pathname === '/api/methods') return methods || {methods: []};
  }});
  app.$('#open-submission').click(); await flush();
  assert.equal(app.$('#submission-dialog').open, true);
  return app;
}
async function importText(app, text, options = {}) {
  const input = app.$('#recovery-file');
  input.files = [{name: 'recovery.json', size: options.size ?? Buffer.byteLength(text), text: options.read || (async () => text)}];
  input.dispatchEvent({type: 'change'}); await flush();
}
function fillExisting(app) { for (const id of formFields) app.$('#' + id).value = 'existing-' + id; }
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  const pure = await createApp({schemes: [scheme]});
  const parse = (value, challenge = simpleChallenge()) => pure.sandbox.parseRecoveryFile(typeof value === 'string' ? value : JSON.stringify(value), challenge);
  await test('valid 16-byte and 32-byte recoveries normalize keys and required text', () => {
    for (const bytes of [16, 32]) {
      const output = parse(record('fixture-challenge', bytes), simpleChallenge(bytes));
      assert.equal(output.format, 'crucible-recovery-v1');
      assert.equal(output.challenge_id, 'fixture-challenge');
      assert.equal(output.candidate, 'ab'.repeat(bytes));
      assert.equal(output.method.name, 'Fixture attack');
      assert.equal(output.method.pseudocode, 'Recover the fixture key.');
      assert.equal(output.compute.hardware, 'Fixture CPU');
      assert.equal(output.compute.precomputation, 'None');
      assert.equal(output.compute.peak_memory, '4 KiB');
      assert.equal(output.compute.notes, '');
      assert.equal(output.compute.workers, 2);
      assert.equal(output.compute.wall_seconds, 0.25);
    }
  });
  await test('malformed JSON, unsupported format, wrong challenge and invalid containers reject', () => {
    for (const text of ['', '{', '{"candidate":}', 'null', '[]', '"record"', 'true', '17']) assert.throws(() => parse(text), undefined, text);
    for (const [field, value] of [['format', 'future-format'], ['format', 1], ['challenge_id', 'other-challenge'], ['challenge_id', 7], ['method', null], ['method', []], ['method', 'attack'], ['compute', null], ['compute', []], ['compute', 'CPU']]) {
      const valueRecord = record(); valueRecord[field] = value; assert.throws(() => parse(valueRecord), undefined, field + '=' + JSON.stringify(value));
    }
    for (const challenge of [null, {}, {id: 'fixture-challenge'}, {id: 'fixture-challenge', public: {encoding: {shared_secret_bytes: 0}}}]) assert.throws(() => parse(record(), challenge));
  });
  await test('all mandatory fields are required and unknown fields reject at every level', () => {
    for (const path of [['format'], ['challenge_id'], ['candidate'], ['method'], ['compute'], ['method', 'name'], ['method', 'pseudocode'], ['compute', 'hardware'], ['compute', 'workers'], ['compute', 'wall_seconds'], ['compute', 'precomputation'], ['compute', 'peak_memory']]) {
      const value = record(); const parent = path.length === 1 ? value : value[path[0]]; delete parent[path.at(-1)]; assert.throws(() => parse(value), undefined, path.join('.'));
    }
    for (const section of [null, 'method', 'compute']) {
      const value = record(); (section ? value[section] : value).surprise = 'unsupported'; assert.throws(() => parse(value));
    }
    assert.throws(() => parse(JSON.stringify(record()).replace('"format":', '"__proto__":{},"format":')));
  });
  await test('candidate must be exact-length hexadecimal for the active shared-key output', () => {
    for (const candidate of [null, 42, [], true, '', 'ab'.repeat(31), 'ab'.repeat(33), 'z'.repeat(64), '0x' + 'ab'.repeat(32), ' ' + 'ab'.repeat(32), 'ab '.repeat(32)]) {
      const value = record(); value.candidate = candidate; assert.throws(() => parse(value), undefined, String(candidate));
    }
    assert.throws(() => parse(record(), simpleChallenge(16)));
  });
  await test('text schema limits and numeric bounds match server constraints', () => {
    for (const [section, key, maximum] of [['method', 'name', 100], ['method', 'pseudocode', 15000], ['compute', 'hardware', 500], ['compute', 'precomputation', 2000], ['compute', 'peak_memory', 300]]) {
      for (const invalid of ['', ' \n\t ', null, false, 5, [], {}, 'x'.repeat(maximum + 1)]) { const value = record(); value[section][key] = invalid; assert.throws(() => parse(value), undefined, section + '.' + key); }
      const boundary = record(); boundary[section][key] = 'x'.repeat(maximum); assert.equal(parse(boundary)[section][key].length, maximum);
    }
    for (const invalid of [null, false, 5, [], {}, 'x'.repeat(2001)]) { const value = record(); value.compute.notes = invalid; assert.throws(() => parse(value)); }
    for (const invalid of [0, -1, 1000001, 1.5, '2', true, null, [], {}]) { const value = record(); value.compute.workers = invalid; assert.throws(() => parse(value)); }
    for (const invalid of [-1, 315576001, '0.25', true, null, [], {}]) { const value = record(); value.compute.wall_seconds = invalid; assert.throws(() => parse(value)); }
    assert.throws(() => parse(JSON.stringify(record()).replace('"wall_seconds":0.25', '"wall_seconds":1e999')));
    for (const workers of [1, 1000000]) for (const seconds of [0, 315576000]) { const value = record(); value.compute.workers = workers; value.compute.wall_seconds = seconds; assert.equal(parse(value).compute.wall_seconds, seconds); }
  });
  await test('valid import fills the review form without sending a request', async () => {
    const app = await fixtureApp(); await importText(app, JSON.stringify(record()));
    assert.equal(app.$('#candidate').value, 'ab'.repeat(32));
    assert.equal(app.$('#method-select').value, 'new');
    assert.equal(app.$('#method-name').value, 'Fixture attack');
    assert.equal(app.$('#method-pseudocode').value, 'Recover the fixture key.');
    assert.equal(app.$('#hardware').value, 'Fixture CPU');
    assert.equal(String(app.$('#workers').value), '2');
    assert.equal(String(app.$('#wall-seconds').value), '0.25');
    assert.equal(app.$('#precomputation').value, 'None');
    assert.equal(app.$('#peak-memory').value, '4 KiB');
    assert.equal(app.$('#recovery-file-error').hidden, true);
    assert.equal(app.$('#recovery-file-status').hidden, false);
    assert.equal(mutations(app).length, 0);
  });
  await test('only explicit submission sends the imported method and candidate to the server', async () => {
    const app = await fixtureApp({mutationResponder: (url, options, envelope) => {
      if (url.pathname === '/api/methods') return {method: {id: 'fixture-method', ...JSON.parse(options.body)}};
      assert.equal(url.pathname, '/api/challenges/fixture-challenge/submit');
      const clock = envelope({});
      return envelope({submission: {status: 'correct'}, challenge: {id: 'fixture-challenge', profile_id: 'fixture-20', round_id: clock.round.id, issued_at: clock.server_time, ends_at: clock.round.ends_at, status: 'solved', submission: {status: 'correct'}, public: {encoding: {shared_secret_bytes: 32}}}});
    }});
    await importText(app, JSON.stringify(record()));
    assert.equal(mutations(app).length, 0);
    app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
    const posted = mutations(app);
    assert.equal(posted.length, 2);
    assert.equal(posted[0].url.pathname, '/api/methods');
    assert.deepEqual(JSON.parse(posted[0].options.body), {name: 'Fixture attack', pseudocode: 'Recover the fixture key.'});
    const answer = JSON.parse(posted[1].options.body);
    assert.equal(answer.candidate, 'ab'.repeat(32));
    assert.equal(answer.method_id, 'fixture-method');
    assert.deepEqual(answer.compute, {hardware: 'Fixture CPU', workers: 2, wall_seconds: 0.25, precomputation: 'None', peak_memory: '4 KiB', notes: ''});
    assert.equal(app.$('#submission-dialog').open, false);
  });
  await test('invalid imports leave every existing form value unchanged', async () => {
    const app = await fixtureApp(); fillExisting(app); const before = snapshot(app);
    for (const text of ['{', JSON.stringify(record('wrong-challenge')), JSON.stringify({...record(), compute: {...record().compute, workers: 0}})]) {
      await importText(app, text);
      assert.deepEqual(snapshot(app), before);
      assert.equal(app.$('#recovery-file-error').hidden, false);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('oversized files reject before reading and cannot modify the review form', async () => {
    const app = await fixtureApp(); fillExisting(app); const before = snapshot(app); let read = false;
    await importText(app, JSON.stringify(record()), {size: 128 * 1024 + 1, read: async () => { read = true; return JSON.stringify(record()); }});
    assert.equal(read, false);
    assert.deepEqual(snapshot(app), before);
    assert.equal(app.$('#recovery-file-error').hidden, false);
    assert.equal(mutations(app).length, 0);
  });
  await test('a valid file exactly at the 128 KiB limit remains importable', async () => {
    const app = await fixtureApp();
    const text = JSON.stringify(record()).padEnd(128 * 1024, ' ');
    await importText(app, text);
    assert.equal(app.$('#candidate').value, 'ab'.repeat(32));
    assert.equal(app.$('#recovery-file-error').hidden, true);
    assert.equal(mutations(app).length, 0);
  });
  await test('expired, uncertain-retry and submitting forms cannot import new results', async () => {
    for (const stateSetup of ['state.submissionChallenge.ends_at = 0', 'state.pendingAttempt = {challengeId: state.submissionChallenge.id}', 'state.submitBusy = true']) {
      const app = await fixtureApp(); fillExisting(app); const before = snapshot(app); vm.runInContext(stateSetup, app.sandbox);
      await importText(app, JSON.stringify(record()));
      assert.deepEqual(snapshot(app), before);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('a late method-list response preserves the imported method', async () => {
    let resolveMethods; const pendingMethods = new Promise(resolve => { resolveMethods = resolve; });
    const app = await fixtureApp({methods: pendingMethods});
    await importText(app, JSON.stringify(record()));
    resolveMethods({methods: [{id: 'saved-method', name: 'Older method', pseudocode: 'Older code'}]}); await flush();
    assert.equal(app.$('#method-select').value, 'new');
    assert.equal(app.$('#method-name').value, 'Fixture attack');
    assert.equal(app.$('#method-pseudocode').value, 'Recover the fixture key.');
    assert.equal(mutations(app).length, 0);
  });
  await test('a late file read cannot populate a different challenge form', async () => {
    const app = await fixtureApp(); let releaseFile;
    const text = JSON.stringify(record()); const pendingFile = new Promise(resolve => { releaseFile = resolve; });
    await importText(app, text, {read: () => pendingFile});
    app.$('#submission-dialog').close();
    await app.navigate(route('fixture-21'));
    app.$('#open-submission').click(); await flush();
    fillExisting(app); const before = snapshot(app);
    releaseFile(text); await flush();
    assert.deepEqual(snapshot(app), before);
    assert.equal(mutations(app).length, 0);
  });
  console.log(`\n${passed} recovery-upload regressions passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

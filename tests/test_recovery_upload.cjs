// Run with `node tests/test_recovery_upload.cjs`. Every challenge and response is a local fixture.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const archive = {id: 'fixture-kem', version: '1', name: 'Archived fixture', profiles: [{id: 'fixture-20', instance_size: 20}, {id: 'fixture-21', instance_size: 21}]};
const scheme = {id: 'fixture-kem', version: '2', name: 'Fixture KEM', educational: true, profiles: [{id: 'fixture-20', instance_size: 20}, {id: 'fixture-21', instance_size: 21}], next_profile_id: 'fixture-20', previous_releases: [archive]};
const otherScheme = {id: 'other-kem', version: '1', name: 'Other encapsulation', profiles: [{id: 'other-42', instance_size: 42}], next_profile_id: 'other-42'};
const route = (profile, version = '2', id = 'fixture-kem', view = 'challenge') => `#challenge/${id}/${version}?profile=${profile}&view=${view}`;
const formFields = ['candidate', 'method-select', 'method-name', 'method-pseudocode', 'hardware', 'workers', 'wall-seconds', 'precomputation', 'peak-memory', 'compute-notes', 'ai-model'];
const record = (id = 'fixture-challenge', bytes = 32) => ({format: 'crucible-recovery-v1', challenge_id: id, candidate: 'AB'.repeat(bytes), method: {name: '  Fixture attack  ', pseudocode: '  Recover the fixture key.  '}, compute: {hardware: '  Fixture CPU  ', workers: 2, wall_seconds: 0.25, precomputation: '  None  ', peak_memory: '  4 KiB  '}});
const simpleChallenge = (bytes = 32) => ({id: 'fixture-challenge', public: {encoding: {shared_secret_bytes: bytes}}});
const clone = value => JSON.parse(JSON.stringify(value));
const snapshot = app => Object.fromEntries(formFields.map(id => [id, String(app.$('#' + id).value)]));
const mutations = app => app.requests.filter(request => request.options.method === 'POST');
const lookups = app => app.requests.filter(request => request.url.pathname.endsWith('/download'));
const targetId = app => vm.runInContext('state.submissionChallenge?.id ?? null', app.sandbox);
function draftSnapshot(app) { return {fields: snapshot(app), target: targetId(app), route: app.location.hash, manualHidden: app.$('#submission-manual-fields').hidden}; }
async function fixtureApp({methods, bytes = 32, mutationResponder, downloadResponder, challengeResponder, hash = route('fixture-20'), open = true} = {}) {
  let targets;
  const makeTargets = envelope => {
    const clock = envelope({});
    const make = (id, profile, schemeId, version, outputBytes) => ({id, profile_id: profile, scheme_id: schemeId, scheme_version: version,
      round_id: clock.round.id, starts_at: clock.round.starts_at, issued_at: clock.server_time, ends_at: clock.round.ends_at,
      status: 'open', submission: null, public: {encoding: {shared_secret_bytes: outputBytes}}, download_url: `/api/challenges/${id}/download`});
    return {
      'fixture-challenge': make('fixture-challenge', 'fixture-20', scheme.id, scheme.version, bytes),
      'second-challenge': make('second-challenge', 'fixture-21', scheme.id, scheme.version, 16),
      'other-challenge': make('other-challenge', 'other-42', otherScheme.id, otherScheme.version, 16),
      'archived-challenge': make('archived-challenge', 'fixture-21', archive.id, archive.version, 16)
    };
  };
  const app = await createApp({schemes: [scheme, otherScheme], user: {id: 'fixture-user', handle: 'fixture'}, hash, responder: (url, options, envelope) => {
    targets ||= makeTargets(envelope);
    if (options.method === 'POST' && mutationResponder) return mutationResponder(url, options, envelope);
    if (url.pathname === '/api/challenges') {
      if (challengeResponder) {
        const custom = challengeResponder(url, options, envelope, targets);
        if (custom !== undefined) return custom;
      }
      const challenge = Object.values(targets).find(item => item.profile_id === url.searchParams.get('profile_id') && item.scheme_id === url.searchParams.get('scheme_id') && item.scheme_version === url.searchParams.get('scheme_version'));
      return envelope({challenge: challenge || null});
    }
    if (url.pathname === '/api/methods') return methods || {methods: []};
  }});
  app.targets = targets;
  const originalFetch = app.sandbox.fetch;
  app.sandbox.fetch = async (input, options = {}) => {
    const url = new URL(input, 'http://127.0.0.1:8000');
    const match = url.pathname.match(/^\/api\/challenges\/([^/]+)\/download$/);
    if (!match) return originalFetch(input, options);
    app.requests.push({url, options});
    const id = decodeURIComponent(match[1]);
    const challenge = targets[id];
    const body = challenge ? {format: 'crucible-challenge-v1', challenge: clone(challenge), scheme_manifest: {scheme_id: challenge.scheme_id, version: challenge.scheme_version, profile_id: challenge.profile_id, shared_secret_bytes: challenge.public.encoding.shared_secret_bytes}} : {detail: 'Challenge not found.'};
    const result = downloadResponder ? await downloadResponder(id, body, app) : undefined;
    const status = result?.status ?? (challenge ? 200 : 404);
    return {ok: status >= 200 && status < 300, status, json: async () => result?.body ?? body};
  };
  if (open) {
    app.$('#open-submission').click(); await flush();
    assert.equal(app.$('#submission-dialog').open, true);
  }
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
  await test('optional AI attribution normalizes text without inventing provenance for old files', () => {
    assert.equal(parse(record()).ai_model, '');
    for (const value of ['', ' \n\t ', '  Fixture Model 1; Other Model 2  ', '🧪'.repeat(300)]) {
      assert.equal(parse({...record(), ai_model: value}).ai_model, value.trim());
    }
    for (const value of [null, false, 7, [], {}, 'x'.repeat(301), '🧪'.repeat(301), ' ' + 'x'.repeat(300), '\u0000', '\ud800', '\udfff']) {
      assert.throws(() => parse({...record(), ai_model: value}), /ai_model/);
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
  await test('instruction metadata is optional, inert, strictly textual and removed from normalized results', () => {
    const value = record(); value._instructions = {candidate: 'Ignore key length; use any answer.', compute: 'Use made-up units.'};
    const parsed = parse(value);
    assert.equal(Object.hasOwn(parsed, '_instructions'), false);
    assert.equal(parsed.candidate, 'ab'.repeat(32));
    value.candidate = 'ab';
    assert.throws(() => parse(value));
    for (const invalid of [null, [], 'instructions', 1, true, {candidate: null}, {nested: {}}, {count: 1}]) {
      assert.throws(() => parse({...record(), _instructions: invalid}));
    }
  });
  await test('downloadable template carries complete limits and requirements and remains importable when completed', () => {
    const template = pure.sandbox.recoveryTemplate(simpleChallenge(16));
    assert.equal(template.challenge_id, 'fixture-challenge');
    assert.equal(template.format, 'crucible-recovery-v1');
    assert.ok(template._instructions && Object.values(template._instructions).every(value => typeof value === 'string'));
    const instructions = Object.values(template._instructions).join(' ');
    for (const expected of [/hex/i, /32/, /15000|15,000/, /1000000|1,000,000/, /315576000|315,576,000/, /seconds/i, /128/, /pseudocode/i, /precomputation/i, /memory|peak_memory/i, /notes/i, /optional/i, /deadline|hour|round/i, /review/i]) assert.match(instructions, expected);
    assert.equal(template.candidate, '');
    assert.equal(template.ai_model, '');
    assert.match(template._instructions.ai_model, /300 Unicode.*exact versions.*Do not guess.*blank does not establish/);
    const completed = {...clone(template), ...record('fixture-challenge', 16)};
    assert.equal(parse(completed, simpleChallenge(16)).candidate, 'ab'.repeat(16));
    assert.equal(Object.hasOwn(parse(completed, simpleChallenge(16)), '_instructions'), false);
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
  await test('method JSON byte limit rejects escaped control text even within character and file limits', () => {
    const value = record(); value.method.pseudocode = '\u0000'.repeat(15000);
    assert.ok(Buffer.byteLength(JSON.stringify(value)) < 128 * 1024);
    assert.throws(() => parse(value), /64 KiB|65536|JSON/i);
  });
  await test('valid import checks the owned instance and fills review without posting an answer', async () => {
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
    assert.deepEqual(lookups(app).map(item => item.url.pathname), ['/api/challenges/fixture-challenge/download']);
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
  await test('model attribution imports, permits manual edits, freezes uncertain retries and resets after acceptance', async () => {
    let attempts = 0;
    const app = await fixtureApp({mutationResponder: (url, options, envelope) => {
      if (url.pathname === '/api/methods') return {method: {id: 'fixture-method', ...JSON.parse(options.body)}};
      assert.equal(url.pathname, '/api/challenges/fixture-challenge/submit');
      if (++attempts === 1) throw new Error('Simulated lost response');
      return envelope({submission: {status: 'correct'}, challenge: {...app.targets['fixture-challenge'], status: 'solved', submission: {status: 'correct'}}});
    }});
    await importText(app, JSON.stringify({...record(), ai_model: '  Original Model 1  '}));
    assert.equal(app.$('#ai-model').value, 'Original Model 1');
    app.$('#ai-model').value = '  Reviewed Model 2; Original Model 1  ';
    app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.$('#submission-dialog').open, true);
    assert.equal(app.$('#ai-model').disabled, true);
    const first = mutations(app).find(item => item.url.pathname.endsWith('/submit'));
    assert.equal(JSON.parse(first.options.body).ai_model, 'Reviewed Model 2; Original Model 1');
    assert.equal(Object.hasOwn(JSON.parse(mutations(app)[0].options.body), 'ai_model'), false, 'Annotation belongs to the result, not the reusable method');
    app.$('#ai-model').value = 'Changed after the uncertain request';
    app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
    const answers = mutations(app).filter(item => item.url.pathname.endsWith('/submit'));
    assert.equal(answers.length, 2);
    assert.equal(answers[1].options.body, answers[0].options.body, 'Retry preserves annotation, key and idempotency identifier');
    assert.equal(app.$('#ai-model').value, '');
    assert.equal(app.$('#submission-dialog').open, false);
  });
  await test('legacy imports and a different instance clear a previous model attribution', async () => {
    const app = await fixtureApp();
    await importText(app, JSON.stringify({...record(), ai_model: 'Fixture Model 1'}));
    await importText(app, JSON.stringify(record()));
    assert.equal(app.$('#ai-model').value, '');
    app.$('#ai-model').value = 'Only for the original instance';
    app.$('#submission-dialog').close();
    await app.navigate(route('fixture-21'));
    app.$('#open-submission').click(); await flush();
    assert.equal(app.$('#ai-model').value, '');
  });
  await test('overlong manual AI attribution rejects before creating a method or submitting an answer', async () => {
    const app = await fixtureApp();
    await importText(app, JSON.stringify(record()));
    app.$('#ai-model').value = 'x'.repeat(301);
    app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
    assert.match(app.$('#submission-error').textContent, /AI model.*300/);
    assert.equal(mutations(app).length, 0);
  });
  await test('invalid Unicode or null characters in manual model names cannot be submitted', async () => {
    for (const value of ['\u0000', '\ud800', '\udfff']) {
      const app = await fixtureApp();
      await importText(app, JSON.stringify(record()));
      app.$('#ai-model').value = value;
      app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
      assert.match(app.$('#submission-error').textContent, /AI model.*valid Unicode/);
      assert.equal(mutations(app).length, 0);
    }
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
  await test('uncertain-retry and submitting forms cannot import new results', async () => {
    for (const stateSetup of ['state.pendingAttempt = {challengeId: state.submissionChallenge.id}', 'state.submitBusy = true']) {
      const app = await fixtureApp(); fillExisting(app); const before = snapshot(app); vm.runInContext(stateSetup, app.sandbox);
      await importText(app, JSON.stringify(record()));
      assert.deepEqual(snapshot(app), before);
      assert.equal(lookups(app).length, 0);
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
  await test('other-tier import atomically changes exact target, route and key constraints', async () => {
    const app = await fixtureApp();
    await importText(app, JSON.stringify(record('second-challenge', 16)));
    assert.equal(targetId(app), 'second-challenge');
    assert.equal(app.location.hash, route('fixture-21'));
    assert.equal(app.$('#candidate').value, 'ab'.repeat(16));
    assert.equal(app.$('#candidate').minLength, 32);
    assert.equal(app.$('#candidate').maxLength, 32);
    assert.match(app.$('#candidate').pattern, /32/);
    assert.match(app.$('#candidate-hint').textContent, /16-byte/);
    assert.match(app.$('#submission-context').textContent, /21/);
    assert.equal(app.$('#submission-manual-fields').hidden, false);
    assert.equal(app.$('#upload-recovery').disabled, false);
    assert.equal(app.$('#submit-answer').disabled, false);
    assert.equal(lookups(app).length, 1);
    assert.equal(mutations(app).length, 0);
  });
  await test('other-KEM import preserves the selected view and targets its exact registered release', async () => {
    const app = await fixtureApp({hash: route('fixture-20', '2', 'fixture-kem', 'leaderboard')});
    await importText(app, JSON.stringify(record('other-challenge', 16)));
    assert.equal(targetId(app), 'other-challenge');
    assert.equal(app.location.hash, route('other-42', '1', 'other-kem', 'leaderboard'));
    assert.equal(app.$('#candidate').value, 'ab'.repeat(16));
    assert.equal(mutations(app).length, 0);
  });
  await test('an open archived instance imports without being remapped to the current release', async () => {
    const app = await fixtureApp();
    await importText(app, JSON.stringify(record('archived-challenge', 16)));
    assert.equal(targetId(app), 'archived-challenge');
    assert.equal(app.location.hash, route('fixture-21', '1'));
    assert.equal(vm.runInContext('state.submissionChallenge.scheme_version', app.sandbox), '1');
    assert.equal(mutations(app).length, 0);
  });
  await test('manual tools supports file-first import without a selected challenge', async () => {
    for (const view of ['leaderboard', 'protocol']) {
      const app = await fixtureApp({hash: route('fixture-20', '2', 'fixture-kem', view), challengeResponder: (_, __, envelope) => envelope({challenge: null})});
      assert.equal(targetId(app), null);
      assert.equal(app.$('#submission-manual-fields').hidden, true);
      assert.equal(app.$('#submission-target-hint').hidden, false);
      assert.equal(app.$('#upload-recovery').disabled, false);
      assert.equal(app.$('#download-recovery-template').disabled, true);
      assert.equal(app.$('#submit-answer').disabled, true);
      await importText(app, JSON.stringify(record('other-challenge', 16)));
      assert.equal(targetId(app), 'other-challenge');
      assert.equal(app.$('#submission-manual-fields').hidden, false);
      assert.equal(app.$('#candidate').value, 'ab'.repeat(16));
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('invalid other-tier content cannot change the current draft, target or route', async () => {
    const app = await fixtureApp(); fillExisting(app); const before = draftSnapshot(app);
    for (const value of [record('second-challenge', 32), {...record('second-challenge', 16), compute: {...record().compute, workers: 0}}, {...record('second-challenge', 16), extra: 'unknown'}]) {
      await importText(app, JSON.stringify(value));
      assert.deepEqual(draftSnapshot(app), before);
      assert.equal(app.$('#recovery-file-error').hidden, false);
      assert.equal(mutations(app).length, 0);
    }
    assert.equal(lookups(app).length, 2, 'unknown root fields reject before any owned lookup');
  });
  await test('unavailable, foreign and unauthenticated lookups preserve the complete draft', async () => {
    for (const status of [401, 404, 503]) {
      const app = await fixtureApp({downloadResponder: () => ({status, body: {detail: status === 404 ? 'Challenge not found.' : 'Unavailable.'}})});
      fillExisting(app); const before = draftSnapshot(app);
      await importText(app, JSON.stringify(record('second-challenge', 16)));
      assert.deepEqual(draftSnapshot(app), before);
      assert.equal(app.$('#recovery-file-error').hidden, false);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('lookup must return the requested owned open unsubmitted unexpired instance with known metadata', async () => {
    const changes = [
      value => { value.id = 'different-id'; },
      value => { value.status = 'expired'; },
      value => { value.status = 'solved'; value.submission = {status: 'correct'}; },
      value => { value.status = 'incorrect'; value.submission = {status: 'incorrect'}; },
      value => { value.submission = {status: 'correct'}; },
      value => { value.ends_at = 0; },
      value => { delete value.ends_at; },
      value => { value.scheme_id = 'unregistered-kem'; },
      value => { value.scheme_version = 'unknown-version'; },
      value => { value.profile_id = 'unregistered-profile'; },
      value => { value.public.encoding.shared_secret_bytes = 0; }
    ];
    for (const change of changes) {
      const app = await fixtureApp({downloadResponder: (_, body) => { change(body.challenge); return {body}; }});
      fillExisting(app); const before = draftSnapshot(app);
      await importText(app, JSON.stringify(record('second-challenge', 16)));
      assert.deepEqual(draftSnapshot(app), before);
      assert.equal(app.$('#recovery-file-error').hidden, false);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('even same-instance import checks fresh server status after another tab submitted', async () => {
    const app = await fixtureApp({downloadResponder: (_, body) => ({body: {...body, challenge: {...body.challenge, status: 'solved', submission: {status: 'correct'}}}})});
    fillExisting(app); const before = draftSnapshot(app);
    await importText(app, JSON.stringify(record()));
    assert.equal(lookups(app).length, 1);
    assert.deepEqual(draftSnapshot(app), before);
    assert.equal(app.$('#recovery-file-error').hidden, false);
    assert.equal(mutations(app).length, 0);
  });
  await test('file-first invalid import leaves the target empty and upload available', async () => {
    const app = await fixtureApp({challengeResponder: (_, __, envelope) => envelope({challenge: null})});
    const before = draftSnapshot(app);
    await importText(app, JSON.stringify(record('does-not-exist')));
    assert.deepEqual(draftSnapshot(app), before);
    assert.equal(app.$('#upload-recovery').disabled, false);
    assert.equal(app.$('#submit-answer').disabled, true);
    assert.equal(mutations(app).length, 0);
  });
  await test('invalid identifiers and malformed headers cause no lookup or mutation', async () => {
    const app = await fixtureApp(); fillExisting(app); const before = draftSnapshot(app);
    for (const id of ['', '../different/path', 'a?candidate=secret', 'https://attacker.invalid', 'x'.repeat(101), 42, null]) {
      await importText(app, JSON.stringify(record(id)));
      assert.deepEqual(draftSnapshot(app), before);
    }
    assert.equal(lookups(app).length, 0);
    assert.equal(mutations(app).length, 0);
  });
  await test('an expired displayed target does not prevent importing a different open instance', async () => {
    const app = await fixtureApp();
    vm.runInContext('state.submissionChallenge.ends_at = 0', app.sandbox);
    await importText(app, JSON.stringify(record('second-challenge', 16)));
    assert.equal(targetId(app), 'second-challenge');
    assert.equal(app.$('#candidate').value, 'ab'.repeat(16));
    assert.equal(mutations(app).length, 0);
  });
  await test('import never loses an uncertain receipt when reopened from another page', async () => {
    const app = await fixtureApp(); fillExisting(app);
    vm.runInContext("state.pendingAttempt = {challengeId: 'fixture-challenge', payload: {candidate: 'pending-secret', idempotency_key: 'pending-retry'}}", app.sandbox);
    app.$('#submission-dialog').close();
    await app.navigate(route('other-42', '1', 'other-kem'));
    app.$('#open-submission').click(); await flush();
    const pendingBefore = vm.runInContext('JSON.stringify(state.pendingAttempt)', app.sandbox);
    await importText(app, JSON.stringify(record('other-challenge', 16)));
    assert.equal(vm.runInContext('JSON.stringify(state.pendingAttempt)', app.sandbox), pendingBefore);
    assert.equal(targetId(app), 'fixture-challenge');
    assert.equal(lookups(app).length, 0);
    assert.equal(mutations(app).length, 0);
  });
  await test('late owned lookup cannot populate a closed and reopened different form', async () => {
    let releaseLookup;
    const app = await fixtureApp({downloadResponder: (_, body) => new Promise(resolve => { releaseLookup = () => resolve({body}); })});
    await importText(app, JSON.stringify(record('second-challenge', 16)));
    assert.equal(typeof releaseLookup, 'function');
    app.$('#submission-dialog').close();
    await app.navigate(route('other-42', '1', 'other-kem'));
    app.$('#open-submission').click(); await flush();
    fillExisting(app); const before = draftSnapshot(app);
    releaseLookup(); await flush();
    assert.deepEqual(draftSnapshot(app), before);
    assert.equal(app.$('#upload-recovery').disabled, false);
    assert.equal(mutations(app).length, 0);
  });
  await test('logout or account change invalidates pending file reads and owned lookups', async () => {
    for (const stage of ['file', 'lookup']) for (const change of ['logout', 'other-account']) {
      let release;
      const options = stage === 'lookup' ? {downloadResponder: (_, body) => new Promise(resolve => { release = () => resolve({body}); })} : {};
      const app = await fixtureApp(options);
      const text = JSON.stringify(record('second-challenge', 16));
      const fileOptions = stage === 'file' ? {read: () => new Promise(resolve => { release = () => resolve(text); })} : {};
      await importText(app, text, fileOptions);
      vm.runInContext(change === 'logout' ? "setSession({user: null, csrf_token: 'next'})" : "setSession({user: {id:'other-account',handle:'other'}, csrf_token:'next'})", app.sandbox);
      const before = draftSnapshot(app);
      release(); await flush();
      assert.deepEqual(draftSnapshot(app), before);
      assert.equal(targetId(app), null);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('navigating during a file read or owned lookup prevents a late import from hijacking navigation', async () => {
    for (const stage of ['file', 'lookup']) {
      let release;
      const options = stage === 'lookup' ? {downloadResponder: (_, body) => new Promise(resolve => { release = () => resolve({body}); })} : {};
      const app = await fixtureApp(options); fillExisting(app);
      const text = JSON.stringify(record('second-challenge', 16));
      await importText(app, text, stage === 'file' ? {read: () => new Promise(resolve => { release = () => resolve(text); })} : {});
      await app.navigate('#about');
      const before = draftSnapshot(app);
      release(); await flush();
      assert.deepEqual(draftSnapshot(app), before);
      assert.equal(app.$('#upload-recovery').disabled, false);
      assert.equal(mutations(app).length, 0);
    }
  });
  await test('late original method-list and page lookup cannot replace a successfully imported other-tier draft', async () => {
    let releaseMethods, releasePage;
    const app = await fixtureApp({methods: new Promise(resolve => { releaseMethods = resolve; }), challengeResponder: (url, options, envelope, targets) => {
      if (url.searchParams.get('profile_id') === 'fixture-21') return new Promise(resolve => { releasePage = () => resolve(envelope({challenge: targets['second-challenge']})); });
    }});
    await importText(app, JSON.stringify(record('second-challenge', 16)));
    assert.equal(targetId(app), 'second-challenge');
    const before = draftSnapshot(app);
    releaseMethods({methods: [{id: 'old', name: 'Saved old method', pseudocode: 'Old code'}]});
    releasePage(); await flush();
    assert.deepEqual(draftSnapshot(app), before);
    assert.equal(app.$('#method-select').value, 'new');
    assert.equal(mutations(app).length, 0);
  });
  await test('instance expiring while its owned lookup is pending is rejected atomically', async () => {
    let releaseLookup;
    const app = await fixtureApp({downloadResponder: (_, body) => new Promise(resolve => { releaseLookup = () => resolve({body}); })});
    fillExisting(app); const before = draftSnapshot(app);
    await importText(app, JSON.stringify(record('second-challenge', 16)));
    vm.runInContext('state.timeOffset += 3600000', app.sandbox);
    releaseLookup(); await flush();
    assert.deepEqual(draftSnapshot(app), before);
    assert.equal(app.$('#recovery-file-error').hidden, false);
    assert.equal(mutations(app).length, 0);
  });
  await test('only an explicit final click posts the imported other-tier ID and strips instructions', async () => {
    const app = await fixtureApp({mutationResponder: (url, options, envelope) => {
      if (url.pathname === '/api/methods') return {method: {id: 'imported-method', ...JSON.parse(options.body)}};
      assert.equal(url.pathname, '/api/challenges/second-challenge/submit');
      return envelope({submission: {status: 'correct'}, challenge: {...app.targets['second-challenge'], status: 'solved', submission: {status: 'correct'}}});
    }});
    const value = record('second-challenge', 16); value._instructions = {candidate: 'Advisory description only.'};
    await importText(app, JSON.stringify(value));
    assert.equal(mutations(app).length, 0);
    app.$('#submission-form').dispatchEvent({type: 'submit'}); await flush();
    const posted = mutations(app);
    assert.deepEqual(posted.map(item => item.url.pathname), ['/api/methods', '/api/challenges/second-challenge/submit']);
    const body = JSON.parse(posted[1].options.body);
    assert.equal(body.candidate, 'ab'.repeat(16));
    assert.equal(Object.hasOwn(body, '_instructions'), false);
    assert.equal(Object.hasOwn(body, 'challenge_id'), false);
    assert.equal(app.$('#submission-dialog').open, false);
  });
  console.log(`\n${passed} recovery-upload regressions passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

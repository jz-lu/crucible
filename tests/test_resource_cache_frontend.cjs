// Synthetic graph values exercise browser ownership; these are not KEM evidence.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'cache-alice', handle: 'alice'};
const impl = {id: 'fixture', sha256: 'c'.repeat(64)}, method = 'd'.repeat(64);
const release = id => ({id, version: '1', name: id, protocol: {construction: 'Synthetic'},
  profiles: [{id: id + '-1', profile_id: id + '-1', instance_size: 1, parameters: {}, encoding: {shared_secret_bytes: 32}}]});
const schemes = Array.from({length: 8}, (_, i) => release('cache-' + i));
function report(scheme) {
  const stats = {complete: true, duration_ns: {median: 2000, q1: 1000, q3: 3000, n: 100}};
  return {projection: 'charts', scheme_id: scheme.id, version: '1', state: 'current', historical: [],
    publication: {id: 'a'.repeat(64), completeness: 'complete', received_at: 1800000000, source: {},
      binding: {implementation: impl, profiles: [{profile_id: scheme.profiles[0].id, configuration: 'config'}]}},
    resources: {measurement_profiles: {[method]: {profile: {method: {id: 'fixture'}, environment: {os: 'Linux', cpu: 'Fixture CPU'}, sampling: {workers: 5, trials: 20}}}},
      configurations: [{configuration: 'config', sizes_bytes: {public_key: 32, ciphertext: 64}, communication: {fresh_exchange_bytes: 96},
        native_evidence: [{implementation: impl, comparison_profile: method, platform_identified: true,
          computation: {keygen: stats, encapsulate: stats, decapsulate: stats}}]}]}};
}
const deferred = () => {let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve};};
const reads = app => app.requests.filter(r => r.url.pathname === '/api/protocol/resources');
const points = (app, card = 'public') => app.$('#overview-chart-' + card).querySelectorAll('.chart-point');
const notebook = {mount: () => ({show: async () => {}, hide() {}, setAccount() {}, invalidateReads() {}, warm: async () => ({bytes: 0})})};
async function fixture(extra) {
  return createApp({schemes, user, hash: '#about', notebook, responder: (url, options, envelope) => {
    const value = extra?.(url, options, envelope); if (value !== undefined) return value;
    if (url.pathname === '/api/session') return envelope({user, csrf_token: 'cache-session', capabilities: {review_protocol_proposals: true}});
    if (url.pathname === '/api/protocol/resources') return report(schemes.find(s => s.id === url.searchParams.get('scheme_id')));
    if (url.pathname === '/api/people/alice') return envelope({person: {handle: 'alice', records: []}});
    if (url.pathname === '/api/people') return envelope({people: []});
    if (url.pathname === '/api/me/submissions') return envelope({submissions: []});
    if (options.method === 'POST' || options.method === 'PUT') return envelope({});
  }});
}
let passed = 0, complete = false;
async function test(name, fn) {await fn(); console.log('PASS ' + name); passed++;}
process.once('beforeExit', () => {if (!complete && !process.exitCode) {console.error('Resource cache test did not finish'); process.exitCode = 1;}});
(async () => {
  await test('eight reviewer scheme jobs prepare compact graph data within existing budgets and never render hidden charts', async () => {
    const app = await fixture();
    for (let i = 0; i < 26; i++) {await vm.runInContext('runBrowseWarmup()', app.sandbox); await flush();}
    assert.equal(reads(app).length, 8);
    assert.ok(reads(app).every(r => r.url.searchParams.get('projection') === 'charts'));
    assert.equal(app.document.querySelectorAll('.resource-chart').length, 0);
    assert.ok(vm.runInContext('browseWarmState.done.size <= BROWSE_WARM_JOBS', app.sandbox));
    const bytes = vm.runInContext('browseWarmState.bytes', app.sandbox);
    assert.ok(bytes >= schemes.reduce((sum, s) => sum + Buffer.byteLength(JSON.stringify(report(s))), 0));
    assert.ok(bytes < 4 * 1024 * 1024);
    await app.navigate('#scheme/cache-7/1');
    assert.equal(reads(app).length, 8, 'First view reuses its prepared report');
    assert.equal(points(app).length, 1); assert.equal(points(app, 'cipher').length, 1); assert.equal(points(app, 'computation').length, 1);
    assert.match(app.$('#overview-chart-computation').textContent, /5 workers × 20 measured calls/);
    assert.match(app.$('#overview-chart-computation').textContent, /Fixture CPU/);
  });
  await test('foreground subscribers share and paint a still-pending optional request', async () => {
    const pending = deferred();
    const app = await fixture(url => url.pathname === '/api/protocol/resources' ? pending.promise : undefined);
    let warmed = 0;
    const task = app.window.CrucibleResources.load(schemes[0], app.sandbox.api, () => warmed++, {speculative: true});
    await app.navigate('#scheme/cache-0/1');
    assert.equal(reads(app).length, 1); assert.equal(points(app).length, 0);
    pending.resolve(report(schemes[0])); const result = await task; await flush();
    assert.equal(warmed, 1); assert.ok(result.bytes > 0);
    for (const card of ['public', 'cipher', 'computation']) assert.equal(points(app, card).length, 1);
    assert.equal(reads(app).length, 1);
  });
  await test('unrelated participant writes keep evidence while unknown writes and access rotation clear it', async () => {
    const app = await fixture(); await app.navigate('#scheme/cache-0/1');
    for (const [path, method] of [['/api/notebook/notes', 'POST'], ['/api/notebook/notes/note-id/revisions', 'POST'], ['/api/notebook/notes/note-id/publication-requests', 'POST'], ['/api/me/profile', 'PUT'], ['/api/challenges', 'POST'], ['/api/challenges/challenge-id/submit', 'POST']]) {
      await app.sandbox.api(path, {}, {method});
      await app.navigate('#about'); await app.navigate('#scheme/cache-0/1');
      assert.equal(points(app).length, 1); assert.equal(reads(app).length, 1, path);
    }
    await app.sandbox.api('/api/future-mutation', {});
    await app.navigate('#about'); await app.navigate('#scheme/cache-0/1');
    assert.equal(reads(app).length, 2);
    app.sandbox.setSession(app.envelope({user, csrf_token: 'rotated', capabilities: {review_protocol_proposals: true}}));
    await app.navigate('#about'); await app.navigate('#scheme/cache-0/1');
    assert.equal(reads(app).length, 3);
  });
  await test('quiet refresh retains the existing curve and control focus while validation is pending', async () => {
    const pending = deferred(); let count = 0;
    const app = await fixture(url => url.pathname === '/api/protocol/resources' ? ++count === 1 ? report(schemes[0]) : pending.promise : undefined);
    await app.navigate('#scheme/cache-0/1');
    const control = app.$('#overview-chart-public').querySelector('select'); control.focus();
    vm.runInContext('Date.now = () => ' + (Date.now() + 60001), app.sandbox);
    const task = app.window.CrucibleResources.load(schemes[0], app.sandbox.api, () => app.sandbox.redrawResourceCharts(schemes[0]));
    app.sandbox.redrawResourceCharts(schemes[0]);
    assert.equal(points(app).length, 1); assert.equal(app.document.activeElement, control);
    pending.resolve(report(schemes[0])); await task; await flush();
    assert.equal(app.$('#overview-chart-public').querySelector('select'), control);
    assert.equal(app.document.activeElement, control);
  });
  await test('failed optional reads do not poison a later visible request and page retirement detaches pending listeners', async () => {
    const app = await fixture(); const model = app.window.CrucibleResources;
    let changes = 0;
    await model.load(schemes[0], async () => {throw new Error('Synthetic offline');}, () => changes++, {speculative: true});
    assert.equal(changes, 0);
    await app.navigate('#scheme/cache-0/1'); assert.equal(points(app).length, 1); assert.equal(reads(app).length, 1);
    model.clear(); const pending = deferred();
    const task = model.load(schemes[1], () => pending.promise, () => changes++);
    model.invalidateReads(); pending.resolve(report(schemes[1])); await task;
    assert.equal(changes, 0);
    await model.load(schemes[1], async () => report(schemes[1]), () => changes++);
    assert.equal(changes, 1);
  });
  complete = true; console.log(passed + ' resource cache integration checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

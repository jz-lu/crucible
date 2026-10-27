// Synthetic UI fixture; not resource evidence for an actual cryptosystem.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const id = 'a'.repeat(64), method = 'b'.repeat(64);
const impl = {id: 'portable-v2', sha256: 'c'.repeat(64)};
const profiles = [1, 2].map(n => ({id: 'fixture-' + n, profile_id: 'fixture-' + n, instance_size: n, parameters: {n}, encoding: {shared_secret_bytes: 32}}));
const scheme = {id: 'resource-fixture', version: '1', name: 'Resource fixture', profiles, next_profile_id: 'fixture-1', profile_metrics: {'fixture-1': {public_key_bytes: 999, ciphertext_bytes: 999}}};
const op = {complete: true, duration_ns: {median: 2000, q1: 1000, q3: 3000, n: 100}};
const rows = profiles.map((p, i) => ({configuration: String(i), sizes_bytes: {public_key: 32 + i, ciphertext: 64, private_key: 48}, communication: {fresh_exchange_bytes: 96 + i, cached_exchange_bytes: 64},
  native_evidence: [{implementation: impl, comparison_profile: method, platform_identified: true, computation: {keygen: op, encapsulate: op, decapsulate: op}, first_call: {keygen: {...op, duration_ns: {...op.duration_ns, median: 6000, n: 5}}}, memory_components: {'keygen:rss': {complete: true, values: {max: 8192, n: 5}}}, profile: {environment: {cpu: 'Fixture CPU'}}}],
  stack_evidence: [{comparison_profile: method, operations: {keygen: {planned: 5, measured: 5, max_observed_bytes: 256}}}],
  memory_evidence: [{comparison_profile: method, operations: {keygen: {planned: 2, measured: 2, max_observed_bytes: 1024}}}]}));
const report = {scheme_id: scheme.id, version: scheme.version, publication: {id, received_at: 1800000000,
  binding: {implementation: impl, profiles: profiles.map((p,i) => ({profile_id: p.id, configuration: String(i)}))},
  source: {repository: 'countableclouds/crucible-schemes', commit: 'd'.repeat(40), run_id: '12345'}}, resources: {configurations: rows}};
(async () => {
  const app = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? structuredClone(report) : undefined});
  const publicChart = app.$('#overview-chart-public');
  assert.deepEqual(publicChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['32', '33']);
  assert.equal(app.$('#overview-badge-public').textContent, 'Repo measured');
  const change = async (selector, value) => { const select = app.$(selector).querySelector('select'); select.value = value; select.dispatchEvent({type: 'change'}); await flush(); };
  await change('#overview-chart-public', 'fresh');
  assert.deepEqual(publicChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['96', '97']);
  const chart = app.$('#overview-chart-computation');
  assert.deepEqual(chart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['2', '2']);
  assert.equal(chart.querySelectorAll('.resource-whisker').length, 2);
  for (const [view, value] of [['rss', '8192'], ['stack', '256'], ['heap_stack', '1024'], ['first', '6']]) {
    await change('#overview-chart-computation', view);
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(p => p.dataset.value), [value, value]);
  }
  assert.match(chart.textContent, /portable-v2/);
  assert.equal(chart.querySelector('.resource-evidence-links').querySelector('a').href, '/api/resource-publications/' + id + '/download');
  const model = app.window.CrucibleResources;
  assert.equal(model.coordinate({type: 'integer', decimal: '9007199254740993'}), null);
  assert.equal(model.exact({type: 'integer', decimal: '9007199254740993'}), '9007199254740993');
  assert.equal(model.point(rows[0], 'warm', 'keygen', 'different').value, null);
  assert.equal(model.point(undefined, 'rss', 'keygen', method).value, null);
  const incomplete = structuredClone(rows[0]); incomplete.native_evidence[0].computation.keygen.complete = false;
  assert.equal(model.point(incomplete, 'warm', 'keygen', method).value, null);
  const wrong = structuredClone(report); wrong.version = 'wrong';
  const other = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? wrong : undefined});
  assert.notEqual(other.$('#overview-badge-public').textContent, 'Repo measured');
  console.log('PASS published communication, time, RAM, evidence links, exact integers, mismatched profiles and release isolation');
})().catch(error => { console.error(error); process.exitCode = 1; });

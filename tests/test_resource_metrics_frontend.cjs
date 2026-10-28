// Synthetic UI fixture; not resource evidence for an actual cryptosystem.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const id = 'a'.repeat(64), method = 'b'.repeat(64);
const impl = {id: 'portable-v2', sha256: 'c'.repeat(64)};
const profiles = [1, 2].map(n => ({id: 'fixture-' + n, profile_id: 'fixture-' + n, instance_size: n, parameters: {n}, encoding: {shared_secret_bytes: 32}}));
const scheme = {id: 'resource-fixture', version: '1', name: 'Resource fixture', profiles, next_profile_id: 'fixture-1', profile_metrics: {'fixture-1': {public_key_bytes: 999, ciphertext_bytes: 999}}};
const op = {complete: true, duration_ns: {median: 2000, q1: 1000, q3: 3000, n: 100}};
const rows = profiles.map((p, i) => ({configuration: String(i), sizes_bytes: {public_key: 32 + i, ciphertext: 64, private_key: 48}, communication: {fresh_exchange_bytes: 96 + i, cached_exchange_bytes: 64},
  native_evidence: [{implementation: impl, comparison_profile: method, platform_identified: true, computation: {keygen: op, encapsulate: op, decapsulate: op}, first_call: {keygen: {...op, duration_ns: {...op.duration_ns, median: 6000, n: 5}}}, memory_components: {'keygen:rss': {complete: true, values: {max: 8192, n: 5}}}, profile: {environment: {cpu: 'Fixture CPU'}}}],
  stack_evidence: [{implementation: impl, comparison_profile: method, operations: {keygen: {planned: 5, measured: 5, max_observed_bytes: 256}}}],
  memory_evidence: [{implementation: impl, comparison_profile: method, operations: {keygen: {planned: 2, measured: 2, max_observed_bytes: 1024}}}]}));
const report = {scheme_id: scheme.id, version: scheme.version, state: 'current', historical: [], publication: {id, completeness: 'complete', received_at: 1800000000,
  binding: {implementation: impl, profiles: profiles.map((p,i) => ({profile_id: p.id, configuration: String(i)}))},
  source: {repository: 'countableclouds/crucible-schemes', commit: 'd'.repeat(40), run_id: '12345'}}, resources: {configurations: rows}};
(async () => {
  const app = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? structuredClone(report) : undefined});
  const publicChart = app.$('#overview-chart-public');
  assert.deepEqual(publicChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['32', '33']);
  assert.equal(app.$('#overview-badge-public').textContent, 'Current');
  const change = async (selector, value) => { const select = app.$(selector).querySelector('select[aria-label="Measure"]'); select.value = value; select.dispatchEvent({type: 'change'}); await flush(); };
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
  assert.notEqual(other.$('#overview-badge-public').textContent, 'Current');
  assert.equal(other.$('#overview-chart-public').querySelectorAll('.chart-point').length, 0);

  const choose = async (instance, card, label, value) => {
    const select = instance.$('#overview-chart-' + card).querySelector(`select[aria-label="${label}"]`);
    select.value = value; select.focus(); select.dispatchEvent({type: 'change'}); await flush();
    assert.equal(instance.document.activeElement, instance.$('#overview-chart-' + card).querySelector(`select[aria-label="${label}"]`));
  };
  for (const state of ['not_calculated', 'queued', 'failed', 'outdated', 'retired', 'cancelled', 'timed_out']) {
    const missing = {scheme_id: scheme.id, version: scheme.version, state, publication: null, resources: null, historical: []};
    const instance = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? missing : undefined});
    const target = instance.$('#overview-chart-public');
    assert.match(target.textContent, /Resources not calculated/);
    assert.equal(target.querySelectorAll('.chart-point').length, 0, 'legacy sizes must not silently fill missing reports');
    const security = instance.$('#overview-chart-security').textContent;
    await choose(instance, 'public', 'Report', 'legacy');
    assert.equal(instance.$('#overview-badge-public').textContent, 'Earlier site reference');
    assert.deepEqual(target.querySelectorAll('.chart-point').map(p => p.dataset.value), ['999']);
    await choose(instance, 'public', 'Report', 'current');
    assert.match(target.textContent, /Resources not calculated/);
    assert.equal(target.querySelectorAll('.chart-point').length, 0);
    assert.equal(instance.$('#overview-chart-security').textContent, security, 'resource choices leave security evidence unchanged');
  }

  const historicalId = 'e'.repeat(64), partialId = 'f'.repeat(64);
  const past = {...structuredClone(report), selected_historical: true, state: 'outdated'};
  past.publication.id = historicalId;
  const partial = {...structuredClone(past), resources: {configurations: []}};
  partial.publication.id = partialId; partial.publication.completeness = 'incomplete'; partial.publication.outcome = 'failed';
  const current = {scheme_id: scheme.id, version: scheme.version, state: 'outdated', publication: null, resources: null,
    historical: [past.publication, partial.publication]};
  past.historical = partial.historical = current.historical;
  const historyApp = await createApp({schemes: [{...scheme, record_inheritance: false, recovered_profile_ids: [profiles[1].id], largest_recovered_profile_id: profiles[1].id}], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources'
    ? url.searchParams.get('publication_id') === historicalId ? past : url.searchParams.get('publication_id') === partialId ? partial : current : undefined});
  await choose(historyApp, 'public', 'Report', historicalId);
  const historicalChart = historyApp.$('#overview-chart-public');
  assert.equal(historyApp.$('#overview-badge-public').textContent, 'Historical');
  assert.deepEqual(historicalChart.querySelectorAll('.chart-recovery-line').map(p => p.dataset.profileId), [profiles[1].id]);
  assert.deepEqual(historicalChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['32', '33']);
  await choose(historyApp, 'public', 'Report', partialId);
  assert.equal(historicalChart.querySelectorAll('.chart-point').length, 0);
  assert.match(historicalChart.textContent, /Incomplete report · not plotted/);
  assert.equal(historicalChart.querySelector('.resource-evidence-links').querySelector('a').href, '/api/resource-publications/' + partialId + '/download');
  await choose(historyApp, 'public', 'Report', historicalId);
  assert.equal(historyApp.requests.filter(r => r.url.searchParams.get('publication_id') === historicalId).length, 1, 'warm history selection uses its own retained report');

  past.selected_historical = false;
  historicalChart.querySelector('[data-resource-refresh]').click(); await flush();
  assert.equal(historyApp.$('#overview-badge-public').textContent, 'Selected report');
  assert.equal(historicalChart.querySelectorAll('.chart-point').length, 2, 'an exact selected publication remains usable if it is also current');

  vm.runInContext('Date.now = () => ' + (Date.now() + 300001), historyApp.sandbox);
  await choose(historyApp, 'public', 'Measure', 'fresh');
  assert.equal(historyApp.requests.filter(r => r.url.searchParams.get('publication_id') === historicalId).length, 3, 'an expired selected report is refreshed rather than left empty');
  assert.deepEqual(historicalChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['96', '97']);

  const zero = structuredClone(report);
  zero.resources.configurations[0].sizes_bytes.public_key = 0;
  zero.resources.configurations[0].native_evidence[0].computation.keygen = {complete: true, duration_ns: {median: 0, q1: 0, q3: 0, n: 100}};
  zero.resources.configurations[1].native_evidence[0].comparison_profile = '9'.repeat(64);
  const zeroApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? zero : undefined});
  assert.deepEqual(zeroApp.$('#overview-chart-public').querySelectorAll('.chart-point').map(p => p.dataset.value), ['0', '33']);
  const zeroChart = zeroApp.$('#overview-chart-computation');
  assert.deepEqual(zeroChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['0']);
  assert.match(zeroChart.querySelector('.chart-series').getAttribute('d'), /^M[^L]+$/, 'incompatible profiles never share a curve');
  await choose(zeroApp, 'computation', 'Measurement profile', '9'.repeat(64));
  assert.deepEqual(zeroChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['2']);
  const wrongImplementation = structuredClone(rows[0]); wrongImplementation.native_evidence[0].implementation.sha256 = '0'.repeat(64);
  assert.equal(model.point(wrongImplementation, 'warm', 'keygen', method, impl).value, null);

  const retryFailed = {...structuredClone(report), lifecycle: {phase: 'failed', reason: 'measurement_incomplete'}};
  const retainedApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? retryFailed : undefined});
  assert.equal(retainedApp.$('#overview-badge-public').textContent, 'Current');
  assert.equal(retainedApp.$('#overview-chart-public').querySelectorAll('.chart-point').length, 2);
  assert.match(retainedApp.$('#overview-chart-public').querySelector('details').textContent, /latest measurement attempt failed.*did not produce complete measurements/);

  const recoveredScheme = {...scheme, record_inheritance: false, recovered_profile_ids: profiles.map(p => p.id), largest_recovered_profile_id: profiles[1].id};
  const oldRelease = {...recoveredScheme, version: 'old', recovered_profile_ids: [profiles[0].id], largest_recovered_profile_id: profiles[0].id};
  recoveredScheme.previous_releases = [oldRelease];
  const recoveredApp = await createApp({schemes: [recoveredScheme], hash: '#scheme/resource-fixture/1', responder: (url, options, envelope) => url.pathname === '/api/protocol/resources'
    ? url.searchParams.get('version') === 'old' ? {...structuredClone(report), version: 'old'} : structuredClone(report)
    : url.pathname === '/api/schemes' ? envelope({schemes: [structuredClone(recoveredScheme)]}) : undefined});
  for (const card of ['public', 'cipher', 'computation', 'security']) {
    const target = recoveredApp.$('#overview-chart-' + card);
    assert.deepEqual(target.querySelectorAll('.chart-recovery-line').map(p => p.dataset.profileId), [profiles[1].id]);
    assert.equal(target.querySelector('.chart-recovery-line').getAttribute('x1'), '465');
    assert.equal(target.querySelector('.chart-recovery-area').getAttribute('width'), '417');
    assert.match(target.querySelector('desc').textContent, /not a security guarantee or recoveries at lower stages/);
  }
  recoveredScheme.recovered_profile_ids = [profiles[0].id]; recoveredScheme.largest_recovered_profile_id = profiles[0].id;
  await recoveredApp.sandbox.refreshProfiles({background:true});
  assert.equal(recoveredApp.$('#overview-chart-public').querySelector('.chart-recovery-line').getAttribute('x1'), '48', 'fresh progress invalidates only the relevant resource rendering');
  assert.equal(recoveredApp.$('#overview-chart-public').querySelector('.chart-recovery-area'), null);
  await recoveredApp.navigate('#scheme/resource-fixture/old');
  assert.deepEqual(recoveredApp.$('#overview-chart-public').querySelectorAll('.chart-recovery-line').map(p => p.dataset.profileId), [profiles[0].id], 'archived graphs use archived release records');
  const namedScheme = {...scheme, selection_kind: 'parameter-set', record_inheritance: false, recovered_profile_ids: [profiles[1].id], largest_recovered_profile_id: profiles[1].id};
  const namedApp = await createApp({schemes: [namedScheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? structuredClone(report) : undefined});
  const namedChart = namedApp.$('#overview-chart-public');
  assert.deepEqual(namedChart.querySelectorAll('.chart-recovery-line').map(p => p.dataset.profileId), [profiles[1].id]);
  assert.equal(namedChart.querySelector('.chart-recovery-area'), null, 'named sets do not inherit a shaded stage frontier');
  assert.equal(namedChart.querySelector('.chart-series'), null);

  let finishShared;
  const sharedPending = new Promise(resolve => { finishShared = resolve; });
  const sharedHistorical = {...structuredClone(report), state: 'outdated', selected_historical: true};
  sharedHistorical.publication.id = historicalId;
  const sharedCurrent = {scheme_id: scheme.id, version: scheme.version, state: 'outdated', publication: null, resources: null,
    historical: [sharedHistorical.publication]};
  sharedHistorical.historical = sharedCurrent.historical;
  const sharedApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources'
    ? url.searchParams.has('publication_id') ? sharedPending : sharedCurrent : undefined});
  const untouchedControl = sharedApp.$('#overview-chart-computation').querySelector('select[aria-label="Measure"]');
  await choose(sharedApp, 'public', 'Report', historicalId);
  await choose(sharedApp, 'cipher', 'Report', historicalId);
  assert.equal(sharedApp.requests.filter(r => r.url.searchParams.get('publication_id') === historicalId).length, 1, 'two cards share one pending historical read');
  finishShared(sharedHistorical); await flush();
  assert.deepEqual(sharedApp.$('#overview-chart-public').querySelectorAll('.chart-point').map(p => p.dataset.value), ['32', '33']);
  assert.deepEqual(sharedApp.$('#overview-chart-cipher').querySelectorAll('.chart-point').map(p => p.dataset.value), ['64', '64'], 'every waiting card repaints after a shared historical read');
  assert.equal(sharedApp.$('#overview-chart-computation').querySelector('select[aria-label="Measure"]'), untouchedControl, 'unchanged cards keep their controls');

  const stableSelect = publicChart.querySelector('select[aria-label="Measure"]'); stableSelect.focus();
  publicChart.querySelector('[data-resource-refresh]').click(); await flush();
  assert.equal(publicChart.querySelector('select[aria-label="Measure"]'), stableSelect, 'unchanged refresh keeps the existing controls');
  assert.equal(app.document.activeElement, stableSelect);

  let resolveRetired, changed = 0;
  model.clear();
  const pending = model.load(scheme, () => new Promise(resolve => { resolveRetired = resolve; }), () => changed++);
  model.clear(); resolveRetired(report); await pending;
  assert.equal(changed, 0, 'a retired account request cannot publish or redraw');
  await model.load(scheme, async () => report, () => changed++);
  assert.equal(changed, 1, 'the replacement account loads its own report');
  console.log('PASS resource states, explicit historical/legacy evidence, incomplete reports, zero values, profile isolation, focus, cache and retired-account requests');
})().catch(error => { console.error(error); process.exitCode = 1; });

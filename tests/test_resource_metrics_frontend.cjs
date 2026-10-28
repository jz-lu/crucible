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
  native_evidence: [{implementation: impl, comparison_profile: method, platform_identified: true, computation: {keygen: op, encapsulate: op, decapsulate: op}, first_call: {keygen: {...op, duration_ns: {median: 6000, q1: 5000, q3: 7000, n: 5}}}, memory_components: {'keygen:rss': {complete: true, values: {max: 8192, n: 5}}}, profile: {method: {id: 'serialized-native-honest-v2'}, environment: {os: 'Linux', cpu: 'Fixture CPU'}, sampling: {workers: 5, trials: 20}}}],
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
  for (const card of ['public', 'cipher', 'computation']) {
    const target = app.$('#overview-chart-' + card);
    const measure = target.querySelector('select[aria-label="Measure"]');
    if (card === 'cipher') assert.equal(measure, null, 'cached exchange is ciphertext, so no redundant choice');
    else assert.equal(measure.querySelectorAll('option').length, 2);
    assert.equal(target.querySelector('select[aria-label="Report"]'), null, 'one report does not need a selector');
    assert.equal(target.querySelector('select[aria-label="Measurement profile"]'), null, 'one measurement profile does not need a selector');
    assert.equal(target.closest('.overview-card').querySelector('.reference-timing-caption'), null);
  }
  const change = async (selector, value) => { const select = app.$(selector).querySelector('select[aria-label="Measure"]'); select.value = value; select.dispatchEvent({type: 'change'}); await flush(); };
  await change('#overview-chart-public', 'fresh');
  assert.deepEqual(publicChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['96', '97']);
  const chart = app.$('#overview-chart-computation');
  assert.deepEqual(chart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['2', '2']);
  assert.equal(chart.querySelectorAll('.resource-whisker').length, 2);
  assert.equal(chart.querySelectorAll('.resource-whisker-cap').length, 4);
  for (const cap of chart.querySelectorAll('.resource-whisker-cap')) {
    assert.equal(Number(cap.getAttribute('x2')) - Number(cap.getAttribute('x1')), 10);
    assert.equal(cap.getAttribute('y1'), cap.getAttribute('y2'));
  }
  assert.match(chart.querySelector('.chart-point').querySelector('title').textContent, /median 2 microseconds; middle 50% 1–3 microseconds; 100 samples/);
  assert.match(chart.querySelector('details').textContent, /not a confidence interval or a hardware correction/);
  assert.match(chart.querySelector('details').textContent, /5 workers × 20 measured calls each/);
  assert.deepEqual(chart.querySelector('thead').querySelectorAll('th').map(n => n.textContent), ['Configuration', 'Median (µs)', 'Middle 50% (µs)', 'Samples']);
  assert.deepEqual(chart.querySelector('tbody').querySelector('tr').querySelectorAll('td').map(n => n.textContent), ['Stage 1', '2', '1–3', '100']);
  for (const [view, value] of [['rss', '8192']]) {
    await change('#overview-chart-computation', view);
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(p => p.dataset.value), [value, value]);
  }
  assert.match(chart.textContent, /portable-v2/);
  assert.equal(chart.querySelector('.resource-evidence-links').querySelector('a').href, '/api/resource-publications/' + id + '/download');
  const model = app.window.CrucibleResources;
  const firstCall = model.point(rows[0], 'first', 'keygen', method, impl);
  assert.equal(firstCall.value, 6); assert.equal(firstCall.samples, 5);
  assert.equal(firstCall.q1, 5); assert.equal(firstCall.q3, 7);
  assert.equal(rows[0].native_evidence[0].computation.keygen.duration_ns.n, 100, 'retained first-call evidence stays separate from the displayed warm series');
  assert.equal(model.coordinate({type: 'integer', decimal: '9007199254740993'}), null);
  assert.equal(model.exact({type: 'integer', decimal: '9007199254740993'}), '9007199254740993');
  assert.equal(model.point(rows[0], 'warm', 'keygen', 'different').value, null);
  assert.equal(model.point(undefined, 'rss', 'keygen', method).value, null);
  const incomplete = structuredClone(rows[0]); incomplete.native_evidence[0].computation.keygen.complete = false;
  assert.equal(model.point(incomplete, 'warm', 'keygen', method).value, null);
  for (const stats of [{median: 2000, n: 100}, {median: 2000, q1: 3000, q3: 4000, n: 100},
    {median: 2000, q1: 0, q3: 1000, n: 100}, {median: 2000, q1: -1, q3: 4000, n: 100},
    {median: 2000, q1: 1000, q3: Infinity, n: 100}, {median: 2000, q1: 1000, q3: '3000', n: 100}]) {
    const row = structuredClone(rows[0]); row.native_evidence[0].computation.keygen.duration_ns = stats;
    const point = model.point(row, 'warm', 'keygen', method, impl);
    assert.equal(point.value, 2, 'a valid median survives missing or invalid quartiles');
    assert.equal(point.q1, null); assert.equal(point.q3, null);
  }
  const invalidRange = structuredClone(report);
  invalidRange.resources.configurations[0].native_evidence[0].computation.keygen.duration_ns = {median: 2000, q1: 3000, q3: 1e100, n: 100};
  invalidRange.resources.configurations[1].native_evidence[0].computation.keygen.duration_ns = {median: 2000};
  const medianApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? invalidRange : undefined});
  const medianChart = medianApp.$('#overview-chart-computation');
  assert.equal(medianChart.querySelectorAll('.resource-whisker').length, 0);
  assert.deepEqual(medianChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['2', '2']);
  assert.match(medianChart.querySelectorAll('.chart-point')[1].querySelector('title').textContent, /quartiles unavailable; sample count unavailable/);
  assert.equal(medianChart.querySelector('.chart-point').getAttribute('cy'), '43', 'invalid quartiles cannot distort the axis');
  const wrong = structuredClone(report); wrong.version = 'wrong';
  const other = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? wrong : undefined});
  assert.notEqual(other.$('#overview-badge-public').textContent, 'Current');
  assert.equal(other.$('#overview-chart-public').querySelectorAll('.chart-point').length, 0);
  assert.equal(other.requests.filter(r => r.url.pathname === '/api/protocol/resources').length, 1, 'rendering an unavailable current report respects the retry guard');

  const choose = async (instance, card, label, value) => {
    const select = instance.$('#overview-chart-' + card).querySelector(`select[aria-label="${label}"]`);
    if (select.closest('details')) select.closest('details').open = true;
    select.value = value; select.focus(); select.dispatchEvent({type: 'change'}); await flush();
    assert.equal(instance.document.activeElement, instance.$('#overview-chart-' + card).querySelector(`select[aria-label="${label}"]`));
  };
  await choose(app, 'computation', 'Measure', 'rss');
  assert.match(chart.querySelector('.resource-summary').textContent, /Includes pre-exec launcher memory/);
  assert.match(chart.querySelector('details').textContent, /not the current executable’s RSS alone/);
  assert.match(chart.querySelector('svg').getAttribute('aria-label'), /includes launcher memory/);
  for (const [profile, warning, scope] of [
    // The projection embeds the method and platform inside profile, not beside it.
    [{method: {id: 'serialized-native-honest-v2'}, environment: {os: 'Linux'}}, true, null],
    [{method: {id: 'serialized-native-honest-v1'}, environment: {os: 'Linux'}}, true, null],
    [{method: {id: 'serialized-native-honest-v2'}, environment: {os: 'Darwin'}}, false, null],
    [{method: {id: 'serialized-native-honest-v3', memory: {rss_methods: {Linux: {id: 'linux-proc-status-vmhwm-v1', scope: 'Current executable memory map resident high-water, excluding pre-exec launcher address spaces'}}}}, environment: {os: 'Linux', rss_method: 'linux-proc-status-vmhwm-v1'}}, false, 'Current executable memory map resident high-water'],
    [{method: {id: 'future-profile'}, environment: {os: 'Linux'}}, false, null],
    [{method: {id: 'serialized-native-honest-v2'}, environment: {}}, false, null],
  ]) {
    const scoped = structuredClone(report);
    for (const row of scoped.resources.configurations) row.native_evidence[0].profile = profile;
    const instance = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? scoped : undefined});
    await choose(instance, 'computation', 'Measure', 'rss');
    const target = instance.$('#overview-chart-computation');
    assert.equal(target.querySelector('.resource-summary').textContent.includes('Includes pre-exec launcher memory'), warning);
    if (scope) assert.ok(target.querySelector('details').textContent.includes(scope));
    assert.deepEqual(target.querySelectorAll('.chart-point').map(p => p.dataset.value), ['8192', '8192'], 'scope correction never changes historical values');
  }
  for (const state of ['not_calculated', 'queued', 'failed', 'outdated', 'retired', 'cancelled', 'timed_out']) {
    const missing = {scheme_id: scheme.id, version: scheme.version, state, publication: null, resources: null, historical: []};
    const instance = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? missing : undefined});
    const target = instance.$('#overview-chart-public');
    assert.match(target.textContent, /Resources not calculated/);
    assert.equal(target.querySelectorAll('.chart-point').length, 0, 'legacy sizes must not silently fill missing reports');
    const security = instance.$('#overview-chart-security').textContent;
    assert.equal(target.querySelector('select[aria-label="Report"]'), null);
    assert.doesNotMatch(target.textContent, /Earlier site reference/);
    await choose(instance, 'public', 'Measure', 'fresh');
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
  assert.ok(historicalChart.querySelector('select[aria-label="Report"]').closest('details'), 'historical report selection stays in the existing method disclosure');
  assert.equal(historicalChart.querySelectorAll('option').some(option => option.value === 'legacy'), false);
  assert.equal(historyApp.$('#overview-badge-public').textContent, 'Historical');
  assert.deepEqual(historicalChart.querySelectorAll('.chart-recovery-line').map(p => p.dataset.profileId), [profiles[1].id]);
  assert.deepEqual(historicalChart.querySelectorAll('.chart-point').map(p => p.dataset.value), ['32', '33']);
  await choose(historyApp, 'computation', 'Report', historicalId);
  await choose(historyApp, 'computation', 'Measure', 'rss');
  assert.equal(historyApp.$('#overview-badge-computation').textContent, 'Historical');
  assert.match(historyApp.$('#overview-chart-computation').querySelector('.resource-summary').textContent, /Includes pre-exec launcher memory/);
  assert.deepEqual(historyApp.$('#overview-chart-computation').querySelectorAll('.chart-point').map(p => p.dataset.value), ['8192', '8192']);
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

  let currentReads = 0, finishCurrent;
  const currentPending = new Promise(resolve => { finishCurrent = resolve; });
  const availableCurrent = {...structuredClone(report), historical: [past.publication]};
  const expiryApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => {
    if (url.pathname !== '/api/protocol/resources') return undefined;
    if (url.searchParams.has('publication_id')) return structuredClone(past);
    return ++currentReads === 1 ? structuredClone(availableCurrent) : currentPending;
  }});
  const initiallyLoaded = Date.now();
  vm.runInContext('Date.now = () => ' + (initiallyLoaded + 60001), expiryApp.sandbox);
  for (const card of ['public', 'cipher', 'computation']) await choose(expiryApp, card, 'Report', historicalId);
  // The current report expires while a more recently fetched historical report
  // remains usable. Selecting Current resources must request it without a reload.
  vm.runInContext('Date.now = () => ' + (initiallyLoaded + 300002), expiryApp.sandbox);
  for (const card of ['public', 'cipher', 'computation']) {
    const select = expiryApp.$('#overview-chart-' + card).querySelector('select[aria-label="Report"]');
    select.value = 'current'; select.dispatchEvent({type: 'change'}); await flush();
  }
  assert.equal(currentReads, 2, 'all three cards share exactly one expired-current refetch');
  const refreshedCurrent = structuredClone(availableCurrent);
  refreshedCurrent.resources.configurations[0].sizes_bytes.public_key = 42;
  finishCurrent(refreshedCurrent); await flush();
  for (const card of ['public', 'cipher', 'computation']) {
    const target = expiryApp.$('#overview-chart-' + card);
    assert.equal(expiryApp.$('#overview-badge-' + card).textContent, 'Current');
    assert.equal(target.querySelectorAll('.chart-point').length, 2);
    assert.equal(target.querySelector('select[aria-label="Report"]').value, 'current');
  }
  assert.deepEqual(expiryApp.$('#overview-chart-public').querySelectorAll('.chart-point').map(p => p.dataset.value), ['42', '33'], 'refreshed data replaces the expired current projection');
  assert.equal(currentReads, 2, 'the completed shared read does not trigger another fetch');

  const zero = structuredClone(report);
  zero.resources.configurations[0].sizes_bytes.public_key = 0;
  zero.resources.configurations[0].native_evidence[0].computation.keygen = {complete: true, duration_ns: {median: 0, q1: 0, q3: 0, n: 100}};
  zero.resources.configurations[1].native_evidence[0].comparison_profile = '9'.repeat(64);
  const zeroApp = await createApp({schemes: [scheme], hash: '#scheme/resource-fixture/1', responder: url => url.pathname === '/api/protocol/resources' ? zero : undefined});
  assert.deepEqual(zeroApp.$('#overview-chart-public').querySelectorAll('.chart-point').map(p => p.dataset.value), ['0', '33']);
  const zeroChart = zeroApp.$('#overview-chart-computation');
  assert.ok(zeroChart.querySelector('select[aria-label="Measurement profile"]').closest('details'));
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
  for (const status of [401, 403, 404]) {
    model.clear();
    await model.load(scheme, async () => report, () => {});
    const readsBefore = app.requests.length;
    await model.load(scheme, async () => { throw Object.assign(new Error('Access unavailable'), {status}); },
      () => app.sandbox.renderOverviewChart(scheme, 'public'), {refresh: true});
    assert.equal(publicChart.querySelectorAll('.chart-point').length, 0, 'denied reads cannot retain previously visible data');
    assert.equal(app.requests.length, readsBefore, 'render after denied access respects the retry guard');
  }
  console.log('PASS resource states, explicit historical evidence, incomplete reports, zero values, profile isolation, focus, cache and retired-account requests');
})().catch(error => { console.error(error); process.exitCode = 1; });

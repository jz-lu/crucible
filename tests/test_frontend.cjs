// Run with `node tests/test_frontend.cjs`. These fixtures are not registered KEMs.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile = (id, size) => ({id, profile_id: id, instance_size: size, name: id, shared_secret_bytes: 32, correctness: {kind: 'fixture', failure_bound: '0'}, parameters: {size}, encoding: {shared_secret_bytes: 32}});
const schemes = [
  {id: 'fixture-alpha', version: '7', name: 'Alpha <KEM>', summary: 'Only a test fixture.', participant_count: 2, educational: true, profiles: [profile('alpha-3', 3), profile('alpha-4', 4)], largest_recovered_profile_id: 'alpha-3', first_recovery: {submission_id: 'receipt-alpha', scheme_version: '7', profile_id: 'alpha-3', handle: 'first-alpha', received_at: 100}, next_profile_id: 'alpha-4', size_parameter: {name: 'Instance size', symbol: 'n', meaning: 'Alpha dimension', mapping: 'The alpha mapping.'}, protocol: {construction: 'Alpha construction.', source_url: '/api/protocol/source?version=7', example_url: null}},
  {id: 'fixture-beta', version: '2', name: 'Beta KEM', summary: 'A different test fixture.', participant_count: 0, educational: false, profiles: [profile('beta-5', 5), profile('beta-6', 6)], largest_recovered_profile_id: null, next_profile_id: 'beta-5', size_parameter: {name: 'Instance size', symbol: 'n', meaning: 'Beta rank', mapping: 'The beta mapping.'}, protocol: {construction: 'Beta construction.', source_url: null, example_url: null}}
];
const route = (scheme, selected, view = 'leaderboard') => `#challenge/${scheme.id}/${scheme.version}?profile=${selected}&view=${view}`;
const railButtons = app => app.$('#size-options').querySelectorAll('.size-option');
const railSize = button => Number(button.textContent.match(/\d+/)?.[0]);
const railSizes = app => railButtons(app).map(railSize);
const activeSizeButton = app => railButtons(app).find(button => button.getAttribute('aria-pressed') === 'true');
const assertSelectedTier = (app, tier) => {
  assert.equal(Number(app.$('#size-slider-value').textContent), tier);
  assert.equal(railSize(activeSizeButton(app)), tier);
  assert.equal(app.$('#profile-select').value, '', 'Jump to is a fresh input, not the selected tier display');
};
const isLargestRecovery = button => /largest recovered/.test(button.getAttribute('aria-label') || '');
const assertAdvanceVisibility = (app, visible) => {
  const button = app.$('#advance-size');
  assert.equal(button.hidden, false, 'The frontier action keeps its layout box');
  assert.equal(button.getAttribute('hidden'), null);
  assert.equal(button.classList.contains('is-concealed'), !visible);
  assert.equal(button.getAttribute('aria-hidden'), String(!visible));
  assert.equal(button.disabled, !visible);
};
const assertLeaderboardState = (app, title, notesVisible = false) => {
  assert.equal(app.$('#leaderboard-title').textContent, title);
  assert.equal(app.$('#leaderboard-scope').hidden, !notesVisible);
  assert.equal(app.$('#leaderboard-footnote').hidden, !notesVisible);
};
const ladderScheme = {...schemes[0], profiles: Array.from({length: 11}, (_, index) => profile('alpha-' + (20 + index), 20 + index)),
  largest_recovered_profile_id: 'alpha-28', next_profile_id: 'alpha-29', first_recovery: {submission_id: 'receipt-alpha-28', scheme_version: '7', profile_id: 'alpha-28', handle: 'first-alpha', received_at: 100}};
const namedProfiles = [512, 768, 1024].map((parameter, index) => ({...profile('named-' + parameter, index + 1),
  profile_kind: 'standard-parameter-set', profile_label: 'ML-KEM-' + parameter, profile_order: index + 1}));
const namedScheme = {id: 'fixture-named', version: '1', name: 'Named fixture', selection_kind: 'parameter-set',
  display_group: 'lattices', record_inheritance: false, scoring_eligible: false, review_status: 'local-beta',
  profiles: [namedProfiles[2], namedProfiles[0], namedProfiles[1]], recovered_profile_ids: ['named-768'],
  largest_recovered_profile_id: 'named-768', next_profile_id: 'named-512', participant_count: 0,
  profile_metrics: Object.fromEntries(namedProfiles.map((profile, index) => [profile.id, {public_key_bytes: 100 + index * 20, ciphertext_bytes: 70 + index * 10}]))};
const researchProfiles = Array.from({length: 50}, (_, index) => ({...profile('research-' + (index + 1), index + 1),
  profile_kind: 'research-tier', profile_label: 'Stage ' + (index + 1), initial_security_estimate: null,
  attack_reference: {log2_candidate_count: 500 + index, meaning: 'Exhaustive bound only; not a security estimate.'}}));
const researchScheme = {id: 'fixture-research', version: '2', name: 'Research fixture', selection_kind: 'tier',
  display_group: 'lattices', record_inheritance: false, scoring_eligible: false, review_status: 'local-beta',
  profiles: researchProfiles, largest_recovered_profile_id: 'research-8', next_profile_id: 'research-1',
  profile_recoveries: [{profile_id: 'research-2'}, {profile_id: 'research-8'}],
  profile_metrics: Object.fromEntries(researchProfiles.map((item, index) => [item.id, {public_key_bytes: 10 + index, ciphertext_bytes: 15 + index}]))};
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('correctness bounds retain their evidence class and model assumptions in the protocol view', async () => {
    const p={...profile('model-1',1),correctness:{kind:'statistical',failure_bound:'2^-128 under a stated model',per_profile_failure_bound:'2^-236',evidence_class:'heuristic-model-analytical-bound',assumptions:'<script>Not executable</script> Independent modeled rounding; not proved for concrete hashes.',argument:'Fixture derivation.',scope:'Honest key generation and encapsulation.'}};
    const value={...schemes[0],profiles:[p],next_profile_id:p.id};
    const app=await createApp({schemes:[value],hash:route(value,p.id,'protocol')});
    const facts=app.$('#correctness-facts');
    assert.match(facts.textContent,/2\^-128/);assert.match(facts.textContent,/2\^-236/);
    assert.match(facts.textContent,/Evidence class/);assert.match(facts.textContent,/Independent modeled rounding/);
    assert.match(facts.textContent,/not proved for concrete hashes/);assert.equal(facts.querySelector('script'),null);
  });
  await test('catalogue is the first page and displays every API KEM without issuing challenges', async () => {
    const app = await createApp({schemes});
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#workspace').hidden, true);
    assert.equal(app.$('#catalogue-title').textContent.trim(), 'KEM Schemes');
    assert.equal(app.$('#nav-challenge').textContent.trim(), 'Schemes');
    assert.match(app.$('#kem-catalogue').textContent, /Alpha <KEM>/);
    assert.match(app.$('#kem-catalogue').textContent, /Beta KEM/);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('th').map(heading => heading.textContent),
      ['KEM', 'Largest recovery', 'Recovered by', 'Participants']);
    assert.match(app.$('#kem-catalogue').textContent, /first-alpha/);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-participants').map(item => item.textContent), ['2', '0']);
    const participantHeading = app.$('#kem-catalogue').querySelectorAll('th').at(-1);
    assert.match(participantHeading.title, /Distinct accounts.*at least one submitted recovery attempt.*successful or unsuccessful.*compatible releases/);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-first-handle').map(button => button.closest('td').dataset.label), ['Recovered by']);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
    const link = app.$('#kem-catalogue').querySelectorAll('a').find(item => item.href.includes('fixture-beta'));
    assert.ok(link, 'Catalogue supplies a shareable KEM link');
    link.click(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.$('#overview-workspace').hidden, false);
    assert.equal(app.$('#workspace').hidden, true);
    assert.equal(app.$('#overview-title').textContent, 'Beta KEM');
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
    app.$('#overview-challenges').click(); await flush();
    assert.equal(app.$('#workspace').hidden, false);
    assert.equal(app.$('#scheme-title').textContent, 'Beta KEM');
    assertSelectedTier(app, 5);
    assert.equal(app.$('#profile-select').min, '5');
    assert.equal(app.$('#profile-select').max, '6');
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('a KEM name opens its overview and View challenges opens its next unrecovered profile', async () => {
    const app = await createApp({schemes});
    app.$('#kem-catalogue').querySelectorAll('.kem-name')[0].click(); await flush();
    assert.equal(app.$('#overview-workspace').hidden, false);
    assert.equal(app.location.hash, '#scheme/fixture-alpha/7');
    assert.equal(app.$('#overview-challenges').href, route(schemes[0], 'alpha-4'));
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges' || request.url.pathname === '/api/leaderboard'), false);
    app.$('#overview-challenges').click(); await flush();
    assertSelectedTier(app, 4);
    assert.equal(app.$('#panel-leaderboard').hidden, false);
    assert.equal(app.$('#panel-challenge').hidden, true);
    assert.equal(app.$('#tab-leaderboard').getAttribute('aria-selected'), 'true');
    const board = app.requests.find(request => request.url.pathname === '/api/leaderboard');
    assert.equal(board.url.searchParams.get('profile_id'), 'alpha-4');
    assert.equal(board.url.searchParams.get('scheme_id'), 'fixture-alpha');
    assert.equal(board.url.searchParams.get('scheme_version'), '7');
    const direct = await createApp({schemes, hash: '#challenge/fixture-alpha/7'});
    assertSelectedTier(direct, 4);
    assert.equal(direct.$('#panel-leaderboard').hidden, false);
    assert.match(direct.location.hash, /profile=alpha-4/);
  });
  await test('catalogue orders participant counts descending with alphabetical ties, independently of recovered stages and bytes', async () => {
    const fixture = (name, size, participants) => {
      const id = 'fixture-' + name.toLowerCase().replace(/ /g, '-');
      const selected = profile(id + '-size', size ?? 128);
      return {...schemes[0], id, name, profiles: [selected],
        largest_recovered_profile_id: size === null ? null : selected.id,
        first_recovery: null, next_profile_id: selected.id, participant_count: participants,
        profile_metrics: {[selected.id]: {public_key_bytes: size, ciphertext_bytes: size}}};
    };
    const catalogue = [fixture('Nine', 9, 5), fixture('Zebra', 36, 12), fixture('A waiting', null, 20),
      fixture('Century', 100, 0), fixture('Z waiting', null, 0), fixture('Amber', 36, 12)];
    const app = await createApp({schemes: catalogue});
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-name').map(link => link.textContent),
      ['A waiting', 'Amber', 'Zebra', 'Nine', 'Century', 'Z waiting']);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-participants').map(label => label.textContent),
      ['20', '12', '12', '5', '0', '0']);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-progress').map(link => link.textContent),
      ['Stage 36', 'Stage 36', 'Stage 9', 'Stage 100']);
    assert.equal(app.document.querySelector('[data-recovery-metric]'), null);
    assert.equal(app.document.querySelector('#recovery-measure-note'), null);
    assert.equal(app.document.querySelector('.kem-metric-tier'), null);
    assert.equal(app.$('#kem-catalogue').querySelectorAll('th').find(heading => heading.textContent === 'Largest recovery').getAttribute('aria-sort'), null);
    assert.equal(app.$('#kem-catalogue').querySelector('th').getAttribute('aria-sort'), null);
    assert.equal(app.$('#kem-catalogue').querySelectorAll('th').at(-1).getAttribute('aria-sort'), 'descending');
    app.$('#kem-catalogue').querySelectorAll('.kem-name').find(link => link.textContent === 'Nine').click(); await flush();
    assert.equal(app.$('#overview-title').textContent, 'Nine');
    app.$('#overview-challenges').click(); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Nine');
    assertSelectedTier(app, 9);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('participant counts treat zero as reported and missing or malformed counts as unknown, with stable name and ID ties', async () => {
    const fixture = (id, name, count) => ({...schemes[0], id, name, participant_count: count});
    const invalid = [null, undefined, '64', -1, 2.5, true, [], {}, Number.MAX_SAFE_INTEGER + 1]
      .map((count, index) => fixture('invalid-' + index, 'A unknown ' + index, count));
    const zero = fixture('zero', 'Zero', 0);
    const sameZ = fixture('same-z', 'amber', 2);
    const sameA = fixture('same-a', 'Amber', 2);
    const most = fixture('most', 'Z most', 1200);
    const app = await createApp({schemes: [zero, ...invalid.slice().reverse(), sameZ, most, sameA]});
    const links = app.$('#kem-catalogue').querySelectorAll('.kem-name');
    assert.deepEqual(links.map(link => link.href.split('/')[1]),
      ['most', 'same-a', 'same-z', 'zero', ...invalid.map(scheme => scheme.id)]);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-participants').map(label => label.textContent),
      ['1,200', '2', '2', '0', ...invalid.map(() => '—')]);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('catalogue recovery links keep exact stages while scheme names open release-specific overviews', async () => {
    const old = {...schemes[0], version: '6', summary: 'Archived construction.', profiles: [profile('alpha-3', 3)], next_profile_id: null,
      profile_metrics: {'alpha-3': {public_key_bytes: 7, ciphertext_bytes: 8}},
      overview: {strengths: ['Original scope.'], limitations: ['Old parameters.'], correctness: 'Original correctness claim.', security: 'Original security scope.'}};
    const current = {...schemes[0], previous_releases: [old],
      profile_metrics: {'alpha-3': {public_key_bytes: 90, ciphertext_bytes: 110}, 'alpha-4': {public_key_bytes: 91, ciphertext_bytes: 111}}};
    const app = await createApp({schemes: [current]});
    const row = app.$('#kem-catalogue').querySelector('.kem-name').closest('tr');
    assert.equal(row.querySelector('.kem-name').href, '#scheme/fixture-alpha/7');
    assert.equal(row.querySelector('.kem-progress').textContent, 'Stage 3');
    assert.equal(row.querySelector('.kem-progress').href, route(current, 'alpha-3'));
    assert.equal(row.querySelector('.kem-first-handle').href, '#person/first-alpha');
    row.querySelector('.kem-progress').click(); await flush();
    assertSelectedTier(app, 3);
    assert.equal(app.$('#panel-leaderboard').hidden, false);
    assert.equal(app.$('#scheme-back').href, '#scheme/fixture-alpha/7?profile=alpha-3');
    app.$('#scheme-back').click(); await flush();
    assert.equal(app.$('#overview-workspace').hidden, false);
    assert.equal(app.document.activeElement, app.$('#overview-workspace'));
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'), 'page');
    assert.equal(app.$('.skip-link').href, '#overview-workspace');
    assert.equal(app.document.title, 'Alpha <KEM> overview — Crucible');
    assert.equal(app.$('#overview-challenges').href, route(current, 'alpha-3'), 'Returning through Overview preserves the selected tier');
    const requests = app.requests.length;
    await app.navigate('#scheme/fixture-alpha/6');
    assert.equal(app.requests.length, requests + 1);
    assert.equal(app.requests.at(-1).url.pathname, '/api/protocol/benchmarks');
    assert.equal(app.requests.at(-1).url.searchParams.get('version'), '6');
    assert.match(app.$('#overview-release').textContent, /Archived release 6/);
    assert.equal(app.$('#overview-summary').textContent, 'Archived construction.');
    assert.deepEqual(app.$('#overview-chart-public').querySelectorAll('.chart-point').map(point => point.dataset.value), ['7']);
    assert.equal(app.$('#overview-correctness').textContent, 'Original correctness claim.');
    assert.equal(app.$('#overview-challenges').href, route(old, 'alpha-3'));
    app.$('#overview-challenges').click(); await flush();
    assert.match(app.location.hash, /fixture-alpha\/6\?/);
    app.$('#scheme-all').click(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    app.history.back(); await flush();
    assert.match(app.location.hash, /fixture-alpha\/6\?/);
    app.history.back(); await flush();
    assert.equal(app.$('#overview-workspace').hidden, false);
    assert.match(app.$('#overview-release').textContent, /Archived release 6/);
    for (const hash of ['#scheme/fixture-alpha/999', '#scheme/missing/7', '#scheme/%ZZ/7', '#scheme/fixture-alpha/6?profile=alpha-4']) {
      await app.navigate(hash);
      assert.equal(app.$('#overview-workspace').hidden, true);
      assert.equal(app.$('#catalogue-workspace').hidden, false);
      assert.equal(app.$('#global-message').hidden, false);
    }
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('mixed catalogues leave standalone schemes ungrouped and preserve exact named recovery links', async () => {
    const named = (id, group) => ({...namedScheme, id, name: id, display_group: group});
    const input = [named('codes-b', 'codes'), schemes[1], named('lattice-b', 'lattices'),
      named('curve', null), named('lattice-a', 'lattices'), schemes[0], named('codes-a', 'codes'), named('lattice-c', 'lattices')];
    const app = await createApp({schemes: input});
    const table = app.$('#kem-catalogue');
    assert.equal(table.querySelectorAll('.kem-name').length, 8);
    assert.deepEqual(table.querySelectorAll('.kem-group-row').map(row => row.textContent), ['Lattice-based3', 'Code-based2']);
    assert.deepEqual(table.querySelectorAll('.kem-name').map(link => link.href.split('/')[1]),
      ['fixture-alpha', 'fixture-beta', 'curve', 'lattice-a', 'lattice-b', 'lattice-c', 'codes-a', 'codes-b']);
    const link = table.querySelectorAll('.kem-name').find(link => link.textContent === 'curve');
    const progress = link.closest('tr').querySelector('.kem-progress');
    assert.equal(progress.textContent, 'ML-KEM-768');
    assert.equal(progress.href, route(input.find(scheme => scheme.id === 'curve'), 'named-768'));
    assert.equal(table.querySelectorAll('th').find(th => th.textContent === 'Participants').getAttribute('aria-sort'), null, 'Grouped ordering is not a global participant ranking');
  });
  await test('named sets select exact profiles without a tier ladder or inherited recovery colors', async () => {
    const app = await createApp({schemes: [namedScheme], hash: route(namedScheme, 'named-1024')});
    assert.equal(app.$('#profile-label').textContent, 'Parameter set');
    for (const id of ['#tier-size-rail', '#tier-size-slider', '#tier-size-jump']) assert.equal(app.$(id).hidden, true);
    const buttons = () => app.$('#parameter-set-options').querySelectorAll('button');
    assert.deepEqual(buttons().map(button => button.textContent), ['ML-KEM-512', 'ML-KEM-768', 'ML-KEM-1024']);
    assert.deepEqual(buttons().filter(button => button.classList.contains('is-recovered')).map(button => button.textContent), ['ML-KEM-768']);
    app.$('#tab-protocol').click(); await flush();
    assert.match(app.$('#parameter-facts').textContent, /Parameter setML-KEM-1024/);
    assert.doesNotMatch(app.$('#parameter-facts').textContent, /Stage 3/);
    app.$('#tab-leaderboard').click(); await flush();
    assertAdvanceVisibility(app, true);
    app.$('#advance-size').click(); await flush();
    assert.equal(app.location.hash, route(namedScheme, 'named-512'));
    assertAdvanceVisibility(app, false);
    buttons()[1].click(); await flush();
    assert.equal(app.location.hash, route(namedScheme, 'named-768'));
    assert.match(app.$('#leaderboard-scope').textContent, /this exact parameter set/);
    assert.match(app.$('#scheme-beta').textContent, /Independent admission review pending/);
    const prompt = app.sandbox.agentInstructions({id: 'fixture-exact-challenge', ends_at: 2000000000, public: {scheme_id: namedScheme.id}});
    assert.match(prompt, /get --challenge-id 'fixture-exact-challenge'/);
    assert.match(prompt, /init 'fixture-named' --profile-id 'named-768'/);
    assert.doesNotMatch(prompt, /--tier|Stage 2/);
    const receipt = {scheme_id: namedScheme.id, scheme_version: '1', profile_id: 'named-768', instance_size: 2};
    assert.equal(app.sandbox.recordProfileLabel(receipt), 'ML-KEM-768');
    assert.equal(app.sandbox.recordProfileLabel({...receipt, scheme_version: 'archived', profile_kind: 'standard-parameter-set', profile_label: 'Exact old set'}), 'Exact old set');
    assert.match(app.sandbox.personalRecordTable([{...receipt, is_current: true, received_at: 100}]).textContent, /Stage \/ parameter set/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('named-set graphs use discrete points and exact recovery markers without interpolation or security invention', async () => {
    const app = await createApp({schemes: [{...namedScheme, recovered_profile_ids: undefined,
      profile_recoveries: [{profile_id: 'named-768'}, {profile_id: 'named-512'}]}], hash: '#scheme/fixture-named/1'});
    for (const id of ['public', 'cipher', 'computation', 'security']) {
      const chart = app.$('#overview-chart-' + id);
      assert.equal(chart.querySelector('.chart-series'), null);
      assert.equal(chart.querySelector('.chart-recovery-area'), null);
      assert.deepEqual(chart.querySelectorAll('.chart-recovery-line').map(line => line.dataset.profileId), ['named-512', 'named-768']);
      assert.match(chart.querySelector('desc').textContent, /Distinct named parameter sets.*not interpolated/);
    }
    const chart = app.$('#overview-chart-public');
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(point => [point.dataset.profileId, point.dataset.value]),
      [['named-512', '100'], ['named-768', '120'], ['named-1024', '140']]);
    assert.match(chart.querySelector('table').textContent, /Parameter set.*ML-KEM-512100.*ML-KEM-768120.*ML-KEM-1024140/);
    assert.equal(app.$('#overview-chart-security').querySelectorAll('.chart-point').length, 0);
    assert.equal(app.$('#overview-chart-computation').querySelectorAll('.chart-point').length, 0);
    const single = {...namedScheme, profiles: [namedProfiles[0]], largest_recovered_profile_id: null, recovered_profile_ids: []};
    const one = await createApp({schemes: [single], hash: '#scheme/fixture-named/1'});
    const dot = one.$('#overview-chart-public').querySelector('.chart-point');
    assert.equal(Number.isFinite(Number(dot.getAttribute('cx'))), true);
    assert.equal(one.$('#overview-chart-public').querySelectorAll('.chart-point').length, 1);
    assert.match(one.$('#overview-release').textContent, /1 parameter set$/);
    assert.equal(one.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('research stages preserve the slider but color only exact recoveries and navigate the first unsolved profile', async () => {
    const app = await createApp({schemes: [researchScheme], hash: route(researchScheme, 'research-4', 'protocol')});
    assert.equal(app.$('#tier-size-slider').hidden, false);
    assert.equal(app.$('#parameter-set-options').hidden, true);
    assert.equal(app.$('#size-slider').max, '49');
    assert.match(app.$('#size-slider').style.cssText, /--recovered-through: 0%/);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-prior')).map(railSize), [2]);
    assert.doesNotMatch(railButtons(app).find(button => railSize(button) === 3).getAttribute('aria-label'), /recovered|earlier stage/);
    assert.match(app.$('#scheme-beta').textContent, /Research stages.*not scored/);
    assert.match(app.$('#profile-note').textContent, /Security estimates are not yet available/);
    assert.match(app.$('#scheme-specific-facts').textContent, /Protocol relationship.*research parameter family/);
    assert.match(app.$('#protocol-record-rules').textContent, /exact stage.*do not transfer/);
    assert.doesNotMatch(app.$('#protocol-record-rules').textContent, /or higher/);
    const records = app.sandbox.personalRecordTable([{scheme_id: researchScheme.id, scheme_version: '2', profile_id: 'research-2',
      tier: 2, record_inheritance: false, scoring_eligible: false, received_at: 100}]);
    assert.match(records.textContent, /Highest stage/);
    assert.doesNotMatch(records.textContent, /Personal best/);
    assert.equal(app.$('#advance-size-value').textContent, '1');
    app.$('#advance-size').click(); await flush();
    assert.equal(app.location.hash, route(researchScheme, 'research-1', 'protocol'));
    assertAdvanceVisibility(app, false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('all research graphs highlight the range to one highest-recovered tier line without inventing recoveries or security', async () => {
    const app = await createApp({schemes: [researchScheme], hash: '#scheme/fixture-research/2'});
    assert.match(app.$('#overview-release').textContent, /50 challenge stages/);
    assert.match(app.$('#overview-beta').textContent, /Research stages/);
    for (const id of ['public', 'cipher', 'computation', 'security']) {
      const chart = app.$('#overview-chart-' + id);
      const area = chart.querySelector('.chart-recovery-area');
      assert.ok(Number(area.getAttribute('width')) > 0);
      assert.equal(Number(area.getAttribute('x')) + Number(area.getAttribute('width')),
        Number(chart.querySelector('.chart-recovery-line').getAttribute('x1')));
      assert.deepEqual(chart.querySelectorAll('.chart-recovery-line').map(line => line.dataset.tier), ['8']);
      assert.equal(chart.querySelector('.overview-recovery-note').textContent, 'Largest recovered · Stage 8');
      assert.match(chart.querySelector('desc').textContent, /highest recorded stage, not a security guarantee or recoveries at lower stages/);
    }
    assert.equal(app.$('#overview-chart-public').querySelectorAll('.chart-point').length, 50);
    assert.equal(app.$('#overview-chart-cipher').querySelectorAll('.chart-point').length, 50);
    assert.equal(app.$('#overview-chart-security').querySelectorAll('.chart-point').length, 0);
    assert.equal(app.$('#overview-chart-computation').querySelectorAll('.chart-point').length, 0);
    assert.doesNotMatch(app.$('#overview-chart-security').textContent, /500|549/);
    const unrecovered = {...researchScheme, largest_recovered_profile_id: null, recovered_profile_ids: [], profile_recoveries: []};
    const empty = await createApp({schemes: [unrecovered], hash: '#scheme/fixture-research/2'});
    assert.equal(empty.$('#overview-graphs').querySelectorAll('.chart-recovery-line').length, 0);
    assert.equal(empty.$('#overview-progress').textContent, '');
    assert.equal(empty.$('#overview-progress').hidden, true);
    await empty.navigate(route(unrecovered, 'research-1'));
    assertSelectedTier(empty, 1);
    assert.equal(empty.$('#leaderboard-title').textContent, 'Unsolved');
    assert.match(empty.$('.tier-record-invitation').textContent, /Stage 1/);
    assert.equal(railButtons(empty).some(button => button.classList.contains('is-prior')), false);
    assert.equal(empty.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('research attack references are labeled honestly while explicit inherited-record policy is preserved', async () => {
    const reference = {...researchScheme, record_inheritance: true, initial_security_model: {id: 'fixture-rho', status: 'limited-reference'},
      profiles: researchProfiles.map(item => ({...item, initial_security_estimate: {model_id: 'fixture-rho', bits: item.instance_size + 7}}))};
    const app = await createApp({schemes: [reference], hash: route(reference, 'research-4', 'protocol')});
    assert.match(app.$('#profile-note').textContent, /Initial classical attack reference: ≈11 bits/);
    assert.match(app.$('#parameter-facts').textContent, /Initial classical attack reference/);
    assert.match(app.$('#protocol-record-rules').textContent, /stage or higher/);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-prior')).map(railSize), [2, 3, 4, 5, 6]);
    assert.doesNotMatch(app.$('#size-slider').style.cssText, /--recovered-through: 0%/);
    await app.navigate('#scheme/fixture-research/2');
    assert.ok(app.$('#overview-chart-public').querySelector('.chart-recovery-area'));
    assert.deepEqual(app.$('#overview-chart-public').querySelectorAll('.chart-recovery-line').map(line => line.dataset.tier), ['8']);
    assert.equal(app.$('#overview-chart-security').querySelectorAll('.chart-point').length, 50);
    assert.equal(app.$('#overview-badge-security').textContent, 'Attack reference');
    assert.match(app.$('#overview-chart-security').textContent, /Other attacks may be faster/);
  });
  await test('exact research records reject a different profile or inherited result rather than displaying it as solved', async () => {
    for (const wrong of [{profile_id: 'research-8', is_inherited: false}, {profile_id: 'research-2', is_inherited: true}]) {
      const app = await createApp({schemes: [researchScheme], hash: route(researchScheme, 'research-2'), responder: (url, options, envelope) =>
        url.pathname === '/api/leaderboard' ? envelope({entries: [{rank: 1, handle: 'wrong-profile', received_at: 100, ...wrong}]}) : undefined});
      assert.equal(app.$('#tier-record').getAttribute('data-state'), 'error');
      assert.match(app.$('#leaderboard-content').textContent, /exact profile/);
      assert.equal(app.$('#leaderboard-content').querySelector('.tier-winner-name'), null);
    }
    const app = await createApp({schemes: [researchScheme], hash: route(researchScheme, 'research-2'), responder: (url, options, envelope) =>
      url.pathname === '/api/leaderboard' ? envelope({entries: [{rank: 1, handle: 'exact-solver', profile_id: 'research-2', received_at: 100}]}) : undefined});
    assert.equal(app.$('#tier-record').getAttribute('data-state'), 'solved');
    assert.match(app.$('#leaderboard-scope').textContent, /this exact stage/);
    assert.doesNotMatch(app.$('#leaderboard-footnote').textContent, /Via Stage/);
    assert.equal(app.$('#leaderboard-content').querySelector('.leaderboard-credit'), null);
  });
  await test('fixed encoding graphs and switchable cost/security graphs preserve sparse stages without requests', async () => {
    const fixture = {...schemes[0], profiles: [20, 21, 50, 90].map(tier => profile('alpha-' + tier, tier)),
      largest_recovered_profile_id: 'alpha-50', next_profile_id: 'alpha-90',
      profile_metrics: {'alpha-20': {public_key_bytes: 0, ciphertext_bytes: 8}, 'alpha-21': {public_key_bytes: null, ciphertext_bytes: 9},
        'alpha-50': {public_key_bytes: 6, ciphertext_bytes: 10}, 'alpha-90': {public_key_bytes: 8, ciphertext_bytes: 12}},
      overview: {strengths: ['<script>not HTML</script>'], limitations: ['Specific limits.'], correctness: 'Exact fixture.', security: 'No security certification.'}};
    fixture.profiles[2].shared_secret_bytes = 16;
    const app = await createApp({schemes: [fixture], user: {id: 'active-account', handle: 'active'}, hash: '#scheme/fixture-alpha/7'});
    const chart = id => app.$('#overview-chart-' + id);
    const points = id => chart(id).querySelectorAll('.chart-point');
    assert.equal(app.$('#overview-graphs').querySelectorAll('.overview-card').length, 4);
    assert.deepEqual(points('public').map(point => [point.dataset.tier, point.dataset.value]), [['20', '0'], ['50', '6'], ['90', '8']]);
    assert.equal((chart('public').querySelector('.chart-series').getAttribute('d').match(/M/g) || []).length, 2, 'Missing data breaks the line instead of becoming zero');
    const [first, middle, last] = points('public').map(point => Number(point.getAttribute('cx')));
    assert.ok(Math.abs((middle - first) / (last - first) - 30 / 70) < 0.0001, 'Sparse stages use their numerical positions');
    assert.match(chart('public').querySelector('table').textContent, /21Not reported/);
    assert.equal(chart('public').querySelector('.overview-values-scroll').tabIndex, 0);
    assert.equal(chart('public').querySelector('.overview-values-scroll').getAttribute('aria-label'), 'Public-key size values by challenge stage');
    assert.equal(chart('public').querySelector('svg').namespaceURI, 'http://www.w3.org/2000/svg');
    assert.equal(chart('public').querySelector('svg').getAttribute('role'), 'img');
    assert.match(chart('public').querySelector('desc').textContent, /3 published values.*20 to 90/);
    assert.equal(app.$('#overview-strengths').querySelectorAll('script').length, 0);
    assert.match(app.$('#overview-strengths').textContent, /<script>not HTML<\/script>/);
    const requestCount = app.requests.length, originalHash = app.location.hash;
    assert.equal(chart('public').closest('.overview-card').querySelectorAll('[data-overview-metric]').length, 0);
    assert.equal(chart('cipher').closest('.overview-card').querySelectorAll('[data-overview-metric]').length, 0);
    assert.equal(app.$('#overview-graphs').querySelectorAll('[data-overview-metric]').length, 5);
    assert.deepEqual(app.$('#overview-graphs').querySelectorAll('h2').map(e=>e.textContent), ['Public-key size', 'Ciphertext size', 'Operation time', 'Initial classical reference']);
    assert.deepEqual(points('cipher').map(point => point.dataset.value), ['8', '9', '10', '12']);
    for (const id of ['computation', 'security']) {
      assert.equal(chart(id).querySelector('.chart-series'), null);
      assert.equal(points(id).length, 0);
      assert.deepEqual(chart(id).querySelectorAll('.chart-tick').map(label => label.textContent), ['20', '21', '90']);
      assert.match(chart(id).textContent, /No (timings|estimates) published/);
    }
    assert.match(chart('computation').textContent, /Reference implementation timings use a shared measurement method/);
    for (const [id, metric] of [['computation', 'encapsulate'], ['computation', 'decapsulate'], ['security', 'quantum']]) {
      const button = chart(id).closest('.overview-card').querySelector('[data-overview-metric="' + metric + '"]');
      button.focus(); button.click(); await flush();
      assert.equal(points(id).length, 0);
      assert.equal(button.getAttribute('aria-pressed'), 'true');
      assert.equal(app.document.activeElement, button);
      assert.match(chart(id).textContent, /No (timings|estimates) published/);
    }
    assert.equal(app.requests.length, requestCount);
    assert.equal(app.location.hash, originalHash);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges' || request.url.pathname === '/api/leaderboard'), false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('overview rejects malformed bytes and handles constant, single-tier, and empty graphs without fabricated values', async () => {
    const invalid = [null, '64', -1, 2.5, true, [], {}, Number.MAX_SAFE_INTEGER + 1];
    const fixture = {...schemes[0], profiles: invalid.map((value, index) => ({...profile('alpha-' + (20 + index), 20 + index), shared_secret_bytes: value})),
      profile_metrics: Object.fromEntries(invalid.map((value, index) => ['alpha-' + (20 + index), {public_key_bytes: value, ciphertext_bytes: value}]))};
    const single = {...schemes[1], profiles: [profile('beta-5', 5)], profile_metrics: {'beta-5': {public_key_bytes: 0, ciphertext_bytes: 0}}};
    const empty = {...schemes[1], id: 'empty', profiles: []};
    const app = await createApp({schemes: [fixture, single, empty], hash: '#scheme/fixture-alpha/7'});
    assert.equal(app.$('#overview-chart-public').querySelectorAll('.chart-point').length, 0);
    assert.match(app.$('#overview-chart-public').textContent, /No values published/);
    assert.equal(app.$('#overview-chart-public').querySelectorAll('.chart-point').length, 0);
    await app.navigate('#scheme/fixture-beta/2');
    assert.deepEqual(app.$('#overview-chart-public').querySelectorAll('.chart-point').map(point => point.dataset.value), ['0']);
    assert.doesNotMatch(app.$('#overview-chart-public').querySelector('.chart-series').getAttribute('d'), /NaN|Infinity/);
    assert.deepEqual(app.$('#overview-chart-cipher').querySelectorAll('.chart-point').map(point => point.dataset.value), ['0']);
    await app.navigate('#scheme/empty/2');
    assert.equal(app.$('#overview-challenges').hidden, true);
    assert.equal(app.$('#overview-chart-public').querySelectorAll('.chart-point').length, 0);
    assert.match(app.$('#overview-chart-public').querySelector('desc').textContent, /No challenge stages published/);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
  });
  await test('tier help belongs to the catalogue recovery header and remains a single accessible toggle', async () => {
    const app = await createApp({schemes});
    const button = app.$('#tier-info');
    assert.equal(button.closest('th').classList.contains('recovery-heading'), true);
    assert.equal(button.getAttribute('aria-controls'), 'tier-help');
    assert.equal(button.getAttribute('aria-expanded'), 'false');
    button.click(); await flush();
    assert.equal(app.$('#tier-help').hidden, false);
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    assert.match(app.$('#tier-help').textContent, /equal stage numbers do not imply equal security/);
    button.click(); await flush();
    assert.equal(app.$('#tier-help').hidden, true);
    await app.navigate(route(schemes[0], 'alpha-3'));
    assert.equal(app.$('#workspace').querySelector('#tier-info'), null);
    assert.equal(app.$('#workspace').querySelector('#tier-help'), null);
    assert.equal(app.document.querySelectorAll('#tier-info').length, 1);
  });
  await test('overview recovery markers use the displayed release and real tier coordinates on every card, including empty estimates', async () => {
    const old = {...schemes[0], version: '6', profiles: [20, 24, 90].map(tier => profile('alpha-' + tier, tier)), largest_recovered_profile_id: 'alpha-24'};
    const current = {...old, version: '7', largest_recovered_profile_id: 'alpha-90', previous_releases: [old]};
    const app = await createApp({schemes: [current], hash: '#scheme/fixture-alpha/6'});
    const charts = () => app.$('#overview-graphs').querySelectorAll('.overview-card');
    for (const card of charts()) {
      const line = card.querySelector('.chart-recovery-line');
      assert.equal(line.dataset.tier, '24');
      assert.ok(Math.abs(Number(line.getAttribute('x1')) - (48 + 4 / 70 * 417)) < 0.0001);
      assert.equal(line.getAttribute('x1'), line.getAttribute('x2'));
      assert.match(card.querySelector('desc').textContent, /Largest recovered · Stage 24.*not a security guarantee or individual recoveries/);
      assert.equal(card.querySelector('.overview-recovery-note').textContent, 'Largest recovered · Stage 24');
      assert.ok(Number(card.querySelector('.chart-recovery-area').getAttribute('width')) > 0);
    }
    assert.equal(app.$('#overview-chart-security').querySelectorAll('.chart-point').length, 0);
    assert.equal(app.$('#overview-chart-computation').querySelectorAll('.chart-point').length, 0);
    await app.navigate('#scheme/fixture-alpha/7');
    for (const card of charts()) assert.equal(card.querySelector('.chart-recovery-line').getAttribute('x1'), '465');
    current.largest_recovered_profile_id = 'alpha-20';
    await app.sandbox.refreshProfiles(); await flush();
    for (const card of charts()) {
      assert.equal(card.querySelector('.chart-recovery-line').getAttribute('x1'), '48');
      assert.equal(card.querySelector('.chart-recovery-area'), null);
    }
    for (const invalid of [null, 'alpha-91', 'unrecorded-profile']) {
      current.largest_recovered_profile_id = invalid;
      await app.sandbox.refreshProfiles(); await flush();
      for (const card of charts()) {
        assert.equal(card.querySelector('.chart-recovery-line'), null);
        assert.equal(card.querySelector('.chart-recovery-area'), null);
        assert.equal(card.querySelector('.overview-recovery-note'), null);
      }
    }
    assert.equal(app.$('.overview-notes').hidden, true);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('calibrated fixed graphs plot actual frozen estimates while mapped parameters remain separate', async () => {
    const calibrated = {...schemes[0], version: '5', profiles: [[1, 20, 8.13], [2, 22, 9.05], [50, 300, 128.2]].map(([tier, parameterBits, bits]) => ({
      ...profile('calibrated-' + tier, tier), parameter_bits: parameterBits, parameter_label: 'subgroup-order bits',
      initial_security_estimate: {model_id: 'classical-baseline-v1', bits, target_bits: tier === 50 ? 128 : tier + 7}})),
      initial_security_model: {id: 'classical-baseline-v1', name: 'Initial classical rho/NFS reference', label: 'Frozen classical reference', summary: 'A declared reference work model.', scope: 'No measured security guarantee.'},
      largest_recovered_profile_id: null, next_profile_id: 'calibrated-1'};
    const old = {...schemes[0], version: '4', profiles: [profile('alpha-20', 20)], next_profile_id: 'alpha-20'};
    calibrated.previous_releases = [old];
    const app = await createApp({schemes: [calibrated], hash: '#scheme/fixture-alpha/5'});
    const chart = app.$('#overview-chart-security');
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(point => [point.dataset.tier, point.dataset.value]), [['1', '8.13'], ['2', '9.05'], ['50', '128.2']]);
    assert.equal(app.$('#overview-title-security').textContent, 'Initial classical reference');
    assert.equal(app.$('#overview-badge-security').textContent, 'Initial model');
    assert.match(chart.textContent, /Frozen classical reference; not a measured security guarantee/);
    assert.match(chart.querySelector('table').textContent, /18.13.*29.05.*50128.2/);
    assert.equal(chart.querySelectorAll('.chart-recovery-line').length, 0);
    assert.equal(chart.closest('.overview-card').querySelectorAll('button').length, 2);
    chart.closest('.overview-card').querySelector('[data-overview-metric="quantum"]').click(); await flush();
    assert.equal(chart.querySelectorAll('.chart-point').length, 0);
    chart.closest('.overview-card').querySelector('[data-overview-metric="classical"]').click(); await flush();
    assert.equal(chart.querySelectorAll('.chart-point').length, 3);
    await app.navigate(route(calibrated, 'calibrated-1', 'protocol'));
    assertSelectedTier(app, 1);
    assert.equal(app.$('#profile-note').textContent, 'Initial classical estimate: ≈8.1 bits.');
    const fact = label => app.$('#parameter-facts').querySelectorAll('div').find(row => row.querySelector('dt')?.textContent === label)?.querySelector('dd')?.textContent;
    assert.equal(fact('Challenge stage'), 'Stage 1');
    assert.equal(fact('Subgroup order size'), '20 bits');
    assert.equal(fact('Initial classical estimate'), '≈8.1 bits');
    assert.equal(fact('Reference model'), 'Initial classical rho/NFS reference');
    assert.match(fact('Estimate scope'), /declared reference work model.*No measured security guarantee/);
    assert.match(app.$('#protocol-size-caveat').textContent, /frozen initial classical reference/);
    app.$('#size-slider').value = '1';
    app.$('#size-slider').dispatchEvent({type: 'input'}); await flush();
    assert.equal(app.$('#profile-note').textContent, 'Initial classical estimate: ≈9.1 bits.');
    app.$('#size-slider').dispatchEvent({type: 'pointercancel'}); await flush();
    assert.equal(app.$('#profile-note').textContent, 'Initial classical estimate: ≈8.1 bits.');
    await app.navigate('#challenge');
    assert.match(app.$('#tier-help').textContent, /Stages mark positions.*default progression.*different numbers of stages.*equal stage numbers do not imply equal security/);
    await app.navigate(route(old, 'alpha-20', 'protocol'));
    assert.equal(app.$('#profile-note').textContent, 'Stages follow this scheme’s default progression through reviewed configurations.');
    assert.equal(fact('Initial classical estimate'), undefined);
    assert.match(app.$('#protocol-size-caveat').textContent, /not an estimate of security bits/);
    await app.navigate('#scheme/fixture-alpha/4');
    assert.equal(chart.querySelectorAll('.chart-point').length, 0);
    assert.match(chart.textContent, /No initial classical estimate is published for this release/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('initial estimates reject mismatched models and nonnumeric or negative bits instead of inventing a tier-derived estimate', async () => {
    const invalid = [null, '8', -1, NaN, Infinity, true, {}, []];
    const calibrated = {...schemes[0], initial_security_model: {id: 'classical-baseline-v1'},
      profiles: invalid.map((bits, index) => ({...profile('invalid-' + index, index + 1), parameter_bits: 300,
        initial_security_estimate: {model_id: 'classical-baseline-v1', bits, target_bits: 128}}))};
    calibrated.profiles.push({...profile('wrong-model', 20), initial_security_estimate: {model_id: 'other-model', bits: 80}});
    calibrated.profiles.push({...profile('zero-estimate', 21), initial_security_estimate: {model_id: 'classical-baseline-v1', bits: 0}});
    const app = await createApp({schemes: [calibrated], hash: '#scheme/fixture-alpha/7'});
    const chart = app.$('#overview-chart-security');
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(point => [point.dataset.tier, point.dataset.value]), [['21', '0']]);
    assert.match(chart.querySelector('table').textContent, /1Not reported/);
    delete calibrated.initial_security_model;
    await app.sandbox.refreshProfiles(); await flush();
    assert.equal(chart.querySelectorAll('.chart-point').length, 0);
    assert.match(chart.textContent, /No initial classical estimate is published/);
  });
  await test('the size picker accepts registered integers and rejects unsupported values without changing the challenge', async () => {
    const sparse = {...schemes[0], profiles: [profile('alpha-3', 3), profile('alpha-7', 7)], next_profile_id: 'alpha-7'};
    const app = await createApp({schemes: [sparse, schemes[1]], hash: route(sparse, 'alpha-3')});
    assert.equal(app.$('#previous-size').disabled, true);
    assert.equal(app.$('#next-size').disabled, false);
    assertSelectedTier(app, 3);
    const original = app.location.hash;
    for (const invalid of ['2', '8', '4', '3.5', 'not-a-number']) {
      app.$('#profile-select').value = invalid;
      app.$('#profile-select').dispatchEvent({type: 'change'}); await flush();
      assert.equal(app.location.hash, original);
      assert.equal(app.$('#profile-error').hidden, false);
      assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    }
    const requestsBeforeBlank = app.requests.length;
    app.$('#profile-select').value = '';
    app.$('#profile-select').dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.location.hash, original);
    assert.equal(app.requests.length, requestsBeforeBlank);
    assertSelectedTier(app, 3);
    app.$('#profile-select').value = '7';
    app.$('#profile-select').dispatchEvent({type: 'change'}); await flush();
    assert.match(app.location.hash, /profile=alpha-7/);
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.$('#previous-size').disabled, false);
    assert.equal(app.$('#next-size').disabled, true);
    assert.equal(app.$('#panel-leaderboard').hidden, false);
    assertSelectedTier(app, 7);
    app.$('#previous-size').click(); await flush();
    assertSelectedTier(app, 3);
    app.$('#next-size').click(); await flush();
    assertSelectedTier(app, 7);
    app.$('#profile-select').value = '3';
    app.$('#profile-select').dispatchEvent({type: 'keydown', key: 'Enter'}); await flush();
    assert.match(app.location.hash, /profile=alpha-3/);
    assertSelectedTier(app, 3);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('a recovery entry opens its method and separate timing evidence in a dialog', async () => {
    const entry = {rank: 1, handle: 'fixture-solver', profile_id: 'alpha-3', received_at: 100, elapsed_seconds: 12,
      method: {name: 'Fixture recovery', pseudocode: '<script>untrusted_method()</script>'},
      compute: {hardware: 'Fixture CPU', workers: 4, wall_seconds: 2, precomputation: 'None', peak_memory: '2 KiB', notes: 'Reported by fixture'}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: url => url.pathname === '/api/leaderboard' ? {entries: [entry]} : undefined});
    const button = app.$('#leaderboard-content').querySelector('.tier-record-result');
    assert.ok(button);
    assert.equal(button.getAttribute('aria-haspopup'), 'dialog');
    button.focus(); button.click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.equal(app.$('#recovery-title').textContent, 'Fixture recovery');
    assert.match(app.$('#recovery-context').textContent, /fixture-solver.*Alpha <KEM>.*Stage 3.*version 7/);
    const content = app.$('#recovery-content').textContent;
    assert.match(content, /Time to submit12(?:\.0)?s from issue to receipt/);
    assert.match(content, /Reported attack time2\.00s/);
    assert.match(content, /Fixture CPU/);
    assert.match(content, /untrusted_method/);
    assert.equal(app.$('#recovery-content').querySelectorAll('script').length, 0);
    app.$('#recovery-dialog').querySelector('[data-close]').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, false);
    assert.equal(app.document.activeElement, button);
    assert.equal(app.$('.tier-winner-name').href, '#person/fixture-solver');
    assert.equal(app.$('#recovery-dialog').open, false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('only the first winner is prominent; its profile and exact source receipt have separate actions', async () => {
    const winner = {rank: 1, handle: '<winner>/name', profile_id: 'alpha-3', instance_size: 3, scheme_version: '7', received_at: 100,
      submission_id: 'receipt-winner', method: {name: 'Original method'}, compute: {}};
    const second = {...winner, rank: 2, handle: 'second-account', submission_id: 'receipt-second'};
    const receipt = {...winner, id: winner.submission_id, scheme_id: 'fixture-alpha', status: 'correct',
      elapsed_seconds: 3, method: {name: 'Full method', pseudocode: 'Recorded source evidence', source: {download_url: '/api/methods/source/bundle'}}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
      if (url.pathname === '/api/leaderboard') return envelope({entries: [winner, second]});
      if (url.pathname === '/api/submissions/receipt-winner') return envelope({submission: receipt});
      if (url.pathname.startsWith('/api/people/')) return envelope({person: {handle: winner.handle, points: 150, records: [], recent_recoveries: []}});
    }});
    assert.equal(app.$('#tier-record').getAttribute('data-state'), 'solved');
    assert.equal(app.$('#tab-leaderboard').textContent.trim(), 'Record');
    assert.equal(app.$('#nav-people').textContent.trim(), 'Leaderboard');
    assert.equal(app.$('#tier-record-notes').open, false);
    assert.equal(app.$('#leaderboard-content').querySelectorAll('table').length, 0);
    assert.equal(app.$('#leaderboard-content').querySelectorAll('.tier-winner-name').length, 1);
    assert.doesNotMatch(app.$('#leaderboard-content').textContent, /second-account/);
    assert.equal(app.$('.tier-winner-name').children.length, 0);
    assert.equal(app.$('.tier-winner-name').href, '#person/' + encodeURIComponent(winner.handle));
    assert.equal(app.requests.some(r => r.url.pathname.startsWith('/api/submissions/')), false);
    app.$('.tier-record-result').click(); await flush();
    assert.match(app.$('#recovery-title').textContent, /Full method/);
    assert.match(app.$('#recovery-content').textContent, /Recorded source evidence|Download code bundle/);
    assert.equal(app.requests.filter(r => r.url.pathname.startsWith('/api/submissions/')).length, 1);
    app.$('#recovery-dialog').close();
    app.$('.tier-winner-name').click(); await flush();
    assert.equal(app.$('#person-title').textContent, winner.handle);
    assert.equal(app.$('#recovery-dialog').open, false);
    assert.equal(app.requests.some(r => r.options.method === 'POST'), false);
  });
  await test('signed-out Take on this stage opens the selected challenge and sign-in without issuing before authentication', async () => {
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-4')});
    assertLeaderboardState(app, 'Unsolved');
    assert.equal(app.$('#tier-record').getAttribute('data-state'), 'unsolved');
    assert.match(app.$('.tier-record-invitation').textContent, /Stage 4/);
    assert.equal(app.$('#tier-record-notes').hidden, true);
    app.$('.tier-record-challenge').click(); await flush();
    assert.match(app.location.hash, /profile=alpha-4.*view=challenge/);
    assert.equal(app.$('#panel-challenge').hidden, false);
    assert.equal(app.$('#auth-dialog').open, true);
    assert.equal(app.requests.some(r => r.options.method === 'POST'), false);
  });
  await test('malformed or mismatched winner responses stay errors, never unsolved records', async () => {
    for (const data of [{}, {entries: null}, {entries: [{rank: 2, handle: 'wrong', received_at: 100}]},
        {entries: [], profile_id: 'alpha-4'}, {entries: [], scheme_id: 'other'}, {entries: [], scheme_version: '99'}]) {
      const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) =>
        url.pathname === '/api/leaderboard' ? envelope(data) : undefined});
      assertLeaderboardState(app, 'Recovery record');
      assert.equal(app.$('#tier-record').getAttribute('data-state'), 'error');
      assert.match(app.$('#leaderboard-content').textContent, /could not be loaded/);
      assert.equal(app.$('#tier-record-notes').hidden, true);
      assert.equal(app.$('#leaderboard-content').querySelector('.tier-winner-name'), null);
    }
  });
  await test('inherited leaderboard credit names the actual larger recovery and opens its original size and release', async () => {
    const current = {...schemes[0], profiles: [20, 36].map(size => profile('alpha-' + size, size)),
      largest_recovered_profile_id: 'alpha-36', next_profile_id: null};
    const entry = {rank: 1, handle: 'larger-size-solver', profile_id: 'alpha-36', instance_size: 36,
      scheme_version: '6', received_at: 100, elapsed_seconds: 12,
      method: {name: 'Original larger recovery', pseudocode: 'Instructions for the size-36 instance'},
      compute: {hardware: 'Original fixture CPU', wall_seconds: 2, notes: 'Original compute report'}};
    const app = await createApp({schemes: [current], hash: route(current, 'alpha-20'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/leaderboard') return undefined;
      return envelope({entries: [{...entry, is_inherited: url.searchParams.get('profile_id') === 'alpha-20'}], includes_compatible_versions: ['6']});
    }});
    assertLeaderboardState(app, 'Broken by', true);
    const board = app.$('#leaderboard-content');
    assert.equal(board.querySelectorAll('.tier-winner').length, 1);
    assert.deepEqual(board.querySelectorAll('.leaderboard-credit').map(credit => credit.textContent), ['Via Stage 36']);
    assert.match(app.$('#leaderboard-scope').textContent, /recovery at this stage or higher/);
    assert.match(app.$('#leaderboard-scope').textContent, /version 6/);
    assert.match(app.$('#leaderboard-footnote').textContent, /Via Stage.*recovery at a higher stage.*actual instance/);
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/leaderboard').at(-1).url.searchParams.get('profile_id'), 'alpha-20');
    board.querySelector('.tier-record-result').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.match(app.$('#recovery-context').textContent, /larger-size-solver.*Alpha <KEM>.*Stage 36.*version 6/);
    assert.doesNotMatch(app.$('#recovery-context').textContent, /Stage 20|version 7/);
    assert.match(app.$('#recovery-content').textContent, /Instructions for the size-36 instance/);
    assert.match(app.$('#recovery-content').textContent, /Time to submit12(?:\.0)?s from issue to receipt/);
    assert.match(app.$('#recovery-content').textContent, /Original compute report/);
    assertSelectedTier(app, 20);
    app.$('#recovery-dialog').querySelector('[data-close]').click(); await flush();
    await app.navigate(route(current, 'alpha-36'));
    assertLeaderboardState(app, 'Broken by', true);
    assert.equal(board.querySelectorAll('.tier-winner').length, 1);
    assert.equal(board.querySelectorAll('.leaderboard-credit').length, 0);
    board.querySelector('.tier-record-result').click(); await flush();
    assert.match(app.$('#recovery-context').textContent, /Stage 36.*version 6/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('leaderboard headings reflect successful results for the selected size, with notes only below populated results', async () => {
    let resolveEmpty;
    const empty = new Promise(resolve => { resolveEmpty = resolve; });
    const entry = {rank: 1, handle: 'size-four-solver', profile_id: 'alpha-4', received_at: 100,
      elapsed_seconds: 2, method: {name: 'Fixture method', pseudocode: 'Fixture instructions'}, compute: {wall_seconds: 1}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/leaderboard') return undefined;
      return url.searchParams.get('profile_id') === 'alpha-3' ? empty : envelope({entries: [entry]});
    }});
    assertLeaderboardState(app, 'Recovery record');
    assert.match(app.$('#leaderboard-content').textContent, /Loading/);
    resolveEmpty(app.envelope({entries: []})); await flush();
    assertLeaderboardState(app, 'Unsolved');
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    await app.navigate(route(schemes[0], 'alpha-4'));
    assertLeaderboardState(app, 'Broken by', true);
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    assertSelectedTier(app, 4);
    assert.match(app.$('#leaderboard-content').textContent, /size-four-solver/);
    assert.deepEqual(app.$('#panel-leaderboard').querySelectorAll('#leaderboard-content, #leaderboard-scope, #leaderboard-footnote').map(element => element.id),
      ['leaderboard-content', 'leaderboard-scope', 'leaderboard-footnote']);
    await app.navigate(route(schemes[0], 'alpha-3'));
    assertLeaderboardState(app, 'Unsolved');
    assert.doesNotMatch(app.$('#leaderboard-content').textContent, /size-four-solver/);
  });
  await test('refresh failures preserve a known record while missing selections remain neutral', async () => {
    let failRefresh;
    let requestCount = 0;
    const failed = new Promise((resolve, reject) => { failRefresh = reject; });
    const entry = {rank: 1, handle: 'fixture-solver', profile_id: 'alpha-3', received_at: 100,
      elapsed_seconds: 2, method: {name: 'Fixture method', pseudocode: 'Fixture instructions'}, compute: {wall_seconds: 1}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/leaderboard') return undefined;
      return ++requestCount === 1 ? envelope({entries: [entry]}) : failed;
    }});
    assertLeaderboardState(app, 'Broken by', true);
    app.$('#refresh-leaderboard').click(); await flush();
    assertLeaderboardState(app, 'Broken by', true);
    failRefresh(new Error('Fixture outage')); await flush();
    assertLeaderboardState(app, 'Broken by', true);
    assert.match(app.$('#leaderboard-content').textContent, /fixture-solver/);
    assert.match(app.$('#toast').textContent, /refresh failed/);
    assert.equal(app.$('#refresh-leaderboard').disabled, false);
    await app.navigate('#challenge');
    await app.sandbox.loadLeaderboard();
    assertLeaderboardState(app, 'Recovery record');
    assert.match(app.$('#leaderboard-content').textContent, /Choose an available stage/);
  });
  await test('the adjacent-size rail centers the selected profile and clamps to the published endpoints', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-25')});
    assert.deepEqual(railSizes(app), [23, 24, 25, 26, 27]);
    assert.equal(railSize(activeSizeButton(app)), 25);
    assert.equal(railButtons(app).filter(button => button.getAttribute('aria-pressed') === 'true').length, 1);
    assert.equal(app.$('#size-range').textContent, '25 of 30');
    await app.navigate(route(ladderScheme, 'alpha-20'));
    assert.deepEqual(railSizes(app), [20, 21, 22, 23, 24]);
    assert.equal(app.$('#previous-size').disabled, true);
    await app.navigate(route(ladderScheme, 'alpha-30'));
    assert.deepEqual(railSizes(app), [26, 27, 28, 29, 30]);
    assert.equal(app.$('#next-size').disabled, true);
  });
  await test('the frontier remains reachable outside the rail and the largest-recovery marker remains exact', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-20', 'protocol')});
    assert.equal(railSizes(app).includes(28), false);
    assert.equal(railSizes(app).includes(29), false);
    assert.equal(Number(app.$('#advance-size-value').textContent), 29);
    assertAdvanceVisibility(app, true);
    assert.equal(railButtons(app).some(isLargestRecovery), false);
    app.$('#advance-size').click(); await flush();
    assertSelectedTier(app, 29);
    assertAdvanceVisibility(app, false);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railButtons(app).filter(isLargestRecovery).map(railSize), [28]);
    for (const button of railButtons(app).filter(button => railSize(button) < 28)) {
      assert.equal(isLargestRecovery(button), false);
      assert.doesNotMatch(button.getAttribute('aria-label') || '', /recovered|solved/i);
    }
    assert.match(railButtons(app).find(button => railSize(button) === 28).getAttribute('aria-label'), /largest recovered/);
    railButtons(app).find(button => railSize(button) === 27).click(); await flush();
    assertSelectedTier(app, 27);
    assertAdvanceVisibility(app, true);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('the prior-size range and next-unsolved dot stay fixed while selection and exact evidence remain distinct', async () => {
    const sparse = {...ladderScheme, profiles: [20, 22, 24, 27, 30].map(size => profile('alpha-' + size, size)),
      recovered_profile_ids: ['alpha-20', 'alpha-27'], largest_recovered_profile_id: 'alpha-27', next_profile_id: 'alpha-30'};
    const app = await createApp({schemes: [sparse, schemes[1]], hash: route(sparse, 'alpha-24')});
    const markedSizes = className => railButtons(app).filter(button => button.classList.contains(className)).map(railSize);
    assert.deepEqual(markedSizes('is-prior'), [20, 22, 24, 27]);
    assert.deepEqual(markedSizes('is-next-unsolved'), [30]);
    assert.deepEqual(railButtons(app).filter(isLargestRecovery).map(railSize), [27]);
    assert.equal(railSize(activeSizeButton(app)), 24);
    assert.match(activeSizeButton(app).getAttribute('aria-label'), /earlier stage/);
    for (const size of [22, 24]) {
      const button = railButtons(app).find(button => railSize(button) === size);
      assert.match(button.getAttribute('aria-label'), /earlier stage/);
      assert.doesNotMatch(button.getAttribute('aria-label'), /recovered|solved/i);
    }
    assert.match(railButtons(app).find(button => railSize(button) === 20).getAttribute('aria-label'), /recovered/);
    assert.match(railButtons(app).find(button => railSize(button) === 27).getAttribute('aria-label'), /largest recovered/);
    assert.match(railButtons(app).find(button => railSize(button) === 30).getAttribute('aria-label'), /next unsolved stage/);
    for (const selected of [20, 27, 30]) {
      railButtons(app).find(button => railSize(button) === selected).click(); await flush();
      assert.equal(railSize(activeSizeButton(app)), selected);
      assert.equal(activeSizeButton(app).classList.contains('is-prior'), selected <= 27);
      assert.equal(isLargestRecovery(activeSizeButton(app)), selected === 27);
      assert.deepEqual(markedSizes('is-prior'), [20, 22, 24, 27]);
      assert.deepEqual(markedSizes('is-next-unsolved'), [30]);
    }
    await app.navigate(route(schemes[1], 'beta-5'));
    assert.deepEqual(markedSizes('is-prior'), []);
    assert.deepEqual(markedSizes('is-next-unsolved'), [5]);
    railButtons(app).find(button => railSize(button) === 6).click(); await flush();
    assert.deepEqual(markedSizes('is-next-unsolved'), [5]);
  });
  await test('legacy metadata shades earlier sizes without labelling them as accepted recoveries', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-28')});
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-prior')).map(railSize), [26, 27, 28]);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-next-unsolved')).map(railSize), [29]);
    assert.match(activeSizeButton(app).getAttribute('aria-label'), /largest recovered/);
    await app.navigate(route(ladderScheme, 'alpha-20'));
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-prior')).map(railSize), [20, 21, 22, 23, 24]);
    assert.equal(railButtons(app).some(button => button.classList.contains('is-next-unsolved')), false);
    for (const button of railButtons(app)) {
      assert.match(button.getAttribute('aria-label'), /earlier stage/);
      assert.doesNotMatch(button.getAttribute('aria-label'), /recovered|solved/i);
    }
  });
  await test('changing KEMs updates the record and range without carrying another KEM recovery', async () => {
    const beta = {...schemes[1], largest_recovered_profile_id: 'beta-6', next_profile_id: null};
    const gamma = {...schemes[1], id: 'fixture-gamma', profiles: [profile('gamma-90', 90), profile('gamma-91', 91)],
      largest_recovered_profile_id: null, next_profile_id: 'gamma-90'};
    const app = await createApp({schemes: [ladderScheme, beta, gamma], hash: route(ladderScheme, 'alpha-20')});
    await app.navigate(route(beta, 'beta-5'));
    assertAdvanceVisibility(app, false);
    assert.deepEqual(railSizes(app), [5, 6]);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-prior')).map(railSize), [5, 6]);
    assert.equal(railButtons(app).some(button => button.classList.contains('is-next-unsolved')), false);
    assert.match(app.$('#size-range').textContent, /5.*6/);
    app.$('#profile-select').value = '6';
    app.$('#jump-size').click(); await flush();
    assert.match(app.location.hash, /fixture-beta/);
    assertSelectedTier(app, 6);
    await app.navigate(route(gamma, 'gamma-90'));
    assert.equal(Number(app.$('#advance-size-value').textContent), 90);
    assertAdvanceVisibility(app, false);
    assert.deepEqual(railSizes(app), [90, 91]);
    assert.match(app.$('#size-range').textContent, /90.*91/);
    assert.equal(railButtons(app).some(isLargestRecovery), false);
    assert.equal(railButtons(app).some(button => button.classList.contains('is-prior')), false);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-next-unsolved')).map(railSize), [90]);
  });
  await test('sparse profiles remain valid rail choices and arbitrary jumps preserve the current view', async () => {
    const sparse = {...ladderScheme, profiles: [20, 22, 27, 35, 60, 90, 128].map(size => profile('alpha-' + size, size)),
      largest_recovered_profile_id: 'alpha-90', next_profile_id: 'alpha-128'};
    const app = await createApp({schemes: [sparse], hash: route(sparse, 'alpha-35', 'protocol')});
    assert.deepEqual(railSizes(app), [22, 27, 35, 60, 90]);
    const original = app.location.hash;
    for (const invalid of ['21', '19', '129', '35.5']) {
      app.$('#profile-select').value = invalid;
      app.$('#jump-size').click(); await flush();
      assert.equal(app.location.hash, original);
      assert.equal(app.$('#profile-error').hidden, false);
      assert.equal(railSize(activeSizeButton(app)), 35);
    }
    app.$('#profile-select').value = '';
    app.$('#jump-size').click(); await flush();
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.location.hash, original);
    assertSelectedTier(app, 35);
    app.$('#profile-select').value = '128';
    app.$('#jump-size').click(); await flush();
    assertSelectedTier(app, 128);
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railSizes(app), [27, 35, 60, 90, 128]);
    assert.equal(app.$('#next-size').disabled, true);
    app.$('#previous-size').click(); await flush();
    assertSelectedTier(app, 90);
    assert.equal(app.$('#panel-protocol').hidden, false);
  });
  await test('the advance action targets the scheme frontier, preserves each view, and never issues a challenge', async () => {
    const sparse = {...ladderScheme, profiles: [20, 24, 36].map(size => profile('alpha-' + size, size)),
      largest_recovered_profile_id: 'alpha-24', next_profile_id: 'alpha-36'};
    const complete = {...schemes[1], id: 'fixture-complete', largest_recovered_profile_id: 'beta-6', next_profile_id: null};
    for (const view of ['leaderboard', 'challenge', 'protocol']) {
      const app = await createApp({schemes: [sparse, schemes[1], complete], user: {id: 'fixture-user', handle: 'fixture'},
        hash: route(sparse, 'alpha-20', view)});
      assertAdvanceVisibility(app, true);
      assert.equal(Number(app.$('#advance-size-value').textContent), 36);
      assert.match(app.$('#advance-size-label').textContent, /Go to next unsolved stage/);
      app.$('#advance-size').click(); await flush();
      assertSelectedTier(app, 36);
      assert.equal(app.$('#panel-' + view).hidden, false);
      assertAdvanceVisibility(app, false);
      assert.equal(Number(app.$('#advance-size-value').textContent), 36);
      await app.navigate(route(sparse, 'alpha-24', view));
      assertAdvanceVisibility(app, true);
      assert.equal(Number(app.$('#advance-size-value').textContent), 36);
      await app.navigate(route(schemes[1], 'beta-6', view));
      assertAdvanceVisibility(app, true);
      assert.equal(Number(app.$('#advance-size-value').textContent), 5);
      app.$('#advance-size').click(); await flush();
      assertSelectedTier(app, 5);
      assert.equal(app.$('#panel-' + view).hidden, false);
      assertAdvanceVisibility(app, false);
      await app.navigate(route(complete, 'beta-5', view));
      assertAdvanceVisibility(app, false);
      assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
    }
  });
  await test('the slider previews the size controls, cancels cleanly, and commits exact sparse profiles without premature requests', async () => {
    const sparse = {...ladderScheme, profiles: [36, 20, 24, 90].map(size => profile('alpha-' + size, size)),
      largest_recovered_profile_id: 'alpha-24', next_profile_id: 'alpha-36'};
    const single = {...schemes[1], profiles: [profile('beta-55', 55)], next_profile_id: 'beta-55'};
    const app = await createApp({schemes: [sparse, single], user: {id: 'fixture-user', handle: 'fixture'},
      hash: route(sparse, 'alpha-24', 'protocol')});
    const slider = app.$('#size-slider');
    assert.equal(slider.getAttribute('type'), 'range');
    assert.equal(String(slider.min), '0');
    assert.equal(String(slider.max), '3');
    assert.equal(slider.getAttribute('step'), '1');
    assert.equal(slider.disabled, false);
    assert.equal(slider.value, '1');
    assert.equal(Number(app.$('#size-slider-value').textContent), 24);
    assertSelectedTier(app, 24);
    const originalRoute = app.location.hash;
    const previewRequests = app.requests.length;
    app.$('#profile-select').value = '90';
    slider.value = '2';
    slider.dispatchEvent({type: 'input'}); await flush();
    assert.equal(Number(app.$('#size-slider-value').textContent), 36);
    assert.equal(slider.getAttribute('aria-valuetext'), 'Challenge stage 36');
    assert.equal(app.location.hash, originalRoute);
    assertSelectedTier(app, 36);
    assert.equal(railSize(activeSizeButton(app)), 36);
    assert.deepEqual(railSizes(app), [20, 24, 36, 90]);
    assert.equal(app.$('#size-range').textContent, '36 of 90');
    assertAdvanceVisibility(app, false);
    assert.equal(app.requests.length, previewRequests);
    slider.dispatchEvent({type: 'pointercancel'}); await flush();
    assert.equal(slider.value, '1');
    assert.equal(slider.getAttribute('aria-valuetext'), 'Challenge stage 24');
    assert.equal(Number(app.$('#size-slider-value').textContent), 24);
    assertSelectedTier(app, 24);
    assert.equal(railSize(activeSizeButton(app)), 24);
    assertAdvanceVisibility(app, true);
    assert.equal(app.location.hash, originalRoute);
    assert.equal(app.requests.length, previewRequests);
    slider.value = '2';
    slider.dispatchEvent({type: 'input'}); await flush();
    assert.equal(app.requests.length, previewRequests);
    slider.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.location.hash, route(sparse, 'alpha-36', 'protocol'));
    assertSelectedTier(app, 36);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(slider.value, '2');
    assertAdvanceVisibility(app, false);
    railButtons(app).find(button => railSize(button) === 20).click(); await flush();
    assert.equal(slider.value, '0');
    assert.equal(Number(app.$('#size-slider-value').textContent), 20);
    await app.navigate(route(sparse, 'alpha-90', 'protocol'));
    assert.equal(slider.value, '3');
    assert.equal(slider.getAttribute('aria-valuetext'), 'Challenge stage 90');
    assertAdvanceVisibility(app, true);
    app.$('#profile-select').value = '24';
    app.$('#jump-size').click(); await flush();
    assert.equal(slider.value, '1');
    for (const invalid of ['-1', '4', '1.5', 'not-a-number']) {
      slider.value = invalid;
      slider.dispatchEvent({type: 'change'}); await flush();
      assert.equal(app.location.hash, originalRoute);
      assert.equal(slider.value, '1');
      assert.equal(Number(app.$('#size-slider-value').textContent), 24);
    }
    await app.navigate(route(single, 'beta-55', 'protocol'));
    assert.equal(slider.disabled, true);
    assert.equal(String(slider.min), '0');
    assert.equal(String(slider.max), '0');
    assert.equal(slider.value, '0');
    assert.equal(Number(app.$('#size-slider-value').textContent), 55);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('Jump to stays blank on selection, preserves an unsent draft on unrelated rendering, and clears on navigation', async () => {
    const app = await createApp({schemes: [ladderScheme, schemes[1]], hash: route(ladderScheme, 'alpha-25', 'protocol')});
    const input = app.$('#profile-select');
    assertSelectedTier(app, 25);
    input.focus();
    input.value = '29';
    const originalRoute = app.location.hash;
    const requestsBeforeRender = app.requests.length;
    app.sandbox.renderProfiles(); await flush();
    assert.equal(input.value, '29');
    assert.equal(Number(app.$('#size-slider-value').textContent), 25);
    assert.equal(railSize(activeSizeButton(app)), 25);
    assert.equal(app.document.activeElement === input, true);
    assert.equal(app.location.hash, originalRoute);
    assert.equal(app.requests.length, requestsBeforeRender);
    await app.sandbox.refreshProfiles(); await flush();
    assert.equal(input.value, '29', 'Refreshing scheme metadata must not replace an unsent jump');
    assert.equal(Number(app.$('#size-slider-value').textContent), 25);
    assert.equal(app.document.activeElement === input, true);
    app.$('#tab-leaderboard').click(); await flush();
    assert.equal(input.value, '29', 'Changing only the view retains the draft');
    assert.equal(Number(app.$('#size-slider-value').textContent), 25);
    await app.navigate(route(ladderScheme, 'alpha-26'));
    assertSelectedTier(app, 26);
    input.value = '29';
    await app.navigate(route(schemes[1], 'beta-5'));
    assertSelectedTier(app, 5);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('returning to the current valid size clears Jump to and preserves rail focus without duplicate blur requests', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-25')});
    const input = app.$('#profile-select');
    const originalButtons = railButtons(app);
    const selectedButton = activeSizeButton(app);
    const originalRoute = app.location.hash;
    const originalRequests = app.requests.length;
    assertSelectedTier(app, 25);
    input.focus();
    input.value = '25.5';
    input.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.$('#profile-error').hidden, false);
    input.value = '025';
    input.dispatchEvent({type: 'keydown', key: 'Enter'}); await flush();
    assert.equal(input.value, '');
    assertSelectedTier(app, 25);
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.location.hash, originalRoute);
    assert.equal(app.requests.length, originalRequests);
    assert.equal(app.document.activeElement === input, true);
    for (const [index, button] of railButtons(app).entries()) assert.equal(button, originalButtons[index]);
    // Native number inputs can emit change on blur after Enter. The rail's
    // focus target must survive that event rather than being replaced.
    selectedButton.focus();
    input.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.document.activeElement, selectedButton);
    assert.equal(selectedButton.isConnected, true);
    assert.equal(activeSizeButton(app), selectedButton);
    assert.equal(app.requests.length, originalRequests);
    assert.equal(app.$('#profile-error').hidden, true);
    assertSelectedTier(app, 25);
    for (const [index, button] of railButtons(app).entries()) assert.equal(button, originalButtons[index]);
  });
  await test('the rail has one keyboard stop and moves across registered profiles with arrow and endpoint keys', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-25')});
    assert.equal(activeSizeButton(app).tabIndex, 0);
    assert.equal(railButtons(app).filter(button => button.tabIndex === 0).length, 1);
    activeSizeButton(app).focus();
    activeSizeButton(app).dispatchEvent({type: 'keydown', key: 'ArrowRight'}); await flush();
    assert.equal(railSize(activeSizeButton(app)), 26);
    assert.equal(app.document.activeElement, activeSizeButton(app));
    assert.equal(railButtons(app).filter(button => button.tabIndex === 0).length, 1);
    activeSizeButton(app).dispatchEvent({type: 'keydown', key: 'Home'}); await flush();
    assert.equal(railSize(activeSizeButton(app)), 20);
    assert.equal(app.document.activeElement, activeSizeButton(app));
    activeSizeButton(app).dispatchEvent({type: 'keydown', key: 'End'}); await flush();
    assert.equal(railSize(activeSizeButton(app)), 30);
    assert.equal(app.document.activeElement, activeSizeButton(app));
    assert.equal(app.$('#panel-leaderboard').hidden, false);
  });
  await test('archived releases keep their own size rail, frontier, and exact record', async () => {
    const old = {...ladderScheme, version: '6', profiles: ladderScheme.profiles.slice(0, 6),
      largest_recovered_profile_id: 'alpha-22', next_profile_id: 'alpha-23'};
    const current = {...ladderScheme, previous_releases: [old]};
    const app = await createApp({schemes: [current], hash: route(old, 'alpha-20', 'protocol')});
    assert.match(app.$('#size-range').textContent, /20.*25/);
    assert.equal(Number(app.$('#advance-size-value').textContent), 23);
    app.$('#advance-size').click(); await flush();
    assertSelectedTier(app, 23);
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-next-unsolved')).map(railSize), [23]);
    app.$('#profile-select').value = '22';
    app.$('#jump-size').click(); await flush();
    assertSelectedTier(app, 22);
    assert.match(app.location.hash, /fixture-alpha\/6\?/);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railButtons(app).filter(isLargestRecovery).map(railSize), [22]);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('a session outage never assumes the catalogue is public', async () => {
    const app = await createApp({schemes, responder: url => {
      if (url.pathname === '/api/session') throw new Error('Simulated session outage');
    }});
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/schemes'), false);
    assert.equal(app.$('#global-message').hidden, false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('deep links select an exact KEM, version, profile and protocol view', async () => {
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-4', 'protocol')});
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    assertSelectedTier(app, 4);
    assert.equal(app.$('#profile-select').min, '3');
    assert.equal(app.$('#profile-select').max, '4');
    assert.equal(app.$('#profile-label').textContent, 'Stage');
    assert.doesNotMatch(app.$('.profile-field').textContent, /instance size/i);
    assert.equal(app.document.querySelector('#round-deadline'), null);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(app.$('#panel-challenge').hidden, true);
    assert.equal(app.$('#profile-note').textContent, 'Stages follow this scheme’s default progression through reviewed configurations.');
    assert.equal(app.$('#tab-protocol').getAttribute('aria-selected'), 'true');
  });
  await test('protocol content and specification downloads follow the exact KEM release', async () => {
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3', 'protocol')});
    assert.equal(app.$('#protocol-construction').textContent, 'Alpha construction.');
    assert.equal(app.$('#protocol-downloads').hidden, false);
    assert.equal(app.$('#protocol-downloads').querySelector('a').href, '/api/protocol/spec?scheme_id=fixture-alpha&version=7');
    assert.equal(app.$('#protocol-downloads').querySelectorAll('a')[1].href, '/api/protocol/source?version=7');
    await app.navigate(route(schemes[1], 'beta-5', 'protocol'));
    assert.equal(app.$('#protocol-construction').textContent, 'Beta construction.');
    assert.equal(app.$('#protocol-downloads').hidden, false);
    assert.equal(app.$('#protocol-downloads').querySelectorAll('a').length, 1);
    assert.equal(app.$('#protocol-downloads').querySelector('a').href, '/api/protocol/spec?scheme_id=fixture-beta&version=2');
    assert.equal(app.$('#protocol-solver-note').hidden, true);
    assert.equal(app.$('#scheme-education-note').hidden, true);
  });
  await test('browser Back and the home navigation return to the KEM catalogue', async () => {
    const app = await createApp({schemes});
    await app.navigate(route(schemes[0], 'alpha-3'));
    app.history.back(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    await app.navigate(route(schemes[1], 'beta-6'));
    app.$('#nav-challenge').click(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#workspace').hidden, true);
  });
  await test('About us is an empty deep-linked page with navigation state, history, and keyboard focus', async () => {
    const app = await createApp({schemes, hash: '#about'});
    const about = app.$('#about-workspace');
    assert.equal(about.hidden, false);
    assert.equal(about.textContent.trim(), 'About us');
    assert.equal(app.document.title, 'About us — Crucible');
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.$('#workspace').hidden, true);
    assert.equal(app.$('#research-workspace').hidden, true);
    assert.equal(app.$('#nav-about').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'), null);
    assert.equal(app.$('.skip-link').href, '#about-workspace');
    app.$('.skip-link').click();
    assert.equal(app.document.activeElement, about);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
    app.$('#nav-challenge').click(); await flush();
    assert.equal(about.hidden, true);
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#nav-about').getAttribute('aria-current'), null);
    app.history.back(); await flush();
    assert.equal(about.hidden, false);
    assert.equal(app.document.activeElement, about);
    await app.navigate(route(schemes[0], 'alpha-3', 'protocol'));
    app.$('#nav-about').click(); await flush();
    assert.equal(app.location.hash, '#about');
    assert.equal(about.hidden, false);
    assert.equal(app.$('#workspace').hidden, true);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('unknown KEMs and versions cannot show another KEM under an incorrect URL', async () => {
    for (const hash of ['#challenge/missing/1?profile=alpha-3', '#challenge/fixture-alpha/999?profile=alpha-3', '#challenge/fixture-alpha/7?profile=beta-5']) {
      const app = await createApp({schemes, hash});
      assert.equal(app.$('#catalogue-workspace').hidden, false);
      assert.equal(app.$('#workspace').hidden, true);
      assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
    }
  });
  await test('valid navigation clears an invalid-route error', async () => {
    const app = await createApp({schemes, hash: '#challenge/missing/1?profile=alpha-3'});
    assert.equal(app.$('#global-message').hidden, false);
    assert.match(app.$('#global-message-text').textContent, /unavailable/);
    await app.navigate(route(schemes[0], 'alpha-3'));
    assert.equal(app.$('#global-message').hidden, true);
    assert.equal(app.$('#global-message-text').textContent, '');
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    assert.equal(app.$('#workspace').hidden, false);
  });
  await test('keyboard tabs change the visible panel and preserve the selected KEM', async () => {
    const app = await createApp({schemes, hash: route(schemes[1], 'beta-5')});
    assert.equal(app.$('#scheme-clock').closest('#panel-challenge'), app.$('#panel-challenge'));
    assert.match(app.$('#scheme-clock').textContent, /Challenge closes in/);
    assert.equal(app.$('#scheme-clock').querySelectorAll('#countdown').length, 1);
    assert.equal((app.$('#scheme-clock').textContent.match(/\d{2}:\d{2}/g) || []).length, 1);
    assert.doesNotMatch(app.$('#scheme-clock').textContent, /UTC|shared deadline/);
    app.$('#tab-leaderboard').dispatchEvent({type: 'keydown', key: 'ArrowRight'}); await flush();
    assert.equal(app.$('#panel-challenge').hidden, false);
    assert.equal(app.$('#tab-challenge').getAttribute('aria-selected'), 'true');
    assert.equal(app.document.activeElement, app.$('#tab-challenge'));
    assert.equal(app.$('#scheme-title').textContent, 'Beta KEM');
    app.$('#tab-challenge').dispatchEvent({type: 'keydown', key: 'End'}); await flush();
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(app.$('#tab-protocol').tabIndex, 0);
  });
  await test('archived receipts keep their exact details and new challenges return to the current release', async () => {
    const previous = {...schemes[0], version: '6', profiles: [profile('alpha-3', 3)], next_profile_id: null,
      protocol: {...schemes[0].protocol, source_url: '/api/protocol/source?scheme_id=fixture-alpha&version=6'}};
    const current = {...schemes[0], previous_releases: [previous],
      first_recovery: {...schemes[0].first_recovery, scheme_version: '6'}};
    const entry = {id: 'receipt-alpha', challenge_id: 'archived-instance', rank: 1, status: 'correct', handle: 'first-alpha', scheme_id: 'fixture-alpha', scheme_version: '6', profile_id: 'alpha-3', instance_size: 3, received_at: 100,
      elapsed_seconds: 2, method: {id: 'archived-method', name: 'Archived method', pseudocode: 'Archived instructions'}, compute: {wall_seconds: 1}};
    const app = await createApp({schemes: [current], responder: (url, options, envelope) => {
      if (url.pathname === '/api/leaderboard') return envelope({entries: [entry]});
      if (url.pathname === '/api/submissions/receipt-alpha') return envelope({submission: entry});
      if (url.pathname === '/api/people/first-alpha') return envelope({person: {handle: 'first-alpha', points: 0, schemes_recovered: 1, correct_submission_count: 1,
        records: [], recent_recoveries: [{scheme_id: entry.scheme_id, scheme_name: 'Alpha <KEM>', scheme_version: '6', tier: 3, submission_id: entry.id, received_at: entry.received_at, is_current: false}]}});
    }});
    const firstButton = app.$('#kem-catalogue').querySelector('.kem-first-handle');
    assert.equal(firstButton.tagName, 'A');
    firstButton.click(); await flush();
    assert.equal(app.$('#person-workspace').hidden, false);
    assert.equal(app.$('#recovery-dialog').open, false);
    app.$('#person-content').querySelector('.history-item').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.match(app.$('#recovery-context').textContent, /Stage 3.*version 6/);
    assert.match(app.$('#recovery-content').textContent, /Archived instructions/);
    assert.equal(app.$('#person-workspace').hidden, false);
    app.$('#recovery-dialog').close();
    await app.navigate(route(previous, 'alpha-3'));
    assert.equal(app.$('#profile-select').max, '3');
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/leaderboard').at(-1).url.searchParams.get('scheme_version'), '6');
    app.$('#tab-protocol').click(); await flush();
    assert.equal(app.$('#protocol-downloads').querySelector('a').href, '/api/protocol/spec?scheme_id=fixture-alpha&version=6');
    assert.equal(app.$('#protocol-downloads').querySelectorAll('a')[1].href, '/api/protocol/source?scheme_id=fixture-alpha&version=6');
    app.$('#tab-challenge').click(); await flush();
    assert.equal(app.$('#empty-title').textContent, 'Archived release');
    app.$('#issue-button').click(); await flush();
    assert.match(app.location.hash, /fixture-alpha\/7\?/);
    assert.match(app.location.hash, /profile=alpha-4/);
    assert.equal(app.$('#panel-challenge').hidden, false);
    await app.navigate(route(current, 'alpha-3'));
    app.$('#leaderboard-content').querySelector('.tier-record-result').click(); await flush();
    assert.match(app.$('#recovery-context').textContent, /version 6/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('late challenge responses cannot overwrite the newly selected KEM', async () => {
    let releaseAlpha;
    const alpha = new Promise(resolve => { releaseAlpha = resolve; });
    const challenge = (id, profileId, envelope) => {
      const clock = envelope({});
      return {id, profile_id: profileId, round_id: clock.round.id, issued_at: clock.server_time, ends_at: clock.round.ends_at, status: 'open', public: {encoding: {shared_secret_bytes: 32}, marker: id}};
    };
    const app = await createApp({schemes, user: {id: 'fixture-user', handle: 'fixture'}, hash: route(schemes[0], 'alpha-3', 'challenge'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/challenges') return undefined;
      return url.searchParams.get('profile_id') === 'alpha-3' ? alpha : envelope({challenge: challenge('beta-instance', 'beta-5', envelope)});
    }});
    await app.navigate(route(schemes[1], 'beta-5', 'challenge'));
    assert.match(app.$('#download-button').href, /beta-instance/);
    releaseAlpha(app.envelope({challenge: challenge('alpha-instance', 'alpha-3', app.envelope)})); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Beta KEM');
    assert.match(app.$('#download-button').href, /beta-instance/);
    assert.doesNotMatch(app.$('#download-button').href, /alpha-instance/);
  });
  await test('late leaderboard responses cannot overwrite another KEM leaderboard', async () => {
    let releaseAlpha;
    const alpha = new Promise(resolve => { releaseAlpha = resolve; });
    const entry = handle => ({rank: 1, handle, profile_id: 'alpha-3', received_at: 1, elapsed_seconds: 2, method: {name: 'Fixture method', pseudocode: 'No real attack.'}, compute: {hardware: 'Fixture CPU', wall_seconds: 2}});
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3', 'leaderboard'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/leaderboard') return undefined;
      return url.searchParams.get('profile_id') === 'alpha-3' ? alpha : envelope({entries: []});
    }});
    assertLeaderboardState(app, 'Recovery record');
    await app.navigate(route(schemes[1], 'beta-5', 'leaderboard'));
    assertLeaderboardState(app, 'Unsolved');
    assert.match(app.$('#leaderboard-content').textContent, /No accepted recoveries/);
    releaseAlpha(app.envelope({entries: [entry('stale-alpha')]})); await flush();
    assertLeaderboardState(app, 'Unsolved');
    assert.doesNotMatch(app.$('#leaderboard-content').textContent, /stale-alpha/);
    assert.equal(app.$('#scheme-title').textContent, 'Beta KEM');
  });
  await test('late empty results and errors cannot erase a newer size recovery heading or notes', async () => {
    for (const lateResult of ['empty', 'error']) {
      let resolveOld, rejectOld;
      const old = new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; });
      const entry = {rank: 1, handle: 'current-size-solver', profile_id: 'alpha-4', received_at: 100,
        elapsed_seconds: 2, method: {name: 'Fixture method', pseudocode: 'Fixture instructions'}, compute: {wall_seconds: 1}};
      const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
        if (url.pathname !== '/api/leaderboard') return undefined;
        return url.searchParams.get('profile_id') === 'alpha-3' ? old : envelope({entries: [entry]});
      }});
      assertLeaderboardState(app, 'Recovery record');
      await app.navigate(route(schemes[0], 'alpha-4'));
      assertLeaderboardState(app, 'Broken by', true);
      if (lateResult === 'empty') resolveOld(app.envelope({entries: []}));
      else rejectOld(new Error('Stale fixture failure'));
      await flush();
      assertLeaderboardState(app, 'Broken by', true);
      assertSelectedTier(app, 4);
      assert.match(app.$('#leaderboard-content').textContent, /current-size-solver/);
      assert.doesNotMatch(app.$('#leaderboard-content').textContent, /could not be reached|No accepted recoveries/);
    }
  });
  await test('a shorter replacement ladder keeps its own bounds and versioned design explanation', async () => {
    const archived = {...researchScheme, version: 'old', default_progression: {stage_count: 50}, ladder_design: {
      progression: 'Archived fixed dimensions.', cost: 'Fixed transmitted size.', qualification: 'Original evidence only.'}};
    const revised = {...researchScheme, default_progression: {stage_count: 8}, profiles: researchProfiles.slice(0, 8),
      previous_releases: [archived], ladder_design: {progression: 'Eight distinct dimension settings.',
        cost: 'More coefficients and arithmetic.', qualification: 'Native message enumeration is an upper bound.'}};
    const app = await createApp({schemes: [revised], hash: route(revised, 'research-8', 'protocol')});
    assertSelectedTier(app, 8);
    assert.equal(app.$('#profile-select').max, '8');
    assert.equal(app.$('#profile-label').textContent, 'Stage');
    assert.equal(app.$('#size-range').textContent, '8 of 8');
    assert.match(app.$('#scheme-specific-facts').textContent, /Eight distinct dimension settings.*More coefficients and arithmetic.*Native message enumeration/);
    await app.navigate(route(archived, 'research-50', 'protocol'));
    assertSelectedTier(app, 50);
    assert.equal(app.$('#profile-select').max, '50');
    assert.equal(app.$('#size-range').textContent, '50 of 50');
    assert.match(app.$('#scheme-specific-facts').textContent, /Archived fixed dimensions/);
    assert.doesNotMatch(app.$('#scheme-specific-facts').textContent, /Eight distinct/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('default stages preserve exact intermediate configurations, routes, records, and graph positions', async () => {
    const settings = [[20, 1, 1], [24, null, 1.5], [36, 2, 2], [60, null, 2.5], [90, 3, 3]];
    const configurations = settings.map(([size, stage, position]) => ({...profile('alpha-' + size, size),
      stage_number: stage, stage_position: position, native_parameter_label: 'dimension ' + size,
      stage_label: stage == null ? 'Configuration dimension ' + size : 'Stage ' + stage,
      stage_context: stage == null ? {before_stage: Math.floor(position), after_stage: Math.ceil(position)} : null}));
    const curated = {...schemes[0], profiles: configurations, record_inheritance: false,
      default_progression: {stage_count: 3, configuration_count: 5, stages: configurations.filter(item => item.stage_number != null).map(item => ({number: item.stage_number, profile_id: item.id}))},
      largest_recovered_profile_id: 'alpha-60', recovered_profile_ids: ['alpha-60'], next_profile_id: 'alpha-36',
      profile_metrics: Object.fromEntries(configurations.map(item => [item.id, {public_key_bytes: item.instance_size, ciphertext_bytes: item.instance_size + 5}]))};
    const app = await createApp({schemes: [curated], hash: route(curated, 'alpha-60')});
    assert.equal(app.$('#profile-label').textContent, 'Configuration');
    assert.equal(app.$('#size-range').textContent, 'Between 2 and 3');
    assert.equal(app.$('#size-slider-value').textContent, '—');
    assert.deepEqual(railSizes(app), [1, 2, 3]);
    assert.equal(activeSizeButton(app), undefined, 'An intermediate configuration is not aliased to a default stage');
    assert.equal(app.$('#profile-select').min, '1');
    assert.equal(app.$('#profile-select').max, '3');
    assert.match(app.$('#profile-note').textContent, /Between Stages 2 and 3.*dimension 60/);
    const extra = app.$('#intermediate-configurations');
    assert.equal(extra.hidden, false);assert.equal(extra.open, false);
    assert.equal(extra.querySelector('summary').textContent, 'Intermediate configurations (2)');
    assert.deepEqual(extra.querySelectorAll('button').map(button => button.textContent), ['Configuration dimension 24', 'Configuration dimension 60']);
    assert.equal(extra.querySelectorAll('button')[1].getAttribute('aria-pressed'), 'true');
    assert.match(app.$('.tier-record-invitation').textContent, /Configuration dimension 60/);
    assert.match(app.$('.tier-record-challenge').textContent, /Take on this configuration/);
    assert.equal(app.requests.find(request => request.url.pathname === '/api/leaderboard').url.searchParams.get('profile_id'), 'alpha-60');
    const instructions = app.sandbox.agentInstructions({id: 'exact-instance', ends_at: 100, public: {scheme_id: curated.id}});
    assert.match(instructions, /--profile-id 'alpha-60'/);assert.doesNotMatch(instructions, /--stage null|Stage 60/);
    app.$('#previous-size').click();await flush();
    assert.match(app.location.hash, /profile=alpha-36/);assertSelectedTier(app, 2);
    await app.navigate(route(curated, 'alpha-60'));
    app.$('#next-size').click();await flush();assert.match(app.location.hash, /profile=alpha-90/);assertSelectedTier(app, 3);
    app.$('#profile-select').value = '2';app.$('#jump-size').click();await flush();
    assert.match(app.location.hash, /profile=alpha-36/);
    extra.querySelectorAll('button')[0].click();await flush();assert.match(app.location.hash, /profile=alpha-24/);
    await app.navigate('#scheme/fixture-alpha/7');
    assert.equal(app.$('#overview-release').textContent, '3 challenge stages');
    assert.match(app.$('#overview-progress').textContent, /Largest recovery.*Configuration dimension 60/);
    const chart = app.$('#overview-chart-public');
    assert.deepEqual(chart.querySelectorAll('.chart-point').map(point => point.dataset.tier), ['1', '1.5', '2', '2.5', '3']);
    assert.equal(chart.querySelector('.chart-recovery-line').dataset.profileId, 'alpha-60');
    assert.equal(chart.querySelector('.chart-recovery-line').dataset.tier, '2.5');
    assert.match(chart.querySelector('.overview-recovery-note').textContent, /Configuration dimension 60/);
    await app.navigate('#challenge');
    const largest = app.$('#kem-catalogue').querySelector('.kem-progress');
    assert.equal(largest.textContent, 'Configuration dimension 60');
    assert.match(largest.href, /profile=alpha-60/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  console.log(`\n${passed} frontend navigation regressions passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

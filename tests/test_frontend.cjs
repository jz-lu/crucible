// Run with `node tests/test_frontend.cjs`. These fixtures are not registered KEMs.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile = (id, size) => ({id, profile_id: id, instance_size: size, name: id, shared_secret_bytes: 32, correctness: {kind: 'fixture', failure_bound: '0'}, parameters: {size}, encoding: {shared_secret_bytes: 32}});
const schemes = [
  {id: 'fixture-alpha', version: '7', name: 'Alpha <KEM>', summary: 'Only a test fixture.', educational: true, profiles: [profile('alpha-3', 3), profile('alpha-4', 4)], largest_recovered_profile_id: 'alpha-3', first_recovery: {profile_id: 'alpha-3', handle: 'first-alpha', received_at: 100}, next_profile_id: 'alpha-4', size_parameter: {name: 'Instance size', symbol: 'n', meaning: 'Alpha dimension', mapping: 'The alpha mapping.'}, protocol: {construction: 'Alpha construction.', source_url: '/api/protocol/source?version=7', example_url: null}},
  {id: 'fixture-beta', version: '2', name: 'Beta KEM', summary: 'A different test fixture.', educational: false, profiles: [profile('beta-5', 5), profile('beta-6', 6)], largest_recovered_profile_id: null, next_profile_id: 'beta-5', size_parameter: {name: 'Instance size', symbol: 'n', meaning: 'Beta rank', mapping: 'The beta mapping.'}, protocol: {construction: 'Beta construction.', source_url: null, example_url: null}}
];
const route = (scheme, selected, view = 'leaderboard') => `#challenge/${scheme.id}/${scheme.version}?profile=${selected}&view=${view}`;
const railButtons = app => app.$('#size-options').querySelectorAll('.size-option');
const railSize = button => Number(button.textContent.match(/\d+/)?.[0]);
const railSizes = app => railButtons(app).map(railSize);
const activeSizeButton = app => railButtons(app).find(button => button.getAttribute('aria-pressed') === 'true');
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
  largest_recovered_profile_id: 'alpha-28', next_profile_id: 'alpha-29', first_recovery: {profile_id: 'alpha-28', handle: 'first-alpha', received_at: 100}};
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('catalogue is the first page and displays every API KEM without issuing challenges', async () => {
    const app = await createApp({schemes});
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#workspace').hidden, true);
    assert.equal(app.$('#catalogue-title').textContent.trim(), 'Schemes');
    assert.equal(app.$('#nav-challenge').textContent.trim(), 'Schemes');
    assert.match(app.$('#kem-catalogue').textContent, /Alpha <KEM>/);
    assert.match(app.$('#kem-catalogue').textContent, /Beta KEM/);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('th').map(heading => heading.textContent),
      ['KEM', 'Largest recovery', 'First recovery']);
    assert.match(app.$('#kem-catalogue').textContent, /first-alpha/);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/challenges'), false);
    const link = app.$('#kem-catalogue').querySelectorAll('a').find(item => item.href.includes('fixture-beta'));
    assert.ok(link, 'Catalogue supplies a shareable KEM link');
    link.click(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.equal(app.$('#workspace').hidden, false);
    assert.equal(app.$('#scheme-title').textContent, 'Beta KEM');
    assert.equal(app.$('#profile-select').value, '5');
    assert.equal(app.$('#profile-select').min, '5');
    assert.equal(app.$('#profile-select').max, '6');
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('a KEM opens its next unrecovered profile on the leaderboard by default', async () => {
    const app = await createApp({schemes});
    app.$('#kem-catalogue').querySelectorAll('.kem-name')[0].click(); await flush();
    assert.equal(app.$('#profile-select').value, '4');
    assert.equal(app.$('#panel-leaderboard').hidden, false);
    assert.equal(app.$('#panel-challenge').hidden, true);
    assert.equal(app.$('#tab-leaderboard').getAttribute('aria-selected'), 'true');
    const board = app.requests.find(request => request.url.pathname === '/api/leaderboard');
    assert.equal(board.url.searchParams.get('profile_id'), 'alpha-4');
    assert.equal(board.url.searchParams.get('scheme_id'), 'fixture-alpha');
    assert.equal(board.url.searchParams.get('scheme_version'), '7');
    const direct = await createApp({schemes, hash: '#challenge/fixture-alpha/7'});
    assert.equal(direct.$('#profile-select').value, '4');
    assert.equal(direct.$('#panel-leaderboard').hidden, false);
    assert.match(direct.location.hash, /profile=alpha-4/);
  });
  await test('catalogue ranks numeric recovery sizes ascending, breaks ties by name, and puts unrecovered KEMs last', async () => {
    const fixture = (name, size) => {
      const id = 'fixture-' + name.toLowerCase().replace(/ /g, '-');
      const selected = profile(id + '-size', size ?? 128);
      return {...schemes[0], id, name, profiles: [selected],
        largest_recovered_profile_id: size === null ? null : selected.id,
        first_recovery: null, next_profile_id: selected.id};
    };
    const catalogue = [fixture('Nine', 9), fixture('Zebra', 36), fixture('A waiting', null),
      fixture('Century', 100), fixture('Z waiting', null), fixture('Amber', 36)];
    const app = await createApp({schemes: catalogue});
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-name').map(link => link.textContent),
      ['Nine', 'Amber', 'Zebra', 'Century', 'A waiting', 'Z waiting']);
    assert.deepEqual(app.$('#kem-catalogue').querySelectorAll('.kem-progress').map(link => link.textContent),
      ['Size 9', 'Size 36', 'Size 36', 'Size 100']);
    assert.equal(app.$('#kem-catalogue').querySelectorAll('th').find(heading => heading.textContent === 'Largest recovery').getAttribute('aria-sort'), 'ascending');
    app.$('#kem-catalogue').querySelectorAll('.kem-name')[0].click(); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Nine');
    assert.equal(app.$('#profile-select').value, '9');
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('the size picker accepts registered integers and rejects unsupported values without changing the challenge', async () => {
    const sparse = {...schemes[0], profiles: [profile('alpha-3', 3), profile('alpha-7', 7)], next_profile_id: 'alpha-7'};
    const app = await createApp({schemes: [sparse, schemes[1]], hash: route(sparse, 'alpha-3')});
    assert.equal(app.$('#previous-size').disabled, true);
    assert.equal(app.$('#next-size').disabled, false);
    const original = app.location.hash;
    for (const invalid of ['2', '8', '4', '3.5', '', 'not-a-number']) {
      app.$('#profile-select').value = invalid;
      app.$('#profile-select').dispatchEvent({type: 'change'}); await flush();
      assert.equal(app.location.hash, original);
      assert.equal(app.$('#profile-error').hidden, false);
      assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    }
    app.$('#profile-select').value = '7';
    app.$('#profile-select').dispatchEvent({type: 'change'}); await flush();
    assert.match(app.location.hash, /profile=alpha-7/);
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.$('#previous-size').disabled, false);
    assert.equal(app.$('#next-size').disabled, true);
    assert.equal(app.$('#panel-leaderboard').hidden, false);
    app.$('#previous-size').click(); await flush();
    assert.equal(app.$('#profile-select').value, '3');
    app.$('#next-size').click(); await flush();
    assert.equal(app.$('#profile-select').value, '7');
    app.$('#profile-select').value = '3';
    app.$('#profile-select').dispatchEvent({type: 'keydown', key: 'Enter'}); await flush();
    assert.match(app.location.hash, /profile=alpha-3/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('a recovery entry opens its method and separate timing evidence in a dialog', async () => {
    const entry = {rank: 1, handle: 'fixture-solver', profile_id: 'alpha-3', received_at: 100, elapsed_seconds: 12,
      method: {name: 'Fixture recovery', pseudocode: '<script>untrusted_method()</script>'},
      compute: {hardware: 'Fixture CPU', workers: 4, wall_seconds: 2, precomputation: 'None', peak_memory: '2 KiB', notes: 'Reported by fixture'}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: url => url.pathname === '/api/leaderboard' ? {entries: [entry]} : undefined});
    const button = app.$('#leaderboard-content').querySelector('.recovery-entry-button');
    assert.ok(button);
    assert.equal(button.getAttribute('aria-haspopup'), 'dialog');
    button.focus(); button.click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.equal(app.$('#recovery-title').textContent, 'Fixture recovery');
    assert.match(app.$('#recovery-context').textContent, /fixture-solver.*Alpha <KEM>.*Size 3.*version 7/);
    const content = app.$('#recovery-content').textContent;
    assert.match(content, /Time to submit12(?:\.0)?s from issue to receipt/);
    assert.match(content, /Reported attack time2\.00s/);
    assert.match(content, /Fixture CPU/);
    assert.match(content, /untrusted_method/);
    assert.equal(app.$('#recovery-content').querySelectorAll('script').length, 0);
    app.$('#recovery-dialog').querySelector('[data-close]').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, false);
    assert.equal(app.document.activeElement, button);
    app.$('#leaderboard-content').querySelector('.recovery-entry-row').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
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
    assertLeaderboardState(app, 'Broken by:', true);
    const board = app.$('#leaderboard-content');
    assert.equal(board.querySelectorAll('.recovery-entry-row').length, 1);
    assert.deepEqual(board.querySelectorAll('.leaderboard-credit').map(credit => credit.textContent), ['via size 36']);
    assert.match(app.$('#leaderboard-scope').textContent, /recover this size or a larger one/);
    assert.match(app.$('#leaderboard-scope').textContent, /version 6/);
    assert.match(app.$('#leaderboard-footnote').textContent, /via size.*larger recovery.*actual instance/);
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/leaderboard').at(-1).url.searchParams.get('profile_id'), 'alpha-20');
    board.querySelector('.recovery-entry-button').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.match(app.$('#recovery-context').textContent, /larger-size-solver.*Alpha <KEM>.*Size 36.*version 6/);
    assert.doesNotMatch(app.$('#recovery-context').textContent, /Size 20|version 7/);
    assert.match(app.$('#recovery-content').textContent, /Instructions for the size-36 instance/);
    assert.match(app.$('#recovery-content').textContent, /Time to submit12(?:\.0)?s from issue to receipt/);
    assert.match(app.$('#recovery-content').textContent, /Original compute report/);
    assert.equal(app.$('#profile-select').value, '20');
    app.$('#recovery-dialog').querySelector('[data-close]').click(); await flush();
    await app.navigate(route(current, 'alpha-36'));
    assertLeaderboardState(app, 'Broken by:', true);
    assert.equal(board.querySelectorAll('.recovery-entry-row').length, 1);
    assert.equal(board.querySelectorAll('.leaderboard-credit').length, 0);
    board.querySelector('.recovery-entry-button').click(); await flush();
    assert.match(app.$('#recovery-context').textContent, /Size 36.*version 6/);
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
    assertLeaderboardState(app, 'Recoveries');
    assert.match(app.$('#leaderboard-content').textContent, /Loading/);
    resolveEmpty(app.envelope({entries: []})); await flush();
    assertLeaderboardState(app, 'Unsolved');
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    await app.navigate(route(schemes[0], 'alpha-4'));
    assertLeaderboardState(app, 'Broken by:', true);
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    assert.equal(app.$('#profile-select').value, '4');
    assert.match(app.$('#leaderboard-content').textContent, /size-four-solver/);
    assert.deepEqual(app.$('#panel-leaderboard').querySelectorAll('#leaderboard-content, #leaderboard-scope, #leaderboard-footnote').map(element => element.id),
      ['leaderboard-content', 'leaderboard-scope', 'leaderboard-footnote']);
    await app.navigate(route(schemes[0], 'alpha-3'));
    assertLeaderboardState(app, 'Unsolved');
    assert.doesNotMatch(app.$('#leaderboard-content').textContent, /size-four-solver/);
  });
  await test('refresh failures and missing selections remain neutral rather than being labelled unsolved', async () => {
    let failRefresh;
    let requestCount = 0;
    const failed = new Promise((resolve, reject) => { failRefresh = reject; });
    const entry = {rank: 1, handle: 'fixture-solver', profile_id: 'alpha-3', received_at: 100,
      elapsed_seconds: 2, method: {name: 'Fixture method', pseudocode: 'Fixture instructions'}, compute: {wall_seconds: 1}};
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/leaderboard') return undefined;
      return ++requestCount === 1 ? envelope({entries: [entry]}) : failed;
    }});
    assertLeaderboardState(app, 'Broken by:', true);
    app.$('#refresh-leaderboard').click(); await flush();
    assertLeaderboardState(app, 'Recoveries');
    failRefresh(new Error('Fixture outage')); await flush();
    assertLeaderboardState(app, 'Recoveries');
    assert.match(app.$('#leaderboard-content').textContent, /could not be reached/);
    assert.equal(app.$('#refresh-leaderboard').disabled, false);
    await app.navigate('#challenge');
    await app.sandbox.loadLeaderboard();
    assertLeaderboardState(app, 'Recoveries');
    assert.match(app.$('#leaderboard-content').textContent, /Choose an available profile/);
  });
  await test('the adjacent-size rail centers the selected profile and clamps to the published endpoints', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-25')});
    assert.deepEqual(railSizes(app), [23, 24, 25, 26, 27]);
    assert.equal(railSize(activeSizeButton(app)), 25);
    assert.equal(railButtons(app).filter(button => button.getAttribute('aria-pressed') === 'true').length, 1);
    assert.match(app.$('#size-range').textContent, /20.*30/);
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
    assert.equal(app.$('#profile-select').value, '29');
    assertAdvanceVisibility(app, false);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railButtons(app).filter(isLargestRecovery).map(railSize), [28]);
    for (const button of railButtons(app).filter(button => railSize(button) < 28)) {
      assert.equal(isLargestRecovery(button), false);
      assert.doesNotMatch(button.getAttribute('aria-label') || '', /recovered|solved/i);
    }
    assert.match(railButtons(app).find(button => railSize(button) === 28).getAttribute('aria-label'), /largest recovered/);
    railButtons(app).find(button => railSize(button) === 27).click(); await flush();
    assert.equal(app.$('#profile-select').value, '27');
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
    assert.match(activeSizeButton(app).getAttribute('aria-label'), /earlier size/);
    for (const size of [22, 24]) {
      const button = railButtons(app).find(button => railSize(button) === size);
      assert.match(button.getAttribute('aria-label'), /earlier size/);
      assert.doesNotMatch(button.getAttribute('aria-label'), /recovered|solved/i);
    }
    assert.match(railButtons(app).find(button => railSize(button) === 20).getAttribute('aria-label'), /recovered/);
    assert.match(railButtons(app).find(button => railSize(button) === 27).getAttribute('aria-label'), /largest recovered/);
    assert.match(railButtons(app).find(button => railSize(button) === 30).getAttribute('aria-label'), /next unsolved size/);
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
      assert.match(button.getAttribute('aria-label'), /earlier size/);
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
    assert.equal(app.$('#profile-select').value, '6');
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
    for (const invalid of ['21', '19', '129', '35.5', '']) {
      app.$('#profile-select').value = invalid;
      app.$('#jump-size').click(); await flush();
      assert.equal(app.location.hash, original);
      assert.equal(app.$('#profile-error').hidden, false);
      assert.equal(railSize(activeSizeButton(app)), 35);
    }
    app.$('#profile-select').value = '128';
    app.$('#jump-size').click(); await flush();
    assert.equal(app.$('#profile-select').value, '128');
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railSizes(app), [27, 35, 60, 90, 128]);
    assert.equal(app.$('#next-size').disabled, true);
    app.$('#previous-size').click(); await flush();
    assert.equal(app.$('#profile-select').value, '90');
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
      assert.match(app.$('#advance-size-label').textContent, /Go to next unsolved instance/);
      app.$('#advance-size').click(); await flush();
      assert.equal(app.$('#profile-select').value, '36');
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
      assert.equal(app.$('#profile-select').value, '5');
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
    const originalRoute = app.location.hash;
    const previewRequests = app.requests.length;
    slider.value = '2';
    slider.dispatchEvent({type: 'input'}); await flush();
    assert.equal(Number(app.$('#size-slider-value').textContent), 36);
    assert.equal(slider.getAttribute('aria-valuetext'), 'Instance size 36');
    assert.equal(app.location.hash, originalRoute);
    assert.equal(app.$('#profile-select').value, '36');
    assert.equal(railSize(activeSizeButton(app)), 36);
    assert.deepEqual(railSizes(app), [20, 24, 36, 90]);
    assert.match(app.$('#size-range').textContent, /20.*90/);
    assertAdvanceVisibility(app, false);
    assert.equal(app.requests.length, previewRequests);
    slider.dispatchEvent({type: 'pointercancel'}); await flush();
    assert.equal(slider.value, '1');
    assert.equal(slider.getAttribute('aria-valuetext'), 'Instance size 24');
    assert.equal(Number(app.$('#size-slider-value').textContent), 24);
    assert.equal(app.$('#profile-select').value, '24');
    assert.equal(railSize(activeSizeButton(app)), 24);
    assertAdvanceVisibility(app, true);
    assert.equal(app.location.hash, originalRoute);
    assert.equal(app.requests.length, previewRequests);
    slider.value = '2';
    slider.dispatchEvent({type: 'input'}); await flush();
    assert.equal(app.requests.length, previewRequests);
    slider.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.location.hash, route(sparse, 'alpha-36', 'protocol'));
    assert.equal(app.$('#profile-select').value, '36');
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(slider.value, '2');
    assertAdvanceVisibility(app, false);
    railButtons(app).find(button => railSize(button) === 20).click(); await flush();
    assert.equal(slider.value, '0');
    assert.equal(Number(app.$('#size-slider-value').textContent), 20);
    await app.navigate(route(sparse, 'alpha-90', 'protocol'));
    assert.equal(slider.value, '3');
    assert.equal(slider.getAttribute('aria-valuetext'), 'Instance size 90');
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
  await test('returning to the current valid size preserves rail button identity and focus through blur change', async () => {
    const app = await createApp({schemes: [ladderScheme], hash: route(ladderScheme, 'alpha-25')});
    const input = app.$('#profile-select');
    const originalButtons = railButtons(app);
    const selectedButton = activeSizeButton(app);
    const originalRoute = app.location.hash;
    input.focus();
    input.value = '25.5';
    input.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.$('#profile-error').hidden, false);
    input.value = '025';
    input.dispatchEvent({type: 'keydown', key: 'Enter'}); await flush();
    assert.equal(input.value, '25');
    assert.equal(app.$('#profile-error').hidden, true);
    assert.equal(app.location.hash, originalRoute);
    for (const [index, button] of railButtons(app).entries()) assert.equal(button, originalButtons[index]);
    // Native number inputs can emit change on blur after Enter. The rail's
    // focus target must survive that event rather than being replaced.
    selectedButton.focus();
    input.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.document.activeElement, selectedButton);
    assert.equal(selectedButton.isConnected, true);
    assert.equal(activeSizeButton(app), selectedButton);
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
    assert.equal(app.$('#profile-select').value, '23');
    assert.deepEqual(railButtons(app).filter(button => button.classList.contains('is-next-unsolved')).map(railSize), [23]);
    app.$('#profile-select').value = '22';
    app.$('#jump-size').click(); await flush();
    assert.equal(app.$('#profile-select').value, '22');
    assert.match(app.location.hash, /fixture-alpha\/6\?/);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.deepEqual(railButtons(app).filter(isLargestRecovery).map(railSize), [22]);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('public catalogue remains available when session creation fails', async () => {
    const app = await createApp({schemes, responder: url => {
      if (url.pathname === '/api/session') throw new Error('Simulated session outage');
    }});
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.match(app.$('#kem-catalogue').textContent, /Alpha <KEM>/);
    assert.match(app.$('#kem-catalogue').textContent, /Beta KEM/);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('deep links select an exact KEM, version, profile and protocol view', async () => {
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-4', 'protocol')});
    assert.equal(app.$('#scheme-title').textContent, 'Alpha <KEM>');
    assert.equal(app.$('#profile-select').value, '4');
    assert.equal(app.$('#profile-select').min, '3');
    assert.equal(app.$('#profile-select').max, '4');
    assert.equal(app.$('#profile-label').textContent, 'Instance size');
    assert.equal(app.document.querySelector('#round-deadline'), null);
    assert.equal(app.$('#panel-protocol').hidden, false);
    assert.equal(app.$('#panel-challenge').hidden, true);
    assert.match(app.$('#profile-note').textContent, /alpha dimension/i);
    assert.equal(app.$('#tab-protocol').getAttribute('aria-selected'), 'true');
  });
  await test('protocol content follows the KEM and missing downloads stay hidden', async () => {
    const app = await createApp({schemes, hash: route(schemes[0], 'alpha-3', 'protocol')});
    assert.equal(app.$('#protocol-construction').textContent, 'Alpha construction.');
    assert.equal(app.$('#protocol-downloads').hidden, false);
    assert.equal(app.$('#protocol-downloads').querySelector('a').href, '/api/protocol/source?version=7');
    await app.navigate(route(schemes[1], 'beta-5', 'protocol'));
    assert.equal(app.$('#protocol-construction').textContent, 'Beta construction.');
    assert.equal(app.$('#protocol-downloads').hidden, true);
    assert.equal(app.$('#protocol-downloads').querySelectorAll('a').length, 0);
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
  await test('archived receipts keep their release links and new challenges return to the current release', async () => {
    const previous = {...schemes[0], version: '6', profiles: [profile('alpha-3', 3)], next_profile_id: null,
      protocol: {...schemes[0].protocol, source_url: '/api/protocol/source?scheme_id=fixture-alpha&version=6'}};
    const current = {...schemes[0], previous_releases: [previous],
      first_recovery: {...schemes[0].first_recovery, scheme_version: '6'}};
    const entry = {rank: 1, handle: 'first-alpha', scheme_version: '6', profile_id: 'alpha-3', received_at: 100,
      elapsed_seconds: 2, method: {name: 'Archived method', pseudocode: 'Archived instructions'}, compute: {wall_seconds: 1}};
    const app = await createApp({schemes: [current], responder: url => url.pathname === '/api/leaderboard' ? {entries: [entry]} : undefined});
    const firstLink = app.$('#kem-catalogue').querySelector('.kem-first-handle');
    assert.match(firstLink.href, /fixture-alpha\/6\?/);
    firstLink.click(); await flush();
    assert.equal(app.$('#profile-select').max, '3');
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/leaderboard').at(-1).url.searchParams.get('scheme_version'), '6');
    app.$('#tab-protocol').click(); await flush();
    assert.equal(app.$('#protocol-downloads').querySelector('a').href, '/api/protocol/source?scheme_id=fixture-alpha&version=6');
    app.$('#tab-challenge').click(); await flush();
    assert.equal(app.$('#empty-title').textContent, 'Archived release');
    app.$('#issue-button').click(); await flush();
    assert.match(app.location.hash, /fixture-alpha\/7\?/);
    assert.match(app.location.hash, /profile=alpha-4/);
    assert.equal(app.$('#panel-challenge').hidden, false);
    await app.navigate(route(current, 'alpha-3'));
    app.$('#leaderboard-content').querySelector('.recovery-entry-button').click(); await flush();
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
    const app = await createApp({schemes, user: {id: 'fixture-user', handle: 'fixture'}, hash: route(schemes[0], 'alpha-3'), responder: (url, options, envelope) => {
      if (url.pathname !== '/api/challenges') return undefined;
      return url.searchParams.get('profile_id') === 'alpha-3' ? alpha : envelope({challenge: challenge('beta-instance', 'beta-5', envelope)});
    }});
    await app.navigate(route(schemes[1], 'beta-5'));
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
    assertLeaderboardState(app, 'Recoveries');
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
      assertLeaderboardState(app, 'Recoveries');
      await app.navigate(route(schemes[0], 'alpha-4'));
      assertLeaderboardState(app, 'Broken by:', true);
      if (lateResult === 'empty') resolveOld(app.envelope({entries: []}));
      else rejectOld(new Error('Stale fixture failure'));
      await flush();
      assertLeaderboardState(app, 'Broken by:', true);
      assert.equal(app.$('#profile-select').value, '4');
      assert.match(app.$('#leaderboard-content').textContent, /current-size-solver/);
      assert.doesNotMatch(app.$('#leaderboard-content').textContent, /could not be reached|No accepted recoveries/);
    }
  });
  console.log(`\n${passed} frontend navigation regressions passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

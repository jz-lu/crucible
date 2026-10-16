// Run with node. Requests remain inside the HTML-backed DOM fixture.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'account-a', handle: 'alice'};
const clone = value => JSON.parse(JSON.stringify(value));
const scheme = {
  id: 'fixture', version: '1', name: 'Fixture', next_profile_id: 'tier-1',
  review_status: 'local-beta', scoring_eligible: true,
  profiles: [1, 2, 3].map(tier => ({id: 'tier-' + tier, instance_size: tier,
    shared_secret_bytes: 32, parameters: {tier}, encoding: {shared_secret_bytes: 32}, correctness: {}})),
  size_parameter: {meaning: 'Fixture'}, protocol: {},
  profile_metrics: {'tier-1': {public_key_bytes: 10}, 'tier-2': {public_key_bytes: 20}, 'tier-3': {public_key_bytes: 30}}
};
const route = (tier = 1, view = 'leaderboard') => `#challenge/fixture/1?profile=tier-${tier}&view=${view}`;
const participant = (points = 0) => ({handle: 'alice', points, rank: 1,
  score_breakdown: {progress: points, early_recovery: 0, scheme_contributions: 0},
  accepted_proposal_count: 0, records: [], first_records: [], recent_recoveries: []});
const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
const response = data => ({ok: true, status: 200, json: async () => data});
const recovery = (tier = 1, handle = 'bob') => ({profile_id: 'tier-' + tier, handle,
  submission_id: 'receipt-' + tier + '-' + handle, received_at: 100, scheme_version: '1'});
const record = (tier = 1, selected = tier, handle = 'bob') => ({...recovery(tier, handle), rank: 1,
  instance_size: tier, is_inherited: tier !== selected, method: {name: 'Fixture recovery'}});
const recover = (current, tier, handle = 'bob') => ({...current,
  recovered_profile_ids: [...new Set([...(current.recovered_profile_ids || []), 'tier-' + tier])],
  largest_recovered_profile_id: 'tier-' + tier, next_profile_id: tier < 3 ? 'tier-' + (tier + 1) : null,
  first_recovery: recovery(tier, handle)});
const leaderboardReads = app => app.requests.filter(request => request.url.pathname === '/api/leaderboard').length;
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }

(async () => {
  await test('fresh catalogue objects do not disable an existing Take on this stage action', async () => {
    const app = await createApp({schemes: [scheme], hash: route(), responder: (url, options, envelope) =>
      url.pathname === '/api/schemes' ? envelope({schemes: clone([scheme])}) : undefined});
    const action = app.$('.tier-record-challenge');
    await app.sandbox.synchronizeSession();
    assert.equal(action.isConnected, true, 'Unchanged records are not rebuilt in the background');
    action.click(); await flush();
    assert.equal(app.location.hash, route(1, 'challenge'));
    assert.equal(app.$('#auth-dialog').open, true);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });

  await test('overlapping session events share one read and avoid hidden protocol rendering', async () => {
    const app = await createApp({schemes: [scheme], user, hash: route()});
    const original = app.sandbox.fetch, gate = deferred();
    let sessions = 0, details = 0;
    app.sandbox.fetch = async (url, options) => {
      if (url === '/api/session') { sessions++; await gate.promise; }
      return original(url, options);
    };
    app.sandbox.renderProtocol = () => { details++; };
    const start = app.requests.length;
    const first = app.sandbox.synchronizeSession();
    const second = app.sandbox.synchronizeSession();
    await flush(); assert.equal(sessions, 1);
    gate.resolve(); await Promise.all([first, second]);
    assert.equal(details, 0);
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session', '/api/schemes']);
    await app.navigate(route(1, 'protocol'));
    assert.equal(details, 0, 'The same profile already has its immutable protocol details');
    await app.navigate(route(2, 'protocol'));
    assert.equal(details, 1, 'A genuinely changed selection still renders its exact protocol');
  });

  await test('an external recovery updates the visible unsolved record once, without writes', async () => {
    let current = clone(scheme), entries = [];
    const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
      if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
      if (url.pathname === '/api/leaderboard') return envelope({entries: clone(entries)});
    }});
    assert.equal(app.$('#leaderboard-title').textContent, 'Unsolved');
    const start = app.requests.length;
    current = recover(current, 1); entries = [record()];
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(app.$('#leaderboard-title').textContent, 'Broken by');
    assert.equal(app.$('.tier-winner-name').textContent, 'bob');
    assert.equal(app.$('#advance-size-value').textContent, '2');
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session', '/api/schemes', '/api/leaderboard']);
    const winner = app.$('.tier-winner');
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), 2);
    assert.equal(app.$('.tier-winner'), winner, 'Unchanged metadata preserves the card');
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });

  await test('only selected or higher-tier recovery metadata refreshes an inherited record', async () => {
    let current = clone(scheme), entries = [];
    const app = await createApp({schemes: [current], hash: route(2), responder: (url, options, envelope) => {
      if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
      if (url.pathname === '/api/leaderboard') return envelope({entries: clone(entries)});
    }});
    current = {...recover(current, 1), participant_count: 12};
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), 1, 'A lower-tier solve cannot change this record');
    current = recover(current, 3); entries = [record(3, 2)];
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), 2);
    assert.equal(app.$('.tier-winner-name').textContent, 'bob');
    assert.equal(app.$('.leaderboard-credit').textContent, 'Via Stage 3');
  });

  await test('exact stages and named parameter sets ignore other profiles and observe their own recovery metadata', async () => {
    for (const policy of [{record_inheritance: false}, {selection_kind: 'parameter-set'}]) {
      let current = {...clone(scheme), ...policy}, entries = [];
      const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
        if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
        if (url.pathname === '/api/leaderboard') return envelope({entries: clone(entries)});
      }});
      current = {...recover(current, 3), profile_recoveries: [recovery(3)]};
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 1);
      assert.equal(app.$('#leaderboard-title').textContent, 'Unsolved');
      current = {...current, recovered_profile_ids: ['tier-1', 'tier-3'], profile_recoveries: [recovery(1), recovery(3)]};
      entries = [record()];
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 2);
      assert.equal(app.$('.tier-winner-name').textContent, 'bob');
      current.profile_recoveries[0] = recovery(1, 'carol'); entries = [record(1, 1, 'carol')];
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 3, 'A revised first receipt for this exact profile is relevant');
      assert.equal(app.$('.tier-winner-name').textContent, 'carol');
      assert.equal(app.$('#leaderboard-content').querySelector('.leaderboard-credit'), null);
    }
  });

  await test('unrelated catalogue changes and hidden record tabs do not request a leaderboard', async () => {
    let current = clone(scheme), other = {...clone(scheme), id: 'other'};
    const app = await createApp({schemes: [current, other], hash: route(), responder: (url, options, envelope) => {
      if (url.pathname === '/api/schemes') return envelope({schemes: clone([current, other])});
    }});
    other = recover(other, 3); current = {...current, participant_count: 20};
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), 1);
    for (const view of ['protocol', 'challenge']) {
      await app.navigate(route(1, view));
      current = recover(current, view === 'protocol' ? 1 : 2);
      const reads = leaderboardReads(app);
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), reads);
    }
    await app.navigate(route());
    app.document.hidden = true; current = recover(current, 3);
    const reads = leaderboardReads(app);
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), reads, 'A hidden browser tab does not refresh its card');
    app.document.hidden = false;
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), reads + 1);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });

  await test('background record refresh preserves an open disclosure and shares an in-flight metadata read', async () => {
    let current = recover(clone(scheme), 1), pending = null;
    const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
      if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
      if (url.pathname === '/api/leaderboard') return pending ? pending.promise : envelope({entries: [record()]});
    }});
    const winner = app.$('.tier-winner'), notes = app.$('#tier-record-notes'); notes.open = true;
    current = recover(current, 2); pending = deferred();
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(app.$('.tier-winner'), winner);
    assert.equal(notes.hidden, false); assert.ok(notes.open);
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), 2, 'The same pending refresh is not duplicated');
    pending.resolve(app.envelope({entries: [record()]})); await flush();
    assert.equal(app.$('#tier-record-notes'), notes); assert.ok(notes.open);
    assert.equal(app.$('#refresh-leaderboard').disabled, false);
  });

  await test('a failed record refresh retries unchanged metadata on the next session sync', async () => {
    for (const initialFailure of [false, true]) {
      let current = clone(scheme), fail = initialFailure, entries = [];
      const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
        if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
        if (url.pathname === '/api/leaderboard') {
          if (fail) throw new Error('Temporary record outage');
          return envelope({entries: clone(entries)});
        }
      }});
      current = recover(current, 1); entries = [record()]; fail = true;
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 2);
      assert.equal(app.$('#tier-record').dataset.state, initialFailure ? 'error' : 'unsolved');
      fail = false;
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 3);
      assert.equal(app.$('.tier-winner-name').textContent, 'bob');
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 3);
      fail = true;
      await app.sandbox.loadLeaderboard({preserve: true});
      assert.equal(app.$('.tier-winner-name').textContent, 'bob');
      fail = false;
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), 5, 'A failed read of unchanged metadata is also retried');
    }
  });

  await test('a superseded record read cannot restore an older winner for the same selection', async () => {
    let current = clone(scheme), pending = null;
    const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
      if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
      if (url.pathname === '/api/leaderboard' && pending) return pending.promise;
    }});
    current = recover(current, 1); const older = pending = deferred();
    await app.sandbox.synchronizeSession(); await flush();
    current = recover(current, 2); const newer = pending = deferred();
    await app.sandbox.synchronizeSession(); await flush();
    newer.resolve(app.envelope({entries: [record(2, 1, 'carol')]})); await flush();
    older.resolve(app.envelope({entries: [record()]})); await flush();
    assert.equal(app.$('.tier-winner-name').textContent, 'carol');
    assert.equal(app.$('.leaderboard-credit').textContent, 'Via Stage 2');
    const reads = leaderboardReads(app);
    await app.sandbox.synchronizeSession(); await flush();
    assert.equal(leaderboardReads(app), reads);
  });

  await test('late record refreshes cannot overwrite a new selection or a hidden tab', async () => {
    for (const destination of [route(2), route(1, 'protocol')]) {
      let current = clone(scheme), pending = null;
      const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) => {
        if (url.pathname === '/api/schemes') return envelope({schemes: clone([current])});
        if (url.pathname === '/api/leaderboard' && url.searchParams.get('profile_id') === 'tier-1' && pending) return pending.promise;
      }});
      current = recover(current, 1); pending = deferred();
      await app.sandbox.synchronizeSession(); await flush();
      await app.navigate(destination);
      const content = app.$('#leaderboard-content').firstChild;
      pending.resolve(app.envelope({entries: [record()]})); await flush();
      assert.equal(app.$('#leaderboard-content').firstChild, content);
      assert.equal(app.$('#leaderboard-content').querySelector('.tier-winner'), null);
      pending = null;
      await app.navigate(route());
      assert.equal(app.$('#leaderboard-title').textContent, 'Unsolved');
      const reads = leaderboardReads(app);
      await app.sandbox.synchronizeSession(); await flush();
      assert.equal(leaderboardReads(app), reads, 'The fresh navigation owns the successful metadata key');
    }
  });

  await test('out-of-order catalogue reads cannot overwrite a newer catalogue', async () => {
    const app = await createApp({schemes: [scheme], hash: '#scheme/fixture/1'});
    const reads = [];
    app.sandbox.fetch = () => { const pending = deferred(); reads.push(pending); return pending.promise; };
    const older = app.sandbox.refreshProfiles(), newer = app.sandbox.refreshProfiles();
    reads[1].resolve(response(app.envelope({schemes: [{...clone(scheme), name: 'New catalogue'}]})));
    await newer; assert.equal(app.$('#overview-title').textContent, 'New catalogue');
    reads[0].resolve(response(app.envelope({schemes: [{...clone(scheme), name: 'Older catalogue'}]})));
    await older; assert.equal(app.$('#overview-title').textContent, 'New catalogue');
  });

  await test('a stale catalogue failure cannot replace a newer successful refresh', async () => {
    const app = await createApp({schemes: [scheme]});
    const reads = [];
    app.sandbox.fetch = () => { const pending = deferred(); reads.push(pending); return pending.promise; };
    const older = app.sandbox.refreshProfiles(), newer = app.sandbox.refreshProfiles();
    reads[1].resolve(response(app.envelope({schemes: [{...clone(scheme), name: 'Fresh catalogue'}]})));
    await newer;
    reads[0].resolve({ok: false, status: 503, json: async () => ({detail: 'Obsolete failure'})});
    assert.equal(await older, false);
    assert.match(app.$('#kem-catalogue').textContent, /Fresh catalogue/);
    assert.equal(app.$('#global-message').hidden, true);
  });

  await test('an older session response cannot restore an account after sign-out', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#about'});
    const gate = deferred(); app.sandbox.fetch = () => gate.promise;
    const synchronization = app.sandbox.synchronizeSession();
    app.sandbox.setSession(app.envelope({user: null, csrf_token: 'signed-out'}));
    gate.resolve(response(app.envelope({user, csrf_token: 'old-session'})));
    await synchronization;
    assert.equal(app.$('#auth-button').hidden, false);
    assert.equal(app.$('#account-handle').hidden, true);
  });

  await test('background sync preserves profile disclosures and expanded connected-agent controls', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#person/alice', responder: (url, options, envelope) => {
      if (url.pathname === '/api/people/alice') return envelope({person: participant()});
      if (url.pathname === '/api/agent/tokens') return envelope({tokens: []});
    }});
    const disclosures = ['#first-records', '#personal-records', '#recent-solves', '.person-score-breakdown', '#agent-connections'].map(selector => app.$(selector));
    for (const disclosure of disclosures) disclosure.open = true;
    app.$('#agent-connections').dispatchEvent({type: 'toggle'}); await flush();
    const profile = app.$('#person-content').firstChild;
    const start = app.requests.length;
    await app.sandbox.synchronizeSession();
    assert.equal(app.$('#person-content').firstChild, profile);
    for (const disclosure of disclosures) assert.ok(disclosure.isConnected && disclosure.open);
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session', '/api/people/alice']);
    assert.equal(app.requests.find(request => request.url.pathname === '/api/people/alice').url.searchParams.get('include_milestones'), 'false');
  });

  await test('agent or other-tab recoveries update the visible profile while disclosures and agent controls stay open', async () => {
    let current = participant();
    const app = await createApp({schemes: [scheme], user, hash: '#person/alice', responder: (url, options, envelope) => {
      if (url.pathname === '/api/people/alice') return envelope({person: clone(current)});
      if (url.pathname === '/api/agent/tokens') return envelope({tokens: []});
    }});
    for (const selector of ['#first-records', '#personal-records', '#recent-solves', '.person-score-breakdown', '#agent-connections']) app.$(selector).open = true;
    app.$('#agent-connections').dispatchEvent({type: 'toggle'}); await flush();
    const settings = app.$('#agent-connections'), settingsContent = app.$('#agent-token-list').firstChild;
    const record = {scheme_id: 'fixture', scheme_version: '1', scheme_name: 'Fixture', profile_id: 'tier-1', tier: 1,
      submission_id: 'external-receipt', received_at: 100, is_current: true, scoring_eligible: true,
      score: {progress: 10, early_recovery: 40, total: 50}};
    current = {...participant(50), score_breakdown: {progress: 10, early_recovery: 40, scheme_contributions: 0},
      records: [record], first_records: [{...record, place: 1}], recent_recoveries: [record]};
    await app.sandbox.synchronizeSession();
    assert.match(app.$('.person-stats').textContent, /Points50/);
    assert.equal(app.$('#first-records').querySelectorAll('.milestone-result').length, 1);
    assert.equal(app.$('#personal-records').querySelectorAll('.person-record-result').length, 1);
    assert.equal(app.$('#recent-solves').querySelectorAll('.history-item').length, 1);
    for (const selector of ['#first-records', '#personal-records', '#recent-solves', '.person-score-breakdown']) assert.ok(app.$(selector).open);
    assert.equal(app.$('#agent-connections'), settings); assert.ok(settings.open);
    assert.equal(app.$('#agent-token-list').firstChild, settingsContent);
    const stableStats = app.$('.person-stats'), stableRecords = app.$('#personal-records');
    await app.sandbox.synchronizeSession();
    assert.equal(app.$('.person-stats'), stableStats, 'Identical public data does not replace existing DOM');
    assert.equal(app.$('#personal-records'), stableRecords);
  });

  await test('late background profile results are discarded after an authentication revision', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#person/alice', responder: (url, options, envelope) =>
      url.pathname === '/api/people/alice' ? envelope({person: participant()}) : undefined});
    const original = app.sandbox.fetch, gate = deferred();
    app.sandbox.fetch = (url, options) => url.startsWith('/api/people/') ? gate.promise : original(url, options);
    const synchronization = app.sandbox.synchronizeSession(); await flush();
    app.sandbox.setSession(app.envelope({user: null, csrf_token: 'new-epoch'}));
    gate.resolve(response(app.envelope({person: participant(999)})));
    await synchronization;
    assert.match(app.$('.person-stats').textContent, /Points0/);
    assert.equal(app.$('#agent-connections').hidden, true);
    assert.equal(app.$('#auth-button').hidden, false);
  });

  await test('background sync keeps participant pagination, while explicit Refresh requests current compact data', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#people', responder: (url, options, envelope) => {
      if (url.pathname === '/api/people') {
        const offset = Number(url.searchParams.get('offset'));
        return envelope({people: [{...participant(), handle: offset ? 'bob' : 'alice'}],
          pagination: {limit: 50, offset, has_more: !offset}});
      }
    }});
    app.$('#more-people').click(); await flush();
    const content = app.$('#people-content').firstChild, start = app.requests.length;
    await app.sandbox.synchronizeSession();
    assert.equal(app.$('#people-content').firstChild, content);
    assert.deepEqual(app.$('#people-content').querySelectorAll('.person-link').map(link => link.textContent), ['alice', 'bob']);
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session']);
    app.$('#refresh-people').click(); await flush();
    assert.deepEqual(app.$('#people-content').querySelectorAll('.person-link').map(link => link.textContent), ['alice']);
    for (const request of app.requests.filter(request => request.url.pathname === '/api/people')) {
      assert.equal(request.url.searchParams.get('include_records'), 'false');
    }
  });

  await test('background sync does not re-enter a proposal editor or erase its draft', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#propose', responder: (url, options, envelope) =>
      url.pathname === '/api/me/protocol-proposals' ? envelope({proposals: []}) : undefined});
    app.$('#proposal-review-notes').value = 'Unsent review notes';
    app.$('#proposal-review-evidence').value = 'Unsent evidence';
    const editor = app.$('#proposal-preview').firstChild;
    let routeEntries = 0;
    app.sandbox.enterProposalPage = () => { routeEntries++; };
    const start = app.requests.length;
    await app.sandbox.synchronizeSession();
    assert.equal(routeEntries, 0);
    assert.equal(app.$('#proposal-review-notes').value, 'Unsent review notes');
    assert.equal(app.$('#proposal-review-evidence').value, 'Unsent evidence');
    assert.equal(app.$('#proposal-preview').firstChild, editor);
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session']);
  });

  await test('background sync does not cancel or repeat a pending explicit connection approval', async () => {
    const gate = deferred();
    const connection = {user_code: 'ABCD-EFGH', label: 'Fixture agent', scopes: ['challenges:read'],
      created_at: Date.now()/1000, expires_at: Date.now()/1000 + 120, status: 'pending'};
    const app = await createApp({schemes: [scheme], user, hash: '#connect?code=ABCD-EFGH', responder: (url, options, envelope) => {
      if (url.pathname.endsWith('/approve')) return gate.promise;
      if (url.pathname === '/api/agent/connect/ABCD-EFGH') return envelope({connection});
    }});
    app.$('[data-agent-decision="approve"]').click(); await flush();
    const start = app.requests.length;
    await app.sandbox.synchronizeSession();
    assert.deepEqual(app.requests.slice(start).map(request => request.url.pathname), ['/api/session']);
    assert.ok(app.$('[data-agent-decision="approve"]').disabled);
    gate.resolve(app.envelope({connection: {...connection, status: 'approved'}})); await flush();
    assert.match(app.$('#connect-content').textContent, /Connection approved/);
    assert.equal(app.requests.filter(request => request.options.method === 'POST').length, 1);
  });

  await test('background catalogue updates preserve graph disclosures and an active tier preview', async () => {
    let current = clone(scheme);
    const app = await createApp({schemes: [current], hash: '#scheme/fixture/1', responder: (url, options, envelope) =>
      url.pathname === '/api/schemes' ? envelope({schemes: clone([current])}) : undefined});
    app.$('#overview-chart-public').querySelector('details').open = true;
    current.largest_recovered_profile_id = 'tier-2';
    await app.sandbox.synchronizeSession();
    assert.ok(app.$('#overview-chart-public').querySelector('details').open);
    assert.equal(app.$('#overview-chart-public').querySelectorAll('.chart-recovery-line').length, 1);
    assert.equal(app.$('#overview-chart-public').querySelector('.chart-recovery-line').dataset.tier, '2');
    await app.navigate(route());
    const slider = app.$('#size-slider'); slider.focus(); slider.value = '2'; slider.dispatchEvent({type: 'input'});
    current.largest_recovered_profile_id = 'tier-3';
    await app.sandbox.synchronizeSession();
    assert.equal(app.$('#size-slider-value').textContent, '3');
    assert.equal(app.location.hash, route());
    slider.dispatchEvent({type: 'change'}); await flush();
    assert.equal(app.location.hash, route(3));
  });

  await test('metadata deferred while a tier control is focused renders at the next safe refresh', async () => {
    for (const selector of ['#size-slider', '#profile-select']) {
      let current = clone(scheme);
      const app = await createApp({schemes: [current], hash: route(), responder: (url, options, envelope) =>
        url.pathname === '/api/schemes' ? envelope({schemes: clone([current])}) : undefined});
      app.$(selector).focus();
      if (selector === '#profile-select') app.$(selector).value = '2';
      current = {...current, largest_recovered_profile_id: 'tier-2', next_profile_id: 'tier-3'};
      await app.sandbox.synchronizeSession();
      assert.equal(app.$('#advance-size-value').textContent, '1', 'Focused control remains untouched');
      app.document.body.focus();
      await app.sandbox.synchronizeSession();
      assert.equal(app.$('#advance-size-value').textContent, '3');
      assert.equal(app.$('#size-options').querySelectorAll('.is-prior').length, 2);
      if (selector === '#profile-select') assert.equal(app.$(selector).value, '2', 'The unsent jump is preserved');
      assert.equal(app.location.hash, route());
    }
  });

  await test('accepted recovery refresh updates a visible profile without closing its disclosures', async () => {
    let points = 0;
    const app = await createApp({schemes: [scheme], user, hash: '#person/alice', responder: (url, options, envelope) =>
      url.pathname === '/api/people/alice' ? envelope({person: participant(points)}) : undefined});
    app.$('#personal-records').open = true; app.$('#first-records').open = true;
    points = 50;
    await app.sandbox.refreshRecoveryViews();
    assert.match(app.$('.person-stats').textContent, /Points50/);
    assert.ok(app.$('#personal-records').open && app.$('#first-records').open);
    assert.equal(app.requests.filter(request => request.url.pathname === '/api/people/alice').length, 2);
  });

  await test('request timeout covers a stalled JSON body and preserves uncertain-write status', async () => {
    const app = await createApp({schemes: [scheme]});
    let expire, cleared = false;
    app.sandbox.setTimeout = callback => { expire = callback; return 1; };
    app.sandbox.clearTimeout = () => { cleared = true; };
    app.sandbox.fetch = async (url, options) => ({ok: true, status: 200, json: () => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted body')));
    })});
    const pending = app.sandbox.api('/api/test-write', {});
    const rejected = assert.rejects(pending, error => error.status === 0 && /too long/.test(error.message));
    await flush(); assert.equal(cleared, false);
    expire(); await rejected; assert.equal(cleared, true);
  });

  await test('malformed success bodies are retryable errors, while HTTP failures retain their status', async () => {
    const app = await createApp({schemes: [scheme]});
    app.sandbox.fetch = async () => ({ok: true, status: 200, json: async () => { throw new Error('truncated JSON'); }});
    await assert.rejects(app.sandbox.api('/api/test-write', {}), error => error.status === 0 && /unreadable response/.test(error.message));
    app.sandbox.fetch = async () => ({ok: false, status: 403, json: async () => { throw new Error('not JSON'); }});
    await assert.rejects(app.sandbox.api('/api/test-write', {}), error => error.status === 403);
  });

  console.log(`\n${passed} request lifecycle regressions passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

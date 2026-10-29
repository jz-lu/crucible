const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const scoring = {status: 'active', policy_id: 'crucible-points-v11', ordering: 'points_desc_handle'};
const record = {scheme_id: 'fixture', scheme_name: 'Fixture scheme', scheme_version: '2', profile_id: 'tier-4', tier: 4, submission_id: 'receipt-a', received_at: 100, is_current: true, scoring_eligible: true, score: {progress: 0, early_recovery: 100, total: 100}};
const person = handle => ({handle, points: 200, rank: 1, score_status: 'active', score_breakdown: {progress: 0, early_recovery: 100, scheme_contributions: 0, note_contributions: 0, bounty_points:100}, accepted_proposal_count: 0, schemes_recovered: 1, correct_submission_count: 2, records: [record], recent_recoveries: [record]});
const schemes = [{id: 'fixture', version: '2', name: 'Fixture scheme', profiles: [{id: 'tier-4', instance_size: 4}], largest_recovered_profile_id: 'tier-4', first_recovery: {handle: 'alpha', profile_id: 'tier-4', submission_id: 'receipt-a'}}];
const baseResponder = (url, options, envelope) => {
  if (url.pathname === '/api/people') return envelope({people: [person('alpha'), {...person('beta'), points: 0, rank: 2}], pagination: {limit: 50, offset: 0, total: 2, has_more: false}, scoring});
  if (url.pathname.startsWith('/api/people/')) return envelope({person: person(decodeURIComponent(url.pathname.slice('/api/people/'.length))), scoring});
};
let passed = 0;
async function test(name, run) {await run(); passed++; console.log('PASS ' + name);}
(async () => {
  await test('Leaderboard precedes My submissions in account navigation and loads only on demand', async () => {
    const app = await createApp({schemes, responder: baseResponder});
    assert.deepEqual(app.$('.primary-nav').children.filter(e => !e.hidden).map(e => e.textContent.trim()), ['Schemes', 'Research notebook', 'About us']);
    const accountActions = app.$('.account-actions').children;
    assert.equal(accountActions.indexOf(app.$('#nav-people')), accountActions.indexOf(app.$('#history-button')) - 1);
    assert.equal(app.$('#history-button').hidden, true);
    assert.equal(app.$('#nav-people').hidden, false);
    assert.equal(app.requests.some(r => r.url.pathname === '/api/people'), false);
    app.$('#nav-people').click(); await flush();
    assert.equal(app.location.hash, '#people');
    assert.equal(app.$('#people-workspace').hidden, false);
    assert.equal(app.$('#nav-people').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'), null);
    assert.equal(app.document.title, 'Leaderboard — Crucible');
    assert.equal(app.document.activeElement, app.$('#people-workspace'));
    assert.deepEqual(app.$('#people-content').querySelectorAll('.person-link').map(e => e.textContent), ['alpha', 'beta']);
    assert.deepEqual(app.$('#people-content').querySelectorAll('.person-points').map(e => e.textContent), ['200', '0']);
    assert.equal(app.$('#people-score-note').hidden, true);
    assert.equal(app.requests.some(r => r.options.method === 'POST'), false);
    app.sandbox.showWorkspace('access');
    assert.equal(app.$('.primary-nav').hidden, true);
    assert.equal(app.$('#nav-people').hidden, true);
    app.sandbox.showWorkspace('people');
    assert.equal(app.$('#nav-people').hidden, false);
  });
  await test('Recovered by and the signed-in handle lead to real profiles; Back returns to the catalogue', async () => {
    const app = await createApp({schemes, user: {id: 'a', handle: 'alpha'}, responder: baseResponder});
    assert.equal(app.$('#history-button').hidden, false);
    assert.equal(app.$('#nav-people').hidden, false);
    assert.equal(app.$('#account-handle').href, '#person/alpha');
    const recovered = app.$('#kem-catalogue').querySelector('.kem-first-handle');
    assert.equal(recovered.tagName, 'A');
    recovered.click(); await flush();
    assert.equal(app.$('#person-title').textContent, 'alpha');
    assert.equal(app.$('#person-workspace').hidden, false);
    assert.equal(app.$('#recovery-dialog').open, false);
    assert.match(app.$('#person-content').textContent, /Record breakthroughs100Admitted schemes0Research bounties100Research notes0/);
    assert.match(app.$('#person-content').textContent, /100 points, once per person per scheme/);
    assert.doesNotMatch(app.$('#person-content').textContent, /500 personal-progress|2,000 points for your best first/);
    app.history.back(); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, false);
  });
  await test('profile recovery buttons preserve the archived receipt and show method details', async () => {
    const archived = {...record, scheme_version: '1', is_current: false, scoring_eligible: false, score: null};
    const entry = {id: 'receipt-a', handle: 'alpha', status: 'correct', scheme_id: 'fixture', scheme_version: '1', profile_id: 'tier-4', instance_size: 4, received_at: 100, elapsed_seconds: 1, method: {name: 'Original method', pseudocode: 'Original steps'}, compute: {hardware: 'CPU'}};
    const app = await createApp({schemes, hash: '#person/alpha', responder: (url, opts, envelope) => {
      if (url.pathname === '/api/people/alpha') return envelope({person: {...person('alpha'), records: [archived], recent_recoveries: [archived]}, scoring});
      if (url.pathname === '/api/submissions/receipt-a') return envelope({submission: entry});
    }});
    assert.match(app.$('#person-content').textContent, /Archived · release 1/);
    assert.match(app.$('#person-content').textContent, /Not scored/);
    app.$('#person-content').querySelector('.person-record-result').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
    assert.match(app.$('#recovery-context').textContent, /version 1/);
    assert.match(app.$('#recovery-content').textContent, /Original steps/);
    app.$('#recovery-dialog').close();
    app.$('#person-content').querySelector('.history-item').click(); await flush();
    assert.equal(app.$('#recovery-dialog').open, true);
  });
  await test('profile deep links encode handles and render content as text', async () => {
    const handle = '<test>/name?';
    const app = await createApp({schemes, hash: '#person/' + encodeURIComponent(handle), responder: baseResponder});
    assert.equal(app.$('#person-title').textContent, handle);
    assert.equal(app.requests.filter(r => r.url.pathname.startsWith('/api/people/')).at(-1).url.pathname, '/api/people/' + encodeURIComponent(handle));
    assert.equal(app.$('#person-title').children.length, 0);
    assert.equal(app.$('.skip-link').href, '#person-workspace');
  });
  await test('late profile responses cannot replace another participant or a different page', async () => {
    let release;
    const slow = new Promise(resolve => {release = resolve;});
    const app = await createApp({schemes, hash: '#person/alpha', responder: (url, opts, envelope) => url.pathname === '/api/people/alpha' ? slow : baseResponder(url, opts, envelope)});
    await app.navigate('#person/beta');
    assert.equal(app.$('#person-title').textContent, 'beta');
    release(app.envelope({person: person('alpha')})); await flush();
    assert.equal(app.$('#person-title').textContent, 'beta');
    assert.doesNotMatch(app.$('#person-content').textContent, /alpha/);
    await app.navigate('#about');
    assert.equal(app.$('#person-workspace').hidden, true);
  });
  await test('pagination appends participants without duplicates and refresh starts at zero', async () => {
    const app = await createApp({schemes, hash: '#people', responder: (url, opts, envelope) => {
      if (url.pathname !== '/api/people') return;
      const offset = Number(url.searchParams.get('offset'));
      return envelope({people: offset ? [person('beta'), person('gamma')] : [person('alpha'), person('beta')], pagination: {limit: 2, offset, total: 3, has_more: offset === 0}, scoring});
    }});
    assert.equal(app.$('#more-people').hidden, false);
    app.$('#more-people').click(); await flush();
    assert.deepEqual(app.$('#people-content').querySelectorAll('.person-link').map(e => e.textContent), ['alpha', 'beta', 'gamma']);
    assert.equal(app.$('#more-people').hidden, true);
    app.$('#refresh-people').click(); await flush();
    assert.equal(app.requests.filter(r => r.url.pathname === '/api/people').at(-1).url.searchParams.get('offset'), '0');
  });
  await test('pending, empty and failed profiles do not fabricate points or recoveries', async () => {
    const app = await createApp({schemes, hash: '#person/new', responder: (url, opts, envelope) => url.pathname === '/api/people/new' ? envelope({person: {handle: 'new', points: null, schemes_recovered: 0, correct_submission_count: 0, records: [], recent_recoveries: []}}) : undefined});
    assert.match(app.$('#person-content').textContent, /Pending/);
    assert.match(app.$('#person-content').textContent, /No solved challenges yet/);
    await app.navigate('#person/missing');
    assert.match(app.$('#person-content').textContent, /could not|connection|request|Unable/i);
    assert.doesNotMatch(app.$('#person-content').textContent, /No solved challenges yet/);
  });
  console.log(`${passed} people/profile frontend checks passed.`);
})().catch(error => {console.error(error); process.exitCode = 1;});

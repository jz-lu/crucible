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
    assert.deepEqual(app.$('.primary-nav').children.filter(e => !e.hidden).map(e => e.textContent.trim()), ['Schemes', 'Bounties', 'Notes', 'About us']);
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
  await test('How points work opens without a read, uses profile category names and restores its trigger on close', async () => {
    const app = await createApp({schemes, hash: '#people', responder: baseResponder});
    const trigger = app.$('#points-help-button'), dialog = app.$('#points-help-dialog');
    const before = app.requests.length; trigger.click();
    assert.equal(dialog.open, true); assert.equal(app.document.body.style.overflow, 'hidden');
    assert.equal(app.requests.length, before);
    for (const label of ['Record breakthroughs','Admitted schemes','Research bounties','Research notes','Bounty allowance']) assert.ok(dialog.textContent.includes(label));
    assert.match(dialog.textContent, /2,000 points/); assert.match(dialog.textContent, /100 points per completed active week/);
    assert.match(dialog.textContent, /review approval alone earns 0 points/);
    assert.match(dialog.textContent, /independent reviewer approves the exact solution evidence/);
    assert.match(dialog.textContent, /proposer sign-off is not required/);
    assert.match(dialog.textContent, /release marked Not scored earns 0 points/);
    dialog.querySelector('[data-close]').click();
    assert.equal(dialog.open, false); assert.equal(app.document.activeElement, trigger);
    assert.equal(app.document.body.style.overflow, '');
    trigger.click(); await app.navigate('#about');
    assert.equal(dialog.open, false); assert.notEqual(app.document.activeElement, trigger);
    trigger.click(); assert.equal(dialog.open, false, 'A hidden Leaderboard trigger cannot open its dialog');
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
  await test('profile stats begin with an ordinal leaderboard place and a separate suffix', async () => {
    for (const [rank, expected] of [[1,'1st'],[2,'2nd'],[3,'3rd'],[5,'5th'],[11,'11th'],[12,'12th'],[13,'13th'],[21,'21st'],[22,'22nd'],[23,'23rd'],[111,'111th']]) {
      const app = await createApp({schemes, hash:'#person/alpha', responder:(url, opts, envelope)=>url.pathname==='/api/people/alpha' ? envelope({person:{...person('alpha'),rank},scoring}) : baseResponder(url,opts,envelope)});
      const link = app.$('#person-leaderboard-rank'), stats=app.$('#person-content').querySelector('.person-stats');
      assert.equal(link.textContent,expected);assert.equal(link.href,'#people');
      assert.equal(link.querySelector('.person-rank-number').textContent,String(rank));assert.equal(link.querySelector('sup').textContent,expected.slice(String(rank).length));
      assert.equal(stats.children[0].querySelector('dt').textContent,'Place on leaderboard');
      assert.ok(stats.children[1].classList.contains('person-points-stat'));assert.equal(stats.children[2].querySelector('dt').textContent,'Accepted proposals');
      assert.equal(app.$('#person-workspace').querySelector('.profile-title-row').querySelector('a'),null);assert.equal(app.$('#person-workspace').querySelector('.scheme-back'),null);
      link.click();await flush();assert.equal(app.location.hash,'#people');assert.equal(app.$('#people-workspace').hidden,false);
    }
  });
  await test('rank refresh preserves the focused stat link and unavailable places show a dash', async () => {
    let data = {...person('alpha'),rank:5};
    const app = await createApp({schemes,hash:'#person/alpha',responder:(url,opts,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:data,scoring}):baseResponder(url,opts,envelope)});
    const link=()=>app.$('#person-leaderboard-rank');link().focus();data={...data,rank:11};await app.sandbox.loadPerson('alpha',{preserve:true});
    assert.equal(link().textContent,'11th');assert.equal(app.document.activeElement,link());
    const stable=link();await app.sandbox.loadPerson('alpha',{preserve:true});assert.equal(link(),stable);
    for(const rank of [null,0,-1,1.5,'5']) {data={...data,rank};await app.sandbox.loadPerson('alpha',{preserve:true});assert.equal(link().textContent,'—');assert.equal(link().querySelector('sup'),null);}
    data={...data,rank:5,points:null};await app.sandbox.loadPerson('alpha',{preserve:true});assert.equal(link().textContent,'—');
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
  await test('reviewer profiles show their role without points or rank and retain research records', async () => {
    const reviewer = {...person('reviewer'), score_status:'reviewer', accepted_proposal_count:3,
      records:[{...record, scoring_eligible:false, score:null}], first_records:[{...record, place:1}]};
    // Role status is authoritative even if an old score projection remains in
    // an intermediate response. It must never render as an active competitor.
    const app = await createApp({schemes, hash:'#person/reviewer', responder:(url,opts,envelope) =>
      url.pathname === '/api/people/reviewer' ? envelope({person:reviewer,scoring}) : baseResponder(url,opts,envelope)});
    const content = app.$('#person-content'), stats = content.querySelector('.person-stats');
    assert.deepEqual(stats.querySelectorAll('dt').map(element => element.textContent), ['Role','Accepted proposals']);
    assert.deepEqual(stats.querySelectorAll('dd').map(element => element.textContent), ['Reviewer','3']);
    assert.equal(content.querySelector('#person-leaderboard-rank'), null);
    assert.equal(content.querySelector('.person-points-stat'), null);
    assert.equal(content.querySelector('.person-score-breakdown'), null);
    assert.equal(content.querySelector('.person-record-points'), null);
    assert.doesNotMatch(content.textContent, /Pending|Scoring is being finalized|Place on leaderboard|Points/);
    assert.equal(app.$('#personal-records').querySelector('.person-record-result').dataset.submissionId, record.submission_id);
    assert.equal(app.$('#first-records').querySelector('.milestone-result').dataset.submissionId, record.submission_id);
    assert.equal(app.$('#recent-solves').querySelector('.history-item').dataset.submissionId, record.submission_id);
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

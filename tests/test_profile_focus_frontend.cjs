const assert = require('node:assert/strict');
const {createApp} = require('./frontend-dom.cjs');

const user = {id: 'owner', handle: 'alpha'};
const record = (id, scheme = 'fixture') => ({scheme_id: scheme, scheme_name: scheme,
  scheme_version: '1', profile_id: 'stage-1', tier: 1, place: 1, submission_id: id,
  received_at: 100, is_current: true, scoring_eligible: true, score: {total: 10}});
const profile = () => ({handle: 'alpha', points: 20, accepted_proposal_count: 0,
  score_breakdown: {progress: 20}, records: [record('one'), record('two', 'other')],
  first_records: [record('one'), record('two', 'other')],
  recent_recoveries: [record('one'), record('two', 'other')]});
const schemes = ['fixture', 'other'].map(id => ({id, version: '1', name: id,
  profiles: [{id: 'stage-1', instance_size: 1}], next_profile_id: 'stage-1'}));
const deferred = () => {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};};

async function boot() {
  let data = profile(), pending = null;
  const app = await createApp({schemes, user, hash: '#person/alpha', responder: (url, options, envelope) => {
    if (url.pathname === '/api/people/alpha') return pending?.promise || envelope({person: data});
  }});
  return {app, change: update => {data = {...data, ...update};}, hold: () => (pending = deferred()),
    refresh: () => app.sandbox.loadPerson('alpha', {preserve: true})};
}

let passed = 0;
async function test(name, run) {await run(); passed++; console.log('PASS ' + name);}
(async () => {
  await test('changed points preserve focus and disclosure state for each profile summary', async () => {
    for (const selector of ['.person-score-breakdown', '#first-records', '#personal-records', '#recent-solves']) {
      const {app, change, refresh} = await boot();
      const section = app.$(selector); section.open = true;
      const old = section.querySelector('summary'); old.focus();
      change({points: 25}); await refresh();
      const next = app.$(selector);
      assert.ok(next !== section, 'Changed data really replaced the section');
      assert.equal(next.open, true);
      assert.ok(app.document.activeElement === next.querySelector('summary'), 'Focus follows the same summary');
    }
  });
  await test('receipt focus follows identity when records reorder in all three lists', async () => {
    for (const [selector, field] of [['#first-records', 'first_records'], ['#personal-records', 'records'], ['#recent-solves', 'recent_recoveries']]) {
      const {app, change, refresh} = await boot();
      const section = app.$(selector); section.open = true;
      section.querySelectorAll('[data-submission-id]')[1].focus();
      change({[field]: [record('two', 'other'), record('one')]}); await refresh();
      const buttons = app.$(selector).querySelectorAll('[data-submission-id]');
      assert.ok(app.document.activeElement === buttons[0], 'Focus follows the receipt, not its old row');
      assert.equal(app.document.activeElement.dataset.submissionId, 'two');
    }
  });
  await test('a removed receipt falls back to its own disclosure summary', async () => {
    const {app, change, refresh} = await boot();
    app.$('#personal-records').open = true;
    app.$('#personal-records').querySelector('[data-submission-id]').focus();
    change({records: [record('two', 'other')]}); await refresh();
    assert.ok(app.document.activeElement === app.$('#personal-records').querySelector('summary'));
    assert.equal(app.$('#personal-records').open, true);
  });
  await test('profile scheme links retain focus by destination after a reorder', async () => {
    const {app, change, refresh} = await boot();
    app.$('#personal-records').open = true;
    app.$('#personal-records').querySelectorAll('a')[1].focus();
    change({records: [record('two', 'other'), record('one')]}); await refresh();
    assert.ok(app.document.activeElement === app.$('#personal-records').querySelector('a'));
    assert.equal(app.document.activeElement.href, '#scheme/other/1');
  });
  await test('a removed disclosure falls back to the profile workspace', async () => {
    const {app, change, refresh} = await boot();
    app.$('.person-score-breakdown').querySelector('summary').focus();
    change({score_breakdown: null}); await refresh();
    assert.equal(app.document.querySelector('.person-score-breakdown'), null);
    assert.ok(app.document.activeElement === app.$('#person-workspace'));
  });
  await test('moving focus outside the profile during a read prevents focus restoration', async () => {
    const {app, hold, refresh} = await boot();
    app.$('#personal-records').querySelector('summary').focus();
    const pending = hold(), request = refresh();
    const outside = app.$('#history-button'); outside.focus();
    pending.resolve(app.envelope({person: {...profile(), points: 25}})); await request;
    assert.ok(app.document.activeElement === outside);
  });
  await test('a dialog opened during a read keeps its focus', async () => {
    const {app, hold, refresh} = await boot();
    app.$('#personal-records').querySelector('summary').focus();
    const pending = hold(), request = refresh();
    const dialog = app.$('#history-dialog'); dialog.showModal();
    const close = dialog.querySelector('[data-close]'); close.focus();
    pending.resolve(app.envelope({person: {...profile(), points: 25}})); await request;
    assert.equal(dialog.open, true); assert.ok(app.document.activeElement === close);
  });
  await test('unchanged data preserves the original DOM and focused control', async () => {
    const {app, refresh} = await boot();
    const section = app.$('#personal-records'), summary = section.querySelector('summary');
    section.open = true; summary.focus(); await refresh();
    assert.ok(app.$('#personal-records') === section);
    assert.ok(app.document.activeElement === summary);
  });
  console.log(`${passed} profile-focus regression checks passed.`);
})().catch(error => {console.error(error); process.exitCode = 1;});

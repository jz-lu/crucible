const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'bounty-navigation-user', handle: 'researcher'};

async function boot({hash = '#about', account = user} = {}) {
  const calls = [];
  let savedRoute = '#bounties';
  const notebook = {mount(root, options) {
    calls.push({kind: 'mount', options});
    return {
      async show(params) { calls.push({kind: 'show', params: new URLSearchParams(params)}); },
      hide() { calls.push({kind: 'hide'}); },
      setAccount() {}, invalidateReads() {}, async warm() { return {bytes: 0}; },
      bountyRoute: () => savedRoute
    };
  }};
  const app = await createApp({schemes: [], user: account, hash, notebook, bootstrap: true,
    responder: (url, options, envelope) => url.pathname === '/api/session'
      ? envelope({user: account, csrf_token: 'test-csrf', access: {mode: 'beta', allowed: Boolean(account)}}) : undefined});
  return {...app, calls, remember: route => { savedRoute = route; }};
}

let passed = 0;
async function test(name, run) { await run(); passed++; console.log('PASS ' + name); }
(async () => {
  await test('Bounties follows Schemes in main navigation and opens without a catalogue dependency', async () => {
    const app = await boot();
    assert.deepEqual(app.$('.primary-nav').querySelectorAll('button').map(node => node.id),
      ['nav-challenge', 'nav-bounties', 'nav-notebook', 'nav-about', 'nav-research']);
    app.$('#nav-bounties').click(); await flush();
    assert.equal(app.location.hash, '#bounties');
    assert.equal(app.document.title, 'Bounties — Crucible');
    assert.equal(app.$('#nav-bounties').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#nav-notebook').getAttribute('aria-current'), null);
    assert.equal(app.$('#notebook-workspace').hidden, false);
    assert.equal(app.$('#notebook-workspace').getAttribute('aria-label'), 'Bounties');
    assert.equal(app.calls.at(-1).params.get('view'), 'bounties');
    assert.equal(app.requests.some(r => r.url.pathname === '/api/schemes'), false);
  });
  await test('old task and composer links retain their selections on the new destination', async () => {
    for (const query of [
      'view=bounties&status=mine&page=2&bounty=task-one&detail=contributions',
      'view=guide&compose=bounty', 'bounty=task-two', 'compose=bounty'
    ]) {
      const app = await boot({hash: '#notebook?' + query});
      const original = new URLSearchParams(query); original.delete('view');
      assert.equal(app.location.hash, '#bounties?' + original);
      const shown = app.calls.filter(call => call.kind === 'show').at(-1).params;
      for (const [name, value] of original) assert.equal(shown.get(name), value);
      assert.equal(shown.get('view'), 'bounties');
      assert.equal(app.$('#nav-bounties').getAttribute('aria-current'), 'page');
    }
  });
  await test('main tabs and browser Back retain independent notebook and bounty destinations', async () => {
    const app = await boot({hash: '#bounties?status=mine'});
    app.remember('#bounties?status=mine');
    app.$('#nav-notebook').click(); await flush();
    assert.equal(app.location.hash, '#notebook');
    assert.equal(app.$('#nav-notebook').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#notebook-workspace').getAttribute('aria-label'), 'Research notebook');
    app.history.back(); await flush();
    assert.equal(app.location.hash, '#bounties?status=mine');
    assert.equal(app.$('#nav-bounties').getAttribute('aria-current'), 'page');
    app.$('#nav-about').click(); await flush();
    app.$('#nav-bounties').click(); await flush();
    assert.equal(app.location.hash, '#bounties?status=mine');
    assert.equal(app.calls.filter(call => call.kind === 'mount').length, 1);
  });
  await test('bounty links do not rewrite a malformed exact passage citation', async () => {
    const hash = '#notebook?note=source&revision=1&sha256=' + 'a'.repeat(64) + '&location=L1&view=bounties';
    const app = await boot({hash});
    assert.equal(app.location.hash, hash);
    assert.equal(app.$('#nav-notebook').getAttribute('aria-current'), 'page');
    assert.equal(app.calls.filter(call => call.kind === 'show').at(-1).params.get('location'), 'L1');
  });
  await test('an explicit unavailable capability hides the main tab and retires old and new bounty links', async () => {
    const app = await boot();
    app.window.CrucibleFeatures = {bounties: false};
    app.$('html').setAttribute('data-bounties-enabled', 'false');
    for (const route of ['#bounties?bounty=private-task', '#notebook?view=bounties&compose=bounty']) {
      await app.navigate(route);
      assert.equal(app.location.hash, '#notebook');
      assert.equal(app.$('#nav-bounties').hidden, true);
      assert.equal(app.calls.filter(call => call.kind === 'show').at(-1).params.size, 0);
    }
    const preview = app.window.CruciblePendingPage.describe('#bounties');
    assert.equal(preview.title, 'Research notebook');
    assert.equal(preview.page, 'notebook-notes');
  });
  await test('signed-out bounty routes preserve the destination without mounting private content', async () => {
    const app = await boot({hash: '#bounties?status=mine', account: null});
    assert.equal(app.location.hash, '#bounties?status=mine');
    assert.equal(app.$('#access-workspace').hidden, false);
    assert.equal(app.$('#notebook-workspace').hidden, true);
    assert.equal(app.calls.length, 0);
    assert.equal(app.requests.some(r => r.url.pathname.startsWith('/api/bounties')), false);
  });
  console.log(passed + ' main bounty navigation checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
async function boot(hash = '#about') {
  const calls = [];
  const notebook = {mount() { return {
    async show(params) { calls.push(new URLSearchParams(params)); },
    hide() {}, setAccount() {}, invalidateReads() {}, async warm() { return {bytes: 0}; }
  }; }};
  return {...await createApp({hash, schemes: [], user: {id: 'viewer', handle: 'reader'}, notebook, bootstrap: true}), calls};
}
let passed = 0;
async function test(name, run) { await run(); passed++; console.log('PASS ' + name); }
(async () => {
  await test('the Scheme map heading action selects Schemes while Notes keeps its own main tab', async () => {
    const app = await boot('#challenge');
    assert.equal(app.$('#catalogue-title').textContent, 'Schemes');
    assert.equal(app.$('#nav-notebook').textContent.trim(), 'Notes');
    app.$('#propose-scheme-button').click(); await flush();
    assert.equal(app.location.hash, '#propose');
    assert.equal(app.$('#proposal-workspace').hidden, false);
    assert.equal(app.document.title, 'Propose a scheme — Crucible');
    app.$('#nav-challenge').click(); await flush();
    app.$('#scheme-map-button').click(); await flush();
    assert.equal(app.location.hash, '#challenge?view=map');
    assert.equal(app.document.title, 'Scheme map — Crucible');
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'), 'page');
    assert.equal(app.$('#nav-notebook').getAttribute('aria-current'), null);
    assert.equal(app.calls.at(-1).get('view'), 'map');
    app.$('#nav-challenge').click(); await flush();
    assert.equal(app.location.hash, '#challenge'); assert.equal(app.$('#catalogue-workspace').hidden, false);
    app.history.back(); await flush(); assert.equal(app.calls.at(-1).get('view'), 'map');
    app.$('#nav-notebook').click(); await flush();
    assert.equal(app.document.title, 'Notes — Crucible'); assert.equal(app.calls.at(-1).size, 0);
  });
  await test('old map deep links retain their selection and exact passage links keep their validation query', async () => {
    const app = await boot('#notebook?view=map&selection=fixture');
    assert.equal(app.location.hash, '#challenge?view=map&selection=fixture');
    assert.equal(app.calls.at(-1).get('selection'), 'fixture');
    const pin = '#notebook?note=source&revision=1&sha256=' + 'a'.repeat(64) + '&location=L1&view=map';
    await app.navigate(pin);
    assert.equal(app.location.hash, pin); assert.equal(app.calls.at(-1).get('location'), 'L1');
    assert.equal(app.$('#nav-notebook').getAttribute('aria-current'), 'page');
  });
  await test('a map deep link does not wait for the full scheme catalogue', async () => {
    const app = await boot('#challenge?view=map');
    assert.equal(app.$('#notebook-workspace').hidden, false);
    assert.equal(app.calls.at(-1).get('view'), 'map');
    assert.equal(app.requests.some(({url}) => url.pathname === '/api/schemes' && url.searchParams.get('projection') !== 'map'), false);
  });
  await test('explicit main tabs scroll to the top even on repeat clicks, while browser history retains its own scroll', async () => {
    const app = await boot('#challenge'), scrolls = [];
    app.window.scrollTo = options => scrolls.push(JSON.parse(JSON.stringify(options)));
    for (const selector of ['#nav-notebook', '#nav-notebook', '#nav-bounties', '#nav-about', '#nav-challenge']) {
      const before = scrolls.length; app.$(selector).click(); await flush();
      assert.equal(scrolls.length, before + 1);
      assert.deepEqual(scrolls.at(-1), {top: 0, left: 0, behavior: 'instant'});
    }
    const before = scrolls.length; app.history.back(); await flush();
    assert.equal(scrolls.length, before);
    app.$('.brand-wordmark').dispatchEvent({type: 'click', ctrlKey: true, button: 0}); await flush();
    assert.equal(scrolls.length, before, 'Modified home links keep their native behavior');
    app.$('.brand-wordmark').click(); await flush(); assert.equal(scrolls.length, before + 1);
  });
  console.log(passed + ' visualization navigation checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

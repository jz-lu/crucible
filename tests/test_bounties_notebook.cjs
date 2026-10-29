const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'alice-id', handle: 'alice'};
const deferred = () => {let resolve; const promise = new Promise(yes => resolve = yes); return {promise, resolve};};
async function boot({prepare, reviewOnly = false} = {}) {
  const app = await createApp({schemes: [], hash: '#about'}), calls = [];
  let account = user, credential = 'one', preparationCount = 0, routeTask = Promise.resolve();
  for (const file of ['store.js', 'seed.js', 'notebook.js']) vm.runInContext(fs.readFileSync('web/notebook/' + file, 'utf8'), app.sandbox, {filename: file});
  app.sandbox.window.CrucibleBounties = {mount(root, options) {
    calls.push({kind: 'mount', root, options});
    return {show: async params => calls.push({kind: 'show', params: new URLSearchParams(params)}), hide: () => calls.push({kind: 'hide'}), destroy: () => {calls.push({kind: 'destroy'}); root.replaceChildren();}, invalidateReads: () => calls.push({kind: 'invalidate'}), warm: async () => {calls.push({kind: 'warm'}); return {bytes: 42};}, route: () => reviewOnly ? '#reviews?section=bounties' : '#notebook?view=bounties'};
  }};
  const root = app.$(reviewOnly ? '#review-notebook-workspace' : '#notebook-workspace');
  const controller = app.sandbox.window.CrucibleNotebook.mount(root, {
    api: async path => {
      if (path.startsWith('/api/notebook/notes?')) return {notes: [], next_after: null};
      throw Error('Unexpected read: ' + path);
    }, account: () => account, session: () => credential, canWarm: () => true, reviewOnly,
    prepareBounties: async () => {preparationCount++; await prepare?.();},
    navigate: route => {app.history.pushState(null, '', route); return routeTask = controller.show(new URLSearchParams(route.split('?')[1]));}
  });
  return {...app, root, calls, controller, q: name => root.querySelector('[data-nb="' + name + '"]'), prepared: () => preparationCount,
    routeTask: () => routeTask, rotate() {credential = 'two'; controller.setAccount(account);}, revoke() {account = null; controller.setAccount(null);}};
}
let passed = 0;
async function test(name, work) {await work(); passed++; console.log('PASS ' + name);}
(async () => {
  await test('bounty intent warms one modular controller without showing or replacing the notebook', async () => {
    const app = await boot(); await app.controller.show();
    assert.equal(app.prepared(), 0);
    app.q('bounties-view').dispatchEvent({type: 'pointerenter'}); await flush();
    assert.equal(app.prepared(), 1); assert.equal(app.q('bounties').hidden, true);
    assert.deepEqual(app.calls.map(c => c.kind), ['mount', 'warm']);
    assert.equal(app.q('library').hidden, false);
    app.q('bounties-view').click(); await app.routeTask();
    assert.equal(app.q('library').hidden, true); assert.equal(app.q('bounties').hidden, false);
    assert.equal(app.calls.at(-1).params.get('view'), 'bounties');
    assert.equal(app.root.querySelector('.nb-visualization-tabs').textContent, 'Scheme mapBountiesTimeline');
  });
  await test('pending bounty assets cannot mount after account or credential retirement', async () => {
    for (const change of ['rotate', 'revoke']) {
      const held = deferred(), app = await boot({prepare: () => held.promise});
      const showing = app.controller.show(new URLSearchParams('view=bounties'));
      await flush(); app[change](); held.resolve(); await showing;
      assert.equal(app.calls.some(c => c.kind === 'mount'), false);
      assert.equal(app.q('bounties').textContent, '');
    }
  });
  await test('leaving during asset setup may prepare a hidden controller but never show a stale route', async () => {
    const held = deferred(), app = await boot({prepare: () => held.promise});
    const showing = app.controller.show(new URLSearchParams('view=bounties'));
    await flush(); await app.controller.show(); held.resolve(); await showing;
    assert.equal(app.calls.filter(c => c.kind === 'mount').length, 1);
    assert.equal(app.calls.some(c => c.kind === 'show'), false);
    assert.equal(app.q('library').hidden, false); assert.equal(app.q('bounties').hidden, true);
    await app.controller.show(new URLSearchParams('view=bounties'));
    assert.equal(app.calls.filter(c => c.kind === 'show').length, 1);
  });
  await test('review bounty routes keep the dedicated reviewer workspace and session predicate', async () => {
    const app = await boot({reviewOnly: true});
    await app.controller.show(new URLSearchParams('section=bounties'));
    const mount = app.calls.find(c => c.kind === 'mount');
    assert.equal(mount.options.reviewOnly, true); assert.equal(mount.options.current(), true);
    assert.equal(app.q('library').hidden, true); assert.equal(app.q('bounties').hidden, false);
    assert.equal(app.calls.find(c => c.kind === 'show').params.get('review'), null);
    app.rotate(); assert.equal(mount.options.current(), false);
    assert.equal(app.calls.at(-1).kind, 'destroy');
  });
  console.log(passed + ' bounty notebook integration checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

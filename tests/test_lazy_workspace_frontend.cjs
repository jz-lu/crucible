const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'fixture-user', handle: 'alice'};
const scheme = {id: 'fixture', version: '1', name: 'Fixture scheme', next_profile_id: 'stage-1',
  profiles: [{id: 'stage-1', instance_size: 1, shared_secret_bytes: 32, parameters: {}, encoding: {shared_secret_bytes: 32}, correctness: {}}],
  size_parameter: {meaning: 'Fixture'}, protocol: {}, profile_metrics: {'stage-1': {public_key_bytes: 10}}};
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return {promise, resolve, reject}; };
const paths = app => app.requests.map(request => request.url.pathname);
const read = (app, source) => vm.runInContext(source, app.sandbox);
const active = (envelope, account = user) => envelope({user: account, csrf_token: 'csrf', access: {mode: 'beta', status: 'active', allowed: true}});
function notebookDouble() {
  const calls = {mount: 0, show: 0};
  return {calls, mount(target) {
    calls.mount++; const marker = target.ownerDocument.createElement('div'); marker.id = 'notebook-fixture'; target.replaceChildren(marker);
    return {show: async () => { calls.show++; }, hide() {}, setAccount() {}};
  }};
}
function assets(app) {
  return [...app.document.querySelectorAll('link'), ...app.document.querySelectorAll('script')]
    .filter(element => (element.src || element.href || '').startsWith('/notebook/'));
}
async function timedNotebook() {
  const app = await createApp({schemes:[scheme], hash:'#about', responder:(url, options, envelope) => {
    if (url.pathname === '/api/session') return active(envelope);
  }});
  const timers = new Map(); let sequence=0;
  app.sandbox.setTimeout = (callback, delay) => {const id=++sequence;timers.set(id,{callback,delay});return id;};
  app.sandbox.clearTimeout = id => timers.delete(id);
  await app.navigate('#notebook');
  return {...app,timers};
}
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('independent routes authenticate before loading only their own data', async () => {
    for (const hash of ['#about', '#people', '#notebook']) {
      const notebook = notebookDouble();
      const app = await createApp({schemes: [scheme], hash, notebook, responder: (url, options, envelope) => {
        if (url.pathname === '/api/session') return active(envelope);
        if (url.pathname === '/api/people') return envelope({people: []});
      }});
      assert.equal(paths(app)[0], '/api/session');
      assert.equal(paths(app).includes('/api/schemes'), false, hash);
      assert.equal(paths(app).includes('/api/challenges'), false, hash);
      assert.equal(read(app, 'state.ready && state.protectedLoaded'), true);
      if (hash === '#notebook') assert.equal(notebook.calls.show, 1);
    }
  });
  await test('catalogue is fetched on demand once and a late response does not reload an independent page', async () => {
    const gate = deferred(), notebook = notebookDouble();
    const app = await createApp({schemes: [scheme], hash: '#about', notebook, responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return gate.promise;
    }});
    await app.navigate('#challenge');
    await app.navigate('#scheme/fixture/1');
    assert.equal(app.requests.filter(request => request.url.searchParams.get('projection') === 'navigation').length, 1);
    await app.navigate('#notebook');
    assert.equal(notebook.calls.show, 1);
    gate.resolve(app.envelope({schemes: [scheme]})); await flush();
    assert.equal(notebook.calls.show, 1);
    assert.equal(app.$('#notebook-workspace').hidden, false);
    await app.navigate('#challenge');
    assert.match(app.$('#kem-catalogue').textContent, /Fixture scheme/);
    assert.equal(app.requests.filter(request => request.url.searchParams.get('projection') === 'navigation').length, 1);
  });
  await test('direct map route mounts without downloading the full profile catalogue', async () => {
    const app=await createApp({schemes:[scheme],hash:'#notebook?view=map',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session')return active(envelope);
      if(url.pathname==='/api/schemes')throw new Error('Map graph must not wait for profile metadata');
    }});
    assert.equal(assets(app).length,4,'Assets start without waiting for navigation metadata');
    assert.equal(app.$('#notebook-workspace').hidden,false,'The authorized map has its own data');
    const notebook=notebookDouble();app.window.CrucibleNotebook=notebook;
    for(const asset of assets(app))asset.onload();await flush();
    assert.equal(notebook.calls.mount,1);assert.equal(notebook.calls.show,1);
    assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
    assert.deepEqual(app.requests.filter(request=>request.url.pathname==='/api/schemes').map(request=>request.url.searchParams.get('projection')),['map']);
  });
  await test('ordinary session refresh cannot strand the initial catalogue read', async () => {
    const gate = deferred();
    const app = await createApp({schemes: [scheme], responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return gate.promise;
    }});
    const refresh = app.sandbox.synchronizeSession(); await flush();
    gate.resolve(app.envelope({schemes: [scheme]})); await refresh; await flush();
    assert.equal(app.requests.filter(request => request.url.searchParams.get('projection') === 'navigation').length, 1);
    assert.match(app.$('#kem-catalogue').textContent, /Fixture scheme/);
  });
  await test('a late catalogue failure cannot replace an independent page and a later navigation can retry', async () => {
    const gate = deferred(); let reads = 0;
    const app = await createApp({schemes: [scheme], responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes' && reads++ === 0) return gate.promise;
    }});
    await app.navigate('#about');
    gate.reject(new Error('Synthetic catalogue failure')); await flush();
    assert.equal(app.$('#about-workspace').hidden, false);
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    await app.navigate('#challenge');
    assert.equal(reads, 2);
    assert.match(app.$('#kem-catalogue').textContent, /Fixture scheme/);
  });
  await test('revocation discards a lazy catalogue and clears protected independent pages', async () => {
    const gate = deferred(), notebook = notebookDouble();
    const app = await createApp({schemes: [scheme], hash: '#notebook', notebook, responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return gate.promise;
    }});
    let reloads = 0; app.window.location.reload = () => reloads++;
    await app.navigate('#challenge');
    app.sandbox.setSession(app.envelope({user, csrf_token: 'csrf', access: {mode: 'beta', status: 'revoked', allowed: false}}));
    gate.resolve(app.envelope({schemes: [scheme]})); await flush();
    assert.equal(reloads, 1);
    assert.equal(read(app, 'state.catalogueLoaded'), false);
    assert.equal(read(app, 'state.schemes.length'), 0);
    assert.equal(app.$('#access-workspace').hidden, false);
  });
  await test('an account change starts a fresh catalogue instead of joining the old account read', async () => {
    const gates = [deferred(), deferred()]; let reads = 0;
    const app = await createApp({schemes: [scheme], responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return gates[reads++].promise;
    }});
    app.sandbox.setSession(active(app.envelope, {id: 'other', handle: 'bob'}));
    const newLoad = app.sandbox.loadAuthorizedWorkspace(); await flush();
    assert.equal(reads, 2);
    gates[1].resolve(app.envelope({schemes: [{...scheme, name: 'Current catalogue'}]})); await newLoad;
    gates[0].resolve(app.envelope({schemes: [{...scheme, name: 'Old catalogue'}]})); await flush();
    assert.match(app.$('#kem-catalogue').textContent, /Current catalogue/);
    assert.doesNotMatch(app.$('#kem-catalogue').textContent, /Old catalogue/);
  });
  await test('all notebook assets start independently, mounting waits for readiness, and re-entry keeps the DOM', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#notebook'});
    const requested = assets(app);
    assert.deepEqual(requested.map(element => element.src || element.href),
      ['/notebook/notebook.css', '/notebook/seed.js', '/notebook/store.js', '/notebook/notebook.js']);
    assert.ok(requested.slice(1).every(script => script.async === true));
    const notebook = notebookDouble(); app.window.CrucibleNotebook = notebook;
    requested[3].onload(); requested[1].onload(); requested[2].onload(); await flush();
    assert.equal(notebook.calls.mount, 0, 'stylesheet still pending');
    requested[0].onload(); await flush();
    const marker = app.$('#notebook-fixture');
    await app.navigate('#about'); await app.navigate('#notebook');
    assert.equal(notebook.calls.mount, 1);
    assert.equal(notebook.calls.show, 2);
    assert.equal(app.$('#notebook-fixture'), marker);
    assert.equal(assets(app).length, 4);
    assert.equal(paths(app).includes('/api/schemes'), false);
  });
  await test('actual notebook scripts initialize correctly in every arrival order', async () => {
    const permutations = values => values.length ? values.flatMap((value,index) => permutations(values.filter((_,i)=>i!==index)).map(rest=>[value,...rest])) : [[]];
    for (const order of permutations(['seed.js','store.js','notebook.js'])) {
      const app = await createApp({schemes:[scheme], user, hash:'#about'});
      for (const file of order) vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/notebook',file),'utf8'),app.sandbox,{filename:file});
      assert.equal(typeof app.window.CrucibleNotebook.mount,'function');
      assert.equal(app.window.TheoryStore.validateSnapshot(app.window.CrucibleTheorySeed).format,'crucible-theory-local-v1');
      const root=app.$('#notebook-workspace');
      const controller=app.window.CrucibleNotebook.mount(root,{account:()=>user,navigate:()=>{},api:async url=>{
        assert.ok(url.startsWith('/api/notebook/notes?'));return {notes:[],next_after:null};
      }});
      await controller.show();assert.match(root.textContent,/No notes/);
    }
  });
  await test('same-account session rotation starts a fresh read and ignores the previous session response', async () => {
    const gates = [deferred(), deferred()]; let reads = 0;
    const app = await createApp({schemes: [scheme], responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return gates[reads++].promise;
    }});
    app.sandbox.setSession({...active(app.envelope), csrf_token: 'rotated-csrf'});
    const newLoad = app.sandbox.loadAuthorizedWorkspace(); await flush();
    assert.equal(reads, 2);
    gates[1].resolve(app.envelope({schemes: [{...scheme, name: 'Current session'}]})); await newLoad;
    gates[0].resolve(app.envelope({schemes: [{...scheme, name: 'Expired session'}]})); await flush();
    assert.match(app.$('#kem-catalogue').textContent, /Current session/);
    assert.doesNotMatch(app.$('#kem-catalogue').textContent, /Expired session/);
  });
  await test('failed assets retry without duplicate successful downloads or a mount before completion', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#notebook'});
    const requested = assets(app), notebook = notebookDouble(); app.window.CrucibleNotebook = notebook;
    requested[0].onerror(); requested.slice(1).forEach(script => script.onload()); await flush();
    assert.equal(notebook.calls.mount, 0);
    assert.match(app.$('#notebook-workspace').textContent, /could not be loaded/);
    const retry = app.sandbox.showNotebook(''); await flush();
    const retried = assets(app);
    assert.equal(retried.length, 4);
    assert.ok(requested.slice(1).every(script => retried.includes(script)));
    retried.find(element => element.tagName === 'LINK').onload(); await retry;
    assert.equal(notebook.calls.mount, 1);
  });
  for (const stalledIndex of [0,3]) await test((stalledIndex ? 'script' : 'stylesheet') + ' deadline permits a bounded retry and ignores the old attempt callbacks', async () => {
    const app=await timedNotebook(), requested=assets(app), stalled=requested[stalledIndex];
    assert.equal(app.timers.size,4);assert.ok([...app.timers.values()].every(timer=>timer.delay===15000));
    const lateLoad=stalled.onload,lateError=stalled.onerror;
    const notebook=notebookDouble();app.window.CrucibleNotebook=notebook;
    requested.forEach((element,index)=>{if(index!==stalledIndex)element.onload();});
    assert.equal(app.timers.size,1);const deadline=[...app.timers.values()][0].callback;
    deadline();await flush();
    assert.equal(app.timers.size,0);assert.equal(stalled.isConnected,false);
    assert.equal(stalled.onload,null);assert.equal(stalled.onerror,null);
    assert.match(app.$('#notebook-workspace').textContent,/took too long/);assert.equal(notebook.calls.mount,0);
    const retry=app.sandbox.showNotebook('');await flush();
    const replacement=assets(app).find(element=>!requested.includes(element));
    assert.ok(replacement);
    const retryURL=new URL(replacement.src||replacement.href,'http://127.0.0.1:8000');
    assert.equal(retryURL.pathname,stalled.src||stalled.href);assert.equal(retryURL.search,'?asset_attempt=2');
    assert.equal(assets(app).length,4);assert.equal(app.timers.size,1);
    const path=JSON.stringify(stalled.src||stalled.href),pending=read(app,'notebookAssetLoads.get('+path+')');
    lateLoad();lateError();deadline();await flush();
    assert.equal(notebook.calls.mount,0,'late load cannot complete the new attempt');
    assert.equal(read(app,'notebookAssetLoads.get('+path+')'),pending,'late error cannot evict the new task');
    assert.equal(app.timers.size,1,'late deadline cannot clear the new timer');
    replacement.onload();await retry;
    assert.equal(notebook.calls.mount,1);assert.equal(app.timers.size,0);
  });
  await test('revocation during a stalled asset prevents retries or late callbacks from mounting private notebook content', async () => {
    const app=await timedNotebook(),requested=assets(app),lateLoads=requested.map(element=>element.onload);
    const notebook=notebookDouble();app.window.CrucibleNotebook=notebook;
    app.sandbox.setSession(app.envelope({user,csrf_token:'csrf',access:{mode:'beta',status:'revoked',allowed:false}}));
    [...app.timers.values()].forEach(timer=>timer.callback());await flush();
    lateLoads.forEach(callback=>callback());await app.sandbox.showNotebook('');
    assert.equal(app.timers.size,0);assert.equal(assets(app).length,0);
    assert.equal(notebook.calls.mount,0);assert.equal(app.$('#access-workspace').hidden,false);
    assert.deepEqual(paths(app),['/api/session','/api/notebook/notes'], 'Only the already-authorized initial read ran; revocation starts no further note request');
  });
  await test('leaving during asset loading cannot mount a hidden notebook', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#notebook'});
    const notebook = notebookDouble(); app.window.CrucibleNotebook = notebook;
    await app.navigate('#challenge');
    assets(app).forEach(element => element.onload()); await flush();
    assert.equal(notebook.calls.mount, 0);
    await app.navigate('#notebook');
    assert.equal(notebook.calls.mount, 1);
  });
  await test('revocation during notebook downloads prevents mounting or starting further note reads', async () => {
    const app = await createApp({schemes: [scheme], hash: '#notebook', responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
    }});
    const notebook = notebookDouble(); app.window.CrucibleNotebook = notebook;
    app.sandbox.setSession(app.envelope({user, csrf_token: 'csrf', access: {mode: 'beta', status: 'revoked', allowed: false}}));
    assets(app).forEach(element => element.onload()); await flush();
    assert.equal(notebook.calls.mount, 0);
    assert.equal(app.$('#access-workspace').hidden, false);
    assert.deepEqual(paths(app), ['/api/session', '/api/notebook/notes'], 'No new request follows revocation');
  });
  await test('the notebook map never blocks its renderer on full scheme metadata', async () => {
    const notebook = notebookDouble();
    const app = await createApp({schemes: [scheme], user, hash: '#notebook?view=map', notebook});
    assert.equal(app.requests.filter(request => request.url.searchParams.get('projection') === 'navigation').length, 0);
    assert.equal(read(app, 'state.schemes.length'), 0);
    assert.equal(notebook.calls.show, 1);
  });
  await test('scheme leaderboard and protocol navigation skip challenge reads; challenge view still reads once', async () => {
    const app = await createApp({schemes: [scheme], user, hash: '#challenge/fixture/1?profile=stage-1&view=leaderboard'});
    assert.equal(paths(app).includes('/api/challenges'), false);
    assert.equal(paths(app).filter(path => path === '/api/leaderboard').length, 1);
    await app.navigate('#challenge/fixture/1?profile=stage-1&view=protocol');
    assert.equal(paths(app).includes('/api/challenges'), false);
    await app.navigate('#challenge/fixture/1?profile=stage-1&view=challenge');
    assert.equal(paths(app).filter(path => path === '/api/challenges').length, 1);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  console.log(`\n${passed} lazy workspace checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

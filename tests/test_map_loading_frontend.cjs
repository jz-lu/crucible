const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const harnessPath = path.join(__dirname, 'frontend-dom.cjs');
const helper = {module:{exports:{}}, require:createRequire(harnessPath), __dirname, console, URL, URLSearchParams, AbortController, Blob, TextEncoder, queueMicrotask, setImmediate, setTimeout, clearTimeout, setInterval};
vm.runInNewContext(fs.readFileSync(harnessPath, 'utf8') + '\nmodule.exports.parseHTML = parseHTML;', helper);
const {createApp, parseHTML, flush} = helper.module.exports;
const source = fs.readFileSync(path.join(__dirname, '../web/notebook/notebook.js'), 'utf8');
const template = '<html><head><script src="should-not-load.js"></script></head><body><header>Not imported</header><main><p class="intro">Removed intro</p><div id="landscape"></div><a href="notes.html">Notes</a></main><dialog></dialog></body></html>';
const scripts = ['catalogue.js', 'note-ui.js', 'map-views.js', 'app.js'];
const globals = ['CRUCIBLE_LANDSCAPE', 'NoteUI', 'LandscapeViews', 'CrucibleResearchMap'];
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};};

async function boot() {
  const app = await createApp({schemes:[], hash:'#about'});
  const createElement = app.document.createElement;
  app.document.createElement = tag => {
    const node = createElement(tag);
    node.attachShadow = () => {node.shadowRoot=createElement('shadow-root');return node.shadowRoot;};
    return node;
  };
  app.document.importNode = node => node;
  app.sandbox.DOMParser = class {parseFromString(text) {return parseHTML(text);}};
  app.window.location.origin = 'http://127.0.0.1:8000';
  const timers = new Map(); let timerId=0;
  app.sandbox.setTimeout = fn => {const id=++timerId;timers.set(id,fn);return id;};
  app.sandbox.clearTimeout = id => timers.delete(id);
  const reads = [], calls = {mount:0, show:0, hide:0, destroy:0};
  app.sandbox.fetch = (url,options) => {assert.equal(url,'/map/index.html');const wait=deferred();reads.push({url,options,...wait});return wait.promise;};
  vm.runInContext(source, app.sandbox, {filename:'notebook.js'});
  let account={id:'map-owner'}, session='first-session';
  const root=app.$('#notebook-workspace'), q=name=>root.querySelector(`[data-nb="${name}"]`);
  const releases=[{id:'dh',version:'current'}];
  const controller=app.window.CrucibleNotebook.mount(root, {
    account:()=>account, session:()=>session, currentReleases:()=>releases, navigate:()=>{},
    api:async url=>{assert.ok(url.startsWith('/api/notebook/notes?'));return {notes:[],next_after:null};}
  });
  const renderer={mount(shadow,options) {
    assert.ok(globals.every(name=>app.window[name]),'every script completed before mount');
    assert.ok(shadow.querySelector('main'));assert.ok(shadow.querySelector('dialog'));
    assert.equal(shadow.querySelector('script'),null);assert.equal(shadow.querySelector('header'),null);
    assert.equal(shadow.querySelector('.intro'),null);
    assert.equal(options.currentReleases(),releases);calls.mount++;calls.options=options;
    return {show:()=>calls.show++,hide:()=>calls.hide++,destroy:()=>calls.destroy++};
  }};
  const pendingScripts=()=>app.document.querySelector('head').querySelectorAll('script').filter(node=>node.src?.startsWith('/map/'));
  const finishScript=name=>{const script=pendingScripts().find(node=>new URL(node.src,'http://127.0.0.1:8000').pathname==='/map/'+name);assert.ok(script?.onload);app.window[globals[scripts.indexOf(name)]]=name==='app.js'?renderer:{};script.onload();};
  const finishScripts=()=>scripts.forEach(name=>{if(!app.window[globals[scripts.indexOf(name)]])finishScript(name);});
  const respond=(index=reads.length-1,ok=true)=>reads[index].resolve({ok,text:async()=>template});
  const surface=()=>q('map').children.find(node=>node.shadowRoot);
  const stylesheet=()=>surface().shadowRoot.querySelector('link');
  return {...app,controller,q,reads,calls,timers,pendingScripts,finishScript,finishScripts,respond,surface,stylesheet,
    show:()=>controller.show(new URLSearchParams('view=map')),
    setAccount:value=>{account=value;controller.setAccount(value);},
    rotate:()=>{session='rotated-session';controller.setAccount(account);}};
}

let passed=0;
async function test(name,fn){await fn();console.log('PASS '+name);passed++;}
(async()=>{
  await test('all six graph assets start before any result and no unstyled map mounts',async()=>{
    const app=await boot(),loading=app.show();
    assert.deepEqual(Array.from(app.pendingScripts(),script=>script.src),scripts.map(name=>'/map/'+name));
    assert.ok(app.pendingScripts().every(script=>script.async===true),'independent execution cannot be held behind a removed timed-out script');
    assert.equal(app.reads.length,1);assert.equal(app.reads[0].options.credentials,'same-origin');
    assert.equal(app.reads[0].options.cache,'no-store');assert.ok(app.reads[0].options.signal);
    assert.equal(app.stylesheet().href,'/map/styles.css');assert.equal(app.surface().isConnected,true);
    assert.equal(app.surface().hidden,true);assert.match(app.q('map').textContent,/Loading scheme map/);
    const quiet=app.q('map').querySelector('.nb-reading');
    assert.equal(quiet.getAttribute('aria-busy'),'true');
    assert.ok(quiet.querySelector('[role="status"]').classList.contains('nb-sr-only'));
    const surface=app.surface();let parent=surface.parentElement,detachments=0;
    Object.defineProperty(surface,'parentElement',{get:()=>parent,set:value=>{if(parent&&!value)detachments++;parent=value;}});
    app.respond();app.finishScripts();await flush();assert.equal(app.calls.mount,0,'stylesheet still pending');
    app.stylesheet().onload();await loading;
    assert.equal(app.calls.mount,1);assert.equal(app.calls.show,1);assert.equal(app.surface().hidden,false);
    assert.equal(app.surface(),surface);assert.equal(detachments,0,'keep the loaded shadow stylesheet continuously connected');
    assert.doesNotMatch(app.q('map').textContent,/Loading scheme map/);assert.equal(app.timers.size,0);
    assert.equal(app.q('map').querySelector('.nb-reading'),null);
  });
  await test('note bodies load on demand, retry independently and reject a changed owner',async()=>{
    const app=await boot(),loading=app.show();app.respond();app.finishScripts();app.stylesheet().onload();await loading;
    assert.equal(app.window.CRUCIBLE_NOTES,undefined);
    const first=app.calls.options.loadNotes();const rejected=assert.rejects(first,/asset unavailable/);
    let script=app.pendingScripts().find(node=>node.src==='/map/notes-data.js');assert.ok(script);
    script.onerror();await rejected;assert.equal(app.calls.mount,1,'Body failure does not tear down the graph');
    const retry=app.calls.options.loadNotes();script=app.pendingScripts().find(node=>node.src==='/map/notes-data.js?asset_attempt=2');assert.ok(script);
    app.rotate();app.window.CRUCIBLE_NOTES={notes:[]};script.onload();await assert.rejects(retry,/no longer active/);
  });
  await test('HTML and CSS completing first cannot mount before the last independent script',async()=>{
    const app=await boot(),loading=app.show();app.respond();app.stylesheet().onload();
    scripts.slice(0,-1).forEach(app.finishScript);await flush();assert.equal(app.calls.mount,0);
    app.finishScript('app.js');await loading;assert.equal(app.calls.mount,1);
  });
  await test('actual map scripts initialize in reverse order without consuming missing globals',async()=>{
    const app=await boot();app.document.getElementById=()=>null;
    for(const name of [...scripts].reverse())vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/map',name),'utf8'),app.sandbox,{filename:name});
    assert.ok(globals.every(name=>app.window[name]));assert.equal(typeof app.window.CrucibleResearchMap.mount,'function');
    assert.equal(app.calls.mount,0,'the readiness barrier owns mounting');
  });
  await test('failed script waits for the other downloads and retries only the missing dependency',async()=>{
    const app=await boot(),loading=app.show();const failed=app.pendingScripts()[1];
    failed.onerror();app.respond();app.stylesheet().onload();await flush();
    assert.doesNotMatch(app.q('map').textContent,/could not be loaded/,'do not start an overlapping retry');
    scripts.filter(name=>name!=='note-ui.js').forEach(app.finishScript);await loading;
    assert.match(app.q('map').textContent,/could not be loaded/);assert.equal(app.surface(),undefined);
    const successful=app.pendingScripts();const retry=app.show();
    assert.equal(app.pendingScripts().length,4);assert.ok(successful.every(script=>app.pendingScripts().includes(script)));
    assert.equal(app.pendingScripts().find(script=>!successful.includes(script)).src,'/map/note-ui.js?asset_attempt=2');
    app.finishScript('note-ui.js');app.respond();app.stylesheet().onload();await retry;
    assert.equal(app.calls.mount,1);assert.equal(app.timers.size,0);
  });
  await test('stylesheet failure leaves no surface and retries without downloading successful scripts',async()=>{
    const app=await boot(),loading=app.show();app.stylesheet().onerror();app.respond();app.finishScripts();await loading;
    assert.equal(app.surface(),undefined);assert.match(app.q('map').textContent,/could not be loaded/);
    const successful=app.pendingScripts(),retry=app.show();
    assert.equal(app.stylesheet().href,'/map/styles.css?asset_attempt=2');
    app.respond();app.stylesheet().onload();await retry;
    assert.deepEqual(app.pendingScripts(),successful);assert.equal(app.calls.mount,1);
  });
  await test('denied template never mounts previously loaded data and a retry makes a fresh private read',async()=>{
    const app=await boot(),loading=app.show();app.respond(0,false);app.finishScripts();app.stylesheet().onload();await loading;
    assert.equal(app.calls.mount,0);assert.equal(app.surface(),undefined);
    const retry=app.show();assert.equal(app.reads.length,2);app.respond();app.stylesheet().onload();await retry;
    assert.equal(app.calls.mount,1);
  });
  await test('a malformed successful template is discarded so retry performs a fresh read',async()=>{
    const app=await boot(),loading=app.show();
    app.reads[0].resolve({ok:true,text:async()=>'<html><body>Unexpected response</body></html>'});
    app.finishScripts();app.stylesheet().onload();await loading;
    assert.equal(app.calls.mount,0);assert.equal(app.surface(),undefined);
    const retry=app.show();assert.equal(app.reads.length,2);
    app.respond();app.stylesheet().onload();await retry;assert.equal(app.calls.mount,1);
  });
  await test('pending preparation survives switching notebook views without showing a hidden map',async()=>{
    const app=await boot(),old=app.show(),oldSurface=app.surface(),style=app.stylesheet();
    await app.controller.show();
    assert.equal(app.q('map').hidden,true);
    const latest=app.show();
    assert.equal(app.surface(),oldSurface);assert.equal(app.reads.length,1);
    app.respond();style.onload();app.finishScripts();await Promise.all([old,latest]);
    assert.equal(app.calls.mount,1);assert.equal(app.surface(),oldSurface);
  });
  for(const change of ['sign-out','session rotation'])await test(change+' during loading prevents a delayed map from appearing',async()=>{
    const app=await boot(),loading=app.show(),style=app.stylesheet();
    if(change==='sign-out')app.setAccount(null);else app.rotate();
    app.respond();app.finishScripts();style.onload();await loading;
    assert.equal(app.calls.mount,0);assert.equal(app.q('map').textContent,'');assert.equal(app.surface(),undefined);
  });
  await test('asset deadlines fail closed and settled timers are released',async()=>{
    const app=await boot(),loading=app.show();
    const scriptDeadline=[...app.timers.values()].find(callback=>String(callback).includes('Map asset unavailable.'));assert.ok(scriptDeadline);scriptDeadline();
    app.respond();app.stylesheet().onload();scripts.slice(1).forEach(app.finishScript);await loading;
    assert.equal(app.calls.mount,0);assert.match(app.q('map').textContent,/could not be loaded/);assert.equal(app.timers.size,0);
  });
  console.log(`Passed ${passed} parallel map loading checks.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const harnessPath = path.join(__dirname, 'frontend-dom.cjs');
const helper = {module:{exports:{}}, require:createRequire(harnessPath), __dirname, console};
vm.runInNewContext(fs.readFileSync(harnessPath, 'utf8') + '\nmodule.exports.parseHTML = parseHTML;', helper);
const document = helper.module.exports.parseHTML(fs.readFileSync(path.join(__dirname, '../web/map/index.html'), 'utf8'));
const listeners = new Map();
const scrolls=[];let reducedMotion=false,mapHeight=400;
document.querySelector('#landscape').getBoundingClientRect=()=>({top:500,height:mapHeight});
const sandbox = {innerHeight:800,scrollY:0,scrollTo:options=>scrolls.push(options),matchMedia:()=>({matches:reducedMotion}),document, URL, URLSearchParams, console, setTimeout, clearTimeout,
  location:{replace:() => {throw Error('Embedded map must not navigate the page');}},
  addEventListener:(name, fn) => listeners.set(name, fn),
  removeEventListener:(name, fn) => {if(listeners.get(name) === fn)listeners.delete(name);}};
sandbox.window = sandbox; document.defaultView = sandbox;
document.getElementById = () => null; // The document shell has no map; this root stands in for its shadow tree.
document.removeEventListener = (name, fn) => document.listeners.set(name, (document.listeners.get(name)||[]).filter(item => item !== fn));
vm.createContext(sandbox);
for (const file of ['catalogue.js', 'notes-data.js', 'note-ui.js', 'map-views.js', 'app.js'])
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/map', file), 'utf8'), sandbox, {filename:file});
const controller = sandbox.CrucibleResearchMap.mount(document, {currentReleases:() => [{id:'ml-kem', version:'registered-current'}]});
const $ = selector => document.querySelector(selector);
controller.show();
assert.equal($('#map-count').textContent, '13 KEMs');
assert.equal(document.querySelectorAll('button[data-view]').length, 0);
assert.ok($('.network-svg'));
$('[data-filter="scored"]').click();
assert.equal($('#map-count').textContent, '8 KEMs');
assert.equal(scrolls.at(-1).top,300);assert.equal(scrolls.at(-1).behavior,'smooth');
mapHeight=1000;reducedMotion=true;$('[data-filter="highlighted"]').click();
assert.equal(scrolls.at(-1).top,476);assert.equal(scrolls.at(-1).behavior,'instant');
$('[data-scheme="ml-kem"]').click();
assert.equal($('#mobile-details').open, true);
assert.deepEqual(Array.from($('#mobile-inspector').querySelector('.detail-actions').querySelectorAll('a'), n => n.textContent),
  ['Open challenge', 'Read research note', 'View parameters']);
assert.equal($('#mobile-inspector').querySelector('.challenge-action').href, '#challenge/ml-kem/registered-current?view=challenge');
assert.equal($('#mobile-inspector').querySelector('.detail-actions').querySelectorAll('a').at(-1).href, '#scheme/ml-kem/registered-current');
assert.equal($('#mobile-inspector').querySelectorAll('details').length, 0);
assert.equal($('#mobile-inspector').querySelectorAll('.detail-section').length, 0);
Array.from($('#mobile-inspector').querySelectorAll('a')).find(link => link.textContent === 'Read research note').click();
assert.equal($('#mobile-detail-title').textContent, 'Research note');
assert.match($('#mobile-inspector').textContent, /Conditions & scope/);
assert.ok($('#mobile-inspector').querySelector('.note-sources'));
assert.equal($('#mobile-details').open, true);
controller.hide(); controller.show();
assert.equal($('#map-count').textContent, '8 KEMs', 'Collection survives hiding the map');
$('[data-filter="all"]').click();
$('#search').value = 'Saber'; $('#search').dispatchEvent({type:'input'});
assert.equal($('#map-count').textContent, '1 KEM');
$('[data-scheme="saber"]').click();
assert.deepEqual(Array.from($('#mobile-inspector').querySelector('.detail-actions').querySelectorAll('a'), n => n.textContent), ['Read research note']);
controller.hide(); controller.show();
assert.equal($('#search').value, 'Saber');
assert.equal($('#map-count').textContent, '1 KEM');
controller.destroy();
assert.equal(listeners.size, 0);
console.log('PASS network-only map, compact popup, inline research note, retained filters, viewport centering/reduced motion and lifecycle cleanup');

(async () => {
  let releases=[], linkRead, linkCalls=0;
  const linked = sandbox.CrucibleResearchMap.mount(document, {currentReleases:()=>releases,
    loadReleases:()=>{linkCalls++;return new Promise((resolve,reject)=>{linkRead={resolve,reject};});}});
  linked.show();$('[data-scheme="ml-kem"]').click();await new Promise(setImmediate);
  const readLink=$('#mobile-inspector').querySelector('a');readLink.focus();
  releases=[{id:'ml-kem',version:'current-live'}];linkRead.resolve();await new Promise(setImmediate);
  assert.equal($('#mobile-inspector').querySelector('.challenge-action').href,'#challenge/ml-kem/current-live?view=challenge');
  assert.equal(document.activeElement,readLink,'Updating links keeps the focused research-note link');
  releases=[];$('[data-scheme="ml-kem"]').click();await new Promise(setImmediate);
  linkRead.reject(new Error('Unavailable'));await new Promise(setImmediate);
  const retry=$('.map-links-retry'),before=linkCalls;retry.click();retry.click();await new Promise(setImmediate);
  assert.equal(linkCalls,before+1,'Repeated retry clicks share one read');
  Array.from($('#mobile-inspector').querySelectorAll('a')).find(link=>link.textContent==='Read research note').click();
  releases=[{id:'ml-kem',version:'late-live'}];linkRead.resolve();await new Promise(setImmediate);
  assert.equal($('#mobile-detail-title').textContent,'Research note');
  assert.equal($('#mobile-inspector').querySelector('.challenge-action'),null,'Late metadata cannot replace an opened note');
  linked.destroy();
  console.log('PASS small map-link reads preserve focus, retry once, and cannot replace a later popup');
  const bundle = sandbox.CRUCIBLE_NOTES; vm.runInContext('window.CRUCIBLE_NOTES = null', sandbox);
  let pending, reads=0;
  const lazy = sandbox.CrucibleResearchMap.mount(document, {loadNotes:()=>{reads++;return new Promise((resolve,reject)=>{pending={resolve,reject};});}});
  lazy.show();assert.equal($('#map-count').textContent,'13 KEMs');assert.equal(reads,0,'Map layout does not request research bodies');
  const svg=$('.network-svg');lazy.hide();lazy.show();assert.equal($('.network-svg'),svg,'Warm return retains the existing SVG');
  const open=()=>{$('[data-scheme="ml-kem"]').click();Array.from($('#mobile-inspector').querySelectorAll('a')).find(link=>link.textContent==='Read research note').click();};
  open();await new Promise(setImmediate);assert.equal(reads,1);assert.match($('#mobile-inspector').textContent,/Loading research note/);
  pending.reject(new Error('Unavailable'));await new Promise(setImmediate);
  assert.match($('#mobile-inspector').textContent,/could not be loaded/);
  $('#mobile-inspector').querySelector('button').click();await new Promise(setImmediate);assert.equal(reads,2);
  pending.resolve(bundle);await new Promise(setImmediate);assert.match($('#mobile-inspector').textContent,/Conditions & scope/);
  lazy.hide();lazy.show();open();assert.equal(reads,2,'One successful note bundle is reused within this owner');lazy.destroy();

  for (const change of ['hide','destroy','different popup','close']) {
    let complete;
    const stale=sandbox.CrucibleResearchMap.mount(document,{loadNotes:()=>new Promise(resolve=>{complete=resolve;})});
    stale.show();open();await new Promise(setImmediate);
    if(change==='different popup')$('[data-scheme="saber"]').click();
    else if(change==='close')$('#mobile-details').close();else stale[change]();
    complete(bundle);await new Promise(setImmediate);
    if(change==='different popup')assert.equal($('#mobile-detail-title').textContent,'Scheme');
    else assert.equal($('#mobile-details').open,false);
    assert.doesNotMatch($('#mobile-inspector').textContent,/Conditions & scope/,'A late body cannot reopen or replace a different context');stale.destroy();
  }
  console.log('PASS lazy note-body loading, failed-read retry, retained SVG and late response ownership');
})().catch(error=>{console.error(error);process.exitCode=1;});

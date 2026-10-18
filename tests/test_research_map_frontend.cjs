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

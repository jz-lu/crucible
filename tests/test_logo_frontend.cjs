const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/bootstrap.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture({readyState = 'complete', complete = true, width = 189, decode} = {}) {
  const classes = new Set(), listeners = new Map(), readyWhenGated = [];
  const image = {classList: {contains: name => name === 'brand-mark'}, complete, naturalWidth: width,
    currentSrc: '/assets/crucible-logo.png?v=fixture', dataset: {}, decode};
  const root = {classList: {contains: name => classes.has(name), add: name => {
    readyWhenGated.push(image.dataset.logoReady); classes.add(name);
  }}};
  const document = {readyState, querySelector: () => root, querySelectorAll: () => [image],
    addEventListener(type, callback) {listeners.set(type, callback);}};
  const context = {document, window: {CrucibleSessionBootstrap: {consumed: true}}};
  vm.createContext(context); vm.runInContext(source, context);
  return {image, classes, listeners, context, readyWhenGated};
}
(async () => {
  let resolve;
  const fresh = fixture({complete: false, decode: () => new Promise(done => {resolve = done;})});
  assert(fresh.classes.has('decode-brand-marks'));
  assert.equal(fresh.image.dataset.logoReady, undefined);
  fresh.image.complete = true; fresh.listeners.get('load')({target: fresh.image});
  assert.equal(fresh.image.dataset.logoReady, undefined, 'load waits for the complete decode');
  resolve(); await flush(); assert.equal(fresh.image.dataset.logoReady, 'true');

  let calls = 0;
  const cached = fixture({decode: () => {calls++; return Promise.resolve();}});
  assert.equal(cached.image.dataset.logoReady, 'true');
  assert.deepEqual(cached.readyWhenGated, ['true'], 'already visible logo is ready before the hiding class exists');
  await flush(); assert.equal(cached.image.dataset.logoReady, 'true');
  vm.runInContext(source, cached.context); assert.equal(calls, 1, 'duplicate bootstrap does not restart image work');

  const parsing = fixture({readyState: 'loading', complete: false, decode: () => Promise.resolve()});
  assert.equal(parsing.image.dataset.logoReady, undefined);
  parsing.image.complete = true;
  parsing.listeners.get('DOMContentLoaded')(); await flush();
  assert.equal(parsing.image.dataset.logoReady, 'true', 'cached image missed by load capture is found after parsing');

  const rejected = fixture({complete: false, decode: () => Promise.reject(new Error('Synthetic browser decode rejection'))});
  rejected.image.complete = true; rejected.listeners.get('load')({target: rejected.image});
  await flush(); assert.equal(rejected.image.dataset.logoReady, 'true', 'valid completed load is the decode fallback');
  const legacy = fixture({complete: false});
  legacy.image.complete = true; legacy.listeners.get('load')({target: legacy.image});
  assert.equal(legacy.image.dataset.logoReady, 'true');
  const broken = fixture({width: 0}); assert.equal(broken.image.dataset.logoReady, undefined);

  let staleDone;
  const stale = fixture({complete: false, decode: () => new Promise(done => {staleDone = done;})});
  stale.image.complete = true; stale.listeners.get('load')({target: stale.image});
  stale.image.currentSrc = '/assets/crucible-logo.png?v=replacement';
  staleDone(); await flush(); assert.equal(stale.image.dataset.logoReady, undefined);
  console.log('PASS complete/cached/late/rejected/unsupported/failed/stale logo decode paths');
})().catch(error => {console.error(error); process.exitCode = 1;});

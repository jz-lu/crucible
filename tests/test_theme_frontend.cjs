const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/bootstrap.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../web/styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
function node() {
  const attributes = new Map(), listeners = new Map();
  return {hidden: true, getAttribute: name => attributes.get(name),
    setAttribute: (name, value) => attributes.set(name, value),
    addEventListener(type, callback) { (listeners.get(type) || listeners.set(type, []).get(type)).push(callback); },
    emit(type, event = {}) { for (const callback of listeners.get(type) || []) callback(event); }};
}
function fixture({saved, dark = false, blocked = false, parsed = true, noMedia = false} = {}) {
  const root = node(), button = node(), label = node(), meta = node(), writes = [], stored = new Map();
  if (saved !== undefined) stored.set('crucible.theme', saved);
  const media = {...node(), matches: dark};
  const document = {...node(), documentElement: root, readyState: parsed ? 'complete' : 'loading',
    querySelector(selector) { return selector === '#theme-toggle' ? (parsed ? button : null) : selector === '#theme-toggle-label' ? (parsed ? label : null) : selector === 'meta[name="theme-color"]' ? meta : null; }};
  const window = {...node(), CruciblePendingPage: {}, CrucibleSessionBootstrap: {},
    matchMedia: noMedia ? undefined : () => media,
    localStorage: {
      getItem(key) { if (blocked) throw Error('Blocked'); return stored.get(key) ?? null; },
      setItem(key, value) { if (blocked) throw Error('Blocked'); writes.push([key, value]); stored.set(key, value); }
    }};
  const context = vm.createContext({window, document});
  const evaluate = () => vm.runInContext(source, context);
  evaluate();
  return {root, button, label, meta, window, document, media, writes, evaluate,
    parse() { parsed = true; document.readyState = 'interactive'; document.emit('readystatechange'); }};
}
let passed = 0;
function test(name, run) { run(); console.log('PASS ' + name); passed++; }
test('saved appearance wins over the system and the toggle persists an explicit choice', () => {
  for (const saved of [undefined,'dark','light','bogus']) for (const dark of [true,false]) {
    const f=fixture({saved,dark,parsed:false}), selected=['dark','light'].includes(saved)?saved:dark?'dark':'light';
    assert.equal(f.root.getAttribute('data-theme'),selected);f.parse();
    f.button.emit('click');const chosen=selected==='dark'?'light':'dark';
    assert.equal(f.root.getAttribute('data-theme'),chosen);assert.deepEqual(f.writes,[['crucible.theme',chosen]]);
    f.media.matches=!dark;f.media.emit('change');assert.equal(f.root.getAttribute('data-theme'),chosen);
    f.window.emit('storage',{key:'crucible.theme',newValue:'dark'});assert.equal(f.root.getAttribute('data-theme'),'dark');
    f.evaluate();f.button.emit('click');assert.equal(f.writes.length,2);
  }
});
test('denied storage and absent matchMedia keep the appearance control usable', () => {
  const f=fixture({blocked:true,noMedia:true});assert.equal(f.root.getAttribute('data-theme'),'light');
  f.button.emit('click');assert.equal(f.root.getAttribute('data-theme'),'dark');
  f.button.emit('click');assert.equal(f.root.getAttribute('data-theme'),'light');
});
test('the shell exposes a light and dark appearance control without partner branding', () => {
  assert.match(html,/name="color-scheme" content="light dark"/);assert.match(html,/id="theme-toggle"/);
  assert.doesNotMatch(html,/resi-logo|resi\.org|>RESI</);assert.match(css,/:root\[data-theme=dark\]/);
});
function tokens(selector) {
  const rule = css.slice(css.indexOf(selector) + selector.length).split('}')[0];
  return Object.fromEntries([...rule.matchAll(/--([a-z-]+):\s*(#[a-f0-9]+);/g)].map(m => [m[1], m[2]]));
}
function luminance(hex) {
  let h = hex.slice(1); if (h.length === 3) h = [...h].map(c => c+c).join('');
  return [0,2,4].map(i => parseInt(h.slice(i,i+2),16)/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4)
    .reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i], 0);
}
function ratio(a,b) { const x=luminance(a),y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); }
test('the light palette keeps text and semantic badge contrast at WCAG AA', () => {
  const light=tokens(':root {'),dark={...light,...tokens(':root[data-theme=dark] {')};
  for (const [name,p] of [['light',light],['dark',dark]]) {
    for (const fg of ['ink','text','muted','primary']) for (const bg of ['paper','surface']) {
      assert.ok(ratio(p[fg],p[bg]) >= 4.5, `${name} ${fg}/${bg} ${ratio(p[fg],p[bg])}`);
    }
    for (const fg of ['success','error','violet','info','amber']) {
      assert.ok(ratio(p[fg],p[fg+'-bg']) >= 4.5, `${name} ${fg} badge`);
    }
    assert.ok(ratio(p['success-selected'],p['success-on-selected']) >= 4.5, `${name} selected level`);
    assert.ok(ratio(p['points-ink'],p['points-bg']) >= 4.5, `${name} points badge`);
    assert.ok(ratio(p['recovery-red'],p.surface) >= 3, `${name} recovery graph line`);
  }
});
test('shadow map inherits the light palette and standalone map stays light', () => {
  const map=fs.readFileSync(path.join(__dirname,'../web/map/styles.css'),'utf8');
  assert.match(map, /:root,:host\{--white:var\(--surface\);--focus:var\(--error\)/);
  assert.doesNotMatch(map, /:host\{[^}]*--(?:paper|ink|text):#/);
  assert.doesNotMatch(map, /prefers-color-scheme: dark|:root\[data-theme=dark\]/);
  assert.match(map, /\.network-region\{fill:var\(--region-violet\)/);
  assert.match(css, /\.chart-recovery-line\s*\{\s*stroke: var\(--recovery-red\)/);
});
console.log(`${passed} theme checks passed`);

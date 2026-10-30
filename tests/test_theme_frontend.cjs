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
test('stored light overrides a dark OS before the body exists', () => {
  const f = fixture({saved: 'light', dark: true, parsed: false});
  assert.equal(f.root.getAttribute('data-theme'), 'light');
  assert.equal(f.meta.getAttribute('content'), '#ebede6');
  assert.deepEqual(f.writes, []);
  f.parse(); assert.equal(f.button.hidden, false);
  assert.equal(f.button.getAttribute('aria-label'), 'Switch to dark theme');
  assert.equal(f.label.textContent, 'Dark mode');
});
test('stored dark overrides a light OS immediately', () => {
  const f = fixture({saved: 'dark'});
  assert.equal(f.root.getAttribute('data-theme'), 'dark');
  assert.equal(f.meta.getAttribute('content'), '#171a18');
  assert.equal(f.button.getAttribute('aria-label'), 'Switch to light theme');
  assert.equal(f.label.textContent, 'Light mode');
});
test('unset or invalid preferences follow OS changes without persisting them', () => {
  for (const saved of [undefined, 'bogus', '<script>']) {
    const f = fixture({saved, dark: true});
    assert.equal(f.root.getAttribute('data-theme'), 'dark');
    f.media.matches = false; f.media.emit('change');
    assert.equal(f.root.getAttribute('data-theme'), 'light');
    assert.deepEqual(f.writes, []);
  }
});
test('toggle persists only a nonsensitive explicit theme and then ignores OS changes', () => {
  const f = fixture(); f.button.emit('click');
  assert.equal(f.root.getAttribute('data-theme'), 'dark');
  assert.equal(f.label.textContent, 'Light mode');
  assert.deepEqual(f.writes, [['crucible.theme', 'dark']]);
  f.media.emit('change'); assert.equal(f.root.getAttribute('data-theme'), 'dark');
  f.button.emit('click'); assert.equal(f.root.getAttribute('data-theme'), 'light');
  assert.equal(f.label.textContent, 'Dark mode');
  assert.equal(f.button.getAttribute('data-next-theme'), 'dark');
});
test('blocked storage and absent matchMedia do not break paint or toggling', () => {
  const f = fixture({blocked: true, noMedia: true});
  assert.equal(f.root.getAttribute('data-theme'), 'light');
  f.button.emit('click'); assert.equal(f.root.getAttribute('data-theme'), 'dark');
});
test('cross-tab preference changes update theme; removal returns to OS default', () => {
  const f = fixture({dark: true, saved: 'light'});
  f.window.emit('storage', {key: 'unrelated', newValue: 'dark'});
  assert.equal(f.root.getAttribute('data-theme'), 'light');
  f.window.emit('storage', {key: 'crucible.theme', newValue: 'dark'});
  assert.equal(f.root.getAttribute('data-theme'), 'dark');
  f.window.emit('storage', {key: 'crucible.theme', newValue: null});
  f.media.matches = false; f.media.emit('change');
  assert.equal(f.root.getAttribute('data-theme'), 'light');
});
test('repeated bootstrap and readiness events never double-bind the toggle', () => {
  const f = fixture({parsed: false}); f.parse(); f.parse(); f.evaluate();
  f.button.emit('click');
  assert.equal(f.root.getAttribute('data-theme'), 'dark');
  assert.equal(f.writes.length, 1);
});
test('native button has an accessible name, keyboard focus treatment and no layout shift', () => {
  assert.match(html, /<button id="theme-toggle"[^>]*type="button"[^>]*aria-label="[^"]+"[^>]*hidden>/);
  assert.match(css, /\.theme-toggle\s*\{[^}]*position:fixed;[^}]*width:128px;[^}]*height:44px;/);
  assert.match(css, /\.theme-toggle:focus-visible\s*\{[^}]*outline:2px/);
  assert.match(html, /name="color-scheme" content="light dark"/);
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
test('both palettes keep text and semantic badge contrast at WCAG AA', () => {
  const light=tokens(':root {'), dark={...light,...tokens(':root[data-theme=dark] {')};
  for (const [name,p] of [['light',light],['dark',dark]]) {
    for (const fg of ['ink','text','muted','primary']) for (const bg of ['paper','surface']) {
      assert.ok(ratio(p[fg],p[bg]) >= 4.5, `${name} ${fg}/${bg} ${ratio(p[fg],p[bg])}`);
    }
    for (const fg of ['success','error','violet','info','amber']) {
      assert.ok(ratio(p[fg],p[fg+'-bg']) >= 4.5, `${name} ${fg} badge`);
    }
    assert.ok(ratio(p['success-selected'],p['success-on-selected']) >= 4.5, `${name} selected stage`);
    assert.ok(ratio(p['recovery-red'],p.surface) >= 3, `${name} recovery graph line`);
  }
});
test('shadow map inherits theme, while standalone map still has a dark OS fallback', () => {
  const map=fs.readFileSync(path.join(__dirname,'../web/map/styles.css'),'utf8');
  assert.match(map, /:root,:host\{--white:var\(--surface\);--focus:var\(--error\)/);
  assert.doesNotMatch(map, /:host\{[^}]*--(?:paper|ink|text):#/);
  assert.match(map, /prefers-color-scheme: dark/);
  assert.match(map, /\.network-region\{fill:var\(--region-violet\)/);
  assert.match(css, /\.chart-recovery-line\s*\{\s*stroke: var\(--recovery-red\)/);
});
console.log(`${passed} theme checks passed`);

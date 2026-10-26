const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('./frontend-dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../web/bootstrap.js'), 'utf8');

function element(attributes = {}) {
  const attrs = new Map(Object.entries(attributes)), classes = new Set();
  return {
    textContent: '',
    set innerHTML(_) { throw new Error('Public route labels must use textContent'); },
    getAttribute: name => attrs.get(name) ?? null,
    setAttribute: (name, value) => attrs.set(name, String(value)),
    removeAttribute: name => attrs.delete(name),
    classList: {
      contains: name => classes.has(name), add: name => classes.add(name),
      toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }
    }
  };
}
function events() {
  const listeners = new Map();
  return {
    addEventListener(type, callback, options) {
      const current = listeners.get(type) || [];
      current.push({callback, once: options?.once}); listeners.set(type, current);
    },
    dispatch(type) {
      for (const item of [...(listeners.get(type) || [])]) {
        if (item.once) listeners.set(type, listeners.get(type).filter(other => other !== item));
        item.callback({type});
      }
    }
  };
}
function fixture({hash = '', parsed = true, appFirst = false, hint, storageBlocked = false, resolvedView} = {}) {
  const root = element(), nodes = new Map([['html', root]]), requests = [], timers = new Map();
  const document = {...events(), documentElement: root, readyState: parsed ? 'complete' : 'loading', body: null,
    querySelector: selector => nodes.get(selector) || null, querySelectorAll: () => []};
  const stored = new Map(hint === undefined ? [] : [['crucible.pending-presentation', hint]]);
  const window = {...events(), location: {hash}, sessionStorage: {
    getItem(key) { if (storageBlocked) throw new Error('Storage unavailable'); return stored.get(key) ?? null; },
    setItem(key, value) { if (storageBlocked) throw new Error('Storage unavailable'); stored.set(key, value); }
  }};
  if (appFirst) window.CrucibleSessionBootstrap = {consumed: true};
  function installBody() {
    document.body = element({'data-access-pending': 'true'});
    nodes.set('#access-workspace', element({'aria-busy': 'true'}));
    nodes.set('#access-title', element({'aria-hidden': 'true'}));
    for (const name of ['challenge', 'notebook', 'about']) nodes.set('#nav-' + name, element());
  }
  if (parsed) installBody();
  if (resolvedView) {
    document.body.removeAttribute('data-access-pending');
    document.body.setAttribute('data-access-view', resolvedView);
    nodes.get('#access-title').textContent = 'Already resolved';
  }
  const context = {window, document, URLSearchParams, AbortController,
    fetch(url, options) { requests.push({url, options}); return new Promise(() => {}); },
    setTimeout(callback, duration) { const key = timers.size + 1; timers.set(key, {callback, duration}); return key; },
    clearTimeout: key => timers.delete(key)};
  vm.createContext(context);
  const evaluate = () => vm.runInContext(source, context);
  evaluate();
  return {root, window, document, requests, nodes, installBody, evaluate, context, stored};
}
let passed = 0;
function test(name, run) { run(); console.log('PASS ' + name); passed++; }

test('DOM clones copy independent markup and children but never listeners or private controller state', () => {
  const document = parseHTML('<body><div id="source" class="frame" hidden data-pending-layout="notebook">Before <span class="label">Notes</span> after</div></body>');
  const original = document.querySelector('#source'), child = original.querySelector('.label');
  let calls = 0;
  original.addEventListener('click', () => calls++);
  child.addEventListener('click', () => calls++);
  original.privateController = {accountId: 'must-not-clone'};
  const shallow = original.cloneNode();
  assert.equal(shallow.childNodes.length, 0);
  assert.equal(shallow.id, 'source');
  assert.equal(shallow.hidden, true);
  assert.equal(shallow.parentElement, null);
  const clone = original.cloneNode(true), copyChild = clone.querySelector('.label');
  assert.equal(clone.ownerDocument, document);
  assert.equal(copyChild.ownerDocument, document);
  assert.equal(clone.parentElement, null);
  assert.equal(copyChild.parentElement, clone);
  assert.notEqual(copyChild, child);
  assert.equal(clone.textContent, 'Before Notes after');
  assert.equal(clone.getAttribute('data-pending-layout'), 'notebook');
  assert.equal(clone.privateController, undefined);
  clone.removeAttribute('id'); clone.removeAttribute('data-pending-layout'); clone.hidden = false;
  copyChild.textContent = 'Timeline';
  assert.equal(original.id, 'source');
  assert.equal(original.hidden, true);
  assert.equal(original.textContent, 'Before Notes after');
  clone.click(); copyChild.click();
  assert.equal(calls, 0);
  child.click();
  assert.equal(calls, 2, 'The original child still bubbles to its original parent');
});

test('DOM clones retain namespace and current form values without copying live object references', () => {
  const document = parseHTML('<body><input value="initial"><textarea>initial</textarea><option>Option</option></body>');
  const input = document.querySelector('input'), textarea = document.querySelector('textarea'), option = document.querySelector('option');
  input.value = 'edited'; input.checked = true;
  textarea.value = 'edited body'; option.selected = true;
  const clonedInput = input.cloneNode(true);
  assert.equal(clonedInput.value, 'edited');
  assert.equal(clonedInput.getAttribute('value'), 'initial');
  assert.equal(clonedInput.checked, true);
  assert.equal(textarea.cloneNode(true).value, 'edited body');
  assert.equal(option.cloneNode(true).selected, true);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 10 10');
  const clone = svg.cloneNode(true);
  assert.equal(clone.namespaceURI, svg.namespaceURI);
  assert.equal(clone.getAttribute('viewBox'), '0 0 10 10');
});

test('public route descriptions select bounded known layouts without querying account or catalogue data', () => {
  const f = fixture(), describe = f.window.CruciblePendingPage.describe;
  const examples = [
    ['', 'catalogue', 'KEM Schemes'], ['#', 'catalogue', 'KEM Schemes'],
    ['#challenge', 'catalogue', 'KEM Schemes'], ['#people', 'people', 'Leaderboard'],
    ['#notebook', 'notebook-notes', 'Research notebook'], ['#notebook?view=timeline', 'notebook-timeline', 'Research notebook'],
    ['#notebook?view=map', 'notebook-map', 'Research notebook'], ['#notebook?view=unknown', 'notebook-notes', 'Research notebook'],
    ['#notebook?filter=mine&tag=private-looking-label', 'notebook-notes', 'Research notebook'],
    ['#notebook?note=private-id', 'list', 'Research notebook'],
    ['#notebook?note=private-id&view=timeline', 'list', 'Research notebook'],
    ['#notebook?research=1', 'list', 'Research notebook'],
    ['#notebook?research_request=private-request&view=map', 'list', 'Research notebook'],
    ['#notebook?review=1', 'list', 'Review center'], ['#notebook?research_review=1', 'list', 'Review center'],
    ['#scheme/test/v1', 'chart', 'Scheme'], ['#scheme/test/v1/assumptions', 'list', 'Assumptions'],
    ['#challenge/test/v1', 'list', 'Challenge'], ['#reviews', 'list', 'Review center'],
    ['#propose?review=1', 'list', 'Review center'], ['#about', 'neutral', 'About us'],
    ['#propose', 'neutral', 'Propose a scheme'], ['#connect', 'neutral', 'Connect an agent'],
    ['#join?token=synthetic-never-rendered', 'neutral', 'Private beta'], ['#auth?github=failed', 'neutral', 'Sign in'],
    ['#reset-password?token=synthetic-never-rendered', 'neutral', 'Reset password'],
    ['#unknown', 'neutral', 'Crucible'], ['x'.repeat(4097), 'neutral', 'Crucible']
  ];
  for (const [hash, page, title] of examples) assert.deepEqual({...describe(hash)}, {page, title}, hash);
  assert.deepEqual({...describe({})}, {page: 'neutral', title: 'Crucible'});
  assert.deepEqual(f.requests.map(item => item.url), ['/api/session']);
  assert.equal(Object.isFrozen(f.window.CruciblePendingPage), true);
});

test('private note, request and review selections never become pre-access content or data requests', () => {
  for (const hash of [
    '#notebook?note=private-note-id&revision=17&sha256=private-hash',
    '#notebook?research_request=private-request-id&view=timeline',
    '#notebook?review=1&note=private-note-id',
    '#reviews?proposal=private-proposal-id',
    '#notebook?tag=private-tag&filter=mine'
  ]) {
    const f = fixture({hash, hint: 'workspace'});
    assert.doesNotMatch(f.nodes.get('#access-title').textContent, /private-|17/);
    assert.deepEqual(f.requests.map(item => item.url), ['/api/session']);
    assert.equal(f.document.body.getAttribute('data-access-pending'), 'true');
    assert.equal(f.nodes.get('#access-workspace').getAttribute('aria-busy'), 'true');
  }
});

test('profile labels decode a bounded public handle and never interpret markup or malformed paths', () => {
  const f = fixture({hash: '#person/%3Cimg%20src=x%3E'}), describe = f.window.CruciblePendingPage.describe;
  assert.equal(f.nodes.get('#access-title').textContent, '<img src=x>');
  for (const [encoded, title] of [['alice', 'alice'], ['Zo%C3%AB', 'Zoë'], ['a'.repeat(100), 'a'.repeat(100)],
    ['a'.repeat(101), 'Profile'], ['', 'Profile'], ['%E0%A4%A', 'Profile'], ['alice%2Fbob', 'Profile'],
    ['alice%00bob', 'Profile'], ['alice%0Abob', 'Profile'], ['alice%7Fbob', 'Profile']]) {
    assert.deepEqual({...describe('#person/' + encoded)}, {page: 'profile', title});
  }
  assert.equal(describe('#person/alice?token=do-not-display').title, 'alice');
});

test('head execution applies the route before body parsing and fills its heading before a held deferred app', () => {
  const f = fixture({hash: '#notebook?view=timeline', parsed: false});
  assert.equal(f.root.getAttribute('data-pending-page'), 'notebook-timeline');
  assert.equal(f.nodes.has('#access-title'), false);
  f.installBody();
  // A deferred app can hold DOMContentLoaded indefinitely; interactive is
  // enough for the public shell and does not authorize any private work.
  f.document.readyState = 'interactive';
  f.document.dispatch('readystatechange');
  assert.equal(f.nodes.get('#access-title').textContent, 'Research notebook');
  assert.equal(f.nodes.get('#access-title').getAttribute('aria-hidden'), null);
  assert.equal(f.nodes.get('#nav-notebook').getAttribute('aria-current'), 'page');
  assert.deepEqual(f.requests.map(item => item.url), ['/api/session']);
  assert.equal(f.requests[0].options.credentials, 'same-origin');
  assert.equal(f.requests[0].options.cache, 'no-store');
});

test('pending hash changes update only public geometry and navigation without additional requests', () => {
  const f = fixture({hash: '#notebook'});
  for (const [hash, page, title, selected] of [
    ['#notebook?view=timeline', 'notebook-timeline', 'Research notebook', 'notebook'],
    ['#notebook?view=map', 'notebook-map', 'Research notebook', 'notebook'],
    ['#notebook?view=unknown', 'notebook-notes', 'Research notebook', 'notebook'],
    ['#about', 'neutral', 'About us', 'about'], ['#scheme/test/v1', 'chart', 'Scheme', 'challenge'],
    ['#people', 'people', 'Leaderboard', null], ['#person/alice', 'profile', 'alice', null]
  ]) {
    f.window.location.hash = hash; f.window.dispatch('hashchange');
    assert.equal(f.root.getAttribute('data-pending-page'), page);
    assert.equal(f.nodes.get('#access-title').textContent, title);
    for (const tab of ['challenge', 'notebook', 'about']) {
      assert.equal(f.nodes.get('#nav-' + tab).getAttribute('aria-current'), selected === tab ? 'page' : null);
      assert.equal(f.nodes.get('#nav-' + tab).classList.contains('selected'), selected === tab);
    }
  }
  assert.equal(f.requests.length, 1);
});

test('late parser callbacks and hash changes never replace a resolved or failed access gate', () => {
  for (const gate of ['resolved', 'failed']) {
    const f = fixture({hash: '#notebook', parsed: false});
    f.installBody();
    if (gate === 'resolved') f.document.body.removeAttribute('data-access-pending');
    else f.nodes.get('#access-workspace').setAttribute('aria-busy', 'false');
    f.nodes.get('#access-title').textContent = gate === 'resolved' ? 'Private beta' : 'Could not check access';
    f.window.location.hash = '#people'; f.document.readyState = 'interactive';
    f.document.dispatch('readystatechange'); f.window.dispatch('hashchange');
    f.window.CruciblePendingPage.apply();
    assert.equal(f.nodes.get('#access-title').textContent, gate === 'resolved' ? 'Private beta' : 'Could not check access');
    assert.equal(f.root.getAttribute('data-pending-page'), 'notebook-notes');
    assert.equal(f.nodes.get('#nav-challenge').getAttribute('aria-current'), null);
    assert.equal(f.requests.length, 1);
  }
});

test('app-first and repeated bootstrap execution preserve one-shot session ownership', () => {
  const appFirst = fixture({hash: '#people', appFirst: true});
  assert.equal(appFirst.requests.length, 0);
  const f = fixture({hash: '#people'}), helper = f.window.CruciblePendingPage, boot = f.window.CrucibleSessionBootstrap;
  f.evaluate();
  assert.equal(f.requests.length, 1);
  assert.equal(f.window.CruciblePendingPage, helper);
  assert.equal(f.window.CrucibleSessionBootstrap, boot);
});

test('fresh or unavailable-storage root stays neutral while the hint changes only public presentation', () => {
  for (const options of [{}, {storageBlocked: true}, {hint: 'unrecognized'}]) {
    const f = fixture(options);
    assert.equal(f.root.getAttribute('data-pending-page'), 'neutral');
    assert.equal(f.nodes.get('#access-title').textContent, 'Crucible');
    assert.equal(f.requests.length, 1);
  }
  const gate = fixture({hash: '#notebook', hint: 'gate'});
  assert.equal(gate.root.getAttribute('data-pending-page'), 'gate');
  assert.equal(gate.nodes.get('#access-title').textContent, 'Private beta');
  assert.equal(gate.nodes.get('#nav-notebook').getAttribute('aria-current'), null);
  assert.deepEqual(gate.requests.map(item => item.url), ['/api/session']);
  const workspace = fixture({hint: 'workspace'});
  assert.equal(workspace.root.getAttribute('data-pending-page'), 'catalogue');
  assert.equal(workspace.nodes.get('#access-title').textContent, 'KEM Schemes');
  assert.deepEqual(workspace.requests.map(item => item.url), ['/api/session']);
});

test('remember stores only the two nonauthoritative labels and never replaces a resolved gate', () => {
  const f = fixture();
  f.window.CruciblePendingPage.remember(false);
  assert.deepEqual([...f.stored], [['crucible.pending-presentation', 'gate']]);
  f.window.CruciblePendingPage.remember({id: 'must-not-store'});
  assert.equal(f.stored.get('crucible.pending-presentation'), 'gate');
  f.document.body.removeAttribute('data-access-pending');
  f.nodes.get('#access-title').textContent = 'Private beta';
  f.window.CruciblePendingPage.remember(true);
  f.window.CruciblePendingPage.apply();
  assert.equal(f.nodes.get('#access-title').textContent, 'Private beta');
  assert.deepEqual([...f.stored], [['crucible.pending-presentation', 'workspace']]);
  assert.equal(f.requests.length, 1);
  const blocked = fixture({storageBlocked: true});
  assert.doesNotThrow(() => blocked.window.CruciblePendingPage.remember(true));
});

test('a late bootstrap records the app-first resolved presentation without altering its UI or session', () => {
  for (const view of ['workspace', 'gate', 'unexpected']) {
    const f = fixture({hash: '#notebook', appFirst: true, resolvedView: view});
    assert.equal(f.nodes.get('#access-title').textContent, 'Already resolved');
    assert.equal(f.root.getAttribute('data-pending-page'), null);
    assert.equal(f.nodes.get('#nav-notebook').getAttribute('aria-current'), null);
    assert.equal(f.requests.length, 0);
    assert.deepEqual([...f.stored], view === 'unexpected' ? [] : [['crucible.pending-presentation', view]]);
  }
});

test('unexecuted HTML starts neutral with inert decorative variants and no fabricated results', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  assert.match(html, /<h1 id="access-title" aria-hidden="true"><\/h1>/);
  assert.match(html, /<nav class="primary-nav"[^>]*\binert\b[^>]*\bhidden\b/);
  assert.doesNotMatch(html.slice(0, html.indexOf('<body')), /data-pending-page=/);
  const preview = html.slice(html.indexOf('<div id="access-loading-preview"'), html.indexOf('<div id="access-auth-actions"'));
  assert.deepEqual([...preview.matchAll(/data-pending-layout="([^"]+)"/g)].map(match => match[1]),
    ['neutral', 'gate', 'catalogue', 'people', 'profile', 'notebook', 'list', 'chart', 'timeline']);
  assert.equal((preview.match(/class="pending-page-preview[^>]*aria-hidden="true"/g) || []).length, 9);
  const document = parseHTML(html), frame = document.querySelector('#notebook-frame-template');
  assert.equal(frame.getAttribute('aria-hidden'), 'true');
  assert.notEqual(frame.getAttribute('inert'), null);
  assert.equal(frame.querySelectorAll('[id]').length, 0, 'Cloning the frame must not duplicate control IDs');
  for (const variant of document.querySelectorAll('[data-pending-layout]')) {
    assert.equal(variant.querySelectorAll('a,script,form,[href],[action],[name],[data-nb],[data-note],[data-user]').length, 0);
    const controls = variant.querySelectorAll('button,input,select,textarea');
    if (variant === frame) {
      assert.ok(controls.length > 0, 'The notebook frame mirrors the normal static controls');
      assert.ok(controls.every(control => control.disabled), 'Every pre-access control is disabled');
    } else assert.equal(controls.length, 0, 'Other decorative layouts remain noninteractive');
  }
  assert.deepEqual(frame.querySelectorAll('[data-notebook-tab]').map(node => node.getAttribute('data-notebook-tab')), ['notes', 'map', 'timeline']);
  assert.deepEqual(frame.querySelectorAll('[data-notebook-panel]').map(node => node.getAttribute('data-notebook-panel')), ['notes', 'map', 'timeline']);
  assert.equal(frame.querySelector('.nb-count').textContent, '');
  assert.equal(frame.querySelector('.nb-timeline-total').textContent, '');
  assert.equal(frame.querySelector('.nb-timeline-plot').querySelectorAll('svg').length, 0);
  assert.equal(frame.querySelector('.nb-list-placeholder').childNodes.length, 0);
  assert.equal(frame.querySelector('.nb-map-placeholder').childNodes.length, 0);
  assert.doesNotMatch(preview, /data-(?:note|user|token|account)(?:[\s=-])|\b(?:href|action|name)=/i);
  assert.equal((preview.match(/<table class="kem-table">/g) || []).length, 1);
  assert.doesNotMatch(preview.slice(0, preview.indexOf('data-pending-layout="catalogue"')), />KEM</);
});

console.log(`\n${passed} initial route checks passed.`);

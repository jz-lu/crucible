const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp} = require('./frontend-dom.cjs');

async function boot(initial = [{id:'frodokem', version:'current/2'}]) {
  const app = await createApp({schemes:(initial || []).map(release => ({...release, name:release.id,
    profiles:[{id:'first-profile', instance_size:1, parameter_bits:1}, {id:'next-profile', instance_size:2, parameter_bits:2}],
    next_profile_id:'next-profile'})), hash:'#about'});
  let releases = initial;
  const el = (tag, cls, text) => {
    const node = app.document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const schemes = ['frodokem', 'literature-only'].map(id => ({
    id, name:id, family:'plain-lwe', summary:'A research description',
    tags:[], assumptions:[], note_ref:{note_id:id, revision:1}
  }));
  app.window.CRUCIBLE_LANDSCAPE = {
    schemes, families:[{id:'plain-lwe', name:'Plain LWE', domain:'lattices'}],
    domains:[{id:'lattices', name:'Lattices'}], assumptions:[], highlights:[], relationships:[],
    scoring_snapshot:{releases:[{scheme:'frodokem', version:'obsolete/1'}, {scheme:'literature-only', version:'unregistered'}]}
  };
  app.window.CRUCIBLE_NOTES = {notes:[]};
  app.window.NoteUI = {el, body:content => el('p', '', content), tag:content => el('span', '', content), noteHref:(id, revision) => 'notes.html#note/' + id + '/' + revision};
  app.window.LandscapeViews = {render:(_, root, entries, options) => {
    for (const entry of entries) {
      const node = el('button', '', entry.name); node.dataset.scheme = entry.id;
      node.addEventListener('click', () => options.select(entry.id)); root.append(node);
    }
  }};
  app.document.getElementById = id => app.document.querySelector('#' + id);
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../web/map/app.js'), 'utf8'), app.sandbox);
  const root = el('section');
  const add = (tag, id) => {const node=el(tag); node.id=id; root.append(node); return node;};
  add('div','landscape'); add('dialog','mobile-details'); add('div','mobile-inspector');
  add('input','search'); add('p','map-count'); add('p','filter-description');
  add('h2','mobile-detail-title'); add('button','close-details'); root.append(el('div','workspace'));
  app.document.body.append(root);
  const controller = app.window.CrucibleResearchMap.mount(root, {currentReleases:() => releases});
  controller.show();
  return {...app, root, controller, setReleases:value => {releases=value;}, open:id => root.querySelector('[data-scheme="' + id + '"]').click(), actions:() => root.querySelector('.detail-actions').querySelectorAll('a')};
}

let passed = 0;
async function test(name, fn) {await fn(); console.log('PASS ' + name); passed++;}
(async () => {
  await test('scheme actions pin the current registered release rather than a map snapshot', async () => {
    const app = await boot(); app.open('frodokem');
    assert.deepEqual(app.actions().map(a => [a.textContent, a.href]), [
      ['Open challenge','#challenge/frodokem/current%2F2?view=challenge'],
      ['Read research note','notes.html#note/frodokem/1'],
      ['View parameters','#scheme/frodokem/current%2F2']
    ]);
    assert.equal(app.requests.some(request => request.url.pathname === '/api/protocol/assumptions'), false);
  });
  await test('literature-only nodes never inherit actions from a stale scoring snapshot', async () => {
    const app = await boot(); app.open('literature-only');
    assert.deepEqual(app.actions().map(a => a.textContent), ['Read research note']);
  });
  await test('Open challenge reaches the challenge tab and selects the next current profile without generating an instance', async () => {
    const app = await boot(); app.open('frodokem');
    const href = app.actions()[0].href;
    // The real map lives in a shadow tree; its data-view cannot be a shell tab.
    app.root.remove();
    await app.navigate(href);
    assert.equal(app.location.hash, '#challenge/frodokem/current%2F2?profile=next-profile&view=challenge');
    assert.equal(app.$('#overview-workspace').hidden, true);
    assert.equal(app.$('#panel-challenge').hidden, false);
    assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
  });
  await test('reopening a node follows catalogue changes without a new HTTP request', async () => {
    const app = await boot(); app.open('frodokem'); const count = app.requests.length;
    app.setReleases([{id:'frodokem', version:'next/3'}]); app.open('frodokem');
    assert.equal(app.actions().at(-1).href, '#scheme/frodokem/next%2F3');
    app.setReleases([]); app.open('frodokem');
    assert.deepEqual(app.actions().map(a => a.textContent), ['Read research note']);
    assert.equal(app.requests.length, count);
  });
  await test('an unavailable live catalogue never falls back to fabricated release links', async () => {
    const app = await boot(null); app.open('frodokem');
    assert.deepEqual(app.actions().map(a => a.textContent), ['Read research note']);
  });
  await test('only backdrop clicks dismiss the popup; interior whitespace and content stay open', async () => {
    const app = await boot(); app.open('frodokem');
    const modal = app.root.querySelector('#mobile-details');
    modal.getBoundingClientRect = () => ({left:100, right:500, top:100, bottom:400});
    modal.dispatchEvent({type:'click', target:modal, clientX:250, clientY:200});
    assert.equal(modal.open, true, 'Inside dialog whitespace is not the backdrop');
    modal.dispatchEvent({type:'click', target:app.actions()[0], clientX:50, clientY:50});
    assert.equal(modal.open, true, 'A child click never counts as a backdrop click');
    modal.dispatchEvent({type:'click', target:modal});
    assert.equal(modal.open, true, 'A non-pointer event cannot accidentally dismiss the dialog');
    for (const [clientX, clientY] of [[50,200], [550,200], [250,50], [250,450]]) {
      app.open('frodokem');
      modal.dispatchEvent({type:'click', target:modal, clientX, clientY});
      assert.equal(modal.open, false);
      assert.equal(app.root.querySelector('[data-scheme="frodokem"]').getAttribute('aria-pressed'), 'false');
    }
    app.open('frodokem'); app.root.querySelector('#close-details').click();
    assert.equal(modal.open, false, 'The Close button continues to work');
  });
  console.log(`${passed} research map frontend checks passed`);
})().catch(error => {console.error(error); process.exitCode=1;});

const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; };
const user = {id: 'alice', handle: 'alice'};
const scheme = {id: 'demo', version: '1', name: 'Private scheme name', profiles: [{id: 'p1', instance_size: 1}], next_profile_id: 'p1'};
const navigation = {projection: 'navigation', catalogue_revision: 'one', schemes: [scheme]};
const active = envelope => envelope({user, csrf_token: 'csrf', access: {mode: 'beta', allowed: true, status: 'active'}});
function responder(hold) {
  return (url, options, envelope) => {
    if (url.pathname === '/api/session') return active(envelope);
    if (url.pathname === '/api/schemes') return hold ? hold.promise : envelope(navigation);
  };
}
let passed = 0;
async function test(name, run) {await run(); console.log('PASS ' + name); passed++;}
(async () => {
  await test('cold catalogue keeps its real heading and controls while only neutral rows are pending', async () => {
    const hold = deferred(), app = await createApp({responder: responder(hold)});
    const title = app.$('#catalogue-title'), refresh = app.$('#refresh-catalogue'), target = app.$('#kem-catalogue');
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#route-loading').hidden, true);
    assert.equal(target.getAttribute('aria-busy'), 'true');
    assert.equal(target.querySelector('tbody').children.length, 7);
    assert.equal(target.querySelectorAll('.kem-group-row').length, 1);
    assert.equal(target.querySelectorAll('a, button').length, 0);
    assert.equal(target.querySelector('.loader'), null);
    assert.equal(target.querySelector('[role="status"]').className, 'sr-only');
    assert.equal(refresh.disabled, true);
    assert.doesNotMatch(target.textContent, /Private scheme name/);
    assert.equal(app.$('.primary-nav').hidden, false);
    app.$('#catalogue-workspace').focus();
    hold.resolve(app.envelope(navigation)); await flush();
    assert.equal(app.$('#catalogue-title'), title); assert.equal(app.$('#refresh-catalogue'), refresh);
    assert.equal(app.document.activeElement, app.$('#catalogue-workspace'));
    assert.equal(target.getAttribute('aria-busy'), 'false'); assert.equal(refresh.disabled, false);
    assert.match(target.textContent, /Private scheme name/);
  });
  await test('navigation remains usable while the catalogue is held and its late response cannot reopen it', async () => {
    const hold = deferred(), app = await createApp({responder: responder(hold)});
    await app.navigate('#about');
    assert.equal(app.$('#about-workspace').hidden, false);
    hold.resolve(app.envelope(navigation)); await flush();
    assert.equal(app.$('#about-workspace').hidden, false);
    assert.equal(app.$('#catalogue-workspace').hidden, true);
  });
  await test('revocation during cold loading clears real content and ignores its late response', async () => {
    const hold = deferred(), app = await createApp({responder: responder(hold)});
    vm.runInContext("setSession({user:null,csrf_token:'revoked',access:{mode:'beta',allowed:false,status:'revoked'}})", app.sandbox);
    hold.resolve(app.envelope(navigation)); await flush();
    assert.equal(app.$('#access-workspace').hidden, false);
    assert.equal(app.$('#catalogue-workspace').hidden, true);
    assert.doesNotMatch(app.$('#kem-catalogue').textContent, /Private scheme name/);
    assert.equal(app.$('#access-workspace').getAttribute('data-catalogue-pending'), null);
  });
  await test('a failed catalogue keeps the heading and places retry in the same content region', async () => {
    const hold = deferred(), app = await createApp({responder: responder(hold)});
    const title = app.$('#catalogue-title'); hold.reject(new Error('Synthetic read failure')); await flush();
    assert.equal(app.$('#catalogue-workspace').hidden, false);
    assert.equal(app.$('#catalogue-title'), title);
    assert.match(app.$('#kem-catalogue').textContent, /server could not be reached.*Try again/);
    assert.equal(app.$('#kem-catalogue').getAttribute('aria-busy'), 'false');
    assert.equal(app.$('#refresh-catalogue').disabled, false);
  });
  await test('warm background refresh leaves the existing table and focused scheme untouched', async () => {
    let hold;
    const app = await createApp({responder: (url, options, envelope) => {
      if (url.pathname === '/api/session') return active(envelope);
      if (url.pathname === '/api/schemes') return hold ? hold.promise : envelope(navigation);
    }});
    const table = app.$('#kem-catalogue').firstChild;
    app.$('#nav-about').focus(); hold = deferred();
    const task = vm.runInContext('refreshProfiles({background:true})', app.sandbox);
    assert.equal(app.$('#kem-catalogue').firstChild, table);
    assert.equal(app.$('#kem-catalogue').getAttribute('aria-busy'), 'false');
    hold.resolve(app.envelope(navigation)); await task; await flush();
    assert.equal(app.$('#kem-catalogue').firstChild, table);
    assert.equal(app.document.activeElement, app.$('#nav-about'));
  });
  await test('unresolved access requests no private content and generic read statuses are not visible copy', async () => {
    const hold = deferred(), app = await createApp({responder: url => url.pathname === '/api/session' ? hold.promise : undefined});
    assert.equal(app.$('#access-workspace').getAttribute('aria-busy'), 'true');
    assert.equal(app.requests.length, 1);
    assert.doesNotMatch(app.$('#access-loading-preview').textContent, /Private scheme name|alice/);
    vm.runInContext("setLoading($('#people-content'), 'Loading participants')", app.sandbox);
    assert.equal(app.$('#people-content').firstChild.firstChild.className, 'sr-only');
    assert.equal(app.$('#people-content').querySelector('.loader'), null);
    hold.resolve(app.envelope({user: null, csrf_token: 'anon', access: {mode: 'beta', allowed: false, status: 'anonymous'}})); await flush();
    assert.equal(app.$('#access-loading-preview').hidden, true);
    assert.equal(app.$('#access-workspace').getAttribute('aria-busy'), 'false');
  });
  console.log(`${passed} loading-polish checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});

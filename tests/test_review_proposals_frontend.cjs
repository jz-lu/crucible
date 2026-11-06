// The Proposals overview joins two independent reviewer workflows, not their cursors or permissions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');

const reviewer = {id:'reviewer', handle:'reviewer'};
const author = {id:'author', handle:'author'};
const digest = 'a'.repeat(64);
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};};
const response = (data, status=200) => ({ok:status >= 200 && status < 300, status,
  json:async () => data, text:async () => JSON.stringify(data)});
const specification = title => ({format:'crucible-kem-spec-v1', name:title, version:'draft-1', summary:'Synthetic unreviewed proposal.',
  algorithms:{setup:'PRIVATE_ALGORITHM_BODY', keygen:'Fixture key generation.', encapsulate:'Fixture encapsulation.', decapsulate:'Fixture decapsulation.'},
  parameters:{tier_meaning:'Fixture scale.'}, encoding:{public_key:'Fixture bytes.'},
  correctness:{kind:'unknown', argument:'Unproved fixture.'}, security:{claim:'No claim.'}, implementation:null, references:[]});
const scheme = (id, title=id) => ({id, status:'submitted', revision:2, created_at:100, content_sha256:digest,
  specification:specification(title)});
const change = (id, purpose='procedure', title=id) => ({id, purpose, title});
const packet = (id, title=id) => ({...change(id, 'procedure', title), question:'Synthetic proposed process change.',
  scope:'Human assessment only.', sources:[], packet_sha256:digest, can_review:true, can_retract:false});
const schemePage = (records=[], next=null) => ({proposals:records, next_after:next});
const changePage = (records=[], next=null) => ({requests:records, next_after:next});

function notebookDouble() {
  return {mount(root) {return {show:async () => {}, warm:async () => ({bytes:0}),
    setAccount:value => {if (!value) root.replaceChildren();}, hide:() => {}};}};
}
async function boot({hash='#reviews?section=proposals', hook, notebook=notebookDouble()}={}) {
  return createApp({schemes:[], user:reviewer, hash, notebook, responder:async (url, options, envelope) => {
    if (hook) {const value = await hook(url, options, envelope); if (value !== undefined) return value;}
    if (url.pathname === '/api/session') return envelope({user:reviewer, csrf_token:'test-csrf',
      access:{mode:'beta', allowed:true, status:'active'}, capabilities:{review_protocol_proposals:true}});
    if (url.pathname === '/api/protocol/review-queue' || url.pathname === '/api/me/protocol-proposals') return schemePage();
    if (url.pathname === '/api/notebook/research-requests') return changePage();
    if (url.pathname.startsWith('/api/protocol/proposals/')) return {proposal:scheme(url.pathname.split('/').at(-1)), author};
    if (url.pathname.startsWith('/api/notebook/research-requests/')) return {request:packet(url.pathname.split('/').at(-1))};
  }});
}
function installRealNotebook(app) {
  app.sandbox.crypto.subtle = require('node:crypto').webcrypto.subtle;
  app.sandbox.window.localStorage = {getItem:() => null}; app.sandbox.window.confirm = () => true;
  for (const file of ['store.js', 'seed.js', 'notebook.js']) {
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../web/notebook', file), 'utf8'), app.sandbox, {filename:file});
  }
}
const group = (app, kind) => app.$('#review-' + kind + '-list');
const pages = (app, kind) => app.$('#review-' + kind + '-pages');
const button = (root, label) => root.querySelectorAll('button').find(item => item.textContent === label);
const titles = (app, kind) => group(app, kind).querySelectorAll('.proposal-list-name').map(item => item.textContent);
const reads = (app, kind) => app.requests.filter(item => item.url.pathname ===
  (kind === 'schemes' ? '/api/protocol/review-queue' : '/api/notebook/research-requests'));
let passed = 0;
async function test(name, run) {await run(); passed++; console.log('PASS ' + name);}

(async () => {
  await test('the overview renders two bounded independent groups with real purpose labels and inert titles', async () => {
    const purposes = ['visualization_nomination', 'procedure', 'scoring_nomination', 'map_nomination', 'highlight_nomination', 'source_check'];
    const app = await boot({hook:url => url.pathname === '/api/protocol/review-queue' ?
      schemePage([scheme('scheme-one', '<img src=x onerror=unsafe()>')]) : url.pathname === '/api/notebook/research-requests' ?
        changePage(purposes.map((purpose, index) => change('change-' + index, purpose))) : undefined});
    assert.equal(app.$('#review-proposals-workspace').hidden, false);
    assert.equal(app.$('#review-nav-proposals').getAttribute('aria-current'), 'page');
    assert.equal(group(app, 'schemes').querySelectorAll('a').length, 1);
    assert.equal(group(app, 'changes').querySelectorAll('a').length, 6);
    assert.equal(group(app, 'schemes').querySelector('img'), null);
    assert.match(group(app, 'schemes').textContent, /<img src=x onerror=unsafe\(\)>/);
    assert.doesNotMatch(app.$('#review-proposals-workspace').textContent, /PRIVATE_ALGORITHM_BODY/);
    for (const label of ['Content or visualization change', 'Pipeline or process change', 'Challenge scoring',
      'Scheme map inclusion', 'Highlight a result', 'Source check']) assert.ok(group(app, 'changes').textContent.includes(label));
    for (const kind of ['schemes', 'changes']) assert.equal(reads(app, kind)[0].url.searchParams.get('limit'), '25');
    assert.equal(reads(app, 'changes')[0].url.searchParams.get('review'), 'true');
    assert.equal(group(app, 'schemes').querySelector('a').href, '#reviews?section=proposals&proposal=scheme-one');
    assert.equal(group(app, 'changes').querySelector('a').href, '#reviews?section=proposals&research_request=change-0');
  });

  await test('paging one group never sends or consumes the other workflow cursor', async () => {
    const app = await boot({hook:url => {
      if (url.pathname === '/api/protocol/review-queue') return url.searchParams.has('after') ?
        schemePage([scheme('scheme-two')]) : schemePage([scheme('scheme-one')], 'scheme-cursor');
      if (url.pathname === '/api/notebook/research-requests') return url.searchParams.has('after') ?
        changePage([change('change-two')]) : changePage([change('change-one')], 'change-cursor');
    }});
    button(pages(app, 'schemes'), 'Next page').click(); await flush();
    assert.deepEqual(titles(app, 'schemes'), ['scheme-two']); assert.deepEqual(titles(app, 'changes'), ['change-one']);
    assert.equal(reads(app, 'schemes').at(-1).url.searchParams.get('after'), 'scheme-cursor');
    assert.equal(reads(app, 'changes').length, 1);
    button(pages(app, 'changes'), 'Next page').click(); await flush();
    assert.deepEqual(titles(app, 'changes'), ['change-two']);
    assert.equal(reads(app, 'changes').at(-1).url.searchParams.get('after'), 'change-cursor');
    button(pages(app, 'schemes'), 'Previous page').click(); await flush();
    assert.deepEqual(titles(app, 'schemes'), ['scheme-one']); assert.deepEqual(titles(app, 'changes'), ['change-two']);
    assert.equal(reads(app, 'schemes').at(-1).url.searchParams.has('after'), false);
    assert.equal(button(pages(app, 'changes'), 'Next page').disabled, true);
  });

  await test('a partial network failure leaves the other group usable and retries only the failed group', async () => {
    let failed = true;
    const app = await boot({hook:url => {
      if (url.pathname === '/api/protocol/review-queue') return schemePage([scheme('available-scheme')]);
      if (url.pathname === '/api/notebook/research-requests') {
        if (failed) throw Object.assign(Error('Synthetic temporary group failure'), {status:503});
        return changePage([change('recovered-change')]);
      }
    }});
    assert.deepEqual(titles(app, 'schemes'), ['available-scheme']);
    assert.match(group(app, 'changes').textContent, /could not be reached/);
    failed = false; button(group(app, 'changes'), 'Try again').click(); await flush();
    assert.deepEqual(titles(app, 'changes'), ['recovered-change']);
    assert.equal(reads(app, 'schemes').length, 1); assert.equal(reads(app, 'changes').length, 2);
    assert.equal(group(app, 'changes').getAttribute('aria-busy'), null);
  });

  await test('return navigation paints compact cached summaries then revalidates and successful decisions invalidate them', async () => {
    const delayedScheme = deferred(), delayedChange = deferred(); let refresh = false;
    const app = await boot({hook:(url, options) => {
      if (url.pathname === '/api/protocol/review-queue') return refresh ? delayedScheme.promise : schemePage([scheme('cached-scheme')]);
      if (url.pathname === '/api/notebook/research-requests') return refresh ? delayedChange.promise : changePage([change('cached-change')]);
      if (url.pathname.endsWith('/publication-reviews')) return {note:{id:'synthetic-decision'}};
    }});
    const compact = app.sandbox.cachedRead('review-proposals:schemes:');
    assert.doesNotMatch(JSON.stringify(compact), /PRIVATE_ALGORITHM_BODY|specification|algorithms|content_sha256/);
    await app.navigate('#reviews?section=flagged'); refresh = true;
    await app.navigate('#reviews?section=proposals');
    assert.deepEqual(titles(app, 'schemes'), ['cached-scheme']); assert.deepEqual(titles(app, 'changes'), ['cached-change']);
    assert.equal(group(app, 'schemes').getAttribute('aria-busy'), 'true');
    delayedScheme.resolve(schemePage([scheme('fresh-scheme')])); delayedChange.resolve(changePage()); await flush();
    assert.deepEqual(titles(app, 'schemes'), ['fresh-scheme']); assert.deepEqual(titles(app, 'changes'), []);
    await app.sandbox.api('/api/notebook/notes/synthetic-decision/publication-reviews', {decision:'changes_requested'});
    assert.equal(app.sandbox.cachedRead('review-proposals:schemes:'), null);
    assert.equal(group(app, 'schemes').textContent, '');
  });

  await test('permission denial retires both sibling reads immediately while session refresh is unresolved', async () => {
    const app = await boot({hash:'#about'});
    const denied = deferred(), sibling = deferred(), session = deferred(), original = app.sandbox.fetch;
    app.sandbox.fetch = (input, options) => {
      const url = new URL(input, 'http://testserver');
      if (url.pathname === '/api/session') return session.promise;
      if (url.pathname === '/api/protocol/review-queue') return denied.promise;
      if (url.pathname === '/api/notebook/research-requests') return sibling.promise;
      return original(input, options);
    };
    await app.navigate('#reviews?section=proposals');
    denied.resolve(response({detail:'Synthetic reviewer access revoked'}, 403)); await flush();
    for (const kind of ['schemes', 'changes']) assert.match(group(app, kind).textContent, /Synthetic reviewer access revoked/);
    sibling.resolve(response(changePage([change('MUST_NOT_REAPPEAR')]))); await flush();
    assert.doesNotMatch(app.$('#review-proposals-workspace').textContent, /MUST_NOT_REAPPEAR/);
    assert.equal(app.sandbox.cachedRead('review-proposals:changes:'), null);
    for (const kind of ['schemes', 'changes']) assert.notEqual(group(app, kind).getAttribute('aria-busy'), 'true');
    session.resolve(response(app.envelope({user:reviewer, csrf_token:'test-csrf',
      access:{mode:'beta', allowed:true, status:'active'}, capabilities:{review_protocol_proposals:false}}))); await flush();
    assert.equal(app.$('#review-proposals-workspace').hidden, true);
  });

  await test('late overview denial clears a newer private detail in the same reviewer session', async () => {
    const denied = deferred(), session = deferred(); let hold = true;
    const app = await boot({hash:'#about', hook:url => {
      if (url.pathname === '/api/protocol/proposals/current-scheme') return {proposal:scheme('current-scheme', 'CURRENT_PRIVATE_DETAIL'), author};
    }});
    const original = app.sandbox.fetch;
    app.sandbox.fetch = (input, options) => {
      const url = new URL(input, 'http://testserver');
      if (url.pathname === '/api/session') return session.promise;
      if (url.pathname === '/api/protocol/review-queue' && hold) return denied.promise;
      return original(input, options);
    };
    await app.navigate('#reviews?section=proposals'); hold = false;
    await app.navigate('#reviews?section=proposals&proposal=current-scheme');
    assert.match(app.$('#proposal-detail-content').textContent, /CURRENT_PRIVATE_DETAIL/);
    denied.resolve(response({detail:'Prior overview denied'}, 403)); await flush();
    assert.doesNotMatch(app.$('#proposal-detail-content').textContent, /CURRENT_PRIVATE_DETAIL/);
    assert.equal(app.$('#proposal-review-form').hidden, true);
    session.resolve(response(app.envelope({user:reviewer, csrf_token:'test-csrf',
      access:{mode:'beta', allowed:true, status:'active'}, capabilities:{review_protocol_proposals:false}}))); await flush();
  });

  await test('late scheme and research detail responses cannot repaint after switching proposal subtype', async () => {
    const lateScheme = deferred(), lateChange = deferred();
    const app = await boot({hash:'#about', notebook:null, hook:url => {
      if (url.pathname === '/api/protocol/proposals/late-scheme') return lateScheme.promise;
      if (url.pathname === '/api/notebook/research-requests/late-change') return lateChange.promise;
      if (url.pathname === '/api/notebook/research-requests/current-change') return {request:packet('current-change', 'CURRENT_CHANGE_PACKET')};
      if (url.pathname === '/api/protocol/proposals/current-scheme') return {proposal:scheme('current-scheme', 'CURRENT_SCHEME_DETAIL'), author};
    }});
    installRealNotebook(app);
    await app.navigate('#reviews?section=proposals&proposal=late-scheme');
    await app.navigate('#reviews?section=proposals&research_request=current-change');
    const root = app.$('#review-notebook-workspace');
    assert.match(root.textContent, /CURRENT_CHANGE_PACKET/);
    lateScheme.resolve({proposal:scheme('late-scheme', 'STALE_SCHEME_DETAIL'), author}); await flush();
    assert.equal(app.$('#proposal-workspace').hidden, true);
    assert.doesNotMatch(app.$('#proposal-detail-content').textContent, /STALE_SCHEME_DETAIL/);
    assert.match(root.textContent, /CURRENT_CHANGE_PACKET/);
    await app.navigate('#reviews?section=proposals&research_request=late-change');
    await app.navigate('#reviews?section=proposals&proposal=current-scheme');
    lateChange.resolve({request:packet('late-change', 'STALE_CHANGE_PACKET')}); await flush();
    assert.equal(root.hidden, true); assert.doesNotMatch(root.textContent, /STALE_CHANGE_PACKET/);
    assert.match(app.$('#proposal-detail-content').textContent, /CURRENT_SCHEME_DETAIL/);
  });

  await test('review capability revocation clears compact caches and ignores pending overview responses', async () => {
    let phase = 0; const lateScheme = deferred(), lateChange = deferred();
    const app = await boot({hook:url => {
      if (url.pathname === '/api/protocol/review-queue') return phase ? lateScheme.promise : schemePage([scheme('private-scheme')]);
      if (url.pathname === '/api/notebook/research-requests') return phase ? lateChange.promise : changePage([change('private-change')]);
    }});
    await app.navigate('#reviews?section=flagged'); phase = 1; await app.navigate('#reviews?section=proposals');
    assert.deepEqual(titles(app, 'schemes'), ['private-scheme']);
    app.sandbox.setSession(app.envelope({user:reviewer, csrf_token:'test-csrf',
      access:{mode:'beta', allowed:true, status:'active'}, capabilities:{review_protocol_proposals:false}}));
    lateScheme.resolve(schemePage([scheme('late-private-scheme')])); lateChange.resolve(changePage([change('late-private-change')])); await flush();
    assert.equal(app.$('#review-proposals-workspace').hidden, true);
    assert.doesNotMatch(app.$('#review-proposals-workspace').textContent, /private-scheme|private-change/);
    assert.equal(app.sandbox.cachedRead('review-proposals:schemes:'), null);
    assert.equal(app.sandbox.cachedRead('review-proposals:changes:'), null);
  });

  await test('local argument updates join Proposals and open in the dedicated reviewer reader', async () => {
    const shows=[],notebook={mount(){return{show:async p=>shows.push(new URLSearchParams(p)),warm:async()=>({bytes:0}),setAccount(){},hide(){}};}};
    const app=await boot({hash:'#about',notebook,hook:url=>url.pathname==='/api/notebook/statement-workflow/updates'?{updates:[
      {id:'argument-update',target:{id:'trial',title:'<img onerror=unsafe()> scoped update'},status:'pending'},
      {id:'older-argument-update',target:{id:'trial',title:'An earlier adoption'},status:'approved'},
      {id:'declined-argument-update',target:{id:'trial',title:'An earlier decision'},status:'declined'}
    ]}:undefined});
    vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../web/notebook/statement-workflow.js'),'utf8'),app.sandbox,{filename:'statement-workflow.js'});
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'test-csrf',access:{mode:'beta',allowed:true,status:'active'},capabilities:{review_protocol_proposals:true,statement_workflow_trial:true}}));
    await app.navigate('#reviews?section=proposals');await flush();
    const group=app.$('#review-argument-updates');assert.match(group.textContent,/Argument updates.*scoped update.*Awaiting review/s);assert.equal(group.querySelectorAll('img').length,0);
    const list=group.querySelector('.nb-argument-update-list'),history=group.querySelector('.nb-past-argument-updates');
    assert.equal(list.children.filter(item=>item.classList.contains('nb-research-request')).length,1);
    assert.ok(history);assert.ok(!history.open);assert.equal(history.querySelector('summary').textContent,'Past argument updates');
    assert.equal(history.querySelectorAll('.nb-research-request').length,2);assert.match(history.textContent,/An earlier adoption.*Applied.*An earlier decision.*Not adopted/s);
    group.querySelector('a').click();await flush();
    assert.equal(app.location.hash,'#reviews?section=proposals&statement_update=argument-update');assert.equal(app.$('#review-notebook-workspace').hidden,false);assert.equal(app.$('#review-proposals-workspace').hidden,true);
    assert.equal(shows.at(-1).get('statement_update'),'argument-update');
  });

  await test('late local argument summaries cannot repaint after reviewer access is removed', async () => {
    const held=deferred();const app=await boot({hash:'#about',hook:url=>url.pathname==='/api/notebook/statement-workflow/updates'?held.promise:undefined});
    vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../web/notebook/statement-workflow.js'),'utf8'),app.sandbox,{filename:'statement-workflow.js'});
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'test-csrf',capabilities:{review_protocol_proposals:true,statement_workflow_trial:true}}));
    await app.navigate('#reviews?section=proposals');
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'test-csrf',capabilities:{review_protocol_proposals:false,statement_workflow_trial:true}}));
    held.resolve({updates:[{id:'late',target:{id:'private',title:'LATE_PRIVATE_ARGUMENT'},status:'pending'}]});await flush();
    assert.doesNotMatch(app.$('#review-proposals-workspace').textContent,/LATE_PRIVATE_ARGUMENT/);assert.equal(app.$('#review-proposals-workspace').hidden,true);
  });

  console.log(`${passed} review-proposals frontend checks passed.`);
})().catch(error => {console.error(error); process.exitCode = 1;});

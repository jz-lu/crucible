// Reviewer work is routed through an account page; ordinary notebook work stays separate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const reviewer = {id:'reviewer', handle:'reviewer'};
const participant = {id:'participant', handle:'participant'};
const deferred = () => {let resolve; const promise = new Promise(value => {resolve = value;}); return {promise, resolve};};
function notebookDouble() {
  const mounts = [];
  return {mounts, mount(root, options) {
    const record = {root, options, shows:[], warms:[], accounts:[], hidden:0}; mounts.push(record);
    return {show:async params => {record.shows.push(new URLSearchParams(params));}, warm:async(kind,section)=>{record.warms.push({kind,section});return{bytes:0};}, setAccount:account => record.accounts.push(account), hide:() => record.hidden++};
  }};
}
async function boot({user=reviewer, canReview=true, hash='#reviews', notebook=notebookDouble(), hook}={}) {
  const app = await createApp({schemes:[], user, hash, notebook, responder:async (url,options,envelope) => {
    if (hook) { const value = await hook(url,options,envelope); if (value !== undefined) return value; }
    if (url.pathname === '/api/session') return envelope({user,csrf_token:'test',capabilities:{review_protocol_proposals:canReview}});
    if (url.pathname.startsWith('/api/people/')) return envelope({person:{handle:decodeURIComponent(url.pathname.split('/').at(-1)),points:0,accepted_proposal_count:0,records:[],recent_recoveries:[]}});
    if (url.pathname === '/api/protocol/review-queue' || url.pathname === '/api/me/protocol-proposals') return envelope({proposals:[],next_after:null});
    if (url.pathname === '/api/notebook/research-requests') return {requests:[],next_after:null};
  }});
  return {...app, notebook};
}
function buttonText(root,label) {return root.querySelectorAll('button').find(button=>button.textContent===label);}
function installRealNotebook(app) {
  app.sandbox.crypto.subtle=require('node:crypto').webcrypto.subtle;
  app.sandbox.window.localStorage={getItem:()=>null};app.sandbox.window.confirm=()=>true;
  for(const file of ['store.js','seed.js','notebook.js']) vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../web/notebook',file),'utf8'),app.sandbox,{filename:file});
}
let passed = 0;
async function test(name, run) {await run(); passed++; console.log('PASS '+name);}
(async () => {
  await test('Review center belongs only to the current reviewer account, never primary navigation', async () => {
    const app = await boot({hash:'#person/reviewer'});
    assert.equal(app.$('#profile-review-link').hidden,false);
    assert.equal(app.$('#profile-review-link').href,'#reviews?section=flagged');
    assert.equal(app.$('.primary-nav').querySelectorAll('a').some(link=>link.href.startsWith('#reviews')),false);
    await app.navigate('#person/participant'); assert.equal(app.$('#profile-review-link').hidden,true);
    await app.navigate('#person/reviewer'); assert.equal(app.$('#profile-review-link').hidden,false);
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'same',capabilities:{review_protocol_proposals:false}}));
    assert.equal(app.$('#profile-review-link').hidden,true);
    app.sandbox.setSession(app.envelope({user:participant,csrf_token:'next',capabilities:{review_protocol_proposals:true}}));
    assert.equal(app.$('#profile-review-link').hidden,true);
  });
  await test('Flagged requests are the default page, with a separate review-only notebook controller', async () => {
    const app = await boot();
    assert.equal(app.location.hash,'#reviews?section=flagged');
    assert.equal(app.document.title,'Review center — Crucible');
    assert.equal(app.$('#review-workspace').hidden,false);
    assert.equal(app.$('#notebook-workspace').hidden,true);
    assert.equal(app.$('#review-nav-flagged').getAttribute('aria-current'),'page');
    const mounted = app.notebook.mounts[0];
    assert.equal(mounted.root.id,'review-notebook-workspace'); assert.equal(mounted.options.reviewOnly,true);
    assert.equal(mounted.shows[0].get('review'),'1');
    await app.navigate('#notebook');
    assert.equal(app.$('#review-workspace').hidden,true); assert.ok(mounted.hidden>0);
    assert.equal(app.notebook.mounts[1].root.id,'notebook-workspace'); assert.equal(app.notebook.mounts[1].options.reviewOnly,false);
    app.history.back(); await flush(); assert.equal(app.$('#review-workspace').hidden,false);
    assert.equal(app.notebook.mounts.length,2);
  });
  await test('review tabs and old links retain exact targets while canonicalizing to dedicated routes', async () => {
    const app=await boot({hash:'#notebook?review=1&note=note-one&revision=3'});
    assert.match(app.location.hash,/^#reviews\?/); assert.equal(app.notebook.mounts[0].shows[0].get('note'),'note-one');
    assert.equal(app.notebook.mounts[0].shows[0].get('revision'),'3');
    await app.navigate('#notebook?research=1&research_review=1&research_request=request-one');
    assert.match(app.location.hash,/section=proposals/);
    const params=app.notebook.mounts[0].shows.at(-1);
    assert.equal(params.get('research_request'),'request-one'); assert.equal(params.get('research_review'),'1'); assert.equal(params.has('review'),false);
    await app.navigate('#reviews?section=sharing'); assert.equal(app.location.hash,'#reviews?section=flagged');
    app.$('#review-nav-proposals').click(); await flush();
    assert.equal(app.location.hash,'#reviews?section=proposals');
    assert.equal(app.$('#review-proposals-workspace').hidden,false); assert.equal(app.$('#review-notebook-workspace').hidden,true);
    assert.equal(app.$('#proposal-workspace').hidden,true);
    assert.equal(app.$('#review-nav-proposals').getAttribute('aria-current'),'page');
    assert.equal(app.requests.filter(value=>value.url.pathname==='/api/protocol/review-queue').length,1);
    assert.equal(app.document.querySelectorAll('dialog').some(dialog=>dialog.open),false);
  });
  await test('three reviewer entries use canonical groups while legacy publication links warm attention', async () => {
    const app=await boot({hash:'#reviews?section=publication&note=note-one&revision=3&research=1'});
    assert.match(app.location.hash,/section=flagged/);
    assert.equal(app.$('#review-nav-flagged').getAttribute('aria-current'),'page');
    assert.equal(app.$('#review-navigation').querySelectorAll('a').length,3);
    assert.deepEqual(app.$('#review-navigation').querySelectorAll('a').map(node=>node.textContent),['Flagged requests','Proposals','Bounties']);
    const controller=app.notebook.mounts[0],params=controller.shows.at(-1);
    assert.equal(params.get('note'),'note-one');assert.equal(params.get('revision'),'3');
    assert.equal(params.get('review'),'1');assert.equal(params.has('research'),false);
    const jobs=app.sandbox.browseWarmJobs().filter(job=>/^(flagged|publication):/.test(job.key));
    assert.deepEqual(Array.from(jobs,job=>job.key),['flagged:list','flagged:next']);
    for(const job of jobs)await job.run();
    assert.deepEqual(controller.warms,[{kind:'list',section:'flagged'},{kind:'next',section:'flagged'}]);
    app.$('#review-nav-bounties').click();await flush();
    assert.equal(app.$('#review-nav-flagged').getAttribute('aria-current'),null);
    assert.equal(app.$('#review-nav-bounties').getAttribute('aria-current'),'page');
    app.history.back();await flush();
    assert.equal(app.$('#review-nav-flagged').getAttribute('aria-current'),'page');
    assert.equal(controller.shows.at(-1).get('revision'),'3');
  });
  await test('ordinary and signed-out accounts cannot mount queues or fetch reviewer detail', async () => {
    for (const user of [participant,null]) for (const route of ['#reviews','#reviews?section=flagged','#reviews?section=flagged&note=private-note&revision=2','#reviews?section=schemes&proposal=private-one','#propose?review=1&proposal=private-one','#notebook?review=1&note=private-note','#reviews?section=research&research_request=private-request']) {
      const app=await boot({user,canReview:false,hash:route});
      assert.equal(app.notebook.mounts.length,0);
      assert.equal(app.requests.some(value=>value.url.pathname.includes('protocol') || value.url.pathname.includes('notebook')),false);
      assert.equal(app.$('#review-navigation').hidden,true); assert.equal(app.$('#proposal-workspace').hidden,true);
      assert.equal(app.$('#review-access-message').hidden,false);
    }
  });
  await test('capability revocation clears notebook reviewer state and blocks a late scheme queue', async () => {
    const app=await boot(); const controller=app.notebook.mounts[0];
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'same',capabilities:{review_protocol_proposals:false}}));
    assert.equal(controller.accounts.at(-1),null); assert.equal(app.$('#review-notebook-workspace').hidden,true);
    assert.equal(app.$('#review-access-message').hidden,false);
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'restored',capabilities:{review_protocol_proposals:true}}));
    await flush(); assert.equal(app.notebook.mounts.length,1); assert.equal(app.$('#review-notebook-workspace').hidden,false);
    assert.equal(controller.accounts.at(-1).id,reviewer.id); assert.equal(controller.shows.length,2);
    const pending=deferred(); const late=await boot({hash:'#reviews?section=schemes',hook:url=>url.pathname==='/api/protocol/review-queue'?pending.promise:undefined});
    late.sandbox.setSession(late.envelope({user:participant,csrf_token:'next',capabilities:{review_protocol_proposals:false}}));
    pending.resolve({proposals:[{id:'private',specification:{name:'Never display this'}}]}); await flush();
    assert.equal(late.$('#proposal-list').textContent,''); assert.equal(late.$('#proposal-workspace').hidden,true);
  });
  await test('real notebook controller follows the account review route and records an exact inline publication decision', async () => {
    const digest='e'.repeat(64); let approved=false;
    const content={title:'Exact publication candidate',claim:'A deliberately unproved fixture.',conditions:'Fixture only.',body:'Reproducible fixture evidence.',tags:['algebra'],kind:'conjecture'};
    const revision=()=>({revision:3,content_sha256:digest,created_at:100,content,publication_status:approved?'approved':'submitted',publication:{requested_at:99}});
    const candidate=()=>({id:'publication-one',owner:participant,can_edit:false,can_review:true,created_at:100,revision:3,revision_count:3,content_sha256:digest,publication_status:revision().publication_status,revisions:[revision()]});
    const app=await boot({hash:'#about',notebook:null,hook:(url,options)=>{
      if(url.pathname==='/api/notebook/review-queue')return{notes:[candidate()],next_after:null,screening:url.searchParams.get('screening')};
      if(url.pathname==='/api/notebook/notes/publication-one')return{note:candidate()};
      if(url.pathname==='/api/notebook/notes/publication-one/publication-reviews'){approved=true;return{note:candidate()};}
      if(url.pathname==='/api/notebook/research-requests')return{requests:[],can_review:true,next_after:null};
    }});
    installRealNotebook(app);
    await app.navigate('#person/reviewer'); app.$('#profile-review-link').click(); await flush();
    const root=app.$('#review-notebook-workspace');
    assert.match(root.textContent,/Requests needing human publication review.*Exact publication candidate/);
    assert.equal(root.querySelector('[data-nb="new"]'),null); assert.equal(root.querySelector('[data-nb="notes-view"]'),null);
    root.querySelector('.nb-title').click();
    buttonText(root,'Read full note →').click(); await flush();
    assert.equal(app.location.hash,'#reviews?section=flagged&note=publication-one&revision=3');
    assert.equal(app.requests.filter(row=>row.url.pathname==='/api/notebook/notes/publication-one').at(-1).url.searchParams.get('revision'),'3');
    assert.match(root.textContent,/Publication decision/); assert.equal(root.querySelector('[data-nb="dialog"]').open,false);
    root.querySelector('[name="reason"]').value='Claims and uncertainty are clearly attributed.';
    root.querySelector('.nb-publication-review').querySelector('form').dispatchEvent({type:'submit'}); await flush();
    const write=app.requests.find(row=>row.url.pathname.endsWith('/publication-reviews'));
    assert.equal(write.options.method,'POST');
    const body=JSON.parse(write.options.body);assert.equal(body.revision,3);assert.equal(body.content_sha256,digest);assert.equal(body.decision,'approved');
    assert.equal(root.querySelector('.nb-publication-review'),null); assert.match(root.textContent,/Published/);
    app.$('#review-nav-proposals').click(); await flush();
    assert.equal(app.requests.filter(row=>row.url.pathname==='/api/notebook/research-requests').at(-1).url.searchParams.get('review'),'true');
    assert.equal(root.hidden,true);assert.match(app.$('#review-changes-list').textContent,/No proposed changes awaiting review/);assert.equal(app.$('#review-proposals-workspace').hidden,false);
    assert.equal(app.document.querySelectorAll('dialog').some(dialog=>dialog.open),false);
  });
  await test('real ordinary and reviewer notebook controllers stay separate and clear on capability loss and sign-out', async () => {
    const digest='f'.repeat(64);
    const content={title:'Private review seam fixture',claim:'A private fixture claim.',conditions:'Fixture only.',body:'Private fixture body.',tags:[],kind:'conjecture'};
    const revision={revision:1,content_sha256:digest,created_at:100,content,publication_status:'submitted',publication:{requested_at:99}};
    const record={id:'seam-one',owner:reviewer,can_edit:true,can_review:true,created_at:100,revision:1,revision_count:1,content_sha256:digest,publication_status:'submitted',revisions:[revision]};
    const app=await boot({hash:'#about',notebook:null,hook:url=>{
      if(url.pathname==='/api/notebook/notes/seam-one')return{note:record};
      if(url.pathname==='/api/notebook/notes'||url.pathname==='/api/notebook/review-queue')return{notes:[record],next_after:null,screening:url.searchParams.get('screening')};
      if(url.pathname==='/api/notebook/research-requests')return{requests:[],next_after:null};
    }});
    installRealNotebook(app);
    await app.navigate('#reviews?section=publication&note=seam-one&revision=1');
    const reviewRoot=app.$('#review-notebook-workspace');assert.ok(reviewRoot.querySelector('.nb-publication-review'));
    await app.navigate('#notebook?note=seam-one&revision=1');
    const normalRoot=app.$('#notebook-workspace');assert.notEqual(reviewRoot.notebookController,normalRoot.notebookController);
    assert.equal(reviewRoot.hidden,true);assert.equal(normalRoot.hidden,false);
    assert.ok(buttonText(normalRoot,'Edit'));assert.equal(buttonText(normalRoot,'Publish'),undefined);
    assert.equal(normalRoot.querySelector('.nb-publication-review'),null);assert.equal(normalRoot.querySelector('.nb-review-links'),null);
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'same',capabilities:{review_protocol_proposals:false}}));
    assert.doesNotMatch(reviewRoot.textContent,/Private review seam fixture|Private fixture body/);
    // Rotating this browser session clears private readers, then reloads the visible one.
    await flush();
    assert.match(normalRoot.textContent,/Private review seam fixture/);
    await app.navigate('#reviews?section=flagged');assert.equal(app.$('#review-access-message').hidden,false);
    assert.equal(reviewRoot.hidden,true);
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'',capabilities:{review_protocol_proposals:false}}));
    assert.doesNotMatch(reviewRoot.textContent,/Private review seam fixture|Private fixture body/);
    assert.doesNotMatch(normalRoot.textContent,/Private review seam fixture|Private fixture body/);
    assert.equal(app.document.querySelectorAll('dialog').some(dialog=>dialog.open),false);
  });
  await test('flagged publication decisions retain the reader section and browser history refreshes the attention queue',async()=>{
    let approved=false;
    const digest='c'.repeat(64),candidate=()=>{
      const revision={revision:2,content_sha256:digest,created_at:100,publication_status:approved?'approved':'submitted',publication:{requested_at:99},
        content:{title:'Exact flagged request',claim:'Fixture claim',conditions:'Fixture scope',body:'Fixture body',tags:[],kind:'conjecture'}};
      return{id:'flagged-one',owner:participant,can_edit:false,can_review:true,revision:2,revision_count:2,content_sha256:digest,publication_status:revision.publication_status,revisions:[revision]};
    };
    const app=await boot({hash:'#about',notebook:null,hook:url=>{
      if(url.pathname==='/api/notebook/review-queue')return{screening:url.searchParams.get('screening'),notes:approved?[]:[candidate()],next_after:null};
      if(url.pathname==='/api/notebook/notes/flagged-one')return{note:candidate()};
      if(url.pathname==='/api/notebook/notes/flagged-one/publication-reviews'){approved=true;return{note:candidate()};}
    }});
    installRealNotebook(app);
    await app.navigate('#reviews?section=flagged');await app.navigate('#reviews?section=flagged');
    const root=app.$('#review-notebook-workspace');
    root.querySelector('.nb-title').click();buttonText(root,'Read full note →').click();await flush();
    assert.equal(app.location.hash,'#reviews?section=flagged&note=flagged-one&revision=2');
    root.querySelector('[name="reason"]').value='Checked the exact submitted revision in context.';
    root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.equal(app.location.hash,'#reviews?section=flagged&note=flagged-one&revision=2');
    assert.equal(root.querySelector('.nb-publication-review'),null);assert.match(root.textContent,/Published/);
    assert.equal(app.$('#review-nav-flagged').getAttribute('aria-current'),'page');
    buttonText(root,'← Flagged requests').click();await flush();
    assert.equal(app.location.hash,'#reviews?section=flagged');assert.match(root.textContent,/No flagged requests awaiting approval/);
    app.history.back();await flush();assert.equal(root.querySelector('.nb-publication-review'),null);
    assert.match(app.location.hash,/section=flagged&note=flagged-one&revision=2/);
    await app.navigate('#reviews?section=flagged');assert.match(root.textContent,/No flagged requests awaiting approval/);
    assert.equal(app.requests.filter(row=>row.url.pathname.endsWith('/publication-reviews')).length,1);
    assert.ok(app.requests.filter(row=>row.url.pathname==='/api/notebook/notes/flagged-one').every(row=>row.url.searchParams.get('revision')==='2'));
  });
  console.log(`${passed} review-center frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

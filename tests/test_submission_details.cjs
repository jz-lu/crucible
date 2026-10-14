// Run with `node tests/test_submission_details.cjs`. All records are fixtures.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const owner = {id: 17, handle: 'owner'};
const profile = tier => ({id:'alpha-'+tier,instance_size:tier,name:'Fixture tier '+tier,parameters:{tier},correctness:{}});
const previous = {id:'fixture-alpha',version:'3',name:'Original Alpha',profiles:[profile(20),profile(30)],protocol:{}};
const scheme = {id:'fixture-alpha',version:'4',name:'Current Alpha',summary:'Fixture',educational:false,
  profiles:[profile(20),profile(30)],previous_releases:[previous],largest_recovered_profile_id:'alpha-30',next_profile_id:'alpha-20',protocol:{},
  first_recovery:{submission_id:'public-first',handle:'archived-solver',scheme_version:'3',profile_id:'alpha-30',received_at:100}};
const receipt = (overrides = {}) => ({id:'public-first',challenge_id:'original-challenge',handle:'archived-solver',status:'correct',
  scheme_id:scheme.id,scheme_version:'3',profile_id:'alpha-30',instance_size:30,received_at:100,elapsed_seconds:12,
  method:{id:'method-pinned-v1',name:'Pinned original method',pseudocode:'<script>literal_method_text()</script>\nUse the original immutable parameters.'},
  compute:{hardware:'Original CPU',workers:4,wall_seconds:2,precomputation:'Original preparation',peak_memory:'2 KiB',notes:'Original compute note'},...overrides});
const publicReceipt = receipt();
const privateReceipt = receipt({id:'owner-incorrect',challenge_id:'owner-challenge',handle:owner.handle,status:'incorrect',
  profile_id:'alpha-20',instance_size:20,method:{id:'private-method',name:'Private unsuccessful method',pseudocode:'PRIVATE_PSEUDOCODE'},
  compute:{hardware:'PRIVATE_HARDWARE',workers:1,wall_seconds:0.5,precomputation:'None',peak_memory:'Not measured',notes:'PRIVATE_NOTES'}});
const summary = item => ({id:item.id,scheme_id:item.scheme_id,scheme_version:item.scheme_version,profile_id:item.profile_id,
  method_name:item.method.name,received_at:item.received_at,status:item.status});
const deferred = () => {let resolve; const promise = new Promise(r=>{resolve=r;}); return {promise,resolve};};
async function boot({user=owner, hook, history=[summary(privateReceipt),summary(publicReceipt)]} = {}) {
  return createApp({schemes:[scheme],user,responder:async(url,options,envelope)=>{
    if (hook) {
      const overridden=await hook(url,options,envelope);
      if (overridden!==undefined) return overridden;
    }
    if (url.pathname==='/api/people/archived-solver') return envelope({person:{handle:'archived-solver',points:0,schemes_recovered:1,correct_submission_count:1,records:[],recent_recoveries:[{scheme_id:scheme.id,scheme_name:'Original Alpha',scheme_version:'3',tier:30,submission_id:'public-first',received_at:100,is_current:false}]}});
    if (url.pathname==='/api/me/submissions') return envelope({submissions:history});
    if (url.pathname==='/api/submissions/public-first') return envelope({submission:receipt()});
    if (url.pathname==='/api/submissions/owner-incorrect') return envelope({submission:receipt(privateReceipt)});
  }});
}
async function publicButton(app) {
  app.$('#kem-catalogue').querySelector('.kem-first-handle').click(); await flush();
  assert.equal(app.$('#person-workspace').hidden,false);
  return app.$('#person-content').querySelector('.history-item');
}
const detailCalls = app => app.requests.filter(r=>r.url.pathname.startsWith('/api/submissions/'));
const closeDetails = app => app.$('#recovery-dialog').querySelector('[data-close]').click();
const fact = (app,label) => app.$('#recovery-content').querySelectorAll('div').find(row=>row.querySelector('dt')?.textContent===label)?.querySelector('dd')?.textContent;
const assertReadOnly = app => assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
let passed=0;
async function test(name,run) {await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('catalogue profile links lead to an exact public receipt preserving original release and tier',async()=>{
    const app=await boot({user:null});
    const button=await publicButton(app);
    assert.equal(button.tagName,'BUTTON');
    assert.equal(button.type,'button');
    assert.equal(button.getAttribute('aria-haspopup'),'dialog');
    const hash=app.location.hash;
    button.focus();button.click();await flush();
    assert.equal(app.$('#recovery-dialog').open,true);
    assert.equal(detailCalls(app).length,1);
    assert.equal(detailCalls(app)[0].url.pathname,'/api/submissions/public-first');
    assert.equal(detailCalls(app)[0].options.method,'GET');
    assert.equal(app.location.hash,hash);
    assert.equal(app.$('#recovery-title').textContent,'Pinned original method');
    assert.match(app.$('#recovery-context').textContent,/archived-solver.*Original Alpha.*Tier 30.*version 3/);
    assert.doesNotMatch(app.$('#recovery-context').textContent,/Current Alpha|version 4|Tier 20/);
    assert.equal(app.$('#recovery-eyebrow').textContent,'Recorded recovery');
    assert.equal(fact(app,'Result'),'Recovered');
    assert.match(fact(app,'Time to submit'),/12(?:\.0)?s from issue to receipt/);
    assert.equal(fact(app,'Reported attack time'),'2.00s');
    assert.equal(fact(app,'Hardware'),'Original CPU');
    assert.equal(fact(app,'Parallel workers'),'4');
    assert.equal(fact(app,'Precomputation'),'Original preparation');
    assert.equal(fact(app,'Peak memory'),'2 KiB');
    assert.equal(fact(app,'Notes'),'Original compute note');
    assert.match(app.$('#recovery-content').querySelector('pre').textContent,/literal_method_text/);
    assert.equal(app.$('#recovery-content').querySelectorAll('script').length,0);
    closeDetails(app);await flush();
    assert.equal(app.document.activeElement===button,true,'Focus returns to the profile recovery');
    assertReadOnly(app);
  });
  await test('history entries are whole keyboard buttons and owner-only incorrect details keep history beneath',async()=>{
    const app=await boot();
    const historyTrigger=app.$('#history-button');
    historyTrigger.focus();historyTrigger.click();await flush();
    const history=app.$('#history-dialog');
    const row=app.$('#history-content').querySelector('.history-item');
    assert.equal(row.tagName,'BUTTON');
    assert.equal(row.type,'button');
    assert.equal(row.getAttribute('aria-haspopup'),'dialog');
    assert.match(row.querySelector('.history-item-title').textContent,/Original Alpha.*Tier 20/);
    row.focus();row.click();await flush();
    assert.equal(history.open,true);
    assert.equal(app.$('#recovery-dialog').open,true);
    assert.equal(detailCalls(app).at(-1).url.pathname,'/api/submissions/owner-incorrect');
    assert.equal(app.$('#recovery-eyebrow').textContent,'Recorded submission');
    assert.equal(fact(app,'Result'),'Incorrect');
    assert.match(app.$('#recovery-content').textContent,/PRIVATE_PSEUDOCODE.*PRIVATE_HARDWARE/);
    assert.doesNotMatch(app.$('#recovery-content').textContent,/accepted key/i);
    assert.equal(app.document.body.style.overflow,'hidden');
    closeDetails(app);await flush();
    assert.equal(history.open,true);
    assert.equal(app.document.body.style.overflow,'hidden');
    assert.equal(app.document.activeElement===row,true,'Focus returns to the history row');
    assert.equal(app.$('#recovery-content').textContent,'');
    history.querySelector('[data-close]').click();await flush();
    assert.equal(app.document.body.style.overflow,'');
    assert.equal(app.document.activeElement===historyTrigger,true,'Focus returns to My submissions');
    assert.equal(app.$('#history-content').textContent,'');
    assertReadOnly(app);
  });
  await test('missing and malformed detail responses replace previous content with a recoverable error',async()=>{
    const bad=[{}, {submission:null}, {submission:receipt({id:'wrong-id'})},
      {submission:receipt({status:'pending'})}, {submission:receipt({handle:null})},
      {submission:receipt({method:null})}, {submission:receipt({compute:null})}];
    for (const response of bad) {
      let failed=false;
      const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/submissions/public-first' && failed ? envelope(response) : undefined});
      const button=await publicButton(app);
      button.click();await flush();
      assert.match(app.$('#recovery-content').textContent,/literal_method_text/);
      closeDetails(app);failed=true;
      button.click();
      assert.doesNotMatch(app.$('#recovery-content').textContent,/literal_method_text/);
      await flush();
      assert.equal(app.$('#recovery-dialog').open,true);
      assert.match(app.$('#recovery-content').textContent,/submission details could not be loaded/i);
      assert.doesNotMatch(app.$('#recovery-content').textContent,/literal_method_text|Original CPU/);
      assert.equal(app.$('#recovery-content').querySelector('pre'),null);
      assert.equal(detailCalls(app).length,2,'Failures do not automatically refetch');
      assertReadOnly(app);
    }
  });
  await test('network failures clear stale detail data and retry only after an explicit click',async()=>{
    let failing=true;
    const app=await boot({hook:url=>{if(url.pathname==='/api/submissions/public-first' && failing)throw new Error('Simulated network outage');}});
    (await publicButton(app)).click();await flush();
    assert.match(app.$('#recovery-content').textContent,/could not be reached/i);
    assert.equal(detailCalls(app).length,1);
    assert.equal(app.$('#recovery-content').querySelector('pre'),null);
    failing=false;
    app.$('#recovery-content').querySelector('button').click();await flush();
    assert.equal(detailCalls(app).length,2);
    assert.equal(app.$('#recovery-title').textContent,'Pinned original method');
    assertReadOnly(app);
  });
  await test('closing a loading detail prevents a late response from reopening or restoring its contents',async()=>{
    const pending=deferred();
    const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/submissions/public-first' ? pending.promise.then(()=>envelope({submission:receipt()})) : undefined});
    (await publicButton(app)).click();await flush();
    assert.equal(app.$('#recovery-dialog').open,true);
    closeDetails(app);await flush();
    pending.resolve();await flush();
    assert.equal(app.$('#recovery-dialog').open,false);
    assert.equal(app.$('#recovery-content').textContent,'');
    assert.equal(app.$('#recovery-context').textContent,'');
    assert.equal(app.document.body.style.overflow,'');
  });
  await test('a reopened dialog cannot be overwritten by an earlier receipt response',async()=>{
    const pending=deferred();
    const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/submissions/public-first' ? pending.promise.then(()=>envelope({submission:receipt()})) : undefined});
    (await publicButton(app)).click();await flush();
    closeDetails(app);
    app.$('#history-button').click();await flush();
    app.$('#history-content').querySelector('.history-item').click();await flush();
    assert.equal(app.$('#recovery-title').textContent,'Private unsuccessful method');
    pending.resolve();await flush();
    assert.equal(app.$('#recovery-title').textContent,'Private unsuccessful method');
    assert.equal(fact(app,'Result'),'Incorrect');
  });
  await test('account changes discard pending detail fetches and clear both private dialogs',async()=>{
    for (const nextUser of [null,{id:99,handle:'another-account'}]) {
      const pending=deferred();
      const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/submissions/owner-incorrect' ? pending.promise.then(()=>envelope({submission:receipt(privateReceipt)})) : undefined});
      app.$('#history-button').click();await flush();
      app.$('#history-content').querySelector('.history-item').click();await flush();
      app.sandbox.setSession(app.envelope({user:nextUser,csrf_token:'new-session'}));
      pending.resolve();await flush();
      assert.equal(app.$('#history-dialog').open,false);
      assert.equal(app.$('#recovery-dialog').open,false);
      assert.equal(app.$('#history-content').textContent,'');
      assert.equal(app.$('#recovery-content').textContent,'');
      assert.equal(app.$('#recovery-context').textContent,'');
      assert.doesNotMatch(app.document.body.textContent,/PRIVATE_PSEUDOCODE|PRIVATE_HARDWARE|PRIVATE_NOTES/);
    }
  });
  await test('already rendered owner-only details are removed immediately on sign-out',async()=>{
    const app=await boot();
    app.$('#history-button').click();await flush();
    app.$('#history-content').querySelector('.history-item').click();await flush();
    assert.match(app.$('#recovery-content').textContent,/PRIVATE_PSEUDOCODE/);
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'signed-out'}));
    assert.equal(app.$('#history-dialog').open,false);
    assert.equal(app.$('#recovery-dialog').open,false);
    assert.doesNotMatch(app.document.body.textContent,/PRIVATE_PSEUDOCODE|PRIVATE_HARDWARE|PRIVATE_NOTES|Private unsuccessful method/);
    assert.equal(app.document.body.style.overflow,'');
  });
  await test('late history responses cannot populate a closed dialog or a different account',async()=>{
    for (const switchAccount of [false,true]) {
      const pending=deferred();
      const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/me/submissions' ? pending.promise.then(()=>envelope({submissions:[summary(privateReceipt)]})) : undefined});
      app.$('#history-button').click();await flush();
      if(switchAccount) app.sandbox.setSession(app.envelope({user:{id:99,handle:'other'},csrf_token:'other'}));
      else app.$('#history-dialog').close();
      pending.resolve();await flush();
      assert.equal(app.$('#history-dialog').open,false);
      assert.equal(app.$('#history-content').textContent,'');
      assert.doesNotMatch(app.document.body.textContent,/Private unsuccessful method/);
    }
  });
  console.log(`\n${passed} submission-detail regressions passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

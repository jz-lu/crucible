const assert=require('node:assert/strict');
const vm=require('node:vm');
const {createApp,flush}=require('./frontend-dom.cjs');
const user={id:'alice-id',handle:'alice'};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const release=(id='one',version='1')=>({id,version,name:id,profiles:[{id:'stage-1',instance_size:1,
  shared_secret_bytes:32,parameters:{},encoding:{shared_secret_bytes:32},correctness:{}}],
  protocol:{construction:'Synthetic fixed description'},profile_metrics:{},next_profile_id:'stage-1'});
const releases=[release('one'),release('two'),release('three')];
const compact=scheme=>{const copy={...scheme};delete copy.protocol;return copy;};
function notebook(calls,override) {return {mount(target,options){calls.push('mount');return {
  show:async()=>calls.push('show'),hide(){},setAccount(){},invalidateReads(){},
  warm:async kind=>{calls.push(kind);return override?override(kind,options):10;},
};}};}
async function appFixture({hash='#about',extra,values=releases,nb}={}) {
  const calls=[];
  const app=await createApp({schemes:values,user,hash,notebook:nb||notebook(calls),responder:(url,options,envelope)=>{
    const answer=extra?.(url,options,envelope);if(answer!==undefined)return answer;
    if(url.pathname==='/api/schemes') {
      if(url.searchParams.get('projection')==='release')return envelope({projection:'release',catalogue_revision:'r1',
        scheme:values.find(item=>item.id===url.searchParams.get('scheme_id')),current_version:'1'});
      return envelope({projection:'navigation',catalogue_revision:'r1',schemes:values.map(compact)});
    }
    if(url.pathname==='/api/people/alice')return envelope({person:{handle:'alice',points:0,records:[],recent_recoveries:[]}});
    if(url.pathname==='/api/people/bob')return envelope({person:{handle:'bob',points:0,records:[],recent_recoveries:[]}});
    if(url.pathname==='/api/people')return envelope({people:[{handle:'alice',points:0}],pagination:{limit:50,offset:0,has_more:false}});
    if(url.pathname==='/api/me/submissions')return envelope({submissions:[]});
  }});
  return {app,calls};
}
const step=app=>vm.runInContext('runBrowseWarmup()',app.sandbox);
async function drain(app,count=24){for(let i=0;i<count;i++){await step(app);await flush();}}
const reads=(app,path)=>app.requests.filter(row=>row.url.pathname===path);
let passed=0;async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('only admitted reviewers prepare two queue pages in separately budgeted idle jobs without showing the workspace',async()=>{
    const calls=[],held=deferred();
    const nb={mount(target,options){const prefix=options.reviewOnly?'review:':'notes:';calls.push(prefix+'mount');return {
      show:async()=>calls.push(prefix+'show'),hide(){},setAccount(){},invalidateReads(){},
      warm:async kind=>{calls.push(prefix+kind);return prefix==='review:'&&kind==='list'?held.promise:{bytes:10};},
    };}};
    const {app}=await appFixture({nb,extra:(url,options,envelope)=>url.pathname==='/api/session'?
      envelope({user,csrf_token:'review-session',capabilities:{review_protocol_proposals:true}}):undefined});
    await drain(app,3);const first=step(app);await flush();
    assert.deepEqual(calls,['notes:mount','notes:timeline','notes:list','review:mount','review:list']);
    await step(app);assert.equal(calls.includes('review:next'),false);
    assert.equal(app.$('#review-notebook-workspace').hidden,true);assert.equal(calls.some(value=>value.endsWith(':show')),false);
    held.resolve({bytes:100});await first;
    app.$('#auth-dialog').showModal();await step(app);assert.equal(calls.includes('review:next'),false);
    app.$('#auth-dialog').close();await step(app);assert.equal(calls.filter(value=>value==='review:next').length,1);
    await drain(app,30);assert.equal(calls.filter(value=>value==='review:list').length,1);assert.equal(calls.filter(value=>value==='review:next').length,1);
    const normal=await appFixture();await drain(normal.app);assert.equal(vm.runInContext('reviewNotebookWorkspace',normal.app.sandbox),null);
  });
  await test('the global warm byte bound and revoked review capability stop the second queue job',async()=>{
    for(const mode of ['budget','revoked']){
      const calls=[];
      const nb={mount(target,options){return {show:async()=>{},hide(){},setAccount(){},invalidateReads(){},warm:async kind=>{
        if(options.reviewOnly)calls.push(kind);
        return {bytes:options.reviewOnly&&kind==='list'&&mode==='budget'?4*1024*1024:0};
      }};}};
      const {app}=await appFixture({nb,extra:(url,options,envelope)=>url.pathname==='/api/session'?
        envelope({user,csrf_token:'review-session',capabilities:{review_protocol_proposals:true}}):undefined});
      await drain(app,4);assert.deepEqual(calls,['list']);
      if(mode==='revoked')app.sandbox.setSession(app.envelope({user,csrf_token:'review-session',capabilities:{review_protocol_proposals:false}}));
      await drain(app,30);assert.deepEqual(calls,['list']);
    }
  });
  await test('initial page paints before optional work, then one idle job runs at a time',async()=>{
    const held=deferred(),calls=[];
    const {app}=await appFixture({nb:notebook(calls,kind=>kind==='timeline'?held.promise:10)});
    assert.equal(app.$('#about-workspace').hidden,false);assert.deepEqual(calls,[]);
    assert.equal(app.requests.length,1,'Initial About needs only its session');
    await step(app);assert.deepEqual(calls,['mount']);
    const pending=step(app);await flush();assert.deepEqual(calls,['mount','timeline']);
    await step(app);assert.deepEqual(calls,['mount','timeline'],'A second scheduler cannot start another job');
    await app.navigate('#people');await flush();
    assert.equal(reads(app,'/api/people').length,1,'Foreground reads do not wait behind optional Timeline');
    held.resolve(20);await pending;await step(app);
    assert.deepEqual(calls,['mount','timeline','list']);assert.equal(calls.includes('show'),false);
  });
  await test('no warmups before access, after revocation, with saveData or in a hidden tab',async()=>{
    const {app,calls}=await appFixture();
    app.document.hidden=true;await step(app);assert.deepEqual(calls,[]);
    app.document.hidden=false;app.sandbox.navigator.connection={saveData:true};await step(app);assert.deepEqual(calls,[]);
    app.sandbox.navigator.connection.saveData=false;
    app.sandbox.setSession(app.envelope({user,csrf_token:'gone',access:{mode:'beta',allowed:false,status:'revoked'}}));
    await step(app);assert.deepEqual(calls,[]);
    const anon=await createApp({schemes:releases,responder:(url,options,envelope)=>url.pathname==='/api/session'?
      envelope({user:null,access:{mode:'beta',allowed:false,status:'anonymous'}}):undefined});
    await step(anon);assert.equal(anon.requests.length,1);
  });
  await test('foreground pending reads and open dialogs defer optional jobs without blocking them',async()=>{
    const held=deferred(),{app,calls}=await appFixture({extra:url=>url.pathname==='/api/people'?held.promise:undefined});
    await app.navigate('#people');await step(app);assert.deepEqual(calls,[]);
    held.resolve(app.envelope({people:[]}));await flush();
    app.$('#auth-dialog').showModal();await step(app);assert.deepEqual(calls,[]);
    app.$('#auth-dialog').close();await step(app);assert.deepEqual(calls,['mount']);
  });
  await test('only current releases warm, while unselected historical timing reports are not fetched',async()=>{
    const values=[{...release('one'),previous_releases:[release('one','old')]},release('two'),release('three')];
    const {app,calls}=await appFixture({hash:'#scheme/two/1',values});
    await drain(app);
    const detail=app.requests.filter(row=>row.url.searchParams.get('projection')==='release');
    assert.deepEqual(detail.map(row=>row.url.searchParams.get('scheme_id')),['two','one','three']);
    assert.equal(detail.some(row=>row.url.searchParams.get('version')==='old'),false);
    assert.equal(reads(app,'/api/protocol/benchmarks').length,0);
    assert.deepEqual(calls,['mount','timeline','list','map']);
    assert.equal(app.$('#overview-workspace').hidden,false);assert.equal(app.$('#notebook-workspace').hidden,true);
    assert.equal(app.requests.some(row=>row.options.method==='POST'||row.url.pathname==='/api/challenges'||row.url.pathname.startsWith('/api/submissions/')),false);
  });
  await test('warm profile shares a foreground request and recent results are consumed once without a duplicate GET',async()=>{
    const held=deferred(),{app}=await appFixture({extra:url=>url.pathname==='/api/people/alice'?held.promise:undefined});
    // Static setup, timeline, index, navigation, first release, skipped legacy timing.
    await drain(app,6);const warming=step(app);await flush();
    assert.equal(reads(app,'/api/people/alice').length,1);
    await app.navigate('#person/alice');assert.equal(reads(app,'/api/people/alice').length,1);
    held.resolve(app.envelope({person:{handle:'alice',points:7,records:[],recent_recoveries:[]}}));await warming;await flush();
    assert.match(app.$('#person-content').textContent,/7/);
    await app.navigate('#about');await app.navigate('#person/alice');
    assert.equal(reads(app,'/api/people/alice').length,1);
    await app.navigate('#about');await app.navigate('#person/alice');
    assert.equal(reads(app,'/api/people/alice').length,2,'The recent warm response is consumed only once');
  });
  await test('an expanded catalogue still warms at most eight current releases and24 jobs, prioritizing the viewed one',async()=>{
    const values=Array.from({length:9},(_,i)=>release('release-'+(i+1)));
    const {app}=await appFixture({hash:'#scheme/release-9/1',values});
    await drain(app,40);
    const details=app.requests.filter(row=>row.url.searchParams.get('projection')==='release').map(row=>row.url.searchParams.get('scheme_id'));
    assert.deepEqual(details,['release-9',...Array.from({length:7},(_,i)=>'release-'+(i+1))]);
    assert.equal(reads(app,'/api/protocol/benchmarks').length,0);
    assert.equal(vm.runInContext('browseWarmState.done.size',app.sandbox),24);
    assert.equal(vm.runInContext('browseWarmState.done.has(\'release:["release-8","1"]\')',app.sandbox),false);
  });
  await test('same-account CSRF rotation and a new account cannot retain an old warm result',async()=>{
    for(const next of [{...user},{id:'bob-id',handle:'bob'}]) {
      const held=deferred(),{app}=await appFixture({extra:url=>url.pathname==='/api/people/alice'?held.promise:undefined});
      await drain(app,6);const warming=step(app);await flush();
      app.sandbox.setSession(app.envelope({user:next,csrf_token:'rotated',access:{mode:'beta',allowed:true,status:'active'}}));
      held.resolve(app.envelope({person:{handle:'alice',points:9999,records:[],recent_recoveries:[]}}));await warming;await flush();
      assert.equal(vm.runInContext("cachedRead('person:alice')",app.sandbox),null);
      assert.equal(vm.runInContext("warmedReads.has('person:alice')",app.sandbox),false);
      assert.doesNotMatch(app.$('#person-content').textContent,/9999/);
    }
  });
  await test('failed or malformed optional responses never replace the current view or retry endlessly',async()=>{
    const {app}=await appFixture({extra:url=>url.pathname==='/api/people/alice'?Promise.reject(new Error('fixture failure')):
      url.pathname==='/api/people'?{people:'invalid'}:undefined});
    await drain(app,30);const count=app.requests.length;await drain(app,30);
    assert.equal(app.requests.length,count);assert.equal(app.$('#about-workspace').hidden,false);
    assert.equal(app.$('#global-message').hidden,true);assert.equal(vm.runInContext("cachedRead('people')",app.sandbox),null);
  });
  await test('response accounting and job cap stop remaining warmups without claiming them complete',async()=>{
    const calls=[],{app}=await appFixture({nb:notebook(calls,kind=>({bytes:kind==='timeline'?3.5*1024*1024:512*1024}))});
    await drain(app,30);assert.deepEqual(calls,['mount','timeline','list']);
    assert.equal(vm.runInContext('browseWarmState.bytes',app.sandbox),4*1024*1024);
    assert.equal(vm.runInContext('browseWarmState.done.has("navigation")',app.sandbox),false);
    const second=await appFixture();await step(second.app);
    vm.runInContext('browseWarmState.done = new Set(Array.from({length:24},(_,i)=>"fixture:"+i))',second.app.sandbox);
    await step(second.app);assert.deepEqual(second.calls,['mount']);
  });
  await test('format validation retains warmed reads and CSRF while a confirmed save still invalidates them',async()=>{
    const {app}=await appFixture({extra:(url,options,envelope)=>{
      if(url.pathname==='/api/protocol/proposals/validate')return envelope({specification:{name:'Format-only fixture'}});
      if(url.pathname==='/api/notebook/notes'&&options.method==='POST')return envelope({note:{id:'saved-fixture'}});
    }});
    await drain(app);const before=reads(app,'/api/people/alice').length;
    const epoch=vm.runInContext('readViewEpoch',app.sandbox);
    await app.sandbox.api('/api/protocol/proposals/validate',{specification:{name:'Format-only fixture'}});
    const check=reads(app,'/api/protocol/proposals/validate').at(-1);
    assert.equal(check.options.headers['X-CSRF-Token'],'test-csrf');assert.equal(check.options.credentials,'same-origin');assert.equal(check.options.cache,'no-store');
    assert.equal(vm.runInContext('readViewEpoch',app.sandbox),epoch);
    await app.navigate('#person/alice');assert.equal(reads(app,'/api/people/alice').length,before,'The completed warm read survives format checking');
    await app.sandbox.api('/api/notebook/notes',{content:{title:'Saved fixture'}});
    assert.ok(vm.runInContext('readViewEpoch',app.sandbox)>epoch);assert.equal(vm.runInContext("cachedRead('person:alice')",app.sandbox),null);
    await app.navigate('#about');await app.navigate('#person/alice');assert.equal(reads(app,'/api/people/alice').length,before+1);
  });
  await test('validation exemption is exact-path POST only and cannot bypass access or session invalidation',async()=>{
    const {app}=await appFixture({extra:(url,options,envelope)=>url.pathname.startsWith('/api/protocol/proposals/validate')?envelope({specification:{}}):undefined});
    for(const [path,method] of [['/api/protocol/proposals/validate?extra=1','POST'],['/api/protocol/proposals/validate/','POST'],['/api/protocol/proposals/validate','PATCH']]){
      const epoch=vm.runInContext('readViewEpoch',app.sandbox);await app.sandbox.api(path,{specification:{}},{method});
      assert.ok(vm.runInContext('readViewEpoch',app.sandbox)>epoch);
    }
    await drain(app);app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));
    assert.equal(vm.runInContext("cachedRead('person:alice')",app.sandbox),null);
    app.sandbox.setSession(app.envelope({user,csrf_token:'denied',access:{mode:'beta',allowed:false,status:'revoked'}}));
    const before=app.requests.length;
    await assert.rejects(app.sandbox.api('/api/protocol/proposals/validate',{specification:{}}),error=>error.status===403);
    assert.equal(app.requests.length,before);
  });
  await test('a rotated timing report cannot refill the old reference cache',async()=>{
    const held=deferred(),{app}=await appFixture({hash:'#scheme/one/1',extra:url=>url.pathname==='/api/protocol/benchmarks'?held.promise:undefined});
    const report=app.$('#overview-chart-computation').querySelector('select[aria-label="Report"]');
    report.value='legacy';report.dispatchEvent({type:'change'});await flush();
    app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));
    held.resolve(app.envelope({method:{id:'fixture',version:1},report:null}));await flush();
    assert.equal(vm.runInContext('referenceTimings.size',app.sandbox),0);
  });
  console.log(passed+' bounded browse warmup checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

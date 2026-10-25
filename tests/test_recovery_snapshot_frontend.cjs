const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id:'owner', handle:'alice'};
const profiles = [1,2,3].map(size => ({id:'stage-'+size, instance_size:size, shared_secret_bytes:32,
  parameters:{}, encoding:{shared_secret_bytes:32}, correctness:{}}));
const winner = (extra={}) => ({rank:1, submission_id:'receipt-1', handle:'bob', profile_id:'stage-2',
  scheme_id:'fixture', scheme_version:'1', instance_size:2, is_inherited:true,
  received_at:1700000000, method:{name:'Exact fixture method'}, ...extra});
function scheme() {
  return {id:'fixture', version:'1', name:'Fixture', profiles, protocol:{}, profile_metrics:{},
    participant_count:1, recovered_profile_ids:['stage-2'], largest_recovered_profile_id:'stage-2',
    next_profile_id:'stage-3', first_recovery:null, profile_recoveries:[],
    recovery_summary:{format:'crucible-recovery-summary-v1', complete:true, scheme_id:'fixture', scheme_version:'1', profiles:{
      'stage-1':{winner:winner(), includes_compatible_versions:[]},
      'stage-2':{winner:winner({is_inherited:false}), includes_compatible_versions:[]},
      'stage-3':{winner:null, includes_compatible_versions:[]}}}};
}
const route = id => '#challenge/fixture/1?profile=stage-'+id+'&view=leaderboard';
const deferred = () => {let resolve, reject; const promise=new Promise((yes,no)=>{resolve=yes;reject=no;}); return {promise,resolve,reject};};
const reads = (app,path) => app.requests.filter(r=>r.url.pathname===path);
const result = app => app.$('#leaderboard-content').querySelector('button');
const progress = release => ({projection:'progress', catalogue_revision:'stable', releases:[release]});
function projected(release, responder) {
  return createApp({schemes:[release],user,hash:route(1),responder:(url,options,envelope)=>{
    const answer=responder?.(url,options,envelope); if(answer!==undefined)return answer;
    if(url.pathname==='/api/schemes') return envelope(url.searchParams.get('projection')==='release' ?
      {projection:'release',catalogue_revision:'stable',scheme:release,current_version:'1'} :
      {projection:'navigation',catalogue_revision:'stable',schemes:[release]});
  }});
}
let passed=0;
async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('complete snapshot paints solved and unsolved stages immediately without per-stage reads',async()=>{
    const app=await createApp({schemes:[scheme()],user,hash:route(1)});
    assert.equal(app.$('#leaderboard-title').textContent,'Broken by');
    assert.match(app.$('#leaderboard-content').textContent,/bob.*Via Stage 2.*Exact fixture method/);
    await app.navigate(route(2));
    assert.equal(app.$('#leaderboard-title').textContent,'Broken by');
    assert.doesNotMatch(app.$('#leaderboard-content').textContent,/Via Stage/);
    await app.navigate(route(3)); assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');
    await app.navigate(route(1)); assert.equal(reads(app,'/api/leaderboard').length,0);
    assert.equal(app.requests.some(r=>r.url.pathname.startsWith('/api/submissions/')),false);
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('compatible-source identity and receipt details remain original and fetched only on click',async()=>{
    const release=scheme();
    release.recovery_summary.profiles['stage-1']={winner:winner({scheme_version:'old'}),includes_compatible_versions:['old']};
    const app=await createApp({schemes:[release],user,hash:route(1),responder:(url,options,envelope)=>{
      if(url.pathname==='/api/submissions/receipt-1') return envelope({submission:{...winner({scheme_version:'old'}),id:'receipt-1',
        status:'correct',method:{name:'Exact fixture method',pseudocode:'Detail only'},compute:{}}});
    }});
    assert.match(app.$('#leaderboard-content').textContent,/Release old/);
    assert.match(app.$('#leaderboard-scope').textContent,/version old/);
    assert.equal(reads(app,'/api/submissions/receipt-1').length,0);
    result(app).click();await flush();
    assert.equal(reads(app,'/api/submissions/receipt-1').length,1);
    assert.match(app.$('#recovery-content').textContent,/Detail only/);
  });
  const invalids = {
    absent:r=>delete r.recovery_summary,
    version:r=>r.recovery_summary.format='future',
    incomplete:r=>r.recovery_summary.complete=false,
    missingStage:r=>delete r.recovery_summary.profiles['stage-3'],
    extraStage:r=>r.recovery_summary.profiles.foreign={winner:null,includes_compatible_versions:[]},
    wrongRelease:r=>r.recovery_summary.scheme_version='old',
    wrongScheme:r=>r.recovery_summary.scheme_id='another',
    badWinner:r=>r.recovery_summary.profiles['stage-3'].winner={},
    unknownSource:r=>r.recovery_summary.profiles['stage-1'].winner.scheme_version='unlisted',
    badInheritance:r=>r.recovery_summary.profiles['stage-1'].winner.is_inherited=false,
    namedInheritance:r=>r.selection_kind='parameter-set',
    nonInheriting:r=>r.record_inheritance=false,
  };
  for(const [name,mutate] of Object.entries(invalids)) await test(name+' snapshot falls back without inventing an unsolved state',async()=>{
    const release=scheme();mutate(release);const held=deferred();
    const app=await createApp({schemes:[release],user,hash:route(1),responder:url=>url.pathname==='/api/leaderboard'?held.promise:undefined});
    assert.equal(reads(app,'/api/leaderboard').length,1);
    assert.notEqual(app.$('#leaderboard-title').textContent,'Unsolved');
    held.resolve(app.envelope({entries:[]}));await flush();
    assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');
  });
  await test('named and non-inheriting exact winners are valid snapshots',async()=>{
    for(const policy of [{selection_kind:'parameter-set'},{record_inheritance:false}]) {
      const release={...scheme(),...policy};release.recovery_summary.profiles['stage-1'].winner=null;
      const app=await createApp({schemes:[release],user,hash:route(2)});
      assert.equal(app.$('#leaderboard-title').textContent,'Broken by');
      assert.equal(reads(app,'/api/leaderboard').length,0);
    }
  });
  await test('explicit refresh shares one progress read, keeps current content and replaces all stage entries',async()=>{
    const held=deferred(), release=scheme();
    const app=await projected(release,url=>url.searchParams.has('progress_only')?held.promise:undefined);
    const button=app.$('#refresh-leaderboard');button.focus();button.click();button.click();await flush();
    assert.equal(app.requests.filter(r=>r.url.searchParams.has('progress_only')).length,1);
    assert.equal(app.$('#leaderboard-title').textContent,'Broken by');assert.equal(app.document.activeElement,button);
    const fresh=scheme();fresh.recovery_summary.profiles['stage-1'].winner=winner({handle:'carol',submission_id:'receipt-2'});
    fresh.recovery_summary.profiles['stage-3'].winner=winner({handle:'carol',submission_id:'receipt-3',profile_id:'stage-3',instance_size:3,is_inherited:false});
    held.resolve(app.envelope(progress(fresh)));await flush();
    assert.match(app.$('#leaderboard-content').textContent,/carol/);assert.equal(button.disabled,false);
    await app.navigate(route(3));assert.match(app.$('#leaderboard-content').textContent,/carol/);
    assert.equal(reads(app,'/api/leaderboard').length,0);
    assert.equal(app.requests.filter(r=>r.url.searchParams.get('projection')==='release').length,1);
  });
  await test('failed snapshot refresh preserves the record, focus, and a truthful retryable error',async()=>{
    const held=deferred(),app=await projected(scheme(),url=>url.searchParams.has('progress_only')?held.promise:undefined);
    const content=app.$('#leaderboard-content').firstChild,button=app.$('#refresh-leaderboard');
    button.focus();button.click();await flush();held.reject(new TypeError('synthetic disconnected'));await flush();
    assert.equal(app.$('#leaderboard-content').firstChild,content);assert.equal(app.document.activeElement,button);
    assert.equal(button.disabled,false);assert.match(app.$('#toast').textContent,/refresh failed/i);
    assert.equal(reads(app,'/api/leaderboard').length,0);
  });
  await test('polling incorporates a changed winner even when old recovery hints are identical and keeps result focus',async()=>{
    let fresh=scheme();const app=await projected(scheme(),url=>url.searchParams.has('progress_only')?app.envelope(progress(fresh)):undefined);
    const original=result(app);original.focus();
    fresh.recovery_summary.profiles['stage-1'].winner.method.name='Updated public method title';
    await vm.runInContext('synchronizeSession()',app.sandbox);await flush();
    assert.match(app.$('#leaderboard-content').textContent,/Updated public method title/);
    assert.notEqual(result(app),original);assert.equal(app.document.activeElement,result(app));
    assert.equal(reads(app,'/api/leaderboard').length,0);
  });
  await test('progress missing a summary removes it from cached release detail and uses legacy fallback',async()=>{
    const fresh=scheme();delete fresh.recovery_summary;
    const app=await projected(scheme(),(url,options,envelope)=>url.searchParams.has('progress_only')?envelope(progress(fresh)):undefined);
    app.$('#refresh-leaderboard').click();await flush();
    assert.equal(reads(app,'/api/leaderboard').length,1);assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');
  });
  await test('post-recovery navigation refresh adds no duplicate legacy leaderboard read',async()=>{
    const release=scheme();delete release.recovery_summary;
    const app=await createApp({schemes:[release],user,hash:route(1)});
    assert.equal(reads(app,'/api/leaderboard').length,1);
    await vm.runInContext('refreshRecoveryViews()',app.sandbox);await flush();
    assert.equal(reads(app,'/api/leaderboard').length,1);
  });
  await test('a replaced winner transfers removed result focus to the stable Record tab during refresh',async()=>{
    const held=deferred(),app=await projected(scheme(),url=>url.searchParams.has('progress_only')?held.promise:undefined);
    result(app).focus();const task=vm.runInContext('refreshLeaderboard()',app.sandbox);await flush();
    const fresh=scheme();fresh.recovery_summary.profiles['stage-1'].winner=null;
    held.resolve(app.envelope(progress(fresh)));await task;await flush();
    assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');
    assert.equal(app.document.activeElement,app.$('#tab-leaderboard'));
  });
  await test('refreshing an open result dialog rebinds its return target without moving dialog focus',async()=>{
    const held=deferred(),app=await projected(scheme(),(url,options,envelope)=>{
      if(url.searchParams.has('progress_only'))return held.promise;
      if(url.pathname==='/api/submissions/receipt-1')return envelope({submission:{...winner(),id:'receipt-1',status:'correct',compute:{}}});
    });
    const old=result(app);old.click();await flush();
    const close=app.$('#recovery-dialog').querySelector('button');close.focus();
    const task=vm.runInContext('refreshLeaderboard()',app.sandbox);await flush();
    const fresh=scheme();fresh.recovery_summary.profiles['stage-1'].winner.method.name='Updated label';
    held.resolve(app.envelope(progress(fresh)));await task;await flush();
    assert.equal(app.document.activeElement,close);assert.notEqual(result(app),old);
    assert.equal(vm.runInContext('dialogTriggers.get(recoveryDialog)',app.sandbox),result(app));
  });
  await test('late refresh after selection changes does not paint the old winner or disable the new controls',async()=>{
    const held=deferred(),app=await projected(scheme(),url=>url.searchParams.has('progress_only')?held.promise:undefined);
    app.$('#refresh-leaderboard').click();await flush();await app.navigate(route(3));
    assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');assert.equal(app.$('#refresh-leaderboard').disabled,false);
    held.resolve(app.envelope(progress(scheme())));await flush();
    assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');assert.equal(app.$('#refresh-leaderboard').disabled,false);
  });
  await test('revocation discards a late refresh and clears the visible recovery',async()=>{
    const held=deferred(),app=await projected(scheme(),url=>url.searchParams.has('progress_only')?held.promise:undefined);
    app.$('#refresh-leaderboard').click();await flush();
    app.sandbox.setSession(app.envelope({user,csrf_token:'revoked',access:{mode:'beta',allowed:false,status:'revoked'}}));
    held.resolve(app.envelope(progress(scheme())));await flush();
    assert.equal(app.$('#access-workspace').hidden,false);assert.equal(app.$('#leaderboard-content').textContent,'');
    assert.equal(reads(app,'/api/leaderboard').length,0);
  });
  console.log(passed+' recovery snapshot checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

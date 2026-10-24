const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id:'alice',handle:'alice'};
const yes = {admitted:true,reason:null}, no = {admitted:false,reason:'release_not_admitted'};
const profiles = ['stage-1','stage-2'].map((id,index)=>({id,instance_size:index+1,parameter_bits:index+1,stage_number:index+1,
  stage_label:'Stage '+(index+1),shared_secret_bytes:32,encoding:{shared_secret_bytes:32},parameters:{},correctness:{}}));
const release = admission => ({id:'fixture',version:'2',name:'Fixture KEM',profiles,protocol:{construction:'Fixture'},
  next_profile_id:'stage-1',size_parameter:{meaning:'Fixture'},generation_admission:admission});
const route = (view='leaderboard',profile='stage-1',version='2')=>`#challenge/fixture/${version}?profile=${profile}&view=${view}`;
const postCount = app=>app.requests.filter(r=>r.url.pathname==='/api/challenges'&&r.options.method==='POST').length;
const reads = app=>app.requests.filter(r=>r.url.pathname==='/api/challenges'&&r.options.method!=='POST');
function challenge(envelope, profile='stage-1') {
  const clock=envelope({});return {id:'existing',scheme_id:'fixture',scheme_version:'2',profile_id:profile,round_id:clock.round.id,
    issued_at:clock.server_time,ends_at:clock.round.ends_at,status:'open',public:{encoding:{shared_secret_bytes:32}}};
}
async function boot({admission=no,exact=admission,view='leaderboard',existing=false,started=false,extra,schemeOverride}={}) {
  let current=admission;
  const app=await createApp({hash:route(view),responder:(url,options,envelope)=>{
    const override=extra?.(url,options,envelope);if(override!==undefined)return override;
    if(url.pathname==='/api/session')return envelope({user,csrf_token:'csrf',access:{mode:'beta',status:'active',allowed:true}});
    if(url.pathname==='/api/schemes')return envelope({schemes:[schemeOverride||release(current)]});
    if(url.pathname==='/api/challenges'){
      if(options.method==='POST')return envelope({challenge:challenge(envelope,JSON.parse(options.body).profile_id)});
      return envelope({challenge:existing?challenge(envelope,url.searchParams.get('profile_id')):null,generation_admission:exact,
        round_started:started,round_started_round_id:started?envelope({}).round.id:null});
    }
  }});
  return {...app,setAdmission:value=>{current=value;}};
}
let count=0;
async function test(name,run){await run();console.log('PASS '+name);count++;}
(async()=>{
  await test('disabled releases explain availability before the record action without an extra read',async()=>{
    const app=await boot();
    assert.match(app.$('#leaderboard-content').textContent,/not enabled in this beta/);
    assert.equal(app.$('.tier-record-challenge').textContent,'View existing challenges →');assert.equal(reads(app).length,0);
    app.$('.tier-record-challenge').click();await flush();
    assert.equal(reads(app).length,1);assert.equal(postCount(app),0);assert.equal(app.$('#issue-button').disabled,true);
    assert.equal(app.$('#empty-title').textContent,'Challenges not enabled');assert.equal(app.$('#challenge-error').hidden,true);
    assert.equal(app.$('#scheme-clock').hidden,true);assert.equal(app.$('#challenge-instance-rule').hidden,true);
  });
  await test('admitted release keeps the deliberate one-click issue path',async()=>{
    const app=await boot({admission:yes});app.$('.tier-record-challenge').click();await flush();
    assert.equal(reads(app).length,1);assert.equal(postCount(app),1);assert.equal(app.$('#challenge-active').hidden,false);
  });
  await test('exact profile denial overrides stale release permission and cancels explicit intent',async()=>{
    const app=await boot({admission:yes,exact:no});app.$('.tier-record-challenge').click();await flush();
    assert.equal(postCount(app),0);assert.equal(app.$('#issue-button').disabled,true);
    assert.equal(vm.runInContext('state.challengeStartIntent',app.sandbox),null);
    await app.sandbox.issueChallenge();assert.equal(postCount(app),0,'Programmatic retry uses the same denial');
    await app.navigate(route());assert.match(app.$('.tier-record-challenge').textContent,/View existing/);
  });
  await test('mixed development release defers to the exact profile without guessing eligibility',async()=>{
    const app=await boot({admission:{admitted:null,reason:'profile_specific'},exact:no});
    assert.match(app.$('.tier-record-challenge').textContent,/Take on/);app.$('.tier-record-challenge').click();await flush();
    assert.equal(reads(app).length,1);assert.equal(postCount(app),0);assert.equal(app.$('#issue-button').disabled,true);
  });
  await test('an already-started round cannot automatically issue an unadmitted profile',async()=>{
    const app=await boot({view:'challenge',admission:yes,exact:no,started:true});
    assert.equal(postCount(app),0);assert.equal(app.$('#challenge-error').hidden,true);
  });
  await test('existing issued instances retain download, agent and submission controls',async()=>{
    const app=await boot({view:'challenge',existing:true});
    assert.equal(app.$('#challenge-active').hidden,false);assert.equal(app.$('#manual-instance-tools').hidden,false);
    assert.equal(app.$('#use-agent').disabled,false);assert.equal(app.$('#open-submission').disabled,false);
    assert.equal(app.$('#download-button').href,'/api/challenges/existing/download');assert.equal(postCount(app),0);
    assert.equal(app.$('#scheme-clock').hidden,false);
  });
  await test('a late denied profile response cannot disable the next selected profile',async()=>{
    let finish;
    const pending=new Promise(resolve=>{finish=resolve;});
    const app=await boot({view:'challenge',admission:yes,extra:(url)=>url.pathname==='/api/challenges'&&url.searchParams.get('profile_id')==='stage-1'?pending:undefined});
    await app.navigate(route('challenge','stage-2'));assert.equal(app.$('#issue-button').disabled,false);
    finish(app.envelope({challenge:null,generation_admission:no}));await flush();
    assert.equal(app.$('#issue-button').disabled,false);assert.equal(app.$('#empty-title').textContent,'Ready?');
  });
  await test('a normal catalogue refresh updates visible controls without another challenge read',async()=>{
    const app=await boot({admission:yes});const oldReads=reads(app).length;
    app.setAdmission(no);await app.sandbox.refreshProfiles({background:true});await flush();
    assert.equal(reads(app).length,oldReads);assert.match(app.$('.tier-record-challenge').textContent,/View existing/);
    assert.doesNotMatch(app.$('#leaderboard-content').textContent,/Set the first record/);
    app.setAdmission(yes);await app.sandbox.refreshProfiles({background:true});await flush();
    assert.match(app.$('.tier-record-challenge').textContent,/Take on/);
  });
  await test('compact progress refresh propagates settings changes into cached release detail',async()=>{
    let admission=yes;const progress=()=>({participant_count:0,recovered_profile_ids:[],largest_recovered_profile_id:null,next_profile_id:'stage-1',first_recovery:null,generation_admission:admission});
    const app=await boot({admission:yes,extra:(url,options,envelope)=>{
      if(url.pathname!=='/api/schemes')return undefined;
      const scheme={...release(admission),...progress()};
      if(url.searchParams.has('progress_only'))return envelope({projection:'progress',catalogue_revision:'fixed',releases:[{id:'fixture',version:'2',...progress()}]});
      if(url.searchParams.get('projection')==='release')return envelope({projection:'release',catalogue_revision:'fixed',scheme,current_version:'2'});
      delete scheme.protocol;return envelope({projection:'navigation',catalogue_revision:'fixed',schemes:[scheme]});
    }});
    const details=()=>app.requests.filter(r=>r.url.searchParams.get('projection')==='release').length;
    assert.equal(details(),1);assert.match(app.$('.tier-record-challenge').textContent,/Take on/);
    admission=no;await app.sandbox.refreshProfiles({background:true});await flush();
    assert.equal(details(),1);assert.equal(reads(app).length,0);assert.match(app.$('.tier-record-challenge').textContent,/View existing/);
    assert.equal(vm.runInContext('selectedScheme().generation_admission.admitted',app.sandbox),false);
    admission=yes;await app.sandbox.refreshProfiles({background:true});await flush();assert.match(app.$('.tier-record-challenge').textContent,/Take on/);
  });
  await test('archived releases keep history and a working route to the current challenge',async()=>{
    const archived={admitted:false,reason:'archived_release'};
    const scheme={...release(yes),previous_releases:[{...release(archived),version:'1'}]};
    const app=await boot({admission:yes,schemeOverride:scheme,extra:(url,options,envelope)=>url.pathname==='/api/challenges'&&url.searchParams.get('scheme_version')==='1'?envelope({challenge:null,generation_admission:archived}):undefined});
    await app.navigate(route('challenge','stage-1','1'));
    assert.equal(app.$('#empty-title').textContent,'Archived release');assert.equal(app.$('#issue-button').disabled,false);
    app.$('#issue-button').click();await flush();assert.match(app.location.hash,/#challenge\/fixture\/2/);assert.equal(postCount(app),0);
  });
  await test('overview remains browsable and carries the same disabled-release message',async()=>{
    const app=await boot();await app.navigate('#scheme/fixture/2');
    assert.equal(app.$('#overview-generation-status').hidden,false);assert.match(app.$('#overview-generation-status').textContent,/not enabled/);
    assert.equal(app.$('#overview-challenges').hidden,false);assert.equal(reads(app).length,0);
  });
  await test('malformed explicit exact admission fails closed rather than authorizing a POST',async()=>{
    const app=await boot({admission:yes,exact:{admitted:'true'},view:'challenge',started:true});
    assert.equal(postCount(app),0);assert.equal(app.$('#issue-button').disabled,true);
  });
  console.log(`${count} challenge availability checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

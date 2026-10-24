const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id:'owner', handle:'alice'};
const profile = {id:'stage-1', instance_size:1, shared_secret_bytes:32, parameters:{}, encoding:{shared_secret_bytes:32}, correctness:{}};
const scheme = {id:'fixture', version:'1', name:'Fixture', profiles:[profile], next_profile_id:profile.id, protocol:{}, profile_metrics:{}};
const route = '#challenge/fixture/1?profile=stage-1&view=challenge';
const deferred = () => {let resolve; const promise = new Promise(done => {resolve=done;}); return {promise,resolve};};
const summary = app => ({id:'instance', profile_id:profile.id, scheme_id:scheme.id, scheme_version:scheme.version,
  round_id:app.envelope({}).round.id, issued_at:app.envelope({}).server_time, ends_at:app.envelope({}).round.ends_at,
  status:'open', projection:'summary', shared_secret_bytes:32, download_url:'/api/challenges/instance/download'});
const challengeReads = app => app.requests.filter(r => r.url.pathname === '/api/challenges' && r.options.method === 'GET');
let passed=0;
async function test(name, run) {await run(); passed++; console.log('PASS '+name);}
(async () => {
  await test('a cold challenge read overlaps both navigation and exact description, then is consumed once', async () => {
    const navigation=deferred(), description=deferred(), challenge=deferred();
    const app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope) => {
      if(url.pathname==='/api/schemes')return url.searchParams.get('projection')==='release'?description.promise:navigation.promise;
      if(url.pathname==='/api/challenges')return challenge.promise;
    }});
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/schemes').length,2);
    assert.equal(challengeReads(app).length,1,'Read begins before either description is ready');
    assert.equal(challengeReads(app)[0].url.searchParams.get('summary'),'true');
    challenge.resolve(app.envelope({challenge:summary(app),round_started:true,round_started_round_id:app.envelope({}).round.id}));
    navigation.resolve(app.envelope({projection:'navigation',catalogue_revision:'stable',schemes:[{...scheme,protocol:undefined}]}));
    await flush();assert.equal(app.$('#challenge-active').hidden,true,'Description is still pending');
    description.resolve(app.envelope({projection:'release',catalogue_revision:'stable',scheme,current_version:'1'}));
    await flush();assert.equal(challengeReads(app).length,1);
    assert.equal(app.$('#challenge-active').hidden,false);
    assert.match(app.$('#target-key').textContent,/32-byte key/);
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('a prefetched challenge cannot start a round or appear after leaving its route', async () => {
    const challenge=deferred();
    const app=await createApp({schemes:[scheme],user,hash:route,responder:url=>url.pathname==='/api/challenges'?challenge.promise:undefined});
    await app.navigate('#about');
    challenge.resolve(app.envelope({challenge:null,round_started:true,round_started_round_id:app.envelope({}).round.id}));
    await flush();assert.equal(app.$('#about-workspace').hidden,false);
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('an explicit instance request asks for a summary and retains key length and download controls', async () => {
    let app;
    app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/challenges' && options.method==='POST')return envelope({challenge:summary(app),round_started:true,round_started_round_id:app.envelope({}).round.id});
    }});
    app.$('#issue-button').click();await flush();
    const post=app.requests.find(r=>r.url.pathname==='/api/challenges'&&r.options.method==='POST');
    assert.equal(post.url.searchParams.get('summary'),'true');
    assert.equal(app.$('#challenge-active').hidden,false);assert.match(app.$('#target-key').textContent,/32-byte key/);
    assert.equal(app.$('#download-button').href,'/api/challenges/instance/download');
    assert.equal(vm.runInContext('state.challenge.public',app.sandbox),undefined);
  });
  await test('map link metadata stays independent of full navigation and rotates with the session', async () => {
    const old=deferred(), fresh=deferred();let calls=0, options;
    const notebook={mount(target,value){options=value;return {show:async()=>{},hide(){},setAccount(){}};}};
    const app=await createApp({schemes:[scheme],user,hash:'#notebook?view=map',notebook,responder:url=>{
      if(url.pathname==='/api/schemes')return ++calls===1?old.promise:fresh.promise;
    }});
    const first=options.prepareMapCatalogue();await flush();
    assert.equal(app.$('#notebook-workspace').hidden,false);
    assert.equal(app.requests.filter(r=>r.url.searchParams.get('projection')==='navigation').length,0);
    app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));
    const second=options.prepareMapCatalogue();await flush();assert.equal(calls,2);
    old.resolve(app.envelope({projection:'map',schemes:[{id:'old-session',version:'0'}]}));await first;
    assert.equal(options.currentReleases().length,0);
    fresh.resolve(app.envelope({projection:'map',schemes:[{id:'fixture',version:'1'}]}));await second;
    assert.equal(options.currentReleases()[0].id,'fixture');
    assert.equal(vm.runInContext('state.catalogueLoaded',app.sandbox),false);
  });
  await test('a warm challenge paints its exact round immediately while actions wait for fresh status', async () => {
    let app, hold=false;const late=deferred();
    app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/challenges')return hold?late.promise:envelope({challenge:null,round_started:false});
    }});
    const data=app.envelope({challenge:summary(app),round_started:true,round_started_round_id:app.envelope({}).round.id});
    app.sandbox.fetch=async()=>({ok:true,status:200,json:async()=>data});
    await app.sandbox.loadChallenge();
    await app.navigate('#about');hold=true;
    app.sandbox.fetch=async()=>({ok:true,status:200,json:async()=>late.promise});
    await app.navigate(route);
    assert.equal(app.$('#challenge-active').hidden,false);assert.equal(app.$('#challenge-loading').hidden,true);
    assert.equal(app.$('#use-agent').disabled,true);assert.equal(app.$('#open-submission').disabled,true);
    late.resolve(app.envelope({challenge:{...summary(app),status:'solved',submission:{status:'correct'}},round_started:true,round_started_round_id:app.envelope({}).round.id}));
    await flush();assert.match(app.$('#challenge-status').textContent,/Recovered/);
    assert.equal(app.$('#use-agent').disabled,false);
  });
  await test('a rotated session discards a late challenge and starts only a fresh read', async () => {
    let calls=0;const old=deferred(),fresh=deferred();
    const app=await createApp({schemes:[scheme],user,hash:route,responder:url=>{
      if(url.pathname==='/api/challenges')return ++calls===1?old.promise:fresh.promise;
    }});
    app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));
    old.resolve(app.envelope({challenge:summary(app),round_started:true,round_started_round_id:app.envelope({}).round.id}));
    await flush();assert.equal(calls,2);assert.equal(app.$('#challenge-active').hidden,true);
    fresh.resolve(app.envelope({challenge:null,round_started:false}));await flush();
    assert.equal(app.$('#challenge-loading').hidden,true);assert.equal(app.$('#empty-title').textContent,'Ready?');
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('a denied refresh removes a warm instance instead of keeping stale controls or cached data', async () => {
    const app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope)=>
      url.pathname==='/api/challenges'?envelope({challenge:summary({envelope})}):undefined});
    assert.equal(app.$('#challenge-active').hidden,false);await app.navigate('#about');
    app.sandbox.fetch=async()=>({ok:false,status:403,json:async()=>({detail:'Access denied'})});
    await app.navigate(route);assert.equal(app.$('#challenge-active').hidden,true);
    assert.match(app.$('#challenge-error-text').textContent,/Access denied/);
    assert.equal(app.sandbox.cachedRead(app.sandbox.challengeReadKey('fixture','1','stage-1')),null);
  });
  await test('the next hour never paints an earlier round from memory or generates from its access', async () => {
    let hold=false;const late=deferred();
    const app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope)=>
      url.pathname==='/api/challenges'?(hold?late.promise:envelope({challenge:summary({envelope})})):undefined});
    await app.navigate('#about');hold=true;
    const id=app.envelope({}).round.id+1;
    const next={server_time:id*3600+1,round:{id,starts_at:id*3600,ends_at:(id+1)*3600}};
    app.sandbox.absorbClock(next);await app.navigate(route);
    assert.equal(app.$('#challenge-active').hidden,true);assert.equal(app.$('#challenge-loading').hidden,false);
    late.resolve({...next,challenge:null,round_started:false});await flush();
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
    assert.equal(app.$('#empty-title').textContent,'Ready?');
  });
  console.log(`${passed} latency regression checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

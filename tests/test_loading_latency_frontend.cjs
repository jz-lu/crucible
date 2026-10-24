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
  await test('a direct map overlaps its tiny index with held notebook assets only after fresh access', async () => {
    const session=deferred(), index=deferred();let mounted=0, options;
    const app=await createApp({schemes:[scheme],hash:'#notebook?view=map',responder:url=>{
      if(url.pathname==='/api/session')return session.promise;
      if(url.pathname==='/api/schemes')return index.promise;
    }});
    const assets=()=>app.document.querySelectorAll('link,script').filter(element=>(element.src||element.href||'').startsWith('/notebook/'));
    assert.equal(app.requests.length,1);assert.equal(assets().length,0,'No protected work before access resolves');
    session.resolve(app.envelope({user,csrf_token:'first',access:{mode:'beta',allowed:true,status:'active'}}));await flush();
    assert.equal(assets().length,4);
    assert.deepEqual(app.requests.filter(r=>r.url.pathname==='/api/schemes').map(r=>r.url.searchParams.get('projection')),['map'],
      'Tiny index starts before any notebook asset has loaded');
    app.window.CrucibleNotebook={mount(target,value){mounted++;options=value;return {
      show:async()=>{void value.prepareMapCatalogue();},hide(){},setAccount(){}};}};
    assets().forEach(asset=>asset.onload());await flush();
    assert.equal(mounted,1,'Rendering does not wait for the independent index');
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/schemes').length,1,'Controller shares the same read');
    index.resolve(app.envelope({projection:'map',schemes:[{id:'fixture',version:'1'}]}));await flush();
    assert.equal(options.currentReleases()[0].id,'fixture');
    assert.equal(app.requests.some(r=>r.options.method==='POST'||r.url.pathname==='/api/challenges'),false);
  });
  await test('rotation during cold map assets discards the old index and mounts only the current session', async () => {
    const old=deferred(),fresh=deferred();let calls=0,mounted=0,options;
    const app=await createApp({schemes:[scheme],user,hash:'#notebook?view=map',responder:url=>{
      if(url.pathname==='/api/schemes')return ++calls===1?old.promise:fresh.promise;
    }});
    app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));
    await app.sandbox.loadAuthorizedWorkspace();await flush();assert.equal(calls,2);
    old.resolve(app.envelope({projection:'map',schemes:[{id:'old-session',version:'0'}]}));await flush();
    app.window.CrucibleNotebook={mount(target,value){mounted++;options=value;return {show:async()=>{},hide(){},setAccount(){}};}};
    const assets=app.document.querySelectorAll('link,script').filter(element=>(element.src||element.href||'').startsWith('/notebook/'));
    assert.equal(assets.length,4,'Rotation reuses code downloads without reusing old rights');
    assets.forEach(asset=>asset.onload());await flush();
    assert.equal(mounted,1);assert.equal(options.currentReleases().length,0);
    fresh.resolve(app.envelope({projection:'map',schemes:[{id:'fixture',version:'1'}]}));await flush();
    assert.equal(options.currentReleases()[0].id,'fixture');
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
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
    for(let tick=0;tick<3;tick++)app.sandbox.updateClock();
    assert.equal(app.$('#open-submission').disabled,true,'Clock ticks cannot authorize an unconfirmed cached instance');
    const methodReads=app.requests.filter(r=>r.url.pathname==='/api/methods').length;
    app.$('#open-submission').click();await app.sandbox.openSubmission();await flush();
    assert.equal(app.$('#submission-dialog').open,false,'Handler also refuses stale status without relying on disabled markup');
    assert.equal(vm.runInContext('state.submissionChallenge',app.sandbox),null);
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/methods').length,methodReads);
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
  await test('an uncertain answer retains its original target and retry payload during a fresh-status read, even after its deadline', async () => {
    let hold=false;const late=deferred();
    const app=await createApp({schemes:[scheme],user,hash:route,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/challenges')return hold?late.promise:envelope({challenge:summary({envelope})});
      if(url.pathname==='/api/methods')return envelope({methods:[{id:'method-1',name:'Fixture method'}]});
    }});
    const writes=[],fetch=app.sandbox.fetch;
    app.sandbox.fetch=async(path,options)=>{
      if(path!=='/api/challenges/instance/submit')return fetch(path,options);
      writes.push(JSON.parse(options.body));
      if(writes.length===1)return {ok:false,status:503,json:async()=>({detail:'Fixture lost result'})};
      return {ok:true,status:200,json:async()=>app.envelope({challenge:{...summary(app),status:'incorrect',submission:{status:'incorrect'}},submission:{status:'incorrect'}})};
    };
    await app.sandbox.openSubmission();
    for(const [id,value] of Object.entries({candidate:'ab'.repeat(32),'method-select':'method-1',hardware:'Fixture CPU',workers:'1',
      'wall-seconds':'2',precomputation:'None','peak-memory':'1 MiB'}))app.$('#'+id).value=value;
    app.$('#submission-form').dispatchEvent({type:'submit'});await flush();
    assert.equal(writes.length,1);assert.match(app.$('#submission-error').textContent,/no confirmed result/);
    const pending=vm.runInContext('JSON.stringify(state.pendingAttempt)',app.sandbox);
    app.$('#submission-dialog').close();hold=true;
    const refresh=app.sandbox.loadChallenge();await flush();
    assert.equal(vm.runInContext('state.challengeLoading',app.sandbox),true);
    vm.runInContext('state.submissionChallenge.ends_at = now() - 1',app.sandbox);
    await app.sandbox.openSubmission();app.sandbox.updateClock();
    assert.equal(app.$('#submission-dialog').open,true);assert.equal(app.$('#submit-answer').disabled,false);
    assert.equal(vm.runInContext('JSON.stringify(state.pendingAttempt)',app.sandbox),pending);
    assert.equal(vm.runInContext('state.submissionChallenge.id',app.sandbox),'instance');
    assert.match(app.$('#submit-answer').textContent,/Retry the same answer/);
    app.$('#submission-form').dispatchEvent({type:'submit'});await flush();
    assert.equal(writes.length,2);assert.deepEqual(writes[1],writes[0]);
    assert.equal(vm.runInContext('state.pendingAttempt',app.sandbox),null);
    late.resolve(app.envelope({challenge:{...summary(app),status:'incorrect',submission:{status:'incorrect'}}}));await refresh;
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

const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp,flush} = require('./frontend-dom.cjs');
const user={id:'browse-fixture',handle:'alice'};
const session=envelope=>envelope({user,csrf_token:'fixture-csrf',access:{mode:'beta',status:'active',allowed:true}});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const response=data=>({ok:true,status:200,json:async()=>data});
const value=(app,expression)=>vm.runInContext(expression,app.sandbox);
const fixture=()=>createApp({schemes:[],hash:'#about',responder:(url,options,envelope)=>url.pathname==='/api/session'?session(envelope):undefined});
let passed=0;
async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  for(const kind of ['list','notebook','catalogue','release','map'])await test(kind+' browsing GET recovers once with its original controller and request options',async()=>{
    const app=await fixture(), calls=[];
    const scheme={id:'fixture',version:'1',name:'Fixture',profiles:[],protocol:{}};
    const data=app.envelope(kind==='catalogue'?{schemes:[]}:
      kind==='release'?{projection:'release',scheme,current_version:'1'}:
      kind==='map'?{projection:'map',schemes:[]}:{marker:'fresh'});
    app.sandbox.fetch=async(path,options)=>{calls.push({path,options});if(calls.length===1)throw new TypeError('Synthetic interrupted fetch');return response(data)};
    await ({list:()=>app.sandbox.readAgain('fixture','/api/people'),notebook:()=>app.sandbox.notebookAPI('/api/notebook/timeline'),
      catalogue:()=>app.sandbox.loadAuthorizedCatalogue(),release:()=>app.sandbox.loadRelease('fixture','1'),map:()=>app.sandbox.loadMapCatalogue()})[kind]();
    assert.equal(calls.length,2);assert.equal(calls[0].options.signal,calls[1].options.signal);
    for(const {options} of calls){assert.equal(options.method,'GET');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.body,undefined)}
  });

  await test('a persistent People outage remains visible after two attempts and waits for explicit retry',async()=>{
    let online=false,reads=0;
    const app=await createApp({schemes:[],hash:'#people',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session')return session(envelope);
      if(url.pathname==='/api/people'){reads++;if(!online)throw new TypeError('Synthetic offline');return envelope({people:[],pagination:{has_more:false}})}
    }});
    assert.equal(reads,2);assert.match(app.$('#people-content').textContent,/could not be reached/);
    assert.equal(app.$('#global-message').hidden,true);await flush();assert.equal(reads,2);
    online=true;app.$('#people-content').querySelector('button').click();await flush();
    assert.equal(reads,3);assert.doesNotMatch(app.$('#people-content').textContent,/could not be reached/);
    assert.equal(app.requests.some(item=>item.options.method==='POST'),false);
  });

  await test('recovery is explicit and never applies to writes, raw downloads, auth, agents or ordinary session polls',async()=>{
    const app=await fixture();let calls=0;
    app.sandbox.fetch=async()=>{calls++;throw new TypeError('Synthetic offline')};
    for(const [path,body,options] of [
      ['/api/people',undefined,{}],['/api/people',{}, {readRecovery:true}],
      ['/api/people',{}, {method:'GET',readRecovery:true}],['/api/people',undefined,{method:'HEAD',readRecovery:true}],
      ['/api/people',undefined,{raw:true,readRecovery:true}],['/api/auth/login',undefined,{readRecovery:true}],
      ['/api/access/status',undefined,{readRecovery:true}],['/api/agent/pipeline',undefined,{readRecovery:true}],
      ['/api/session',undefined,{readRecovery:true}]
    ]){
      const before=calls;await assert.rejects(app.sandbox.api(path,body,options),error=>error.code==='network_unreachable');
      assert.equal(calls,before+1,path+' must not be retried');
    }
  });

  await test('HTTP errors, interrupted bodies and expired deadlines never trigger browsing recovery',async()=>{
    const app=await fixture();
    for(const kind of ['http','body','deadline']){
      let calls=0,controller;
      app.sandbox.AbortController=class extends AbortController{constructor(){super();controller=this}};
      app.sandbox.fetch=async()=>{
        calls++;
        if(kind==='deadline'){controller.abort();throw new TypeError('Synthetic deadline')}
        return{ok:kind!=='http',status:kind==='http'?429:200,json:async()=>{if(kind==='body')throw new TypeError('Synthetic body interruption');return{detail:'Busy'}}};
      };
      await assert.rejects(app.sandbox.api('/api/people',undefined,{readRecovery:true}),error=>kind==='http'?error.status===429:error.code===(kind==='deadline'?'response_timeout':'response_interrupted'));
      assert.equal(calls,1);
    }
  });

  for(const kind of ['intent','warm'])await test('an in-flight '+kind+' read consumed by the visible view shares its single recovery',async()=>{
    const app=await fixture(),held=deferred();let calls=0;
    const data=app.envelope({people:[]});
    app.sandbox.fetch=async()=>++calls===1?held.promise:response(data);
    const background=kind==='intent'?app.sandbox.primeRead('shared','/api/people'):app.sandbox.warmRead('shared','/api/people',data=>data.people);
    const visible=app.sandbox.readAgain('shared','/api/people');
    held.reject(new TypeError('Synthetic interrupted fetch'));
    assert.equal(await visible,data);await background;
    assert.equal(calls,2);
  });

  await test('a completed failed intent is discarded before the next foreground request',async()=>{
    const app=await fixture();let calls=0,online=false;
    app.sandbox.fetch=async()=>{calls++;if(!online)throw new TypeError('Synthetic offline');return response(app.envelope({marker:'fresh'}))};
    app.sandbox.primeRead('failed','/api/people');await flush();assert.equal(calls,2);
    online=true;const data=await app.sandbox.readAgain('failed','/api/people');
    assert.equal(calls,3);assert.equal(data.marker,'fresh');
  });

  await test('a failed early notebook handoff is discarded before controller readiness',async()=>{
    let reads=0,online=false;
    const app=await createApp({schemes:[],hash:'#notebook',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session')return session(envelope);
      if(url.pathname==='/api/notebook/notes'){reads++;if(!online)throw new TypeError('Synthetic offline');return envelope({notes:[]})}
    }});
    assert.equal(reads,2);assert.equal(value(app,'notebookEarlyReads.size'),0);
    online=true;await app.sandbox.notebookAPI('/api/notebook/notes?limit=25&include_curated=true&scope=all');
    assert.equal(reads,3);
  });

  await test('document retirement and credential rotation prevent recovery and clock side effects',async()=>{
    for(const mode of ['retired','csrf']){
      const app=await fixture(),held=deferred();let calls=0;
      app.sandbox.fetch=async()=>{calls++;return held.promise};
      const task=app.sandbox.api('/api/people',undefined,{readRecovery:true});
      if(mode==='retired')app.window.dispatchEvent({type:'pagehide'});
      else app.sandbox.setSession({...session(app.envelope),csrf_token:'rotated'});
      held.reject(new TypeError('Synthetic old fetch'));await assert.rejects(task);
      assert.equal(calls,1);
    }
    const app=await fixture(),held=deferred();
    app.sandbox.fetch=()=>held.promise;
    const task=app.sandbox.api('/api/people',undefined,{readRecovery:true});
    app.sandbox.setSession({...session(app.envelope),csrf_token:'new-credential'});
    const accepted=value(app,'state.timeOffset');
    held.resolve(response(app.envelope({server_time:app.envelope({}).server_time-600})));await task;
    assert.equal(value(app,'state.timeOffset'),accepted);
  });

  for(const method of ['synchronizeSession','refreshRound'])await test(method+' cannot paint a retired failure after persisted restoration',async()=>{
    const app=await fixture(),held=deferred();let calls=0;
    app.sandbox.fetch=async()=>{calls++;return held.promise};
    const task=app.sandbox[method]();await flush();
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow',persisted:true});
    held.reject(new TypeError('Synthetic retired request'));await task;await flush();
    assert.equal(app.$('#global-message').hidden,true);assert.equal(calls,1);
  });

  await test('restored startup detaches the still-pending retired catalogue and accepts only the fresh one',async()=>{
    const old=deferred(),fresh=deferred();let reads=0;
    const app=await createApp({schemes:[],hash:'#challenge',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session')return session(envelope);
      if(url.pathname==='/api/schemes')return ++reads===1?old.promise:fresh.promise;
    }});
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow',persisted:true});await flush();
    assert.equal(reads,2);assert.equal(value(app,'state.catalogueLoaded'),false);
    old.resolve(app.envelope({schemes:[],catalogue_revision:'retired',server_time:app.envelope({}).server_time-600}));await flush();
    assert.equal(value(app,'state.catalogueLoaded'),false);
    fresh.resolve(app.envelope({schemes:[],catalogue_revision:'fresh'}));await flush();
    assert.equal(value(app,'state.catalogueRevision'),'fresh');assert.equal(app.$('#catalogue-workspace').hidden,false);
    assert.equal(app.$('#global-message').hidden,true);
  });
  console.log(`\n${passed} browsing read recovery checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1});

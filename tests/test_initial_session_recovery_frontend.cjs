const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id:'recovery-fixture',handle:'alice'};
const session = envelope => envelope({user,csrf_token:'fixture-csrf',access:{mode:'beta',status:'active',allowed:true}});
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const count = app => app.requests.filter(item=>item.url.pathname==='/api/session').length;
let passed=0;
async function test(name,run){await run();console.log('PASS '+name);passed++;}

(async()=>{
  for(const bootstrap of [true,false])await test((bootstrap?'bootstrap':'app')+' initial transport failure recovers once under the original signal',async()=>{
    const held=deferred();let reads=0;
    const app=await createApp({schemes:[],hash:'#about',bootstrap,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session'){
        if(++reads===1)throw new TypeError('Synthetic failed fetch');
        return held.promise;
      }
    }});
    assert.equal(count(app),2);assert.equal(app.$('#about-workspace').hidden,true);
    assert.equal(app.requests.every(item=>item.url.pathname==='/api/session'),true);
    assert.equal(app.requests[0].options.signal,app.requests[1].options.signal,'Recovery cannot restart the deadline/controller');
    for(const item of app.requests){assert.equal(item.options.method,'GET');assert.equal(item.options.cache,'no-store');assert.equal(item.options.credentials,'same-origin');}
    held.resolve(session(app.envelope));await flush();
    assert.equal(count(app),2);assert.equal(app.$('#about-workspace').hidden,false);
    assert.equal(app.$('#global-message').hidden,true);
  });

  await test('persistent offline remains visible inline and only an explicit retry starts another read',async()=>{
    let online=false;
    const app=await createApp({schemes:[],hash:'#about',bootstrap:true,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session'){if(!online)throw new TypeError('Synthetic offline');return session(envelope);}
    }});
    assert.equal(count(app),2);assert.match(app.$('#access-description').textContent,/Access could not be checked.*connection/);
    assert.equal(app.$('#global-message').hidden,true);assert.equal(app.$('#about-workspace').hidden,true);
    assert.equal(app.$('#access-retry').textContent,'Try again');
    await flush();assert.equal(count(app),2,'No repeated background retries');
    online=true;app.$('#access-retry').click();await flush();
    assert.equal(count(app),3);assert.equal(app.$('#about-workspace').hidden,false);
  });

  for(const mode of ['retired','superseded-session'])await test(mode+' initial response cannot recover or paint',async()=>{
    const held=deferred();
    const app=await createApp({schemes:[],hash:'#about',bootstrap:true,responder:url=>url.pathname==='/api/session'?held.promise:undefined});
    if(mode==='retired')app.window.dispatchEvent({type:'pagehide'});
    else app.sandbox.setSession(app.envelope({user:null,csrf_token:'new-session',access:{mode:'beta',status:'anonymous',allowed:false}}));
    held.reject(new TypeError('Synthetic old fetch'));await flush();
    assert.equal(count(app),1);assert.equal(app.$('#global-message').hidden,true);
    assert.equal(app.document.querySelector('#access-retry'),null);assert.equal(app.$('#about-workspace').hidden,true);
  });

  await test('a response from before pagehide stays retired even after pageshow',async()=>{
    const held=deferred();
    const app=await createApp({schemes:[],hash:'#about',bootstrap:true,responder:url=>url.pathname==='/api/session'?held.promise:undefined});
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow'});
    held.resolve(session(app.envelope));await flush();
    assert.equal(count(app),1);assert.equal(app.$('#about-workspace').hidden,true);
    assert.equal(app.$('#global-message').hidden,true);
  });

  await test('restoring an unresolved document starts one fresh access read, never its retired response',async()=>{
    const old=deferred(),fresh=deferred();let reads=0;
    const app=await createApp({schemes:[],hash:'#about',bootstrap:true,responder:url=>{
      if(url.pathname==='/api/session')return ++reads===1?old.promise:fresh.promise;
    }});
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow',persisted:true});await flush();
    assert.equal(count(app),2);assert.equal(app.$('#about-workspace').hidden,true);
    old.resolve(session(app.envelope));await flush();assert.equal(app.$('#about-workspace').hidden,true);
    fresh.resolve(session(app.envelope));await flush();assert.equal(app.$('#about-workspace').hidden,false);
    assert.equal(count(app),2);
  });

  await test('restoring startup after access succeeded restarts an interrupted catalogue read',async()=>{
    const held=deferred();let catalogueReads=0;
    const app=await createApp({schemes:[],hash:'#challenge',bootstrap:true,responder:(url,options,envelope)=>{
      if(url.pathname==='/api/session')return session(envelope);
      if(url.pathname==='/api/schemes' && ++catalogueReads===1)return held.promise;
    }});
    assert.equal(vm.runInContext('state.sessionResolved',app.sandbox),true);
    assert.equal(vm.runInContext('state.catalogueLoaded',app.sandbox),false);
    app.window.dispatchEvent({type:'pagehide'});
    held.reject(new TypeError('Synthetic interrupted catalogue'));await flush();
    assert.equal(app.$('#global-message').hidden,true);
    app.window.dispatchEvent({type:'pageshow',persisted:true});await flush();
    assert.equal(count(app),2);assert.equal(catalogueReads,2);
    assert.equal(vm.runInContext('state.catalogueLoaded',app.sandbox),true);
    assert.equal(app.$('#catalogue-workspace').hidden,false);
    assert.equal(app.$('#global-message').hidden,true);
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow',persisted:true});await flush();
    assert.equal(count(app),2,'Completed startup needs no repeated initialization');
  });

  await test('a late retired initial response cannot roll back the accepted same-round clock',async()=>{
    const old=deferred(),fresh=deferred();let reads=0;
    const app=await createApp({schemes:[],hash:'#about',bootstrap:true,responder:url=>{
      if(url.pathname==='/api/session')return ++reads===1?old.promise:fresh.promise;
    }});
    const data=session(app.envelope);
    app.window.dispatchEvent({type:'pagehide'});app.window.dispatchEvent({type:'pageshow',persisted:true});await flush();
    fresh.resolve({...data,server_time:data.server_time+30});await flush();
    const accepted=vm.runInContext('state.timeOffset',app.sandbox);
    assert.equal(app.$('#about-workspace').hidden,false);
    old.resolve({...data,server_time:data.server_time-30});await flush();
    assert.equal(vm.runInContext('state.timeOffset',app.sandbox),accepted);
    assert.equal(count(app),2);assert.equal(app.$('#global-message').hidden,true);
  });

  await test('polling and writes never recover a failed fetch automatically',async()=>{
    const app=await createApp({schemes:[],hash:'#about',user});
    const calls=[];app.sandbox.fetch=async(path,options)=>{calls.push({path,options});throw new TypeError('Synthetic failed fetch');};
    for(const [path,body,options] of [['/api/session',undefined,{}],['/api/auth/login',{handle:'fixture'},{}],
      ['/api/session',{}, {initialSession:true}]]){
      const before=calls.length;await assert.rejects(app.sandbox.api(path,body,options),error=>error.code==='network_unreachable');
      assert.equal(calls.length,before+1);
    }
  });

  await test('timeouts, HTTP failures and broken bodies do not start another initial session request',async()=>{
    const app=await createApp({schemes:[],hash:'#about'});
    for(const mode of ['deadline','http','body']){
      let fetches=0,finished=0;const controller=new AbortController();
      const response=mode==='deadline'?Promise.reject(new TypeError('Synthetic timeout')):Promise.resolve({
        ok:mode!=='http',status:mode==='http'?429:200,
        json:async()=>{if(mode==='body')throw new TypeError('Synthetic interrupted body');return{detail:'Busy'};}
      });
      if(mode==='deadline')controller.abort();
      app.window.CrucibleSessionBootstrap={response,controller,finish:()=>finished++};
      app.sandbox.fetch=async()=>{fetches++;throw Error('Unexpected retry');};
      await assert.rejects(app.sandbox.api('/api/session',undefined,{initialSession:true}));
      assert.equal(fetches,0);assert.equal(finished,1);
    }
  });
  console.log(`\n${passed} initial session recovery checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

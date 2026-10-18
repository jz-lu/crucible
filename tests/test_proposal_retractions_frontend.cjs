// Retraction is an explicit author action; this harness has no live network.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const digest = 'a'.repeat(64), supportHash = 'b'.repeat(64);
const record = overrides => ({id:'proposal-one',revision:4,content_sha256:digest,packet_sha256:'c'.repeat(64),can_retract:true,...overrides});
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
async function mount({value=record(),kind='scheme',api}={}) {
  const app=await createApp({schemes:[],hash:'#about'}),host=app.document.createElement('section'),calls=[];
  app.document.body.append(host);let active=true,successes=0,sequence=0;
  app.sandbox.crypto.randomUUID=()=>`retraction-${++sequence}`;
  app.window.CrucibleRetractions.mount(host,{record:value,kind,current:()=>active,onSuccess:async()=>{successes++;},api:async(path,body)=>{
    calls.push({path,body:plain(body)});
    return api?api(path,body):{[kind==='scheme'?'proposal':'request']:{id:value.id,retracted:true}};
  }});
  return{...app,host,calls,successes:()=>successes,setCurrent:v=>{active=v;},reason:()=>host.querySelector('textarea'),source:()=>host.querySelector('input'),submit:async()=>{host.querySelector('form').dispatchEvent({type:'submit'});await flush();}};
}
let passed=0;async function test(name,fn){await fn();console.log('PASS '+name);passed++;}
(async()=>{
  await test('rendering and opening an author form are inert, and permission must be explicit',async()=>{
    for(const permission of [undefined,false,'true']){
      const app=await mount({value:record({can_retract:permission,tags:['proposal','retracted'],extensions:{can_retract:true}})});
      assert.equal(app.host.querySelector('form'),null);assert.equal(app.calls.length,0);assert.equal(app.host.textContent,'');
    }
    const app=await mount();assert.equal(app.host.querySelector('details').open,false);assert.equal(app.calls.length,0);
    app.host.querySelector('details').open=true;await flush();assert.equal(app.calls.length,0);
  });
  await test('reason is required before creating a scheme retraction bound to the saved revision',async()=>{
    const app=await mount();app.reason().value='   ';await app.submit();assert.equal(app.calls.length,0);
    assert.match(app.host.textContent,/Explain why/);app.reason().value='  This claim was superseded.  ';await app.submit();
    assert.equal(app.calls[0].path,'/api/protocol/proposals/proposal-one/retraction');
    assert.deepEqual(app.calls[0].body,{reason:'This claim was superseded.',idempotency_key:'retraction-1',expected_revision:4,content_sha256:digest});
    assert.equal(app.successes(),1);
  });
  await test('research request uses the frozen packet and an exact same-origin supporting note',async()=>{
    const app=await mount({kind:'research'});app.reason().value='The model no longer supports this recommendation.';
    app.source().value='http://127.0.0.1:8000/#notebook?'+new URLSearchParams({note:'curated:corrective-note',revision:'2',sha256:supportHash});await app.submit();
    assert.equal(app.calls[0].path,'/api/notebook/research-requests/proposal-one/retraction');
    assert.deepEqual(app.calls[0].body,{reason:'The model no longer supports this recommendation.',supporting_note:{note_id:'curated:corrective-note',revision:2,content_sha256:supportHash},idempotency_key:'retraction-1',packet_sha256:'c'.repeat(64)});
    assert.equal('expected_revision' in app.calls[0].body,false);
  });
  await test('off-origin, arbitrary route and unpinned note links cannot be submitted',async()=>{
    const app=await mount();app.reason().value='Correction recorded.';
    for(const link of [
      'https://external.invalid/#notebook?note=x&revision=2&sha256='+supportHash,
      'http://name:password@127.0.0.1:8000/#notebook?note=x&revision=2&sha256='+supportHash,
      '/api/notebook/notes/x#notebook?note=x&revision=2&sha256='+supportHash,
      '#notebook?note=x', '#notebook?note=x&revision=0&sha256='+supportHash,
      '#notebook?note=x&revision=2&sha256=missing', 'javascript:alert(1)']){
      app.source().value=link;await app.submit();assert.equal(app.calls.length,0,link);
      assert.equal(app.reason().disabled,false);assert.match(app.host.querySelector('[role="alert"]').textContent,/note|link/);
    }
  });
  await test('lost acknowledgement retries one frozen payload and cannot silently change evidence',async()=>{
    let attempts=0;const app=await mount({api:async()=>{if(++attempts===1)throw Error('Connection lost');return{proposal:{id:'proposal-one',retracted:true}};}});
    app.reason().value='Original reason';await app.submit();assert.equal(app.reason().disabled,true);assert.equal(app.source().disabled,true);
    assert.match(app.host.textContent,/Retry to confirm the same retraction/);app.reason().value='Different reason';app.source().value='#notebook?note=x&revision=1&sha256='+supportHash;
    await app.submit();assert.deepEqual(app.calls[0],app.calls[1]);assert.equal(app.successes(),1);
  });
  await test('definite rejection unlocks correction and assigns a new action identity',async()=>{
    let attempts=0;const app=await mount({api:async()=>{if(++attempts===1)throw Object.assign(Error('Reason needs correction'),{status:422});return{proposal:{id:'proposal-one',retracted:true}};}});
    app.reason().value='Initial reason';await app.submit();assert.equal(app.reason().disabled,false);
    app.reason().value='Corrected reason';await app.submit();assert.notEqual(app.calls[0].body.idempotency_key,app.calls[1].body.idempotency_key);
    assert.equal(app.calls[1].body.reason,'Corrected reason');assert.equal(app.successes(),1);
  });
  await test('concurrent submits do not duplicate the request',async()=>{
    const pending=deferred(),app=await mount({api:()=>pending.promise});app.reason().value='Withdraw';await app.submit();await app.submit();
    assert.equal(app.calls.length,1);pending.resolve({proposal:{id:'proposal-one',retracted:true}});await flush();assert.equal(app.successes(),1);
  });
  await test('a mismatched receipt is uncertain and never presented as confirmed',async()=>{
    const app=await mount({api:async()=>({proposal:{id:'other-proposal',retracted:true}})});app.reason().value='Withdraw';await app.submit();
    assert.equal(app.successes(),0);assert.equal(app.reason().disabled,true);assert.match(app.host.textContent,/receipt could not be confirmed.*Retry/);
  });
  await test('stale navigation or account context prevents writes and ignores late replies',async()=>{
    const untouched=await mount();untouched.reason().value='Private';untouched.setCurrent(false);await untouched.submit();assert.equal(untouched.calls.length,0);
    for(const failure of [false,true]){
      const pending=deferred(),app=await mount({api:()=>pending.promise});app.reason().value='Private';await app.submit();app.setCurrent(false);app.host.replaceChildren();
      if(failure)pending.reject(Error('PRIVATE response'));else pending.resolve({proposal:{id:'proposal-one',retracted:true}});
      await flush();assert.equal(app.successes(),0);assert.equal(app.host.textContent,'');
    }
  });
  await test('trusted retraction renders an inert reason and exact support link without another form',async()=>{
    const app=await mount({value:record({retracted:true,retraction:{reason:'<img src=x onerror=attack()>',supporting_note:{note_id:'note-one',revision:2,content_sha256:supportHash}}})});
    assert.match(app.host.textContent,/Retracted.*<img src=x onerror=attack\(\)>/);assert.equal(app.host.querySelector('img'),null);assert.equal(app.host.querySelector('form'),null);
    const link=app.host.querySelector('a');assert.equal(link.href,'#notebook?'+new URLSearchParams({note:'note-one',revision:'2',sha256:supportHash}));assert.equal(app.calls.length,0);
    const compact=await mount({value:record({retracted:true,retraction:{created_at:100}})});assert.equal(compact.host.querySelector('a'),null);assert.doesNotMatch(compact.host.textContent,/undefined|null/);
  });
  console.log(`${passed} retraction frontend tests passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

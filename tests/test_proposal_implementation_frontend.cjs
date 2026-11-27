// No real accounts or requests: exercise the shared progress reader and mutation receipt.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const digest = 'a'.repeat(64);
const base = changes => ({kind:'scheme',target_id:'proposal-one',title:'Proposal',decision_id:'decision-one',decision_sha256:digest,
  status:'awaiting_implementation',eligible:true,can_update:true,head_event_id:null,actor:null,events:[],...changes});
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => {let resolve,reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;});return {promise,resolve,reject};};
async function mount({initial=base(),api}={}) {
  const app = await createApp({schemes:[],hash:'#about'}), host = app.document.createElement('section'), calls = [];
  app.document.body.append(host); let active = true, sequence = 0, changes = 0;
  app.sandbox.crypto.randomUUID = () => 'update-' + (++sequence);
  const receipt = body => ({implementation:base({status:body.status,head_event_id:'event-one',actor:{handle:'reviewer'},
    events:[{id:'event-one',status:body.status,summary:body.summary,evidence:body.evidence,actor:{handle:'reviewer'}}]}),
    event:{id:'event-one',status:body.status,summary:body.summary,evidence:body.evidence}});
  app.window.CrucibleImplementation.mount(host,{initial,kind:'scheme',targetId:'proposal-one',current:()=>active,onChange:()=>changes++,api:async(path,body)=>{
    calls.push({path,body:body === undefined ? undefined : plain(body)});return api ? api(path,body,receipt) : receipt(body);
  }});
  const control = name => host.querySelector('[name="implementation-' + name + '"]');
  return {...app,host,calls,changes:()=>changes,setCurrent:value=>{active=value;},control,
    submit:async()=>{host.querySelector('form').dispatchEvent({type:'submit'});await flush();}};
}
let passed=0; async function test(name,fn) {await fn(); console.log('PASS '+name);passed++;}
(async()=>{
  await test('author progress is read-only and does not grant authority from an authored label',async()=>{
    for(const can_update of [false,undefined,'true']) {
      const app=await mount({initial:base({can_update})});assert.equal(app.host.querySelector('form'),null);assert.equal(app.calls.length,0);
    }
    const app=await mount({initial:base({can_update:false})});assert.match(app.host.textContent,/Awaiting implementation/);
    assert.match(app.host.textContent,/does not certify research/);assert.equal(app.host.querySelector('form'),null);
  });
  await test('start binds an update to the approved decision and current event',async()=>{
    const app=await mount();app.control('summary').value='  Implementing the reviewed change.  ';await app.submit();
    assert.deepEqual(app.calls[0],{path:'/api/proposal-implementations/scheme/proposal-one/events',body:{decision_id:'decision-one',expected_event_id:null,status:'in_progress',summary:'Implementing the reviewed change.',evidence:[],idempotency_key:'update-1'}});
    assert.equal(app.changes(),1);assert.match(app.host.textContent,/Updated by reviewer/);assert.match(app.host.textContent,/Implementing the reviewed change/);
  });
  await test('completion requires evidence and preserves an exact note citation',async()=>{
    const app=await mount({initial:base({status:'in_progress',head_event_id:'started'})});app.control('status').value='completed';app.control('summary').value='Implemented and checked.';await app.submit();
    assert.equal(app.calls.length,0);assert.match(app.host.textContent,/Completion needs/);
    app.control('evidence-title').value='Implementation check';app.control('evidence-url').value='#notebook?note=note-one&revision=2&sha256='+digest;
    await app.submit();assert.equal(app.calls[0].body.expected_event_id,'started');assert.equal(app.calls[0].body.status,'completed');
    assert.equal(app.calls[0].body.evidence[0].url,'/#notebook?note=note-one&revision=2&sha256='+digest);assert.equal(app.changes(),1);
  });
  await test('implementation evidence accepts exact section passages without changing their selector',async()=>{
    const app=await mount();app.control('summary').value='A precise source supports this work.';
    app.control('evidence-title').value='Source passage';
    const url='/#notebook?note=note-one&revision=2&sha256='+digest+'&passage=p1-b-3-8';
    app.control('evidence-url').value=url;await app.submit();assert.equal(app.calls[0].body.evidence[0].url,url);
  });
  await test('an in-progress proposal defaults to recording further progress without declaring completion',async()=>{
    const app=await mount({initial:base({status:'in_progress',head_event_id:'started'})});
    assert.equal(app.control('status').value,'in_progress');assert.deepEqual(app.control('status').options.map(option=>option.textContent),['Record progress','Mark completed']);
    app.control('summary').value='Parameter checks are complete; implementation review remains.';await app.submit();
    assert.equal(app.calls[0].body.status,'in_progress');assert.equal(app.calls[0].body.expected_event_id,'started');assert.deepEqual(app.calls[0].body.evidence,[]);assert.equal(app.changes(),1);
  });
  await test('unsafe and unpinned evidence cannot become links or writes',async()=>{
    const app=await mount();app.control('summary').value='Starting';app.control('evidence-title').value='Source';
    for(const link of ['javascript:alert(1)','http://external.invalid/x','https://user:password@example.org','#notebook?note=x','#notebook?note=x&revision=2&sha256='+digest+'&review=1']) {
      app.control('evidence-url').value=link;await app.submit();assert.equal(app.calls.length,0,link);
    }
    const rendered=await mount({initial:base({status:'completed',can_update:false,head_event_id:'done',events:[{id:'done',status:'completed',summary:'<img onerror=attack()>',evidence:[{label:'<script>evil</script>',url:'javascript:alert(1)'},{label:'<b>valid</b>',url:'https://example.org/evidence'}]}]})});
    assert.equal(rendered.host.querySelector('img'),null);assert.equal(rendered.host.querySelector('script'),null);assert.equal(rendered.host.querySelector('a').textContent,'<b>valid</b>');
    assert.match(rendered.host.textContent,/Evidence link unavailable/);
  });
  await test('ambiguous failures retry one frozen write even if controls change',async()=>{
    let tries=0;const app=await mount({api:async(path,body,receipt)=>{if(++tries===1)throw Error('Response lost');return receipt(body);}});
    app.control('summary').value='Original';await app.submit();assert.equal(app.control('summary').disabled,true);assert.match(app.host.textContent,/Retry to confirm the same update/);
    app.control('summary').value='Different';await app.submit();assert.deepEqual(app.calls[0],app.calls[1]);assert.equal(app.changes(),1);
  });
  await test('validation failure permits correction, conflict requires refreshed progress',async()=>{
    const app=await mount({api:async()=>{throw Object.assign(Error('Needs correction'),{status:422});}});app.control('summary').value='Initial';await app.submit();
    assert.equal(app.control('summary').disabled,false);app.control('summary').value='Corrected';await app.submit();assert.notEqual(app.calls[0].body.idempotency_key,app.calls[1].body.idempotency_key);
    const conflicted=await mount({api:async()=>{throw Object.assign(Error('Updated by another reviewer'),{status:409});}});conflicted.control('summary').value='Start';await conflicted.submit();
    assert.equal(conflicted.control('summary').disabled,true);assert.match(conflicted.host.textContent,/Refresh progress/);
  });
  await test('mismatched receipt is never announced as successful',async()=>{
    const app=await mount({api:async(path,body,receipt)=>{const result=receipt(body);result.event.summary='Different update';return result;}});
    app.control('summary').value='Original';await app.submit();assert.equal(app.changes(),0);assert.match(app.host.textContent,/receipt could not be confirmed/);assert.equal(app.control('summary').disabled,true);
  });
  await test('an exact retry receipt is accepted when another reviewer has already advanced progress',async()=>{
    const app=await mount({api:async(path,body,receipt)=>{const result=receipt(body);result.implementation.status='completed';result.implementation.head_event_id='later';result.implementation.events.push({id:'later',status:'completed',summary:'Completed afterward',evidence:[]});return result;}});
    app.control('summary').value='Starting implementation';await app.submit();assert.equal(app.changes(),1);assert.match(app.host.textContent,/Completed afterward/);assert.doesNotMatch(app.host.textContent,/Retry to confirm/);
  });
  await test('late replies and duplicate clicks cannot mutate a departed reader',async()=>{
    const pending=deferred(),app=await mount({api:()=>pending.promise});app.control('summary').value='Start';await app.submit();await app.submit();assert.equal(app.calls.length,1);
    app.setCurrent(false);app.host.replaceChildren();pending.resolve({implementation:base({status:'in_progress'})});await flush();assert.equal(app.changes(),0);assert.equal(app.host.textContent,'');
    const departed=await mount();departed.control('summary').value='Start';departed.setCurrent(false);await departed.submit();assert.equal(departed.calls.length,0);
  });
  await test('compact progress loads its pinned history before enabling edits',async()=>{
    const pending=deferred(),initial=base();delete initial.events;
    const app=await mount({initial,api:()=>pending.promise});assert.equal(app.host.querySelector('form'),null);assert.match(app.calls[0].path,/decision_id=decision-one/);
    pending.resolve({implementation:base()});await flush();assert.ok(app.host.querySelector('form'));
  });
  await test('a permission loss removes previously visible progress',async()=>{
    const initial=base();delete initial.events;
    const app=await mount({initial,api:async()=>{throw Object.assign(Error('No access'),{status:403});}});await flush();
    assert.doesNotMatch(app.host.textContent,/Awaiting implementation/);assert.match(app.host.textContent,/no longer available/);
  });
  console.log(`${passed} implementation frontend tests passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

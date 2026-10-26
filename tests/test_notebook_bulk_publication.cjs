const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp} = require('./frontend-dom.cjs');

const web = path.resolve(__dirname, '../web');
const owner = {id:'10000000-0000-4000-8000-000000000001', handle:'alice'};
const other = {id:'10000000-0000-4000-8000-000000000002', handle:'bob'};
const identifier = n => '20000000-0000-4000-8000-' + String(n).padStart(12, '0');
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const failure = (status, message='Synthetic request failure') => Object.assign(new Error(message), {status});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate, description) {
  for (let attempt=0; attempt<200; attempt++) {if (predicate()) return; await tick();}
  assert.ok(predicate(), description);
}
function summary(number, changes={}) {
  const {publication_status='private', revision=1, ...rest} = changes;
  const digest=number.toString(16).padStart(64,'0');
  const row={revision,content_sha256:digest,created_at:100+number,summary:true,publication_status,
    content:{title:'Private note '+number,claim:'Summary only',tags:[]}};
  return {id:identifier(number),owner,can_edit:true,deletion:null,revision,content_sha256:digest,
    publication_status,revision_count:revision,revisions:[row],shared_revision:null,proposal_revision:null,...rest};
}
const index = (notes, changes={}) => ({format:'crucible-notebook-summary-index-v1',projection:'index',
  complete:true,viewer_id:owner.id,scope:'mine',include_curated:false,notes,next_after:null,
  overflow_reason:null,capabilities:{},server_time:100,...changes});
const receipt = (url, body, changes={}) => ({format:'crucible-publication-request-v1',publication_request:{
  id:'30000000-0000-4000-8000-000000000001',note_id:url.pathname.split('/')[4],
  revision:body.revision,content_sha256:body.content_sha256,requested_at:100,status:'submitted',...changes}});

async function fixture({rows=[summary(1),summary(2)], inspect}={}) {
  const shell=await createApp({schemes:[],hash:'#about'});
  let account=owner, credential='session-one', sequence=0;
  const requests=[];
  shell.sandbox.crypto.randomUUID=()=> '40000000-0000-4000-8000-'+String(++sequence).padStart(12,'0');
  shell.sandbox.window.confirm=()=>true;
  for (const file of ['store.js','seed.js','notebook.js']) {
    vm.runInContext(fs.readFileSync(path.join(web,'notebook',file),'utf8'),shell.sandbox,{filename:file});
  }
  const root=shell.$('#notebook-workspace');
  const api=async(raw,body)=>{
    const url=new URL(raw,'http://testserver');
    const call={raw,url,body:body===undefined?undefined:clone(body)};requests.push(call);
    if (inspect) {const result=await inspect(call,requests);if(result!==undefined)return result;}
    if (url.pathname==='/api/notebook/notes' && body===undefined) {
      if(url.searchParams.get('projection')==='index')return index(rows);
      return {notes:rows,next_after:null,capabilities:{}};
    }
    if (url.pathname.endsWith('/publication-requests') && body!==undefined) {
      assert.equal(url.searchParams.get('summary'),'true','Bulk requests must use compact receipts');
      return receipt(url,body);
    }
    throw Error('Unexpected bulk fixture request: '+url.pathname);
  };
  let controller;
  const navigate=route=>{shell.history.pushState(null,'',route);return controller.show(new URLSearchParams(route.split('?')[1]||''));};
  controller=shell.window.CrucibleNotebook.mount(root,{api,navigate,account:()=>account,session:()=>credential});
  await controller.show(new URLSearchParams());
  const q=name=>root.querySelector('[data-nb="'+name+'"]');
  const buttons=()=>q('dialog-body').querySelectorAll('button');
  const findButton=label=>buttons().find(button=>typeof label==='string'?button.textContent===label:label.test(button.textContent));
  const available=label=>Boolean(findButton(label) && !findButton(label).disabled && !findButton(label).hidden);
  const activate=button=>{
    assert.ok(button,'Expected dialog action');assert.equal(button.disabled,false,'Action must be usable');
    button.click();
    if(button.type==='submit')button.closest('form').dispatchEvent({type:'submit'});
  };
  return {...shell,root,controller,q,requests,findButton,available,activate,
    writes:()=>requests.filter(call=>call.body!==undefined),
    open:async()=>{assert.ok(q('publish-all'),'Heading offers Publish all');q('publish-all').click();await until(()=>q('dialog').open,'Bulk dialog opens');},
    confirm:async(count)=>{await until(()=>findButton('Send '+count+' publish requests'),'Exact complete confirmation appears');activate(findButton('Send '+count+' publish requests'));},
    changeAccount(value){account=value;controller.setAccount(value);},
    rotateSession(){credential='session-two';controller.setAccount(account);}};
}
function assertNoBodyReads(app) {
  assert.equal(app.requests.some(call=>call.body===undefined && call.url.pathname.startsWith('/api/notebook/notes/')),false);
  assert.equal(app.requests.some(call=>call.url.pathname.startsWith('/map/')),false);
}
let passed=0;
async function test(name,run){
  let deadline;
  try {
    await Promise.race([run(),new Promise((_,reject)=>{
      deadline=setTimeout(()=>reject(Error('Bulk test did not reach its expected event: '+name)),10000);
    })]);
    console.log('PASS '+name);passed++;
  } finally {clearTimeout(deadline);}
}

(async()=>{
  await test('fresh owned-summary preparation and cancellation never write or fetch bodies',async()=>{
    const entered=deferred(), held=deferred();
    const app=await fixture({inspect:call=>{if(call.url.searchParams.get('projection')==='index'){entered.resolve();return held.promise;}}});
    await app.open();await entered.promise;
    assert.equal(app.writes().length,0);assert.equal(app.available(/^Send /),false);
    held.resolve(index([summary(1),summary(2)]));
    await until(()=>app.findButton('Send 2 publish requests'),'Preparation finishes');
    const request=app.requests.find(call=>call.url.searchParams.get('projection')==='index');
    assert.equal(request.url.searchParams.get('scope'),'mine');
    assert.equal(request.url.searchParams.has('q'),false);assert.equal(request.url.searchParams.has('tag'),false);
    app.q('close').click();assert.equal(app.q('dialog').open,false);
    assert.equal(app.writes().length,0);assertNoBodyReads(app);
  });

  await test('mixed eligibility submits only own editable latest private revisions in sequence',async()=>{
    const rows=[summary(1),summary(2,{publication_status:'submitted'}),summary(3,{publication_status:'approved'}),
      summary(4,{deletion:{id:'deleted'},can_edit:false}),summary(5,{can_edit:false}),
      summary(6,{publication_status:'changes_requested'}),summary(7,{publication_status:'withdrawn'}),
      summary(8,{revision:2,shared_revision:{revision:1,publication_status:'approved'}})];
    const first=deferred(), second=deferred();
    const app=await fixture({rows,inspect:(call,all)=>{
      if(call.body!==undefined)return all.filter(item=>item.body!==undefined).length===1?first.promise:second.promise;
    }});
    await app.open();await app.confirm(2);await until(()=>app.writes().length===1,'First request starts');
    await tick();assert.equal(app.writes().length,1,'No parallel POSTs');
    first.resolve(receipt(app.writes()[0].url,app.writes()[0].body));
    await until(()=>app.writes().length===2,'Second follows receipt');
    second.resolve(receipt(app.writes()[1].url,app.writes()[1].body));
    await until(()=>/2 confirmed/i.test(app.q('dialog-body').textContent),'Completion reports both requests');
    assert.deepEqual(app.writes().map(call=>call.url.pathname.split('/')[4]),[identifier(1),identifier(8)]);
    for(const call of app.writes()) {
      const note=rows.find(row=>call.url.pathname.includes(row.id));
      assert.deepEqual(Object.keys(call.body).sort(),['content_sha256','idempotency_key','revision']);
      assert.equal(call.body.revision,note.revision);assert.equal(call.body.content_sha256,note.content_sha256);
      assert.match(call.body.idempotency_key,/^[0-9a-f-]{36}$/);
    }
    assert.notEqual(app.writes()[0].body.idempotency_key,app.writes()[1].body.idempotency_key);
    assertNoBodyReads(app);
  });

  await test('rate limit pauses after partial completion and Continue is explicit',async()=>{
    let attempts=0;
    const app=await fixture({rows:[summary(1),summary(2),summary(3)],inspect:call=>{
      if(call.body!==undefined && ++attempts===2)throw failure(429,'Please wait before more requests.');
    }});
    await app.open();await app.confirm(3);
    await until(()=>app.findButton(/^Continue/),'Rate-limit continuation appears');
    assert.equal(app.writes().length,2);await tick();assert.equal(app.writes().length,2);
    app.activate(app.findButton(/^Continue/));
    await until(()=>app.writes().length===4,'Continuation retries the stopped item and completes remaining');
    assert.equal(app.writes()[2].url.pathname,app.writes()[1].url.pathname);
    assert.deepEqual(app.writes()[2].body,app.writes()[1].body);
    assert.equal(app.writes().filter(call=>call.url.pathname.includes(identifier(1))).length,1);
  });

  await test('uncertain outcome retries the same exact commitment before advancing',async()=>{
    let attempts=0;
    const app=await fixture({inspect:call=>{if(call.body!==undefined && ++attempts===1)throw failure(0,'Connection interrupted.');}});
    await app.open();await app.confirm(2);
    await until(()=>app.findButton(/Retry/i),'Uncertain result offers retry');
    assert.equal(app.writes().length,1);
    app.activate(app.findButton(/Retry/i));
    await until(()=>app.writes().length===3,'Retry then remaining request');
    assert.deepEqual(app.writes()[1].body,app.writes()[0].body);
    assert.equal(app.writes()[1].url.pathname,app.writes()[0].url.pathname);
    assert.equal(app.writes()[2].url.pathname.includes(identifier(2)),true);
  });

  await test('stale revision is skipped without changing the confirmed revision',async()=>{
    let attempts=0;
    const app=await fixture({inspect:call=>{if(call.body!==undefined && ++attempts===1)throw failure(409,'Refresh the changed revision.');}});
    await app.open();await app.confirm(2);
    await until(()=>app.writes().length===2,'Later eligible item still receives request');
    await until(()=>/skip|changed/i.test(app.q('dialog-body').textContent),'Changed item is reported');
    assert.equal(app.writes()[0].body.revision,1);assert.equal(app.writes()[1].url.pathname.includes(identifier(2)),true);
    assertNoBodyReads(app);
  });

  await test('a mismatched compact receipt remains uncertain and retries the original request',async()=>{
    let attempts=0;
    const app=await fixture({inspect:call=>{
      if(call.body!==undefined && ++attempts===1)return receipt(call.url,call.body,{note_id:identifier(99)});
    }});
    await app.open();await app.confirm(2);
    await until(()=>app.available(/Retry/i),'An unbound receipt must require retry');
    assert.equal(app.writes().length,1);
    assert.match(app.q('dialog-body').querySelector('.nb-batch-status').textContent,/0 confirmed/);
    app.activate(app.findButton(/Retry/i));
    await until(()=>app.writes().length===3,'Validated retry permits the next item');
    assert.deepEqual(app.writes()[1].body,app.writes()[0].body);
    assert.equal(app.writes()[1].raw,app.writes()[0].raw);
  });

  await test('access denial stops the batch without attempting later notes',async()=>{
    const app=await fixture({inspect:call=>{if(call.body!==undefined)throw failure(403,'Access is no longer active.');}});
    await app.open();await app.confirm(2);
    await until(()=>app.q('dialog-body').querySelector('.nb-error')?.textContent,'Denied request is reported');
    assert.equal(app.writes().length,1);
    assert.match(app.q('dialog-body').querySelector('.nb-batch-status').textContent,/0 confirmed/);
    app.q('close').click();assert.equal(app.q('dialog').open,false);
  });

  await test('closing an in-flight batch warns and stops every subsequent request',async()=>{
    const held=deferred();let prompts=0;
    const app=await fixture({inspect:call=>call.body!==undefined?held.promise:undefined});
    await app.open();await app.confirm(2);await until(()=>app.writes().length===1,'First request is pending');
    app.window.confirm=()=>{prompts++;return false;};app.q('close').click();
    assert.equal(prompts,1);assert.equal(app.q('dialog').open,true);
    app.window.confirm=()=>{prompts++;return true;};app.q('close').click();
    assert.equal(prompts,2);assert.equal(app.q('dialog').open,false);
    held.resolve(receipt(app.writes()[0].url,app.writes()[0].body));
    await tick(); // Drain the resolved response chain, not an arbitrary timed delay.
    assert.equal(app.writes().length,1);assert.equal(app.q('dialog').open,false);
  });

  for(const stop of ['hide','session','account']) await test(stop+' invalidation prevents later sends and late private result rendering',async()=>{
    const held=deferred();
    const app=await fixture({inspect:call=>call.body!==undefined?held.promise:undefined});
    await app.open();await app.confirm(2);await until(()=>app.writes().length===1,'First POST in flight');
    if(stop==='hide')app.controller.hide();else if(stop==='session')app.rotateSession();else app.changeAccount(other);
    held.resolve(receipt(app.writes()[0].url,app.writes()[0].body));
    await tick(); // The held promise and its response handlers settle before this turn.
    assert.equal(app.writes().length,1);assert.equal(app.q('dialog').open,false);
    if(stop!=='hide')assert.equal(app.q('dialog-body').textContent,'');
  });

  await test('overflow index completes metadata pagination before allowing confirmation',async()=>{
    const page2=deferred(), reached=deferred();let preparing=false;
    const app=await fixture({inspect:call=>{
      if(call.url.searchParams.get('projection')==='index'){preparing=true;return index([],{complete:false,overflow_reason:'count_limit'});}
      if(preparing && call.body===undefined && call.url.pathname==='/api/notebook/notes') {
        assert.equal(call.url.searchParams.get('scope'),'mine');assert.equal(call.url.searchParams.get('limit'),'100');
        if(!call.url.searchParams.has('after'))return {notes:[summary(1)],next_after:'cursor-two'};
        assert.equal(call.url.searchParams.get('after'),'cursor-two');reached.resolve();return page2.promise;
      }
    }});
    await app.open();await reached.promise;
    assert.equal(app.writes().length,0);assert.equal(app.available(/^Send /),false);
    page2.resolve({notes:[summary(2)],next_after:null});
    await until(()=>app.findButton('Send 2 publish requests'),'Only complete enumeration allows confirmation');
    app.q('close').click();assert.equal(app.writes().length,0);assertNoBodyReads(app);
  });

  for(const [name,invalid] of [
    ['wrong viewer',data=>({...data,viewer_id:other.id})],['foreign owner row',data=>({...data,notes:[{...data.notes[0],owner:other}]})],['wrong scope',data=>({...data,scope:'all'})],
    ['curated inclusion',data=>({...data,include_curated:true})],['incomplete rows',data=>({...data,complete:false,overflow_reason:'count_limit'})],
    ['duplicate identity',data=>({...data,notes:[data.notes[0],data.notes[0]]})],
    ['mismatched revision hash',data=>({...data,notes:[{...data.notes[0],content_sha256:'f'.repeat(64)}]})],
    ['mismatched publication state',data=>({...data,notes:[{...data.notes[0],publication_status:'approved'}]})],
  ]) await test(name+' refuses confirmation without writes',async()=>{
    const app=await fixture({inspect:call=>call.url.searchParams.get('projection')==='index'?invalid(index([summary(1)])):undefined});
    await app.open();await until(()=>app.q('dialog-body').querySelector('.nb-error')?.textContent,'Invalid preparation is visible');
    assert.equal(app.available(/^Send /),false);assert.equal(app.writes().length,0);
  });

  await test('the enumeration bound refuses an unfinished tenth page without publishing a subset',async()=>{
    let preparing=false, pages=0;
    const app=await fixture({inspect:call=>{
      if(call.url.searchParams.get('projection')==='index'){preparing=true;return index([],{complete:false,overflow_reason:'count_limit'});}
      if(preparing && call.body===undefined && call.url.pathname==='/api/notebook/notes') {
        const offset=pages++*100;
        return {notes:Array.from({length:100},(_,i)=>summary(offset+i+1)),next_after:'page-'+pages};
      }
    }});
    await app.open();await until(()=>app.q('dialog-body').querySelector('.nb-error')?.textContent,'Incomplete bounded scan fails closed');
    assert.equal(pages,10);assert.equal(app.available(/^Send /),false);assert.equal(app.writes().length,0);
  });

  await test('repeating pagination cursor refuses a partial all-notes confirmation',async()=>{
    let preparing=false;
    const app=await fixture({inspect:call=>{
      if(call.url.searchParams.get('projection')==='index'){preparing=true;return index([],{complete:false,overflow_reason:'count_limit'});}
      if(preparing && call.body===undefined && call.url.pathname==='/api/notebook/notes')return {
        notes:[summary(call.url.searchParams.has('after')?2:1)],next_after:'same-cursor'};
    }});
    await app.open();await until(()=>app.q('dialog-body').querySelector('.nb-error')?.textContent,'Cursor loop is rejected');
    assert.equal(app.available(/^Send /),false);assert.equal(app.writes().length,0);
  });
  console.log(`\n${passed} bulk publication checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

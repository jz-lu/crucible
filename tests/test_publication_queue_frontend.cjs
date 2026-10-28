// Publication summaries may be reused; exact review revisions and decisions may not.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const reviewer = {id:'reviewer', handle:'reviewer'};
const requestTypes = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../web/notebook/request-types.json'), 'utf8'));
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject};};
const page = (title='Queued claim', next_after=null) => ({notes:title === null ? [] : [record(title)], next_after});
function record(title='Queued claim', full=false) {
  const revision = {revision:2, content_sha256:'a'.repeat(64), created_at:100, publication_status:'submitted',
    summary:!full, content:{title, claim:'A precise claim', tags:['algebra'], kind:'conjecture',
      ...(full ? {conditions:'Fixture scope', body:'Full private review body'} : {})}};
  return {id:'candidate', owner:{id:'author',handle:'author'}, can_edit:false, can_review:true,
    created_at:90, revision:2, revision_count:1, content_sha256:revision.content_sha256,
    publication_status:'submitted', revisions:[revision]};
}
function installNotebook(app) {
  app.sandbox.crypto.subtle = require('node:crypto').webcrypto.subtle;
  app.sandbox.window.localStorage = {getItem:()=>null, setItem:()=>{throw Error('No persisted review data');}};
  app.sandbox.window.confirm = () => true;
  for (const file of ['store.js','seed.js','notebook.js']) vm.runInContext(
    fs.readFileSync(path.resolve(__dirname, '../web/notebook', file), 'utf8'), app.sandbox, {filename:file});
}
async function boot(respond=()=>undefined, {initial=true, canWarm=()=>true}={}) {
  const app = await createApp({schemes:[], hash:'#about'});
  installNotebook(app);
  let owner=reviewer, csrf='session-one', now=1000000, navigation=Promise.resolve();
  app.sandbox.Date = class extends Date {static now() {return now;}};
  const requests=[], root=app.$('#review-notebook-workspace');
  const timers=new Map();let timerId=0;
  app.sandbox.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,delay});return id;};
  app.sandbox.clearTimeout=id=>timers.delete(id);
  const advanceTimers=async(ms=250)=>{for(const[id,timer]of [...timers])if(timer.delay<=ms){timers.delete(id);await timer.fn();}await flush();};
  const api=async(url,body,options)=>{
    requests.push({url,body,options});
    const result=await respond(url,body,options);
    if(result!==undefined)return result;
    if(url.startsWith('/api/notebook/review-queue?'))return page();
    if(url.startsWith('/api/notebook/notes/candidate'))return {note:record('Queued claim',true)};
    if(url==='/notebook/request-types.json')return requestTypes;
    throw Error('Unexpected request: '+url);
  };
  const controller=app.sandbox.window.CrucibleNotebook.mount(root,{api, account:()=>owner, session:()=>csrf,
    reviewOnly:true, canWarm, navigate:route=>{navigation=controller.show(new URLSearchParams(route.split('?')[1]||''));}});
  const show=extra=>controller.show(new URLSearchParams({section:'publication',...extra}));
  if(initial)await show();
  return {...app,root,controller,show,requests,q:name=>root.querySelector(`[data-nb="${name}"]`),
    navigateDone:()=>navigation, advance:ms=>{now+=ms;}, advanceTimers,
    account:(next,credential=csrf)=>{owner=next;csrf=credential;controller.setAccount(next);}};
}
const queueReads = app => app.requests.filter(row=>row.url.startsWith('/api/notebook/review-queue?'));
const byText = (root,text) => root.querySelectorAll('button').find(node=>node.textContent===text);
let passed=0;
async function test(name, run) {
  let timeout;
  try {
    await Promise.race([run(),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Test did not finish: '+name)),5000);})]);
    passed++;console.log('PASS '+name);
  } finally {clearTimeout(timeout);}
}
(async()=>{
  await test('initial warming prepares exactly two compact pages without hidden rendering or note bodies',async()=>{
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?
      new URLSearchParams(url.split('?')[1]).has('after')?page('Second request','cursor-three'):page('First request','cursor-two'):undefined,{initial:false});
    const first=await app.controller.warm('list'),second=await app.controller.warm('next');
    assert.ok(first.bytes>0);assert.ok(second.bytes>0);assert.equal(queueReads(app).length,2);
    assert.equal(app.q('list').querySelector('.nb-entry'),null);assert.equal(app.root.querySelector('.nb-publication-review'),null);
    assert.equal(app.requests.some(row=>row.url.startsWith('/api/notebook/notes/')),false);
    await app.show();assert.match(app.q('list').textContent,/First request/);assert.equal(app.q('more').disabled,false);
    assert.equal(queueReads(app).length,2,'The just-prepared first page settles without another GET');
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second request/);
    assert.equal(queueReads(app).length,2,'The just-prepared second page settles without another GET');
    assert.ok(queueReads(app).every(row=>new URLSearchParams(row.url.split('?')[1]).get('summary')==='true'));
  });
  await test('visible paging prepares one next page after settling, without recursively crawling the queue',async()=>{
    const app=await boot(url=>{
      if(!url.startsWith('/api/notebook/review-queue?'))return;
      const after=new URLSearchParams(url.split('?')[1]).get('after');
      return after==='two'?page('Second request','three'):after==='three'?page('Third request','four'):after==='four'?page('Fourth request'):page('First request','two');
    });
    assert.equal(queueReads(app).length,1);await app.advanceTimers();assert.equal(queueReads(app).length,2);
    await app.advanceTimers();assert.equal(queueReads(app).length,2);
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second request/);
    assert.equal(queueReads(app).length,2);await app.advanceTimers();assert.equal(queueReads(app).length,3);
    await app.advanceTimers();assert.equal(queueReads(app).length,3,'Third-page preparation must not crawl to page four');
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Third request/);
    assert.equal(queueReads(app).length,3);await app.advanceTimers();assert.equal(queueReads(app).length,4);
  });
  await test('foreground entry joins a pending preparation and full review readers remain fresh',async()=>{
    const late=deferred();let waiting=true;
    const app=await boot(url=>waiting&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined,{initial:false});
    const warm=app.controller.warm('list');const visible=app.show();
    assert.equal(queueReads(app).length,1);late.resolve(page());await Promise.all([warm,visible]);waiting=false;
    assert.match(app.q('list').textContent,/Queued claim/);assert.equal(queueReads(app).length,1);
    await app.show({note:'candidate',revision:'2'});await app.show();await app.show({note:'candidate',revision:'2'});
    assert.equal(app.requests.filter(row=>row.url.startsWith('/api/notebook/notes/candidate')).length,2);
  });
  await test('prepared responses are consumed once and never renew their original five-minute freshness',async()=>{
    const late=deferred();let waiting=false;
    const app=await boot(url=>waiting&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined,{initial:false});
    await app.controller.warm('list');app.advance(9000);await app.show();assert.equal(queueReads(app).length,1);
    app.advance(291001);waiting=true;const expired=app.show();
    assert.equal(app.q('list').querySelector('.nb-entry'),null);late.resolve(page('Fresh request'));await expired;
    await app.show();assert.equal(queueReads(app).length,3,'Only a completed speculative response gets a single no-refetch visit');
  });
  await test('failed, oversized and malformed preparation cannot poison a later explicit read or warm page two',async()=>{
    for(const mode of ['failed','oversized','malformed']){
      let first=true;const huge=page('Too large','two');huge.notes[0].revisions[0].content.claim='x'.repeat(1000001);
      const app=await boot(url=>{
        if(!url.startsWith('/api/notebook/review-queue?')||!first)return;
        if(mode==='failed')throw Error('Offline');
        return mode==='oversized'?huge:{notes:[],next_after:7};
      },{initial:false});
      if(mode==='oversized')await app.controller.warm('list');else await assert.rejects(app.controller.warm('list'));
      await app.controller.warm('next');assert.equal(queueReads(app).length,1);
      first=false;await app.show();assert.equal(queueReads(app).length,2);assert.match(app.q('list').textContent,/Queued claim/);
    }
  });
  await test('hidden tabs, Save-Data, foreground work, readers and navigation suppress optional next-page preparation',async()=>{
    for(const mode of ['hidden','save-data','busy','reader','hide']){
      let allowed=true;
      const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?page('First request','two'):undefined,{canWarm:()=>allowed});
      if(mode==='hidden')app.document.hidden=true;
      if(mode==='save-data')app.sandbox.navigator.connection={saveData:true};
      if(mode==='busy')allowed=false;
      if(mode==='reader')await app.show({note:'candidate',revision:'2'});
      if(mode==='hide')app.controller.hide();
      await app.advanceTimers();assert.equal(queueReads(app).length,1,mode+' suppresses pending preparation');
      if(['hidden','save-data','busy'].includes(mode)){await app.controller.warm('next');assert.equal(queueReads(app).length,1);}
    }
  });
  await test('brief overlapping work defers one-ahead preparation; waiting is bounded and navigation still cancels it',async()=>{
    for(const mode of ['idle','deadline','late-idle','hide','reader','failure']){
      let allowed=false;
      const app=await boot(url=>{
        if(!url.startsWith('/api/notebook/review-queue?'))return;
        if(mode==='failure'&&new URLSearchParams(url.split('?')[1]).has('after'))throw Error('Offline');
        return page('First request','two');
      },{canWarm:()=>allowed});
      await app.advanceTimers();assert.equal(queueReads(app).length,1);
      if(mode==='deadline'){app.advance(10000);await app.advanceTimers();}
      if(mode==='late-idle')app.advance(10000);
      if(mode==='hide')app.controller.hide();
      if(mode==='reader')await app.show({note:'candidate',revision:'2'});
      allowed=true;await app.advanceTimers();await app.advanceTimers();
      assert.equal(queueReads(app).length,['idle','failure'].includes(mode)?2:1,mode);
      await app.advanceTimers();assert.equal(queueReads(app).length,['idle','failure'].includes(mode)?2:1,'No recursive preparation or automatic network retry');
    }
  });
  for(const change of ['account','session','write','denial'])await test('late prepared page cannot repopulate summaries after '+change,async()=>{
    const late=deferred();let waiting=false;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?
      waiting?late.promise:page('First request','two'):undefined,{initial:false});
    await app.controller.warm('list');waiting=true;const warming=app.controller.warm('next');
    if(change==='account')app.account({id:'other',handle:'other'});
    if(change==='session')app.account(reviewer,'rotated');
    if(change==='write')app.controller.invalidateReads();
    if(change==='denial'){late.reject(Object.assign(Error('Reviewer access revoked'),{status:403}));await assert.rejects(warming);}
    else{late.resolve(page('Old private second page'));await warming;}
    waiting=false;const current=app.show();assert.doesNotMatch(app.q('list').textContent,/First request|Old private second page/);await current;
    assert.equal(queueReads(app).length,3);assert.doesNotMatch(app.q('list').textContent,/Old private second page/);
  });
  await test('returning queue paints compact summaries immediately and preserves unchanged expansion and focus',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined);
    assert.equal(new URLSearchParams(queueReads(app)[0].url.split('?')[1]).get('summary'),'true');
    app.q('list').querySelector('.nb-title').click();
    const row=app.q('list').querySelector('.nb-entry'), title=row.querySelector('.nb-title');title.focus();
    app.controller.hide();held=true;const pending=app.show();
    assert.equal(app.q('list').querySelector('.nb-entry'),row);
    assert.equal(app.q('list').querySelector('.nb-reading'),null);
    assert.equal(app.q('list').getAttribute('aria-busy'),'true');
    assert.equal(app.q('list').querySelector('.nb-preview').hidden,false);
    assert.equal(app.requests.some(row=>row.url.startsWith('/api/notebook/notes/')),false,'Expanding a review summary never prefetches its body');
    assert.equal(app.root.querySelector('.nb-publication-review'),null);
    late.resolve(page());await pending;
    assert.equal(app.q('list').querySelector('.nb-entry'),row);
    assert.equal(app.document.activeElement,title);
    assert.equal(app.q('list').getAttribute('aria-busy'),null);
  });
  await test('an empty cached queue stays settled during refresh instead of becoming a loading page',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?(held?late.promise:page(null)):undefined);
    const empty=app.q('list').querySelector('.nb-empty');held=true;
    const pending=app.show();assert.equal(app.q('list').querySelector('.nb-empty'),empty);
    assert.equal(app.q('list').querySelector('.nb-reading'),null);
    late.resolve(page(null));await pending;assert.equal(app.q('list').querySelector('.nb-empty'),empty);
  });
  await test('repeated entry and leaving then returning share one exact in-flight queue GET',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined);
    held=true;const first=app.show();app.controller.hide();const second=app.show();const third=app.show();
    assert.equal(queueReads(app).length,2);
    const outside=app.document.createElement('button');app.document.body.append(outside);outside.focus();
    late.resolve(page('New queued claim'));await Promise.all([first,second,third]);
    assert.match(app.q('list').textContent,/New queued claim/);
    assert.equal(app.document.activeElement,outside,'Fresh summary must not steal unrelated focus');
  });
  await test('queue pages are cached by the exact cursor and never substituted for another page',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?
      (held?late.promise:new URLSearchParams(url.split('?')[1]).has('after')?page('Second queued claim'):page('First queued claim','cursor-two')):undefined);
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second queued claim/);
    assert.equal(new URLSearchParams(queueReads(app)[1].url.split('?')[1]).get('after'),'cursor-two');
    held=true;app.q('prev').click();
    assert.match(app.q('list').textContent,/First queued claim/);assert.doesNotMatch(app.q('list').textContent,/Second queued claim/);
    late.resolve(page('First queued claim','cursor-two'));await flush();
    assert.equal(app.q('page').textContent,'Page 1');
  });
  await test('a transient refresh failure retains valid queue rows and explicit retry without extending freshness',async()=>{
    let failure=false, held=null;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?
      held?.promise|| (failure?Promise.reject(Error('Temporary network failure')):page()):undefined);
    failure=true;await app.show();
    assert.match(app.q('list').textContent,/Queued claim/);
    assert.match(app.q('notice').textContent,/could not be refreshed/);assert.ok(byText(app.q('notice'),'Try again'));
    app.controller.hide();held=deferred();const returning=app.show();
    assert.match(app.q('list').textContent,/Queued claim/);held.resolve(page());await returning;
    held=null;failure=true;app.advance(299000);await app.show();
    app.advance(1001);held=deferred();const expired=app.show();
    assert.doesNotMatch(app.q('list').textContent,/Queued claim/,'A failed refresh must not renew the five-minute cache');
    held.resolve(page('Fresh after expiry'));await expired;
    failure=true;held=null;await app.show();failure=false;
    byText(app.q('notice'),'Try again').click();await flush();assert.equal(app.q('notice').hidden,true);
  });
  await test('an invalidated review cursor discards the cached page and restarts at the first page',async()=>{
    let expired=false;
    const app=await boot(url=>{
      if(!url.startsWith('/api/notebook/review-queue?'))return;
      if(!new URLSearchParams(url.split('?')[1]).has('after'))return page('First queued claim','cursor-two');
      if(expired)throw Object.assign(Error('Invalid sharing review cursor.'),{status:422});
      return page('Second queued claim');
    });
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second queued claim/);
    expired=true;await app.show();assert.doesNotMatch(app.q('list').textContent,/Second queued claim/);
    byText(app.q('list'),'Restart publish requests').click();await flush();
    assert.match(app.q('list').textContent,/First queued claim/);
    assert.equal(new URLSearchParams(queueReads(app).at(-1).url.split('?')[1]).has('after'),false);
  });
  await test('oversized queue summaries are not retained beyond the existing cache bound',async()=>{
    const late=deferred();let held=false;const huge=page();huge.notes[0].revisions[0].content.claim='x'.repeat(1000001);
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?(held?late.promise:huge):undefined);
    held=true;const pending=app.show();assert.equal(app.q('list').querySelector('.nb-entry'),null);
    late.resolve(page());await pending;
  });
  for(const change of ['account','session']) await test(change+' changes retire cached and in-flight data without letting old cleanup remove a replacement',async()=>{
    const old=deferred(),fresh=deferred();let phase=0;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?(phase===1?old.promise:phase===2?fresh.promise:page()):undefined);
    phase=1;const previous=app.show();
    app.account(change==='account'?{id:'other',handle:'other'}:reviewer,'session-two');
    phase=2;const next=app.show();assert.doesNotMatch(app.q('list').textContent,/Queued claim/);
    old.resolve(page('Old private claim'));await previous;
    const same=app.show();assert.equal(queueReads(app).length,3,'Old completion cannot erase the replacement pending task');
    fresh.resolve(page('Current claim'));await Promise.all([next,same]);
    assert.match(app.q('list').textContent,/Current claim/);assert.doesNotMatch(app.q('list').textContent,/Old private claim/);
  });
  await test('a denied refresh removes cached summaries, including a denial after leaving the queue',async()=>{
    for(const hidden of [false,true]){
      const late=deferred();let held=false;
      const app=await boot(url=>held&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined);
      held=true;const pending=app.show();if(hidden)app.controller.hide();
      late.reject(Object.assign(Error('Reviewer access revoked'),{status:403}));await pending;
      // The denied promise still rejects; the important boundary is before awaiting it.
      const returning=app.show();assert.doesNotMatch(app.q('list').textContent,/Queued claim/);await returning;
      assert.doesNotMatch(app.q('list').textContent,/Queued claim/);
      app.account(null);assert.equal(app.q('list').textContent,'');
    }
  });
  await test('page-two denial retires page-one success after Research changes and allows an explicit fresh retry',async()=>{
    const old=deferred(),fresh=deferred(),retry=deferred();let phase='initial';
    const app=await boot(url=>{
      if(url.startsWith('/api/notebook/research-requests'))return {requests:[],next_after:null};
      if(!url.startsWith('/api/notebook/review-queue?'))return;
      const second=new URLSearchParams(url.split('?')[1]).has('after');
      if(phase==='old'&&second)return old.promise;
      if(phase==='fresh'&&!second)return fresh.promise;
      if(phase==='retry')return retry.promise;
      return second?page('Second request'):page('First request','cursor-two');
    });
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second request/);
    phase='old';const pendingOld=app.show();
    await app.show({section:'research'});
    phase='fresh';const pendingFresh=app.show();assert.equal(queueReads(app).length,4);
    old.reject(Object.assign(Error('Reviewer access revoked'),{status:403}));await pendingOld;
    assert.match(app.q('list').textContent,/Reviewer access revoked/);
    assert.equal(app.q('list').getAttribute('aria-busy'),null);
    assert.equal(app.q('pagination').hidden,true);assert.equal(app.q('count').textContent,'');
    fresh.resolve(page('Admitted before old denial'));await pendingFresh;
    assert.doesNotMatch(app.q('list').textContent,/Admitted before old denial/);
    phase='retry';byText(app.q('list'),'Try again').click();await flush();
    assert.equal(queueReads(app).length,5,'Retry uses a new task, never a retired in-flight result');
    assert.doesNotMatch(app.q('list').textContent,/First request|Second request|Admitted before old denial/);
    retry.resolve(page('Freshly authorized'));await flush();assert.match(app.q('list').textContent,/Freshly authorized/);
    app.controller.hide();const returning=app.show();assert.match(app.q('list').textContent,/Freshly authorized/);await returning;
  });
  await test('queue denial clears an active pending exact review reader instead of stranding its busy frame',async()=>{
    const old=deferred(),reader=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/review-queue?')?old.promise:
      held&&url.startsWith('/api/notebook/notes/candidate')?reader.promise:undefined);
    held=true;const pendingQueue=app.show();const pendingReader=app.show({note:'candidate',revision:'2'});
    old.reject(Object.assign(Error('Reviewer access revoked'),{status:403}));await pendingQueue;
    assert.match(app.q('full').textContent,/Reviewer access revoked/);assert.ok(byText(app.q('full'),'Try again'));
    reader.resolve({note:record('Old authorized body',true)});await pendingReader;
    assert.doesNotMatch(app.q('full').textContent,/Old authorized body|Full private review body/);
    assert.equal(app.root.querySelector('.nb-publication-review'),null);
    held=false;byText(app.q('full'),'Try again').click();await flush();assert.ok(app.root.querySelector('.nb-publication-review'));
  });
  for(const change of ['account','session']) await test('late denial from an old '+change+' cannot clear the replacement authorized queue',async()=>{
    const old=deferred(),fresh=deferred();let phase=0;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?(phase===1?old.promise:phase===2?fresh.promise:page()):undefined);
    phase=1;const previous=app.show();app.account(change==='account'?{id:'other',handle:'other'}:reviewer,'session-two');
    phase=2;const next=app.show();old.reject(Object.assign(Error('Old forbidden read'),{status:403}));await previous;
    const same=app.show();assert.equal(queueReads(app).length,3,'Old denial cannot detach the new owner task');
    fresh.resolve(page('Replacement authorized'));await Promise.all([next,same]);
    assert.match(app.q('list').textContent,/Replacement authorized/);assert.doesNotMatch(app.q('list').textContent,/Old forbidden read/);
  });
  await test('write invalidation prevents an older queue response from overwriting newer results',async()=>{
    const old=deferred(),fresh=deferred();let phase=0;
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?(phase===1?old.promise:phase===2?fresh.promise:page('Old request')):undefined);
    phase=1;const previous=app.show();app.controller.invalidateReads();phase=2;const next=app.show();
    assert.doesNotMatch(app.q('list').textContent,/Old request/);fresh.resolve(page('After mutation'));await next;
    old.resolve(page('Obsolete request'));await previous;
    const returning=app.show();assert.match(app.q('list').textContent,/After mutation/);await returning;
    assert.doesNotMatch(app.q('list').textContent,/Obsolete request/);
  });
  await test('cached summaries never unlock review forms or cache full revisions',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/notes/candidate')?late.promise:undefined);
    await app.show({note:'candidate',revision:'2'});assert.ok(app.root.querySelector('.nb-publication-review'));
    await app.show();held=true;
    const pending=app.show({note:'candidate',revision:'2'});
    assert.equal(app.root.querySelector('.nb-publication-review'),null);
    assert.doesNotMatch(app.q('full').textContent,/Full private review body/);
    assert.equal(app.requests.filter(row=>row.url.startsWith('/api/notebook/notes/candidate')).length,2);
    assert.match(app.requests.at(-1).url,/revision=2/);
    late.resolve({note:record('Fresh exact candidate',true)});await pending;
    assert.ok(app.root.querySelector('.nb-publication-review'));assert.match(app.q('full').textContent,/Fresh exact candidate/);
  });
  await test('a confirmed review decision invalidates the queue before returning to it',async()=>{
    let decided=false;const late=deferred();
    const app=await boot((url,body)=>url.endsWith('/publication-reviews')?(decided=true,{note:record('Approved',true)}):
      decided&&url.startsWith('/api/notebook/review-queue?')?late.promise:undefined);
    await app.show({note:'candidate',revision:'2'});
    app.root.querySelector('[name="reason"]').value='Suitable for discussion.';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();await app.navigateDone();
    assert.equal(app.requests.filter(row=>row.body).length,1);
    const pending=app.show();assert.doesNotMatch(app.q('list').textContent,/Queued claim/);
    late.resolve(page(null));await pending;assert.match(app.q('list').textContent,/No publish requests awaiting approval/);
  });
  await test('shell publication, review, edit and trash writes invalidate the separate reviewer cache',async()=>{
    let held=null;
    const app=await createApp({schemes:[],user:reviewer,hash:'#about',responder:async(url,options,envelope)=>{
      if(url.pathname==='/api/session')return envelope({user:reviewer,csrf_token:'test',capabilities:{review_protocol_proposals:true}});
      if(url.pathname==='/api/notebook/review-queue')return held?held.promise:page();
      if(options.method!=='GET'&&url.pathname.startsWith('/api/notebook/notes/'))return envelope({ok:true});
    }});
    installNotebook(app);await app.navigate('#reviews?section=publication');
    const root=app.$('#review-notebook-workspace'),list=()=>root.querySelector('[data-nb="list"]');
    for(const [suffix,method] of [['/publication-requests','POST'],['/publication-reviews','POST'],['','PUT'],['/trash','POST']]){
      assert.match(list().textContent,/Queued claim/);await app.navigate('#about');
      await app.sandbox.api('/api/notebook/notes/candidate'+suffix,{fixture:true},{method});
      held=deferred();const pending=app.navigate('#reviews?section=publication');await flush();
      assert.doesNotMatch(list().textContent,/Queued claim/,method+' '+suffix+' retires the cached queue');
      held.resolve(page());await pending;await flush();held=null;
    }
  });
  console.log(`${passed} publication-queue frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

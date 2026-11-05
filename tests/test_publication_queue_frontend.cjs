// Publication summaries may be reused; exact review revisions and decisions may not.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const reviewer = {id:'reviewer', handle:'reviewer'};
const requestTypes = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../web/notebook/request-types.json'), 'utf8'));
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject};};
const page = (title='Queued claim', next_after=null, screening='attention') => ({notes:title === null ? [] : [record(title)], next_after, screening});
function record(title='Queued claim', full=false) {
  const revision = {revision:2, content_sha256:'a'.repeat(64), created_at:100, publication_status:'submitted', publication:{requested_at:99},
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
  let owner=reviewer, csrf='session-one', now=1000000, navigation=Promise.resolve(), lastRoute=null;
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
    if(url.startsWith('/api/notebook/review-queue?'))return page('Queued claim', null, new URLSearchParams(url.split('?')[1]).get('screening'));
    if(url.startsWith('/api/notebook/notes/candidate'))return {note:record('Queued claim',true)};
    if(url==='/notebook/request-types.json')return requestTypes;
    throw Error('Unexpected request: '+url);
  };
  const controller=app.sandbox.window.CrucibleNotebook.mount(root,{api, account:()=>owner, session:()=>csrf,
    reviewOnly:true, canWarm, navigate:route=>{lastRoute=route;navigation=controller.show(new URLSearchParams(route.split('?')[1]||''));}});
  const show=extra=>controller.show(new URLSearchParams({section:'flagged',...extra}));
  if(initial)await show();
  return {...app,root,controller,show,requests,q:name=>root.querySelector(`[data-nb="${name}"]`),
    navigateDone:()=>navigation, lastRoute:()=>lastRoute, advance:ms=>{now+=ms;}, advanceTimers,
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
    assert.ok(queueReads(app).every(row=>new URLSearchParams(row.url.split('?')[1]).get('screening')==='attention'));
  });
  await test('legacy publication entry and flagged entry share one human-attention queue and cursor',async()=>{
    const app=await boot(url=>{
      if(!url.startsWith('/api/notebook/review-queue?'))return;
      const query=new URLSearchParams(url.split('?')[1]);
      assert.equal(query.get('screening'),'attention');
      return page(query.has('after')?'Second attention request':'First attention request',query.has('after')?null:'next-attention');
    });
    app.q('more').click();await flush();assert.match(app.q('list').textContent,/Second attention request/);
    await app.show({section:'publication'});assert.match(app.q('list').textContent,/Second attention request/);
    assert.equal(new URLSearchParams(queueReads(app).at(-1).url.split('?')[1]).get('after'),'next-attention');
    assert.equal(app.q('list').getAttribute('aria-label'),'Flagged requests');
  });
  await test('legacy and current warm calls join the exact same pending attention summary read',async()=>{
    const late=deferred();
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?late.promise:undefined,{initial:false});
    const old=app.controller.warm('list','publication'),current=app.controller.warm('list','flagged');
    assert.equal(queueReads(app).length,1);
    assert.equal(new URLSearchParams(queueReads(app)[0].url.split('?')[1]).get('screening'),'attention');
    late.resolve(page('Needs human review'));await Promise.all([old,current]);
    await app.show({section:'flagged'});assert.match(app.q('list').textContent,/Needs human review/);
    assert.equal(queueReads(app).length,1);
  });
  await test('missing or mismatched screening echoes never admit, cache or warm mixed queue rows',async()=>{
    for(const scope of ['attention'])for(const echo of [undefined,'flagged','unflagged']){
      let valid=false;
      const app=await boot(url=>{
        if(!url.startsWith('/api/notebook/review-queue?'))return;
        const result=page(valid?'Correct filtered title':'Mixed private title','next',scope);
        if(!valid){if(echo===undefined)delete result.screening;else result.screening=echo;}return result;
      },{initial:false});
      const section='flagged';
      await assert.rejects(app.controller.warm('list',section),/queue is unavailable/);
      await app.controller.warm('next',section);assert.equal(queueReads(app).length,1);
      await app.show({section});assert.doesNotMatch(app.q('list').textContent,/Mixed private title/);
      assert.match(app.q('list').textContent,/queue is unavailable/);assert.equal(app.q('list').querySelector('.nb-entry'),null);
      valid=true;byText(app.q('list'),'Try again').click();await flush();assert.match(app.q('list').textContent,/Correct filtered title/);
    }
  });
  await test('flagged summaries render only fixed category labels and limitations, never model text or markup',async()=>{
    const result=page('Flagged <img src=x onerror=alert(1)>');
    result.notes[0].revisions[0].publication.screening={verification:'summary_only',status:'human_review',attention_reason:'content_findings',reason:'PRIVATE_REASON',
      categories:[{code:'spam',label:'<script>PRIVATE_LABEL</script>',count:2},{code:'<img>',label:'PRIVATE_UNKNOWN',count:1}],
      concern_count:2,limitation_count:1,reviewer:{concerns:[{explanation:'PRIVATE_MODEL_TEXT'}]}};
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?result:undefined,{initial:false});
    await app.show({section:'flagged'});
    assert.match(app.q('list').textContent,/Flagged <img src=x onerror=alert\(1\)>/);
    assert.equal(app.q('list').querySelector('.nb-screening-summary').textContent,'Possible spam (2) · Screening limitation');
    assert.doesNotMatch(app.q('list').textContent,/PRIVATE_REASON|PRIVATE_LABEL|PRIVATE_UNKNOWN|PRIVATE_MODEL_TEXT/);
    assert.equal(app.q('list').querySelectorAll('img,script').length,0);assert.equal(app.root.querySelector('.nb-publication-review'),null);
    assert.equal(app.requests.some(row=>row.body||row.url.startsWith('/api/notebook/notes/')),false);
  });
  await test('flagged readers preserve exact links and back navigation without granting self-review',async()=>{
    const app=await boot(url=>{if(url.startsWith('/api/notebook/notes/candidate')){const own=record('Own flagged request',true);own.owner=reviewer;own.can_review=false;return{note:own};}},{initial:false});
    await app.show({section:'flagged'});app.q('list').querySelector('.nb-title').click();
    byText(app.q('list'),'Read full note →').click();await app.navigateDone();
    assert.equal(app.lastRoute(),'#reviews?section=flagged&note=candidate&revision=2');
    assert.equal(app.root.querySelector('.nb-publication-review'),null);assert.match(app.q('full').textContent,/Another reviewer/);
    byText(app.q('full'),'← Flagged requests').click();await app.navigateDone();
    assert.equal(app.lastRoute(),'#reviews?section=flagged');
    assert.equal(new URLSearchParams(queueReads(app).at(-1).url.split('?')[1]).get('screening'),'attention');
  });
  await test('human-attention rows distinguish model concerns from unavailable screening and manual review',async()=>{
    const result=page();
    result.notes=Array.from(['content_findings','screening_unavailable','manual_review_needed'],(reason,index)=>{
      const row=record('Attention request '+index);row.id='candidate-'+index;
      row.revisions[0].publication.screening={verification:'summary_only',attention_reason:reason,attention_label:'UNTRUSTED_LABEL',
        reason:'UNTRUSTED_REASON',categories:[{code:'spam',count:1}],limitation_count:0};return row;
    });
    const app=await boot(url=>url.startsWith('/api/notebook/review-queue?')?result:undefined);
    const summaries=app.q('list').querySelectorAll('.nb-screening-summary').map(node=>node.textContent);
    assert.deepEqual(summaries,['Possible spam','Screening unavailable · Manual review needed','Manual review needed']);
    assert.doesNotMatch(app.q('list').textContent,/UNTRUSTED_LABEL|UNTRUSTED_REASON/);
    await app.show({section:'flagged',research:'1',research_request:'old-research'});
    assert.equal(app.requests.some(row=>row.url.includes('/research-requests')),false);
  });
  for(const change of ['account','session'])await test('switching '+change+' retires the attention queue cache and the pending flagged read',async()=>{
    const old=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/review-queue?')&&new URLSearchParams(url.split('?')[1]).get('screening')==='attention'?old.promise:undefined);
    await app.show({section:'flagged'});held=true;const pending=app.show({section:'flagged'});
    app.account(change==='account'?{id:'replacement',handle:'replacement'}:reviewer,'new-session');
    assert.equal(app.q('list').textContent,'');old.resolve(page('Old flagged private title'));await pending;
    assert.doesNotMatch(app.q('list').textContent,/Old flagged private title|Queued claim/);
    held=false;await app.show();assert.match(app.q('list').textContent,/Queued claim/);
    await app.show({section:'flagged'});assert.match(app.q('list').textContent,/Queued claim/);
    assert.doesNotMatch(app.q('list').textContent,/Old flagged private title/);
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
    byText(app.q('list'),'Restart flagged requests').click();await flush();
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
    late.resolve(page(null));await pending;assert.match(app.q('list').textContent,/No flagged requests awaiting approval/);
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
  const dirtyReview = app => {
    const reason = app.root.querySelector('[name="reason"]'), decision = app.root.querySelector('[name="decision"]');
    reason.value = 'Check the attribution and exact scope.'; reason.dispatchEvent({type:'input'});
    decision.value = 'changes_requested'; decision.dispatchEvent({type:'change'});
  };
  const warnsOnUnload = app => {const event={type:'beforeunload'};app.sandbox.window.dispatchEvent(event);return event.defaultPrevented===true;};
  await test('review decision and reason survive evidence navigation but await a fresh authorized exact reader',async()=>{
    const late=deferred();let held=false;
    const app=await boot(url=>held&&url.startsWith('/api/notebook/notes/candidate')?late.promise:undefined);
    await app.show({note:'candidate',revision:'2'});dirtyReview(app);
    assert.equal(warnsOnUnload(app),true);
    app.controller.hide();await app.show();assert.equal(warnsOnUnload(app),true);
    held=true;const reopening=app.show({note:'candidate',revision:'2'});
    assert.equal(app.root.querySelector('[name="reason"]'),null);
    late.resolve({note:record('Queued claim',true)});await reopening;
    assert.equal(app.root.querySelector('[name="reason"]').value,'Check the attribution and exact scope.');
    assert.equal(app.root.querySelector('[name="decision"]').value,'changes_requested');
    assert.equal(app.requests.filter(row=>row.url.startsWith('/api/notebook/notes/candidate')).length,2);
    assert.equal(app.requests.some(row=>row.body),false);
  });
  await test('different note, revision, hash or request timestamp cannot inherit a review draft and require explicit discard',async()=>{
    for(const change of ['note','revision','hash','request']){
      let selected=record('Queued claim',true);
      const app=await boot(url=>url.startsWith('/api/notebook/notes/')?{note:selected}:undefined);
      await app.show({note:'candidate',revision:'2'});dirtyReview(app);await app.show();
      selected=record('Different request',true);
      if(change==='note')selected.id='different-note';
      if(change==='revision')selected.revisions[0].revision=3;
      if(change==='hash')selected.revisions[0].content_sha256='b'.repeat(64);
      if(change==='request')selected.revisions[0].publication.requested_at=100;
      await app.show({note:selected.id,revision:String(selected.revisions[0].revision)});
      assert.equal(app.root.querySelector('[name="reason"]'),null,change);
      assert.match(app.q('full').textContent,/review draft for another request/);
      app.sandbox.window.confirm=()=>false;
      byText(app.q('full'),'Discard draft and review this request').click();await flush();
      assert.equal(app.root.querySelector('[name="reason"]'),null);
      assert.equal(warnsOnUnload(app),true);
      app.sandbox.window.confirm=()=>true;
      byText(app.q('full'),'Discard draft and review this request').click();await app.navigateDone();
      assert.equal(app.root.querySelector('[name="reason"]').value,'');
      assert.equal(app.root.querySelector('[name="decision"]').value,'approved');
      assert.equal(warnsOnUnload(app),false);
      assert.equal(app.requests.some(row=>row.body),false);
    }
  });
  await test('an uncertain decision retains its frozen payload after leaving, fresh return and retry',async()=>{
    const late=deferred(),writes=[];let finished=false;
    const app=await boot((url,body)=>{
      if(url.endsWith('/publication-reviews')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)return late.promise;finished=true;return{};}
      if(url.startsWith('/api/notebook/notes/candidate')){const note=record('Queued claim',true);if(finished)note.revisions[0].publication_status='changes_requested';return{note};}
    });
    await app.show({note:'candidate',revision:'2'});dirtyReview(app);
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    await app.show();late.reject(Object.assign(Error('Lost response'),{status:0}));await flush();
    await app.show({note:'candidate',revision:'2'});
    const reason=app.root.querySelector('[name="reason"]');assert.equal(reason.disabled,true);
    assert.equal(reason.value,'Check the attribution and exact scope.');
    assert.ok(byText(app.q('full'),'Retry the same request'));
    reason.value='Mutation must not change a frozen retry';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();await app.navigateDone();
    assert.deepEqual(writes[1],writes[0]);assert.equal(writes.length,2);
    assert.equal(app.root.querySelector('.nb-form'),null);
    assert.equal(warnsOnUnload(app),false);
  });
  await test('account, session, permission loss and finished server reviews clear retained review drafts',async()=>{
    for(const change of ['account','session','denial','can-review','finished']){
      let denied=false,canReview=true,status='submitted';
      const app=await boot(url=>{
        if(url.startsWith('/api/notebook/notes/candidate')){
          if(denied)throw Object.assign(Error('Access denied'),{status:403});
          const note=record('Queued claim',true);note.can_review=canReview;note.revisions[0].publication_status=status;return{note};
        }
      });
      await app.show({note:'candidate',revision:'2'});dirtyReview(app);
      if(change==='account')app.account({id:'other',handle:'other'});
      if(change==='session')app.account(reviewer,'new-session');
      if(change==='denial')denied=true;
      if(change==='can-review')canReview=false;
      if(change==='finished')status='approved';
      await app.show({note:'candidate',revision:'2'});
      assert.equal(warnsOnUnload(app),false,change);
      denied=false;canReview=true;status='submitted';
      await app.show({note:'candidate',revision:'2'});
      assert.equal(app.root.querySelector('[name="reason"]').value,'',change);
      assert.equal(app.requests.some(row=>row.body),false);
    }
  });
  await test('a confirmed review receipt clears its draft while away without navigating off evidence',async()=>{
    const late=deferred();let finished=false;
    const app=await boot((url,body)=>{
      if(url.endsWith('/publication-reviews'))return late.promise;
      if(url.startsWith('/api/notebook/notes/candidate')){const note=record('Queued claim',true);if(finished)note.revisions[0].publication_status='approved';return{note};}
    });
    await app.show({note:'candidate',revision:'2'});dirtyReview(app);
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    await app.show();finished=true;late.resolve({});await flush();
    assert.equal(app.q('full').hidden,true);
    assert.equal(warnsOnUnload(app),false);
    await app.show({note:'candidate',revision:'2'});assert.equal(app.root.querySelector('.nb-form'),null);
  });
  await test('a late denial for an old account cannot clear a replacement reviewer draft',async()=>{
    const late=deferred();
    const app=await boot((url,body)=>url.endsWith('/publication-reviews')?late.promise:undefined);
    await app.show({note:'candidate',revision:'2'});dirtyReview(app);
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    app.account({id:'replacement',handle:'replacement'},'replacement-session');
    await app.show({note:'candidate',revision:'2'});dirtyReview(app);
    late.reject(Object.assign(Error('Old permission denied'),{status:403}));await flush();
    assert.equal(app.root.querySelector('[name="reason"]').value,'Check the attribution and exact scope.');
    assert.equal(app.root.querySelector('[name="reason"]').disabled,false);
    assert.equal(warnsOnUnload(app),true);
    assert.doesNotMatch(app.q('notice').textContent,/Old permission/);
  });
  await test('a missing exact request timestamp cannot unlock a review form',async()=>{
    const app=await boot(url=>{if(url.startsWith('/api/notebook/notes/candidate')){const note=record('Queued claim',true);delete note.revisions[0].publication;return{note};}});
    await app.show({note:'candidate',revision:'2'});
    assert.equal(app.root.querySelector('.nb-form'),null);
    assert.match(app.q('full').textContent,/exact publication request could not be confirmed/);
    assert.equal(app.requests.some(row=>row.body),false);
  });
  console.log(`${passed} publication-queue frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

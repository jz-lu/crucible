const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'alice', handle: 'alice'};
const scheme = {id:'fixture', version:'1', name:'Fixture', profiles:[{id:'stage-1',instance_size:1}]};
const submission = {id:'receipt',scheme_id:'fixture',scheme_version:'1',profile_id:'stage-1',method_name:'Attack method',received_at:100,status:'incorrect'};
const note = (id, tags = [], extra = {}) => ({id,revision:2,created_at:100,publication_status:'private',
  revisions:[{revision:2,created_at:101,content:{title:'Note '+id,tags},requests:[]}],...extra});
const task = (id, extra = {}) => ({id,title:'Bounty '+id,status:'open',owner:user,created_at:100,...extra});
const bountyPage = (bounties, offset = 0, has_more = false) => ({bounties,pagination:{offset,limit:25,has_more}});
const deferred = () => {let resolve; const promise = new Promise(r => {resolve=r;});return {promise,resolve};};
async function boot({hash='#activity',hook,features} = {}) {
  const app=await createApp({schemes:[scheme],user,hash,bootstrap:true,responder:async(url,options,envelope)=>{
    const overridden=await hook?.(url,options,envelope); if(overridden!==undefined)return overridden;
    if(url.pathname==='/api/me/submissions')return envelope({submissions:[submission]});
    if(url.pathname==='/api/notebook/notes')return envelope({notes:[note('attack',['attack-analysis']),note('scheme',['proposal','scheme-description'])],next_after:null});
    if(url.pathname==='/api/bounties')return envelope(bountyPage([task('own'),task('contributed',{owner:{id:'bob',handle:'bob'}})]));
  }});
  if(features)app.window.CrucibleFeatures=features;
  return app;
}
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('activity is an independent full-page destination with ordered sections and active navigation',async()=>{
    const app=await boot();
    assert.equal(app.$('#activity-workspace').hidden,false);assert.equal(app.$('#catalogue-workspace').hidden,true);
    assert.equal(app.document.querySelector('#history-dialog'),null);
    assert.equal(app.$('#history-button').textContent.trim(),'My activity');
    assert.equal(app.$('#history-button').getAttribute('aria-current'),'page');
    assert.equal(app.document.title,'My activity — Crucible');
    assert.deepEqual(app.$('#activity-workspace').querySelectorAll('h2').map(el=>el.textContent),['Schemes','Bounties','Notes']);
    assert.equal(app.requests.some(row=>row.url.pathname==='/api/schemes'),false);
    assert.match(app.$('#history-content').textContent,/Attack method.*Incorrect/);
    assert.match(app.$('#activity-bounties').textContent,/Proposed by you.*You contributed/);
    const notes=app.$('#activity-notes').querySelectorAll('a');
    assert.match(notes[0].textContent,/Attack/);assert.match(notes[1].textContent,/Proposed scheme/);
    assert.equal(notes[1].querySelector('.activity-scheme-tag').textContent,'Proposed scheme');
    assert.equal(notes[0].querySelector('.activity-scheme-tag'),null);
    assert.equal(notes[0].href,'#notebook?note=attack&revision=2');
    assert.equal(app.$('#activity-bounties').querySelector('a').href,'#bounties?status=mine&bounty=own');
    assert.equal(app.requests.some(row=>row.options.method==='POST'),false);
  });
  await test('notes include scheme proposals attached to earlier revisions and ordinary notes',async()=>{
    const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({notes:[
      note('proposal',[],{proposal_revision:{revision:1,requests:[{kind:'scheme_admission'}]}}),note('ordinary')],next_after:null}):undefined});
    const rows=app.$('#activity-notes').querySelectorAll('a');assert.match(rows[0].textContent,/Proposed scheme/);assert.match(rows[1].textContent,/Note · Revision/);
    assert.equal(rows[0].querySelector('.activity-scheme-tag').textContent,'Proposed scheme');
    assert.equal(rows[0].querySelector('.history-item-meta').textContent.startsWith('Revision '),true);
  });
  await test('slow or failed sections keep the other sections readable and retry independently',async()=>{
    const held=deferred();const app=await boot({hook:url=>url.pathname==='/api/notebook/notes'?held.promise:undefined});
    assert.match(app.$('#history-content').textContent,/Attack method/);assert.match(app.$('#activity-bounties').textContent,/Bounty own/);
    assert.ok(app.$('#activity-notes').querySelector('.history-preview'));
    held.resolve(app.envelope({notes:null,next_after:null}));await flush();
    assert.match(app.$('#activity-notes').textContent,/could not be loaded/);
    const oldFetch=app.sandbox.fetch;app.sandbox.fetch=(path,options)=>String(path).startsWith('/api/notebook/notes')?Promise.resolve({ok:true,status:200,json:async()=>app.envelope({notes:[],next_after:null})}):oldFetch(path,options);
    app.$('#activity-notes').querySelector('button').click();await flush();
    assert.match(app.$('#activity-notes').textContent,/No notes yet/);assert.match(app.$('#history-content').textContent,/Attack method/);
  });
  await test('pagination keeps earlier notes and bounty activity and follows the server cursor',async()=>{
    const app=await boot({hook:(url,options,envelope)=>{
      if(url.pathname==='/api/notebook/notes')return envelope({notes:[note(url.searchParams.has('after')?'next':'first')],next_after:url.searchParams.has('after')?null:'opaque+cursor'});
      if(url.pathname==='/api/bounties')return envelope(bountyPage([task(url.searchParams.get('offset')==='25'?'next':'first')],Number(url.searchParams.get('offset')),url.searchParams.get('offset')==='0'));
    }});
    app.$('#more-activity-notes').focus();app.$('#more-activity-notes').click();await flush();
    assert.equal(app.$('#activity-notes').querySelectorAll('a').length,2);assert.equal(app.$('#more-activity-notes').hidden,true);
    assert.equal(app.document.activeElement.dataset.activityId,'next');
    assert.equal(app.requests.filter(row=>row.url.pathname==='/api/notebook/notes').at(-1).url.searchParams.get('after'),'opaque+cursor');
    app.$('#more-activity-bounties').click();await flush();assert.equal(app.$('#activity-bounties').querySelectorAll('a').length,2);
    await app.navigate('#about');await app.navigate('#activity');
    assert.equal(app.$('#activity-notes').querySelectorAll('a').length,2);
  });
  await test('Load more reaches a third page and an expired cache restarts a usable first page',async()=>{
    const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({
      notes:[note(url.searchParams.get('after')||'first')],next_after:url.searchParams.get('after')==='third'?null:url.searchParams.get('after')==='second'?'third':'second'}):undefined});
    for(let i=0;i<2;i++){app.$('#more-activity-notes').click();await flush();}
    assert.equal(app.$('#activity-notes').querySelectorAll('a').length,3);
    assert.equal(app.$('#more-activity-notes').hidden,true);
    await app.navigate('#about');
    vm.runInContext("readViews.get('activity:notes').at = Date.now()-300001",app.sandbox);
    await app.navigate('#activity');assert.equal(app.$('#activity-notes').querySelectorAll('a').length,1);
    vm.runInContext("readViews.get('activity:notes').at = Date.now()-300001",app.sandbox);
    app.$('#more-activity-notes').focus();app.$('#more-activity-notes').click();await flush();
    assert.equal(app.$('#activity-notes').querySelectorAll('a').length,1);assert.equal(app.$('#more-activity-notes').hidden,false);
    assert.doesNotMatch(app.$('#activity-notes').textContent,/could not/);
  });
  await test('cached unchanged activity keeps row identity and focus while quietly revalidating',async()=>{
    let slow=false;const held=deferred();const app=await boot({hook:url=>slow&&url.pathname==='/api/notebook/notes'?held.promise:undefined});
    const row=app.$('#activity-notes').querySelector('a');await app.navigate('#about');slow=true;await app.navigate('#activity');row.focus();
    assert.equal(app.$('#activity-notes').querySelector('a'),row);
    held.resolve(app.envelope({notes:[note('attack',['attack-analysis']),note('scheme',['proposal','scheme-description'])],next_after:null}));await flush();
    assert.equal(app.$('#activity-notes').querySelector('a'),row);assert.equal(app.document.activeElement,row);
  });
  await test('departing activity or losing access retires slow private reads',async()=>{
    for(const denied of [false,true]){
      const held=deferred();const app=await boot({hook:url=>url.pathname==='/api/notebook/notes'?held.promise:undefined});
      if(denied)app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:{mode:'beta',allowed:false,status:'anonymous'}}));
      else await app.navigate('#about');
      held.resolve(app.envelope({notes:[note('PRIVATE-LATE')],next_after:null}));await flush();
      assert.doesNotMatch(app.$('#activity-notes').textContent,/PRIVATE-LATE/);assert.equal(app.$('#activity-workspace').hidden,true);
      if(denied)for(const selector of ['#history-content','#activity-bounties','#activity-notes'])assert.equal(app.$(selector).textContent,'');
    }
  });
  await test('disabled bounty capability hides the section and makes no bounty reads',async()=>{
    const app=await boot({hash:'#about',features:{bounties:false}});await app.navigate('#activity');
    assert.equal(app.$('#activity-bounties-section').hidden,true);assert.equal(app.requests.some(row=>row.url.pathname==='/api/bounties'),false);
    assert.match(app.$('#activity-notes').textContent,/Attack/);
  });
  console.log(`${passed} activity checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

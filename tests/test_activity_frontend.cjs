const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id:'alice',handle:'alice'};
const scheme = {id:'fixture',version:'1',name:'Fixture',profiles:[{id:'stage-1',instance_size:1}]};
const submission = {id:'receipt',scheme_id:'fixture',scheme_version:'1',scheme_name:'Fixture public name',profile_id:'stage-1',method_name:'Attack method',received_at:100,status:'incorrect'};
const note = (id,tags=[],extra={}) => ({id,revision:2,created_at:100,publication_status:'private',
  revisions:[{revision:2,created_at:101,content:{title:'Note '+id,claim:'Summary '+id,tags,body:'PRIVATE FULL BODY'},requests:[]}],...extra});
const task = (id,extra={}) => ({id,title:'Bounty '+id,summary:'Task summary '+id,status:'open',owner:user,created_at:100,...extra});
const bountyPage = (bounties,offset=0,has_more=false) => ({bounties,pagination:{offset,limit:25,has_more}});
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
async function boot({hash='#activity',hook,features}={}) {
  const app=await createApp({schemes:[scheme],user,hash,bootstrap:true,responder:async(url,options,envelope)=>{
    const overridden=await hook?.(url,options,envelope);if(overridden!==undefined)return overridden;
    if(url.pathname==='/api/me/submissions')return envelope({submissions:[submission],next_after:null});
    if(url.pathname==='/api/notebook/notes')return envelope({notes:[note('attack',['attack-analysis']),note('scheme',['proposal','scheme-description'])],next_after:null});
    if(url.pathname==='/api/bounties')return envelope(bountyPage([task('own'),task('contributed',{owner:{id:'bob'}})]));
  }});if(features)app.window.CrucibleFeatures=features;return app;
}
const select=(app,kind)=>app.navigate('#activity?kind='+kind);
const rows=(app,kind)=>app.$(kind==='schemes'?'#history-content':'#activity-'+kind).querySelectorAll('.activity-item');
const reads=(app,path)=>app.requests.filter(row=>row.url.pathname===path);
const cache=(app,kind)=>vm.runInContext(`cachedRead(activityPageKey('${kind}',activityFirstPath('${kind}')))`,app.sandbox);
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
 await test('activity selects one compact category without fetching the two hidden lists',async()=>{
  const app=await boot();assert.equal(app.$('#activity-workspace').hidden,false);assert.equal(app.$('#history-button').getAttribute('aria-current'),'page');
  assert.equal(app.document.title,'My activity — Crucible');assert.equal(app.$('#activity-tab-schemes').getAttribute('aria-pressed'),'true');
  assert.equal(app.$('#activity-notes-section').hidden,true);assert.equal(app.$('#activity-bounties-section').hidden,true);
  assert.equal(reads(app,'/api/notebook/notes').length,0);assert.equal(reads(app,'/api/bounties').length,0);
  assert.equal(reads(app,'/api/me/submissions')[0].url.searchParams.get('limit'),'25');
  const row=rows(app,'schemes')[0];assert.match(row.textContent,/Fixture public name.*Incorrect.*Attack method/);
  assert.equal(row.querySelector('.activity-preview').hidden,true);row.querySelector('.activity-toggle').click();
  assert.equal(row.querySelector('.activity-preview').hidden,false);assert.equal(row.querySelector('.activity-toggle').getAttribute('aria-expanded'),'true');
  assert.equal(row.querySelector('.activity-detail').getAttribute('aria-haspopup'),'dialog');
  assert.equal(app.requests.some(row=>row.options.method==='POST'),false);
 });
 await test('note previews preserve proposals, status and full-reader links without caching full bodies',async()=>{
  const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({notes:[
    note('proposal',[],{proposal_revision:{requests:[{kind:'scheme_admission'}]},publication_status:'approved'}),
    note('ordinary',[],{publication_status:'cancelled'})],next_after:null}):undefined});
  const entries=rows(app,'notes');assert.match(entries[0].textContent,/Proposed scheme.*Published/);assert.match(entries[1].textContent,/Publish request cancelled/);
  assert.equal(entries[0].querySelector('.activity-detail').href,'#notebook?note=proposal&revision=2');
  assert.equal(reads(app,'/api/notebook/notes')[0].url.searchParams.get('summary'),'true');
  assert.doesNotMatch(JSON.stringify(cache(app,'notes')),/PRIVATE FULL BODY/);
  assert.equal(reads(app,'/api/me/submissions').length,0);
 });
 await test('bounties retain authorship and contribution context with stable full-detail routes',async()=>{
  const app=await boot();await select(app,'bounties');
  assert.equal(app.$('#activity-schemes-section').hidden,true);assert.equal(app.$('#activity-bounties-section').hidden,false);
  assert.match(rows(app,'bounties')[0].textContent,/Proposed by you/);assert.match(rows(app,'bounties')[0].querySelector('.activity-summary').textContent,/Task summary own/);assert.match(rows(app,'bounties')[1].textContent,/You contributed/);
  assert.equal(rows(app,'bounties')[0].querySelector('.activity-detail').href,'#bounties?status=mine&bounty=own');
 });
 await test('cold rows use an inert quiet placeholder and failures retry only the selected list',async()=>{
  const held=deferred();let hold=true;const app=await boot({hash:'#activity?kind=notes',hook:url=>hold&&url.pathname==='/api/notebook/notes'?held.promise:undefined});
  const placeholder=app.$('#activity-notes').querySelector('.history-preview');assert.ok(placeholder);assert.equal(placeholder.getAttribute('aria-hidden'),'true');
  assert.equal(placeholder.querySelectorAll('button,a').length,0);assert.doesNotMatch(app.$('#activity-notes').textContent,/loading/i);
  held.resolve(app.envelope({notes:null,next_after:null}));await flush();assert.match(app.$('#activity-notes').textContent,/could not be loaded/);
  hold=false;app.$('#activity-notes').querySelector('button').click();await flush();assert.equal(rows(app,'notes').length,2);
  assert.equal(reads(app,'/api/bounties').length,0);assert.equal(reads(app,'/api/me/submissions').length,0);
 });
 await test('paging replaces rows, preserves category positions, and refreshes only the current page on return',async()=>{
  const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({
    notes:[note(url.searchParams.get('after')||'first')],next_after:url.searchParams.get('after')==='third'?null:url.searchParams.get('after')?'third':'second+cursor'}):undefined});
  app.$('#activity-next').click();await flush();assert.equal(rows(app,'notes').length,1);assert.match(rows(app,'notes')[0].textContent,/second\+cursor/);
  app.$('#activity-next').click();await flush();assert.match(rows(app,'notes')[0].textContent,/third/);assert.equal(app.$('#activity-next').disabled,true);
  const before=reads(app,'/api/notebook/notes').length;await select(app,'schemes');await select(app,'notes');
  assert.equal(app.$('#activity-page-select').value,'2');assert.equal(reads(app,'/api/notebook/notes').length,before+1);
  assert.equal(reads(app,'/api/notebook/notes').at(-1).url.searchParams.get('after'),'third');
  app.$('#activity-page-select').value='0';app.$('#activity-page-select').dispatchEvent({type:'change',target:app.$('#activity-page-select')});await flush();
  assert.match(rows(app,'notes')[0].textContent,/Note first/);assert.equal(app.$('#activity-previous').disabled,true);
 });
 await test('scheme history uses cursor pages rather than dropping rows after the old cap',async()=>{
  const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/me/submissions'?envelope({submissions:[{...submission,id:url.searchParams.get('after')?'second':'first'}],next_after:url.searchParams.has('after')?null:'first'}):undefined});
  app.$('#activity-next').click();await flush();assert.equal(rows(app,'schemes')[0].dataset.activityId,'second');
  assert.equal(reads(app,'/api/me/submissions').at(-1).url.searchParams.get('after'),'first');
  app.$('#activity-previous').click();await flush();assert.equal(rows(app,'schemes')[0].dataset.activityId,'first');
 });
 await test('a rolling old server remains readable without claiming pagination it does not support',async()=>{
  const values=Array.from({length:50},(_,i)=>({...submission,id:'legacy-'+i}));
  const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/me/submissions'?envelope({submissions:values}):undefined});
  assert.equal(rows(app,'schemes').length,50);assert.equal(app.$('#activity-next').disabled,true);
  assert.throws(()=>vm.runInContext("activityCollectionPage('schemes',{submissions:Array.from({length:26},(_,i)=>({id:String(i)})),next_after:null},activityFirstPath('schemes'))",app.sandbox),/could not be loaded/);
 });
 await test('cached unchanged rows keep expansion, identity and focus during a quiet refresh',async()=>{
  const held=deferred();let slow=false;const app=await boot({hash:'#activity?kind=notes',hook:url=>slow&&url.pathname==='/api/notebook/notes'?held.promise:undefined});
  const row=rows(app,'notes')[0],toggle=row.querySelector('.activity-toggle');toggle.click();await app.navigate('#about');slow=true;await select(app,'notes');toggle.focus();
  assert.equal(rows(app,'notes')[0],row);assert.equal(row.querySelector('.activity-preview').hidden,false);
  held.resolve(app.envelope({notes:[note('attack',['attack-analysis']),note('scheme',['proposal','scheme-description'])],next_after:null}));await flush();
  assert.equal(rows(app,'notes')[0],row);assert.equal(app.document.activeElement,toggle);
 });
 await test('changed rows preserve expanded state and restore focus after repaint',async()=>{
  let changed=false;const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({notes:[note('same',[],{publication_status:changed?'submitted':'private'})],next_after:null}):undefined});
  const row=rows(app,'notes')[0];row.querySelector('.activity-toggle').click();row.querySelector('.activity-detail').focus();changed=true;
  await app.sandbox.showActivity();const updated=rows(app,'notes')[0];assert.notEqual(updated,row);assert.equal(updated.querySelector('.activity-preview').hidden,false);
  assert.equal(app.document.activeElement,updated.querySelector('.activity-detail'));assert.match(updated.textContent,/Publish request pending/);
 });
 await test('expired page data revalidates only the selected cursor, without restoring all earlier pages',async()=>{
  const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({notes:[note(url.searchParams.has('after')?'next':'first')],next_after:url.searchParams.has('after')?null:'cursor'}):undefined});
  app.$('#activity-next').click();await flush();await app.navigate('#about');
  vm.runInContext("for(const [key,value] of readViews) if(key.startsWith('activity:'))value.at=Date.now()-300001",app.sandbox);
  const before=reads(app,'/api/notebook/notes').length;await select(app,'notes');assert.equal(reads(app,'/api/notebook/notes').length,before+1);
  assert.equal(app.$('#activity-page-select').value,'1');assert.match(rows(app,'notes')[0].textContent,/Note next/);
 });
 await test('new first-page snapshots discard old continuation and repeated cursors cannot loop',async()=>{
  let fresh=false;const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({
    notes:[note('record')],next_after:fresh?'changed':url.searchParams.get('after')||'original'}):undefined});
  app.$('#activity-next').click();await flush();assert.equal(app.$('#activity-next').disabled,true);
  fresh=true;app.$('#activity-previous').click();await flush();
  assert.equal(vm.runInContext("activityPageState('notes').paths[1]",app.sandbox),'/api/notebook/notes?scope=mine&summary=true&limit=25&after=changed');
 });
 await test('account change, access loss and departure prevent late private results from repainting or filling caches',async()=>{
  for(const mode of ['depart','denied','account']){
   const held=deferred();const app=await boot({hash:'#activity?kind=notes',hook:url=>url.pathname==='/api/notebook/notes'?held.promise:undefined});
   if(mode==='depart')await app.navigate('#about');
   else app.sandbox.setSession(app.envelope({user:mode==='account'?{id:'bob',handle:'bob'}:null,csrf_token:'new',access:{mode:'beta',allowed:mode==='account',status:mode==='account'?'active':'anonymous'}}));
   if(mode==='account')await app.navigate('#about');
   held.resolve(app.envelope({notes:[note('PRIVATE-LATE')],next_after:null}));await flush();
   assert.doesNotMatch(app.$('#activity-notes').textContent,/PRIVATE-LATE/);assert.equal(app.$('#activity-workspace').hidden,true);assert.equal(cache(app,'notes'),null);
   if(mode==='denied')for(const id of ['history-content','activity-bounties','activity-notes'])assert.equal(app.$('#'+id).textContent,'');
  }
 });
 await test('known mutations invalidate the relevant activity pages while unknown writes and session rotation clear all',async()=>{
  const app=await boot({hook:(url,options,envelope)=>options.method==='POST'?envelope({saved:true}):undefined});
  await select(app,'bounties');await select(app,'notes');assert.ok(cache(app,'schemes'));assert.ok(cache(app,'bounties'));
  await app.sandbox.api('/api/challenges/fixture/submit',{});assert.equal(cache(app,'schemes'),null);assert.ok(cache(app,'notes'));assert.ok(cache(app,'bounties'));
  await app.sandbox.api('/api/notebook/notes',{});assert.equal(cache(app,'notes'),null);assert.equal(cache(app,'bounties'),null);
  await select(app,'schemes');await app.sandbox.api('/api/future-operation',{});assert.equal(cache(app,'schemes'),null);
  await select(app,'schemes');app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));assert.equal(cache(app,'schemes'),null);
 });
 await test('idle warmup requests only selected first and next summaries, never chains through history',async()=>{
  const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?envelope({notes:[note(url.searchParams.get('after')||'first')],next_after:url.searchParams.has('after')?'third':'second'}):undefined});
  await vm.runInContext("Promise.all(browseWarmJobs().filter(job=>job.key.startsWith('activity:')).map(job=>job.run()))",app.sandbox);
  assert.equal(reads(app,'/api/notebook/notes').length,2);assert.equal(reads(app,'/api/me/submissions').length,0);assert.equal(reads(app,'/api/bounties').length,0);
  const serialized=vm.runInContext('JSON.stringify([...readViews])+JSON.stringify([...warmedReads])',app.sandbox);assert.doesNotMatch(serialized,/PRIVATE FULL BODY/);
  assert.ok(vm.runInContext("cachedRead(activityPageKey('notes',activityPageState('notes').paths[1]))",app.sandbox));
 });
 await test('returned note and bounty proposals keep their actual review status',async()=>{
  const app=await boot({hash:'#activity?kind=notes',hook:(url,options,envelope)=>url.pathname==='/api/notebook/notes'?
    envelope({notes:[note('returned',[],{publication_status:'changes_requested'})],next_after:null}):url.pathname==='/api/bounties'?
    envelope(bountyPage([task('review',{status:'under_review'}),task('declined',{status:'declined'})])):undefined});
  assert.match(rows(app,'notes')[0].textContent,/Changes requested/);await select(app,'bounties');
  assert.match(rows(app,'bounties')[0].textContent,/Under assessment/);assert.match(rows(app,'bounties')[1].textContent,/Not admitted/);
 });
 await test('expired rows move focus to a connected category control while their replacement is pending',async()=>{
  const held=deferred();let slow=false;const app=await boot({hash:'#activity?kind=notes',hook:url=>slow&&url.pathname==='/api/notebook/notes'?held.promise:undefined});
  rows(app,'notes')[0].querySelector('.activity-toggle').focus();
  vm.runInContext("for(const [key,value] of readViews) if(key.startsWith('activity:'))value.at=Date.now()-300001",app.sandbox);
  slow=true;const pending=app.sandbox.showActivity();assert.equal(app.document.activeElement,app.$('#activity-tab-notes'));
  held.resolve(app.envelope({notes:[],next_after:null}));await pending;
 });
 await test('disabled bounty capability falls back to schemes and makes no bounty reads',async()=>{
  const app=await boot({hash:'#about',features:{bounties:false}});await select(app,'bounties');
  assert.equal(app.$('#activity-tab-bounties').hidden,true);assert.equal(app.$('#activity-bounties-section').hidden,true);assert.equal(reads(app,'/api/bounties').length,0);
 });
 console.log(`${passed} activity checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

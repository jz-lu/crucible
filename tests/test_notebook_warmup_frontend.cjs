const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createApp, flush} = require('./frontend-dom.cjs');
const rootPath = path.resolve(__dirname, '..');
const research = JSON.parse(fs.readFileSync(path.join(rootPath,'web/map/notes.json'),'utf8'));
const literatureCount = research.notes.filter(n=>n.revisions.at(-1).tags.includes('literature')).length;
const requestTypes = JSON.parse(fs.readFileSync(path.join(rootPath,'web/notebook/request-types.json'),'utf8'));
const emptyResearch = {format:'crucible-theory-local-v1',notes:[],views:[]};
const timeline = (overrides={}) => ({format:'crucible-notebook-timeline-v1',date_basis:'recorded_creation_utc',audience:'currently_visible_notes',as_of:'2026-10-18',total:3,undated_count:0,
 points:[{date:'2026-10-16',created:2,total:2},{date:'2026-10-18',created:1,total:3}],server_time:Date.parse('2026-10-18T12:00:00Z')/1000,...overrides});
const user = {id:1,handle:'alice'};
const content = (title='Private claim') => ({title,claim:'A precise claim',conditions:'Scope',body:'Argument as text',tags:['algebra'],kind:'conjecture'});
const revision = (number=1,title='Private claim',status='private') => ({revision:number,content_sha256:'hash-'+number,created_at:100,content:content(title),publication_status:status});
const note = (overrides={}) => {const rev={...revision(),...Object.fromEntries(Object.entries(overrides).filter(([key])=>['revision','content_sha256','content','publication_status'].includes(key)))};const{content:ignored,...metadata}=overrides;return{id:'note-one',owner:user,can_edit:true,created_at:100,revision:rev.revision,content_sha256:rev.content_sha256,publication_status:rev.publication_status,revision_count:1,revisions:[rev],...metadata};};
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const proposalRecord = (id='proposal-one',kind='visualization_nomination') => ({id,kind,status:'submitted',effective:false,test_only_self_review:false});
const proposalNote = (title='A submitted proposal',overrides={}) => {
 const current={...revision(1,title),content_sha256:'a'.repeat(64)};
 return note({...current,revisions:[current],proposal_revision:{revision:1,content_sha256:current.content_sha256,requests:[proposalRecord()]},...overrides});
};
const canonicalContent = value => Array.isArray(value)?'['+value.map(canonicalContent).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalContent(value[key])).join(',')+'}':JSON.stringify(value);
const curatedHash = r => require('node:crypto').createHash('sha256').update(canonicalContent(Object.fromEntries(['title','claim','conditions','body','tags','kind','references','artifacts','extensions'].filter(key=>r[key]!==undefined).map(key=>[key,r[key]])))).digest('hex');
const curatedFixture = (id,title,revisions=1) => ({id,revisions:Array.from({length:revisions},(_,index)=>({revision:index+1,createdAt:1000+index,...content(title+(revisions>1?' '+(index+1):''))}))});
const curatedRequest = (source,number=1,overrides={}) => ({id:'request-'+source.id,purpose:'visualization_nomination',primary_source:{note_id:'curated:'+source.id,revision:number,content_sha256:curatedHash(source.revisions[number-1])},assessment:null,...overrides});
const curatedSummary = source => {
 const latest=source.revisions.at(-1),summary={title:latest.title,claim:latest.claim,tags:latest.tags,kind:latest.kind};
 const r={revision:latest.revision,content_sha256:curatedHash(latest),created_at:latest.createdAt/1000,content:summary,publication_status:'curated',summary:true};
 return{id:'curated:'+source.id,source_kind:'curated-research',owner:null,can_edit:false,can_review:false,...r,revisions:[r],revision_count:source.revisions.length};
};
const listQuery = url => new URLSearchParams(url.split('?')[1]);
const cardActions = app => app.q('list').querySelectorAll('.nb-card-actions').flatMap(group=>group.querySelectorAll('button'));
function summaryPage(url,records){
 const query=listQuery(url),term=(query.get('q')||'').toLowerCase(),tag=query.get('tag');
 const filtered=records.filter(record=>{const r=record.revisions.find(rev=>rev.revision===record.revision)?.content||record.revisions.at(-1).content;return(query.get('scope')!=='mine'||record.owner?.id===user.id)&&(query.get('scope')!=='shared'||record.private_shared===true)&&(query.get('scope')!=='literature'||r.tags.includes('literature'))&&(query.get('scope')!=='human'||r.tags.includes('human-written'))&&(!query.has('proposals_only')||record.proposal_revision?.requests.some(row=>['scheme_admission','visualization_nomination','procedure','scoring_nomination','map_nomination','highlight_nomination'].includes(row.kind)))&&(!tag||r.tags.includes(tag))&&(!term||[r.title,r.claim,...r.tags].join(' ').toLowerCase().includes(term));});
 const offset=Number((query.get('after')||'page:0').split(':')[1]),limit=Number(query.get('limit')||25);
 return{notes:filtered.slice(offset,offset+limit),next_after:offset+limit<filtered.length?'page:'+(offset+limit):null};
}
async function filterNotes(app,value){const select=app.root.querySelector('[name="visibility"]');select.value=value;select.dispatchEvent({type:'change'});await flush();}
async function searchNotes(app,value){app.q('search').value=value;app.q('search').dispatchEvent({type:'input'});await app.advanceTimers(250);}
async function boot({account=user,respond,stored=null,mapFactory,currentReleases,session,digest,reviewOnly=false,researchWorkspace=emptyResearch,initialParams=new URLSearchParams(),onMount}={}) {
  const app=await createApp({schemes:[],hash:'#about'});let current=account;const requests=[],storage=[];
  const timers=new Map();let timerId=0;
  app.sandbox.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,delay});return id;};
  app.sandbox.clearTimeout=id=>timers.delete(id);
  const advanceTimers=async(ms=250)=>{for(const[id,timer]of [...timers])if(timer.delay<=ms){timers.delete(id);await timer.fn();}await flush();};
  app.sandbox.window.localStorage={getItem:key=>{storage.push(['get',key]);return stored;},setItem:()=>{throw Error('Never write local storage');},removeItem:()=>{throw Error('Never remove local storage');}};
  app.sandbox.crypto.subtle=digest?{digest}:require('node:crypto').webcrypto.subtle;
  app.sandbox.window.confirm=()=>true;app.sandbox.crypto.randomUUID=(()=>{let i=0;return()=>`key-${++i}`;})();
  for(const file of ['store.js','seed.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(rootPath,'web/notebook',file),'utf8'),app.sandbox,{filename:file});
  const root=app.$('#notebook-workspace');
  const api=async(url,body,options)=>{requests.push({url,body,options});if(respond){const result=await respond(url,body,options);if(result!==undefined)return url==='/map/notes.json'?vm.runInContext('('+JSON.stringify(result)+')',app.sandbox):result;}if(url==='/notebook/request-types.json')return requestTypes;if(url.startsWith('/api/notebook/research-requests?source_note_id='))return{requests:[],next_after:null};if(url==='/map/notes.json')return vm.runInContext('('+JSON.stringify(researchWorkspace)+')',app.sandbox);if(url.startsWith('/api/notebook/notes?'))return summaryPage(url,researchWorkspace.notes.map(curatedSummary));if(url.startsWith('/api/notebook/notes/note-one'))return{note:note()};throw Error('Unexpected '+url);};
  let controller,navigationTask=Promise.resolve();
  const navigate=route=>{app.history.pushState(null,'',route);navigationTask=controller.show(new URLSearchParams(route.split('?')[1]||''));return navigationTask;};
  controller=app.sandbox.window.CrucibleNotebook.mount(root,{api,account:()=>current,navigate,mapFactory,currentReleases,session,reviewOnly});
  const q=name=>root.querySelector(`[data-nb="${name}"]`);
  onMount?.({root,q});
  await controller.show(initialParams);
  return{...app,root,controller,q,requests,storage,advanceTimers,whenNavigated:()=>navigationTask,setAccount:value=>{current=value;controller.setAccount(value);},show:async(id,rev)=>controller.show(new URLSearchParams(id?{note:id,...(rev?{revision:String(rev)}:{})}:{}))};
}
function fill(app,title='New mathematical claim'){for(const [key,value]of Object.entries(content(title)))if(key!=='tags')app.root.querySelector(`[name="${key}"]`).value=value;app.root.querySelector('[name="tags"]').value='algebra';}
const byText=(root,label)=>root.querySelectorAll('button').find(n=>n.textContent===label);
let passed=0;async function test(name,fn){await fn();console.log('PASS '+name);passed++;}

const indexRow=(id,extra={})=>{const value=note({id,...extra});value.revisions[0].summary=true;return value;};
const summaryIndex=(rows,extra={})=>({format:'crucible-notebook-summary-index-v1',projection:'index',complete:true,viewer_id:1,scope:'all',include_curated:true,notes:rows,next_after:null,capabilities:{},server_time:100,overflow_reason:null,...extra});
const indexPath='/api/notebook/notes?projection=index&include_curated=true';
const lists=app=>app.requests.filter(row=>row.url.startsWith('/api/notebook/notes?'));
(async()=>{
 await test('hidden admitted warming caches timeline and complete summaries without painting either view',async()=>{
  const rows=[indexRow('one')],late=deferred();
  const app=await boot({respond:url=>url===indexPath?summaryIndex(rows):url==='/api/notebook/timeline'?late.promise:undefined});
  app.controller.hide();const oldList=app.q('list').firstChild;
  const warm=app.controller.warm('timeline');await app.controller.warm('list');
  assert.equal(app.q('timeline').querySelector('svg'),null);assert.equal(app.q('list').firstChild,oldList);
  late.resolve(timeline());const stats=await warm;assert.ok(stats.bytes>0);
  const before=app.requests.length;const showing=app.controller.show(new URLSearchParams('view=timeline'));
  assert.ok(app.q('timeline').querySelector('svg'));await showing;assert.equal(app.requests.length,before);
  await app.show();assert.equal(lists(app).length,2);assert.match(app.q('list').textContent,/Private claim/);
 });
 await test('complete index filters tags/scopes/proposals and pages without further list requests',async()=>{
  const rows=Array.from({length:55},(_,i)=>indexRow('row-'+i,{owner:i%2?{id:2,handle:'other'}:user,content:{...content('Title '+i),tags:i%2?['literature']:['human-written']},proposal_revision:i===3?{revision:1,content_sha256:'pin',requests:[proposalRecord()]}:null}));
  const app=await boot({respond:url=>url===indexPath?summaryIndex(rows):undefined});await app.controller.warm('list');await app.show();const before=lists(app).length;
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);app.q('more').click();await flush();assert.match(app.q('list').textContent,/Title 25/);
  await filterNotes(app,'mine');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);assert.doesNotMatch(app.q('list').textContent,/Title 1\b/);
  await filterNotes(app,'research');assert.match(app.q('list').textContent,/Title 1/);assert.doesNotMatch(app.q('list').textContent,/Title 0\b/);
  await filterNotes(app,'human');assert.match(app.q('list').textContent,/Title 0/);
  await filterNotes(app,'proposals');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);assert.match(app.q('list').textContent,/Title 3/);
  await app.controller.show(new URLSearchParams('tag=literature'));assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);
  assert.equal(lists(app).length,before);assert.equal(app.q('more').disabled,false);
  await searchNotes(app,'İ%_');assert.ok(lists(app).at(-1).url.includes('q='));assert.equal(lists(app).length,before+1,'Text search retains server semantics');
 });
 await test('incomplete or invalid index cannot hide server-filter matches',async()=>{
  for(const mode of ['overflow','viewer','bytes']){
   const rows=mode==='bytes'?Array.from({length:30},(_,i)=>indexRow('large-'+i,{content:{...content(),claim:'x'.repeat(20000)}})):[];
   const value=summaryIndex(rows,mode==='overflow'?{complete:false,overflow_reason:'count_limit'}:mode==='viewer'?{viewer_id:2}:{});
   const app=await boot({respond:url=>url===indexPath?value:undefined});
   if(mode==='overflow')await app.controller.warm('list');else await assert.rejects(app.controller.warm('list'),/Invalid notebook index/);
   const before=lists(app).length;await filterNotes(app,'mine');assert.equal(lists(app).length,before+1);assert.ok(lists(app).at(-1).url.includes('scope=mine'));
  }
 });
 await test('late background index cannot survive mutation or account/session rotation',async()=>{
  for(const change of ['mutation','account','session']){
   let session='one';const late=deferred();const app=await boot({session:()=>session,respond:url=>url===indexPath?late.promise:undefined});
   const warming=app.controller.warm('list');
   if(change==='mutation')app.controller.invalidateReads();
   if(change==='account')app.setAccount({id:2,handle:'bob'});
   if(change==='session'){session='two';app.setAccount(user);}
   late.resolve(summaryIndex([indexRow('private-old',{content:content('OLD PRIVATE')})]));await warming;
   const before=lists(app).length;await app.show();assert.equal(lists(app).length,before+1);assert.doesNotMatch(app.root.textContent,/OLD PRIVATE/);
  }
 });
 await test('visible reentry quietly refreshes an older index while filters stay usable locally',async()=>{
  const late=deferred();let reads=0;const rows=[indexRow('one')];
  const app=await boot({respond:url=>url===indexPath?(++reads===1?summaryIndex(rows):late.promise):undefined});
  vm.runInContext('Date.now=()=>1000',app.sandbox);await app.controller.warm('list');await app.show();
  vm.runInContext('Date.now=()=>12000',app.sandbox);await app.show();assert.equal(reads,2);
  const before=lists(app).length;await filterNotes(app,'mine');assert.equal(lists(app).length,before);assert.match(app.q('list').textContent,/Private claim/);
  late.resolve(summaryIndex([indexRow('new',{content:content('Fresh index')})]));await flush();
  await filterNotes(app,'all');assert.match(app.q('list').textContent,/Fresh index/);
 });
 await test('quiet index refresh does not repeat retained server search or Trash reads',async()=>{
  for(const mode of ['search','trash']){
   const late=deferred();let reads=0;const rows=[indexRow('one')];
   const app=await boot({respond:url=>url===indexPath?(++reads===1?summaryIndex(rows):late.promise):undefined});
   vm.runInContext('Date.now=()=>1000',app.sandbox);await app.controller.warm('list');
   if(mode==='search')await searchNotes(app,'Private');else await filterNotes(app,'trash');
   vm.runInContext('Date.now=()=>12000',app.sandbox);await app.show();assert.equal(reads,2);
   const before=lists(app).length;late.resolve(summaryIndex([indexRow('new')]));await flush();
   assert.equal(lists(app).length,before,'Quiet metadata refresh must not repeat a server-only query');
  }
 });
 await test('server page to complete index to expiry never sends synthetic cursors or wrong cached pages',async()=>{
  const rows=Array.from({length:60},(_,i)=>indexRow('item-'+i,{content:content('Row '+i)}));
  const app=await boot({respond:url=>url===indexPath?summaryIndex(rows):url.startsWith('/api/notebook/notes?')?summaryPage(url,rows):undefined});
  vm.runInContext('Date.now=()=>1000',app.sandbox);app.q('more').click();await flush();assert.match(app.q('list').textContent,/Row 25/);
  await app.controller.warm('list');await app.show();assert.match(app.q('list').textContent,/Row 25/);
  vm.runInContext('Date.now=()=>302000',app.sandbox);await app.show();
  assert.match(app.q('list').textContent,/Row 0/);assert.ok(lists(app).every(row=>!decodeURIComponent(row.url).includes('index:')));
 });
 await test('local filtered pages inherit index freshness instead of extending its five-minute lifetime',async()=>{
  const late=deferred();const app=await boot({respond:url=>url===indexPath?summaryIndex([indexRow('one',{content:content('Index from original read')})]):url.includes('scope=mine')?late.promise:undefined});
  vm.runInContext('Date.now=()=>1000',app.sandbox);await app.controller.warm('list');
  vm.runInContext('Date.now=()=>299000',app.sandbox);await filterNotes(app,'mine');assert.match(app.q('list').textContent,/Index from original read/);
  vm.runInContext('Date.now=()=>302000',app.sandbox);const showing=app.show();await flush();
  assert.doesNotMatch(app.q('list').textContent,/Index from original read/);assert.ok(app.q('list').querySelector('.nb-reading'));
  late.resolve({notes:[],next_after:null});await showing;
 });
 await test('next server page intent shares its exact request and never prefetches body data',async()=>{
  const rows=Array.from({length:26},(_,i)=>indexRow('item-'+i,{content:content('Row '+i)}));const late=deferred();let next=0;
  const app=await boot({respond:url=>url===indexPath?summaryIndex([],{complete:false,overflow_reason:'count_limit'}):url.startsWith('/api/notebook/notes?')?(listQuery(url).get('after')?(next++,late.promise):summaryPage(url,rows)):undefined});
  await app.controller.warm('list');await app.show();assert.equal(next,1);app.q('more').click();await flush();assert.equal(next,1);
  late.resolve({notes:[rows[25]],next_after:null});await flush();assert.match(app.q('list').textContent,/Row 25/);
  assert.equal(app.requests.some(row=>row.url==='/map/notes.json'||row.url.startsWith('/api/notebook/notes/item-')),false);
 });
 await test('curated body intent starts only on expansion and paint does not wait for secondary status',async()=>{
  const source=curatedFixture('source','Readable curated body'),status=deferred();
  const app=await boot({researchWorkspace:{...emptyResearch,notes:[source]},respond:url=>url.startsWith('/api/notebook/research-requests?source_note_id=')?status.promise:undefined});
  assert.equal(app.requests.some(row=>row.url==='/map/notes.json'),false);
  app.q('list').querySelector('.nb-title').click();await flush();assert.equal(app.requests.filter(row=>row.url==='/map/notes.json').length,1);
  const showing=app.show('curated:source');await flush();const body=app.q('full').querySelector('.nb-body');assert.match(body.textContent,/Argument as text/);
  assert.equal(byText(app.q('full'),'Propose a change').disabled,true);const cite=byText(app.q('full'),'Cite this note');cite.focus();
  status.resolve({requests:[],next_after:null});await showing;assert.equal(app.q('full').querySelector('.nb-body'),body);assert.equal(app.document.activeElement,cite);assert.equal(byText(app.q('full'),'Propose a change').disabled,false);
  assert.equal(app.requests.filter(row=>row.url==='/map/notes.json').length,1);
 });
 await test('curated secondary failures preserve body and disabled actions but revoked reads clear it',async()=>{
  for(const code of [503,403]){
   const source=curatedFixture('source','Curated text'),status=deferred();let reads=0;
   const app=await boot({researchWorkspace:{...emptyResearch,notes:[source]},respond:url=>url.startsWith('/api/notebook/research-requests?source_note_id=')?(++reads===1?status.promise:{requests:[],next_after:null}):undefined});
   const showing=app.show('curated:source');await flush();const body=app.q('full').querySelector('.nb-body');assert.ok(body);
   status.reject(Object.assign(Error('Denied or unavailable'),{status:code}));await showing;
   if(code===403){assert.equal(app.q('full').querySelector('.nb-body'),null);assert.equal(byText(app.q('full'),'Propose a change'),undefined);}
   else {assert.equal(app.q('full').querySelector('.nb-body'),body);assert.equal(byText(app.q('full'),'Propose a change'),undefined);byText(app.q('full'),'Retry request status').click();await flush();assert.equal(app.q('full').querySelector('.nb-body'),body);assert.equal(byText(app.q('full'),'Propose a change').disabled,false);}
  }
 });
 await test('late curated status cannot change another reader or signed-out content',async()=>{
  const source=curatedFixture('source','Curated text'),status=deferred();const app=await boot({researchWorkspace:{...emptyResearch,notes:[source]},respond:url=>url.startsWith('/api/notebook/research-requests?source_note_id=')?status.promise:undefined});
  const showing=app.show('curated:source');await flush();app.setAccount(null);status.resolve({requests:[curatedRequest(source)],next_after:null});await showing;assert.equal(app.q('full').textContent,'');
 });
 await test('large body cache pressure evicts bodies before bounded summary and timeline entries',async()=>{
  const rows=[indexRow('one')];const large=id=>note({id,content:{...content('Large '+id),body:'x'.repeat(800000)}});
  const app=await boot({respond:url=>url===indexPath?summaryIndex(rows):url==='/api/notebook/timeline'?timeline():url.startsWith('/api/notebook/notes/large')?{note:large(url.split('/').at(-1))}:undefined});
  await app.controller.warm('list');await app.controller.warm('timeline');
  for(const id of ['large1','large2','large3'])await app.show(id);
  const before=lists(app).length;await app.show();assert.equal(lists(app).length,before);assert.match(app.q('list').textContent,/Private claim/);
  const showing=app.controller.show(new URLSearchParams('view=timeline'));assert.ok(app.q('timeline').querySelector('svg'));await showing;
 });
 console.log(`\n${passed} notebook warmup checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

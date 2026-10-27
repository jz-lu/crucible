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
  const api=async(url,body,options)=>{requests.push({url,body,options});if(respond){const result=await respond(url,body,options);if(result!==undefined)return url==='/map/notes.json'?vm.runInContext('('+JSON.stringify(result)+')',app.sandbox):result;}if(url==='/notebook/request-types.json')return requestTypes;if(url==='/api/notebook/curated-note-numbers')return{numbers:Object.fromEntries(researchWorkspace.notes.map((source,index)=>['curated:'+source.id,index+1]))};if(url.startsWith('/api/notebook/research-requests?source_note_id='))return{requests:[],next_after:null};if(url==='/map/notes.json')return vm.runInContext('('+JSON.stringify(researchWorkspace)+')',app.sandbox);if(url.startsWith('/api/notebook/notes?'))return summaryPage(url,researchWorkspace.notes.map(curatedSummary));if(url.startsWith('/api/notebook/notes/note-one'))return{note:note()};throw Error('Unexpected '+url);};
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
(async()=>{
 await test('note numbers appear only after expansion and identify exact revision citations',async()=>{
  const saved=note({number:1042,content_sha256:'a'.repeat(64)});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[saved],next_after:null}:url.startsWith('/api/notebook/notes/note-one')?{note:saved}:undefined});
  assert.equal(app.q('list').querySelector('.nb-note-number'),null);
  app.q('list').querySelector('.nb-title').click();await flush();
  assert.equal(app.q('list').querySelector('.nb-note-number').textContent,'#1042');
  app.q('list').querySelector('.nb-title').click();await flush();
  assert.equal(app.q('list').querySelector('.nb-note-number'),null);
  await app.show('note-one',1);
  app.q('full').querySelector('.nb-number-action').click();
  assert.equal(app.q('dialog-body').querySelector('.nb-citation-number').textContent,'#1042 · revision 1');
  assert.match(app.q('dialog-body').querySelector('.nb-prompt').textContent,/#1042/);
  assert.match(app.q('dialog-body').querySelector('[name="citation-link"]').value,/revision=1&sha256=a{64}/);
 });


 for(const [view,endpoint,result] of [
  ['list','/api/notebook/notes?',{notes:[]}],
  ['full','/api/notebook/notes/note-one',{note:note()}],
  ['timeline','/api/notebook/timeline',timeline()]
 ]) await test(view+' read reserves quiet space with an accessible status until fresh content arrives',async()=>{
  const late=deferred();let held=false;
  const app=await boot({respond:url=>held&&url.startsWith(endpoint)?late.promise:undefined});
  app.controller.invalidateReads();held=true;
  const pending=view==='timeline'?app.controller.show(new URLSearchParams('view=timeline')):app.show(view==='full'?'note-one':undefined);
  const region=app.q(view).querySelector('.nb-reading'),status=region?.querySelector('[role="status"]');
  assert.ok(region);assert.equal(region.getAttribute('aria-busy'),'true');
  assert.ok(status?.classList.contains('nb-sr-only'),'Read status is visually hidden, not an animated loading message');
  assert.match(status.textContent,/Loading/);assert.equal(region.querySelectorAll('button,a,input').length,0);
  late.resolve(result);await pending;
  assert.equal(app.q(view).querySelector('.nb-reading'),null,'Settled content removes the temporary busy region');
 });

 await test('expanding a note prefetches its exact body and the reader shares the in-flight GET',async()=>{
  const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?late.promise:undefined});
  app.q('list').querySelector('.nb-title').click();await flush();
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/notes/note-one').length,1);
  const loading=app.show('note-one');await flush();assert.equal(app.requests.filter(row=>row.url==='/api/notebook/notes/note-one').length,1);
  late.resolve({note:note()});await loading;assert.match(app.q('full').textContent,/Argument as text/);assert.equal(byText(app.q('full'),'Edit').disabled,false);
 });
 await test('note intent results cannot restore private caches after an account switch',async()=>{
  const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?late.promise:undefined});
  app.q('list').querySelector('.nb-title').click();await flush();app.setAccount(null);
  late.resolve({note:note()});await flush();assert.equal(app.q('list').textContent,'');assert.equal(app.q('full').textContent,'');
 });
 await test('an older consumed note intent cannot replace a newer cached body',async()=>{
  const old=deferred(),refresh=deferred();let reads=0;
  const newer=note({content:content('Newer saved note')});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?(++reads===1?old.promise:reads===2?{note:newer}:refresh.promise):undefined});
  app.q('list').querySelector('.nb-title').click();const first=app.show('note-one');
  await app.show();await app.show('note-one');assert.match(app.q('full').textContent,/Newer saved note/);
  old.resolve({note:note({content:content('Older saved note')})});await first;await flush();
  assert.match(app.q('full').textContent,/Newer saved note/,'Late intent cannot change the selected reader');
  await app.show();const returning=app.show('note-one');
  assert.match(app.q('full').textContent,/Newer saved note/,'Return must retain the newer cached body');
  assert.doesNotMatch(app.q('full').textContent,/Older saved note/);
  assert.equal(byText(app.q('full'),'Edit').disabled,true,'Cached permissions still require fresh validation');
  refresh.resolve({note:newer});await returning;assert.equal(reads,3);
  assert.equal(byText(app.q('full'),'Edit').disabled,false);
 });

 await test('returning to the same notebook list paints immediately and keeps unchanged expanded content',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()],next_after:null}):undefined});
  app.q('list').querySelector('.nb-title').click();const first=app.q('list').firstChild;
  app.controller.hide();delay=true;const loading=app.show();
  assert.match(app.q('list').textContent,/Private claim/);assert.ok(app.q('list').firstChild===first, 'preserve the loaded list DOM');
  late.resolve({notes:[note()],next_after:null});await loading;
  assert.ok(app.q('list').firstChild===first, 'preserve the loaded list DOM');assert.equal(app.q('list').querySelector('.nb-preview').hidden,false);
 });
 await test('cached note bodies render before the network while stale action buttons stay disabled',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url==='/api/notebook/notes/note-one'?(delay?late.promise:{note:note()}):undefined});
  await app.show('note-one');await app.show();delay=true;const loading=app.show('note-one');
  assert.match(app.q('full').textContent,/Argument as text/);assert.equal(byText(app.q('full'),'Edit').disabled,true);
  assert.equal(app.document.activeElement,app.q('full').querySelector('h2'),'cached entry hands focus to the reader');
  const back=app.q('full').querySelector('.nb-back');back.focus();
  const body=app.q('full').querySelector('.nb-body');
  late.resolve({note:note()});await loading;
  assert.equal(app.document.activeElement,back,'quiet revalidation preserves the reader choice of focus');
  assert.ok(app.q('full').querySelector('.nb-body')===body, 'preserve the body during unchanged refresh');assert.equal(byText(app.q('full'),'Edit').disabled,false);
  byText(app.q('full'),'Explore connections').click();await flush();assert.ok(app.q('full').querySelector('.nb-note-graph'));
 });
 await test('denied and withdrawn cached notes remove their body and invalidate cached list summaries',async()=>{
  for(const status of [403,404,410]){
   const late=deferred();let delay=false;
   const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?(delay?late.promise:{note:note()}):undefined});
   await app.show('note-one');await app.show();delay=true;const loading=app.show('note-one');
   assert.match(app.q('full').textContent,/Argument as text/);
   late.reject(Object.assign(Error('Access withdrawn'),{status}));await loading;
   assert.doesNotMatch(app.q('full').textContent,/Argument as text/);assert.doesNotMatch(app.q('list').textContent,/Private claim/);
  }
 });
 await test('returning from a reader leaves keyboard focus visible through a list refresh',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
  app.root.hidden=false;
  await app.show('note-one');const back=app.q('full').querySelector('.nb-back');back.focus();delay=true;back.click();await flush();
  assert.equal(app.q('full').hidden,true);assert.equal(app.document.activeElement,app.q('notes-view'));
  assert.equal(Boolean(app.document.activeElement.closest('[hidden]')),false);
  late.resolve({notes:[note()]});await app.whenNavigated();assert.equal(app.document.activeElement,app.q('notes-view'));
 });
 await test('changing a linked notebook filter updates its route and survives reentry',async()=>{
  const app=await boot({initialParams:new URLSearchParams('filter=proposals')});
  app.history.replaceState(null,'','#notebook?filter=proposals');
  await filterNotes(app,'all');assert.equal(app.location.hash,'#notebook');
  app.controller.hide();await app.controller.show(new URLSearchParams(app.location.hash.split('?')[1]||''));
  assert.equal(app.root.querySelector('[name="visibility"]').value,'all');
  assert.equal(listQuery(app.requests.at(-1).url).has('proposals_only'),false);
  await filterNotes(app,'human');assert.equal(app.location.hash,'#notebook?filter=human');
  await app.controller.show(new URLSearchParams('filter=mine'));
  assert.equal(app.root.querySelector('[name="visibility"]').value,'mine');
  assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'mine');
 });
 await test('a pending filter retains the old page with disabled actions, then uses only its server result',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
  delay=true;const changing=filterNotes(app,'human');await flush();
  assert.match(app.q('list').textContent,/Private claim/);assert.doesNotMatch(app.q('list').textContent,/Loading notes/);
  assert.equal(app.q('list').getAttribute('aria-busy'),'true');
  assert.ok(cardActions(app).length);assert.ok(cardActions(app).every(control=>control.disabled));
  late.resolve({notes:[]});await changing;await flush();assert.match(app.q('list').textContent,/No matching notes/);
  assert.doesNotMatch(app.q('list').textContent,/Private claim/);assert.equal(app.q('list').getAttribute('aria-busy'),null);
 });
 await test('search wording and focus stay stable from construction through a held filter and its result',async()=>{
  let initial;const late=deferred();let delay=false;
  const app=await boot({onMount:({q})=>{initial=q('search').placeholder;},respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
  assert.equal(initial,'Search titles, tags or #number…');assert.equal(app.q('search').placeholder,initial);
  const search=app.q('search');search.focus();delay=true;await filterNotes(app,'human');
  assert.equal(app.q('search'),search);assert.equal(search.placeholder,initial);assert.equal(app.document.activeElement,search);
  late.resolve({notes:[]});await flush();assert.equal(search.placeholder,initial);assert.equal(app.document.activeElement,search);
 });
 await test('empty pages apply only to their exact query, never to a newly requested filter',async()=>{
  let pending=deferred(),hold=false;
  const app=await boot({initialParams:new URLSearchParams('filter=human'),respond:url=>url.startsWith('/api/notebook/notes?')?(hold?pending.promise:{notes:[]}):undefined});
  assert.match(app.q('list').textContent,/No matching notes/);hold=true;await filterNotes(app,'mine');
  assert.doesNotMatch(app.q('list').textContent,/No matching notes|No notes yet/);assert.ok(app.q('list').querySelector('.nb-reading'));
  pending.resolve({notes:[note()]});await flush();assert.match(app.q('list').textContent,/Private claim/);
  pending=deferred();await filterNotes(app,'human');assert.match(app.q('list').textContent,/No matching notes/);
  assert.equal(app.q('list').getAttribute('aria-busy'),'true');pending.resolve({notes:[]});await flush();
 });
 await test('expanding retained rows cannot reactivate paging or edits before the requested page arrives',async()=>{
  const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(listQuery(url).has('after')?late.promise:{notes:[note()],next_after:'page:25'}):undefined});
  app.q('more').click();await flush();assert.equal(listQuery(app.requests.at(-1).url).get('after'),'page:25');
  app.q('list').querySelector('.nb-title').click();await flush();
  assert.equal(app.q('prev').disabled,true);assert.equal(app.q('more').disabled,true);
  assert.ok(cardActions(app).length);assert.ok(cardActions(app).every(control=>control.disabled));
  late.resolve({notes:[note({id:'next-page',content:content('Next page result')})],next_after:null});await flush();
  assert.match(app.q('list').textContent,/Next page result/);assert.equal(app.q('prev').disabled,false);assert.equal(app.q('more').disabled,true);
 });
 await test('replaced or failed filter rows hand focus to the stable list only when those rows own it',async()=>{
  for(const failure of [false,true]){
   const late=deferred();let delay=false;
   const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
   delay=true;await filterNotes(app,'human');app.q('list').querySelector('.nb-title').focus();
   if(failure)late.reject(Object.assign(Error('Filter unavailable'),{status:503}));else late.resolve({notes:[]});
   await flush();assert.equal(app.document.activeElement,app.q('list'));assert.equal(app.document.activeElement.isConnected,true);
  }
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
  delay=true;await filterNotes(app,'human');app.q('notes-view').focus();late.resolve({notes:[]});await flush();assert.equal(app.document.activeElement,app.q('notes-view'));
 });
 await test('rapid filter and route changes cannot substitute a late page or clear the newer pending state',async()=>{
  const human=deferred(),mine=deferred();let delay=false;
  const app=await boot({respond:url=>{if(!url.startsWith('/api/notebook/notes?'))return;const scope=listQuery(url).get('scope');return delay&&scope==='human'?human.promise:delay&&scope==='mine'?mine.promise:{notes:[note()]};}});
  delay=true;await filterNotes(app,'human');const current=app.controller.show(new URLSearchParams('filter=mine'));await flush();
  human.resolve({notes:[note({id:'late',content:content('Late human result')})]});await flush();
  assert.doesNotMatch(app.q('list').textContent,/Late human result/);assert.equal(app.q('list').getAttribute('aria-busy'),'true');
  mine.resolve({notes:[note({content:content('Mine response')})]});await current;
  assert.match(app.q('list').textContent,/Mine response/);assert.doesNotMatch(app.q('list').textContent,/Late human result/);assert.equal(app.q('list').getAttribute('aria-busy'),null);
  app.controller.hide();delay=false;await app.controller.show(new URLSearchParams('filter=human'));assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'human');
 });
 await test('failed or forbidden new filters never present retained rows as completed matches',async()=>{
  for(const status of [503,403]){
   const late=deferred();let delay=false;
   const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
   delay=true;await filterNotes(app,'human');assert.match(app.q('list').textContent,/Private claim/);
   late.reject(Object.assign(Error('Filter unavailable'),{status}));await flush();
   assert.doesNotMatch(app.q('list').textContent,/Private claim/);assert.match(app.q('list').textContent,/Filter unavailable/);assert.equal(app.q('list').getAttribute('aria-busy'),null);
  }
 });
 await test('a filter read cannot retain or restore another account or rotated session page',async()=>{
  const late=deferred();let delay=false,session='one';
  const app=await boot({session:()=>session,respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()]}):undefined});
  delay=true;await filterNotes(app,'human');session='two';app.setAccount(user);
  assert.equal(app.q('list').textContent,'');assert.equal(app.q('list').getAttribute('aria-busy'),null);
  late.resolve({notes:[note({content:content('OLD SESSION DATA')})]});await flush();assert.equal(app.q('list').textContent,'');
 });
 await test('same-account session rotation clears notebook readers and rejects a pending cached refresh',async()=>{
  const late=deferred();let delay=false,session='one';
  const app=await boot({session:()=>session,respond:url=>url==='/api/notebook/notes/note-one'?(delay?late.promise:{note:note()}):undefined});
  await app.show('note-one');delay=true;const loading=app.show('note-one');
  session='two';app.setAccount(user);assert.equal(app.q('full').textContent,'');
  late.resolve({note:note()});await loading;assert.equal(app.q('full').textContent,'');
 });
 await test('latest and pinned notebook revisions use distinct caches and retain citation checks',async()=>{
  const late=deferred();let delay=false;
  const first=revision(1,'Exact older'),second=revision(2,'Exact latest');
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?(delay?late.promise:{note:note({revision:2,revisions:[first,second]})}):undefined});
  await app.show('note-one');delay=true;const loading=app.show('note-one',1);
  assert.doesNotMatch(app.q('full').textContent,/Exact latest/);assert.match(app.q('full').textContent,/Loading note/);
  late.resolve({note:note({revision:2,revisions:[first,second]})});await loading;assert.match(app.q('full').textContent,/Exact older/);
 });
 await test('external notebook mutations invalidate reader caches before the next navigation',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({respond:url=>url==='/api/notebook/notes/note-one'?(delay?late.promise:{note:note()}):undefined});
  await app.show('note-one');await app.show();app.controller.invalidateReads();delay=true;
  const loading=app.show('note-one');assert.match(app.q('full').textContent,/Loading note/);assert.doesNotMatch(app.q('full').textContent,/Argument as text/);
  late.resolve({note:note()});await loading;
 });
 await test('private draft deletion freezes its exact revision and retries after a lost reply',async()=>{
  let writes=0,deleted=false;
  const app=await boot({respond:(url,body)=>{
   if(url.endsWith('/delete')){writes++;deleted=true;if(writes===1)throw Error('Connection lost');return{deletion:{note_id:'note-one',id:'deletion-one'},deleted};}
   if(url==='/api/notebook/notes/note-one')return{note:note({can_delete:true})};
   if(url.startsWith('/api/notebook/notes?'))return{notes:deleted?[]:[note({can_delete:true})]};
  }});
  await app.show('note-one');byText(app.q('full'),'Edit').click();byText(app.q('dialog-body'),'Remove note').click();
  assert.match(app.q('dialog-body').textContent,/Move this private draft to Trash/);
  assert.equal(writes,0);byText(app.q('dialog-body'),'Move to Trash').click();await flush();
  const first=app.requests.find(row=>row.body);
  assert.equal(first.body.revision,1);assert.equal(first.body.content_sha256,'hash-1');
  byText(app.q('dialog-body'),'Retry the same delete').click();await flush();
  assert.deepEqual(app.requests.filter(row=>row.body)[1].body,first.body);
  assert.equal(app.q('dialog').open,false);assert.match(app.q('list').textContent,/No notes yet/);
 });
 await test('Trash restores its exact deletion and refreshes the current list',async()=>{
  let restored=false;
  const trashed=note({can_edit:false,can_restore:true,deletion:{id:'deletion-one'},can_delete:false});
  const app=await boot({respond:(url,body)=>{
   if(url.endsWith('/restore')){assert.equal(body.deletion_id,'deletion-one');restored=true;return{deletion:{note_id:'note-one',id:'deletion-one'},deleted:false};}
   if(url.startsWith('/api/notebook/notes?'))return{notes:listQuery(url).get('scope')==='trash'&&!restored?[trashed]:[]};
  }});
  await filterNotes(app,'trash');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'trash');
  assert.match(app.q('list').textContent,/In Trash/);assert.equal(byText(app.q('list'),'Edit'),undefined);
  byText(app.q('list'),'Restore').click();assert.equal(restored,false);
  byText(app.q('dialog-body'),'Restore note').click();await flush();
  assert.equal(app.q('dialog').open,false);assert.match(app.q('list').textContent,/No matching notes/);
 });
 await test('removal lives inside Edit and requires owner and draft permissions',async()=>{
  for(const record of [note({can_delete:false}),note({can_delete:true,owner:{id:2,handle:'alice'}})]){
   const app=await boot({respond:url=>url==='/api/notebook/notes/note-one'?{note:record}:undefined});
   await app.show('note-one');assert.equal(byText(app.q('full'),'Delete'),undefined);
   byText(app.q('full'),'Edit')?.click();assert.equal(byText(app.q('dialog-body'),'Remove note'),undefined);
  }
  const app=await boot();app.q('new').click();assert.equal(byText(app.q('dialog-body'),'Remove note'),undefined);
 });
 await test('removal from Edit respects unsaved changes and confirmation cancellation',async()=>{
  const app=await boot({respond:url=>url==='/api/notebook/notes/note-one'?{note:note({can_delete:true})}:undefined});
  await app.show('note-one');assert.equal(byText(app.q('full'),'Delete'),undefined);
  byText(app.q('full'),'Edit').click();const title=app.root.querySelector('[name="title"]');title.value='Keep this unsaved draft';
  let confirmations=0;app.sandbox.window.confirm=()=>{confirmations++;return false;};
  byText(app.q('dialog-body'),'Remove note').click();assert.equal(confirmations,1);assert.equal(title.value,'Keep this unsaved draft');
  assert.equal(byText(app.q('dialog-body'),'Move to Trash'),undefined);
  title.value='Private claim';byText(app.q('dialog-body'),'Remove note').click();
  byText(app.q('dialog-body'),'Cancel').click();assert.equal(app.q('dialog').open,false);
  assert.equal(app.requests.filter(row=>row.body).length,0);
 });
 await test('removal is blocked during a pending or uncertain revision save',async()=>{
  const pending=deferred();
  const app=await boot({respond:url=>url.endsWith('/revisions')?pending.promise:url==='/api/notebook/notes/note-one'?{note:note({can_delete:true})}:undefined});
  await app.show('note-one');byText(app.q('full'),'Edit').click();fill(app,'Updated draft');
  const remove=byText(app.q('dialog-body'),'Remove note');app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(remove.disabled,true);remove.click();assert.equal(byText(app.q('dialog-body'),'Move to Trash'),undefined);
  pending.reject(Error('Connection lost'));await flush();assert.equal(remove.disabled,true);
  assert.equal(app.requests.some(row=>row.url.endsWith('/delete')),false);
 });
 await test('late delete replies do not navigate or show data after an account switch',async()=>{
  const pending=deferred();
  const app=await boot({respond:url=>url.endsWith('/delete')?pending.promise:url==='/api/notebook/notes/note-one'?{note:note({can_delete:true})}:undefined});
  await app.show('note-one');byText(app.q('full'),'Edit').click();byText(app.q('dialog-body'),'Remove note').click();byText(app.q('dialog-body'),'Move to Trash').click();
  app.setAccount({id:2,handle:'bob'});const before=app.location.hash;
  pending.resolve({deletion:{note_id:'note-one'},deleted:true});await flush();
  assert.equal(app.location.hash,before);assert.doesNotMatch(app.root.textContent,/Note moved to Trash/);
 });
 await test('signing in on an exact notebook link restores that view and signing out clears it',async()=>{
  let current=null;
  const app=await createApp({schemes:[],hash:'#about',responder:(url,options,envelope)=>{
   if(url.pathname==='/api/auth/login'){current=user;return envelope({user:current,csrf_token:'test-csrf'});}
   if(url.pathname==='/api/auth/logout'){current=null;return envelope({user:null,csrf_token:''});}
   if(url.pathname==='/api/session')return envelope({user:current,csrf_token:'test-csrf'});
   if(url.pathname==='/api/notebook/notes/note-one')return{note:note()};
  }});
  for(const file of ['store.js','seed.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(rootPath,'web/notebook',file),'utf8'),app.sandbox,{filename:file});
  await app.navigate('#notebook?note=note-one&revision=1');
  assert.match(app.$('#notebook-workspace').textContent,/Sign in to read your notebook/);
  app.$('#auth-button').click();app.$('#auth-handle').value='alice';app.$('#auth-password').value='Synthetic test account only';
  app.$('#auth-form').dispatchEvent({type:'submit'});await flush();
  assert.match(app.$('#notebook-workspace').textContent,/Private claim/);
  assert.equal(app.location.hash,'#notebook?note=note-one&revision=1');
  app.$('#logout-button').click();await flush();
  assert.doesNotMatch(app.$('#notebook-workspace').textContent,/Private claim/);
  assert.match(app.$('#notebook-workspace').textContent,/Sign in to read your notebook/);
 });
 await test('empty notebook fetches one bounded summary page without seeding or loading full curated notes',async()=>{
  const app=await boot();assert.match(app.root.textContent,/No notes yet/);assert.equal(app.storage.length,0);
  assert.deepEqual(app.requests.map(r=>r.url),['/api/notebook/notes?limit=25&include_curated=true&scope=all']);
  assert.ok(app.requests.every(r=>r.body===undefined));assert.equal(app.q('page').textContent,'Page 1');
 });
 await test('private and shared notes are marked and My notes requests an owner-filtered server page',async()=>{
  const shared=note({id:'shared',can_edit:false,owner:{id:2,handle:'bob'},...revision(1,'Approved claim','approved')});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,[note(),shared]):undefined});
  assert.match(app.root.textContent,/Private claim.*Private.*alice.*Approved claim.*Published.*bob/);
  await filterNotes(app,'mine');assert.doesNotMatch(app.q('list').textContent,/Approved claim/);
  assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'mine');
 });
 await test('Human-written filter uses a whole-notebook server scope, combines search and resets paging',async()=>{
  const records=Array.from({length:61},(_,i)=>note({id:'human-'+i,content:{...content('Collaborator observation '+i),tags:['human-written','topic']}}));
  records.push(note({id:'agent-one',content:{...content('Agent observation'),tags:['topic']}}));
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,records):undefined});
  app.q('more').click();await flush();assert.equal(listQuery(app.requests.at(-1).url).get('after'),'page:25');
  await filterNotes(app,'human');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'human');assert.equal(listQuery(app.requests.at(-1).url).has('after'),false);
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);assert.match(app.root.textContent,/authorship label supplied by the author, not independently verified/);
  app.q('more').click();await flush();assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'human');
  await searchNotes(app,'observation 60');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'human');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);assert.match(app.q('list').textContent,/Collaborator observation 60/);
  app.setAccount({id:2,handle:'bob'});await app.show();assert.equal(app.root.querySelector('[name="visibility"]').value,'all');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'all');
 });
 await test('exact shared revision never exposes edit actions or guesses a later private revision',async()=>{const shared=note({can_edit:false,owner:{id:2,handle:'bob'},...revision(1,'Approved claim','approved'),revisions:[revision(1,'Approved claim','approved')]});const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:shared}:undefined});await app.show('note-one',1);assert.match(app.q('full').textContent,/Approved claim.*Published.*Revision 1/);assert.equal(byText(app.q('full'),'Edit'),undefined);assert.equal(app.requests.at(-1).url,'/api/notebook/notes/note-one?revision=1');});
 await test('save creates a private account note and retains the submitted draft on validation failure',async()=>{let accepted=false;const app=await boot({respond:(url,body)=>{if(body&&url==='/api/notebook/notes'){if(!accepted)throw Object.assign(Error('Review the conditions'),{status:422});return{note:note({id:'created'})};}if(url.endsWith('/created'))return{note:note({id:'created'})};}});app.q('new').click();fill(app);const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();assert.equal(app.q('dialog').open,true);assert.equal(app.root.querySelector('[name="title"]').value,'New mathematical claim');assert.match(app.q('dialog-body').textContent,/draft is still here/);assert.equal(app.root.querySelector('[name="title"]').disabled,false);accepted=true;form.dispatchEvent({type:'submit'});await flush();assert.equal(app.q('dialog').open,false);assert.equal(app.requests.filter(r=>r.body).length,2);assert.equal(app.requests.filter(r=>r.body)[0].body.content.title,'New mathematical claim');});
 await test('lost save responses retain a frozen idempotent payload and do not create another note on retry',async()=>{let writes=0;const app=await boot({respond:(url,body)=>{if(body&&url==='/api/notebook/notes'){if(++writes===1)throw Object.assign(Error('Lost response'),{status:0});return{note:note()};}}});app.q('new').click();fill(app);const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();assert.equal(app.root.querySelector('[name="title"]').disabled,true);app.root.querySelector('[name="title"]').value='Tampered pending draft';form.dispatchEvent({type:'submit'});await flush();const writesList=app.requests.filter(r=>r.body);assert.equal(writesList.length,2);assert.deepEqual(writesList[0].body,writesList[1].body);assert.equal(writesList[1].body.content.title,'New mathematical claim');});
 await test('revision conflicts preserve the draft and bind the request to its original revision and content hash',async()=>{const app=await boot({respond:(url,body)=>{if(body)throw Object.assign(Error('A newer revision exists'),{status:409});}});await app.show('note-one');byText(app.q('full'),'Edit').click();fill(app,'Improved claim');app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();const write=app.requests.find(r=>r.body);assert.equal(write.body.expected_revision,1);assert.equal(write.body.content_sha256,'hash-1');assert.equal(app.root.querySelector('[name="title"]').value,'Improved claim');assert.match(app.q('dialog-body').textContent,/latest saved revision in another tab/);});
 await test('account switches immediately clear private records and discard a late list response',async()=>{const late=deferred();let delay=false;const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[note()],next_after:null}):undefined});assert.match(app.root.textContent,/Private claim/);delay=true;const loading=app.controller.show();app.setAccount({id:2,handle:'bob'});assert.doesNotMatch(app.root.textContent,/Private claim/);late.resolve({notes:[note()],next_after:null});await loading;assert.doesNotMatch(app.root.textContent,/Private claim/);});
 await test('account switches clear the editor and late saves cannot reopen or navigate private notes',async()=>{const late=deferred();const app=await boot({respond:(url,body)=>body?late.promise:undefined});app.q('new').click();fill(app);app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();app.setAccount(null);assert.equal(app.q('dialog').open,false);assert.equal(app.q('dialog-body').textContent,'');late.resolve({note:note()});await flush();assert.equal(app.q('dialog').open,false);assert.doesNotMatch(app.root.textContent,/New mathematical claim/);});
 await test('browser drafts require review and explicit import, omit unchanged toy notes and never alter local storage',async()=>{const seed=JSON.parse(JSON.stringify(require('../web/notebook/seed.js')));seed.notes.push({id:'my-draft',revisions:[{revision:1,createdAt:1000,...content('My local draft')}]});const app=await boot({stored:JSON.stringify(seed),respond:(url,body)=>url==='/api/notebook/import'?{notes:[],id_map:{}}:undefined});app.q('files').click();assert.equal(app.storage.length,0);byText(app.q('dialog-body'),'Review browser drafts for import').click();assert.equal(app.storage.length,1);assert.match(app.q('dialog-body').textContent,/1 note ready.*6 unchanged examples excluded.*Nothing has been uploaded/);assert.equal(app.requests.filter(r=>r.body).length,0);byText(app.q('dialog-body'),'Import notes').click();await flush();const write=app.requests.find(r=>r.body);assert.equal(write.url,'/api/notebook/import');assert.equal(write.body.workspace.notes.length,1);assert.equal(write.body.workspace.notes[0].id,'my-draft');assert.equal(app.storage.length,1);});
 await test('three pages keep exactly25 summaries rendered and Previous returns to a stable cursor',async()=>{
  const records=Array.from({length:75},(_,i)=>note({id:'page-note-'+i,content:content('Summary '+String(i+1).padStart(2,'0'))}));
  for(const record of records){delete record.revisions[0].content.body;delete record.revisions[0].content.conditions;record.revisions[0].summary=true;}
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,records):undefined});
  function check(first,last,page){const entries=app.q('list').querySelectorAll('.nb-entry');assert.equal(entries.length,25);assert.ok(entries.every(e=>!e.hidden&&e.querySelector('.nb-title').getAttribute('aria-expanded')==='false'));assert.match(entries[0].textContent,new RegExp('Summary '+first));assert.match(entries.at(-1).textContent,new RegExp('Summary '+last));assert.equal(app.q('page').textContent,'Page '+page);}
  check('01','25',1);assert.equal(app.q('prev').disabled,true);
  app.q('more').click();await flush();check('26','50',2);assert.doesNotMatch(app.q('list').textContent,/Summary 01/);
  app.q('more').click();await flush();check('51','75',3);assert.equal(app.q('more').disabled,true);
  app.q('prev').click();await flush();check('26','50',2);assert.equal(listQuery(app.requests.at(-1).url).get('after'),'page:25');
  app.q('prev').click();await flush();check('01','25',1);assert.equal(listQuery(app.requests.at(-1).url).has('after'),false);
  assert.equal(app.requests.some(r=>r.url==='/map/notes.json'||r.url.includes('curated_proposals=')),false);
 });
 await test('navigation hides and resumes an unsaved editor without losing its draft or warning',async()=>{const app=await boot();app.q('new').click();fill(app,'Keep this draft');app.controller.hide();assert.equal(app.q('dialog').open,false);let warned=false;app.sandbox.window.dispatchEvent({type:'beforeunload',preventDefault:()=>{warned=true;}});assert.equal(warned,true);await app.show();assert.equal(app.q('dialog').open,true);assert.equal(app.root.querySelector('[name="title"]').value,'Keep this draft');});
 await test('a save failing while another page is open remains retryable when returning',async()=>{const late=deferred();const app=await boot({respond:(url,body)=>body?late.promise:undefined});app.q('new').click();fill(app);app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();app.controller.hide();late.reject(Object.assign(Error('Lost response'),{status:0}));await flush();assert.equal(app.q('dialog').open,false);await app.show();assert.equal(app.q('dialog').open,true);assert.match(app.q('dialog-body').textContent,/Retry the same save/);assert.equal(app.root.querySelector('[name="title"]').disabled,true);});
 await test('Publish requires an explicit saved-revision request and never exposes private-sharing controls',async()=>{
  const app=await boot({respond:(url,body)=>body?{note:note()}:undefined});await app.show('note-one');byText(app.q('full'),'Publish').click();
  assert.equal(app.requests.some(r=>r.body),false);assert.match(app.q('dialog-body').textContent,/This revision.*after a reviewer approves.*Later edits stay private/);assert.equal(app.root.querySelector('[name="private-share-handles"]'),null);
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();const write=app.requests.find(r=>r.body);assert.equal(write.url,'/api/notebook/notes/note-one/publication-requests');assert.equal(write.body.revision,1);assert.equal(write.body.content_sha256,'hash-1');assert.equal(app.requests.some(r=>r.url.includes('private-shares')),false);
 });
 await test('New note and author actions are visible without notebook review tools',async()=>{
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()],capabilities:{review_publication:true}}:undefined});
  assert.equal(app.q('new').closest('details'),null);assert.equal(app.q('files').closest('details'),null);assert.equal(app.q('review-links'),null);assert.doesNotMatch(app.root.textContent,/Notebook tools|Review:/);
  const card=app.q('list').querySelector('.nb-entry');for(const label of ['Edit','Publish']){assert.equal(byText(card,label).closest('details'),null);assert.equal(byText(card,label).closest('.nb-entry-heading'),card.querySelector('.nb-entry-heading'));assert.equal(byText(card,label).closest('.nb-title'),null);}assert.equal(card.querySelector('.nb-preview').hidden,true);
  await app.show('note-one');for(const label of ['Edit','Publish'])assert.equal(byText(app.q('full'),label).closest('details'),null);assert.equal(byText(app.root,'Share'),undefined);
 });
 await test('only owned editable cards show author actions; handle names and stale share metadata grant nothing',async()=>{
  const records=[note(),note({id:'other',owner:{id:2,handle:'alice'},can_edit:true,private_shared:true}),note({id:'read-only',can_edit:false}),curatedSummary(curatedFixture('curated','Reference'))];
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:records}:undefined});const cards=app.q('list').querySelectorAll('.nb-entry');assert.ok(byText(cards[0],'Edit'));assert.ok(byText(cards[0],'Publish'));for(const card of cards.slice(1)){assert.equal(byText(card,'Edit'),undefined);assert.equal(byText(card,'Publish'),undefined);}
 });
 await test('card Edit fetches the latest full revision rather than editing a list summary',async()=>{
  const summary=note();summary.revisions[0]={...summary.revisions[0],summary:true,content:{title:'Earlier title',claim:'Earlier summary',tags:[]}};
  const latest=note({revision:2,revisions:[revision(),revision(2,'Latest complete note')]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[summary]}:url==='/api/notebook/notes/note-one'?{note:latest}:undefined});byText(app.q('list'),'Edit').click();await flush();assert.equal(app.requests.at(-1).url,'/api/notebook/notes/note-one');assert.ok(app.root.querySelector('[name="title"]'), app.root.textContent);assert.equal(app.root.querySelector('[name="title"]').value,'Latest complete note');assert.equal(app.root.querySelector('[name="body"]').value,'Argument as text');assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('card Publish fetches current detail and requests exactly that saved revision',async()=>{
  const latest=note({revision:2,revisions:[revision(),revision(2,'Current saved note')]});const app=await boot({respond:(url,body)=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?{note:latest}:url.endsWith('/publication-requests')?{}:undefined});byText(app.q('list'),'Publish').click();await flush();assert.match(app.q('dialog-body').textContent,/Current saved note.*Revision 2/);assert.equal(app.requests.some(r=>r.body),false);app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();const write=app.requests.find(r=>r.body);assert.equal(write.body.revision,2);assert.equal(write.body.content_sha256,'hash-2');
 });
 await test('card actions recover from a failed detail fetch and reject stale edit permissions',async()=>{
  let attempts=0;const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?(++attempts===1?Promise.reject(Error('Unavailable')):{note:note({owner:{id:2,handle:'bob'},can_edit:false})}):undefined});const edit=byText(app.q('list'),'Edit');edit.click();await flush();assert.match(app.q('list').textContent,/Unavailable.*Try again/);assert.equal(edit.disabled,false);edit.click();await flush();assert.equal(app.q('dialog').open,false);assert.match(app.q('list').textContent,/latest editable note could not be loaded/);assert.equal(app.requests.some(r=>r.body),false);
 });
 await test('late card detail never opens an editor after account, route, or dialog changes',async()=>{
  for(const change of ['account','route','dialog']){const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url==='/api/notebook/notes/note-one'?late.promise:undefined});byText(app.q('list'),'Edit').click();await flush();if(change==='account')app.setAccount({id:2,handle:'bob'});else if(change==='route')app.controller.hide();else app.q('new').click();late.resolve({note:note({...revision(2,'PRIVATE LATE CONTENT')})});await flush();assert.doesNotMatch(app.root.textContent,/PRIVATE LATE CONTENT/);if(change!=='dialog')assert.equal(app.q('dialog').open,false);else assert.equal(app.q('dialog-title').textContent,'New private note');}
 });
 await test('overlapping card loads restore each card and open only the most recent action',async()=>{
  const first=deferred(),second=deferred(),rows=[note(),note({id:'note-two',...revision(1,'Second note')})];
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:rows}:url==='/api/notebook/notes/note-one'?first.promise:url==='/api/notebook/notes/note-two'?second.promise:undefined});
  const cards=app.q('list').querySelectorAll('.nb-entry'),a=byText(cards[0],'Edit'),b=byText(cards[1],'Edit');a.click();b.click();first.resolve({note:rows[0]});await flush();assert.equal(a.disabled,false);assert.equal(b.disabled,true);assert.equal(app.q('dialog').open,false);assert.equal(cards[0].querySelector('.nb-card-message').hidden,true);second.resolve({note:rows[1]});await flush();assert.equal(b.disabled,false);assert.equal(app.root.querySelector('[name="title"]').value,'Second note');assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('legacy shared links return the ordinary notebook without private-sharing requests',async()=>{
  for(const query of ['shared=1&state=pending','filter=shared']){const app=await boot({initialParams:new URLSearchParams(query)});assert.equal(app.root.querySelector('[name="visibility"]').value,'all');assert.equal(app.requests[0].url,'/api/notebook/notes?limit=25&include_curated=true&scope=all');assert.equal(app.requests.some(r=>r.url.includes('private-shares')),false);assert.equal(byText(app.root,'Share'),undefined);}
 });
 await test('Publish is absent for pending, published, rejected and historical revisions while Edit remains',async()=>{
  for(const state of ['submitted','approved','changes_requested','withdrawn']){
   const record=note({...revision(1,'Saved note',state)});
   const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[record]}:url.startsWith('/api/notebook/notes/note-one')?{note:record}:undefined});
   assert.equal(byText(app.q('list'),'Publish'),undefined);assert.ok(byText(app.q('list'),'Edit'));
   await app.show('note-one');assert.equal(byText(app.q('full'),'Publish'),undefined);assert.ok(byText(app.q('full'),'Edit'));assert.equal(app.requests.some(row=>row.body),false);
  }
  const record=note({revision:2,revisions:[revision(1,'Earlier draft'),revision(2,'Latest draft')]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:record}:undefined});
  await app.show('note-one',1);assert.equal(byText(app.q('full'),'Publish'),undefined);await app.show('note-one',2);assert.ok(byText(app.q('full'),'Publish'));
 });
 await test('a stale Publish card refreshes a newly pending request without another confirmation or write',async()=>{
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()]}:url.startsWith('/api/notebook/notes/note-one')?{note:note({...revision(1,'Pending elsewhere','submitted')})}:undefined});
  byText(app.q('list'),'Publish').click();await flush();assert.equal(byText(app.q('list'),'Publish'),undefined);assert.equal(app.q('dialog').open,false);assert.match(app.q('list').textContent,/Publish request pending/);assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('publication confirmation cancels without a write and a confirmed pending request removes Publish',async()=>{
  let record=note();let writes=0;
  const app=await boot({respond:(url,body)=>{
   if(url.endsWith('/publication-requests')){writes++;record=note({...revision(1,'Private claim','submitted')});return{};}
   if(url.startsWith('/api/notebook/notes?'))return{notes:[record]};
   if(url.startsWith('/api/notebook/notes/note-one'))return{note:record};
  }});
  byText(app.q('list'),'Publish').click();await flush();assert.equal(app.q('dialog-title').textContent,'Request publication');assert.ok(byText(app.q('dialog-body'),'Send publish request'));assert.equal(writes,0);
  byText(app.q('dialog-body'),'Cancel').click();assert.equal(app.q('dialog').open,false);assert.equal(writes,0);assert.ok(byText(app.q('list'),'Publish'));
  byText(app.q('list'),'Publish').click();await flush();byText(app.q('dialog-body'),'Send publish request').closest('form').dispatchEvent({type:'submit'});await flush();assert.equal(writes,1);assert.equal(byText(app.q('full'),'Publish'),undefined);assert.match(app.q('full').textContent,/Publish request pending/);
  await app.show();assert.equal(byText(app.q('list'),'Publish'),undefined);assert.ok(byText(app.q('list'),'Edit'));
 });
 await test('lost publication request responses retry the same exact revision commitment',async()=>{
  const writes=[];const app=await boot({respond:(url,body)=>{if(url.endsWith('/publication-requests')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('Lost response'),{status:0});return{};}}});await app.show('note-one');byText(app.q('full'),'Publish').click();const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();assert.ok(byText(app.q('dialog-body'),'Retry the same request'));form.dispatchEvent({type:'submit'});await flush();assert.deepEqual(writes[0],writes[1]);assert.equal(writes[1].content_sha256,'hash-1');assert.equal(writes[1].revision,1);
 });
 await test('editing a historical privately-shared note now describes a private new revision',async()=>{
  const owned=note({private_share_count:2,can_manage_private_sharing:true});const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:owned}:undefined});await app.show('note-one');byText(app.q('full'),'Edit').click();assert.match(app.q('dialog-body').textContent,/private revision/);assert.doesNotMatch(app.q('dialog-body').textContent,/recipients/);fill(app,'Improved note');app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(app.q('notice').textContent,'Private revision saved.');
 });
 await test('normal note reviewer actions navigate to the separate review page without opening a popup',async()=>{
  const queued=note({can_edit:false,can_review:true,owner:{id:2,handle:'bob'},...revision(2,'Candidate','submitted')});const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:queued}:undefined});await app.show('note-one',2);byText(app.q('full'),'Review note').click();assert.equal(app.location.hash,'#reviews?section=publication&note=note-one&revision=2');assert.equal(app.q('dialog').open,false);assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('review-only notebook has no ordinary author tools and records a publication decision inline',async()=>{
  const queued=note({can_edit:false,can_review:true,owner:{id:2,handle:'bob'},...revision(2,'Candidate','submitted')});const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=publication&note=note-one&revision=2'),respond:(url,body)=>body?{note:queued}:url.startsWith('/api/notebook/notes/note-one')?{note:queued}:undefined});assert.equal(app.q('new'),null);assert.equal(app.q('files'),null);assert.equal(app.q('notes-view'),null);assert.equal(app.q('dialog').open,false);assert.equal(byText(app.q('full'),'Edit'),undefined);assert.match(app.q('full').textContent,/Publication decision.*certify its mathematics/);app.root.querySelector('[name="reason"]').value='Unproved claim clearly marked.';app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();const write=app.requests.find(r=>r.body);assert.equal(write.url,'/api/notebook/notes/note-one/publication-reviews');assert.equal(write.body.decision,'approved');assert.equal(write.body.revision,2);
 });
 await test('own pending publish requests explain missing review permission without exposing decision controls',async()=>{
  for(const [reviewOnly,owner,canReview,status,shown] of [
   [true,user,false,'submitted',true], [true,user,true,'submitted',false],
   [true,{id:2,handle:'bob'},false,'submitted',false], [true,user,false,'private',false],
   [false,user,false,'submitted',false]
  ]) {
   const queued=note({owner,can_edit:false,can_review:canReview,...revision(2,'Pending request',status)});
   const app=await boot({reviewOnly,initialParams:new URLSearchParams('section=publication&note=note-one&revision=2'),respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:queued}:undefined});
   assert.equal(app.q('full').textContent.includes('Another reviewer must approve this request.'),shown);
   if(!canReview)assert.equal(app.root.querySelector('.nb-form'),null);
   assert.equal(app.requests.some(row=>row.body),false);
  }
 });
 await test('detached inline review forms cannot submit after leaving the page',async()=>{
  const queued=note({can_edit:false,can_review:true,owner:{id:2,handle:'bob'},...revision(2,'Candidate','submitted')});const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=publication&note=note-one&revision=2'),respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:queued}:undefined});const form=app.root.querySelector('.nb-form');app.root.querySelector('[name="reason"]').value='Reason';app.controller.hide();form.dispatchEvent({type:'submit'});await flush();assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('inline publication retries preserve the exact decision and ignore an account switch',async()=>{
  const queued=note({can_edit:false,can_review:true,owner:{id:2,handle:'bob'},...revision(2,'Candidate','submitted')}),late=deferred(),writes=[];
  const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=publication&note=note-one&revision=2'),respond:(url,body)=>{if(url.endsWith('/publication-reviews')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('Lost response'),{status:0});return late.promise;}if(url.startsWith('/api/notebook/notes/note-one'))return{note:queued};}});
  const form=app.root.querySelector('.nb-form'),reason=app.root.querySelector('[name="reason"]');reason.value='Exact saved reason';form.dispatchEvent({type:'submit'});await flush();assert.equal(reason.disabled,true);reason.value='Mutated after send';form.dispatchEvent({type:'submit'});await flush();assert.deepEqual(writes[0],writes[1]);const route=app.location.hash;app.setAccount({id:3,handle:'carol'});late.resolve({});await flush();assert.equal(app.location.hash,route);assert.equal(app.q('full').textContent,'');assert.equal(app.q('notice').textContent,'');
 });
 await test('inline research assessment retries preserve the frozen packet and ignore late account responses',async()=>{
  const record={id:'r',title:'A source check',purpose:'source_check',question:'Check the theorem',sources:[],packet_sha256:'a'.repeat(64),assessment:null,scope:'Recommendation only',can_review:true},late=deferred(),writes=[];
  const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=research&research_request=r'),respond:(url,body)=>{if(url.endsWith('/assessments')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('Lost response'),{status:0});return late.promise;}if(url==='/api/notebook/research-requests/r')return{request:record};}});
  const form=app.root.querySelector('.nb-form'),findings=app.root.querySelector('[name="assessment-findings"]');findings.value='Checked source';app.root.querySelector('[name="assessment-limitations"]').value='Proof not independently checked';form.dispatchEvent({type:'submit'});await flush();assert.equal(findings.disabled,true);findings.value='Changed after send';form.dispatchEvent({type:'submit'});await flush();assert.deepEqual(writes[0],writes[1]);assert.equal(writes[1].packet_sha256,record.packet_sha256);app.setAccount(null);late.resolve({request:record});await flush();assert.equal(app.q('full').textContent,'');assert.equal(app.q('dialog').open,false);
 });
 await test('editing while reading an old revision starts from the latest private content and hash',async()=>{const history=note({revision:1,revisions:[revision(1,'Old shared','approved'),revision(2,'Latest private','private')]});const app=await boot({respond:(url,body)=>body?Promise.reject(Object.assign(Error('Invalid fixture'),{status:422})):url.startsWith('/api/notebook/notes/note-one')?{note:history}:undefined});await app.show('note-one',1);byText(app.q('full'),'Edit').click();assert.equal(app.root.querySelector('[name="title"]').value,'Latest private');app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();const write=app.requests.find(r=>r.body);assert.equal(write.body.expected_revision,2);assert.equal(write.body.content_sha256,'hash-2');});
 await test('explicit import preserves views and clearly identifies example notes needed by their bindings',async()=>{const seed=JSON.parse(JSON.stringify(require('../web/notebook/seed.js')));seed.views=[{id:'view-one',title:'My argument',question:'Which claim?',scope:'Toy fixture',kind:'argument',bindings:[{id:'bind-one',noteId:'alias-lemma',noteRevision:1,excerpt:'Conditional lemma',scope:'Toy fixture',assessment:'unreviewed',assessmentReason:'',from:'a',to:'b',relation:'supports'}]}];const app=await boot({stored:JSON.stringify(seed),respond:(url,body)=>url==='/api/notebook/import'?{added_notes:1}:undefined});app.q('files').click();byText(app.q('dialog-body'),'Review browser drafts for import').click();assert.match(app.q('dialog-body').textContent,/1 example notes are included because saved views reference them/);byText(app.q('dialog-body'),'Import notes').click();await flush();const write=app.requests.find(r=>r.body);assert.equal(write.body.workspace.views.length,1);assert.equal(write.body.workspace.notes[0].id,'alias-lemma');});
 await test('My notes shows the latest private revision and keeps the approved revision accessible separately',async()=>{
  const own=note({...revision(2,'PRIVATE FUTURE'),shared_revision:revision(1,'Approved original','approved'),revisions:[revision(1,'Approved original','approved'),revision(2,'PRIVATE FUTURE')]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[own],next_after:null}:url.startsWith('/api/notebook/notes/note-one')?{note:own}:undefined});
  await filterNotes(app,'mine');
  assert.match(app.q('list').textContent,/PRIVATE FUTURE.*Private/);assert.doesNotMatch(app.q('list').textContent,/Approved original/);
  app.q('list').querySelector('.nb-title').click();byText(app.q('list'),'Read full note →').click();await app.whenNavigated();
  assert.equal(app.requests.at(-1).url,'/api/notebook/notes/note-one');
  byText(app.q('full'),'Read community revision 1 →').click();await app.whenNavigated();
  assert.equal(app.requests.at(-1).url,'/api/notebook/notes/note-one?revision=1');
  assert.match(app.q('full').textContent,/Approved original.*Published/);assert.doesNotMatch(app.q('full').textContent,/PRIVATE FUTURE/);
 });

 await test('map preparation starts after the visible list and is reused without a second mount',async()=>{
  let mounts=0,shows=0,hides=0;const late=deferred();
  const app=await boot({mapFactory:()=>{mounts++;return late.promise;}});
  assert.equal(mounts,0);await app.advanceTimers(299);assert.equal(mounts,0);
  await app.advanceTimers(300);assert.equal(mounts,1);assert.equal(app.q('map').hidden,true);
  app.q('map-view').dispatchEvent({type:'pointerenter'});app.q('map-view').dispatchEvent({type:'focus'});
  assert.equal(mounts,1,'Intent shares the same preparation');
  late.resolve({show:()=>shows++,hide:()=>hides++,destroy:()=>{}});await flush();
  assert.equal(shows,0);assert.equal(hides,1);assert.equal(app.q('library').hidden,false);
  app.q('map-view').click();await app.whenNavigated();
  assert.equal(mounts,1);assert.equal(shows,1);assert.equal(app.q('map').hidden,false);
 });
 await test('map intent prepares immediately, stays hidden and cannot outlive its account',async()=>{
  let mounts=0,destroyed=0;const late=deferred();
  const app=await boot({mapFactory:()=>{mounts++;return late.promise;}});
  app.q('map-view').dispatchEvent({type:'focus'});assert.equal(mounts,1);
  app.setAccount(null);late.resolve({show:()=>{throw Error('Revoked map shown');},hide:()=>{},destroy:()=>destroyed++});await flush();
  assert.equal(destroyed,1);assert.equal(app.q('map').textContent,'');
  await app.advanceTimers(300);assert.equal(mounts,1);
 });
 await test('leaving the notebook cancels queued background preparation',async()=>{
  let mounts=0;const app=await boot({mapFactory:async()=>{mounts++;return null;}});
  app.controller.hide();await app.advanceTimers(300);assert.equal(mounts,0);
 });
 await test('review workspace never schedules or accepts map preparation',async()=>{
  let mounts=0;const app=await boot({reviewOnly:true,mapFactory:async()=>{mounts++;return null;},respond:url=>url.startsWith('/api/notebook/review-queue?')?{notes:[],next_after:null}:undefined});
  await app.advanceTimers(300);assert.equal(mounts,0);
 });

 await test('map receives a current catalogue callback rather than a frozen release snapshot',async()=>{
  let releases=[{id:'frodokem',version:'first'}],context;
  const app=await boot({currentReleases:()=>releases,mapFactory:async(_,current,options)=>{
    assert.equal(current(),true);context=options;return{show:()=>{},hide:()=>{},destroy:()=>{}};
  }});
  app.q('map-view').click();await flush();assert.equal(context.currentReleases(),releases);
  releases=[{id:'frodokem',version:'next'}];assert.equal(context.currentReleases(),releases);
  assert.equal(app.requests.some(request=>request.url==='/api/schemes'),false);
 });
 await test('network visualization restores the filtered notes and revalidates their visibility quietly',async()=>{
  let mounts=0,shows=0,hides=0;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note()],next_after:null}:undefined,mapFactory:async()=>{mounts++;return{show:()=>shows++,hide:()=>hides++,destroy:()=>{}};}});
  const heading=app.root.querySelector('h1');
  await searchNotes(app,'Private');
  const before=app.requests.length;
  app.q('map-view').click();await flush();
  assert.equal(app.location.hash,'#notebook?view=map');
  assert.equal(app.root.querySelector('h1'),heading);
  assert.equal(app.q('library').hidden,true);assert.equal(app.q('map').hidden,false);
  assert.equal(app.q('map-view').getAttribute('aria-current'),'page');
  assert.equal(app.q('notes-view').getAttribute('aria-current'),null);
  assert.equal(app.root.querySelector('.nb-visualizations').textContent,'VisualizationsScheme mapTimeline');
  app.q('notes-view').click();await flush();
  assert.equal(app.q('library').hidden,false);assert.equal(app.q('map').hidden,true);
  assert.equal(app.q('search').value,'Private');assert.match(app.q('list').textContent,/Private claim/);
  assert.equal(app.requests.length,before+1);assert.equal(mounts,1);
  app.q('map-view').click();await flush();assert.equal(mounts,1);assert.ok(shows>=2&&hides>=1);
 });
 await test('leaving the map during loading cannot replace the restored notebook',async()=>{
  const late=deferred();let disposed=0;
  const app=await boot({mapFactory:()=>late.promise});
  const loading=app.controller.show(new URLSearchParams('view=map'));
  await app.controller.show();
  late.resolve({show:()=>{throw Error('Stale map shown');},hide:()=>{},destroy:()=>disposed++});await loading;
  assert.equal(disposed,0,'Completed preparation stays hidden for the next map visit');assert.equal(app.q('map').hidden,true);assert.equal(app.q('library').hidden,false);
 });
 await test('account changes dispose a map and ignore its delayed initialization',async()=>{
  const late=deferred();let disposed=0;
  const app=await boot({mapFactory:()=>late.promise});
  const loading=app.controller.show(new URLSearchParams('view=map'));app.setAccount(null);
  late.resolve({show:()=>{throw Error('Map reopened after sign out');},hide:()=>{},destroy:()=>disposed++});await loading;
  assert.equal(disposed,1);assert.equal(app.q('map').textContent,'');
 });

 await test('My notes and Literature request globally filtered summary pages without fetching bodies',async()=>{
  const shared=note({id:'shared',can_edit:false,owner:{id:2,handle:'bob'},...revision(1,'Approved claim','approved')});
  const published=note({id:'my-shared',...revision(1,'My published note','approved')});
  const all=[note(),published,shared,...research.notes.map(curatedSummary)];
  const app=await boot({researchWorkspace:research,respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,all):undefined});
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);
  await filterNotes(app,'research');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'literature');
  assert.ok(app.q('list').querySelectorAll('.nb-entry').every(e=>e.querySelector('.nb-literature')));
  await filterNotes(app,'mine');assert.equal(listQuery(app.requests.at(-1).url).get('scope'),'mine');
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,2);assert.match(app.q('list').textContent,/Private claim.*Private.*My published note.*Published/);assert.doesNotMatch(app.q('list').textContent,/Approved claim/);
  assert.deepEqual(Array.from(app.root.querySelector('[name="visibility"]').querySelectorAll('option'),option=>option.textContent),['All notes','My notes','Human-written','Proposals','Literature','Trash']);
  assert.equal(app.requests.some(r=>r.url==='/map/notes.json'||r.body),false);assert.equal(app.storage.length,0);
 });
 await test('curated source links preserve every revision hash and expose sources without author or review tools',async()=>{
  const app=await boot({researchWorkspace:research});
  vm.runInContext(fs.readFileSync(path.join(rootPath,'web/map/notes-data.js'),'utf8'),app.sandbox);
  for(const source of research.notes){
   await app.show('curated:'+source.id,1);
   assert.ok(app.q('full').textContent.includes(source.revisions[0].title));
   assert.ok(app.q('full').textContent.includes(app.sandbox.window.CRUCIBLE_NOTES.revision_hashes[source.id]['1']));
   assert.equal(app.q('full').querySelectorAll('.nb-owner').length,0);
   assert.equal(byText(app.q('full'),'Edit'),undefined);
   assert.equal(byText(app.q('full'),'Submit for community sharing'),undefined);
  }
  await app.show('curated:landscape-ml-kem',1);
  assert.ok(app.q('full').querySelector('.nb-research-sources').querySelector('a').href.startsWith('https://'));
  assert.equal(app.requests.filter(r=>r.url.startsWith('/api/notebook/notes/')).length,0);
  await app.show('curated:landscape-ml-kem',99);assert.match(app.q('full').textContent,/Revision not available/);
 });
 await test('curated list summaries stay bounded and full content loads only on explicit opening',async()=>{
  const app=await boot({researchWorkspace:research});
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);assert.equal(app.requests.some(r=>r.url==='/map/notes.json'),false);
  app.q('more').click();await flush();assert.equal(app.q('list').querySelectorAll('.nb-entry').length,research.notes.length-25);
  assert.equal(app.requests.some(r=>r.url==='/map/notes.json'),false);
  await app.show('curated:landscape-ml-kem',1);assert.equal(app.requests.filter(r=>r.url==='/map/notes.json').length,1);
  assert.match(app.q('full').textContent,/ML-KEM/);
 });
 await test('community sharing requests uses clear audience and revision language and excludes curated research',async()=>{
  const app=await boot({reviewOnly:true,researchWorkspace:research,initialParams:new URLSearchParams('section=publication'),respond:url=>url.startsWith('/api/notebook/review-queue')?{notes:[note({...revision(1,'Review candidate','submitted')})],next_after:null}:undefined});
  assert.match(app.q('list').textContent,/Publish requests.*exact revision.*all approved participants.*internet.*mathematics.*Review candidate.*Publish request pending/);assert.doesNotMatch(app.q('list').textContent,/Sharing review|Awaiting sharing approval/);assert.equal(app.q('list').querySelectorAll('.nb-literature').length,0);
  assert.equal(app.requests.some(r=>r.url==='/map/notes.json'),false);
 });
 await test('an obsolete notebook cursor restarts at the first bounded page after an explicit retry',async()=>{
  const app=await boot({respond:url=>{if(url.startsWith('/api/notebook/notes?')){if(listQuery(url).has('after'))throw Object.assign(Error('Invalid notebook page cursor. Restart the notebook list.'),{status:422});return{notes:[note()],next_after:'old-cursor'};}}});
  app.q('more').click();await flush();assert.match(app.q('list').textContent,/Invalid notebook page cursor/);byText(app.q('list'),'Restart notebook list').click();await flush();assert.equal(listQuery(app.requests.at(-1).url).has('after'),false);assert.equal(listQuery(app.requests.at(-1).url).get('limit'),'25');assert.equal(app.q('page').textContent,'Page 1');assert.match(app.q('list').textContent,/Private claim/);
 });
 await test('summary-page failures expose a retry without loading an unbounded fallback collection',async()=>{
  let failing=true;const app=await boot({respond:url=>{if(url.startsWith('/api/notebook/notes?')){if(failing)throw Error('Notebook temporarily unavailable');return{notes:[note()],next_after:null};}}});
  assert.match(app.q('list').textContent,/Notebook temporarily unavailable/);assert.equal(app.requests.some(r=>r.url==='/map/notes.json'),false);
  failing=false;byText(app.q('list'),'Try again').click();await flush();assert.match(app.q('list').textContent,/Private claim/);assert.doesNotMatch(app.q('list').textContent,/Notebook temporarily unavailable/);
 });
 await test('account change discards delayed full curated content and reloads the next account summary page',async()=>{
  const late=deferred();let delaying=true;const app=await boot({researchWorkspace:research,respond:url=>url==='/map/notes.json'&&delaying?late.promise:undefined});
  const pending=app.show('curated:landscape-ml-kem',1);await flush();app.setAccount({id:3,handle:'carol'});late.resolve(research);await pending;
  assert.equal(app.q('list').textContent,'');assert.equal(app.q('full').textContent,'');delaying=false;await app.show();assert.equal(app.q('list').querySelectorAll('.nb-entry').length,25);
 });
 await test('new proposal composer attaches a checked specification but only saves a private note',async()=>{
  const spec={name:'Candidate KEM',format:'crucible-kem-spec-v1',references:[]};let saved;
  const app=await boot({initialParams:new URLSearchParams('compose=proposal'),respond:(url,body)=>{
    if(url==='/api/protocol/proposals/validate')return{specification:spec,note_content:content('Candidate KEM')};
    if(url==='/api/notebook/notes'&&body){saved=body;return{note:note()};}
  }});
  assert.equal(app.q('dialog').open,true);
  const file=app.root.querySelector('[name="kem-specification"]');file.files=[{size:30,text:async()=>JSON.stringify(spec)}];file.dispatchEvent({type:'change'});await flush();
  assert.equal(app.root.querySelector('[name="title"]').value,'Candidate KEM');
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(saved.content.extensions.kem_specification.name,'Candidate KEM');
  assert.ok(saved.content.tags.includes('scheme-description'));
  assert.equal(app.requests.some(r=>r.url.includes('proposal-submissions')),false);
 });
 await test('scheme review explicitly freezes the saved note revision and retries the same request',async()=>{
  const rev={...revision(),content:{...content(),extensions:{kem_specification:{name:'Candidate'}}}};
  const draft=note({revisions:[rev]});let writes=0;
  const app=await boot({respond:(url,body)=>{
    if(url.endsWith('/proposal-submissions')){writes++;if(writes===1)throw Object.assign(Error('Lost response'),{status:0});return{proposal:{id:'proposal-one',source_note:{id:'note-one',revision:1}}};}
    if(url.startsWith('/api/notebook/notes/note-one'))return{note:draft};
  }});
  await app.show('note-one');byText(app.q('full'),'Submit for scheme review').click();await flush();
  assert.match(app.q('dialog-body').textContent,/review request is private to you and reviewers.*Existing note access remains unchanged/);
  assert.doesNotMatch(app.q('dialog-body').textContent,/note remains private to you and reviewers/);
  assert.equal(writes,0);byText(app.q('dialog-body'),'Submit this revision').click();await flush();
  byText(app.q('dialog-body'),'Retry this submission').click();await flush();
  const submissions=app.requests.filter(r=>r.url.endsWith('/proposal-submissions'));
  assert.equal(submissions[0].body,submissions[1].body);
  assert.equal(submissions[0].body.revision,1);assert.equal(submissions[0].body.content_sha256,'hash-1');
  assert.equal(submissions.some(r=>r.url.includes('publication')),false);
 });
 await test('submitted proposal notes expose review history while participant readers cannot edit or open private review',async()=>{
  const rev={...revision(),content:{...content(),extensions:{kem_specification:{name:'Candidate'}}},scheme_review:{id:'proposal-one',status:'submitted',revision:1}};
  let shown=note({revisions:[rev]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:shown}:undefined});
  await app.show('note-one');assert.match(app.q('full').textContent,/Awaiting scheme review/);
  assert.ok(byText(app.q('full'),'Review & history →'));assert.equal(byText(app.q('full'),'Submit for scheme review'),undefined);
  shown={...shown,can_edit:false,can_review:false,owner:{id:2,handle:'other'}};await app.show('note-one');
  assert.equal(byText(app.q('full'),'Review & history →'),undefined);assert.equal(byText(app.q('full'),'Edit note'),undefined);
 });
 await test('moderator withdrawal pins the revision and retries an uncertain decision',async()=>{
  let shown=note({can_edit:false,can_review:true,can_moderate:true,owner:{id:2,handle:'bob'},revisions:[{...revision(1,'Shared claim','approved'),publication:{scope:'Approval permits community access.'}}]}),attempts=0;
  const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=publication&note=note-one'),respond:(url,body)=>{
   if(url.endsWith('/publication-withdrawals')){if(++attempts===1)throw Error('Connection lost');shown.revisions[0]={...shown.revisions[0],publication_status:'withdrawn',moderation:{reason:'Remove private information.'}};return{withdrawal:{reason:'Remove private information.'}};}
   if(url.startsWith('/api/notebook/notes/note-one'))return{note:shown};
  }});
  await app.show('note-one');byText(app.q('full'),'Withdraw community access').click();
  app.root.querySelector('[name="withdrawal-reason"]').value='Remove private information.';
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  const writes=app.requests.filter(row=>row.url.endsWith('/publication-withdrawals'));
  assert.equal(writes.length,2);assert.equal(writes[0].body,writes[1].body);
  assert.equal(writes[0].body.revision,1);assert.equal(writes[0].body.content_sha256,'hash-1');
  assert.match(app.q('full').textContent,/Withdrawn by moderator/);
  assert.match(app.q('full').textContent,/Historical publication decision/);
  assert.doesNotMatch(app.q('full').textContent,/Approval permits community access/);
  assert.equal(byText(app.q('full'),'Withdraw community access'),undefined);
 });
 await test('withdrawn citation replaces old content with a neutral notice',async()=>{
  let withdrawn=false;
  const app=await boot({respond:url=>{if(url.startsWith('/api/notebook/notes/note-one')){if(withdrawn)throw Object.assign(Error('This revision was withdrawn from community visibility by a moderator.'),{status:410});return{note:note()};}}});
  await app.show('note-one');assert.match(app.q('full').textContent,/Private claim/);
  withdrawn=true;await app.show('note-one',1);
  assert.match(app.q('full').textContent,/Revision withdrawn/);assert.doesNotMatch(app.q('full').textContent,/Private claim|Loading note/);
  assert.equal(byText(app.q('full'),'Download agent research task'),undefined);
 });
 await test('withheld review packet cannot be downloaded as complete evidence',async()=>{
  const app=await boot({respond:url=>url==='/api/notebook/research-requests/request-one'?{request:{id:'request-one',withdrawn_source:true,scope:'A source revision was withdrawn by a moderator.'}}:undefined});
  await app.controller.show(new URLSearchParams({research_request:'request-one'}));
  assert.match(app.q('full').textContent,/Review packet withheld/);assert.equal(byText(app.q('full'),'Download review packet'),undefined);
 });
 await test('scheme panel qualifies development self-review and retains retraction precedence',async()=>{
  const review={id:'proposal-one',status:'approved',revision:1,test_only_self_review:true,effective:false};
  let shown=note({revisions:[{...revision(),content:{...content(),extensions:{kem_specification:{name:'Candidate'}}},scheme_review:review}]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:shown}:undefined});
  await app.show('note-one');
  assert.match(app.q('full').querySelector('.nb-scheme-state').textContent,/test review.*inactive/);
  shown.revisions[0].scheme_review={...review,effective:true};await app.show('note-one');
  assert.match(app.q('full').querySelector('.nb-scheme-state').textContent,/test review/);
  assert.doesNotMatch(app.q('full').querySelector('.nb-scheme-state').textContent,/inactive/);
  shown.revisions[0].scheme_review={...review,retracted:true};await app.show('note-one');
  assert.equal(app.q('full').querySelector('.nb-scheme-state').textContent,'Retracted by proposer');
 });
 await test('every note has citations and citing it pins the selected revision without writing or fetching',async()=>{
  const hash='a'.repeat(64), saved=note({content_sha256:hash});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:saved}:undefined});
  await app.show('note-one',1);assert.match(app.q('full').textContent,/CitationsNo citations recorded/);
  const before=app.requests.length;let copied;app.sandbox.navigator.clipboard={writeText:async value=>{copied=value;}};
  byText(app.q('full'),'Cite this note').click();byText(app.q('dialog-body'),'Copy citation JSON').click();await flush();
  const reference=JSON.parse(copied);assert.equal(reference.title,'Private claim · revision 1');
  assert.equal(reference.url,'/#notebook?note=note-one&revision=1&sha256='+hash);
  assert.equal(app.requests.length,before);assert.match(app.q('dialog-body').textContent,/does not change who can read/);
 });
 await test('citations render inert external sources and exact note links without prefetching',async()=>{
  const refs=[{title:'Primary paper',url:'https://example.org/paper#theorem-2',scope:'Theorem 2, correctness only.'},
   {title:'Prior note',url:'/#notebook?note=other&revision=2&sha256='+'b'.repeat(64),scope:'Lemma 1; unreviewed.'},
   {title:'Unsafe link',url:'javascript:alert(1)'},'An older textual reference'];
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:note({content:{...content(),references:refs}})}:undefined});
  await app.show('note-one');const sources=app.q('full').querySelector('.nb-research-sources');
  const links=sources.querySelectorAll('a');assert.equal(links.length,2);assert.equal(links[0].target,'_blank');
  assert.match(links[1].href,/^#notebook\?note=other&revision=2&sha256=/);assert.notEqual(links[1].target,'_blank');
  assert.match(sources.textContent,/correctness only/);assert.match(sources.textContent,/older textual reference/);
  assert.ok(app.requests.every(r=>!r.url.includes('other')&&!r.url.includes('example.org')));
 });
 await test('a mismatched citation hash never displays the claimed note content or silently selects latest',async()=>{
  const saved=note({content_sha256:'a'.repeat(64)});const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:saved}:undefined});
  await app.controller.show(new URLSearchParams({note:'note-one',revision:'1',sha256:'b'.repeat(64)}));
  assert.match(app.q('full').textContent,/citation does not match/);assert.doesNotMatch(app.q('full').textContent,/Private claim/);
  await app.controller.show(new URLSearchParams({note:'note-one',revision:'1',sha256:'a'.repeat(64)}));
  assert.match(app.q('full').textContent,/Private claim/);
 });
 await test('citation editing validates links and preserves extra source metadata and pending retries',async()=>{
  const refs=[{title:'Paper',url:'https://example.org/paper',scope:'Original statement.',id:'keep-provenance'}];
  const saved=note({content:{...content(),references:refs}});let writes=0;
  const app=await boot({respond:(url,body)=>{if(body){writes++;throw Object.assign(Error('Lost response'),{status:0});}if(url.startsWith('/api/notebook/notes/note-one'))return{note:saved};}});
  saved.revisions[0].content.references=vm.runInContext('('+JSON.stringify(refs)+')',app.sandbox);
  await app.show('note-one');byText(app.q('full'),'Edit').click();
  const link=app.root.querySelector('[name="citation-0-url"]');link.value='javascript:alert(1)';link.dispatchEvent({type:'input'});
  const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();assert.equal(writes,0);assert.match(app.q('dialog-body').textContent,/valid source link/);
  link.value='https://example.org/paper#section-3';link.dispatchEvent({type:'input'});form.dispatchEvent({type:'submit'});await flush();
  assert.equal(link.disabled,true);assert.equal(byText(app.q('dialog-body'),'Add citation').disabled,true);
  link.value='https://example.org/changed';form.dispatchEvent({type:'submit'});await flush();
  const sent=app.requests.filter(r=>r.body);assert.equal(sent.length,2);assert.deepEqual(sent[0].body,sent[1].body);
  assert.equal(sent[0].body.content.references[0].id,'keep-provenance');assert.equal(sent[0].body.content.references[0].url,'https://example.org/paper#section-3');
 });
 await test('new source rows and agent guidance require deliberate claim-specific citations',async()=>{
  let saved;const app=await boot({respond:(url,body)=>{if(body){saved=body;return{note:note()};}}});
  app.q('new').click();fill(app);byText(app.q('dialog-body'),'Add citation').click();
  for(const [key,value] of Object.entries({title:'A source',url:'https://example.org/source',scope:'Section 4 supports this particular claim.'})){
   const field=app.root.querySelector('[name="citation-0-'+key+'"]');field.value=value;field.dispatchEvent({type:'input'});
  }
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(saved.content.references[0].scope,'Section 4 supports this particular claim.');
  app.q('files').click();assert.match(app.q('dialog-body').textContent,/separate, deliberate pass/);assert.match(app.q('dialog-body').textContent,/exact revision and content hash/);assert.match(app.q('dialog-body').textContent,/Do not invent citations/);
 });
 await test('note connections are lazy, directional, exact and navigable without trusting authored titles',async()=>{
  const hash='a'.repeat(64), saved=note({content_sha256:hash}), root={note_id:'note-one',revision:1,content_sha256:hash,title:'Private claim'};
  const app=await boot({respond:url=>{
   if(url.startsWith('/api/notebook/notes/note-one'))return{note:saved};
   if(url.startsWith('/api/notebook/graph?')){const direction=new URL(url,'http://test').searchParams.get('direction');const other={note_id:direction==='incoming'?'response':'prior',revision:2,content_sha256:'b'.repeat(64),title:direction==='incoming'?'Counterexample':'Original lemma'};
    return{format:'crucible-note-graph-v1',root,direction,links:[{source:direction==='incoming'?other:root,target:direction==='incoming'?root:other,relation:direction==='incoming'?'challenges':'builds_on',scope:'<script>inert authored explanation</script>'}],next_after:null};}
  }});
  await app.show('note-one',1);assert.equal(app.requests.some(row=>row.url.startsWith('/api/notebook/graph')),false);
  byText(app.q('full'),'Explore connections').click();await flush();
  const graph=app.root.querySelector('.nb-note-graph');assert.match(graph.textContent,/Challenges this revision/);assert.match(graph.textContent,/Builds on/);
  assert.match(graph.textContent,/Counterexample/);assert.match(graph.textContent,/Original lemma/);assert.equal(graph.querySelectorAll('script').length,0);
  const links=graph.querySelectorAll('a');assert.equal(links.length,2);assert.ok(links.every(link=>link.href.includes('revision=2&sha256='+'b'.repeat(64)+'&connections=1')));
  assert.equal(app.requests.filter(row=>row.url.startsWith('/api/notebook/graph')).length,2);assert.equal(app.requests.some(row=>row.body),false);
 });
 await test('connection failures retry, paginate and do not claim an unavailable graph is empty',async()=>{
  const root={note_id:'note-one',revision:1,content_sha256:'a'.repeat(64),title:'Private claim'};let fail=true;
  const app=await boot({respond:url=>{
   if(url.startsWith('/api/notebook/notes/note-one'))return{note:note({content_sha256:root.content_sha256})};
   if(url.startsWith('/api/notebook/graph?')){const q=new URL(url,'http://test').searchParams,direction=q.get('direction');if(direction==='incoming'&&fail)throw Error('Connections temporarily unavailable');
    const target={note_id:q.has('after')?'second':'first',revision:1,content_sha256:'b'.repeat(64),title:q.has('after')?'Second source':'First source'};
    return{format:'crucible-note-graph-v1',root,direction,links:direction==='incoming'?[]:[{source:root,target,relation:'cites'}],next_after:direction==='outgoing'&&!q.has('after')?'next':null};}
  }});
  await app.show('note-one',1);byText(app.q('full'),'Explore connections').click();await flush();
  const graph=app.root.querySelector('.nb-note-graph');assert.match(graph.textContent,/temporarily unavailable/);assert.doesNotMatch(graph.textContent,/No readable connections/);
  fail=false;byText(graph,'Retry connections').click();byText(graph,'More connections').click();await flush();
  assert.match(graph.textContent,/No readable connections/);assert.match(graph.textContent,/First source.*Second source/);assert.equal(byText(graph,'More connections'),undefined);
 });
 await test('late connections cannot restore private graph content after an account or route change',async()=>{
  for(const change of ['account','route']){const late=deferred(),hash='a'.repeat(64),root={note_id:'note-one',revision:1,content_sha256:hash,title:'PRIVATE graph'};
   const app=await boot({respond:url=>url.startsWith('/api/notebook/graph?')?late.promise:url.startsWith('/api/notebook/notes/note-one')?{note:note({content_sha256:hash})}:undefined});
   await app.show('note-one',1);byText(app.q('full'),'Explore connections').click();await flush();
   if(change==='account')app.setAccount({id:2,handle:'bob'});else await app.controller.show(new URLSearchParams());
   late.resolve({format:'crucible-note-graph-v1',root,direction:'incoming',links:[{source:{...root,note_id:'private-response',title:'PRIVATE late response'},target:root,relation:'challenges'}],next_after:null});await flush();
   assert.doesNotMatch(app.root.textContent,/PRIVATE late response/);
  }
 });
 await test('connection deep links explore the chosen revision and reject a mismatched graph root',async()=>{
  const hash='a'.repeat(64),saved=note({content_sha256:hash});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:saved}:url.startsWith('/api/notebook/graph?')?{format:'crucible-note-graph-v1',root:{note_id:'note-one',revision:2,content_sha256:hash},direction:new URL(url,'http://test').searchParams.get('direction'),links:[],next_after:null}:undefined});
  await app.controller.show(new URLSearchParams({note:'note-one',revision:'1',sha256:hash,connections:'1'}));await flush();
  assert.match(app.root.textContent,/connections could not be verified/);assert.doesNotMatch(app.root.querySelector('.nb-note-graph').textContent,/No readable connections/);
 });
 await test('typed note relationships canonicalize local links and require exact revision hashes',async()=>{
  let saved;const app=await boot({respond:(url,body)=>{if(body){saved=body;return{note:note()};}}});
  app.q('new').click();fill(app);byText(app.q('dialog-body'),'Add citation').click();
  for(const [key,value]of Object.entries({title:'Prior note',url:'/#notebook?note=prior&revision=1',scope:'A counterexample to its main claim.',relation:'challenges'})){
   const field=app.root.querySelector('[name="citation-0-'+key+'"]');field.value=value;field.dispatchEvent({type:key==='relation'?'change':'input'});
  }
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(saved,undefined);assert.match(app.q('dialog-body').textContent,/exact revision and content hash/);
  const source=app.root.querySelector('[name="citation-0-url"]');source.value=new URL(app.location.href).origin+'/#notebook?note=prior&revision=1&sha256='+'b'.repeat(64);source.dispatchEvent({type:'input'});
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(saved.content.references[0].relation,'challenges');assert.equal(saved.content.references[0].url,'/#notebook?note=prior&revision=1&sha256='+'b'.repeat(64));
 });
 await test('editing preserves unrecognized relationship metadata until deliberately changed',async()=>{
  const refs=[{title:'Paper',url:'https://example.org/paper',scope:'A prior result.',relation:{legacy:'retain exactly'},other:'also retained'}], source=note();let saved;
  const app=await boot({respond:(url,body)=>body?(saved=body,{note:note()}):url.startsWith('/api/notebook/notes/note-one')?{note:source}:undefined});
  source.revisions[0].content.references=vm.runInContext('('+JSON.stringify(refs)+')',app.sandbox);
  await app.show('note-one');byText(app.q('full'),'Edit').click();const title=app.root.querySelector('[name="citation-0-title"]');title.value='Corrected title';title.dispatchEvent({type:'input'});
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.ok(saved,app.q('dialog-body').textContent);assert.equal(JSON.stringify(saved.content.references[0].relation),JSON.stringify(refs[0].relation));assert.equal(saved.content.references[0].other,'also retained');
 });
 await test('Write a related note pins the selected source and requires a concrete explanation',async()=>{
  const hash='a'.repeat(64);let saved;
  const app=await boot({respond:(url,body)=>body?(saved=body,{note:note()}):url.startsWith('/api/notebook/notes/note-one')?{note:note({content_sha256:hash})}:undefined});
  await app.show('note-one',1);byText(app.q('full'),'Write a related note').click();fill(app,'A useful counterexample');
  assert.equal(app.root.querySelector('[name="citation-0-relation"]').value,'responds_to');
  assert.equal(app.root.querySelector('[name="citation-0-url"]').value,'/#notebook?note=note-one&revision=1&sha256='+hash);
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(saved,undefined);
  const scope=app.root.querySelector('[name="citation-0-scope"]');scope.value='This counterexample invalidates the claim at x = 0.';scope.dispatchEvent({type:'input'});
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(saved.content.references[0].relation,'responds_to');assert.equal(app.requests.find(row=>row.body).url,'/api/notebook/notes');
 });
 await test('source checks freeze the selected revision and retry a lost response with the same payload',async()=>{
  let attempts=0;const writes=[];
  const record={id:'request-one',title:'A source check',purpose:'source_check',question:'Check section 2',sources:[],packet_sha256:'a'.repeat(64),assessment:null,scope:'No activation',can_review:false};
  const app=await boot({respond:(url,body)=>{if(url==='/api/notebook/research-requests'&&body){writes.push(JSON.parse(JSON.stringify(body)));if(++attempts===1)throw Object.assign(Error('Response lost'),{status:0});return{request:record};}if(url==='/api/notebook/research-requests/request-one')return{request:record};}});
  await app.show('note-one',1);assert.equal(byText(app.q('full'),'Request research review'),undefined);byText(app.q('full'),'Request source check').click();await flush();
  const purpose=app.root.querySelector('[name="research-purpose"]');assert.equal(purpose.value,'source_check');assert.equal(purpose.options.some(option=>option.value==='technical_review'),false);app.root.querySelector('[name="research-question"]').value='Check section 2';
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(app.root.querySelector('[name="research-question"]').disabled,true);
  app.root.querySelector('[name="research-question"]').value='Changed pending input';
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  assert.deepEqual(writes[0],writes[1]);assert.equal(writes[0].purpose,'source_check');assert.equal(writes[0].sources[0].note_id,'note-one');assert.equal(writes[0].sources[0].content_sha256,'hash-1');
  assert.equal(app.location.hash,'#notebook?research_request=request-one');assert.match(app.q('full').textContent,/Check section 2/);
 });
 await test('all remaining request purposes load guidance without the retired technical review definition',async()=>{
  const activeTypes={...requestTypes,actions:requestTypes.actions.filter(action=>action.id!=='technical_review')};
  const app=await boot({respond:url=>url==='/notebook/request-types.json'?activeTypes:undefined});
  await app.show('note-one');byText(app.q('full'),'Request source check').click();await flush();
  const purpose=app.root.querySelector('[name="research-purpose"]');
  assert.deepEqual(purpose.options.map(option=>option.value).sort(),['highlight_nomination','map_nomination','procedure','scoring_nomination','source_check','visualization_nomination']);
  for(const option of purpose.options){purpose.value=option.value;purpose.dispatchEvent({type:'change'});await flush();assert.equal(byText(app.q('dialog-body'),'Send for review').disabled,false);assert.ok(app.q('dialog-body').textContent.includes(activeTypes.actions.find(action=>action.id===option.value).description));}
 });
 await test('retired technical requests remain readable without pending labels or assessment actions',async()=>{
  const source={note_id:'note-one',revision:1,content_sha256:'hash-1',content:content('Frozen historical claim')};
  // A stale server may still say can_review: purpose alone must retire this action.
  const record={id:'retired',title:'Historic technical review',purpose:'technical_review',question:'Old mathematical question',sources:[source],assessment:null,scope:'Historical request',can_review:true};
  const stored={...revision(),requests:[{id:record.id,kind:'technical_review',status:'submitted'}]};
  const app=await boot({respond:url=>url==='/api/notebook/research-requests/retired'?{request:record}:url.startsWith('/api/notebook/research-requests?')?{requests:[record],can_review:false,next_after:null}:url.startsWith('/api/notebook/notes/note-one')?{note:note({revisions:[stored]})}:undefined});
  await app.show('note-one');assert.match(app.q('full').textContent,/Technical review \(retired\).*Retired request type/);assert.doesNotMatch(app.q('full').textContent,/Awaiting assessment|Review requested/);
  await app.controller.show(new URLSearchParams('research=1'));assert.match(app.q('full').textContent,/Technical review \(retired\).*Retired request type/);assert.doesNotMatch(app.q('full').textContent,/Awaiting assessment/);
  byText(app.q('full'),record.title).click();await flush();assert.match(app.q('full').textContent,/Old mathematical question.*Retired request type.*Frozen historical claim/);assert.equal(byText(app.q('full'),'Record assessment'),undefined);assert.ok(byText(app.q('full'),'Download review packet'));
 });
 await test('library distinguishes sharing approval from source checks and retired technical history',async()=>{
  for(const [publication,kind,status,expected] of [
   ['private','technical_review','submitted','Private'],
   ['submitted','technical_review','submitted','Publish request pending'],
   ['private','source_check','submitted','Source check requested'],
   ['private','source_check','recommend','Private']
  ]){
   const stored={...revision(1,'A research note',publication),requests:[{id:'request-one',kind,status}]};
   const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note({...stored,revisions:[stored]})],next_after:null}:undefined});
   const badges=app.q('list').querySelector('.nb-note-status').textContent;
   assert.ok(badges.includes(expected));assert.doesNotMatch(badges,/Review requested/);
   if(kind!=='source_check'||status!=='submitted')assert.doesNotMatch(badges,/Source check requested/);
   if(publication==='private')assert.doesNotMatch(badges,/Publish request pending/);
  }
 });
 await test('a retracted source check cannot show a current source-check request badge',async()=>{
  const r={...revision(),requests:[{id:'withdrawn-check',kind:'source_check',status:'submitted',retracted:true}]};
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[note({revisions:[r]})],next_after:null}:undefined});
  assert.equal(app.q('list').querySelectorAll('.nb-request-label').length,0);assert.doesNotMatch(app.q('list').textContent,/Source check requested/);
 });
 await test('retired technical reviews preserve recorded assessments and their evidence',async()=>{
  const record={id:'retired',title:'Assessed historical request',purpose:'technical_review',retired:true,question:'Prior question',sources:[],scope:'Historical request',can_review:true,assessment:{outcome:'changes_requested',reviewer:{handle:'alice'},findings:'The cited theorem has an extra hypothesis.',limitations:'Only the cited theorem was checked.',checks:{sources:'checked',mathematics:'gaps'}}};
  const app=await boot({respond:url=>url==='/api/notebook/research-requests/retired'?{request:record}:undefined});
  await app.controller.show(new URLSearchParams('research_request=retired'));assert.match(app.q('full').textContent,/Technical review \(retired\).*Further work requested.*extra hypothesis.*Only the cited theorem/);assert.doesNotMatch(app.q('full').textContent,/Awaiting assessment|Retired request type/);assert.equal(byText(app.q('full'),'Record assessment'),undefined);
 });
 await test('research request pages show compact pending requests and hide the normal library',async()=>{
  const app=await boot({respond:url=>url.startsWith('/api/notebook/research-requests?')?{requests:[{id:'r',title:'Review a lead',purpose:'map_nomination',assessment:null}],can_review:true,next_after:null}:undefined});
  await app.controller.show(new URLSearchParams('research=1'));assert.equal(app.q('library').hidden,true);assert.equal(app.q('full').hidden,false);assert.match(app.q('full').textContent,/Review a lead.*Awaiting assessment/);
  assert.equal(byText(app.q('full'),'Review pending requests'),undefined);assert.equal(app.requests.at(-1).url,'/api/notebook/research-requests?review=false');
 });
 await test('research assessment records separate checks and the exact packet commitment',async()=>{
  let payload;const record={id:'r',title:'A literature review',purpose:'source_check',question:'Check the claim',sources:[],packet_sha256:'a'.repeat(64),assessment:null,scope:'Recommendation only',can_review:true};
  const app=await boot({reviewOnly:true,initialParams:new URLSearchParams('section=research&research_request=r'),respond:(url,body)=>{if(url==='/api/notebook/research-requests/r/assessments'){payload=body;return{request:record};}if(url==='/api/notebook/research-requests/r')return{request:record};}});
  assert.equal(app.q('dialog').open,false);assert.match(app.q('full').textContent,/Record research assessment/);
  app.root.querySelector('[name="assessment-findings"]').value='Section 3 states the claimed theorem.';app.root.querySelector('[name="assessment-limitations"]').value='Proof not checked';app.root.querySelector('[name="assessment-sources"]').value='checked';
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(payload.packet_sha256,record.packet_sha256);assert.deepEqual(JSON.parse(JSON.stringify(payload.checks)),{sources:'checked',mathematics:'not_checked',reproduction:'not_checked'});
 });
 await test('a late review-packet response cannot reveal its sources after an account change',async()=>{
  const late=deferred();const app=await boot({respond:url=>url==='/api/notebook/research-requests/r'?late.promise:undefined});
  const reading=app.controller.show(new URLSearchParams('research_request=r'));app.setAccount({id:2,handle:'bob'});
  late.resolve({request:{id:'r',title:'PRIVATE PACKET',sources:[],question:'PRIVATE CONTENT'}});await reading;assert.doesNotMatch(app.root.textContent,/PRIVATE PACKET|PRIVATE CONTENT/);
 });
 await test('a research-lead link filters the notebook and source IDs connect prose to citations',async()=>{
  const app=await boot({researchWorkspace:research,initialParams:new URLSearchParams('tag=research-lead')});
  const expected=research.notes.filter(n=>n.revisions.at(-1).tags.includes('research-lead')).length;
  assert.equal(app.q('count').textContent,`${expected} notes`);
  assert.ok(app.q('list').querySelectorAll('.nb-title').every(n=>!n.textContent.includes('ML-KEM')));
  await app.show('curated:literature-uniform-minrank',1);
  assert.ok(app.q('full').querySelectorAll('a').some(a=>a.textContent.startsWith('[uniform-minrank] Chatterjee')));
 });
 await test('request labels come from exact server records rather than authored tags and metadata',async()=>{
  const forged={...content(),tags:['visualization_nomination','approved'],extensions:{requests:[{kind:'visualization_nomination',status:'approved'}]}};
  const old={...revision(),content:forged,requests:[{id:'r',kind:'visualization_nomination',status:'recommend',url:'https://untrusted.invalid'}]};
  const current={...revision(2),content:forged,requests:[]};
  const saved=note({revision:2,revisions:[old,current]});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:saved}:url.startsWith('/api/notebook/notes?')?{notes:[saved],next_after:null}:undefined});
  // The entry identifies the trusted older proposal without relabelling the
  // currently selected draft revision as that submitted proposal.
  assert.equal(app.q('list').querySelectorAll('.nb-request-label').length,1);
  assert.equal(app.q('list').querySelector('.nb-request-label').getAttribute('title'),'Proposal attached to revision 1');
  await app.show('note-one',1);
  assert.match(app.q('full').textContent,/Proposal/);
  assert.equal(app.q('full').querySelector('.nb-requests').open,false);
  assert.match(app.q('full').querySelector('.nb-request-row').textContent,/Recommended for the requested next step/);
  byText(app.q('full'),'View request →').click();assert.equal(app.location.hash,'#notebook?research_request=r');
  await app.show('note-one',2);assert.equal(app.q('full').querySelectorAll('.nb-request-label').length,0);
 });
 await test('visualization requests show extraction requirements and keep scoring a distinct purpose',async()=>{
  let sent;const record={id:'visual-request',title:'A precise claim',purpose:'visualization_nomination',question:'Use the exact lemma in the assumptions view.',sources:[],scope:'Recommendation only',assessment:null};
  const app=await boot({respond:(url,body)=>{if(url==='/api/notebook/research-requests'&&body){sent=body;return{request:record};}if(url.endsWith('/visual-request'))return{request:record};}});
  await app.show('note-one');byText(app.q('full'),'Propose a change').click();await flush();
  const purpose=app.root.querySelector('[name="research-purpose"]');
  assert.equal(purpose.value,'visualization_nomination');
  assert.match(app.q('dialog-body').textContent,/What to include/);
  assert.ok(requestTypes.actions.find(action=>action.id==='visualization_nomination').requirements.every(text=>app.q('dialog-body').textContent.includes(text)));
  assert.equal(purpose.options.some(option=>option.value==='contribution_nomination'),false);
  purpose.value='scoring_nomination';purpose.dispatchEvent({type:'change'});await flush();
  assert.ok(requestTypes.actions.find(action=>action.id==='scoring_nomination').requirements.every(text=>app.q('dialog-body').textContent.includes(text)));
  purpose.value='visualization_nomination';purpose.dispatchEvent({type:'change'});await flush();
  assert.equal(app.requests.some(request=>request.body),false);
  app.root.querySelector('[name="research-question"]').value=record.question;
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(sent.purpose,'visualization_nomination');assert.equal(sent.sources[0].content_sha256,'hash-1');
  assert.equal(app.requests.some(request=>/publication|proposal-submissions/.test(request.url)),false);
 });
 await test('requirements must load before a request and a late response cannot reopen an old account dialog',async()=>{
  const late=deferred();const app=await boot({respond:url=>url==='/notebook/request-types.json'?late.promise:undefined});
  await app.show('note-one');byText(app.q('full'),'Propose a change').click();
  assert.equal(byText(app.q('dialog-body'),'Send for review').disabled,true);
  app.root.querySelector('[name="research-question"]').value='A target and exact claim';
  app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(app.requests.some(request=>request.body),false);
  app.setAccount(null);late.resolve(requestTypes);await flush();
  assert.equal(app.q('dialog').open,false);assert.equal(app.q('dialog-body').textContent,'');
 });
 await test('curated note request labels use authenticated exact-source metadata and do not mutate static notes',async()=>{
  const source=research.notes[0],raw=source.revisions.at(-1),id='curated:'+source.id;
  const app=await boot({researchWorkspace:research,respond:url=>{
   if(url.startsWith('/api/notebook/research-requests?source_note_id=')){
    const query=new URLSearchParams(url.split('?')[1]);return{requests:[{id:'curated-request',purpose:'visualization_nomination',primary_source:{note_id:query.get('source_note_id'),revision:Number(query.get('source_revision')),content_sha256:query.get('source_sha256')},assessment:{outcome:'recommend',effective:false,test_only_self_review:true}}],next_after:null};
   }
  }});
  await app.show(id,raw.revision);assert.match(app.q('full').textContent,/Proposal/);
  assert.match(app.q('full').querySelector('.nb-request-row').textContent,/test review \(inactive\)/);
  assert.equal(raw.requests,undefined);
  const call=app.requests.find(request=>request.url.startsWith('/api/notebook/research-requests?source_note_id='));assert.equal(new URLSearchParams(call.url.split('?')[1]).get('source_note_id'),id);
 });
 await test('agent handoffs identify the same-origin contract without uploading or fetching it on note load',async()=>{
  const app=await boot();const copied=[];app.sandbox.navigator.clipboard={writeText:async value=>copied.push(value)};
  app.q('files').click();byText(app.q('dialog-body'),'Copy prompt').click();await flush();
  assert.match(copied[0],/^Before working, read the Crucible agent contract at http:\/\/127\.0\.0\.1:8000\/api\/agent\/pipeline/);
  assert.match(copied[0],/Do not request passwords or tokens/);
  assert.match(copied[0],/New notes and revisions start private.*Publication requires reviewer approval of an exact saved revision/);
  assert.doesNotMatch(app.q('dialog-body').textContent,/Imported work stays private|Import adds private notes or revisions/);
  assert.equal(app.requests.some(r=>r.url==='/api/agent/pipeline'||r.body),false);
  const blobs=[];app.sandbox.URL=class extends URL{static createObjectURL(blob){blobs.push(blob);return'blob:fixture';}static revokeObjectURL(){}};
  const create=app.document.createElement;app.document.createElement=tag=>{const element=create(tag);element.remove=()=>{};return element;};
  await app.show('note-one',1);byText(app.q('full'),'Download agent research task').click();
  const task=JSON.parse(await blobs[0].text());
  assert.equal(task.pipeline.url,'http://127.0.0.1:8000/api/agent/pipeline');
  assert.equal(task.request_guidance_url,'http://127.0.0.1:8000/api/agent/guidance/request-types.json');
  assert.equal(task.source_content.body,'Argument as text');
  assert.equal(task.return_format.format,'crucible-theory-local-v1');
  assert.match(task.source_reference.url,/note=note-one&revision=1&sha256=hash-1/);
  assert.equal(app.requests.some(r=>r.body),false);
 });
 await test('a delayed contract download cannot appear after switching account',async()=>{
  const late=deferred();const app=await boot({respond:url=>url==='/api/agent/pipeline'?late.promise:undefined});
  let downloads=0;app.sandbox.URL=class extends URL{static createObjectURL(){downloads++;return'blob:fixture';}};
  app.q('files').click();byText(app.q('dialog-body'),'Download agent contract').click();
  app.setAccount({id:2,handle:'bob'});late.resolve({format:'crucible-agent-pipeline-v1'});await flush();
  assert.equal(downloads,0);assert.equal(app.q('dialog').open,false);
 });
 await test('collapsed proposal badges use trusted records and not authored tags, kinds or review requests',async()=>{
  const submitted=proposalNote(), forgedContent={...content('Only a proposed label'),kind:'Proposal',tags:['proposal','scheme_admission'],extensions:{proposal_revision:{revision:1,requests:[proposalRecord()]}}};
  const forged=note({id:'forged',content:forgedContent});
  const reviewed=note({id:'review-only',content:content('A source check'),proposal_revision:{revision:1,content_sha256:'hash-1',requests:[proposalRecord('source-check','source_check')]}});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,[submitted,forged,reviewed]):undefined});
  const entries=app.q('list').querySelectorAll('.nb-entry');
  assert.equal(entries[0].querySelector('.nb-title').getAttribute('aria-expanded'),'false');
  assert.equal(entries[0].querySelector('.nb-request-label').textContent,'Proposal');
  assert.equal(entries[1].querySelector('.nb-request-label'),null);
  assert.equal(entries[2].querySelectorAll('.nb-request-label').some(node=>node.textContent==='Proposal'),false);
  await filterNotes(app,'proposals');
  assert.ok(app.requests.some(request=>request.url.startsWith('/api/notebook/notes?')&&listQuery(request.url).get('proposals_only')==='true'));
  assert.equal(app.q('count').textContent,'1 note');
  assert.match(app.q('list').textContent,/A submitted proposal/);
  assert.doesNotMatch(app.q('list').textContent,/Only a proposed label|A source check/);
  assert.equal(app.requests.some(request=>request.body),false);
 });
 await test('Proposals uses its own server cursor and leaving the filter resets to the first all-notes page',async()=>{
  const first=proposalNote('First proposal',{id:'first-proposal'}),second=proposalNote('Next proposal',{id:'second-proposal'});
  const app=await boot({respond:url=>{
   if(!url.startsWith('/api/notebook/notes?'))return;
   if(listQuery(url).get('proposals_only')==='true')return listQuery(url).get('after')==='proposal-cursor'?{notes:[second],next_after:null}:{notes:[first],next_after:'proposal-cursor'};
   return{notes:[note()],next_after:'ordinary-cursor'};
  }});
  assert.doesNotMatch(app.q('list').textContent,/First proposal/);await filterNotes(app,'proposals');
  assert.match(app.q('list').textContent,/First proposal/);assert.doesNotMatch(app.q('list').textContent,/Private claim/);
  app.q('more').click();await flush();
  const query=listQuery(app.requests.at(-1).url);assert.equal(query.get('proposals_only'),'true');assert.equal(query.get('after'),'proposal-cursor');
  assert.match(app.q('list').textContent,/Next proposal/);assert.doesNotMatch(app.q('list').textContent,/First proposal/);assert.equal(app.q('more').disabled,true);
  await filterNotes(app,'all');const all=listQuery(app.requests.at(-1).url);assert.equal(all.get('scope'),'all');assert.equal(all.has('proposals_only'),false);assert.equal(all.has('after'),false);
  assert.equal(app.q('page').textContent,'Page 1');assert.match(app.q('list').textContent,/Private claim/);
 });
 await test('an older submitted proposal opens its exact revision and hash instead of the latest draft',async()=>{
  const old={...revision(1,'Submitted mathematical claim'),content_sha256:'b'.repeat(64),requests:[proposalRecord()]};
  const latest={...revision(2,'New private draft'),content_sha256:'c'.repeat(64),requests:[]};
  const saved=note({...latest,revisions:[old,latest],proposal_revision:{revision:1,content_sha256:old.content_sha256,requests:old.requests}});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?{notes:[saved],next_after:null}:url.startsWith('/api/notebook/notes/note-one')?{note:saved}:undefined});
  assert.match(app.q('list').textContent,/New private draft/);assert.equal(app.q('list').querySelector('.nb-request-label').textContent,'Proposal');
  await filterNotes(app,'proposals');
  app.q('list').querySelector('.nb-title').click();byText(app.q('list'),'Read full note →').click();await app.whenNavigated();
  const query=new URLSearchParams(app.location.hash.split('?')[1]);
  assert.equal(query.get('note'),'note-one');assert.equal(query.get('revision'),'1');assert.equal(query.get('sha256'),old.content_sha256);
  assert.match(app.q('full').textContent,/Submitted mathematical claim/);assert.doesNotMatch(app.q('full').textContent,/New private draft/);
  assert.ok(app.requests.some(request=>request.url==='/api/notebook/notes/note-one?revision=1'));
 });
 await test('server-indexed curated proposals open historical revisions without preloading the curated catalogue',async()=>{
  const digestStarted=deferred(),releaseDigest=deferred();
  const source=curatedFixture('curated-first','Curated older proposal',2),summary=curatedSummary(source);
  summary.proposal_revision={revision:1,content_sha256:curatedHash(source.revisions[0]),requests:[proposalRecord('curated-proposal')]};
  const app=await boot({digest:async(...args)=>{digestStarted.resolve();await releaseDigest.promise;return require('node:crypto').webcrypto.subtle.digest(...args);},researchWorkspace:{...emptyResearch,notes:[source]},respond:url=>{
   if(url.startsWith('/api/notebook/notes?'))return summaryPage(url,[summary]);
   if(url.startsWith('/api/notebook/research-requests?source_note_id='))return{requests:[curatedRequest(source,1)],next_after:null};
  }});
  assert.equal(app.q('list').querySelector('.nb-request-label').textContent,'Proposal');assert.equal(app.requests.some(r=>r.url==='/map/notes.json'||r.url.includes('curated_proposals=')),false);
  await filterNotes(app,'proposals');app.q('list').querySelector('.nb-title').click();byText(app.q('list'),'Read full note →').click();
  // WebCrypto completes on a worker, not after a fixed number of event-loop turns.
  // Hold it explicitly to verify the reader waits for the exact-revision digest.
  await digestStarted.promise;
  assert.equal(app.q('full').querySelector('.nb-note-status'),null);
  assert.match(app.q('full').textContent,/Loading note/);
  releaseDigest.resolve();await app.whenNavigated();
  const query=new URLSearchParams(app.location.hash.split('?')[1]);assert.equal(query.get('note'),'curated:curated-first');assert.equal(query.get('revision'),'1');assert.equal(query.get('sha256'),curatedHash(source.revisions[0]));
  assert.match(app.q('full').querySelector('.nb-note-status').textContent,/Proposal/);assert.match(app.q('full').textContent,/Curated older proposal 1/);
  assert.equal(app.requests.filter(r=>r.url==='/map/notes.json').length,1);assert.equal(source.revisions[0].requests,undefined);
 });
 await test('a late unified page cannot reveal another accounts curated proposal status',async()=>{
  const source=curatedFixture('shared-source','Shared research source'),late=deferred();let delaying=false;
  const summary=curatedSummary(source),privateSummary={...summary,proposal_revision:{revision:1,content_sha256:summary.content_sha256,requests:[proposalRecord('BOB-PRIVATE')]}};
  const app=await boot({researchWorkspace:{...emptyResearch,notes:[source]},respond:url=>url.startsWith('/api/notebook/notes?')&&delaying?late.promise:undefined});
  app.setAccount({id:2,handle:'bob'});delaying=true;const pending=app.show();await flush();app.setAccount({id:3,handle:'carol'});
  late.resolve({notes:[privateSummary],next_after:null});await pending;assert.equal(app.q('list').textContent,'');
  delaying=false;await app.show();assert.match(app.q('list').textContent,/Shared research source/);assert.equal(app.q('list').querySelectorAll('.nb-request-label').length,0);
  await filterNotes(app,'proposals');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,0);
 });
 await test('switching back from Proposals ignores its late list response',async()=>{
  const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?url.includes('proposals_only=true')?late.promise:{notes:[note()],next_after:null}:undefined});
  const select=app.root.querySelector('[name="visibility"]');select.value='proposals';select.dispatchEvent({type:'change'});await flush();
  await filterNotes(app,'all');late.resolve({notes:[proposalNote('Late proposal')],next_after:null});await flush();
  assert.equal(select.value,'all');assert.match(app.q('list').textContent,/Private claim/);assert.doesNotMatch(app.q('list').textContent,/Late proposal/);
 });
 await test('global search finds an unloaded note and debounces rapid input into one bounded request',async()=>{
  const records=Array.from({length:75},(_,i)=>note({id:'global-'+i,content:content(i===62?'Rare theorem outside the first page':'Ordinary summary '+i)}));
  records[0].revisions[0].content.body='Secret full-body text must not be searched by this summary endpoint';
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,records):undefined});
  assert.doesNotMatch(app.q('list').textContent,/Rare theorem/);const before=app.requests.length;
  for(const term of ['Ra','Rare','Rare theorem']){app.q('search').value=term;app.q('search').dispatchEvent({type:'input'});}
  assert.equal(app.requests.length,before);await app.advanceTimers(250);assert.equal(app.requests.length,before+1);
  const query=listQuery(app.requests.at(-1).url);assert.equal(query.get('q'),'Rare theorem');assert.equal(query.get('limit'),'25');assert.equal(query.has('after'),false);
  assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);assert.match(app.q('list').textContent,/Rare theorem/);assert.equal(app.q('page').textContent,'Page 1');
  assert.equal(app.q('search').placeholder,'Search titles, tags or #number…');
  await searchNotes(app,'Secret full-body');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,0);
  assert.equal(app.requests.some(r=>r.url==='/map/notes.json'),false);
 });
 await test('proposal filters preserve tags and text search in the global server query',async()=>{
  const algebra=proposalNote('Algebra proposal',{id:'algebra-proposal'});
  const geometryRevision={...revision(1,'Geometry proposal'),content:{...content('Geometry proposal'),tags:['geometry']},content_sha256:'d'.repeat(64)};
  const geometry=note({id:'geometry-proposal',...geometryRevision,revisions:[geometryRevision],proposal_revision:{revision:1,content_sha256:geometryRevision.content_sha256,requests:[proposalRecord('geometry')]}});
  const app=await boot({initialParams:new URLSearchParams('tag=algebra'),respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,[algebra,geometry]):undefined});
  await filterNotes(app,'proposals');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);assert.match(app.q('list').textContent,/Algebra proposal/);
  await searchNotes(app,'unmatched text');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,0);
  await searchNotes(app,'Algebra');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);
  const query=listQuery(app.requests.at(-1).url);assert.equal(query.get('proposals_only'),'true');assert.equal(query.get('tag'),'algebra');assert.equal(query.get('q'),'Algebra');
 });
 await test('late global search results cannot replace a newer search',async()=>{
  const late=deferred();const app=await boot({respond:url=>{
   if(!url.startsWith('/api/notebook/notes?'))return;
   const term=listQuery(url).get('q');return term==='old'?late.promise:{notes:[note({content:content(term==='new'?'New matching result':'Initial result')})],next_after:null};
  }});
  app.q('search').value='old';app.q('search').dispatchEvent({type:'input'});const pending=app.advanceTimers(250);await flush();
  await searchNotes(app,'new');late.resolve({notes:[note({content:content('Old late result')})],next_after:null});await pending;
  assert.match(app.q('list').textContent,/New matching result/);assert.doesNotMatch(app.q('list').textContent,/Old late result/);
 });
 await test('navigation and account changes cancel pending debounce reads and ignore in-flight search results',async()=>{
  const late=deferred();const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')&&listQuery(url).get('q')==='pending'?late.promise:undefined});
  const before=app.requests.length;app.q('search').value='cancelled';app.q('search').dispatchEvent({type:'input'});app.controller.hide();await app.advanceTimers(250);assert.equal(app.requests.length,before);
  await app.show();app.q('search').value='pending';app.q('search').dispatchEvent({type:'input'});const pending=app.advanceTimers(250);await flush();app.setAccount({id:2,handle:'bob'});
  late.resolve({notes:[note({content:content('PRIVATE SEARCH RESULT')})],next_after:null});await pending;assert.doesNotMatch(app.root.textContent,/PRIVATE SEARCH RESULT/);
  await app.show();app.q('search').value='cancelled again';app.q('search').dispatchEvent({type:'input'});const count=app.requests.length;app.setAccount(null);await app.advanceTimers(250);assert.equal(app.requests.length,count);
 });
 await test('account switches clear private search text and tag chips before another page can load',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({initialParams:new URLSearchParams('tag=alice-private-topic'),respond:url=>url.startsWith('/api/notebook/notes?')?(delay?late.promise:{notes:[],next_after:null}):undefined});
  await searchNotes(app,'Alice private research question');
  assert.equal(app.q('search').value,'Alice private research question');assert.match(app.q('filter').textContent,/alice-private-topic/);
  delay=true;app.setAccount({id:2,handle:'bob'});
  // Clear synchronously, before either the new account's request or its response.
  assert.equal(app.q('search').value,'');assert.equal(app.q('filter').textContent,'');
  const loading=app.show();await flush();
  assert.equal(app.q('search').value,'');assert.equal(app.q('filter').textContent,'');
  delay=false;late.resolve({notes:[],next_after:null});await loading;
  await app.controller.show(new URLSearchParams('tag=bob-private-topic'));await searchNotes(app,'Bob private research question');
  assert.match(app.q('filter').textContent,/bob-private-topic/);app.setAccount(null);
  assert.equal(app.q('search').value,'');assert.equal(app.q('filter').textContent,'');
  await app.show();assert.equal(app.q('search').value,'');assert.equal(app.q('filter').textContent,'');
 });
 await test('a research proposer retracts the exact packet and keeps its old assessment as history',async()=>{
  let withdrawn=false;const record=()=>({id:'r',title:'Correct a graph',purpose:'visualization_nomination',question:'Use the proposed curve',sources:[],packet_sha256:'a'.repeat(64),scope:'Recommendation only',can_review:true,can_retract:!withdrawn,retracted:withdrawn,
   assessment:{outcome:'recommend',reviewer:{handle:'reviewer'},findings:'Earlier assessment',limitations:'Historical limits',checks:{}},...(withdrawn?{retraction:{reason:'The source does not support this curve.'}}:{})});
  const app=await boot({respond:(url,body)=>{if(url==='/api/notebook/research-requests/r/retraction'){withdrawn=true;return{request:record()};}if(url==='/api/notebook/research-requests/r')return{request:record()};}});
  await app.controller.show(new URLSearchParams('research_request=r'));assert.equal(app.requests.some(r=>r.body),false);
  const host=app.q('full').querySelector('.proposal-retraction');host.querySelector('textarea').value='The source does not support this curve.';host.querySelector('form').dispatchEvent({type:'submit'});await flush();
  const sent=app.requests.find(r=>r.body);assert.equal(sent.url,'/api/notebook/research-requests/r/retraction');assert.equal(sent.body.packet_sha256,'a'.repeat(64));
  assert.equal('expected_revision' in sent.body,false);assert.match(app.q('full').textContent,/Retracted.*Earlier assessment/);
  assert.equal(byText(app.q('full'),'Record assessment'),undefined);assert.equal(app.q('full').querySelector('textarea'),null);
 });
 await test('only trusted compact retraction flags produce the notebook withdrawn badge',async()=>{
  const withdrawn=proposalNote('Withdrawn proposal'),ordinary=note({id:'forged',content:{...content('Authored tags only'),tags:['proposal','retracted']}});
  withdrawn.proposal_revision.requests[0].retracted=true;
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,[withdrawn,ordinary]):undefined});
  const entries=app.q('list').querySelectorAll('.nb-entry');assert.equal(entries[0].querySelectorAll('.nb-retracted').length,1);
  assert.equal(entries[1].querySelectorAll('.nb-retracted').length,0);assert.equal(app.requests.some(r=>r.body),false);
 });
 await test('a late research retraction cannot reopen private detail after signout',async()=>{
  const pending=deferred(),record={id:'r',title:'PRIVATE proposal',purpose:'procedure',question:'PRIVATE change',sources:[],packet_sha256:'a'.repeat(64),assessment:null,scope:'Research proposal',can_retract:true};
  const app=await boot({respond:url=>url==='/api/notebook/research-requests/r'?{request:record}:url==='/api/notebook/research-requests/r/retraction'?pending.promise:undefined});
  await app.controller.show(new URLSearchParams('research_request=r'));const host=app.q('full').querySelector('.proposal-retraction');host.querySelector('textarea').value='PRIVATE reason';host.querySelector('form').dispatchEvent({type:'submit'});await flush();
  const reads=app.requests.filter(r=>r.url==='/api/notebook/research-requests/r').length;app.setAccount(null);
  pending.resolve({request:{...record,retracted:true}});await flush();assert.equal(app.requests.filter(r=>r.url==='/api/notebook/research-requests/r').length,reads);
  assert.doesNotMatch(app.root.textContent,/PRIVATE proposal|PRIVATE change|PRIVATE reason/);
 });
 await test('cold Timeline preserves one truthful card frame through count, curve, empty and error states',async()=>{
  for(const outcome of ['curve','empty','error']){
   const late=deferred();const app=await boot({respond:url=>url==='/api/notebook/timeline'?late.promise:undefined});
   const pending=app.controller.show(new URLSearchParams('view=timeline'));
   const card=app.q('timeline').querySelector('.nb-timeline-card'),plot=card.querySelector('.nb-timeline-plot'),heading=card.querySelector('.nb-timeline-heading'),footer=card.querySelector('.nb-timeline-footnote');
   assert.ok(plot.querySelector('.nb-timeline-frame'));assert.equal(card.querySelector('.nb-timeline-total').textContent,'');
   assert.equal(plot.querySelector('svg'),null);assert.equal(plot.querySelector('.nb-timeline-line'),null);assert.equal(plot.querySelectorAll('text').length,0);
   assert.equal(card.getAttribute('aria-busy'),'true');assert.match(plot.querySelector('[role="status"]').textContent,/Loading/);
   app.q('timeline-view').focus();
   if(outcome==='error')late.reject(Object.assign(Error('Unavailable'),{status:503}));
   else late.resolve(outcome==='empty'?timeline({total:0,points:[]}):timeline());
   await pending;
   assert.equal(app.q('timeline').querySelector('.nb-timeline-card')===card,true);
   assert.equal(card.querySelector('.nb-timeline-plot')===plot,true);assert.equal(card.querySelector('.nb-timeline-heading')===heading,true);assert.equal(card.querySelector('.nb-timeline-footnote')===footer,true);
   assert.equal(app.document.activeElement,app.q('timeline-view'));assert.equal(card.getAttribute('aria-busy'),null);
   assert.equal(Boolean(plot.querySelector('.nb-timeline-line')),outcome==='curve');
   assert.equal(card.querySelector('.nb-timeline-total').textContent,outcome==='curve'?'3 notes':outcome==='empty'?'0 notes':'');
  }
 });
 await test('an expired same-route Timeline cache cannot leave the placeholder after unchanged data returns',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(delay?late.promise:timeline()):undefined});
  const card=app.q('timeline').querySelector('.nb-timeline-card');
  vm.runInContext('Date.now = () => '+(Date.now()+300001),app.sandbox);delay=true;
  const pending=app.controller.show(new URLSearchParams('view=timeline'));
  assert.ok(app.q('timeline').querySelector('.nb-timeline-frame'));assert.equal(app.q('timeline').querySelector('.nb-timeline-line'),null);
  late.resolve(timeline());await pending;
  assert.equal(app.q('timeline').querySelector('.nb-timeline-card')===card,true);assert.ok(card.querySelector('.nb-timeline-line'));assert.equal(card.getAttribute('aria-busy'),null);
 });
 await test('notebook tab changes reserve only current viewport space and clear it on access or route changes',async()=>{
  let session='one';const app=await boot({session:()=>session,respond:url=>url==='/api/notebook/timeline'?timeline():undefined});
  app.root.hidden=false;app.window.scrollY=180;app.window.innerHeight=900;
  app.root.getBoundingClientRect=()=>({top:-40,height:9000});
  app.q('timeline-view').click();await flush();
  assert.equal(app.root.style.minHeight,'940px','Reserve the viewport, not the previous9000px page');
  assert.equal(app.root.classList.contains('nb-view-scroll-held'),true);
  assert.doesNotMatch(app.root.textContent,/Cumulative notes currently visible|Each note counts once|Your notes and the research behind the map/);
  session='two';app.setAccount(user);assert.equal(app.root.style.minHeight,'');assert.equal(app.root.classList.contains('nb-view-scroll-held'),false);
  await app.show();app.q('timeline-view').click();await flush();assert.equal(app.root.style.minHeight,'940px');
  app.controller.hide();assert.equal(app.root.style.minHeight,'');
  await app.show();app.window.scrollY=0;app.q('timeline-view').click();await flush();assert.equal(app.root.style.minHeight,'');
 });
 await test('Timeline intent is lazy and a hover-focus-click shares one pending aggregate read',async()=>{
  const late=deferred();const app=await boot({respond:url=>url==='/api/notebook/timeline'?late.promise:undefined});
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/timeline').length,0);
  app.q('timeline-view').dispatchEvent({type:'pointerenter'});app.q('timeline-view').dispatchEvent({type:'focus'});
  const pending=app.controller.show(new URLSearchParams('view=timeline'));
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/timeline').length,1);assert.equal(app.q('timeline').querySelector('svg'),null);
  late.resolve(timeline());await pending;assert.ok(app.q('timeline').querySelector('.nb-timeline-line'));
  await app.show();const refreshed=app.controller.show(new URLSearchParams('view=timeline'));await refreshed;
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/timeline').length,2,'A consumed intent does not replace later fresh validation');
 });
 await test('a completed recent Timeline intent paints immediately without a duplicate read',async()=>{
  const app=await boot({respond:url=>url==='/api/notebook/timeline'?timeline():undefined});
  app.q('timeline-view').dispatchEvent({type:'pointerenter'});await flush();
  assert.equal(app.q('timeline').querySelector('svg'),null,'Intent cannot paint a hidden chart');
  const pending=app.controller.show(new URLSearchParams('view=timeline'));assert.ok(app.q('timeline').querySelector('.nb-timeline-line'));await pending;
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/timeline').length,1);
 });
 await test('an older consumed or abandoned Timeline intent cannot replace a newer cached aggregate',async()=>{
  for(const mode of ['consumed','hidden']){
   const old=deferred(),refresh=deferred();let reads=0;
   const newer=timeline({total:9,points:[{date:'2026-10-18',created:9,total:9}]});
   const app=await boot({respond:url=>url==='/api/notebook/timeline'?(++reads===1?old.promise:reads===2?newer:refresh.promise):undefined});
   app.q('timeline-view').dispatchEvent({type:'pointerenter'});
   const first=mode==='consumed'?app.controller.show(new URLSearchParams('view=timeline')):Promise.resolve();
   if(mode==='hidden')app.controller.hide();
   await app.show();await app.controller.show(new URLSearchParams('view=timeline'));
   assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'9 notes');
   old.resolve(timeline());await first;await flush();
   assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'9 notes','Late intent cannot change the selected view');
   await app.show();const returning=app.controller.show(new URLSearchParams('view=timeline'));
   assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'9 notes','Return must retain the newer cached aggregate');
   refresh.resolve(newer);await returning;assert.equal(reads,3);
  }
 });
 await test('Timeline intent expires after ten seconds and cannot be reused after session rotation or mutation',async()=>{
  for(const invalidate of ['age','session','mutation']){
   const old=deferred(),fresh=deferred();let count=0,session='one';
   const app=await boot({session:()=>session,respond:url=>url==='/api/notebook/timeline'?(++count===1?old.promise:fresh.promise):undefined});
   vm.runInContext('Date.now = () => 1000',app.sandbox);
   app.q('timeline-view').dispatchEvent({type:'pointerenter'});
   if(invalidate==='age')vm.runInContext('Date.now = () => 11000',app.sandbox);
   if(invalidate==='session'){session='two';app.setAccount(user);}
   if(invalidate==='mutation')app.controller.invalidateReads();
   const pending=app.controller.show(new URLSearchParams('view=timeline'));assert.equal(count,2);
   old.resolve(timeline({total:987,points:[{date:'2026-10-18',created:987,total:987}]}));await flush();assert.doesNotMatch(app.q('timeline').textContent,/987/);
   fresh.resolve(timeline());await pending;assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'3 notes');
  }
 });
 await test('Timeline intent obeys save-data, hidden-page, inactive and review-only boundaries',async()=>{
  for(const blocked of ['save-data','hidden','inactive','review']){
   const app=await boot({reviewOnly:blocked==='review',respond:url=>url==='/api/notebook/timeline'?timeline():undefined});
   if(blocked==='save-data')app.sandbox.navigator.connection={saveData:true};
   if(blocked==='hidden')app.document.visibilityState='hidden';
   if(blocked==='inactive')app.controller.hide();
   const before=app.requests.length;
   if(blocked==='review')assert.equal(app.q('timeline-view'),null);
   else {app.q('timeline-view').dispatchEvent({type:'pointerenter'});app.q('timeline-view').dispatchEvent({type:'focus'});}
   await flush();assert.equal(app.requests.length,before);
  }
 });
 await test('forbidden consumed Timeline intent clears its frame and can be retried truthfully',async()=>{
  const late=deferred();let count=0;const app=await boot({respond:url=>url==='/api/notebook/timeline'?(++count===1?late.promise:timeline()):undefined});
  app.q('timeline-view').dispatchEvent({type:'pointerenter'});const pending=app.controller.show(new URLSearchParams('view=timeline'));
  late.reject(Object.assign(Error('Denied'),{status:403}));await pending;
  assert.equal(app.q('timeline').getAttribute('aria-busy'),null);assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'');assert.equal(app.q('timeline').querySelector('svg'),null);
  byText(app.q('timeline'),'Try again').click();await flush();assert.equal(count,2);assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'3 notes');
 });
 await test('Timeline is lazy, stays inside the notebook and requests only aggregate counts',async()=>{
  const app=await boot({respond:url=>url==='/api/notebook/timeline'?timeline():undefined});
  assert.equal(app.requests.some(row=>row.url==='/api/notebook/timeline'),false);assert.equal(app.q('timeline').hidden,true);
  const before=app.requests.length;app.q('timeline-view').click();await flush();
  assert.equal(app.location.hash,'#notebook?view=timeline');assert.equal(app.q('timeline').hidden,false);
  assert.equal(app.q('library').hidden,true);assert.equal(app.q('map').hidden,true);
  assert.equal(app.q('timeline-view').getAttribute('aria-current'),'page');assert.equal(app.q('notes-view').getAttribute('aria-current'),null);
  assert.deepEqual(app.requests.slice(before).map(row=>row.url),['/api/notebook/timeline']);assert.ok(app.requests.every(row=>row.body===undefined));
  assert.ok(app.q('timeline').querySelector('svg'));assert.match(app.q('timeline').textContent,/3/);
  assert.equal(app.requests.some(row=>row.url==='/map/notes.json'),false);
 });
 await test('Timeline deep links fetch no list or bodies and re-entry refreshes the visible corpus',async()=>{
  let visits=0;const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(visits++,timeline()):undefined});
  assert.deepEqual(app.requests.map(row=>row.url),['/api/notebook/timeline']);assert.equal(visits,1);
  app.q('notes-view').click();await flush();assert.equal(app.q('timeline').hidden,true);assert.equal(app.q('timeline').querySelector('svg'),null);
  await filterNotes(app,'mine');await searchNotes(app,'my research');app.q('timeline-view').click();await flush();
  assert.equal(visits,2);assert.equal(app.requests.at(-1).url,'/api/notebook/timeline');
  app.q('notes-view').click();await flush();assert.equal(app.q('search').value,'my research');
 });
 await test('a warm Timeline paints before its fresh read and unchanged aggregates preserve DOM and focus',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(delay?late.promise:timeline()):undefined});
  await app.show();delay=true;const pending=app.controller.show(new URLSearchParams('view=timeline'));
  const svg=app.q('timeline').querySelector('svg');assert.ok(svg);assert.equal(app.q('timeline').querySelector('.nb-reading'),null);assert.equal(app.q('timeline').getAttribute('aria-busy'),'true');
  app.q('timeline-view').focus();late.resolve(timeline({server_time:99999999}));await pending;
  assert.equal(app.q('timeline').querySelector('svg'),svg);assert.equal(app.document.activeElement,app.q('timeline-view'));assert.equal(app.q('timeline').getAttribute('aria-busy'),null);
  assert.equal(app.requests.filter(row=>row.url==='/api/notebook/timeline').length,2);assert.equal(app.requests.some(row=>row.url==='/map/notes.json'),false);
 });
 await test('fresh Timeline changes replace cached counts only after the authoritative response',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(delay?late.promise:timeline()):undefined});
  await app.show();delay=true;const pending=app.controller.show(new URLSearchParams('view=timeline'));
  assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'3 notes');
  late.resolve(timeline({total:4,points:[{date:'2026-10-18',created:4,total:4}]}));await pending;
  assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'4 notes');
 });
 await test('cached Timeline survives a temporary read failure with an explicit refresh error',async()=>{
  const late=deferred();let delay=false;
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(delay?late.promise:timeline()):undefined});
  await app.show();delay=true;const pending=app.controller.show(new URLSearchParams('view=timeline'));const svg=app.q('timeline').querySelector('svg');
  late.reject(Object.assign(Error('Temporary failure'),{status:503}));await pending;
  assert.equal(app.q('timeline').querySelector('svg'),svg);assert.match(app.q('notice').textContent,/could not be refreshed/);assert.equal(app.q('timeline').getAttribute('aria-busy'),null);
 });
 await test('forbidden Timeline refreshes remove cached counts and cannot restore them on re-entry',async()=>{
  const first=deferred(),second=deferred();let calls=0;
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(++calls===1?timeline():calls===2?first.promise:second.promise):undefined});
  await app.show();const denied=app.controller.show(new URLSearchParams('view=timeline'));assert.ok(app.q('timeline').querySelector('svg'));
  first.reject(Object.assign(Error('Permission revoked'),{status:403}));await denied;
  assert.equal(app.q('timeline').querySelector('svg'),null);assert.equal(app.q('timeline').getAttribute('aria-busy'),null);
  await app.show();const fresh=app.controller.show(new URLSearchParams('view=timeline'));assert.equal(app.q('timeline').querySelector('svg'),null);assert.ok(app.q('timeline').querySelector('.nb-reading'));
  second.resolve(timeline());await fresh;
 });
 await test('Timeline shares mutation and session invalidation and rejects stale in-flight aggregates',async()=>{
  const old=deferred(),fresh=deferred();let calls=0,session='one';
  const app=await boot({session:()=>session,initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(++calls===1?timeline():calls===2?old.promise:fresh.promise):undefined});
  await app.show();const pending=app.controller.show(new URLSearchParams('view=timeline'));assert.ok(app.q('timeline').querySelector('svg'));
  app.controller.invalidateReads();await app.show();const next=app.controller.show(new URLSearchParams('view=timeline'));assert.equal(app.q('timeline').querySelector('svg'),null);
  old.resolve(timeline({total:999,points:[{date:'2026-10-18',created:999,total:999}]}));await pending;assert.doesNotMatch(app.q('timeline').textContent,/999/);
  session='two';app.setAccount(user);fresh.resolve(timeline());await next;
  assert.equal(app.q('timeline').textContent,'');assert.equal(app.q('timeline').getAttribute('aria-busy'),null);
 });
 await test('an empty timeline has an explicit empty state without a fabricated curve',async()=>{
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?timeline({total:0,points:[]}):undefined});
  assert.match(app.q('timeline').textContent,/No notes|No dated notes|No visible notes/i);assert.equal(app.q('timeline').querySelector('svg'),null);
  assert.equal(app.requests.length,1);
 });
 await test('timeline failure has a retry and cannot be mistaken for an empty collection',async()=>{
  let attempts=0;const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>{if(url==='/api/notebook/timeline'){if(++attempts===1)throw Error('Timeline temporarily unavailable');return timeline();}}});
  assert.match(app.q('timeline').textContent,/timeline could not be loaded/);assert.equal(app.q('timeline').querySelector('svg'),null);
  assert.doesNotMatch(app.q('timeline').textContent,/No notes|No visible notes/i);
  const retry=app.q('timeline').querySelectorAll('button').find(button=>/try again|retry/i.test(button.textContent));assert.ok(retry);retry.click();await flush();
  assert.equal(attempts,2);assert.ok(app.q('timeline').querySelector('svg'));assert.doesNotMatch(app.q('timeline').textContent,/temporarily unavailable/);
 });
 await test('late timeline results cannot replace a newer route or repopulate a hidden panel',async()=>{
  for(const fails of [false,true]){
   const late=deferred();const app=await boot({respond:url=>url==='/api/notebook/timeline'?late.promise:undefined});
   const loading=app.controller.show(new URLSearchParams('view=timeline'));await flush();await app.show();
   if(fails)late.reject(Error('PRIVATE old timeline error'));else late.resolve(timeline({total:987,points:[{date:'2026-10-18',created:987,total:987}]}));
   await loading;assert.equal(app.q('library').hidden,false);assert.equal(app.q('timeline').hidden,true);assert.equal(app.q('timeline').querySelector('svg'),null);
   assert.doesNotMatch(app.q('timeline').textContent,/987|PRIVATE old/);
  }
 });
 await test('account changes synchronously clear timeline counts and ignore late old-account responses',async()=>{
  const late=deferred();let delay=false;const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?(delay?late.promise:timeline()):undefined});
  assert.ok(app.q('timeline').querySelector('svg'));app.setAccount({id:2,handle:'bob'});assert.equal(app.q('timeline').textContent,'');
  delay=true;const loading=app.controller.show(new URLSearchParams('view=timeline'));await flush();app.setAccount(null);
  late.resolve(timeline({total:987,points:[{date:'2026-10-18',created:987,total:987}]}));await loading;
  assert.equal(app.q('timeline').textContent,'');assert.equal(app.q('timeline').querySelector('svg'),null);
 });
 await test('timeline is an accessible integer step plot with exact cumulative event labels',async()=>{
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?timeline():undefined});
  const svg=app.q('timeline').querySelector('svg'),line=svg.querySelector('.nb-timeline-line').getAttribute('d');
  assert.equal(svg.getAttribute('role'),'img');assert.match(svg.getAttribute('aria-label'),/3 dated notes.*revisions do not add notes/);
  assert.match(line,/^M /);assert.deepEqual(line.match(/[A-Za-z]/g),['M','H','V','H','V','H']);assert.doesNotMatch(line,/NaN|Infinity/);
  const ticks=svg.querySelectorAll('text').filter(text=>text.getAttribute('text-anchor')==='end'&&Number(text.getAttribute('y'))<300);
  assert.deepEqual(ticks.map(text=>text.textContent),['0','1','2','3']);
  assert.deepEqual(svg.querySelectorAll('.nb-timeline-point').map(point=>point.textContent),['2026-10-16: 2 notes (2 added)','2026-10-18: 3 notes (1 added)']);
 });
 await test('undated notes remain in the total without being assigned an invented plotting date',async()=>{
  const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?timeline({total:5,undated_count:2}):undefined});
  assert.equal(app.q('timeline').querySelector('.nb-timeline-total').textContent,'5 notes');assert.match(app.q('timeline').textContent,/2 notes have no usable creation date.*included in the total only/);
  assert.match(app.q('timeline').querySelector('svg').getAttribute('aria-label'),/^3 dated notes/);
  assert.equal(app.q('timeline').querySelectorAll('.nb-timeline-point').length,2);
 });
 await test('inconsistent aggregate totals or privacy scope cannot produce a believable timeline',async()=>{
  for(const value of [timeline({total:4}),timeline({audience:'all_accounts'}),timeline({points:[{date:'2026-10-18',created:3,total:2}]})]){
   const app=await boot({initialParams:new URLSearchParams('view=timeline'),respond:url=>url==='/api/notebook/timeline'?value:undefined});
   assert.equal(app.q('timeline').querySelector('svg'),null);assert.match(app.q('timeline').textContent,/could not be loaded/);
  }
 });
 await test('Human-written is unchecked by default and only explicit choices add the canonical tag',async()=>{
  for(const checked of [false,true]){
   let sent;const app=await boot({respond:(url,body)=>url==='/api/notebook/notes'&&body?(sent=body,{note:note()}):undefined});app.q('new').click();fill(app);
   const human=app.root.querySelector('[name="human-written"]');assert.equal(human.checked,false);assert.match(app.q('dialog-body').textContent,/Optional, self-reported label; not verified/);
   if(checked){human.checked=true;human.dispatchEvent({type:'change'});}app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(sent.content.tags.includes('human-written'),checked);assert.equal(Object.hasOwn(sent.content,'human-written'),false);
  }
 });
 await test('Human-written and freeform tags stay synchronized and changes count as an unsaved draft',async()=>{
  const app=await boot();app.q('new').click();const human=app.root.querySelector('[name="human-written"]'),tags=app.root.querySelector('[name="tags"]');
  tags.value='algebra, human-written';tags.dispatchEvent({type:'input'});assert.equal(human.checked,true);
  human.checked=false;human.dispatchEvent({type:'change'});assert.equal(tags.value,'algebra');
  human.checked=true;human.dispatchEvent({type:'change'});assert.equal(tags.value,'algebra, human-written');human.dispatchEvent({type:'change'});assert.equal(tags.value,'algebra, human-written');
  tags.value='algebra';tags.dispatchEvent({type:'input'});assert.equal(human.checked,false);
  let warned=false;app.sandbox.window.confirm=()=>{warned=true;return false;};app.q('close').click();assert.equal(warned,true);assert.equal(app.q('dialog').open,true);
 });
 await test('editing explicitly tagged content preserves or removes Human-written on the new revision',async()=>{
  for(const remove of [false,true]){
   const authored={...content(),tags:['algebra','human-written']};let sent;const app=await boot({respond:(url,body)=>url.endsWith('/revisions')&&body?(sent=body,{note:note()}):url.startsWith('/api/notebook/notes/note-one')?{note:note({content:authored})}:undefined});await app.show('note-one');byText(app.q('full'),'Edit').click();
   const human=app.root.querySelector('[name="human-written"]');assert.equal(human.checked,true);if(remove){human.checked=false;human.dispatchEvent({type:'change'});}app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();assert.equal(sent.content.tags.includes('human-written'),!remove);assert.deepEqual(authored.tags,['algebra','human-written']);
  }
 });
 await test('an uncertain save freezes Human-written and retries the original tag choice',async()=>{
  const writes=[];const app=await boot({respond:(url,body)=>{if(url==='/api/notebook/notes'&&body){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('Lost response'),{status:0});return{note:note()};}}});app.q('new').click();fill(app);const human=app.root.querySelector('[name="human-written"]');human.checked=true;human.dispatchEvent({type:'change'});const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();assert.equal(human.disabled,true);assert.equal(app.root.querySelector('[name="tags"]').disabled,true);
  human.checked=false;human.dispatchEvent({type:'change'});app.root.querySelector('[name="tags"]').value='tampered';form.dispatchEvent({type:'submit'});await flush();assert.deepEqual(writes[0],writes[1]);assert.ok(writes[1].content.tags.includes('human-written'));
 });
 await test('Human-written badges follow the displayed revision across accounts and support ordinary tag filtering',async()=>{
  const tagged=note({owner:{id:2,handle:'bob'},can_edit:false,...revision(1,'Human note','approved'),content:{...content('Human note'),tags:['algebra','human-written']}}),untagged=note({id:'other',owner:{id:2,handle:'bob'},can_edit:false,...revision(1,'Other note','approved')});
  const app=await boot({respond:url=>url.startsWith('/api/notebook/notes?')?summaryPage(url,[tagged,untagged]):url.startsWith('/api/notebook/notes/note-one')?{note:tagged}:undefined});assert.equal(app.q('list').querySelectorAll('.nb-human-written').length,1);assert.match(app.q('list').querySelector('.nb-human-written').getAttribute('title'),/Self-reported.*not verified/);
  app.q('list').querySelector('.nb-title').click();byText(app.q('list'),'Human-written').click();await flush();assert.equal(listQuery(app.requests.at(-1).url).get('tag'),'human-written');assert.equal(app.q('list').querySelectorAll('.nb-entry').length,1);
  await app.show('note-one');assert.equal(app.q('full').querySelectorAll('.nb-human-written').length,1);assert.equal(byText(app.q('full'),'Edit'),undefined);
  app.setAccount({id:3,handle:'carol'});assert.equal(app.q('full').textContent,'');await app.show('note-one');assert.equal(app.q('full').querySelectorAll('.nb-human-written').length,1);
 });
 await test('historical Human-written labels never infer authorship of a later untagged revision',async()=>{
  const older=revision(1,'Earlier human draft','approved');older.content.tags.push('human-written');const latest=revision(2,'Later agent draft');const stored=note({revision:2,revisions:[older,latest]});const app=await boot({respond:url=>url.startsWith('/api/notebook/notes/note-one')?{note:stored}:undefined});await app.show('note-one',1);assert.equal(app.q('full').querySelectorAll('.nb-human-written').length,1);byText(app.q('full'),'Edit').click();assert.equal(app.root.querySelector('[name="human-written"]').checked,false);app.q('close').click();await app.show('note-one',2);assert.equal(app.q('full').querySelectorAll('.nb-human-written').length,0);
 });
 await test('imports preserve only explicit Human-written tags and do not infer authorship from the importing account',async()=>{
  const workspace={format:'crucible-theory-local-v1',notes:[{id:'human',revisions:[{revision:1,createdAt:1000,...content('Human source'),tags:['human-written']}]},{id:'agent',revisions:[{revision:1,createdAt:1000,...content('Agent source')}]}],views:[]};let sent;const app=await boot({stored:JSON.stringify(workspace),respond:(url,body)=>url==='/api/notebook/import'?(sent=body,{added_notes:2}):undefined});app.q('files').click();assert.match(app.q('dialog-body').textContent,/human-written.*never add it automatically or infer it from an account.*omit it from agent-written revisions/);byText(app.q('dialog-body'),'Review browser drafts for import').click();byText(app.q('dialog-body'),'Import notes').click();await flush();assert.deepEqual(Array.from(sent.workspace.notes[0].revisions[0].tags),['human-written']);assert.equal(sent.workspace.notes[1].revisions[0].tags.includes('human-written'),false);
 });
 console.log(`\n${passed} durable notebook frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

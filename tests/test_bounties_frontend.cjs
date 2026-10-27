// Real Bounties renderer and notebook ownership; only DOM/network are synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, parseHTML, flush} = require('./frontend-dom.cjs');
const web = path.resolve(__dirname, '../web');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
function browserPrimitives(document, window) {
  document.createTextNode = text => String(text);
  const proto = Object.getPrototypeOf(document.createElement('div'));
  if (!Object.getOwnPropertyDescriptor(proto,'name')) Object.defineProperty(proto,'name',{get(){return this.getAttribute('name')||'';},set(value){this.setAttribute('name',value);}});
  if (!proto.contains) proto.contains = function(node) { for (;node;node=node.parentElement) if(node===this)return true;return false; };
  if (!proto.setCustomValidity) proto.setCustomValidity = function(text) {this.validationMessage=text;};
  if (!proto.reportValidity) proto.reportValidity = function() {return !this.validationMessage;};
  window.removeEventListener = function(type, fn) {this.listeners.set(type,(this.listeners.get(type)||[]).filter(x=>x!==fn));};
}
function load(sandbox, file) { vm.runInContext(fs.readFileSync(path.join(web,'notebook',file),'utf8'),sandbox,{filename:file}); }
function guide({mutate,current=()=>true}={}) {
  const document = parseHTML('<body><section id="host"></section></body>');
  const window = {listeners:new Map(),addEventListener(type,fn){this.listeners.set(type,[...(this.listeners.get(type)||[]),fn]);},dispatchEvent(event){for(const fn of this.listeners.get(event.type)||[])fn(event);}};
  browserPrimitives(document,window);
  const sandbox = vm.createContext({window,document,URLSearchParams,structuredClone});
  load(sandbox,'research-guide-data.js');load(sandbox,'research-guide.js');mutate?.(window.CrucibleResearchGuideData);
  const host = document.querySelector('#host'), routes=[];let controller;
  controller=window.CrucibleResearchGuide.mount(host,{current,navigate(route){routes.push(route);controller.show(new URLSearchParams(route.split('?')[1]));}});
  const show=query=>controller.show(new URLSearchParams(query));show('view=guide');
  return {document,window,host,controller,routes,show,data:window.CrucibleResearchGuideData,
    control:key=>host.querySelector('[data-rg-control="'+key+'"]')};
}
const buttons = (host,label) => host.querySelectorAll('button').filter(n=>n.textContent===label);
const click = (host,label) => { const match=buttons(host,label);assert.equal(match.length,1,label);match[0].click(); };
function fill(host,key,value){const input=host.querySelector('[name="'+key+'"]');assert.ok(input,key);input.value=value;input.dispatchEvent({type:'input'});return input;}
const submit=host=>host.querySelector('form').dispatchEvent({type:'submit',preventDefault(){}});
const ids = host=>host.querySelectorAll('[data-question]').map(n=>n.dataset.question);
let passed=0;
async function test(name,fn){await fn();console.log('PASS '+name);passed++;}
(async()=>{
 await test('open board starts unselected; all four stable sorts retain the same six examples',()=>{
  const f=guide(),original=ids(f.host);assert.equal(original.length,6);assert.equal(f.host.querySelector('.rg-reader').hidden,true);
  for(const sort of ['concrete','open','points-high','points-low']){
   f.show('view=guide&sort='+sort);const ordered=ids(f.host);assert.deepEqual([...ordered].sort(),[...original].sort());
   const values=ordered.map(id=>sort.startsWith('points')?f.data.questions[id].points:6-f.data.openness[f.data.questions[id].openness].position);
   for(let i=1;i<values.length;i++)assert.ok(['open','points-low'].includes(sort)?values[i]>=values[i-1]:values[i]<=values[i-1]);
  }
  assert.match(f.host.textContent,/Example data/);assert.match(f.host.textContent,/Local changes reset on reload/);
 });
 await test('sorting occurs before pagination, with stable ties and unset points last in either direction',()=>{
  const f=guide({mutate:data=>{data.questions.extra={...data.questions.baseline,title:'Extra example',points:data.questions.baseline.points};data.questions.unset={...data.questions.baseline,title:'Unset reward',points:null};}});
  for(const sort of ['points-high','points-low']){
   f.show('view=guide&sort='+sort);const first=ids(f.host);f.show('view=guide&sort='+sort+'&page=2');const all=[...first,...ids(f.host)];
   assert.equal(new Set(all).size,8);assert.equal(all.at(-1),'unset');assert.ok(all.indexOf('baseline')<all.indexOf('extra'));
  }
 });
 await test('obsolete filters do not hide tasks; invalid sort falls back and exact reader return retains sort',()=>{
  const f=guide();f.show('view=guide&sort=bogus&q=nothing&openness=none&work=none&direction=none');assert.equal(ids(f.host).length,6);
  assert.equal(new URLSearchParams(f.controller.route().split('?')[1]).has('sort'),false);
  f.show('view=guide&sort=points-low');const id=ids(f.host)[0];f.control('question-'+id).click();assert.equal(f.host.querySelector('.rg-board').hidden,true);
  assert.equal(f.document.activeElement,f.control('selected-title'));f.control('reader-back').click();assert.match(f.controller.route(),/sort=points-low/);
  assert.equal(f.document.activeElement,f.control('question-'+id));
  for(const id of ['missing-local-draft','constructor','__proto__']){f.show('view=guide&question='+id);assert.match(f.host.textContent,/no longer in the draft/);}
 });
 await test('every open example has a proposed verification plan, never a claimed executed verifier',()=>{
  const f=guide();for(const id of ids(f.host)){f.show('view=guide&question='+id);assert.match(f.host.querySelector('.rg-detail-body').textContent,/How it’s checkedProposed verification plan · local example/);assert.ok(f.host.querySelector('.rg-verification-result'));}
 });
 await test('proposal validation retains draft and saves only an unchecked local task with no assigned reward',()=>{
  const f=guide();f.control('post').click();fill(f.host,'post-title','A local task');fill(f.host,'post-progress','Reproduce one result');submit(f.host);
  assert.ok(f.host.querySelector('[name="post-verification"]').validationMessage);
  fill(f.host,'post-verification','Pin the input and compare outputs');f.show('view=guide');f.control('post').click();assert.equal(f.host.querySelector('[name="post-title"]').value,'A local task');
  submit(f.host);assert.match(f.host.querySelector('.rg-composer').textContent,/Pin the input and compare outputs/);click(f.host,'Save proposal in draft');
  assert.match(f.controller.route(),/status=mine/);assert.match(f.host.querySelector('.rg-reader').textContent,/Not yet checked/);assert.match(f.host.textContent,/Points not set/);
  f.show('view=guide');assert.equal(ids(f.host).length,6);assert.equal(Object.keys(f.data.questions).length,8,'fixture itself was never mutated');
 });
 await test('a new solution keeps both approvals pending and cannot close another poster’s task',()=>{
  const f=guide();f.show('view=guide&question=baseline&detail=contribute');fill(f.host,'contribution-kind','resolution');fill(f.host,'contribution-summary','Synthetic bounded result');submit(f.host);
  const body=f.host.querySelector('.rg-detail-body');assert.match(body.textContent,/Poster · pendingPipeline · pending/);assert.equal(buttons(body,'Review completion').length,0);
  f.show('view=guide');assert.ok(ids(f.host).includes('baseline'));
 });
 await test('example poster approval preserves separate pipeline approval and own-task ownership',()=>{
  const f=guide();f.show('view=guide&status=mine&question=reproduce&detail=progress');click(f.host.querySelector('.rg-detail-body'),'Review completion');
  fill(f.host,'review-reason','The stated example output matches');submit(f.host.querySelector('.rg-review-action'));
  assert.match(f.controller.route(),/status=past/);assert.match(f.host.textContent,/No real points or credits changed/);
  assert.match(f.host.querySelector('.rg-detail-body').textContent,/Poster · approvedPipeline · approved/);
 });
 await test('unchanged route retains focused DOM, hidden and revoked views cannot act, destroy clears unload hook',()=>{
  let current=true;const f=guide({current:()=>current});f.control('post').click();const title=fill(f.host,'post-title','Retained text');title.focus();f.show('view=guide&compose=bounty');assert.equal(f.document.activeElement,title);
  const warning={type:'beforeunload',preventDefault(){this.prevented=true;}};f.window.dispatchEvent(warning);assert.equal(warning.prevented,true);
  const routeCount=f.routes.length;f.controller.hide();fill(f.host,'post-progress','Output');fill(f.host,'post-verification','Check');submit(f.host);assert.equal(f.routes.length,routeCount);
  f.show('view=guide&compose=bounty');current=false;submit(f.host);assert.equal(f.routes.length,routeCount);const after={type:'beforeunload',preventDefault(){this.prevented=true;}};f.window.dispatchEvent(after);assert.equal(after.prevented,undefined);
  f.controller.destroy();assert.equal(f.host.textContent,'');assert.equal(f.window.listeners.get('beforeunload').length,0);
 });
 async function notebook({prepare,account={id:'alice',handle:'alice'},reviewOnly=false}={}){
  const app=await createApp({schemes:[],hash:'#about'});browserPrimitives(app.document,app.window);app.sandbox.structuredClone=structuredClone;
  for(const file of ['store.js','seed.js','research-guide-data.js','research-guide.js','notebook.js'])load(app.sandbox,file);
  let current=account,credential='one',navigation=Promise.resolve();const requests=[],host=app.$('#notebook-workspace');host.hidden=false;
  const controller=app.window.CrucibleNotebook.mount(host,{account:()=>current,session:()=>credential,reviewOnly,
   prepareGuide:prepare||(()=>Promise.resolve()),api:async(url,body)=>{requests.push({url,body});return {notes:[],next_after:null};},
   navigate:route=>{app.history.pushState(null,'',route);navigation=controller.show(new URLSearchParams(route.split('?')[1]));}});
  return {...app,host,controller,requests,show:query=>controller.show(new URLSearchParams(query)),navigated:()=>navigation,
   account(value){current=value;controller.setAccount(value);},rotate(){credential='two';controller.setAccount(current);}};
 }
 await test('Bounties needs admission and does not fetch note bodies or perform API writes',async()=>{
  const f=await notebook();await f.show('view=guide');assert.match(f.host.textContent,/Research bounties/);assert.deepEqual(f.requests,[]);
  f.account(null);await f.show('view=guide');assert.equal(f.host.querySelector('.rg-root'),null);assert.match(f.host.textContent,/Sign in to read/);
 });
 await test('late protected assets cannot mount after account loss or session rotation',async()=>{
  for(const change of ['account','rotate']){const late=deferred(),f=await notebook({prepare:()=>late.promise});const pending=f.show('view=guide');if(change==='account')f.account(null);else f.rotate();late.resolve();await pending;assert.equal(f.host.querySelector('.rg-root'),null);}
 });
 await test('Notes return preserves same-account draft but session rotation destroys it',async()=>{
  const f=await notebook();await f.show('view=guide');f.host.querySelector('[data-rg-control=post]').click();await f.navigated();fill(f.host,'post-title','Account-owned draft');
  await f.show('');f.host.querySelector('[data-nb=guide-view]').click();await f.navigated();assert.equal(f.host.querySelector('[name=post-title]').value,'Account-owned draft');
  f.rotate();await f.show('view=guide&compose=bounty');assert.equal(f.host.querySelector('[name=post-title]').value,'');
 });
 await test('asset failure has an explicit retry and does not create a second data client',async()=>{
  let attempts=0;const f=await notebook({prepare:()=>++attempts===1?Promise.reject(Error('synthetic')):Promise.resolve()});await f.show('view=guide');assert.match(f.host.textContent,/Bounties could not be loaded/);
  click(f.host,'Try again');await flush();assert.equal(attempts,2);assert.ok(f.host.querySelector('.rg-root'));assert.deepEqual(f.requests,[]);
 });
 await test('review workspace cannot switch to a fixture bounty via view parameter',async()=>{
  let attempts=0;const f=await notebook({reviewOnly:true,prepare:()=>{attempts++;}});await f.show('view=guide');assert.equal(attempts,0);assert.equal(f.host.querySelector('.rg-root'),null);assert.ok(f.requests.every(r=>r.url.startsWith('/api/notebook/review-queue')));
 });
 console.log(`${passed} Bounties checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

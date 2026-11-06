const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const helper = fs.readFileSync(path.join(__dirname,'../web/graph-points.js'),'utf8');
const points=[{id:'p1',position:1,value:20},{id:'p2',position:2,value:null},{id:'p3',position:3,value:80}];
const x=value=>48+(value-1)*208.5, y=value=>190-value*1.5;
const describe=point=>point.id+' · '+(point.value===null?'No comparable measurement':point.value+' units');
async function fixture(){
  const app=await createApp({schemes:[],hash:'#about'});vm.runInContext(helper,app.sandbox,{filename:'graph-points.js'});
  const api=app.window.CrucibleGraphPoints;
  function chart({key='release-one',values=points,initialId=null,oldLabel=false,onPreview,onSelection,onReadout,hidden=false,
    interactionKey='security:ram',previewState=null,container=null}={}){
    const host=container||app.document.createElement('div'),svg=app.document.createElementNS('http://www.w3.org/2000/svg','svg'),readout=app.document.createElement('div');
    if(container)host.replaceChildren();
    const title=app.document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent='Exact reference';title.id='chart-title';svg.append(title);
    if(oldLabel)svg.setAttribute('aria-labelledby','chart-title chart-description');else svg.setAttribute('aria-label','Resource measurement');
    svg.getBoundingClientRect=()=>({left:10,top:20,width:480,height:240});host.hidden=hidden;host.append(svg,readout);if(!container)app.$('#about-workspace').append(host);
    api.attach({svg,readout,key,points:values,x,y,describe,initialId,onPreview,onSelection,onReadout,interactionKey,previewState});
    return{host,svg,readout,marker:selector=>svg.querySelector(selector),status:()=>host.querySelector('.chart-point-announcement'),
      event:(type,position,extra={})=>svg.dispatchEvent({type,clientX:10+x(position),clientY:120,...extra}),
      key:key=>{const event={type:'keydown',key};svg.dispatchEvent(event);return event;}};
  }
  return{...app,api,chart};
}
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
 await test('larger hover and selected dots keep separate positions with the pinned guide',async()=>{
   const app=await fixture(),chart=app.chart({initialId:'p1'});
   chart.event('pointermove',3,{pointerType:'mouse'});
   assert.equal(app.api.selected('release-one'),'p1');assert.equal(chart.marker('.chart-selected-dot').getAttribute('cx'),String(x(1)));assert.equal(chart.marker('.chart-selected-dot').getAttribute('r'),'5');
   assert.equal(chart.marker('.chart-hover-dot').getAttribute('cx'),String(x(3)));assert.equal(chart.marker('.chart-hover-dot').getAttribute('r'),'5');
   assert.equal(chart.marker('.chart-hover-halo').getAttribute('r'),'9');
   assert.equal(chart.marker('.chart-selection-guide').getAttribute('x1'),String(x(1)));assert.match(chart.readout.textContent,/p3/);
   assert.equal(chart.status().textContent,'');chart.event('pointerleave',3);
   assert.equal(chart.marker('.chart-hover-dot').getAttribute('visibility'),'hidden');assert.match(chart.readout.textContent,/p1/);
   assert.equal(chart.svg.querySelectorAll('animate,animateTransform').length,0);
 });
 await test('one click commits the exact profile across graphs in the same release only',async()=>{
   const app=await fixture(),a=app.chart(),b=app.chart({values:points.map(p=>({...p,value:p.value===null?null:p.value*2}))}),other=app.chart({key:'release-two',initialId:'p1'});
   a.event('click',3);assert.equal(app.api.selected('release-one'),'p3');assert.match(b.readout.textContent,/p3 · 160 units/);
   assert.equal(b.marker('.chart-selection-guide').getAttribute('x1'),String(x(3)));assert.equal(other.marker('.chart-selection-guide').getAttribute('x1'),String(x(1)));
   for(const chart of [a,b]){assert.equal(chart.marker('.chart-selected-dot').getAttribute('visibility'),'visible');assert.equal(chart.marker('.chart-selected-dot').getAttribute('cx'),String(x(3)));}
   assert.equal(a.marker('.chart-hover-dot').getAttribute('visibility'),'visible');assert.equal(a.marker('.chart-hover-halo').getAttribute('visibility'),'visible');
   assert.equal(b.marker('.chart-hover-dot').getAttribute('visibility'),'hidden');
   a.event('pointermove',1,{pointerType:'mouse'});assert.equal(a.marker('.chart-hover-dot').getAttribute('cx'),String(x(1)));assert.equal(a.marker('.chart-selection-guide').getAttribute('x1'),String(x(3)));
   assert.match(a.status().textContent,/Selected p3/);assert.equal(b.status().textContent,'');
 });
 await test('missing values keep their exact identity and never snap to a nearby observation',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'});a.event('click',2);
   assert.equal(app.api.selected('release-one'),'p2');assert.match(a.readout.textContent,/p2 · No comparable measurement/);
   assert.equal(a.marker('.chart-selected-dot').getAttribute('visibility'),'hidden');assert.equal(a.marker('.chart-selection-guide').getAttribute('visibility'),'hidden');
   a.key('Enter');assert.equal(app.api.selected('release-one'),'p2');
 });
 await test('Enter on a selected chart retains the selection; arrows preview and announce without committing',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p3'});a.key('Enter');assert.equal(app.api.selected('release-one'),'p3');
   const event=a.key('ArrowLeft');assert.equal(event.defaultPrevented,true);assert.equal(app.api.selected('release-one'),'p3');
   assert.match(a.readout.textContent,/p2/);assert.match(a.status().textContent,/Preview p2.*Enter/);
   a.key('Escape');assert.match(a.readout.textContent,/p3/);a.key('Home');a.key(' ');assert.equal(app.api.selected('release-one'),'p1');
   a.key('End');a.key('Enter');assert.equal(app.api.selected('release-one'),'p3');
 });
 await test('a selected profile absent from another chart is not relabeled or silently replaced',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'}),b=app.chart({values:points.slice(0,2)});a.event('click',3);
   assert.match(b.svg.getAttribute('aria-label'),/Selected configuration is unavailable/);
   assert.equal(b.marker('.chart-selection-guide').getAttribute('visibility'),'hidden');b.key('Enter');b.key('Escape');
   assert.equal(app.api.selected('release-one'),'p3');assert.match(b.status().textContent,/unavailable/);
 });
 await test('a focused keyboard preview survives same-chart replacement without committing',async()=>{
   const app=await fixture(),a=app.chart();a.svg.focus();a.key('End');
   const previewState=app.api.capturePreview(a.svg);assert.ok(previewState);
   const b=app.chart({container:a.host,previewState,values:points.map(p=>({...p,value:p.value===null?null:p.value+10}))});b.svg.focus();
   assert.equal(app.api.selected('release-one'),null);assert.equal(b.marker('.chart-selected-dot').getAttribute('visibility'),'hidden');
   assert.equal(b.marker('.chart-hover-dot').getAttribute('cx'),String(x(3)));assert.match(b.readout.textContent,/p3 · 90 units/);
   b.key('Enter');assert.equal(app.api.selected('release-one'),'p3');assert.match(b.status().textContent,/Selected p3/);
 });
 await test('a vanished or unmeasured restored preview blocks Enter instead of selecting the first point',async()=>{
   for(const initialId of [null,'p1'])for(const values of [points.slice(0,2),points.map(p=>({...p,value:p.id==='p3'?null:p.value}))]){
     const app=await fixture(),a=app.chart({initialId});a.svg.focus();a.key('End');
     const b=app.chart({container:a.host,previewState:app.api.capturePreview(a.svg),values});b.svg.focus();b.key('Enter');
     assert.equal(app.api.selected('release-one'),initialId);assert.equal(b.marker('.chart-hover-dot').getAttribute('visibility'),'hidden');
     assert.match(b.status().textContent,/previewed configuration is unavailable/);
     b.key('Home');b.key('Enter');assert.equal(app.api.selected('release-one'),'p1');
   }
 });
 await test('temporary preview never crosses card, metric, release, route or account boundaries',async()=>{
   for(const boundary of ['card','metric','release','route','account']){
     const app=await fixture(),a=app.chart();a.svg.focus();a.key('End');const previewState=app.api.capturePreview(a.svg);
     if(boundary==='route')app.history.replaceState(null,'','#other');
     if(boundary==='account')app.api.clear();
     const b=app.chart({previewState,container:boundary==='card'?null:a.host,
       interactionKey:boundary==='metric'?'security:quantum':'security:ram',key:boundary==='release'?'release-two':'release-one'});
     assert.equal(b.marker('.chart-hover-dot').getAttribute('visibility'),'hidden');assert.equal(app.api.selected('release-one'),null);
   }
 });
 await test('only focused keyboard previews can be captured, not pointer or inactive chart previews',async()=>{
   const app=await fixture(),a=app.chart();a.key('End');assert.equal(app.api.capturePreview(a.svg),null);
   a.svg.focus();a.event('pointermove',3,{pointerType:'mouse'});assert.equal(app.api.capturePreview(a.svg),null);
   a.key('End');assert.ok(app.api.capturePreview(a.svg));a.svg.dispatchEvent({type:'blur'});assert.equal(app.api.capturePreview(a.svg),null);
 });
 await test('touch preview never captures scrolling and a deliberate tap can commit',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'});const move={type:'pointermove',pointerType:'touch',clientX:475,clientY:120};a.svg.dispatchEvent(move);
   assert.equal(move.defaultPrevented,undefined);assert.match(a.readout.textContent,/p1/);a.event('click',3,{pointerType:'touch'});
   assert.equal(app.api.selected('release-one'),'p3');
 });
 await test('malformed pointer coordinates and zero-size charts cannot select a point',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'});a.svg.dispatchEvent({type:'click'});
   a.svg.dispatchEvent({type:'click',clientX:NaN,clientY:120});a.svg.getBoundingClientRect=()=>({left:0,top:0,width:0,height:0});a.event('click',3);
   assert.equal(app.api.selected('release-one'),'p1');
 });
 await test('account cleanup retires old listeners as well as clearing markers and selection',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p3'});app.api.clear();assert.equal(app.api.selected('release-one'),null);
   assert.equal(a.marker('.chart-interaction-markers'),null);assert.equal(a.readout.textContent,'');a.event('click',2);a.key('Enter');
   assert.equal(app.api.selected('release-one'),null);const next=app.chart({initialId:'p1'});a.event('click',3);
   assert.equal(app.api.selected('release-one'),'p1');assert.match(next.readout.textContent,/p1/);
 });
 await test('detached prior graphs cannot mutate current shared selection',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'});a.host.remove();const b=app.chart();a.event('click',3);a.key('End');a.key('Enter');
   assert.equal(app.api.selected('release-one'),'p1');assert.match(b.readout.textContent,/p1/);
 });
 await test('interactive labels retain scientific descriptions and expose keyboard instructions',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1',oldLabel:true});
   assert.equal(a.svg.getAttribute('role'),'group');assert.equal(a.svg.getAttribute('tabindex'),'0');assert.equal(a.svg.getAttribute('aria-labelledby'),null);
   assert.equal(a.svg.getAttribute('aria-describedby'),'chart-title chart-description');assert.match(a.svg.getAttribute('aria-label'),/Exact reference.*Selected p1.*Arrow keys/);
   assert.equal(a.readout.getAttribute('aria-live'),'off');assert.equal(a.status().getAttribute('aria-live'),'polite');
 });
 await test('hidden prior surfaces cannot commit from a queued pointer or key event',async()=>{
   const app=await fixture(),a=app.chart({initialId:'p1'});a.host.hidden=true;a.event('click',3);a.key('End');a.key('Enter');
   assert.equal(app.api.selected('release-one'),'p1');a.host.hidden=false;a.event('click',3);assert.equal(app.api.selected('release-one'),'p3');
 });
 await test('selected attack source stays pinned through hover, blur and pointerleave',async()=>{
   const app=await fixture();let clicked=0;
   const a=app.chart({onSelection:(point,target)=>{const anchor=app.document.createElement('a');anchor.textContent='A-1';anchor.href='#notebook?note=exact';anchor.addEventListener('click',event=>{event.preventDefault();clicked++;});target.append(anchor);}});
   a.key('End');assert.equal(a.readout.querySelector('a'),null);a.key('Enter');const anchor=a.readout.querySelector('a');assert.ok(anchor);
   anchor.focus();a.svg.dispatchEvent({type:'blur'});a.event('pointerleave',3);
   assert.equal(a.readout.querySelector('a'),anchor);assert.equal(anchor.isConnected,true);assert.equal(app.document.activeElement,anchor);
   a.event('pointermove',1,{pointerType:'mouse'});assert.equal(a.readout.querySelector('a'),anchor);assert.match(a.readout.textContent,/p3/);
   assert.equal(a.marker('.chart-hover-dot').getAttribute('cx'),String(x(1)));a.event('pointerleave',1);assert.equal(a.readout.querySelector('a'),anchor);
   anchor.click();assert.equal(clicked,1);
 });
 await test('structured readouts retain exact selection and accessible descriptions without replacing pinned sources',async()=>{
   const app=await fixture(),calls=[];
   const a=app.chart({onReadout:(point,target)=>{
     calls.push(point.id);
     const stage=app.document.createElement('span'),value=app.document.createElement('strong');
     stage.textContent=point.id;value.textContent=point.value===null?'Unavailable':String(point.value);
     target.append(stage,value);
   },onSelection:(point,target)=>{
     const anchor=app.document.createElement('a');anchor.textContent='Exact source';anchor.href='#source-'+point.id;target.append(anchor);
   }});
   a.event('pointermove',1,{pointerType:'mouse'});
   assert.equal(a.readout.querySelector('strong').textContent,'20');
   assert.equal(a.readout.querySelector('a'),null);
   a.event('click',3);const source=a.readout.querySelector('a');
   assert.equal(a.readout.querySelector('strong').textContent,'80');
   assert.match(a.svg.getAttribute('aria-label'),/Selected p3 · 80 units/);
   assert.match(a.status().textContent,/Selected p3 · 80 units/);
   const count=calls.length;
   a.event('pointermove',1,{pointerType:'mouse'});a.event('pointerleave',1);
   assert.equal(a.readout.querySelector('strong').textContent,'80');
   assert.equal(a.readout.querySelector('a'),source);assert.equal(calls.length,count);
   a.event('click',2);
   assert.equal(a.readout.querySelector('strong').textContent,'Unavailable');
   assert.match(a.svg.getAttribute('aria-label'),/Selected p2 · No comparable measurement/);
 });
 await test('hidden initial charts paint committed positions before reveal and never expose origin ghost markers',async()=>{
   const app=await fixture(),selected=app.chart({initialId:'p3',hidden:true}),empty=app.chart({key:'empty-release',hidden:true});
   assert.equal(selected.marker('.chart-selected-dot').getAttribute('cx'),String(x(3)));assert.equal(selected.marker('.chart-selected-dot').getAttribute('visibility'),'visible');
   assert.equal(selected.marker('.chart-selection-guide').getAttribute('x1'),String(x(3)));selected.host.hidden=false;
   assert.equal(selected.marker('.chart-selection-guide').getAttribute('visibility'),'visible');assert.match(selected.readout.textContent,/p3/);
   for(const selector of ['.chart-selected-dot','.chart-selection-guide','.chart-hover-dot','.chart-hover-halo'])assert.equal(empty.marker(selector).getAttribute('visibility'),'hidden');
   assert.equal(empty.readout.classList.contains('is-empty'),true);empty.event('click',1);assert.equal(app.api.selected('empty-release'),null);
 });
 await test('actual resource and security renderers share profile selection without inventing missing values',async()=>{
   const profiles=[1,2,3].map(n=>({id:'p'+n,profile_id:'p'+n,instance_size:n,parameters:{n},encoding:{shared_secret_bytes:32},
     ...(n!==2?{initial_security_estimate:{model_id:'attack-model',bits:n*10}}:{})}));
   const scheme={id:'interaction-fixture',version:'1',name:'Synthetic interaction fixture',profiles,protocol:{},initial_security_model:{id:'attack-model',kind:'attack-reference'}};
   const report={scheme_id:scheme.id,version:'1',state:'current',historical:[],publication:{id:'a'.repeat(64),completeness:'complete',received_at:100,source:{},
     binding:{implementation:{id:'fixture',sha256:'b'.repeat(64)},profiles:profiles.map(p=>({profile_id:p.id,configuration:p.id}))}},
     resources:{configurations:profiles.map((p,i)=>({configuration:p.id,sizes_bytes:{public_key:32*(i+1),ciphertext:64}}))}};
   const app=await createApp({schemes:[scheme],hash:'#about',responder:url=>url.pathname==='/api/protocol/resources'?report:undefined});
   vm.runInContext(helper,app.sandbox,{filename:'graph-points.js'});await app.navigate('#scheme/interaction-fixture/1');await flush();
   const resource=app.$('#overview-chart-public'),security=app.$('#overview-chart-security');
   for(const chart of [resource,security])chart.querySelector('svg').getBoundingClientRect=()=>({left:0,top:0,width:480,height:240});
   resource.querySelector('svg').dispatchEvent({type:'click',clientX:x(3),clientY:100});
   assert.match(resource.querySelector('.chart-point-readout').textContent,/Stage 3.*96 bytes/);
   assert.match(security.querySelector('.chart-point-readout').textContent,/Stage 3.*30/);
   assert.equal(security.querySelector('.chart-selection-guide').getAttribute('x1'),String(x(3)));
   resource.querySelector('svg').dispatchEvent({type:'click',clientX:x(2),clientY:100});
   assert.match(security.querySelector('.chart-point-readout').textContent,/Stage 2.*No estimate/);
   assert.equal(security.querySelector('.chart-selection-guide').getAttribute('visibility'),'hidden');
   assert.equal(resource.querySelectorAll('dialog,[aria-haspopup="dialog"]').length,0);
   assert.ok(resource.querySelector('details'));assert.ok(security.querySelector('details'));
 });
 await test('late exact-note evidence preserves End then Enter on the actual security chart',async()=>{
   const profiles=[1,2,3].map(n=>({id:'p'+n,instance_size:n,parameters:{n},initial_security_estimate:{model_id:'fixture',bits:n*10}}));
   const scheme={id:'late-note',version:'1',name:'Late note',profiles,protocol:{},initial_security_model:{id:'fixture',kind:'attack-reference'}};
   let release;const response=new Promise(resolve=>{release=resolve;});
   const app=await createApp({schemes:[scheme],hash:'#about',responder:(url,options,envelope)=>{
     if(url.pathname==='/api/session')return envelope({user:null,csrf_token:'fixture',capabilities:{curated_notes:false}});
     if(url.pathname==='/api/protocol/note-justifications')return response;
   }});
   vm.runInContext(helper,app.sandbox,{filename:'graph-points.js'});await app.navigate('#scheme/late-note/1');
   const host=app.$('#overview-chart-security'),first=host.querySelector('svg');first.focus();first.dispatchEvent({type:'keydown',key:'End'});
   release({format:'crucible-note-justifications-v1',scheme_id:'late-note',version:'1',sections:{},graph_models:{}});await flush();
   const replacement=host.querySelector('svg');assert.notEqual(replacement,first);assert.equal(app.document.activeElement,replacement);
   assert.equal(app.window.CrucibleGraphPoints.selected(JSON.stringify(['late-note','1'])),null);
   replacement.dispatchEvent({type:'keydown',key:'Enter'});
   assert.equal(app.window.CrucibleGraphPoints.selected(JSON.stringify(['late-note','1'])),'p3');
   assert.match(host.querySelector('.chart-point-readout').textContent,/Stage 3/);
 });
 await test('late resource data preserves a focused preview as an exact profile becomes measured',async()=>{
   const profiles=[1,2,3].map(n=>({id:'p'+n,instance_size:n,parameters:{n}}));
   const scheme={id:'late-resource',version:'1',name:'Late resource',profiles,protocol:{}};
   let release;const response=new Promise(resolve=>{release=resolve;});
   const app=await createApp({schemes:[scheme],hash:'#about',responder:url=>url.pathname==='/api/protocol/resources'?response:undefined});
   vm.runInContext(helper,app.sandbox,{filename:'graph-points.js'});await app.navigate('#scheme/late-resource/1');
   const host=app.$('#overview-chart-public'),first=host.querySelector('svg');
   // The lightweight DOM double omits native Element.matches.
   first.matches=selector=>selector==='.overview-svg';first.focus();first.dispatchEvent({type:'keydown',key:'End'});
   release({scheme_id:scheme.id,version:'1',state:'current',historical:[],publication:{id:'a'.repeat(64),completeness:'complete',received_at:100,source:{},
     binding:{implementation:{id:'fixture',sha256:'b'.repeat(64)},profiles:profiles.map(p=>({profile_id:p.id,configuration:p.id}))}},
     resources:{configurations:profiles.map((p,i)=>({configuration:p.id,sizes_bytes:{public_key:32*(i+1),ciphertext:64}}))}});await flush();
   const replacement=host.querySelector('svg');assert.notEqual(replacement,first);assert.equal(app.document.activeElement,replacement);
   assert.equal(app.window.CrucibleGraphPoints.selected(JSON.stringify(['late-resource','1'])),null);
   replacement.dispatchEvent({type:'keydown',key:'Enter'});
   assert.equal(app.window.CrucibleGraphPoints.selected(JSON.stringify(['late-resource','1'])),'p3');
   assert.match(host.querySelector('.chart-point-readout').textContent,/Stage 3.*96 bytes/);
 });
 await test('every attack retains its specification, with only applicable premises in numeric order',async()=>{
   const app=await fixture();
   const sources=vm.runInContext(`(() => {
     const scheme={id:'source-scope',version:'1',name:'Source scope',profiles:[{id:'p1',instance_size:1}]};
     const pin={id:'note-one',revision:1,content_sha256:'a'.repeat(64),note_section:'attack'};
     const model={source:pin,assumptions:[
       {label:'A-10',status:'unproved',stages:[1],source:{...pin,note_section:'second'}},
       {label:'A-1',status:'incomplete',stages:[1],source:{...pin,note_section:'scheme-gap'}},
       {label:'A-2',status:'model-condition',stages:[1],source:{...pin,note_section:'first'}},
       {label:'A-3',status:'unproved',stages:[2],source:{...pin,note_section:'other-stage'}}
     ]};
     noteJustifications.set(releaseKey(scheme.id,scheme.version),{value:{graph_models:{attack:model}}});
     const target=document.createElement('div'),point={id:'p1',label:'Stage 1',position:1,model:{id:'attack'}};
     renderPointSources(scheme,point,target);
     const labels=[...target.querySelectorAll('a')].map(a=>a.textContent.replace(/↗$/, ''));
     model.assumptions=[];target.replaceChildren();renderPointSources(scheme,point,target);
     return {labels,withoutPremises:[...target.querySelectorAll('a')].map(a=>a.textContent.replace(/↗$/, ''))};
   })()`,app.sandbox);
   assert.deepEqual(Array.from(sources.labels),['Attack specification','A-2','A-10']);
   assert.deepEqual(Array.from(sources.withoutPremises),['Attack specification']);
 });
 console.log(`\n${passed} graph interaction checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

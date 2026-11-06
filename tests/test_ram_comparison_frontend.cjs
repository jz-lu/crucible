// Exercise the real app renderer with exact-revision RAM evidence and the shared
// graph interaction helper. This is a DOM contract check, not a layout engine.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const graphHelper = fs.readFileSync(path.join(__dirname,'../web/graph-points.js'),'utf8');
const computation = 'crucible-bit-ram-v1';
const pin = section => ({id:'00000000-0000-4000-8000-000000000001',revision:4,
  content_sha256:'a'.repeat(64),note_section:section});
const profiles = () => [1,2,3].map(n=>({id:'p'+n,profile_id:'p'+n,instance_size:n,
  parameters:{n},initial_security_estimate:{model_id:'frozen',bits:200+n}}));
const scheme = () => ({id:'ram-fixture',version:'v1',name:'RAM comparison fixture',
  profiles:profiles(),protocol:{},next_profile_id:'p1',
  initial_security_model:{id:'frozen',label:'Legacy group reference',unit:'log2 group operations',kind:'attack-reference'},
  security_references:{format:'crucible-security-references-v1',scheme_id:'ram-fixture',version:'v1',metrics:{
    quantum:{models:[{id:'quantum-one',label:'Quantum fixture',unit:'log2 logical gates'}],
      estimates:[{profile_id:'p3',model_id:'quantum-one',bits:17}]}}}});
const model = id => ({id,label:'Explicit '+id,unit:'log2 bit operations',computation_model_id:computation,
  scope:'Only this exact shared-key recovery experiment.',limitations:['An upper bound, not a security lower bound.']});
const point = (id,algorithm,bits) => ({profile_id:id,model_id:algorithm,bits,memory_log2_bits:12,
  computation_model_id:computation,success_lower_bound:'1/2',success_status:'conditional-analytical-bound'});
const packet = () => ({format:'crucible-note-justifications-v1',scheme_id:'ram-fixture',version:'v1',
  sections:{security:pin('security'),correctness:pin('correctness')},
  graph_models:{
    'ram-one':{metric:'ram',source:pin('attack-one'),assumptions:[
      {label:'A-10',status:'unproved',profile_ids:['p1'],source:pin('premise-ten')},
      {label:'A-2',status:'model-condition',profile_ids:['p1'],stages:[3],source:pin('premise-two')},
      {label:'A-3',status:'unproved',profile_ids:['p3'],source:pin('premise-three')},
      {label:'A-1',status:'incomplete',profile_ids:['p1'],source:pin('scheme-gap')},
      {label:'A-99',status:'unproved',profile_ids:[],source:pin('out-of-scope')}],},
    'ram-two':{metric:'ram',source:pin('attack-two'),assumptions:[
      {label:'A-3',status:'unproved',profile_ids:['p3'],source:pin('premise-three')}]}},
  attack_comparison:{format:'crucible-ram-attack-comparison-v1',scheme_id:'ram-fixture',version:'v1',
    computation_model_id:computation,analysis_sha256:'b'.repeat(64),
    explanation:'Baseline upper bounds; faster attacks may exist.',models:[model('ram-one'),model('ram-two')],
    estimates:[point('p1','ram-one',31),point('p3','ram-two',77)]}});
async function boot({data=packet(),s=scheme(),enabled=true}={}) {
  const app=await createApp({schemes:[s],hash:'#about',responder:(url,options,envelope)=>{
    if(url.pathname==='/api/session')return envelope({user:null,csrf_token:'fixture-csrf',
      capabilities:{ram_attack_comparison:enabled,curated_notes:false}});
    if(url.pathname==='/api/protocol/note-justifications')return data;
  }});
  vm.runInContext(graphHelper,app.sandbox,{filename:'graph-points.js'});
  await app.navigate('#scheme/ram-fixture/v1');await flush();
  return app;
}
const chart = app => app.$('#overview-chart-security');
const points = app => chart(app).querySelectorAll('.chart-point').map(p=>[p.dataset.profileId,p.dataset.value]);
const switchMetric = async(app,metric)=>{
  chart(app).closest('.overview-card').querySelector('[data-overview-metric="'+metric+'"]').click();await flush();
};
const sourceLinks = app => chart(app).querySelector('.chart-point-readout').querySelectorAll('a');
function graph(app) {
  const svg=chart(app).querySelector('svg');
  svg.getBoundingClientRect=()=>({left:0,top:0,width:480,height:240});
  return {svg,event:(type,stage)=>svg.dispatchEvent({type,clientX:48+(stage-1)*208.5,clientY:100,pointerType:'mouse'})};
}
let passed=0;
async function test(name,fn){await fn();console.log('PASS '+name);passed++;}
(async()=>{
  await test('RAM is the enabled default and never substitutes frozen or quantum numbers',async()=>{
    const app=await boot();
    assert.equal(app.$('#overview-title-security').textContent,'Classical attack work');
    assert.equal(app.$('#overview-badge-security').textContent,'Baseline bound');
    assert.deepEqual(points(app),[['p1','31'],['p3','77']]);
    assert.match(chart(app).querySelector('svg').textContent,/log2 bit operations/);
    assert.equal(chart(app).querySelector('details'),null, 'The local comparison opens the exact note instead of a chart drawer');
    assert.equal(chart(app).querySelector('tbody'),null);
    const selectors=chart(app).closest('.overview-card').querySelectorAll('[data-overview-metric]');
    assert.deepEqual(selectors.map(b=>[b.dataset.overviewMetric,b.textContent]),[['ram','Classical'],['quantum','Quantum']]);
    assert.doesNotMatch(chart(app).textContent,/Model and values/);
    await switchMetric(app,'quantum');assert.deepEqual(points(app),[['p3','17']]);
    await switchMetric(app,'ram');assert.deepEqual(points(app),[['p1','31'],['p3','77']]);
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/protocol/note-justifications').length,1);
  });
  await test('disabled capability keeps the legacy display and exposes no RAM toggle',async()=>{
    const app=await boot({enabled:false});
    assert.deepEqual(points(app),[['p1','201'],['p2','202'],['p3','203']]);
    assert.equal(chart(app).closest('.overview-card').querySelector('[data-overview-metric="ram"]'),null);
    assert.equal(app.sandbox.securityReference(scheme(),scheme().profiles[0],'ram'),null);
  });
  await test('withdrawn justifications retain visibly qualified historical values only with an exact claim pin',async()=>{
    const data=packet(), model=data.graph_models['ram-one'];
    const {note_section,...source}=pin('unused');
    model.source=null;model.assumptions=[];
    model.presentation={target_id:'scheme-ram-one',current_status:'withdrawn',numeric_curve_unchanged:true,conclusion_source:{...source,statement:'attack-claim'}};
    const app=await boot({data});
    assert.deepEqual(points(app),[['p1','31'],['p3','77']]);
    assert.equal(app.$('#overview-badge-security').textContent,'Partly unsupported');
    assert.match(chart(app).textContent,/recorded values remain for reference/);
    for(const mutate of [d=>delete d.graph_models['ram-one'].presentation.conclusion_source,d=>d.graph_models['ram-one'].presentation.numeric_curve_unchanged=false]) {
      const bad=structuredClone(data);mutate(bad);const invalid=await boot({data:bad});
      assert.deepEqual(points(invalid),[['p3','77']]);
    }
  });
  await test('release, format, computation identity and analysis pin mismatches fail closed',async()=>{
    for(const mutate of [
      d=>d.version='v0',d=>d.scheme_id='other',d=>d.format='future-note-format',
      d=>d.attack_comparison.version='v0',d=>d.attack_comparison.scheme_id='other',
      d=>d.attack_comparison.format='future-ram-format',
      d=>d.attack_comparison.computation_model_id='unit-cost-arithmetic',
      d=>d.attack_comparison.analysis_sha256='not-a-pin',
      d=>delete d.attack_comparison,
    ]){
      const data=packet();mutate(data);const app=await boot({data});
      assert.deepEqual(points(app),[],'A malformed RAM payload must not fall back to legacy values');
      assert.equal(chart(app).querySelector('.chart-series'),null);
    }
  });
  await test('only unambiguous finite values with the common metric and success contract are plotted',async()=>{
    for(const mutate of [
      d=>d.estimates[0].computation_model_id='other',
      d=>d.estimates[0].success_lower_bound='1/100',
      d=>d.models[0].computation_model_id='other',
      d=>d.models[0].unit='log2 Core-SVP work',
      d=>d.models[0].label='',
      d=>d.estimates[0].profile_id='nearby-but-different',
      d=>d.estimates[0].model_id='missing',
      d=>d.models.push({...d.models[0]}),
      d=>d.estimates.push({...d.estimates[0]}),
      ...[NaN,Infinity,-1,'31',null].map(value=>d=>d.estimates[0].bits=value),
    ]){
      const data=packet();mutate(data.attack_comparison);const app=await boot({data});
      assert.deepEqual(points(app),[['p3','77']]);
    }
  });
  await test('a missing configuration remains a real gap despite its frozen reference',async()=>{
    const app=await boot();
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','M']);
    const interaction=graph(app);interaction.event('click',2);
    assert.match(chart(app).querySelector('.chart-point-readout').textContent,/Stage 2.*No estimate/);
    assert.equal(sourceLinks(app).length,0);
    assert.equal(chart(app).querySelector('.chart-selection-guide').getAttribute('visibility'),'hidden');
  });
  await test('an absent or malformed exact attack-specification pin cannot support a plotted point',async()=>{
    for(const mutate of [
      d=>delete d.graph_models['ram-one'],
      d=>delete d.graph_models['ram-one'].source,
      d=>d.graph_models['ram-one'].source.content_sha256='wrong',
      d=>d.graph_models['ram-one'].source.revision=0,
      d=>d.graph_models['ram-one'].source.revision=-1,
      d=>d.graph_models['ram-one'].source.id={},
      d=>d.graph_models['ram-one'].metric='classical',
    ]){
      const data=packet();mutate(data);const app=await boot({data});
      assert.deepEqual(points(app),[['p3','77']]);
    }
  });
  await test('compatible algorithms may form one envelope without mixing computation models',async()=>{
    const data=packet();data.attack_comparison.estimates.splice(1,0,point('p2','ram-two',55));
    const app=await boot({data});
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','L','L']);
    assert.deepEqual(points(app),[['p1','31'],['p2','55'],['p3','77']]);
  });
  await test('selected points retain exact attack citations and only their scoped premises',async()=>{
    const app=await boot();const interaction=graph(app);interaction.event('click',1);
    assert.deepEqual(sourceLinks(app).map(a=>a.textContent.replace(/↗$/, '')),['Attack specification','A-2','A-10']);
    const link=sourceLinks(app)[0];
    assert.equal(link.querySelector('[aria-hidden="true"]').textContent, '↗');
    const query=new URLSearchParams(new URL(link.href,'https://example.org/').hash.slice('#notebook?'.length));
    assert.equal(query.get('note'),pin('attack-one').id);
    assert.equal(query.get('revision'),'4');
    assert.equal(query.get('sha256'),'a'.repeat(64));
    assert.equal(query.get('note_section'),'attack-one');
    const readout=chart(app).querySelector('.chart-point-readout');
    assert.match(readout.textContent,/Stage 1/);
    assert.match(readout.textContent,/Work/); assert.match(readout.textContent,/Memory/);
    const exponents=readout.querySelectorAll('sup').map(el=>el.textContent);
    assert.deepEqual(exponents,['31','12']);
    assert.match(readout.textContent,/bit ops/); assert.match(readout.textContent,/bits/);
    assert.doesNotMatch(readout.textContent,/2\^/,'Exponents are presented as real superscripts');
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('hover preserves the selected specification and premises until a deliberate new selection',async()=>{
    const app=await boot();const interaction=graph(app);interaction.event('click',1);
    const before=sourceLinks(app)[0];before.focus();
    interaction.event('pointermove',3);interaction.svg.dispatchEvent({type:'blur'});interaction.event('pointerleave',3);
    assert.equal(sourceLinks(app)[0],before);
    assert.equal(app.document.activeElement,before);
    assert.deepEqual(sourceLinks(app).map(a=>a.textContent.replace(/↗$/, '')),['Attack specification','A-2','A-10']);
    assert.match(chart(app).querySelector('.chart-point-readout').textContent,/Stage 1/);
    interaction.event('click',3);
    assert.deepEqual(sourceLinks(app).map(a=>a.textContent.replace(/↗$/, '')),['Attack specification','A-3']);
    assert.match(sourceLinks(app)[0].href,/note_section=attack-two/);
    assert.match(chart(app).querySelector('.chart-point-readout').textContent,/Stage 3/);
  });
  await test('compact exponent formatting preserves upper bounds by rounding upward',async()=>{
    const data=packet();
    data.attack_comparison.estimates[0].bits=31.01;
    data.attack_comparison.estimates[0].memory_log2_bits=12.01;
    const app=await boot({data});graph(app).event('click',1);
    const exponents=chart(app).querySelector('.chart-point-readout').querySelectorAll('sup').map(el=>el.textContent);
    assert.deepEqual(exponents,['31.1','12.1']);
    assert.deepEqual(points(app),[['p1','31.01'],['p3','77']], 'Display precision must not alter the plotted data');
  });
  await test('an assumption-free attack still has its specification and no empty assumption label',async()=>{
    const data=packet();data.graph_models['ram-one'].assumptions=[];
    const app=await boot({data});graph(app).event('click',1);
    assert.deepEqual(sourceLinks(app).map(a=>a.textContent.replace(/↗$/, '')),['Attack specification']);
    assert.equal(chart(app).querySelector('.chart-point-readout').querySelector('.chart-assumption-link'),null);
  });
  console.log(`${passed} RAM comparison frontend checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

// Exact-release evidence may fill missing points, never invent a security curve.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile = n => ({id:'p'+n,profile_id:'p'+n,instance_size:n,name:'Stage '+n,parameters:{n}});
const model = (id='reference-classical') => ({id,label:'Published model '+id,unit:'log2 Core-SVP work',
  scope:'Only the named underlying lattice parameters.',assumptions:['Heuristic sieving with free memory.'],
  limitations:['Not full-KEM security bits or a proven lower bound.'],
  sources:[{title:'Primary source',url:'https://example.org/paper'},{title:'Unsafe link',url:'javascript:alert(1)'}]});
const metric = (id,bits) => ({explanation:'Only the endpoint is assessed; reduced configurations have no calibrated estimate.',
  models:[model(id)],estimates:[{profile_id:'p6',model_id:id,bits}]});
const scheme = () => ({id:'fixture',version:'v1',name:'Reference fixture',profiles:[1,2,3,4,5,6].map(profile),
  protocol:{},size_parameter:{},next_profile_id:'p1',security_references:{
    format:'crucible-security-references-v1',scheme_id:'fixture',version:'v1',revision:1,
    metrics:{classical:metric('reference-classical',118),quantum:metric('reference-quantum',107)}}});
const boot = s => createApp({schemes:[s],hash:'#scheme/fixture/v1'});
const chart = app => app.$('#overview-chart-security');
const points = app => chart(app).querySelectorAll('.chart-point');
const switchMetric = async(app,name) => {chart(app).closest('.overview-card').querySelector('[data-overview-metric="'+name+'"]').click();await flush();};
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('endpoint references stay isolated and classical/quantum models remain distinct',async()=>{
    const app=await boot(scheme());
    assert.deepEqual(points(app).map(p=>[p.dataset.profileId,p.dataset.value]),[['p6','118']]);
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M']);
    assert.equal(points(app)[0].getAttribute('r'),'4');
    assert.equal(app.$('#overview-title-security').textContent,'Classical attack reference');
    assert.match(chart(app).textContent,/log2 Core-SVP work/);
    assert.match(chart(app).querySelector('.overview-chart-note').textContent,/1 of 6 configurations/);
    assert.match(chart(app).textContent,/not full-KEM security bits/);
    assert.equal(chart(app).querySelector('tbody').querySelectorAll('tr').filter(r=>r.textContent.includes('Not reported')).length,5);
    chart(app).querySelector('details').open=true;
    await switchMetric(app,'quantum');
    assert.deepEqual(points(app).map(p=>[p.dataset.profileId,p.dataset.value]),[['p6','107']]);
    assert.equal(app.$('#overview-title-security').textContent,'Quantum attack reference');
    assert.match(chart(app).textContent,/Published model reference-quantum/);
    assert.equal(chart(app).querySelector('details').open,true);
    assert.doesNotMatch(chart(app).querySelector('svg').textContent,/Estimated security \(bits\)/);
  });
  await test('frozen classical values take precedence over supplemental values without leaking into quantum',async()=>{
    const s=scheme();s.initial_security_model={id:'frozen',name:'Frozen reference',unit:'log2 group operations'};
    s.profiles[5].initial_security_estimate={model_id:'frozen',bits:141.912};
    const app=await boot(s);
    assert.deepEqual(points(app).map(p=>p.dataset.value),['141.912']);
    assert.equal(app.sandbox.securityReference(s,s.profiles[5],'classical').model.id,'frozen');
    await switchMetric(app,'quantum');assert.deepEqual(points(app).map(p=>p.dataset.value),['107']);
    s.profiles[5].initial_security_estimate.model_id='wrong-frozen-model';
    assert.equal(app.sandbox.securityReference(s,s.profiles[5],'classical').bits,118);
  });
  await test('other release identities and profiles never inherit a nearby reference',async()=>{
    for(const mutate of [s=>s.security_references.scheme_id='other',s=>s.security_references.version='v0',
      s=>s.security_references.format='future-format',s=>s.security_references.metrics.classical.estimates[0].profile_id='p60']){
      const s=scheme();mutate(s);const app=await boot(s);
      assert.equal(points(app).length,0);assert.equal(chart(app).querySelector('.chart-series'),null);
    }
  });
  await test('nonfinite, negative, nonnumeric, missing and ambiguous models are never plotted',async()=>{
    for(const value of [NaN,Infinity,-Infinity,-1,'118',null]){
      const s=scheme();s.security_references.metrics.classical.estimates[0].bits=value;
      const app=await boot(s);assert.equal(points(app).length,0,String(value));
    }
    for(const mutate of [m=>m.estimates[0].model_id='missing',m=>m.models=[],
      m=>m.estimates.push({...m.estimates[0]}),m=>m.models.push({...m.models[0]})]){
      const s=scheme();mutate(s.security_references.metrics.classical);
      const app=await boot(s);assert.equal(points(app).length,0);
    }
  });
  await test('incomplete supplemental model contracts fail closed before chart rendering',async()=>{
    const app=await boot(scheme());
    for(const mutate of [
      m=>delete m.models[0].unit,m=>m.models[0].unit='',m=>delete m.models[0].label,
      m=>{delete m.models[0].id;delete m.estimates[0].model_id;},
      m=>{m.models[0].id='';m.estimates[0].model_id='';},
      m=>m.models={},m=>m.estimates={},m=>m.models=[null],m=>m.estimates=[null],
    ]){
      const s=scheme();mutate(s.security_references.metrics.classical);
      assert.equal(app.sandbox.securityReference(s,s.profiles[5],'classical'),null);
    }
  });
  await test('lines never bridge missing configurations or different attack models',async()=>{
    const s=scheme();const m=s.security_references.metrics.classical;
    m.models=[model('one'),model('two')];
    m.estimates=[['p1','one',10],['p2','one',11],['p3','two',12],['p4','two',13],['p6','two',14]]
      .map(([profile_id,model_id,bits])=>({profile_id,model_id,bits}));
    const app=await boot(s);
    assert.deepEqual(points(app).map(p=>p.dataset.profileId),['p1','p2','p3','p4','p6']);
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','L','M','L','M']);
    assert.match(chart(app).textContent,/5 of 6 configurations have a reference/);
    assert.match(chart(app).textContent,/Published model one/);assert.match(chart(app).textContent,/Published model two/);
  });
  await test('qualitative quantum assessments show sourced explanations without zero-value points',async()=>{
    const s=scheme();s.security_references.metrics.quantum={
      explanation:'Shor provides a polynomial-time route; no configuration-specific resource estimate is published.',
      models:[{...model('shor'),label:'Polynomial-time recovery route',unit:'Qualitative; no numerical work estimate',
        sources:[{title:'Shor primary paper',url:'https://arxiv.org/abs/quant-ph/9508027v2'}]}],estimates:[]};
    const app=await boot(s);await switchMetric(app,'quantum');
    assert.equal(points(app).length,0);assert.equal(chart(app).querySelector('.chart-series'),null);
    assert.match(chart(app).textContent,/polynomial-time route/);
    assert.match(chart(app).textContent,/no configuration-specific resource estimate/);
    assert.equal(chart(app).querySelectorAll('a').find(a=>a.textContent==='Shor primary paper').href,'https://arxiv.org/abs/quant-ph/9508027v2');
    assert.match(chart(app).querySelector('summary').textContent,/Model and values/);
  });
  await test('model text stays text, unsafe source URLs are discarded, and assumptions remain reachable',async()=>{
    const s=scheme();s.security_references.metrics.classical.models[0].label='<script>model</script>';
    const app=await boot(s);
    assert.equal(chart(app).querySelector('script'),null);
    assert.match(chart(app).textContent,/<script>model<\/script>/);
    assert.equal(chart(app).querySelectorAll('a').some(a=>a.href.startsWith('javascript:')),false);
    assert.equal(chart(app).querySelectorAll('a').find(a=>a.textContent==='Primary source').href,'https://example.org/paper');
    assert.equal(chart(app).querySelectorAll('a').find(a=>a.textContent.includes('assumptions')).href,'#scheme/fixture/v1/assumptions');
  });
  await test('an exact supporting note stays inside the collapsed security disclosure',async()=>{
    const s=scheme();s.evidence_note={id:'curated:security:fixture:v1',revision:2,content_sha256:'a'.repeat(64)};
    const app=await boot(s);
    const links=chart(app).querySelectorAll('a').filter(a=>a.textContent==='Supporting research note →');
    assert.equal(links.length,1);
    const disclosure=links[0].closest('details');assert.ok(disclosure);assert.equal(disclosure.open,false);
    const url=new URL(links[0].href,'https://example.org/');
    assert.ok(url.hash.startsWith('#notebook?'));
    const query=new URLSearchParams(url.hash.slice('#notebook?'.length));
    assert.equal(query.get('note'),s.evidence_note.id);assert.equal(query.get('revision'),'2');
    assert.equal(query.get('sha256'),s.evidence_note.content_sha256);
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
    await switchMetric(app,'quantum');
    assert.equal(chart(app).querySelectorAll('a').filter(a=>a.textContent==='Supporting research note →').length,1);
    assert.equal(chart(app).querySelector('details').open,false);
  });
  await test('unbound or malformed supporting-note metadata does not create a misleading revision link',async()=>{
    for(const note of [null,{id:'curated:x',revision:1},
      {id:'curated:x',revision:0,content_sha256:'a'.repeat(64)},
      {id:'curated:x',revision:1,content_sha256:'not-a-hash'},
      {id:'https://example.org/x',revision:1,content_sha256:'a'.repeat(64)}]){
      const s=scheme();s.evidence_note=note;const app=await boot(s);
      assert.equal(chart(app).querySelectorAll('a').some(a=>a.textContent==='Supporting research note →'),false);
      assert.equal(points(app).length,1,'Missing optional provenance does not destroy the identified model point');
    }
  });
  console.log(`${passed} security reference frontend checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

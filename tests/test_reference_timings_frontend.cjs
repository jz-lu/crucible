// Reference timing reports are separate, version-bound evidence; missing runs are not zero.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile = n => ({id:'p'+n, profile_id:'p'+n, instance_size:n, name:'Stage '+n, parameters:{n}});
const scheme = (id='alpha', version='1') => ({id,version,name:'Fixture '+id,profiles:[1,2,3,4].map(profile),next_profile_id:'p1',protocol:{},size_parameter:{}});
const method = {id:'reference-operations-v1',version:1,summary:'Fixed reference operations.',workload:'Honest independent invocations.',
  statistics:'Median and first/third quartiles.',includes:['Serialization'],excludes:['Network'],correctness:'Agreement checked.',
  sources:[{title:'Official method',url:'https://example.org/method'},{title:'Unsafe source',url:'javascript:alert(1)'}]};
const measured = (id,seconds,operation='keygen') => ({profile_id:id,operation,status:'measured',samples:9,median_seconds:seconds,q1_seconds:seconds/2,q3_seconds:seconds*2,failure_count:0});
const report = (id='alpha',version='1') => ({id:'pilot-test',scheme_id:id,scheme_version:version,status:'local-pilot',measured_at:'2026-10-17T12:00:00Z',method_id:method.id,
  implementation:{label:'<script>untrusted label</script>',language:'Python',performance_level:'Reference implementation',artifact_sha256:'a'.repeat(64),source_sha256:'b'.repeat(64)},
  environment:{cpu:'Fixture CPU',os:'Fixture OS',runtime:'Python 3',compiler:'None',threads:1,cpu_affinity:'not pinned'},sampling:{warmups:2,trials:9,profile_timeout_seconds:15},notes:['Independent reproduction pending.'],
  results:[measured('p1',.0002),{profile_id:'p2',operation:'keygen',status:'partial',samples:3,median_seconds:0,q1_seconds:0,q3_seconds:0,failure_count:1},measured('p3',.0008),
    {profile_id:'p4',operation:'keygen',status:'timeout',samples:0,failure_count:1},measured('p1',1.2,'encapsulate')]});
const deferred = () => {let resolve; const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const fetches = app => app.requests.filter(r=>r.url.pathname==='/api/protocol/benchmarks');
const chart = app => app.$('#overview-chart-computation');
const points = app => chart(app).querySelectorAll('.chart-point');
const metric = (app,name) => app.$('#overview-graphs').querySelector('[data-overview-metric="'+name+'"]');
async function selectEarlierTimings(app) {
  if (app.$('#overview-workspace').hidden) return;
  const select = chart(app).querySelector('select[aria-label="Report"]');
  if (select) { select.value='legacy'; select.dispatchEvent({type:'change'}); await flush(); }
}
const boot = async (options={}) => {
  const app=await createApp({schemes:[scheme()],hash:'#scheme/alpha/1',...options}), navigate=app.navigate;
  // All checks below exercise the legacy reference timings by explicit choice.
  await selectEarlierTimings(app);
  app.navigate=async hash=>{await navigate(hash);await selectEarlierTimings(app);};
  return app;
};
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('catalogue and denied beta sessions never fetch timing reports',async()=>{
    const catalogue=await boot({hash:'#challenge'});assert.equal(fetches(catalogue).length,0);
    const denied=await boot({responder:(url,_,envelope)=>url.pathname==='/api/session'?envelope({user:null,access:{mode:'beta',allowed:false,status:'anonymous'}}):undefined});
    assert.equal(fetches(denied).length,0);assert.equal(denied.requests.some(r=>r.url.pathname==='/api/schemes'),false);
  });
  await test('current resource views and session polls do not fetch unselected earlier timings',async()=>{
    const app=await createApp({schemes:[scheme()],hash:'#scheme/alpha/1',responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:report()}:undefined});
    assert.equal(fetches(app).length,0);
    await app.sandbox.synchronizeSession();await flush();assert.equal(fetches(app).length,0);
    const select=card=>app.$('#overview-chart-'+card).querySelector('select[aria-label="Report"]');
    select('public').value='legacy';select('public').dispatchEvent({type:'change'});await flush();assert.equal(fetches(app).length,0);
    await selectEarlierTimings(app);assert.equal(fetches(app).length,1);assert.equal(points(app).length,2);
    select('computation').value='current';select('computation').dispatchEvent({type:'change'});await flush();
    app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;
    await app.sandbox.synchronizeSession();await flush();assert.equal(fetches(app).length,1);
    await selectEarlierTimings(app);assert.equal(fetches(app).length,2);assert.equal(points(app).length,2);
  });
  await test('measured medians plot in sensible units with gaps, provenance and accessible quartiles',async()=>{
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:report()}:undefined});
    assert.equal(fetches(app).length,1);assert.equal(fetches(app)[0].url.searchParams.get('version'),'1');
    assert.deepEqual(points(app).map(p=>[p.dataset.profileId,p.dataset.value]),[['p1','0.2'],['p3','0.8']]);
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','M'],'Incomplete configurations break timing lines');
    assert.match(chart(app).querySelector('desc').textContent,/milliseconds/);
    assert.equal(app.$('#overview-badge-computation').textContent,'Earlier site reference');
    assert.match(chart(app).textContent,/not independently reproduced/);assert.match(chart(app).textContent,/Fixture CPU/);
    assert.match(chart(app).textContent,/CPU affinitynot pinned/);assert.match(chart(app).textContent,/Warmups per configuration2/);
    assert.match(chart(app).textContent,/Requested measured trials9/);assert.match(chart(app).textContent,/Configuration time budget15 s/);
    assert.match(chart(app).textContent,/<script>untrusted label<\/script>/);assert.equal(chart(app).querySelector('script'),null);
    const table=chart(app).querySelector('table');assert.match(table.textContent,/SamplesMedianMiddle 50%/);
    assert.match(table.textContent,/0.1 ms–0.4 ms/);assert.match(table.textContent,/Partial run · 1 failed/);assert.match(table.textContent,/Timed out/);
    assert.equal(chart(app).querySelector('.reference-timing-coverage').textContent,'4 of 4 configurations attempted. 2 complete · 2 incomplete.');
    assert.equal(table.querySelector('th').scope,'col');assert.equal(chart(app).querySelector('.overview-values-scroll').tabIndex,0);
    assert.equal(chart(app).querySelectorAll('a').some(a=>a.href.startsWith('javascript:')),false);
    chart(app).querySelector('details').open=true;const button=metric(app,'encapsulate');button.focus();button.click();await flush();
    assert.equal(fetches(app).length,1);assert.equal(chart(app).querySelector('details').open,true);assert.equal(app.document.activeElement,button);
    assert.deepEqual(points(app).map(p=>p.dataset.value),['1.2']);assert.match(chart(app).querySelector('desc').textContent,/seconds/);
    assert.match(chart(app).textContent,/1 of 4 configurations attempted. 1 complete · 3 not attempted/);
    assert.equal(chart(app).querySelector('tbody').querySelectorAll('tr').length,1);
    metric(app,'decapsulate').click();await flush();assert.equal(points(app).length,0);assert.match(table.textContent,/Partial run/);
    assert.match(chart(app).textContent,/0 of 4 configurations attempted. 0 complete · 4 not attempted/);
  });
  await test('full attempted coverage does not label failed or timed-out configurations as complete',async()=>{
    const fullScheme={...scheme(),profiles:Array.from({length:50},(_,i)=>profile(i+1))};
    const fullReport=report();fullReport.results=fullScheme.profiles.map(p=>measured(p.id,.001));
    fullReport.results[48]={profile_id:'p49',operation:'keygen',status:'failed',samples:0,failure_count:1};
    fullReport.results[49]={profile_id:'p50',operation:'keygen',status:'timeout',samples:2,failure_count:1};
    const app=await boot({schemes:[fullScheme],responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:fullReport}:undefined});
    assert.equal(chart(app).querySelector('.reference-timing-coverage').textContent,'50 of 50 configurations attempted. 48 complete · 2 incomplete.');
    assert.equal(chart(app).querySelector('tbody').querySelectorAll('tr').length,50);
    assert.equal(points(app).length,48);assert.match(chart(app).querySelector('table').textContent,/Run failed/);
    assert.match(chart(app).querySelector('table').textContent,/Timed out/);
  });
  await test('successful-key-generation samples plot as conditional points without hiding aborts or joining complete-run lines',async()=>{
    const data=report();
    data.results[1]={...measured('p2',.0005),status:'partial',samples:8,failure_count:1,
      sample_scope:'successful-key-generation',outcomes:{warmup:{abort:0},measured:{abort:1}}};
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:data}:undefined});
    assert.deepEqual(points(app).map(p=>p.dataset.profileId),['p1','p2','p3']);
    const point=chart(app).querySelector('.chart-point-conditional');
    assert.equal(point.dataset.profileId,'p2');assert.equal(point.dataset.value,'0.5');
    assert.match(point.querySelector('title').textContent,/8 successful exchanges only; 1 measured-trial key-generation aborts; 0 warmup aborts/);
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','M']);
    assert.match(chart(app).querySelector('table').textContent,/Successful exchanges only.*1 measured-trial key-generation aborts.*0.5 ms/);
    assert.match(chart(app).querySelector('.chart-conditional-note').textContent,/conditional on successful key generation/);
    assert.match(chart(app).querySelector('.chart-coverage-note').textContent,/1 incomplete run left empty/);
    assert.match(chart(app).querySelector('.reference-timing-coverage').textContent,/2 complete · 2 incomplete/);
    data.results[1].sample_scope='partial-operations';
    const uncertain=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:data}:undefined});
    assert.equal(points(uncertain).length,2,'Other partial operation data stays unplotted');
  });
  await test('all 80 measured configurations remain visible for every timing operation',async()=>{
    const fullScheme={...scheme(),profiles:Array.from({length:80},(_,i)=>profile(i+1))};
    const fullReport=report();fullReport.results=['keygen','encapsulate','decapsulate'].flatMap(operation=>fullScheme.profiles.map(p=>measured(p.id,p.instance_size/1000,operation)));
    const app=await boot({schemes:[fullScheme],responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:fullReport}:undefined});
    for(const operation of ['keygen','encapsulate','decapsulate']){
      metric(app,operation).click();await flush();
      assert.equal(points(app).length,80);
      assert.deepEqual(points(app).map(p=>p.dataset.profileId),fullScheme.profiles.map(p=>p.id));
      assert.equal(chart(app).querySelector('.chart-series').getAttribute('d').match(/L/g).length,79,'Every adjacent measured configuration is connected');
      assert.equal(chart(app).querySelector('.reference-timing-coverage').textContent,'80 of 80 configurations attempted. 80 complete.');
    }
    assert.equal(fetches(app).length,1,'Operation switches reuse the same complete report');
  });
  await test('timing lines break at missing, partial and failed runs while named parameter sets stay discrete',async()=>{
    const configurations={...scheme(),profiles:Array.from({length:9},(_,i)=>profile(i+1))};
    const mixedReport={...report(),results:[measured('p1',.001),measured('p2',.002),
      {profile_id:'p4',operation:'keygen',status:'partial',samples:2,failure_count:0},measured('p5',.005),
      {profile_id:'p6',operation:'keygen',status:'failed',samples:0,failure_count:1},measured('p7',.007),measured('p8',.008),
      {profile_id:'p9',operation:'keygen',status:'timeout',samples:0,failure_count:1}]};
    const responder=url=>url.pathname==='/api/protocol/benchmarks'?{method,report:mixedReport}:undefined;
    const app=await boot({schemes:[configurations],responder});
    assert.deepEqual(points(app).map(p=>p.dataset.profileId),['p1','p2','p5','p7','p8']);
    assert.deepEqual(chart(app).querySelector('.chart-series').getAttribute('d').match(/[ML]/g),['M','L','M','M','L']);
    assert.match(chart(app).querySelector('.overview-chart-note').textContent,/Lines join adjacent measured configurations/);
    const named=await boot({schemes:[{...configurations,selection_kind:'parameter-set'}],responder});
    assert.equal(points(named).length,5);assert.equal(chart(named).querySelector('.chart-series'),null);
  });
  await test('revisiting an expired report fetches newly published values without refetching fresh reports',async()=>{
    let current=null;
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:current}:undefined});
    assert.equal(points(app).length,0);
    await app.navigate('#challenge');await app.navigate('#scheme/alpha/1');
    assert.equal(fetches(app).length,1);
    app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;current=report();
    await app.navigate('#challenge');await app.navigate('#scheme/alpha/1');
    assert.equal(fetches(app).length,2);assert.equal(points(app).length,2);
  });
  await test('visible session polling refreshes expired timings once and preserves unchanged charts',async()=>{
    let current=report(),pending=null;
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?(pending?pending.promise:{method,report:current}):undefined});
    const originalSVG=chart(app).querySelector('svg');
    chart(app).querySelector('details').open=true;
    await app.sandbox.synchronizeSession();await flush();assert.equal(fetches(app).length,1);
    app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;
    await app.sandbox.synchronizeSession();await flush();
    assert.equal(fetches(app).length,2);assert.equal(chart(app).querySelector('svg'),originalSVG);
    app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;pending=deferred();
    await app.sandbox.synchronizeSession();await flush();
    assert.equal(fetches(app).length,3);assert.equal(chart(app).querySelector('svg'),originalSVG,'Refresh keeps the previous chart visible while pending');
    await app.sandbox.synchronizeSession();await flush();
    metric(app,'encapsulate').click();await flush();assert.equal(fetches(app).length,3,'Pending requests and metric changes do not duplicate reads');
    current={...report(),results:[measured('p2',.003,'encapsulate')]};pending.resolve({method,report:current});await flush();
    assert.deepEqual(points(app).map(p=>p.dataset.profileId),['p2']);assert.equal(chart(app).querySelector('details').open,true);
    app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;app.document.hidden=true;
    await app.sandbox.synchronizeSession();await flush();assert.equal(fetches(app).length,3,'A hidden page does not refresh timings');
  });
  await test('a late cache refresh cannot cross account boundaries',async()=>{
    let pending=null;
    const app=await boot({user:{id:'one',handle:'one'},responder:url=>url.pathname==='/api/protocol/benchmarks'?(pending?pending.promise:{method,report:report()}):undefined});
    pending=deferred();app.sandbox.referenceTimingState(scheme()).checkedAt-=60001;
    const oldRequest=app.sandbox.loadReferenceTimings(scheme());await flush();
    app.sandbox.setSession(app.envelope({user:{id:'two',handle:'two'}}));
    const previous=pending;pending=null;
    await app.sandbox.loadReferenceTimings(scheme());await flush();
    previous.resolve({method,report:{...report(),results:[measured('p4',4)]}});await oldRequest;await flush();
    await selectEarlierTimings(app);
    assert.deepEqual(points(app).map(p=>p.dataset.profileId),['p1','p3']);
    assert.deepEqual(app.sandbox.referenceTimingState(scheme()).report.results.map(p=>p.profile_id),report().results.map(p=>p.profile_id));
  });
  await test('no report remains an explicit empty graph, with the shared method available',async()=>{
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:null}:undefined});
    assert.equal(points(app).length,0);assert.equal(chart(app).querySelector('.chart-series'),null);
    assert.match(chart(app).textContent,/No timings published/);assert.match(chart(app).textContent,/Fixed reference operations/);
    assert.match(chart(app).querySelector('table').textContent,/Not measured/);
  });
  await test('mismatched, incomplete or duplicated evidence never becomes plotted data',async()=>{
    for(const mutate of [r=>r.scheme_id='other',r=>r.scheme_version='old',r=>r.method_id='other',r=>r.implementation.artifact_sha256='bad',r=>{r.implementation.artifact_sha256=null;r.implementation.source_sha256='missing';},r=>r.results[0].q1_seconds=9,r=>r.results.push(r.results[0])]){
      const value=report();mutate(value);
      const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:value}:undefined});
      assert.equal(points(app).length,0);assert.match(chart(app).textContent,/Timings unavailable/);assert.equal(app.$('#overview-badge-computation').textContent,'Earlier site reference');
    }
    const sourceOnly=report();sourceOnly.implementation.artifact_sha256=null;
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?{method,report:sourceOnly}:undefined});
    assert.equal(points(app).length,2);assert.match(chart(app).textContent,/Source SHA-256/);
    assert.doesNotMatch(chart(app).textContent,/Implementation closure SHA-256/);
  });
  await test('loading, failure and explicit retry are distinct; retry preserves disclosure',async()=>{
    const wait=deferred();let count=0;
    const app=await boot({responder:url=>url.pathname==='/api/protocol/benchmarks'?(++count===1?wait.promise:{method,report:report()}):undefined});
    assert.doesNotMatch(chart(app).querySelector('svg').textContent,/Loading|Fetching/);
    assert.equal(app.$('#overview-badge-computation').textContent,'Earlier site reference');
    assert.equal(chart(app).querySelector('button').disabled,true);
    chart(app).querySelector('details').open=true;
    wait.resolve({method,report:{}});await flush();assert.match(chart(app).textContent,/Timings unavailable/);
    chart(app).querySelector('button').click();await flush();assert.equal(count,2);assert.equal(points(app).length,2);assert.equal(chart(app).querySelector('details').open,true);
  });
  await test('late reports cannot replace another scheme or release',async()=>{
    const wait=deferred();const app=await boot({schemes:[scheme(),scheme('beta','2')],responder:url=>url.pathname==='/api/protocol/benchmarks'?(url.searchParams.get('scheme_id')==='alpha'?wait.promise:{method,report:report('beta','2')}):undefined});
    await app.navigate('#scheme/beta/2');wait.resolve({method,report:report()});await flush();
    assert.match(chart(app).querySelector('caption').textContent,/Fixture beta.*release 2/);assert.equal(fetches(app).length,2);
    await app.navigate('#scheme/alpha/1');assert.match(chart(app).querySelector('caption').textContent,/Fixture alpha.*release 1/);assert.equal(fetches(app).length,2);
  });
  await test('access revocation clears reports and ignores a late private response',async()=>{
    const wait=deferred();const app=await boot({user:{id:'one',handle:'one'},responder:url=>url.pathname==='/api/protocol/benchmarks'?wait.promise:undefined});
    app.sandbox.setSession(app.envelope({user:null,access:{mode:'beta',allowed:false,status:'revoked'}}));
    wait.resolve({method,report:report()});await flush();
    assert.equal(app.$('#overview-graphs').childElementCount,0);assert.equal(app.$('#overview-workspace').hidden,true);
  });
  console.log(`${passed} reference timing frontend checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

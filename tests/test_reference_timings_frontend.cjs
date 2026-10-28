// Historical backend evidence is retained, but scheme pages have one resource UI.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const scheme = id => ({id, version:'1', name:'Fixture '+id, next_profile_id:'p1',
  profiles:[{id:'p1',profile_id:'p1',instance_size:1,parameters:{n:1}}],
  profile_metrics:{p1:{public_key_bytes:999,ciphertext_bytes:888}}});
const fetches = app => app.requests.filter(r => r.url.pathname === '/api/protocol/benchmarks');
(async () => {
  const app = await createApp({schemes:[scheme('alpha'),scheme('beta')],hash:'#scheme/alpha/1',
    responder:url => url.pathname === '/api/protocol/benchmarks' ? {report:{id:'must-not-be-requested'}} : undefined});
  const checkCards = () => {
    for (const [id, expected] of [['public',['public_key','fresh']],['cipher',[]],['computation',['warm','rss']]]) {
      const chart = app.$('#overview-chart-'+id), card = chart.closest('.overview-card');
      const measure = chart.querySelector('select[aria-label="Measure"]');
      assert.deepEqual(measure?.querySelectorAll('option').map(option=>option.value) || [],expected);
      assert.ok(expected.length <= 2, 'at most two distinct measures per card');
      assert.equal(chart.querySelectorAll('.chart-point').length,0,'old profile metadata never fills missing measurements');
      assert.equal(card.querySelector('.reference-timing-caption'),null);
      assert.equal(card.querySelector('.overview-metric-options'),null,'retired timing buttons are not merely hidden');
      assert.equal(chart.querySelector('select[aria-label="Report"]'),null);
      assert.equal(chart.querySelector('select[aria-label="Measurement profile"]'),null);
      assert.doesNotMatch(chart.textContent,/Earlier site reference|Local pilot|Reference implementation timings/);
    }
  };
  checkCards(); assert.equal(fetches(app).length,0);
  const computation = app.$('#overview-chart-computation');
  for (const operation of ['keygen','encapsulate','decapsulate']) {
    const select = computation.querySelector('select[aria-label="Operation"]');
    select.value = operation; select.dispatchEvent({type:'change'}); await flush();
    assert.equal(fetches(app).length,0);
  }
  const security = app.$('#overview-chart-security');
  const quantum = security.closest('.overview-card').querySelector('[data-overview-metric="quantum"]');
  assert.ok(quantum,'classical/quantum security controls remain separate from resource simplification');
  quantum.click(); await flush();
  assert.equal(app.$('#overview-title-security').textContent,'Quantum security estimate');
  await app.sandbox.synchronizeSession(); await flush(); checkCards();
  await app.navigate('#challenge'); await app.navigate('#scheme/beta/1'); checkCards();
  assert.equal(fetches(app).length,0,'navigation and session polling cannot revive retired reference timings');

  // A missing resource renderer must not silently expose the former graph path.
  app.window.CrucibleResources = null;
  app.$('#overview-chart-public').replaceChildren();
  app.sandbox.renderOverviewChart(scheme('beta'),'public');
  assert.equal(app.$('#overview-chart-public').childElementCount,0);
  assert.equal(fetches(app).length,0);

  const denied = await createApp({schemes:[scheme('alpha')],hash:'#scheme/alpha/1',
    responder:(url,_,envelope)=>url.pathname==='/api/session'?envelope({user:null,access:{mode:'beta',allowed:false,status:'anonymous'}}):undefined});
  assert.equal(fetches(denied).length,0);
  assert.equal(denied.requests.some(r=>r.url.pathname==='/api/schemes'),false);
  console.log('PASS resource-only scheme pages, at most two measures, preserved security controls, no retired selectors or legacy reads, and access boundaries');
})().catch(error=>{console.error(error);process.exitCode=1;});

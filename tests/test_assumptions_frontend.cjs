const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile={id:'p1',profile_id:'p1',instance_size:1,name:'One',shared_secret_bytes:32,parameters:{n:8},correctness:{failure_bound:'0'}};
const scheme=(id='alpha',version='1')=>({id,version,name:'Scheme '+id,profiles:[profile],protocol:{},previous_releases:[]});
const sheet=(id='alpha',version='1')=>({scheme_id:id,scheme_version:version,curation_status:'draft',completeness:'partial',claims:[{id:'hardness',title:'Noisy equations hide the secret.',role:'hardness',supports:['security'],precise_statement:'A <script>statement</script> rendered as text.',scope:'This exact distribution.',challenge:'Find a faster distinguisher.',limitations:['No complete KEM reduction.'],sources:[{title:'Paper',url:'https://example.org/paper'},{title:'Unsafe',url:'javascript:alert(1)'}]},{id:'agreement',title:'Honest exchanges agree.',role:'derived',supports:['correctness'],precise_statement:'An algebraic identity.',scope:'Valid generated keys.',sources:[]}],limitations:['Incomplete argument; no certification.']});
const grouped=()=>{
 const value=sheet();
 value.presentation={lead:'Secrecy and honest agreement have different requirements.',sections:[
  {id:'premises',title:'Premises used by the analysis',description:'These are not claimed independent.',claim_ids:['noise-model']},
  {id:'results',title:'What follows',description:'Conditional conclusions.',claim_ids:['agreement']}]};
 value.claims[0].id='noise-model'; value.claims[0].role='model'; value.claims[0].supports=['correctness'];
 value.claims[0].title='The sampling experiment';
 value.claims[0].purpose='Specifies the noise distribution used by the calculation.';
 value.claims[0].depends_on=[];
 value.claims[1].purpose='Explains why honest parties obtain the same key.';
 value.claims[1].depends_on=['noise-model'];
 return value;
};
const withOverview=()=>{
 const value=grouped();
 value.presentation.overview={goal:'Full confidentiality under chosen-ciphertext queries.',scope:'This exact research release.',boundary:'An unsolved recovery challenge is not a proof.',groups:[
  {id:'evidence',title:'What we have',items:[{title:'Conditional result',text:'A <script>proof</script> in a stated model.',claim_id:'agreement'}]},
  {id:'assumptions',title:'What we still assume',items:[{title:'The model',text:'A concrete implementation is not the ideal experiment.',claim_id:'noise-model'}]},
  {id:'gaps',title:'What is missing here',items:[{title:'Exact application',text:'Review the hypotheses for this release.',claim_id:'agreement'}]}
 ]};
 return value;
};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const boot=(options={})=>createApp({schemes:[scheme()],hash:'#scheme/alpha/1/assumptions',responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:sheet()}:undefined,...options});
const cards=app=>app.$('#assumptions-content').querySelectorAll('.assumption-card');
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
 await test('overview links to an independently reachable exact-release assumptions sheet',async()=>{
  const app=await boot({hash:'#scheme/alpha/1?profile=p1'});
  assert.equal(app.requests.some(r=>r.url.pathname==='/api/protocol/assumptions'),false);
  assert.equal(app.$('#overview-assumptions').href,'#scheme/alpha/1/assumptions?profile=p1');
  app.$('#overview-assumptions').click();await flush();
  assert.equal(app.$('#assumptions-workspace').hidden,false);assert.equal(app.$('#overview-workspace').hidden,true);
  assert.equal(app.$('#assumptions-back').href,'#scheme/alpha/1?profile=p1');
  assert.equal(app.document.title,'Assumptions · Scheme alpha — Crucible');
  assert.equal(app.$('#nav-challenge').getAttribute('aria-current'),'page');
  assert.equal(app.$('.skip-link').href,'#assumptions-workspace');assert.equal(cards(app).length,2);
  assert.equal(cards(app).some(c=>c.open),false);assert.match(app.$('#assumptions-title').textContent,/alpha/);
  assert.equal(app.requests.find(r=>r.url.pathname==='/api/protocol/assumptions').url.searchParams.get('version'),'1');
 });
 await test('direct links open only the requested statement; all prose stays inert',async()=>{
  const app=await boot({hash:'#scheme/alpha/1/assumptions?claim=agreement'});
  assert.deepEqual(cards(app).map(c=>c.open),[false,true]);
  assert.match(cards(app)[0].textContent,/<script>statement<\/script>/);assert.equal(cards(app)[0].querySelector('script'),null);
  assert.equal(cards(app)[0].querySelectorAll('a').some(a=>a.href.startsWith('javascript:')),false);
  assert.match(app.$('#assumptions-content').textContent,/not a security certification/);
  await app.navigate('#scheme/alpha/1/assumptions?claim=missing');
  assert.match(app.$('#assumptions-content').textContent,/That statement is not in this draft/);
 });
 await test('navigation Back and leaving the sheet clear its view',async()=>{
  const app=await boot();await app.navigate('#scheme/alpha/1');
  assert.equal(app.$('#assumptions-workspace').hidden,true);assert.equal(cards(app).length,0);
  app.history.back();await flush();assert.equal(app.$('#assumptions-workspace').hidden,false);assert.equal(cards(app).length,2);
 });
 await test('archived and malformed releases never inherit current claims',async()=>{
  const live=scheme();live.previous_releases=[scheme('alpha','old')];
  const app=await boot({schemes:[live],hash:'#scheme/alpha/old/assumptions',responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:{...sheet('alpha','old'),claims:[]}}:undefined});
  assert.equal(cards(app).length,0);assert.match(app.$('#assumptions-content').textContent,/has not been curated/);
  await app.navigate('#scheme/alpha/missing/assumptions');assert.equal(app.$('#assumptions-workspace').hidden,true);
  await app.navigate('#scheme/alpha/1/assumptions?profile=missing');assert.equal(app.$('#assumptions-workspace').hidden,true);
 });
 await test('mismatched data is rejected and retry can recover',async()=>{
  let count=0;const app=await boot({responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:sheet('alpha',++count===1?'bad':'1')}:undefined});
  assert.equal(cards(app).length,0);assert.match(app.$('#assumptions-content').textContent,/does not match this release/);
  app.$('#assumptions-content').querySelector('button').click();await flush();assert.equal(cards(app).length,2);
 });
 await test('late results cannot overwrite another scheme',async()=>{
  const wait=deferred();const app=await boot({schemes:[scheme(),scheme('beta')],responder:url=>url.pathname==='/api/protocol/assumptions'?(url.searchParams.get('scheme_id')==='alpha'?wait.promise:{sheet:sheet('beta')}):undefined});
  await app.navigate('#scheme/beta/1/assumptions');wait.resolve({sheet:sheet()});await flush();
  assert.match(app.$('#assumptions-title').textContent,/beta/);assert.match(cards(app)[0].querySelector('.assumption-permalink').href,/#scheme\/beta\/1/);
 });
 await test('denied beta access neither requests nor renders sheets',async()=>{
  const app=await boot({responder:(url,_,envelope)=>url.pathname==='/api/session'?envelope({user:null,access:{mode:'beta',allowed:false,status:'anonymous'}}):undefined});
  assert.equal(app.requests.some(r=>r.url.pathname==='/api/protocol/assumptions'),false);assert.equal(app.$('#assumptions-workspace').hidden,true);
 });
 await test('revocation clears content and rejects a late response',async()=>{
  const wait=deferred();const app=await boot({user:{id:'one',handle:'one'},responder:url=>url.pathname==='/api/protocol/assumptions'?wait.promise:undefined});
  app.sandbox.setSession(app.envelope({user:null,access:{mode:'beta',allowed:false,status:'revoked'}}));
  wait.resolve({sheet:sheet()});await flush();assert.equal(cards(app).length,0);assert.equal(app.$('#assumptions-workspace').hidden,true);
 });
 await test('grouped sheets expose purposes and roles without implying an assumption count',async()=>{
  const app=await boot({responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:grouped()}:undefined});
  const root=app.$('#assumptions-content');
  assert.deepEqual(root.querySelectorAll('.assumption-group').map(group=>group.querySelector('h2').textContent),['Premises used by the analysis','What follows']);
  assert.equal(root.querySelectorAll('.assumption-number').length,0);
  assert.equal(cards(app).every(card=>!card.open),true);
  assert.match(cards(app)[0].querySelector('summary').textContent,/Specifies the noise distribution/);
  assert.match(cards(app)[1].querySelector('summary').textContent,/Mathematical consequence/);
  assert.match(root.querySelector('.assumptions-reading-guide').textContent,/different requirements/);
 });
 await test('scoped reading links open their target without another request or inferred status',async()=>{
  const app=await boot({hash:'#scheme/alpha/1/assumptions?claim=agreement',responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:grouped()}:undefined});
  const count=app.requests.length;
  cards(app)[1].querySelector('.assumption-related').querySelector('a').click();await flush();
  assert.equal(app.requests.length,count);
  assert.equal(cards(app)[0].open,true);assert.equal(cards(app)[1].open,true);
  assert.equal(app.location.hash,'#scheme/alpha/1/assumptions?claim=noise-model');
  assert.equal(app.document.activeElement,cards(app)[0].querySelector('summary'));
  assert.doesNotMatch(cards(app)[0].textContent,/verified|certified/);
 });
 await test('invalid groupings and reading references cannot silently hide a statement',async()=>{
  for(const mutate of [value=>value.presentation.sections.pop(),value=>value.presentation.sections[1].claim_ids=['noise-model'],value=>value.claims[1].depends_on=['missing']]){
   const value=grouped();mutate(value);
   const app=await boot({responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:value}:undefined});
   assert.equal(cards(app).length,0);assert.match(app.$('#assumptions-content').textContent,/invalid|include every statement/);
  }
 });
 await test('reading overview opens one supporting statement without a second detail list',async()=>{
  const app=await boot({responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:withOverview()}:undefined});
  const root=app.$('#assumptions-content'), overview=root.querySelector('.assumptions-overview');
  assert.equal(root.children[0],overview);
  assert.deepEqual(overview.querySelectorAll('.assumptions-overview-group').map(group=>group.querySelector('h2').textContent),['What we have','What we still assume','What is missing here']);
  assert.match(overview.textContent,/This exact research release/);
  assert.match(overview.textContent,/<script>proof<\/script>/);assert.equal(overview.querySelector('script'),null);
  assert.equal(root.querySelector('.assumptions-reading-guide'),null);
  assert.equal(cards(app).length,0);assert.equal(root.querySelector('.assumption-group'),null);
  const count=app.requests.length;
  const trigger=overview.querySelector('a');
  assert.equal(trigger.getAttribute('aria-haspopup'),'dialog');
  trigger.click();await flush();
  const reader=root.querySelector('.assumption-reader');
  assert.equal(reader.open,true);assert.equal(app.requests.length,count);
  assert.equal(reader.querySelector('.assumption-reader-content').dataset.claimId,'agreement');
  assert.equal(app.location.hash,'#scheme/alpha/1/assumptions?claim=agreement');
  assert.equal(app.document.activeElement,reader.querySelector('h2'));
  assert.equal(app.document.body.style.overflow,'hidden');
  reader.querySelector('.assumption-related').querySelector('a').click();await flush();
  assert.equal(reader.querySelector('.assumption-reader-content').dataset.claimId,'noise-model');
  assert.equal(app.requests.length,count);
  reader.querySelector('button').click();await flush();
  assert.equal(reader.open,false);assert.equal(reader.childElementCount,0);
  assert.equal(app.location.hash,'#scheme/alpha/1/assumptions');
  assert.equal(app.document.activeElement,trigger);assert.equal(app.document.body.style.overflow,'');
 });
 await test('direct statement links open the reader; navigation releases its modal state',async()=>{
  const app=await boot({hash:'#scheme/alpha/1/assumptions?claim=agreement',responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:withOverview()}:undefined});
  const reader=app.$('.assumption-reader');assert.equal(reader.open,true);
  await app.navigate('#scheme/alpha/1');
  assert.equal(reader.open,false);assert.equal(reader.childElementCount,0);
  assert.equal(app.document.body.style.overflow,'');assert.equal(app.location.hash,'#scheme/alpha/1');
 });
 await test('access revocation closes and clears an open supporting statement',async()=>{
  const app=await boot({user:{id:'one',handle:'one'},hash:'#scheme/alpha/1/assumptions?claim=agreement',responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:withOverview()}:undefined});
  const reader=app.$('.assumption-reader');assert.equal(reader.open,true);
  app.sandbox.setSession(app.envelope({user:null,access:{mode:'beta',allowed:false,status:'revoked'}}));await flush();
  assert.equal(reader.open,false);assert.equal(reader.childElementCount,0);
  assert.equal(app.$('#assumptions-content').childElementCount,0);
  assert.equal(app.document.body.style.overflow,'');
 });
 await test('overview cannot silently reference a different release or omit scope',async()=>{
  for(const mutate of [value=>value.presentation.overview.groups[0].items[0].claim_id='missing',value=>value.presentation.overview.scope='',value=>value.presentation.overview.groups.pop()]){
   const value=withOverview();mutate(value);
   const app=await boot({responder:url=>url.pathname==='/api/protocol/assumptions'?{sheet:value}:undefined});
   assert.equal(cards(app).length,0);assert.equal(app.$('#assumptions-content').querySelector('.assumptions-overview'),null);
   assert.match(app.$('#assumptions-content').textContent,/invalid reading overview/);
  }
 });
 console.log(`${passed} assumptions frontend checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp,flush}=require('./frontend-dom.cjs');
const user={id:'member',handle:'alice'};
const schemes=[{id:'fixture',version:'1',name:'Fixture scheme',participant_count:2,largest_recovered_profile_id:'p2',recovered_profile_ids:['p1','p2'],profiles:[{id:'p1',instance_size:1},{id:'p2',instance_size:2},{id:'p3',instance_size:3}]}];
const bounties=[{id:'task',title:'Investigate a test construction',status:'open',reward_points:300}];
const access=status=>({mode:'beta',status,allowed:status==='active'});
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return{promise,resolve};};
async function boot({actor=null,status=actor?'active':'anonymous',hash='',hook}={}){
 return createApp({schemes,user:actor,hash,bootstrap:true,responder:async(url,options,envelope)=>{
  const data=await hook?.(url,options,envelope);if(data!==undefined)return data;
  if(url.pathname==='/api/session')return envelope({user:actor,csrf_token:'csrf',access:access(status)});
  if(url.pathname==='/api/bounties')return envelope({bounties});
  if(url.pathname==='/api/me/submissions')return envelope({submissions:[]});
 }});
}
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
 await test('the public root explains the site before access resolves without requesting protected data',async()=>{
  const pending=deferred();const app=await boot({hook:url=>url.pathname==='/api/session'?pending.promise:undefined});
  assert.equal(app.$('#home-workspace').hidden,false);assert.equal(app.$('#access-workspace').hidden,true);
  assert.match(app.$('#home-title').textContent,/Find new crypto/);assert.equal(app.$('#home-primary').disabled,true);
  assert.equal(app.$('#home-progress').hidden,true);assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
  pending.resolve(app.envelope({user:null,csrf_token:'csrf',access:access('anonymous')}));await flush();
  assert.equal(app.$('#home-primary').disabled,false);assert.equal(app.$('#home-progress').hidden,true);
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
 });
 await test('visitors get invitation guidance, sign-in and gated paths to schemes, the map and bounties',async()=>{
  const app=await boot();assert.match(app.$('#home-primary').textContent,/Sign in/);assert.match(app.$('#home-access-note').textContent,/invitation/);
  const paths=app.$('#home-workspace').querySelectorAll('a').map(a=>a.href);
  for(const href of ['#challenge','#challenge?view=map','#bounties','#notebook'])assert.ok(paths.includes(href));
  app.$('#home-primary').focus();app.$('#home-primary').click();assert.equal(app.$('#auth-dialog').open,true);
  app.$('#auth-dialog').querySelector('[data-close]').click();await flush();assert.equal(app.document.activeElement,app.$('#home-primary'));
  await app.navigate('#challenge?view=map');assert.equal(app.$('#access-workspace').hidden,false);assert.equal(app.$('#home-workspace').hidden,true);
  assert.equal(app.requests.some(r=>r.url.pathname==='/api/schemes'),false);
  await app.navigate('#home');assert.equal(app.$('#home-workspace').hidden,false);
 });
 await test('member highlights show exact current stages and bounty rewards with useful destinations',async()=>{
  const app=await boot({actor:user});assert.equal(app.$('#home-progress').hidden,false);assert.match(app.$('#home-primary').textContent,/My activity/);
  assert.match(app.$('#home-welcome').textContent,/alice/);assert.equal(app.$('#home-welcome').hidden,false);assert.equal(app.$('#home-access-note').hidden,true);assert.match(app.$('#home-schemes').textContent,/Fixture scheme.*Largest recovery: Stage 2/);
  assert.equal(app.$('#home-schemes').querySelector('.home-stage-strip').children.length,3);assert.equal(app.$('#home-schemes').querySelectorAll('.is-recovered').length,2);
  assert.equal(app.$('#home-schemes').querySelector('a').href,'#scheme/fixture/1');
  assert.match(app.$('#home-bounties').textContent,/Investigate a test construction.*300 points/);
  assert.equal(app.$('#home-bounties').querySelector('a').href,'#bounties?bounty=task');
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/schemes').length,1);
  assert.equal(app.requests.some(r=>r.url.pathname==='/api/challenges'||r.url.pathname==='/api/leaderboard'||r.options.method==='POST'),false);
  app.$('#home-primary').click();await flush();assert.equal(app.location.hash,'#activity');
 });
 await test('frozen bounties show their review state without inviting unavailable contributions',async()=>{
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/bounties'?envelope({bounties:[{...bounties[0],status:'frozen'}]}):undefined});
  assert.match(app.$('#home-bounties').textContent,/Review in progress/);assert.doesNotMatch(app.$('#home-bounties').textContent,/Open for contributions/);
 });
 await test('a slow or failed progress read never blocks the introduction or the other highlight',async()=>{
  const pending=deferred();const app=await boot({actor:user,hook:url=>url.pathname==='/api/schemes'?pending.promise:undefined});
  assert.equal(app.$('#home-workspace').hidden,false);assert.match(app.$('#home-bounties').textContent,/300 points/);
  assert.ok(app.$('#home-schemes').querySelector('.home-placeholder'));
  pending.resolve(app.envelope({schemes:null}));await flush();assert.match(app.$('#home-schemes').textContent,/could not be loaded/);
  assert.match(app.$('#home-bounties').textContent,/300 points/);assert.equal(app.$('#home-primary').disabled,false);
 });
 await test('cached highlights keep their DOM and focused row during quiet revalidation',async()=>{
  let slow=false;const pending=deferred();const app=await boot({actor:user,hook:url=>slow&&url.pathname==='/api/schemes'?pending.promise:undefined});
  const row=app.$('#home-schemes').querySelector('a');await app.navigate('#about');slow=true;await app.navigate('#home');row.focus();
  assert.equal(app.$('#home-schemes').querySelector('a'),row);
  pending.resolve(app.envelope({schemes}));await flush();assert.equal(app.$('#home-schemes').querySelector('a'),row);assert.equal(app.document.activeElement,row);
 });
 await test('permission loss clears member highlights immediately and retires late reads',async()=>{
  const pending=deferred();const app=await boot({actor:user,hook:url=>url.pathname==='/api/bounties'?pending.promise:undefined});
  assert.match(app.$('#home-schemes').textContent,/Fixture scheme/);
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
  assert.equal(app.$('#home-progress').hidden,true);assert.equal(app.$('#home-schemes').textContent,'');assert.equal(app.$('#home-bounties').textContent,'');
  pending.resolve(app.envelope({bounties}));await flush();assert.equal(app.$('#home-bounties').textContent,'');assert.match(app.$('#home-primary').textContent,/Sign in/);
 });
 await test('leaving home retires pending highlights and Back restores the public destination',async()=>{
  const pending=deferred();const app=await boot({actor:user,hook:url=>url.pathname==='/api/bounties'?pending.promise:undefined});
  await app.navigate('#about');pending.resolve(app.envelope({bounties}));await flush();assert.doesNotMatch(app.$('#home-bounties').textContent,/Investigate/);
  app.history.back();await flush();assert.equal(app.$('#home-workspace').hidden,false);assert.match(app.$('#home-bounties').textContent,/Investigate/);
 });
 await test('password sign-in from home stays on the personalized welcome page',async()=>{
  const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/auth/login'?envelope({user,csrf_token:'signed-in',access:access('active')}):undefined});
  app.$('#home-primary').click();app.$('#auth-handle').value='alice';app.$('#auth-password').value='Synthetic local password';app.$('#auth-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(app.$('#auth-dialog').open,false);assert.equal(app.$('#home-workspace').hidden,false);assert.match(app.$('#home-primary').textContent,/My activity/);assert.match(app.$('#home-schemes').textContent,/Fixture scheme/);
  assert.equal(app.sandbox.safeAuthReturnRoute('#home'),'#home');assert.equal(app.sandbox.safeAuthReturnRoute('https://external.invalid'),'#challenge');
 });
 await test('pending accounts get access guidance without any private highlights',async()=>{
  const app=await boot({actor:user,status:'pending'});assert.equal(app.$('#home-progress').hidden,true);assert.match(app.$('#home-primary').textContent,/Check your access/);
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);app.$('#home-primary').click();await flush();assert.equal(app.$('#access-workspace').hidden,false);
 });
 await test('empty data stays distinct from loading, and disabled bounties omit their reads and links',async()=>{
  const app=await boot({actor:user,hash:'#about'});app.window.CrucibleFeatures={bounties:false};await app.navigate('#home');
  assert.equal(app.$('#home-bounties-panel').hidden,true);assert.equal(app.$('#home-bounty-guide').hidden,true);assert.equal(app.requests.some(r=>r.url.pathname==='/api/bounties'),false);
  const empty=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes:[]}):url.pathname==='/api/bounties'?envelope({bounties:[]}):undefined});
  assert.match(empty.$('#home-schemes').textContent,/No schemes/);assert.match(empty.$('#home-bounties').textContent,/No open bounties/);
 });
 console.log(`${passed} home-page checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

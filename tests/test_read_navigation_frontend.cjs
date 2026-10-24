const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'owner', handle: 'alpha'};
const scheme = {id:'fixture',version:'2',name:'Fixture',profiles:[{id:'tier-4',instance_size:4}],next_profile_id:'tier-4'};
const person = handle => ({handle,points:20,records:[],recent_recoveries:[],accepted_proposal_count:0});
const people = {people:[person('alpha'),person('beta')],pagination:{limit:50,offset:0,has_more:false},scoring:{status:'active'}};
const submission = {id:'s1',scheme_id:'fixture',scheme_version:'2',profile_id:'tier-4',method_name:'Recorded method',received_at:100,status:'correct'};
const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r;});return{promise,resolve};};
const defaultData = (url, envelope) => url.pathname==='/api/people' ? envelope(people) : url.pathname.startsWith('/api/people/') ? envelope({person:person(decodeURIComponent(url.pathname.split('/').at(-1)))}) : url.pathname==='/api/me/submissions' ? envelope({submissions:[submission]}) : undefined;
async function boot(hook) { return createApp({schemes:[scheme],user,responder:(url,options,envelope)=>hook?.(url,options,envelope) ?? defaultData(url,envelope)}); }
let passed=0;async function test(name,run){await run();passed++;console.log('PASS '+name);}
(async()=>{
 await test('both home logos navigate within the authorized application',async()=>{
  const app=await boot();await app.navigate('#about');const reads=app.requests.length;
  const logos=app.document.querySelectorAll('a.brand');assert.equal(logos.length,2);
  for(const logo of logos)assert.equal(logo.href,'#challenge');logos[0].click();await flush();
  assert.equal(app.location.hash,'#challenge');assert.equal(app.requests.length,reads);
 });
 await test('returning to People paints immediately, coalesces refreshes and preserves unchanged DOM',async()=>{
  const late=deferred();let slow=false;const app=await boot((url)=>url.pathname==='/api/people'&&slow?late.promise:undefined);
  await app.navigate('#people');const table=app.$('#people-content').firstChild;
  await app.navigate('#about');slow=true;await app.navigate('#people');
  assert.equal(app.$('#people-content').firstChild,table);assert.match(table.textContent,/alpha/);
  await app.navigate('#about');await app.navigate('#people');
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people').length,2);
  late.resolve(app.envelope(people));await flush();assert.equal(app.$('#people-content').firstChild,table);
 });
 await test('profile revisits render their own cached data before network and keep disclosures open',async()=>{
  const late=deferred();let slow=false;const app=await boot(url=>url.pathname==='/api/people/alpha'&&slow?late.promise:undefined);
  await app.navigate('#person/alpha');const first=app.$('#person-content').firstChild;
  app.$('#personal-records').open=true;await app.navigate('#about');slow=true;await app.navigate('#person/alpha');
  assert.equal(app.$('#person-content').firstChild,first);assert.equal(app.$('#personal-records').open,true);
  late.resolve(app.envelope({person:person('alpha')}));await flush();assert.equal(app.$('#person-content').firstChild,first);
  await app.navigate('#person/beta');assert.equal(app.$('#person-title').textContent,'beta');
 });
 await test('My submissions reopen immediately but still revalidate before showing changed results',async()=>{
  const late=deferred();let slow=false;const app=await boot(url=>url.pathname==='/api/me/submissions'&&slow?late.promise:undefined);
  await app.sandbox.showHistory();app.$('#history-dialog').close();slow=true;
  const refresh=app.sandbox.showHistory();assert.match(app.$('#history-content').textContent,/Recorded method/);
  late.resolve(app.envelope({submissions:[]}));await refresh;assert.match(app.$('#history-content').textContent,/No submissions yet/);
 });
 await test('a previously read recovery record survives a same-stage revisit while its GET is pending',async()=>{
  const late=deferred();let slow=false;const app=await boot(url=>url.pathname==='/api/leaderboard'&&slow?late.promise:undefined);
  const route='#challenge/fixture/2?profile=tier-4&view=leaderboard';await app.navigate(route);
  await app.navigate('#about');slow=true;await app.navigate(route);
  assert.equal(app.$('#leaderboard-title').textContent,'Unsolved');assert.doesNotMatch(app.$('#leaderboard-content').textContent,/Loading/);
  late.resolve(app.envelope({entries:[]}));await flush();
 });
 await test('a denied revalidation removes cached profile content rather than leaving a stale reader',async()=>{
  const app=await boot();await app.navigate('#person/alpha');await app.navigate('#about');
  const late=deferred(),fetch=app.sandbox.fetch;
  app.sandbox.fetch=(path,options)=>String(path).startsWith('/api/people/alpha')?late.promise:fetch(path,options);
  await app.navigate('#person/alpha');assert.match(app.$('#person-content').textContent,/Personal records/);
  late.resolve({ok:false,status:403,json:async()=>({detail:'Access denied'})});await flush();
  assert.doesNotMatch(app.$('#person-content').textContent,/Personal records/);assert.match(app.$('#person-content').textContent,/Access denied/);
 });
 await test('same-account session rotation evicts read caches and old pending responses',async()=>{
  const late=deferred();let slow=false;const app=await boot(url=>url.pathname==='/api/people/alpha'&&slow?late.promise:undefined);
  await app.navigate('#person/alpha');await app.navigate('#about');slow=true;await app.navigate('#person/alpha');
  app.sandbox.setSession(app.envelope({user,csrf_token:'rotated'}));await app.navigate('#about');await app.navigate('#person/alpha');
  assert.match(app.$('#person-content').textContent,/Loading profile/);
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'signed-out',access:{mode:'beta',allowed:false,status:'anonymous'}}));
  late.resolve(app.envelope({person:person('alpha')}));await flush();assert.equal(app.$('#person-content').textContent,'');
 });
 await test('successful writes invalidate previously rendered read caches',async()=>{
  const late=deferred();let slow=false;const app=await boot((url,options,envelope)=>url.pathname==='/api/fixture-write'?envelope({ok:true}):url.pathname==='/api/people'&&slow?late.promise:undefined);
  await app.navigate('#people');await app.navigate('#about');await app.sandbox.api('/api/fixture-write',{value:'synthetic'});
  slow=true;await app.navigate('#people');assert.match(app.$('#people-content').textContent,/Loading participants/);
  late.resolve(app.envelope(people));await flush();
 });
 await test('memory retention is bounded and oversized results are not retained',async()=>{
  const app=await boot();for(let i=0;i<40;i++)app.sandbox.rememberRead('synthetic:'+i,{text:'x'.repeat(80000)});
  assert.ok(vm.runInContext('readViews.size',app.sandbox)<=25);
  app.sandbox.rememberRead('too-large',{text:'x'.repeat(500001)});assert.equal(app.sandbox.cachedRead('too-large'),null);
 });
 await test('scheme intent only prefetches exact read-only details, without issuing challenges',async()=>{
  const app=await boot();const before=app.requests.length;app.$('#kem-catalogue').querySelector('.kem-name').dispatchEvent({type:'pointerenter'});await flush();
  // This fixture is the legacy full response, so its detail is already present.
  assert.equal(app.requests.length,before);assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });

 await test('People and profile intent reads are consumed once by their next visible route',async()=>{
  const app=await boot();app.$('#nav-people').dispatchEvent({type:'pointerenter'});await flush();
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people').length,1);
  await app.navigate('#people');assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people').length,1);
  app.$('#account-handle').dispatchEvent({type:'focus'});await flush();await app.navigate('#person/alpha');
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people/alpha').length,1);
 });
 await test('a first profile overlaps label navigation and profile data without duplicate reads',async()=>{
  const nav=deferred(),profile=deferred();
  const app=await createApp({schemes:[scheme],user,hash:'#people',responder:(url,options,envelope)=>url.pathname==='/api/schemes'?nav.promise:url.pathname==='/api/people/alpha'?profile.promise:defaultData(url,envelope)});
  await app.navigate('#person/alpha');assert.equal(app.requests.filter(r=>r.url.pathname==='/api/schemes').length,1);
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people/alpha').length,1);
  profile.resolve(app.envelope({person:person('alpha')}));nav.resolve(app.envelope({schemes:[scheme]}));await flush();
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/people/alpha').length,1);assert.match(app.$('#person-content').textContent,/Personal records/);
 });
 await test('prefetch is suppressed after beta access is lost',async()=>{
  const app=await boot();app.sandbox.setSession(app.envelope({user,csrf_token:'denied',access:{mode:'beta',allowed:false,status:'revoked'}}));
  const before=app.requests.length;app.$('#nav-people').dispatchEvent({type:'pointerenter'});app.$('#account-handle').dispatchEvent({type:'focus'});app.$('#nav-notebook').dispatchEvent({type:'pointerenter'});await flush();
  assert.equal(app.requests.length,before);assert.equal(app.document.querySelector('script[src="/notebook/notebook.js"]'),null);
 });
 console.log(`${passed} render-first navigation checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

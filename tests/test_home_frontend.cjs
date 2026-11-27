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
 await test('the menu shows the confirmed account balance on every member page and clears it on sign-out',async()=>{
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/me/points'?envelope({account_points:{user_id:user.id,points:1240,score_status:'active'}}):undefined});
  assert.equal(app.$('#account-points-value').textContent,'1.2K');assert.match(app.$('#account-points').getAttribute('aria-label'),/1,240/);
  assert.equal(app.$('#account-points').href,'#person/alice');
  await app.navigate('#about');assert.equal(app.$('#account-points').hidden,false);assert.equal(app.$('#account-points-value').textContent,'1.2K');
  assert.equal(app.requests.filter(r=>r.url.pathname==='/api/me/points').length,1);
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
  assert.equal(app.$('#account-points').hidden,true);assert.equal(app.$('#account-points-value').textContent,'—');
 });
 await test('late balances cannot replace a new account balance',async()=>{
  const pending=deferred();let reads=0;const next={id:'second-account',handle:'bob'};
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/me/points'?(++reads===1?pending.promise:envelope({account_points:{user_id:next.id,points:23,score_status:'active'}})):undefined});
  assert.equal(app.$('#account-points-value').textContent,'—');
  app.sandbox.setSession(app.envelope({user:next,csrf_token:'new-session',access:access('active')}));await flush();
  assert.equal(app.$('#account-points-value').textContent,'23');
  pending.resolve(app.envelope({account_points:{user_id:user.id,points:999,score_status:'active'}}));await flush();
  assert.equal(app.$('#account-points-value').textContent,'23');assert.equal(app.$('#account-points').href,'#person/bob');
 });
 await test('a failed first balance read shows unavailable rather than invented zero points',async()=>{
  const app=await boot({actor:user,hook:url=>{if(url.pathname==='/api/me/points')throw Error('Temporary test failure');}});
  assert.equal(app.$('#account-points').hidden,false);assert.equal(app.$('#account-points-value').textContent,'—');
  assert.match(app.$('#account-points').getAttribute('aria-label'),/unavailable/);assert.equal(app.$('#account-points').getAttribute('aria-busy'),'false');
 });
 await test('the root stays gated before and after anonymous access resolves without reading site content',async()=>{
  const pending=deferred();const app=await boot({hook:url=>url.pathname==='/api/session'?pending.promise:undefined});
  assert.equal(app.$('#home-workspace').hidden,true);assert.equal(app.$('#access-workspace').hidden,false);
  assert.equal(app.$('#home-title').textContent,'Find new cryptography. Test its limits.');
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
  pending.resolve(app.envelope({user:null,csrf_token:'csrf',access:access('anonymous')}));await flush();
  assert.equal(app.$('#home-workspace').hidden,true);assert.equal(app.$('#access-workspace').hidden,false);
  assert.equal(app.$('#access-title').textContent,'Private beta');assert.equal(app.$('#access-branding').hidden,false);assert.match(app.$('#access-branding').textContent,/Find new cryptography. Test its limits./);
  assert.equal(app.$('#access-description').textContent,'An invitation is required to join.');
  assert.equal(app.$('#access-create-account').hidden,false);assert.equal(app.$('#access-sign-in').hidden,false);
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
 });
 await test('signed-out visitors see only account entry on every home alias and other site routes',async()=>{
  const app=await boot();
  for(const hash of ['#home','#','#challenge','#about','#notebook','#bounties','#people','#scheme/fixture/1','#home']){
   await app.navigate(hash);assert.equal(app.$('#access-workspace').hidden,false);assert.equal(app.$('#home-workspace').hidden,true);
   assert.equal(app.$('.primary-nav').hidden,true);
  }
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
  app.$('#access-sign-in').focus();app.$('#access-sign-in').click();assert.equal(app.$('#auth-dialog').open,true);
  assert.equal(app.$('#auth-title').textContent,'Welcome back');
  app.$('#auth-dialog').querySelector('[data-close]').click();await flush();assert.equal(app.document.activeElement,app.$('#access-sign-in'));
 });
 await test('create account distinguishes malformed and wrong-site invitations before resuming a local invitation',async()=>{
  const app=await boot();app.$('#access-create-account').click();
  assert.equal(app.$('#auth-title').textContent,'Create an account');assert.match(app.$('#auth-description').textContent,/invitation-only/);
  assert.equal(app.$('#auth-invitation-form').hidden,false);assert.equal(app.$('#auth-sign-in-methods').hidden,true);
  app.$('#auth-invitation-link').value='https://elsewhere.invalid/#join?token=cri_vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv';
  app.$('#auth-invitation-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(app.location.hash,'');assert.match(app.$('#auth-invitation-error').textContent,/different site/);
  app.$('#auth-invitation-link').value='https://crucible.fail/#join?token=cri_'+'e'.repeat(43);
  app.$('#auth-invitation-form').dispatchEvent({type:'submit'});await flush();
  assert.match(app.$('#auth-invitation-error').textContent,/for crucible.fail/);
  assert.match(app.$('#auth-invitation-error').textContent,/local preview/);
  assert.doesNotMatch(app.$('#auth-invitation-error').textContent,/expired|full Crucible invitation/);
  for(const bad of ['not an invitation','https://name:secret@127.0.0.1:8000/#join?token=abc','javascript:alert(1)#join?token=abc']){
   app.$('#auth-invitation-link').value=bad;app.$('#auth-invitation-form').dispatchEvent({type:'submit'});await flush();
   assert.match(app.$('#auth-invitation-error').textContent,/full Crucible invitation/);
  }
  app.$('#auth-invitation-link').value='http://127.0.0.1:8000/#join?token=cri_vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv';
  app.$('#auth-invitation-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(app.location.hash,'#join?token=cri_vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv');assert.equal(app.$('#auth-dialog').open,true);
  assert.equal(app.$('#auth-title').textContent,'Create an account');assert.equal(app.$('#auth-invitation-link').value,'');
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session','/api/access/invitations/inspect']);
 });
 await test('landing layout links select a bounded presentation and admission restores the app header',async()=>{
  const app=await boot({hash:'#home?layout=left'});
  assert.equal(app.$('html').getAttribute('data-beta-landing'),'left');
  await app.navigate('#home?layout=unrecognized');
  assert.equal(app.$('html').getAttribute('data-beta-landing'),'centered');
  app.sandbox.setSession(app.envelope({user,csrf_token:'signed-in',access:access('active')}));
  await app.sandbox.loadAuthorizedWorkspace();await flush();
  assert.equal(app.$('html').getAttribute('data-beta-landing'),null);
  assert.equal(app.$('#home-workspace').hidden,false);
 });
 await test('members get two opportunity panels and one quiet contribution link',async()=>{
  const app=await boot({actor:user});
  assert.equal(app.$('#home-entry').hidden,false);assert.equal(app.$('#home-walkthrough').hidden,true);
  assert.equal(app.document.querySelector('#home-challenge-guide'),null);
  assert.equal(app.$('#home-highlights').querySelectorAll('section').length,2);
  assert.equal(app.$('#home-tour').textContent,'Or try a bounty or propose a scheme.');
  assert.equal(app.$('#home-bounties-link').href,'#bounties');assert.equal(app.$('#home-propose-link').href,'#propose');
  assert.ok(app.requests.some(r=>r.url.pathname==='/api/schemes'));assert.ok(app.requests.some(r=>r.url.pathname==='/api/bounties'&&r.url.searchParams.get('sort')==='points-high'));
  assert.equal(app.requests.some(r=>r.options.method==='POST'||r.url.pathname.includes('/challenges')),false);
  assert.equal(app.$('.home-explore').querySelectorAll('a').length,2);
  const children=app.$('.home-onboarding').childNodes;assert.ok(children.indexOf(app.$('.home-new-label'))<children.indexOf(app.$('#home-get-started')));assert.ok(children.indexOf(app.$('#home-get-started'))<children.indexOf(app.$('.home-explore')));
 });
 await test('the challenge guide starts in the real catalogue and opens its first level without issuing an instance',async()=>{
  const app=await boot({actor:user,hash:'#home?guide=challenge'});await flush();
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  assert.equal(app.location.hash,'#challenge');assert.equal(app.$('#catalogue-workspace').hidden,false);
  assert.equal(app.$('#live-guide-title').textContent,'Choose a scheme.');
  app.$('.kem-name').click();await flush();
  assert.equal(app.location.hash,'#scheme/fixture/1?profile=p1');
  assert.equal(app.$('#live-guide-title').textContent,'Open the challenge.');
  app.$('#participation-guide').querySelector('.button-primary').click();await flush();
  assert.equal(app.location.hash,'#challenge/fixture/1?profile=p1&view=challenge');
  assert.match(app.$('#live-guide-title').textContent,/Get your challenge/);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  app.sandbox.endLiveGuide();assert.equal(app.$('#participation-guide').hidden,true);
 });
 await test('the challenge choice can be dismissed or skipped without starting a guide or issuing an instance',async()=>{
  const app=await boot({actor:user});app.$('.home-weekly-card').click();await flush();
  assert.equal(app.$('#challenge-start-dialog').open,true);app.$('#challenge-start-dialog').querySelector('[data-close]').click();
  assert.match(app.location.hash,/#challenge\/fixture\/1\?profile=p3/);assert.equal(app.$('#participation-guide').hidden,true);
  assert.equal(app.document.activeElement,app.$('#workspace'));await app.navigate('#home');
  app.$('.home-weekly-card').click();await flush();app.$('#challenge-start-own').click();await flush();
  assert.match(app.location.hash,/#challenge\/fixture\/1\?profile=p3/);assert.equal(app.$('#participation-guide').hidden,true);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('weekly guidance opens on its selected challenge and resets a saved partial guide',async()=>{
  const app=await boot({actor:user});app.$('.home-weekly-card').click();await flush();
  assert.match(app.location.hash,/#challenge\/fixture\/1\?profile=p3/);assert.equal(app.$('#workspace').hidden,false);assert.equal(app.$('#challenge-start-dialog').open,true);
  vm.runInContext("state.challenge={id:'owned-weekly',profile_id:'p3',shared_secret_bytes:32,status:'open',ends_at:999999999999};state.challengeLoading=false;",app.sandbox);
  app.sandbox.startLiveGuide('challenge',{schemeId:'fixture',schemeVersion:'1',profileId:'p3',challengeId:'owned-weekly',approach:'agent',toolsReady:true,challengeStep:'work'});
  vm.runInContext('liveGuideMinimized=true;',app.sandbox);app.$('#challenge-start-guided').click();await flush();
  assert.equal(app.$('#challenge-start-dialog').open,false);assert.equal(app.$('#participation-guide').hidden,false);assert.equal(app.$('#participation-guide').classList.contains('is-minimized'),false);assert.equal(app.$('#live-guide-title').textContent,'Let’s try a challenge.');assert.equal(app.$('.live-guide-progress').textContent,'1 / 3');assert.equal(vm.runInContext('liveGuide.approach',app.sandbox),undefined);
  vm.runInContext('liveGuide=null;restoreLiveGuide();renderLiveGuide();',app.sandbox);assert.equal(app.$('#live-guide-title').textContent,'Let’s try a challenge.');assert.equal(app.$('.live-guide-progress').textContent,'1 / 3');assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('background challenge reads keep the current prompt and action nodes stable',async()=>{
  const app=await boot({actor:user,hash:'#challenge/fixture/1?profile=p1&view=challenge'});
  vm.runInContext("state.challenge={id:'owned',profile_id:'p1',shared_secret_bytes:32,status:'open',ends_at:999999999999};state.challengeLoading=false;",app.sandbox);
  app.sandbox.startLiveGuide('challenge',{schemeId:'fixture',schemeVersion:'1',profileId:'p1',approach:'manual',toolsReady:true});
  vm.runInContext('liveGuidePollAt=Number.MAX_SAFE_INTEGER;',app.sandbox);
  const title=app.$('#live-guide-title'),action=app.$('#participation-guide').querySelector('.guide-next');
  assert.equal(title.textContent,'Recover the secret locally.');
  vm.runInContext('state.challengeLoading=true;',app.sandbox);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title'),title);assert.equal(app.$('#participation-guide').querySelector('.guide-next'),action);assert.equal(action.disabled,true);
  vm.runInContext('state.challengeLoading=false;',app.sandbox);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title'),title);assert.equal(app.$('#participation-guide').querySelector('.guide-next'),action);assert.equal(action.disabled,false);
 });
 await test('sign-out invalidates an open challenge choice',async()=>{
  const app=await boot({actor:user});app.$('.home-weekly-card').click();await flush();
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
  assert.equal(app.$('#challenge-start-dialog').open,false);app.$('#challenge-start-guided').click();await flush();
  assert.equal(app.$('#participation-guide').hidden,true);assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('tagline previews toggle panel highlights without navigation or writes',async()=>{
  const app=await boot({actor:user});
  for(const feature of ['bounties','challenge']){
   const control=app.$('[data-home-feature="'+feature+'"]');assert.equal(control.tagName,'BUTTON');
   control.click();await flush();assert.equal(app.location.hash,'');assert.equal(control.getAttribute('aria-pressed'),'true');
   control.click();assert.equal(control.getAttribute('aria-pressed'),'false');
  }
  app.sandbox.startLiveGuide('note');app.$('[data-home-feature="bounties"]').click();await flush();
  assert.equal(app.$('[data-home-feature="bounties"]').getAttribute('aria-pressed'),'true');
  assert.equal(app.$('#participation-guide').hidden,true);
  assert.equal(vm.runInContext('liveGuide.kind',app.sandbox),'note');
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('weekly points use live policy and progress separates personal and community records',async()=>{
  const scoring={policy_id:'fixture-policy',record_advance_points:137,enrolled_releases:[{id:'fixture',version:'1'}]};
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes:[{...schemes[0],recovered_profile_ids:['p1'],largest_recovered_profile_id:'p1'}],recovery_scoring:scoring}):undefined});
  assert.equal(app.$('.home-weekly-points').querySelector('strong').textContent,'137');
  assert.equal(app.$('.home-stage-track').getAttribute('aria-valuenow'),'0');assert.equal(app.$('.home-stage-track').getAttribute('aria-valuemax'),'3');
  assert.match(app.$('.home-stage-labels').textContent,/Your best: no solves yet/);assert.match(app.$('.home-stage-labels').textContent,/Record: Level 1/);assert.match(app.$('.home-weekly-card').href,/profile=p2/);
  app.$('.home-weekly-card').click();await flush();assert.equal(app.$('#challenge-start-dialog').open,true);
  app.$('#challenge-start-guided').click();await flush();assert.match(app.location.hash,/#challenge\/fixture\/1\?profile=p2/);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('weekly rewards survive compact progress reads and match Home after returning from Schemes',async()=>{
  const scoring={policy_id:'unit-policy',record_advance_points:137,enrolled_releases:[{id:'fixture',version:'1'}]};
  let progress=false;
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope(progress?
   {projection:'progress',catalogue_revision:'same-release',recovery_scoring:scoring,releases:[{id:'fixture',version:'1',participant_count:3,recovered_profile_ids:['p1'],largest_recovered_profile_id:'p1',next_profile_id:'p2',first_recovery:null}]}:
   {schemes,catalogue_revision:'same-release',recovery_scoring:scoring}):undefined});
  assert.equal(app.$('.home-weekly-points').querySelector('strong').textContent,'137');
  progress=true;await app.sandbox.refreshProfiles({background:true});await flush();
  assert.equal(app.$('.home-weekly-points').querySelector('strong').textContent,'137');
  assert.match(app.$('.home-weekly-card').href,/profile=p2/);
  await app.navigate('#challenge');
  assert.equal(app.$('#catalogue-weekly').hidden,false);assert.equal(app.$('.catalogue-weekly-points').querySelector('strong').textContent,'137');
  assert.equal(app.$('.catalogue-weekly-card').href,app.$('.home-weekly-card').href);
  await app.navigate('#home');assert.equal(app.$('.home-weekly-points').querySelector('strong').textContent,'137');
  vm.runInContext("installNavigation({schemes:state.schemes,catalogue_revision:'same-release'})",app.sandbox);
  app.sandbox.renderHomeHighlights();assert.equal(app.$('.home-weekly-points').querySelector('strong').textContent,'137');
  vm.runInContext("installNavigation({schemes:state.schemes,catalogue_revision:'new-release'})",app.sandbox);
  app.sandbox.renderHomeHighlights();assert.equal(app.document.querySelector('.home-weekly-points'),null);
 });
 await test('weekly personal progress uses the owned record and hides an already earned bonus',async()=>{
  const scoring={record_advance_points:137,enrolled_releases:[{id:'fixture',version:'1'}]};
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes,recovery_scoring:scoring}):url.pathname==='/api/people/alice'?envelope({person:{handle:'alice',score_status:'active',records:[{scheme_id:'fixture',scheme_version:'1',profile_id:'p2'}],first_records:[{scheme_id:'fixture'}]}}):undefined});
  await flush();assert.equal(app.$('.home-stage-track').getAttribute('aria-valuenow'),'2');assert.match(app.$('.weekly-personal-progress').textContent,/Your best: Level 2/);assert.equal(app.$('.home-stage-fill').style.width,(200/3)+'%');assert.equal(app.document.querySelector('.home-weekly-points'),null);assert.equal(app.$('.weekly-bonus-earned').textContent,'Record bonus earned');
 });
 await test('a failed weekly personal read stays unavailable without inventing completion',async()=>{
  const app=await boot({actor:user,hook:url=>{if(url.pathname==='/api/people/alice')throw Error('Synthetic unavailable own profile');}});await flush();assert.equal(app.$('.weekly-personal-progress').textContent,'Your progress: unavailable');assert.equal(app.$('.home-stage-track').getAttribute('aria-valuenow'),null);
 });
 await test('a late personal record cannot replace a new account’s weekly progress',async()=>{
  const pending=deferred(),bob={id:'another-member',handle:'bob'};
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/people/alice'?pending.promise:url.pathname==='/api/people/bob'?envelope({person:{handle:'bob',score_status:'active',records:[],first_records:[]}}):undefined});
  assert.equal(app.$('.home-stage-track').getAttribute('aria-valuenow'),null);app.sandbox.setSession(app.envelope({user:bob,csrf_token:'bob-session',access:access('active')}));await flush();
  pending.resolve(app.envelope({person:{handle:'alice',score_status:'active',records:[{scheme_id:'fixture',scheme_version:'1',profile_id:'p3'}],first_records:[]}}));await flush();assert.equal(app.$('.weekly-personal-progress').textContent,'Your best: no solves yet');assert.equal(app.$('.home-stage-track').getAttribute('aria-valuenow'),'0');
 });
 await test('level labels normalize server presentation without changing challenge metadata',async()=>{
  const app=await boot({actor:user}),profile={stage_number:7,stage_label:'Stage 7'};
  assert.equal(app.sandbox.profileLabel(profile),'Level 7');assert.equal(profile.stage_label,'Stage 7');assert.equal(profile.stage_number,7);
  assert.equal(app.sandbox.profileLabel({profile_label:'ML-KEM-768'},{selection_kind:'parameter-set'}),'ML-KEM-768');
 });
 await test('Get started pins the introductory teaching scheme at its first level, independently of the weekly record',async()=>{
  const intro={...schemes[0],id:'teaching-dh-kem',name:'Introductory Diffie–Hellman'};
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes:[schemes[0],intro]}):undefined});
  app.$('#home-get-started').click();await flush();
  const guide=vm.runInContext('liveGuide',app.sandbox);
  assert.equal(guide.schemeId,'teaching-dh-kem');assert.equal(guide.profileId,'p1');assert.equal(guide.intro,true);
  assert.match(app.location.hash,/#challenge\/teaching-dh-kem\/1\?profile=p1/);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  assert.equal(app.$('#home-get-started').disabled,false);
 });
 await test('Get started always restarts at step one for owned open, completed and expired instances',async()=>{
  for(const status of ['open','solved','incorrect','expired']){
   const intro={...schemes[0],id:'teaching-dh-kem',name:'Introductory Diffie–Hellman'};
   const app=await boot({actor:user,hook:(url,options,envelope)=>{
    if(url.pathname==='/api/schemes')return envelope({schemes:[intro]});
    if(url.pathname==='/api/challenges'){const clock=envelope({});return envelope({challenge:{id:'owned-'+status,scheme_id:intro.id,scheme_version:'1',profile_id:'p1',projection:'summary',shared_secret_bytes:32,status,issued_at:clock.server_time,ends_at:status==='expired'?clock.server_time-1:clock.round.ends_at,round_id:clock.round.id}});}
   }});
   app.$('#home-get-started').click();await flush();
   assert.equal(app.$('#live-guide-title').textContent,'Let’s try a challenge.');assert.equal(app.$('.live-guide-progress').textContent,'1 / 3');
   vm.runInContext("liveGuide.challengeStep='work';liveGuide.approach='manual';liveGuide.toolsReady=true;",app.sandbox);
   if(status==='expired'){app.$('#submission-dialog').showModal();app.sandbox.renderLiveGuide();assert.equal(app.$('#live-guide-title').textContent,'This round has ended.');app.$('#participation-guide').querySelector('.guide-back').click();assert.equal(app.$('#submission-dialog').open,false);assert.equal(app.$('#live-guide-title').textContent,'Let’s try a challenge.');}
   await app.navigate('#home');app.$('#home-get-started').click();await flush();
   assert.equal(app.$('#live-guide-title').textContent,'Let’s try a challenge.');assert.equal(app.$('.live-guide-progress').textContent,'1 / 3');
   assert.equal(vm.runInContext('liveGuide.challengeStep',app.sandbox),'intro');assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  }
 });
 await test('a late clipboard success cannot advance a replacement guide',async()=>{
  const app=await boot({actor:user,hash:'#challenge/fixture/1?profile=p1&view=challenge'}),pending=deferred();
  vm.runInContext("state.challenge={id:'owned',profile_id:'p1',shared_secret_bytes:32,status:'open',ends_at:999999999999};state.challengeLoading=false;",app.sandbox);
  app.sandbox.navigator.clipboard={writeText:()=>pending.promise};
  const selection={schemeId:'fixture',schemeVersion:'1',profileId:'p1',approach:'agent'};app.sandbox.startLiveGuide('challenge',selection);
  const copy=app.sandbox.copyAgentInstructions();app.sandbox.startLiveGuide('challenge',{...selection,challengeStep:'choice'});
  pending.resolve();await copy;await flush();
  assert.equal(app.$('#live-guide-title').textContent,'Choose how to work.');assert.notEqual(vm.runInContext('liveGuide.toolsReady',app.sandbox),true);
  const second=deferred();app.sandbox.navigator.clipboard={writeText:()=>second.promise};app.sandbox.startLiveGuide('challenge',selection);
  const again=app.sandbox.copyAgentInstructions();app.$('#participation-guide').querySelector('.guide-back').click();assert.equal(app.$('#live-guide-title').textContent,'Choose how to work.');
  second.resolve();await again;await flush();assert.equal(app.$('#live-guide-title').textContent,'Choose how to work.');
 });
 await test('a background status check preserves the active prompt and leaves Back usable',async()=>{
  const pending=deferred();let hold=false;const challenge={id:'owned',profile_id:'p1',shared_secret_bytes:32,status:'open',ends_at:999999999999};
  const app=await boot({actor:user,hash:'#challenge/fixture/1?profile=p1&view=challenge',hook:(url,options,envelope)=>url.pathname==='/api/challenges'?(hold?pending.promise:envelope({challenge})):undefined});
  app.sandbox.startLiveGuide('challenge',{schemeId:'fixture',schemeVersion:'1',profileId:'p1',approach:'manual',toolsReady:true});await flush();
  const title=app.$('#live-guide-title'),button=app.$('#participation-guide').querySelector('.guide-back');hold=true;const loading=app.sandbox.loadChallenge({background:true});await flush();
  assert.equal(app.$('#live-guide-title'),title);assert.equal(button.disabled,false);assert.equal(vm.runInContext('state.challengeLoading',app.sandbox),false);
  pending.resolve(app.envelope({challenge}));await loading;assert.equal(app.$('#live-guide-title'),title);assert.equal(button.disabled,false);
 });
 await test('an unavailable introductory scheme never falls back to the weekly challenge',async()=>{
  const app=await boot({actor:user});app.$('#home-get-started').click();await flush();
  assert.match(app.$('#home-intro-status').textContent,/introductory challenge is unavailable/);
  assert.equal(app.$('#home-intro-status').hidden,false);assert.equal(vm.runInContext('liveGuide',app.sandbox),null);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('Home help is a feature map, then a chosen page gets its own local tour',async()=>{
  const app=await boot({actor:user});app.$('#how-it-works').click();await flush();
  assert.equal(app.$('.orientation-title').textContent,'Explore Crucible');assert.equal(app.location.hash,'');
  const notes=app.$('.orientation-features').querySelectorAll('button').find(button=>button.querySelector('strong').textContent==='Notes');notes.click();await flush();
  assert.equal(app.location.hash,'#notebook');assert.equal(app.$('.orientation-title').textContent,'Research notes');
  app.$('.orientation-card').querySelector('.guide-next').click();await flush();
  assert.equal(app.location.hash,'#notebook');assert.equal(app.$('.orientation-title').textContent,'Write a note');
  app.$('.orientation-directory').click();assert.equal(app.$('.orientation-title').textContent,'Explore Crucible');
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('the live result coach waits for real fields and does not finish on typing',async()=>{
  const app=await boot({actor:user,hash:'#challenge/fixture/1?profile=p1&view=challenge'});
  app.sandbox.startLiveGuide('challenge',{schemeId:'fixture',schemeVersion:'1',profileId:'p1',approach:'manual'});
  vm.runInContext("liveGuidePollAt=Number.MAX_SAFE_INTEGER; state.challenge={id:'owned',projection:'summary',shared_secret_bytes:32,status:'open',ends_at:999999999999}; state.challengeLoading=false; state.submissionChallenge=state.challenge;",app.sandbox);
  app.$('#submission-dialog').showModal();app.$('#method-select').value='new';app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Enter your recovered key.');
  app.$('#candidate').value='0'.repeat(64);app.sandbox.renderLiveGuide();assert.equal(app.$('#live-guide-title').textContent,'Enter your recovered key.');
  app.$('#participation-guide').querySelector('.guide-next').click();assert.equal(app.$('#live-guide-title').textContent,'Choose your method.');
  app.$('#participation-guide').querySelector('.guide-next').click();assert.equal(app.$('#live-guide-title').textContent,'Name your method.');
  app.$('#method-name').value='Unit test';app.$('#method-pseudocode').value='Unit test, not an actual recovery';app.sandbox.renderLiveGuide();
  app.$('#participation-guide').querySelector('.guide-next').click();app.$('#participation-guide').querySelector('.guide-next').click();
  assert.equal(app.$('#live-guide-title').textContent,'Record your hardware.');
  for(const [id,value] of [['hardware','unit fixture'],['precomputation','None'],['peak-memory','Not measured'],['workers','1'],['wall-seconds','0']])app.$('#'+id).value=value;
  for(let i=0;i<5;i++)app.$('#participation-guide').querySelector('.guide-next').click();
  app.sandbox.renderLiveGuide();assert.equal(app.$('#live-guide-title').textContent,'Review and submit your answer.');
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  app.$('#submission-dialog').close();vm.runInContext("state.challenge.submission={id:'receipt',status:'incorrect'};",app.sandbox);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Answer recorded.');assert.match(app.$('#participation-guide').textContent,/did not match.*locked/);
 });
 await test('the contribution chooser offers real note, bounty and scheme workflows',async()=>{
  const app=await boot({actor:user,hash:'#home?guide=tour'});
  assert.match(app.$('#home-guide-title').textContent,/What would you like to contribute/);
  assert.deepEqual(app.$('#home-guide-content').querySelectorAll('[data-contribution]').map(node=>node.dataset.contribution),['note','bounty','scheme']);
  assert.equal(app.$('#participation-guide').hidden,true);
  assert.equal(vm.runInContext('liveGuide',app.sandbox),null);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  app.$('[data-contribution="note"]').click();await flush();
  assert.equal(vm.runInContext('liveGuide.kind',app.sandbox),'note');assert.equal(app.$('#participation-guide').hidden,false);
 });
 await test('a private note save is distinct from a confirmed publication submission',async()=>{
  const app=await boot({actor:user,hash:'#notebook'});app.sandbox.startLiveGuide('note');
  const guide=vm.runInContext('liveGuide',app.sandbox),note={id:'owned-note',owner:user,revision:1,revisions:[{revision:1,content_sha256:'a'.repeat(64),publication_status:'private',content:{}}]};
  app.sandbox.observeLiveGuide('/api/notebook/notes','POST',{note},guide);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Submit your saved note.');
  note.revisions[0].publication_status='submitted';note.revisions[0].publication={id:'publication-receipt'};
  app.sandbox.observeLiveGuide('/api/notebook/notes/owned-note/publication-requests','POST',{note},guide);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Publication request recorded.');assert.doesNotMatch(app.$('#participation-guide').textContent,/has been approved/);
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
  app.sandbox.observeLiveGuide('/api/notebook/notes/owned-note/publication-requests','POST',{note},guide);
  assert.equal(app.$('#participation-guide').hidden,true);
 });
 await test('a guide can return home while the catalogue is unavailable',async()=>{
  const app=await boot({actor:user,hash:'#home?guide=challenge',hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes:null}):undefined});
  assert.equal(app.location.hash,'#challenge');
  await app.navigate('#home');assert.equal(app.$('#home-entry').hidden,false);
  app.sandbox.endLiveGuide();assert.equal(app.$('#participation-guide').hidden,true);
 });
 await test('an expired pinned instance is revalidated without issuing a replacement',async()=>{
  const app=await boot({actor:user,hash:'#challenge/fixture/1?profile=p1&view=challenge'});
  app.sandbox.startLiveGuide('challenge',{schemeId:'fixture',schemeVersion:'1',profileId:'p1',approach:'manual',challengeId:'old-instance'});
  const guide=vm.runInContext('liveGuide',app.sandbox);
  vm.runInContext('liveGuidePollAt=Number.MAX_SAFE_INTEGER; state.challenge=null; state.challengeLoading=false;',app.sandbox);
  app.sandbox.observeLiveGuide('/api/challenges/old-instance/download','GET',{challenge:{id:'old-instance',status:'expired',ends_at:1}},guide);
  app.sandbox.renderLiveGuide();assert.equal(app.$('#live-guide-title').textContent,'This round has ended.');
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('a bounty evidence publication request waits for actual publication before linking',async()=>{
  const app=await boot({actor:user});app.sandbox.startLiveGuide('bounty',{bountyId:'task'});
  const guide=vm.runInContext('liveGuide',app.sandbox);vm.runInContext("state.screen='notebook';liveGuidePollAt=Number.MAX_SAFE_INTEGER;",app.sandbox);
  const note={id:'evidence',owner:user,revision:1,revisions:[{revision:1,content_sha256:'b'.repeat(64),publication_status:'submitted',publication:{id:'publication'},content:{}}]};
  app.sandbox.observeLiveGuide('/api/notebook/notes','POST',{note},guide);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Your note is awaiting publication.');assert.equal(guide.receipt,undefined);
  note.revisions[0].publication_status='approved';app.sandbox.observeLiveGuide('/api/notebook/notes/evidence','GET',{note},guide);app.sandbox.renderLiveGuide();
  assert.equal(app.$('#live-guide-title').textContent,'Link your published contribution.');assert.equal(guide.receipt,undefined);
 });
 await test('leaving a slow guide or signing out retires late scheme renders',async()=>{
  for(const signOut of [false,true]){
   const pending=deferred();const app=await boot({actor:user,hash:'#home?guide=challenge',hook:url=>url.pathname==='/api/schemes'?pending.promise:undefined});
   if(signOut)app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
   else await app.navigate('#home');
   pending.resolve(app.envelope({schemes}));await flush();
   assert.equal(app.$('#home-guide-content').textContent,'');
   assert.equal(app.$('#home-workspace').hidden,signOut);
  }
 });
 await test('password sign-in opens the two choices and loads member highlights',async()=>{
  const app=await boot({hook:(url,options,envelope)=>url.pathname==='/api/auth/login'?envelope({user,csrf_token:'signed-in',access:access('active')}):undefined});
  app.$('#access-sign-in').click();app.$('#auth-handle').value='alice';app.$('#auth-password').value='Synthetic local password';app.$('#auth-form').dispatchEvent({type:'submit'});await flush();
  assert.equal(app.$('#auth-dialog').open,false);assert.equal(app.$('#home-workspace').hidden,false);assert.equal(app.$('#home-tour').hidden,false);
  assert.equal(app.requests.some(r=>r.url.pathname==='/api/schemes'),true);
  assert.equal(app.sandbox.safeAuthReturnRoute('#home'),'#home');assert.equal(app.sandbox.safeAuthReturnRoute('https://external.invalid'),'#home');
 });
 await test('pending accounts cannot open either guide',async()=>{
  const app=await boot({actor:user,status:'pending',hash:'#home?guide=challenge'});
  assert.equal(app.$('#home-workspace').hidden,true);assert.equal(app.$('#access-workspace').hidden,false);
  assert.deepEqual(app.requests.map(r=>r.url.pathname),['/api/session']);
 });
 await test('disabled bounties omit that contribution and empty catalogues stay usable',async()=>{
  const app=await boot({actor:user});app.window.CrucibleFeatures={bounties:false};await app.navigate('#home?guide=tour');
  assert.deepEqual(app.$('#home-guide-content').querySelectorAll('[data-contribution]').map(node=>node.dataset.contribution),['note','scheme']);
  const empty=await boot({actor:user,hash:'#home?guide=challenge',hook:(url,options,envelope)=>url.pathname==='/api/schemes'?envelope({schemes:[]}):undefined});
  assert.equal(empty.$('#live-guide-title').textContent,'No challenges available.');assert.equal(empty.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('home ranks real point totals and the weekly challenge stays stable within its UTC week',async()=>{
  const rows=[{id:'low',title:'Low',status:'open',reward_points:100},{id:'high',title:'High',status:'open',reward_points:500},{id:'middle',title:'Middle',status:'frozen',reward_points:300}];
  const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/bounties'?envelope({bounties:rows}):undefined});
  assert.deepEqual(app.$('#home-bounty-list').querySelectorAll('h3').map(item=>item.textContent),['High','Middle','Low']);
  assert.match(app.$('#home-bounty-list').textContent,/Under review/);
  vm.runInContext('state.schemes.push({...state.schemes[0],id:"second",name:"Second"})',app.sandbox);
  const monday=Date.UTC(2026,10,23)/1000;
  assert.equal(app.sandbox.featuredHomeScheme(monday).id,app.sandbox.featuredHomeScheme(monday+6*86400).id);
  assert.notEqual(app.sandbox.featuredHomeScheme(monday).id,app.sandbox.featuredHomeScheme(monday+7*86400).id);
  assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 await test('a failed bounty read leaves the challenge usable and offers a real retry',async()=>{
  let broken=true;const app=await boot({actor:user,hook:(url,options,envelope)=>url.pathname==='/api/bounties'?envelope({bounties:broken?null:bounties}):undefined});
  assert.ok(app.$('#home-featured-challenge').querySelector('a'));assert.match(app.$('#home-bounty-list').textContent,/Bounties unavailable/);
  broken=false;await app.sandbox.loadHomeHighlights({refresh:true});
  assert.match(app.$('#home-bounty-list').textContent,/Investigate a test construction/);
 });
 await test('sign-out retires a slow featured bounty response and clears its content',async()=>{
  const pending=deferred(),app=await boot({actor:user,hook:url=>url.pathname==='/api/bounties'?pending.promise:undefined});
  app.sandbox.setSession(app.envelope({user:null,csrf_token:'bye',access:access('anonymous')}));
  pending.resolve(app.envelope({bounties}));await flush();
  assert.equal(app.$('#home-bounty-list').textContent,'');assert.equal(app.$('#home-workspace').hidden,true);
  assert.equal(app.$('#how-it-works').hidden,true);
 });
 await test('the optional site tour preserves an existing guide and performs no writes',async()=>{
  const app=await boot({actor:user});assert.equal(app.$('#participation-guide').hidden,true);
  app.sandbox.startLiveGuide('note');const previous=vm.runInContext('liveGuide',app.sandbox);
  app.$('#how-it-works').click();await flush();assert.equal(app.$('.orientation-title').textContent,'Explore Crucible');
  vm.runInContext('newcomerOrientation.cancel()',app.sandbox);await flush();
  assert.equal(vm.runInContext('liveGuide',app.sandbox),previous);assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
 });
 console.log(`${passed} home-page checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

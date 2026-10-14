const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'account-a', handle: 'owner'};
const scopes = ['challenges:read','challenges:write','methods:read','methods:write','submissions:read','submissions:write'];
const connection = (overrides = {}) => ({user_code: 'ABCD-EFGH', label: 'Local research agent', scopes, created_at: Date.now()/1000, expires_at: Date.now()/1000+120, status: 'pending', ...overrides});
const profile = handle => ({handle, points: 0, rank: 1, schemes_recovered: 0, correct_submission_count: 0, records: [], recent_recoveries: []});
const writes = app => app.requests.filter(r => r.options.method === 'POST');
const schemes = [{id:'fixture',version:'1',name:'Fixture',profiles:[{id:'tier-1',instance_size:1}],next_profile_id:'tier-1'}];
let passed = 0;
async function test(name, run) {await run();console.log('PASS '+name);passed++;}
(async () => {
  await test('signed-out connection stays on its code and signing in does not approve it', async () => {
    const app = await createApp({schemes, hash:'#connect?code=abcd-efgh', responder:(url, options, envelope) => {
      if(url.pathname==='/api/agent/connect/ABCD-EFGH')return envelope({connection:connection()});
    }});
    assert.equal(app.$('#connect-workspace').hidden,false);
    assert.match(app.$('#connect-content').textContent,/ABCD-EFGH.*Sign in/);
    assert.equal(app.requests.some(r=>r.url.pathname.startsWith('/api/agent/')),false);
    app.sandbox.setSession(app.envelope({user,csrf_token:'private-csrf'}));await flush();
    assert.equal(app.location.hash,'#connect?code=abcd-efgh');
    assert.match(app.$('#connect-content').textContent,/Local research agent/);
    assert.equal(app.$('#connect-content').querySelector('.agent-scope-list').querySelectorAll('li').length,6);
    assert.equal(app.$('#connect-origin').textContent,'http://127.0.0.1:8000');
    assert.equal(writes(app).length,0);
  });
  await test('only explicit Connect agent sends one CSRF-protected approval and displays no credential', async () => {
    const app=await createApp({schemes,user,hash:'#connect?code=ABCD-EFGH',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/agent/connect/ABCD-EFGH')return envelope({connection:connection()});
      if(url.pathname.endsWith('/approve'))return envelope({connection:connection({status:'approved'}),ignored_secret:'NEVER_RENDER_ME'});
    }});
    assert.equal(writes(app).length,0);
    app.$('#connect-content').querySelector('[data-agent-decision="approve"]').click();await flush();
    assert.equal(writes(app).length,1);
    assert.equal(writes(app)[0].url.pathname,'/api/agent/connect/ABCD-EFGH/approve');
    assert.equal(writes(app)[0].options.headers['X-CSRF-Token'],'test-csrf');
    assert.equal(writes(app)[0].options.body,'{}');
    assert.match(app.$('#connect-content').textContent,/Connection approved/);
    assert.doesNotMatch(app.document.body.textContent,/NEVER_RENDER_ME/);
    assert.equal(app.$('#connect-content').querySelector('[data-agent-decision]'),null);
  });
  await test('expiry, denial and a mismatched preview never leave an approval button', async () => {
    for(const value of [connection({expires_at:0}),connection({status:'denied'}),connection({user_code:'WRNG-CODE'})]){
      const app=await createApp({schemes,user,hash:'#connect?code=ABCD-EFGH',responder:(url,options,envelope)=>url.pathname.startsWith('/api/agent/connect/')?envelope({connection:value}):undefined});
      assert.equal(app.$('#connect-content').querySelector('[data-agent-decision="approve"]'),null);
      assert.equal(writes(app).length,0);
    }
  });
  await test('connection cancellation is an explicit action and does not approve', async () => {
    const app=await createApp({schemes,user,hash:'#connect?code=ABCD-EFGH',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/agent/connect/ABCD-EFGH')return envelope({connection:connection()});
      if(url.pathname.endsWith('/deny'))return envelope({connection:connection({status:'denied'})});
    }});
    app.$('#connect-content').querySelector('[data-agent-decision="deny"]').click();await flush();
    assert.equal(writes(app).length,1);assert.ok(writes(app)[0].url.pathname.endsWith('/deny'));
    assert.match(app.$('#connect-content').textContent,/Connection cancelled/);
  });
  await test('late connection responses cannot render after navigation or account changes', async () => {
    let resolve;const pending=new Promise(r=>{resolve=r;});
    const app=await createApp({schemes,user,hash:'#connect?code=ABCD-EFGH',responder:url=>url.pathname.startsWith('/api/agent/connect/')?pending:undefined});
    await app.navigate('#about');
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'new'}));
    resolve(app.envelope({connection:connection()}));await flush();
    assert.equal(app.$('#connect-workspace').hidden,true);
    assert.equal(app.$('#connect-content').querySelector('[data-agent-decision]'),null);
    assert.equal(writes(app).length,0);
  });
  await test('agent settings are own-profile-only and fetch tokens only when expanded', async () => {
    const app=await createApp({schemes,user,hash:'#person/owner',responder:(url,options,envelope)=>{
      if(url.pathname.startsWith('/api/people/'))return envelope({person:profile(url.pathname.split('/').at(-1))});
      if(url.pathname==='/api/agent/tokens')return envelope({tokens:[]});
    }});
    assert.equal(app.$('#agent-connections').hidden,false);
    assert.equal(app.requests.some(r=>r.url.pathname==='/api/agent/tokens'),false);
    app.$('#agent-connections').open=true;app.$('#agent-connections').dispatchEvent({type:'toggle'});await flush();
    assert.match(app.$('#agent-token-list').textContent,/No agents connected/);
    await app.navigate('#person/someone_else');
    assert.equal(app.$('#agent-connections').hidden,true);
    assert.equal(app.$('#agent-token-list').textContent,'');
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/agent/tokens').length,1);
  });
  await test('disconnect revokes only the selected agent and refreshes metadata', async () => {
    let active=true;
    const token=()=>({id:'agent-one',label:'My agent',scopes,created_at:100,expires_at:Date.now()/1000+1000,revoked_at:active?null:200,active});
    const app=await createApp({schemes,user,hash:'#person/owner',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/owner')return envelope({person:profile('owner')});
      if(url.pathname==='/api/agent/tokens')return envelope({tokens:[token()]});
      if(url.pathname==='/api/agent/tokens/agent-one/revoke'){active=false;return envelope({token:token()});}
    }});
    app.$('#agent-connections').open=true;app.$('#agent-connections').dispatchEvent({type:'toggle'});await flush();
    app.$('#agent-token-list').querySelector('button').click();await flush();
    assert.equal(writes(app).length,1);assert.equal(writes(app)[0].url.pathname,'/api/agent/tokens/agent-one/revoke');
    assert.match(app.$('#agent-token-list').textContent,/Disconnected/);
    assert.equal(app.$('#agent-token-list').querySelector('button'),null);
  });
  await test('sign-out discards late token metadata and removes private settings', async () => {
    let resolve;const pending=new Promise(r=>{resolve=r;});
    const app=await createApp({schemes,user,hash:'#person/owner',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/owner')return envelope({person:profile('owner')});
      if(url.pathname==='/api/agent/tokens')return pending;
    }});
    app.$('#agent-connections').open=true;app.$('#agent-connections').dispatchEvent({type:'toggle'});await flush();
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'next'}));
    resolve(app.envelope({tokens:[{id:'private',label:'PRIVATE_AGENT_LABEL',scopes,active:true}]}));await flush();
    assert.equal(app.$('#agent-connections').hidden,true);
    assert.doesNotMatch(app.document.body.textContent,/PRIVATE_AGENT_LABEL/);
  });
  await test('older agent connections remain reachable through pagination', async () => {
    const app=await createApp({schemes,user,hash:'#person/owner',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/owner')return envelope({person:profile('owner')});
      if(url.pathname==='/api/agent/tokens'){
        const offset=Number(url.searchParams.get('offset'));
        return envelope({tokens:[{id:offset?'older':'newer',label:offset?'Older agent':'Newer agent',scopes,active:true,expires_at:Date.now()/1000+1000}],pagination:{limit:1,offset,has_more:!offset}});
      }
    }});
    app.$('#agent-connections').open=true;app.$('#agent-connections').dispatchEvent({type:'toggle'});await flush();
    app.$('#agent-token-list').querySelectorAll('button').find(button=>button.textContent==='Show more agents').click();await flush();
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/agent/tokens').at(-1).url.searchParams.get('offset'),'1');
    assert.match(app.$('#agent-token-list').textContent,/Newer agent.*Older agent/);
    assert.equal(app.$('#agent-token-list').querySelectorAll('.agent-token-row').length,2);
  });
  await test('agent instructions contain exact challenge identity and CLI retrieval without credentials or submission', async () => {
    let challengeId='exact-challenge-id';
    const app=await createApp({schemes,user,hash:'#challenge/fixture/1?profile=tier-1&view=challenge',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/challenges'){const clock=envelope({});return envelope({challenge:{id:challengeId,profile_id:'tier-1',round_id:clock.round.id,issued_at:clock.server_time,ends_at:clock.round.ends_at,status:'open',public:{scheme_id:'fixture',version:'1',encoding:{shared_secret_bytes:32}}}});}
    }});
    let copied='';app.sandbox.navigator.clipboard={writeText:async value=>{copied=value;}};
    const before=app.requests.length;
    assert.equal(app.$('#agent-instructions').hidden,true);
    app.$('#use-agent').click();await flush();
    assert.equal(app.$('#agent-instructions').hidden,false);
    assert.equal(app.$('#use-agent').getAttribute('aria-expanded'),'true');
    assert.match(app.$('#agent-instructions-text').textContent,/exact-challenge-id/);
    assert.equal(copied,'','Opening instructions does not copy or execute them');
    assert.equal(app.requests.length,before);
    app.sandbox.renderChallenge();
    assert.equal(app.$('#agent-instructions').hidden,false,'The same challenge keeps its open instructions');
    app.sandbox.applyRoute();await flush();
    assert.equal(app.$('#agent-instructions').hidden,false,'Refreshing the same route keeps its open instructions');
    app.$('#use-agent').click();
    assert.equal(app.$('#agent-instructions').hidden,true);
    assert.equal(app.$('#use-agent').getAttribute('aria-expanded'),'false');
    app.$('#use-agent').click();
    assert.equal(app.$('#agent-instructions').hidden,false);
    assert.equal(app.$('#use-agent').getAttribute('aria-expanded'),'true');
    app.$('#copy-agent-instructions').click();await flush();
    assert.match(copied,/http:\/\/127.0.0.1:8000\/api\/agent\/client/);
    assert.match(copied,/get --challenge-id 'exact-challenge-id'/);
    assert.match(copied,/Deadline:.*UTC/);
    assert.match(copied,/init 'fixture' --tier 1 --path NEW_EMPTY_FOLDER/);
    assert.match(app.$('#agent-followup-request').textContent,/Retrieve the newest Fixture challenge at Tier 1/);
    assert.doesNotMatch(copied,/test-csrf|public_key|ciphertext/);
    assert.equal(app.requests.length,before);assert.equal(writes(app).length,0);
    app.sandbox.navigator.clipboard={writeText:async()=>{throw Error('blocked');}};
    app.$('#copy-agent-instructions').click();await flush();
    assert.equal(app.$('#agent-instructions').hidden,false);
    assert.equal(app.$('#agent-instructions-text').textContent,copied);
    challengeId='next-challenge-id';
    await app.sandbox.loadChallenge();
    assert.equal(app.$('#agent-instructions').hidden,true);
    assert.equal(app.$('#agent-instructions-text').textContent,'');
    assert.equal(app.$('#use-agent').getAttribute('aria-expanded'),'false');
    app.$('#use-agent').click();
    assert.match(app.$('#agent-instructions-text').textContent,/next-challenge-id/);
    assert.doesNotMatch(app.$('#agent-instructions-text').textContent,/exact-challenge-id/);
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'next'}));
    assert.equal(app.$('#agent-instructions').hidden,true);
    assert.equal(app.$('#agent-instructions-text').textContent,'');
  });
  await test('source bundles link only through same-origin download endpoints', async () => {
    const app=await createApp({schemes});
    for(const [url,expected] of [['/api/methods/method-one/source/download',1],['https://untrusted.example/code',0],['javascript:alert(1)',0]]){
      app.sandbox.renderRecovery({handle:'owner',status:'correct',received_at:100,elapsed_seconds:1,method:{name:'Method',pseudocode:'Inspect me',source:{download_url:url}},compute:{}},'Fixture');
      assert.equal(app.$('#recovery-content').querySelectorAll('a').length,expected);
    }
  });
  console.log(`${passed} agent handoff frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

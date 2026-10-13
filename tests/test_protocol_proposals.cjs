// Proposal descriptions remain inert, private, and separate from live adapters.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'owner', handle: 'owner'};
const digest = 'a'.repeat(64);
const spec = () => ({format:'crucible-kem-spec-v1',name:'Fixture KEM',version:'draft-1',summary:'An unreviewed fixture.',
  algorithms:{setup:'Set public parameters.',keygen:'<script>never_execute()</script>',encapsulate:'Encapsulate.',decapsulate:'Decapsulate.'},
  parameters:{tier_meaning:'A local fixture scale.',supported_tiers:{kind:'range',min:20,max:30,step:1},mapping:'Map each tier.',distribution:'Independent samples.',reuse:'Fresh parameters.'},
  encoding:{public_key:'Canonical bytes.',ciphertext:'Canonical bytes.',shared_secret:'Depends on tier; see mapping.',shared_secret_bytes:null},
  correctness:{kind:'unknown',failure_bound:null,argument:'Not proved.',scope:'Fixture.',evidence:null},
  security:{notion:'Shared-secret recovery.',model:'Classical.',claim:'No claim.',assumptions:'An unreviewed assumption.'},
  implementation:null,references:[{title:'Not fetched',url:'https://example.invalid/specification'}]});
const receipt = (overrides={}) => ({id:'proposal-one',status:'submitted',revision:1,created_at:100,content_sha256:digest,specification:spec(),...overrides});
const deferred = () => {let resolve; const promise=new Promise(r=>{resolve=r;}); return {promise,resolve};};
async function boot({account=user,hash='#propose',canReview=false,hook}={}) {
  return createApp({schemes:[],user:account,hash,responder:async(url,options,envelope)=>{
    if (hook) { const answer=await hook(url,options,envelope); if (answer!==undefined) return answer; }
    if (url.pathname==='/api/session') return envelope({user:account,csrf_token:'test-csrf',capabilities:{review_protocol_proposals:canReview}});
    if (url.pathname==='/api/me/protocol-proposals') return envelope({proposals:[]});
    if (url.pathname==='/api/protocol/review-queue') return envelope({proposals:[]});
    if (url.pathname==='/api/protocol/proposals/validate') {
      const canonical=JSON.parse(options.body).specification; delete canonical._instructions;
      return envelope({specification:canonical,content_sha256:digest,validation:{status:'format-valid',scope:'Not verified.'}});
    }
    if (url.pathname==='/api/protocol/proposals') return envelope({proposal:receipt()});
    if (url.pathname==='/api/protocol/proposals/proposal-one') return envelope({proposal:receipt(),author:user});
  }});
}
async function upload(app, value=spec()) {
  const text=typeof value==='string'?value:JSON.stringify(value);
  app.$('#proposal-file').files=[{size:Buffer.byteLength(text),text:async()=>text}];
  app.$('#proposal-file').dispatchEvent({type:'change'}); await flush();
}
const saves=app=>app.requests.filter(r=>r.url.pathname==='/api/protocol/proposals'&&r.options.method==='POST');
const checks=app=>app.requests.filter(r=>r.url.pathname==='/api/protocol/proposals/validate');
const reviews=app=>app.requests.filter(r=>r.url.pathname.endsWith('/reviews')&&r.options.method==='POST');
const reviewer={id:'reviewer',handle:'reviewer'};
function fillReview(app,decision='changes_requested') {
  app.$('#proposal-decision').value=decision;
  app.$('#proposal-review-notes').value='Review the parameter mapping.';
  app.$('#proposal-review-evidence').value='Ran the pinned test vectors and checked their expected outputs.';
  for(const input of app.document.querySelectorAll('[data-proposal-check]'))input.checked=decision==='approved';
}
async function recordReview(app){app.$('#proposal-review-form').dispatchEvent({type:'submit'});await flush();}
let passed=0;
async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('proposal route has no new top-level navigation and downloads are explicit same-origin links',async()=>{
    const app=await boot({account:null,hash:'#challenge'});
    const entry=app.$('#catalogue-workspace').querySelectorAll('a').find(link=>link.href==='#propose');
    assert.ok(entry);entry.click();await flush();
    assert.equal(app.$('#proposal-workspace').hidden,false);
    assert.equal(app.$('#catalogue-workspace').hidden,true);
    assert.equal(app.$('#nav-challenge').getAttribute('aria-current'),'page');
    assert.equal(app.document.title,'Propose a scheme — Crucible');
    assert.equal(app.document.activeElement===app.$('#proposal-workspace'),true);
    const links=app.$('#proposal-workspace').querySelectorAll('a').map(a=>a.href);
    assert.ok(links.includes('/api/protocol/spec-template'));assert.ok(links.includes('/api/protocol/spec-schema'));
    assert.equal(app.requests.some(r=>r.url.pathname.includes('protocol')),false);
    app.history.back();await flush();assert.equal(app.$('#proposal-workspace').hidden,true);
  });
  await test('anonymous import checks format only, renders the canonical description safely, and does not fetch references',async()=>{
    const app=await boot({account:null});const value=spec();value._instructions={name:'Remove these instructions.'};
    await upload(app,value);
    assert.equal(checks(app).length,1);assert.equal(saves(app).length,0);
    assert.equal(app.$('#proposal-review').hidden,false);
    const preview=app.$('#proposal-preview');
    assert.match(preview.textContent,/<script>never_execute\(\)<\/script>/);
    assert.equal(preview.querySelectorAll('script').length,0);
    assert.equal(preview.querySelectorAll('a').length,0);
    assert.doesNotMatch(preview.textContent,/Remove these instructions/);
    assert.match(preview.textContent,/20–30.*step 1/);
    assert.match(preview.textContent,/See shared-secret encoding/);
    assert.match(app.$('#proposal-review').textContent,/not been verified.*does not create a live challenge/);
    assert.equal(app.requests.some(r=>r.url.hostname==='example.invalid'),false);
    assert.equal(app.$('#submit-proposal').textContent,'Sign in to submit');
  });
  await test('malformed, nonobject, oversized, and dishonest file lengths cannot reach validation',async()=>{
    const app=await boot();
    for(const value of ['bad JSON','[]','null','7']) {await upload(app,value);assert.equal(checks(app).length,0);assert.equal(app.$('#proposal-review').hidden,true);assert.equal(app.$('#proposal-error').hidden,false);}
    let read=false;app.$('#proposal-file').files=[{size:49153,text:async()=>{read=true;return '{}';}}];
    app.$('#proposal-file').dispatchEvent({type:'change'});await flush();assert.equal(read,false);
    app.$('#proposal-file').files=[{size:1,text:async()=> ' '.repeat(49153)}];app.$('#proposal-file').dispatchEvent({type:'change'});await flush();
    assert.equal(checks(app).length,0);assert.match(app.$('#proposal-error').textContent,/48 KiB/);
  });
  await test('server field paths appear as text and invalidate any previously valid preview',async()=>{
    const app=await boot();await upload(app);
    const original=app.sandbox.fetch;
    app.sandbox.fetch=async(path,options)=>String(path).includes('/validate')?{ok:false,status:422,json:async()=>({detail:'Check the protocol specification.',errors:[{path:'specification.algorithms.keygen',message:'Check this field’s format or required value.'},{path:'<script>path</script>',message:'Literal only.'}]})}:original(path,options);
    await upload(app);
    assert.equal(app.$('#proposal-review').hidden,true);
    assert.equal(app.$('#proposal-preview').textContent,'');
    assert.match(app.$('#proposal-field-errors').textContent,/specification.algorithms.keygen/);
    assert.equal(app.$('#proposal-field-errors').querySelector('script'),null);
    assert.equal(saves(app).length,0);
  });
  await test('incomplete validation responses cannot enable submission',async()=>{
    for(const result of [{},{specification:spec(),content_sha256:digest},{specification:spec(),content_sha256:'invalid',validation:{status:'format-valid'}},
      {specification:{name:'missing'},content_sha256:digest,validation:{status:'format-valid'}}]){
      const app=await boot({hook:(url)=>url.pathname.endsWith('/validate')?result:undefined});await upload(app);
      assert.equal(app.$('#proposal-review').hidden,true);assert.equal(app.$('#submit-proposal').disabled,true);assert.match(app.$('#proposal-error').textContent,/incomplete result/);
    }
  });
  await test('a completed anonymous preview survives first sign-in without automatic submission',async()=>{
    const app=await boot({account:null});await upload(app);app.$('#submit-proposal').click();await flush();
    assert.equal(app.$('#auth-dialog').open,true);assert.equal(saves(app).length,0);
    app.sandbox.setSession(app.envelope({user,csrf_token:'new-session'}));await flush();
    assert.equal(app.$('#proposal-review').hidden,false);assert.match(app.$('#proposal-preview').textContent,/Fixture KEM/);
    assert.equal(app.$('#submit-proposal').textContent,'Submit proposal');assert.equal(saves(app).length,0);
  });
  await test('only an explicit final click submits the canonical specification and leaves the catalogue unchanged',async()=>{
    const app=await boot();const value=spec();value._instructions={note:'Discard before storage.'};await upload(app,value);
    assert.equal(saves(app).length,0);app.$('#submit-proposal').click();await flush();
    assert.equal(saves(app).length,1);const payload=JSON.parse(saves(app)[0].options.body);
    assert.deepEqual(Object.keys(payload).sort(),['idempotency_key','specification']);
    assert.equal(Object.hasOwn(payload.specification,'_instructions'),false);
    assert.equal(app.$('#proposal-review').hidden,true);assert.match(app.$('#proposal-status').textContent,/Submitted for review/);
    assert.equal(app.$('#proposal-status').querySelector('a').href,'#propose?proposal=proposal-one');
    assert.equal(app.requests.some(r=>r.url.pathname==='/api/challenges'&&r.options.method==='POST'),false);
    assert.equal(app.requests.filter(r=>r.url.pathname==='/api/schemes').length,1);
  });
  await test('double clicks serialize submission and uncertain retries preserve the exact key and payload',async()=>{
    const pending=deferred();let attempt=0;
    const app=await boot({hook:(url)=>{if(url.pathname==='/api/protocol/proposals'){attempt++;if(attempt===1)return pending.promise;}}});
    await upload(app);app.$('#submit-proposal').click();app.$('#submit-proposal').click();await flush();assert.equal(saves(app).length,1);
    pending.resolve({});await flush();assert.match(app.$('#proposal-status').textContent,/could not be confirmed/);
    assert.equal(app.$('#upload-proposal').disabled,true);assert.equal(app.$('#submit-proposal').textContent,'Retry submission');
    const first=saves(app)[0].options.body;app.$('#submit-proposal').click();await flush();assert.equal(saves(app).length,2);
    assert.equal(saves(app)[1].options.body,first);assert.match(app.$('#proposal-status').textContent,/Submitted for review/);
  });
  await test('leaving the route discards late validation without submitting or changing the new page',async()=>{
    const pending=deferred();const app=await boot({hook:url=>url.pathname.endsWith('/validate')?pending.promise:undefined});
    const work=upload(app);await flush();await app.navigate('#about');pending.resolve({specification:spec(),content_sha256:digest,validation:{status:'format-valid'}});await work;
    assert.equal(app.$('#about-workspace').hidden,false);assert.equal(app.$('#proposal-preview').textContent,'');assert.equal(saves(app).length,0);
    await app.navigate('#propose');assert.equal(app.$('#proposal-review').hidden,true);
  });
  await test('late file reads and validation cannot populate another account',async()=>{
    for(const phase of ['file','validation']){
      const pending=deferred();const app=await boot({hook:url=>phase==='validation'&&url.pathname.endsWith('/validate')?pending.promise:undefined});
      if(phase==='file'){app.$('#proposal-file').files=[{size:100,text:()=>pending.promise}];app.$('#proposal-file').dispatchEvent({type:'change'});}
      else upload(app);
      await flush();app.sandbox.setSession(app.envelope({user:{id:'other',handle:'other'},csrf_token:'other'}));
      pending.resolve(phase==='file'?JSON.stringify(spec()):{specification:spec(),content_sha256:digest,validation:{status:'format-valid'}});await flush();
      assert.equal(app.$('#proposal-preview').textContent,'');assert.equal(app.$('#proposal-review').hidden,true);assert.equal(saves(app).length,0);
    }
  });
  await test('owner details are read-only and clear immediately on logout; late lists and details cannot return',async()=>{
    const app=await boot({hash:'#propose?proposal=proposal-one'});await flush();
    assert.equal(app.$('#proposal-draft').hidden,true);assert.match(app.$('#proposal-detail-content').textContent,/Fixture KEM.*not been approved/);
    assert.equal(app.$('#proposal-detail-content').querySelector('button'),null);
    app.sandbox.setSession(app.envelope({user:null,csrf_token:'guest'}));await flush();
    assert.doesNotMatch(app.$('#proposal-detail-content').textContent,/Fixture KEM|never_execute/);
    assert.equal(app.$('#proposal-list').textContent,'');
    for(const phase of ['list','detail']){
      const pending=deferred();const delayed=await boot({hash:'#propose?proposal=proposal-one',hook:url=>url.pathname===(phase==='list'?'/api/me/protocol-proposals':'/api/protocol/proposals/proposal-one')?pending.promise:undefined});
      delayed.sandbox.setSession(delayed.envelope({user:null,csrf_token:'guest'}));pending.resolve(phase==='list'?{proposals:[receipt()]}:{proposal:receipt()});await flush();
      assert.doesNotMatch(delayed.$('#proposal-list').textContent+delayed.$('#proposal-detail-content').textContent,/Fixture KEM|never_execute/);
    }
  });
  await test('a late successful submission cannot expose its receipt after an account switch',async()=>{
    const pending=deferred();const app=await boot({hook:url=>url.pathname==='/api/protocol/proposals'?pending.promise:undefined});
    await upload(app);app.$('#submit-proposal').click();await flush();app.sandbox.setSession(app.envelope({user:{id:'other',handle:'other'},csrf_token:'other'}));
    pending.resolve({proposal:receipt()});await flush();assert.equal(app.$('#proposal-preview').textContent,'');assert.equal(app.$('#proposal-status').textContent,'');assert.equal(app.$('#proposal-review').hidden,true);
  });
  await test('review navigation is capability-gated and self-review is never offered',async()=>{
    const ordinary=await boot({hash:'#propose?review=1'});
    assert.equal(ordinary.$('#proposal-review-link').hidden,true);
    assert.equal(ordinary.requests.some(r=>r.url.pathname==='/api/protocol/review-queue'),false);
    assert.match(ordinary.$('#proposal-list').textContent,/configured reviewers/);
    const own=await boot({canReview:true,hash:'#propose?review=1&proposal=proposal-one'});
    assert.equal(own.$('#proposal-review-link').hidden,false);
    assert.equal(own.$('#proposal-review-form').hidden,true);
    assert.equal(own.$('#proposal-detail-actions').hidden,false);
    assert.equal(own.$('#revise-proposal').hidden,false);
    assert.equal(own.$('#revise-proposal').href,'#propose?revise=proposal-one');
  });
  await test('configured reviewers see the private queue and its author-labelled exact detail links',async()=>{
    const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1',hook:url=>url.pathname==='/api/protocol/review-queue'?{proposals:[{...receipt(),author:user}]}:undefined});
    const link=app.$('#proposal-list').querySelector('a');assert.equal(link.href,'#propose?review=1&proposal=proposal-one');
    assert.match(link.textContent,/Revision 1.*Review pending.*owner/);
    link.click();await flush();assert.equal(app.$('#proposal-review-form').hidden,false);
    assert.equal(app.$('#proposal-detail-actions').hidden,false);assert.equal(app.$('#revise-proposal').hidden,true);assert.equal(reviews(app).length,0);
    assert.match(app.$('#proposal-review-form').textContent,/Manual review.*does not activate a challenge or execute an artifact/s);
  });
  await test('approval needs every check, an exact implementation artifact, and substantive evidence',async()=>{
    for(const missing of ['artifact','check','evidence','correctness']){
      const value=spec();if(missing!=='artifact')value.implementation={url:'https://example.invalid/artifact',sha256:'b'.repeat(64)};
      if(missing!=='correctness'){value.correctness.kind='perfect';value.correctness.failure_bound='0';}
      const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:url=>url.pathname.endsWith('/proposal-one')?{proposal:receipt({specification:value}),author:user}:undefined});
      fillReview(app,'approved');
      if(missing==='check')app.document.querySelector('[data-proposal-check]').checked=false;
      if(missing==='evidence')app.$('#proposal-review-evidence').value='Too short';
      await recordReview(app);assert.equal(reviews(app).length,0);assert.match(app.$('#proposal-review-error').textContent,/all five checks/);
    }
  });
  await test('explicit approval binds the decision and evidence to the displayed revision and hashes without activation',async()=>{
    const value=spec();value.implementation={url:'https://example.invalid/artifact',sha256:'b'.repeat(64)};
    value.correctness.kind='perfect';value.correctness.failure_bound='0';
    let current=receipt({specification:value,revision:3});let decision;
    const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:(url,options)=>{
      if(url.pathname.endsWith('/proposal-one'))return{proposal:current,author:user};
      if(url.pathname.endsWith('/reviews')){
        decision=JSON.parse(options.body);current={...current,status:decision.decision};
        return{proposal:current,review:{revision:3,content_sha256:digest,decision:decision.decision}};
      }
    }});
    fillReview(app,'approved');assert.equal(reviews(app).length,0);await recordReview(app);
    assert.equal(reviews(app).length,1);assert.equal(decision.expected_revision,3);assert.equal(decision.content_sha256,digest);
    assert.equal(decision.implementation_sha256,'b'.repeat(64));assert.equal(Object.values(decision.checks).every(Boolean),true);
    assert.match(decision.evidence,/pinned test vectors/);assert.ok(decision.idempotency_key);
    assert.equal(app.$('#proposal-review-form').hidden,true);
    assert.match(app.$('#proposal-detail-content').textContent,/Approved for trusted integration.*does not activate a live challenge or certify security/);
    assert.equal(app.requests.some(r=>r.url.hostname==='example.invalid'||r.url.pathname==='/api/challenges'),false);
  });
  await test('changes can be requested without an implementation, but require notes and evidence',async()=>{
    let current=receipt();const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:(url,options)=>{
      if(url.pathname.endsWith('/proposal-one'))return{proposal:current,author:user};
      if(url.pathname.endsWith('/reviews')){const body=JSON.parse(options.body);current={...current,status:body.decision};return{proposal:current,review:{revision:1,content_sha256:digest,decision:body.decision}};}
    }});
    fillReview(app);app.$('#proposal-review-evidence').value='';await recordReview(app);assert.equal(reviews(app).length,0);
    fillReview(app);await recordReview(app);const payload=JSON.parse(reviews(app)[0].options.body);
    assert.equal(payload.decision,'changes_requested');assert.equal(payload.implementation_sha256,null);assert.equal(Object.values(payload.checks).some(Boolean),false);
    assert.equal(app.$('#proposal-review-form').hidden,true);assert.match(app.$('#proposal-detail-content').textContent,/Changes requested/);
  });
  await test('an uncertain manual review freezes the decision and retries the exact idempotent payload',async()=>{
    let count=0;const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:(url,options)=>{
      if(url.pathname.endsWith('/reviews')){count++;if(count===1)return{};const payload=JSON.parse(options.body);return{proposal:receipt({status:payload.decision}),review:{revision:1,content_sha256:digest,decision:payload.decision}};}
    }});
    fillReview(app);await recordReview(app);assert.equal(app.$('#proposal-review-fields').disabled,true);
    assert.equal(app.$('#submit-proposal-review').textContent,'Retry review');const first=reviews(app)[0].options.body;
    app.$('#proposal-review-notes').value='A script trying to alter a frozen field.';await recordReview(app);
    assert.equal(reviews(app)[1].options.body,first);
  });
  await test('author revision review context, previous review evidence, and optimistic guards stay exact',async()=>{
    let current=receipt({status:'changes_requested',revision:2});
    const audit={revision:2,decision:'changes_requested',reviewer,notes:'Clarify how parameters are sampled.',evidence:'Checked the old sampler source.'};
    const app=await boot({hash:'#propose?proposal=proposal-one',hook:(url,options)=>{
      if(url.pathname.endsWith('/proposal-one'))return{proposal:current,author:user,revisions:[{revision:2,created_at:100,content_sha256:digest,specification:spec()}],reviews:[audit]};
      if(url.pathname.endsWith('/proposal-one/revisions')&&options.method==='POST'){current=receipt({revision:3,specification:JSON.parse(options.body).specification});return{proposal:current};}
    }});
    assert.match(app.$('#proposal-detail-content').textContent,/Clarify how parameters are sampled.*Checked the old sampler source/);
    app.$('#revise-proposal').click();await flush();assert.match(app.$('#proposal-revision-context').textContent,/Replacing revision 2.*earlier revision and reviews remain/);
    await upload(app);assert.equal(app.$('#submit-proposal').textContent,'Submit revised specification');app.$('#submit-proposal').click();await flush();
    const request=app.requests.find(r=>r.url.pathname.endsWith('/proposal-one/revisions')&&r.options.method==='POST');
    const payload=JSON.parse(request.options.body);assert.equal(payload.expected_revision,2);assert.equal(payload.content_sha256,digest);assert.ok(payload.idempotency_key);
    assert.equal(app.$('#proposal-review').hidden,true);assert.match(app.$('#proposal-revision-context').textContent,/Revision 3 submitted/);
    assert.equal(saves(app).length,0);
  });
  await test('another account cannot use an author revision route, including late ownership responses',async()=>{
    const app=await boot({account:reviewer,canReview:true,hash:'#propose?revise=proposal-one'});
    assert.equal(app.$('#upload-proposal').disabled,true);assert.equal(app.$('#submit-proposal').disabled,true);
    assert.match(app.$('#proposal-error').textContent,/Only the proposer/);
    const pending=deferred();const late=await boot({hash:'#propose?revise=proposal-one',hook:url=>url.pathname.endsWith('/proposal-one')?pending.promise:undefined});
    late.sandbox.setSession(late.envelope({user:reviewer,csrf_token:'other',capabilities:{review_protocol_proposals:true}}));
    pending.resolve({proposal:receipt(),author:user});await flush();
    assert.equal(late.$('#upload-proposal').disabled,true);assert.doesNotMatch(late.$('#proposal-revision-context').textContent,/Replacing revision 1 of Fixture/);
  });
  await test('losing reviewer capability clears private review content and discards a late decision response',async()=>{
    const pending=deferred();const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:url=>url.pathname.endsWith('/reviews')?pending.promise:undefined});
    fillReview(app);app.$('#proposal-review-form').dispatchEvent({type:'submit'});await flush();
    // The backend will now deny the private record as well.
    const original=app.sandbox.fetch;app.sandbox.fetch=async(path,options)=>String(path).endsWith('/proposal-one')?{ok:false,status:404,json:async()=>({detail:'Proposal not found.'})}:original(path,options);
    app.sandbox.setSession(app.envelope({user:reviewer,csrf_token:'same',capabilities:{review_protocol_proposals:false}}));
    pending.resolve({proposal:receipt({status:'changes_requested'}),review:{revision:1,content_sha256:digest,decision:'changes_requested'}});await flush();
    assert.equal(app.$('#proposal-review-form').hidden,true);assert.equal(app.$('#proposal-review-notes').value,'');
    assert.equal(app.$('#proposal-review-link').hidden,true);assert.doesNotMatch(app.$('#proposal-detail-content').textContent,/Fixture KEM|never_execute/);
  });
  await test('an uncertain committed review remains retryable after reopening a decided revision',async()=>{
    let current=receipt();let attempts=0;
    const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1&proposal=proposal-one',hook:(url,options)=>{
      if(url.pathname.endsWith('/proposal-one'))return{proposal:current,author:user};
      if(url.pathname.endsWith('/reviews')){const payload=JSON.parse(options.body);current={...current,status:payload.decision};attempts++;return attempts===1?{}:{proposal:current,review:{revision:1,content_sha256:digest,decision:payload.decision}};}
    }});
    fillReview(app);await recordReview(app);const payload=reviews(app)[0].options.body;
    await app.navigate('#about');await app.navigate('#propose?review=1&proposal=proposal-one');
    assert.equal(app.$('#proposal-review-form').hidden,false);assert.equal(app.$('#submit-proposal-review').textContent,'Retry review');
    assert.match(app.$('#proposal-review-status').textContent,/pending decision for revision 1/);
    await recordReview(app);assert.equal(reviews(app)[1].options.body,payload);assert.equal(app.$('#proposal-review-form').hidden,true);
  });
  await test('a stale author revision receives a conflict without silently targeting the newer revision',async()=>{
    const app=await boot({hash:'#propose?revise=proposal-one'});await upload(app);
    const original=app.sandbox.fetch;let attempted;
    app.sandbox.fetch=async(path,options)=>{
      if(String(path).endsWith('/proposal-one/revisions')){attempted=JSON.parse(options.body);return{ok:false,status:409,json:async()=>({detail:'The proposal changed. Review its latest revision.'})};}
      return original(path,options);
    };
    app.$('#submit-proposal').click();await flush();
    assert.equal(attempted.expected_revision,1);assert.equal(attempted.content_sha256,digest);
    assert.match(app.$('#proposal-error').textContent,/proposal changed/);assert.equal(app.$('#proposal-review').hidden,false);
    assert.equal(app.$('#upload-proposal').disabled,false);assert.equal(app.$('#submit-proposal').textContent,'Submit revised specification');
    assert.equal(app.requests.filter(r=>r.url.pathname.endsWith('/proposal-one')).length,1,'No silent rebasing or retry');
  });
  await test('review queue pagination uses the server cursor and appends distinct private records',async()=>{
    const app=await boot({account:reviewer,canReview:true,hash:'#propose?review=1',hook:url=>{
      if(url.pathname==='/api/protocol/review-queue')return url.searchParams.has('after')?{proposals:[{...receipt({id:'proposal-two'}),author:user}],next_after:null}:{proposals:[{...receipt(),author:user}],next_after:'proposal-one'};
    }});
    const more=app.$('#proposal-list').querySelector('button');assert.equal(more.textContent,'Load more proposals');more.click();await flush();
    assert.equal(app.$('#proposal-list').querySelectorAll('a').length,2);
    const requests=app.requests.filter(r=>r.url.pathname==='/api/protocol/review-queue');assert.equal(requests.length,2);assert.equal(requests[1].url.searchParams.get('after'),'proposal-one');
  });
  console.log(`\n${passed} protocol-proposal regressions passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

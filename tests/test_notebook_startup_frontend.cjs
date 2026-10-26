const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp,flush} = require('./frontend-dom.cjs');
const alice={id:'alice',handle:'alice'},bob={id:'bob',handle:'bob'};
const firstPath='/api/notebook/notes?limit=25&include_curated=true&scope=all';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const active=(envelope,user=alice,csrf='csrf')=>envelope({user,csrf_token:csrf,access:{mode:'beta',allowed:true,status:'active'},capabilities:{review_protocol_proposals:true}});
const lists=app=>app.requests.filter(row=>row.url.pathname==='/api/notebook/notes');
const summary=title=>{const revision={revision:1,content_sha256:'synthetic',created_at:100,publication_status:'approved',content:{title,claim:'Synthetic claim',tags:[],kind:'observation'}};return{id:title,owner:alice,can_edit:false,...revision,revisions:[revision]};};
async function boot({hash='#notebook',respond}={}){
  return createApp({schemes:[],hash,responder:(url,options,envelope)=>{
    const value=respond?.(url,options,envelope);if(value!==undefined)return value;
    if(url.pathname==='/api/session')return active(envelope);
    if(url.pathname==='/api/notebook/notes'||url.pathname==='/api/notebook/review-queue')return{notes:[],next_after:null};
  }});
}
async function finishAssets(app){
  for(const file of ['store.js','seed.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/notebook',file),'utf8'),app.sandbox,{filename:file});
  for(const node of app.document.querySelectorAll('link,script')){
    if((node.src||node.href||'').startsWith('/notebook/'))node.onload();
  }
  await flush();
}
let passed=0;
async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('first page request overlaps held assets and the real notebook consumes it exactly once',async()=>{
    const gate=deferred();const app=await boot({respond:url=>url.pathname==='/api/notebook/notes'?gate.promise:undefined});
    assert.equal(lists(app).length,1);assert.equal(lists(app)[0].url.pathname+lists(app)[0].url.search,firstPath);
    assert.equal(app.document.querySelector('#nb-note-list'),null,'Assets still held');
    await finishAssets(app);assert.equal(lists(app).length,1);assert.match(app.$('#nb-note-list').textContent,/Loading notes/);
    gate.resolve({notes:[summary('First result')],next_after:null});await flush();
    assert.match(app.$('#nb-note-list').textContent,/First result/);assert.equal(lists(app).length,1);
    await app.navigate('#about');await app.navigate('#notebook');assert.equal(lists(app).length,2,'Warm visits revalidate normally');
  });
  await test('completed first response is reused when assets finish without a serial duplicate',async()=>{
    const app=await boot();assert.equal(lists(app).length,1);await finishAssets(app);
    assert.match(app.$('#nb-note-list').textContent,/No notes/);assert.equal(lists(app).length,1);
  });
  await test('filtered and reviewer entry do not speculate the ordinary first page',async()=>{
    for(const hash of ['#notebook?tag=algebra','#notebook?filter=proposals','#reviews?section=publication']){
      const app=await boot({hash});assert.equal(lists(app).length,0);await finishAssets(app);
      if(hash.startsWith('#reviews')){
        assert.equal(lists(app).length,0);assert.equal(app.requests.filter(row=>row.url.pathname==='/api/notebook/review-queue').length,1);
      }else{
        assert.equal(lists(app).length,1);
        assert.equal(lists(app)[0].url.searchParams.get(hash.includes('tag=')?'tag':'proposals_only'),hash.includes('tag=')?'algebra':'true');
      }
    }
  });
  await test('a route change during asset loading cannot consume the old unfiltered result',async()=>{
    const gate=deferred();const app=await boot({respond:url=>url.pathname==='/api/notebook/notes'&&!url.searchParams.has('tag')?gate.promise:undefined});
    await app.navigate('#notebook?tag=algebra');await finishAssets(app);
    assert.equal(lists(app).length,2);assert.equal(lists(app)[1].url.searchParams.get('tag'),'algebra');
    gate.resolve({notes:[summary('Obsolete unfiltered result')]});await flush();
    assert.doesNotMatch(app.$('#nb-note-list').textContent,/Obsolete unfiltered/);
  });
  await test('account or same-account session changes discard earlier in-flight data',async()=>{
    for(const next of [bob,alice]){
      const gate=deferred();let calls=0;
      const app=await boot({respond:url=>url.pathname==='/api/notebook/notes'?++calls===1?gate.promise:{notes:[summary('Current session result')]}:undefined});
      app.sandbox.setSession(active(app.envelope,next,'rotated'));await flush();assert.equal(calls,2);
      await finishAssets(app);assert.match(app.$('#nb-note-list').textContent,/Current session result/);
      gate.resolve({notes:[summary('Earlier private result')]});await flush();assert.doesNotMatch(app.$('#nb-note-list').textContent,/Earlier private/);
    }
  });
  await test('revocation while assets are held never mounts earlier private content',async()=>{
    const gate=deferred();const app=await boot({respond:url=>url.pathname==='/api/notebook/notes'?gate.promise:undefined});
    app.window.location.reload=()=>{};
    app.sandbox.setSession(app.envelope({user:alice,csrf_token:'csrf',access:{mode:'beta',allowed:false,status:'revoked'}}));
    gate.resolve({notes:[summary('Earlier private result')]});await finishAssets(app);
    assert.equal(app.$('#access-workspace').hidden,false);assert.equal(app.$('#notebook-workspace').hidden,true);
    assert.equal(app.document.querySelector('#nb-note-list'),null);
  });
  await test('a completed failed early handoff is discarded before the visible notebook makes a fresh read',async()=>{
    const gate=deferred();let calls=0,online=false;
    const app=await boot({respond:url=>{
      if(url.pathname!=='/api/notebook/notes')return;
      if(++calls===1)return gate.promise;
      if(!online)throw new TypeError('Synthetic connection failure');
      return{notes:[],next_after:null};
    }});
    gate.reject(new TypeError('Synthetic interrupted early read'));await flush();
    assert.equal(calls,2,'The early read has exhausted its single transport recovery');
    assert.equal(vm.runInContext('notebookEarlyReads.size',app.sandbox),0);
    assert.equal(app.document.querySelector('#nb-note-list'),null,'Controller assets remain held');
    online=true;await finishAssets(app);
    assert.equal(calls,3,'Visible reader must start fresh, not consume the completed rejection');
    assert.match(app.$('#nb-note-list').textContent,/No notes/);
    assert.doesNotMatch(app.$('#nb-note-list').textContent,/could not be reached/);
    assert.equal(app.requests.some(row=>row.options.method==='POST'),false);
  });
  await test('persistent failure remains visible after bounded early and foreground reads, then explicit retry recovers',async()=>{
    let calls=0,online=false;
    const app=await boot({respond:url=>{
      if(url.pathname!=='/api/notebook/notes')return;
      calls++;
      if(!online)throw new TypeError('Synthetic offline');
      return{notes:[],next_after:null};
    }});
    assert.equal(calls,2);await finishAssets(app);
    assert.equal(calls,4,'Fresh visible read gets its own single bounded recovery');
    assert.match(app.$('#nb-note-list').textContent,/could not be reached/);
    assert.equal(app.$('#nb-note-list').querySelector('button').textContent,'Try again');
    await flush();assert.equal(calls,4,'No background retry loop');
    online=true;app.$('#nb-note-list').querySelector('button').click();await flush();
    assert.equal(calls,5);assert.match(app.$('#nb-note-list').textContent,/No notes/);
    assert.equal(app.requests.some(row=>row.options.method==='POST'),false);
  });
  await test('a response held beyond the short handoff window is revalidated after mount',async()=>{
    let calls=0;const app=await boot({respond:url=>url.pathname==='/api/notebook/notes'?{notes:[summary(++calls===1?'Old response':'Fresh response')]}:undefined});
    vm.runInContext('Date.now = () => '+(Date.now()+11000),app.sandbox);await finishAssets(app);
    assert.equal(calls,2);assert.match(app.$('#nb-note-list').textContent,/Fresh response/);assert.doesNotMatch(app.$('#nb-note-list').textContent,/Old response/);
  });
  console.log(`\n${passed} notebook startup checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

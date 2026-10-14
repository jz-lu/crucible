const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const schemes = [{id:'fixture',version:'2',name:'Fixture',profiles:[{id:'tier-20',instance_size:20}]}];
const milestone = (tier, overrides={}) => ({scheme_id:'fixture',scheme_name:'Fixture',scheme_version:'2',
  profile_id:'tier-'+tier,tier,submission_id:'receipt-'+tier,received_at:100+tier,is_current:true,
  scoring_eligible:true,place:1,counts_toward_bonus:false,...overrides});
const makePerson = (handle='alpha', firstTen={}) => ({handle,points:840,accepted_proposal_count:0,records:[],recent_recoveries:[],
  first_ten:{highlights:[milestone(8),milestone(20,{place:3,counts_toward_bonus:true})],
    history:[milestone(20,{place:3,counts_toward_bonus:true}),milestone(8)],
    pagination:{limit:20,offset:0,has_more:false},...firstTen}});
const getProfile = (url, options, envelope) => url.pathname==='/api/people/alpha' ? envelope({person:makePerson()}) : undefined;
let passed=0;
async function test(name, run) {await run();passed++;console.log('PASS '+name);}
(async()=>{
  await test('profiles show standout places and collapsed history without scoring annotations or extra requests',async()=>{
    const app=await createApp({schemes,hash:'#person/alpha',responder:getProfile});
    const section=app.$('#first-ten-milestones');
    assert.equal(section.querySelectorAll('.milestone-card').length,2);
    assert.match(section.textContent,/1st to reach Tier 8/);
    assert.match(section.textContent,/3rd to reach Tier 20/);
    assert.equal(section.querySelectorAll('.milestone-bonus').length,0);
    assert.doesNotMatch(section.textContent,/Current bonus|best bonus per scheme/);
    assert.equal(section.querySelector('.milestone-history').open,false);
    assert.equal(section.querySelector('.milestone-more').hidden,true);
    assert.equal(app.requests.some(r=>r.url.pathname.endsWith('/milestones')||r.options.method==='POST'),false);
  });
  await test('archived milestone opens its original receipt; names stay plain text',async()=>{
    const archived=milestone(12,{scheme_name:'<script>Example</script>',scheme_version:'1',is_current:false,submission_id:'old-receipt'});
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/alpha')return envelope({person:makePerson('alpha',{highlights:[archived],history:[archived]})});
      if(url.pathname==='/api/submissions/old-receipt')return envelope({submission:{id:'old-receipt',handle:'alpha',status:'correct',scheme_id:'fixture',scheme_version:'1',profile_id:'tier-12',instance_size:12,received_at:112,method:{name:'Original attack',pseudocode:'Original method'},compute:{hardware:'CPU'}}});
    }});
    assert.match(app.$('.milestone-card').textContent,/Archived · release 1/);
    assert.equal(app.$('.milestone-scheme').children.length,0);
    app.$('.milestone-card').click();await flush();
    assert.equal(app.$('#recovery-dialog').open,true);
    assert.match(app.$('#recovery-context').textContent,/version 1/);
    assert.match(app.$('#recovery-title').textContent,/Original attack/);
    assert.match(app.$('#recovery-content').textContent,/Original method/);
  });
  await test('explicit history paging appends unique receipts and keeps the fixed highlight set',async()=>{
    const initial=Array.from({length:20},(_,i)=>milestone(20-i));
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/alpha')return envelope({person:makePerson('alpha',{highlights:[milestone(20)],history:initial,pagination:{limit:20,offset:0,has_more:true}})});
      if(url.pathname.endsWith('/milestones'))return envelope({milestones:[milestone(1),milestone(21)],pagination:{limit:20,offset:20,has_more:false}});
    }});
    app.$('.milestone-more').click();await flush();
    assert.equal(app.requests.find(r=>r.url.pathname.endsWith('/milestones')).url.searchParams.get('offset'),'20');
    assert.equal(app.$('.milestone-history-list').querySelectorAll('.milestone-result').length,21);
    assert.equal(app.$('.milestone-highlights').querySelectorAll('.milestone-card').length,1);
    assert.equal(app.$('.milestone-more').hidden,true);
  });
  await test('late history responses cannot replace a different profile or a newly loaded copy of the same profile',async()=>{
    for(const destination of ['#person/beta','#person/alpha']){
      let release;
      const pending=new Promise(resolve=>{release=resolve;});
      const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>{
        if(url.pathname.endsWith('/milestones'))return pending;
        if(url.pathname.startsWith('/api/people/'))return envelope({person:makePerson(url.pathname.split('/').at(-1),{pagination:{limit:20,offset:0,has_more:true}})});
      }});
      app.$('.milestone-more').click();await flush();
      await app.navigate('#about');await app.navigate(destination);
      release(app.envelope({milestones:[milestone(99)],pagination:{limit:20,offset:20,has_more:false}}));await flush();
      assert.doesNotMatch(app.$('#first-ten-milestones').textContent,/Tier 99/);
      assert.equal(app.$('.milestone-more').disabled,false);
    }
  });
  await test('failed or invalid pages retain existing results and allow retry',async()=>{
    let calls=0;
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/alpha')return envelope({person:makePerson('alpha',{pagination:{limit:20,offset:0,has_more:true}})});
      if(url.pathname.endsWith('/milestones')){
        calls++;
        if(calls===1)throw new Error('Offline');
        return envelope({milestones:[milestone(21,{place:calls===2?11:10})],pagination:{limit:20,offset:20,has_more:false}});
      }
    }});
    for(let i=0;i<2;i++){
      app.$('.milestone-more').click();await flush();
      assert.equal(app.$('.milestone-history-list').querySelectorAll('.milestone-result').length,2);
      assert.equal(app.$('.milestone-history').querySelector('[role="alert"]').hidden,false);
      assert.equal(app.$('.milestone-more').disabled,false);
    }
    app.$('.milestone-more').click();await flush();
    assert.equal(app.$('.milestone-history-list').querySelectorAll('.milestone-result').length,3);
    assert.match(app.$('.milestone-history-list').textContent,/10th to reach Tier 21/);
    assert.equal(app.$('.milestone-history').querySelector('[role="alert"]').hidden,true);
  });
  await test('empty milestone history stays neutral and introduces no fabricated achievements',async()=>{
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:makePerson('alpha',{highlights:[],history:[]})}):undefined});
    assert.match(app.$('#first-ten-milestones').textContent,/No First 10 milestones yet/);
    assert.equal(app.$('#first-ten-milestones').querySelectorAll('button').length,0);
  });
  console.log(`${passed} milestone frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

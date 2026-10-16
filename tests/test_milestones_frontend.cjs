const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const schemes = [{id:'fixture',version:'2',name:'Fixture',profiles:[{id:'tier-20',instance_size:20}]}];
const first = (tier, overrides={}) => ({scheme_id:'fixture',scheme_name:'Fixture',scheme_version:'2',
  profile_id:'tier-'+tier,tier,submission_id:'receipt-'+tier,received_at:100+tier,is_current:true,
  scoring_eligible:true,place:1,...overrides});
const makePerson = (firstRecords=[]) => ({handle:'alpha',points:840,accepted_proposal_count:0,records:[],recent_recoveries:[],
  first_records:firstRecords,first_ten:{highlights:[first(8),first(20,{place:3})],history:[first(20,{place:3}),first(8)]}});
let passed=0;
async function test(name, run) {await run();passed++;console.log('PASS '+name);}
(async()=>{
  await test('profile uses highest first-place records and hides the legacy first-ten list behind no extra requests',async()=>{
    const records=[first(19),first(5,{scheme_id:'other',scheme_name:'Other scheme'})];
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:makePerson(records)}):undefined});
    const section=app.$('#first-records');
    assert.equal(section.tagName,'DETAILS');
    assert.equal(section.open,false);
    assert.match(section.querySelector('summary').textContent,/First to reach2 schemes/);
    const table=section.querySelector('table');
    assert.equal(table.getAttribute('aria-label'),'Highest first-place stage per scheme');
    assert.deepEqual(table.querySelectorAll('th').map(cell=>cell.textContent),['Scheme','Stage','Reached']);
    assert.equal(table.querySelector('tbody').querySelectorAll('tr').length,2);
    assert.match(section.textContent,/Stage 19/);
    assert.doesNotMatch(section.textContent,/Stage 8|Stage 20|3rd|First 10|Current bonus/);
    assert.equal(app.requests.some(r=>r.url.pathname.endsWith('/milestones')||r.options.method==='POST'),false);
  });
  await test('first-place result opens the original receipt and renders supplied names as plain text',async()=>{
    const record=first(12,{scheme_name:'<script>Example</script>',scheme_version:'1',is_current:false,submission_id:'old-receipt'});
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>{
      if(url.pathname==='/api/people/alpha')return envelope({person:makePerson([record])});
      if(url.pathname==='/api/submissions/old-receipt')return envelope({submission:{id:'old-receipt',handle:'alpha',status:'correct',scheme_id:'fixture',scheme_version:'1',profile_id:'tier-12',instance_size:12,received_at:112,method:{name:'Original attack',pseudocode:'Original method'},compute:{hardware:'CPU'}}});
    }});
    assert.match(app.$('#first-records').textContent,/Archived · release 1/);
    assert.equal(app.$('.milestone-scheme').children.length,0);
    const button=app.$('#first-records').querySelector('.milestone-result');
    assert.equal(button.getAttribute('aria-haspopup'),'dialog');
    assert.match(button.getAttribute('aria-label'),/first to reach Stage 12/);
    button.click();await flush();
    assert.equal(app.$('#recovery-dialog').open,true);
    assert.match(app.$('#recovery-context').textContent,/version 1/);
    assert.match(app.$('#recovery-title').textContent,/Original attack/);
  });
  await test('non-first and incomplete records do not appear as achievements',async()=>{
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:makePerson([first(12,{place:2}),first(13,{submission_id:''}),first(14)])}):undefined});
    assert.match(app.$('#first-records').textContent,/1 scheme/);
    assert.match(app.$('#first-records').textContent,/Stage 14/);
    assert.doesNotMatch(app.$('#first-records').textContent,/Stage 12|Stage 13/);
  });
  await test('empty first records stay neutral even when legacy top-ten records exist',async()=>{
    const app=await createApp({schemes,hash:'#person/alpha',responder:(url,options,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:makePerson()}):undefined});
    assert.match(app.$('#first-records').textContent,/No first-place records yet/);
    assert.equal(app.$('#first-records').querySelectorAll('button').length,0);
  });
  await test('first records use the configuration’s current stage label without changing its point weight',async()=>{
    const remapped={...schemes[0],default_progression:{stage_count:19},profiles:[{id:'tier-20',instance_size:20,stage_number:19,stage_label:'Stage 19'}]};
    const app=await createApp({schemes:[remapped],hash:'#person/alpha',responder:(url,options,envelope)=>url.pathname==='/api/people/alpha'?envelope({person:makePerson([first(20)])}):undefined});
    assert.equal(app.$('#first-records').querySelector('.milestone-result').textContent,'Stage 19');
    assert.match(app.$('#first-records').querySelector('.milestone-result').getAttribute('aria-label'),/first to reach Stage 19/);
    assert.doesNotMatch(app.$('#first-records').textContent,/Stage 20/);
  });
  console.log(`${passed} first-record frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const profile = {id:'p1',instance_size:1,parameters:{large:'9007199254740993'}};
const old = {id:'fixture',version:'old',profiles:[profile]};
const scheme = {id:'fixture',version:'current',name:'Fixture',profiles:[profile],previous_releases:[old]};
const row = version => ({id:'fixture',version,participant_count:2,recovered_profile_ids:['p1'],largest_recovered_profile_id:'p1',next_profile_id:null,first_recovery:{handle:'alpha',profile_id:'p1'}});
const progress = () => ({projection:'progress',catalogue_revision:'stable',releases:[row('current'),row('old')]});
const snapshot = app => JSON.parse(vm.runInContext('JSON.stringify({schemes:state.schemes,revision:state.catalogueRevision})',app.sandbox));
async function fixture(respond) {
  const app = await createApp({schemes:[scheme],hash:'#about',responder:(url,opts,envelope)=>{
    if (url.pathname === '/api/schemes') return envelope(respond?.(url) ?? {schemes:[scheme],catalogue_revision:'stable'});
  }});
  // About is independent of scheme metadata; explicitly establish this
  // fixture's catalogue before exercising incremental progress refreshes.
  await app.sandbox.loadAuthorizedCatalogue();
  return app;
}
async function metadataFixture(hash) {
  let deployed = false;
  const value = () => ({...scheme, ...row('current'),
    summary: deployed ? 'Revised summary' : 'Original summary',
    profiles: [{...profile, shared_secret_bytes:32, stage_number:1,
      correctness:{kind:'perfect',failure_bound:'0'}, encoding:{shared_secret_bytes:32}}],
    profile_metrics:{p1:{public_key_bytes:deployed ? 20 : 10,ciphertext_bytes:8}},
    overview:{security:deployed ? 'Revised security discussion' : 'Original security discussion'},
    protocol:{construction:deployed ? 'Revised construction' : 'Original construction'}});
  const app = await createApp({hash,schemes:[value()],responder:(url,opts,envelope)=>{
    if (url.pathname === '/api/schemes') return envelope(url.searchParams.has('progress_only') ?
      {...progress(),catalogue_revision:deployed ? 'revised' : 'stable'} :
      {schemes:[value()],catalogue_revision:deployed ? 'revised' : 'stable'});
  }});
  return {app,deploy:()=>{deployed=true;}};
}
(async()=>{
  {
    const calls=[];
    const app=await fixture(url=>{calls.push(url.search);return url.searchParams.has('progress_only') ? progress() : undefined;});
    await app.sandbox.refreshProfiles({background:true});
    assert.deepEqual(calls,['?projection=navigation','?progress_only=true']);
    const current=snapshot(app).schemes[0];
    assert.equal(current.participant_count,2);
    assert.equal(current.previous_releases[0].first_recovery.handle,'alpha');
    assert.equal(current.profiles[0].parameters.large,'9007199254740993');
  }
  for (const corrupt of [data=>{data.catalogue_revision='changed';},data=>{data.releases.pop();},
      data=>{data.releases[1]=data.releases[0];},data=>{data.releases[1]=null;},data=>{delete data.releases[0].first_recovery;}]) {
    const calls=[];
    const app=await fixture(url=>{calls.push(url.search);if(url.searchParams.has('progress_only')){const data=progress();corrupt(data);return data;}});
    await app.sandbox.refreshProfiles({background:true});
    assert.deepEqual(calls,['?projection=navigation','?progress_only=true','?projection=navigation']);
    assert.equal(snapshot(app).schemes[0].participant_count,undefined);
  }
  {
    const app=await fixture(url=>{if(url.searchParams.has('progress_only')){const data=progress();data.releases[0].profiles=[];data.releases[0].protocol={source_url:'bad'};return data;}});
    await app.sandbox.refreshProfiles({background:true});
    assert.equal(snapshot(app).schemes[0].profiles.length,1);
    assert.equal(snapshot(app).schemes[0].protocol,undefined);
  }
  {
    const app=await fixture();let resolve;
    app.sandbox.fetch=()=>new Promise(r=>{resolve=r;});
    const pending=app.sandbox.refreshProfiles({background:true});await flush();
    vm.runInContext('state.catalogueRequest++;state.schemes=[];state.catalogueRevision=null;',app.sandbox);
    resolve({ok:true,status:200,json:async()=>app.envelope(progress())});
    assert.equal(await pending,false);assert.deepEqual(snapshot(app),{schemes:[],revision:null});
  }
  {
    const {app,deploy}=await metadataFixture('#scheme/fixture/current');
    const report = app.$('#overview-chart-public').querySelector('select[aria-label="Report"]');
    report.value = 'legacy'; report.dispatchEvent({type:'change'}); await flush();
    assert.equal(app.$('#overview-chart-public').querySelector('.chart-point').dataset.value,'10');
    assert.equal(app.$('#overview-security').textContent,'Original security discussion');
    deploy();await app.sandbox.refreshProfiles({background:true});
    assert.equal(snapshot(app).revision,'revised');
    const updatedReport = app.$('#overview-chart-public').querySelector('select[aria-label="Report"]');
    updatedReport.value = 'legacy'; updatedReport.dispatchEvent({type:'change'}); await flush();
    assert.equal(app.$('#overview-chart-public').querySelector('.chart-point').dataset.value,'20');
    assert.equal(app.$('#overview-security').textContent,'Revised security discussion');
    assert.equal(app.$('#overview-summary').textContent,'Revised summary');
  }
  for (const editing of [false,true]) {
    const {app,deploy}=await metadataFixture('#challenge/fixture/current?profile=p1&view=protocol');
    assert.equal(app.$('#protocol-construction').textContent,'Original construction');
    if (editing) app.$('#size-slider').focus();
    deploy();await app.sandbox.refreshProfiles({background:true});
    if (editing) {
      assert.equal(app.$('#protocol-construction').textContent,'Original construction','Active editing defers the metadata rerender');
      app.document.body.focus();
      await app.sandbox.refreshProfiles({background:true});
    }
    assert.equal(app.$('#protocol-construction').textContent,'Revised construction');
    assert.equal(vm.runInContext('state.profileMetadataPending',app.sandbox),null);
  }
  console.log('PASS compact refresh, exact release merge, fallback, metadata preservation, same-release metadata rerender, deferred protocol updates and stale reply guard');
})().catch(error=>{console.error(error);process.exitCode=1;});

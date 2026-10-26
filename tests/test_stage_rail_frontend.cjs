// Current Frodo ladder metadata only; no native generation or real recoveries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createApp} = require('./frontend-dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../crucible/lattice_frodo_v3.py'), 'utf8');
const version = source.match(/^SCHEME_VERSION = "([^"]+)"/m)[1];
const dimensions = source.match(/^DIMENSIONS = \(([^)]+)\)/m)[1].split(',').map(Number);
assert.equal(version, 'anchor-research-v3');
assert.equal(dimensions.length, 80);
assert.match(source, /"record_inheritance": False/);
const profiles = dimensions.map((n, index) => ({id:`frodo-lwe-v3-tier-${index + 1}`,
  instance_size:index + 1, stage_number:index + 1, parameter_bits:n,
  profile_kind:'research-tier', record_inheritance:false, stage_label:`Stage ${index + 1}`,
  parameters:{n}, shared_secret_bytes:16, encoding:{shared_secret_bytes:16}, correctness:{}}));
const id = stage => profiles[stage - 1].id;
function release(recovered = [2, 8], overrides = {}) {
  return {id:'frodokem', version, name:'FrodoKEM', selection_kind:'tier', record_inheritance:false,
    profiles, protocol:{}, profile_metrics:{}, participant_count:1,
    recovered_profile_ids:recovered.map(id), largest_recovered_profile_id:recovered.length ? id(Math.max(...recovered)) : null,
    next_profile_id:id(1), ...overrides};
}
const route = (value, stage = 4) => `#challenge/${value.id}/${value.version}?profile=${value.profiles[stage - 1].id}&view=protocol`;
const track = app => app.$('#size-slider').style.cssText;
const point = (value, count = 80) => `calc(7px + (100% - 14px) * ${value / (count - 1)})`;
const greenStops = value => (value.split('; --recovery-thumb:')[0].match(/#278451/g) || []).length;
async function fixture(value = release(), stage = 4) {
  return createApp({schemes:[value], hash:route(value, stage)});
}
let passed = 0;
async function test(name, run) {
  let timeout;
  try { await Promise.race([run(), new Promise((_, reject) => {timeout=setTimeout(()=>reject(new Error('Timed out: '+name)), 10000);})]); }
  finally {clearTimeout(timeout);}
  passed++; console.log('PASS ' + name);
}
(async()=>{
  await test('Frodo exact recoveries color separate cells without claiming the intervening stages', async()=>{
    const app=await fixture(); const css=track(app);
    assert.equal(app.$('#size-slider').max,'79');
    assert.match(css,/--recovered-through: 0%; --recovery-track: linear-gradient/);
    assert.equal(greenStops(css),4,'Two distinct recovered cells, not a single prefix');
    for (const position of [0.5,1.5,6.5,7.5]) assert.ok(css.includes(point(position)));
    assert.ok(css.includes(`var(--line) ${point(1.5)}, var(--line) ${point(6.5)}`),'Unsolved gap remains gray');
    const buttons=app.$('#size-options').querySelectorAll('button');
    assert.deepEqual(buttons.filter(button=>button.classList.contains('is-prior')).map(button=>Number(button.textContent)),[2]);
    assert.doesNotMatch(buttons.find(button=>button.textContent==='3').getAttribute('aria-label'),/recovered|earlier stage/);
    assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
  });
  await test('endpoint recoveries stop at their own cells and keep the middle unsolved', async()=>{
    const app=await fixture(release([1,80])); const css=track(app);
    assert.ok(css.includes('#278451 0%'));
    assert.ok(css.includes('#278451 100%'));
    assert.ok(css.includes(`var(--line) ${point(0.5)}, var(--line) ${point(78.5)}`));
    assert.equal(greenStops(css),4);
  });
  await test('adjacent exact recoveries merge without coloring the next unsolved cell', async()=>{
    const app=await fixture(release([2,3])); const css=track(app);
    assert.equal(greenStops(css),2);
    assert.ok(css.includes(`#278451 ${point(0.5)}, #278451 ${point(2.5)}`));
  });
  await test('all registered stages recovered produce one complete green rail', async()=>{
    const app=await fixture(release(Array.from({length:80},(_,index)=>index+1)));
    assert.equal(greenStops(track(app)),2);
    assert.ok(track(app).includes('#278451 0%, #278451 100%'));
  });
  await test('a single-stage exact rail avoids invalid arithmetic', async()=>{
    const app=await fixture(release([1],{profiles:[profiles[0]]}),1);
    assert.equal(app.$('#size-slider').disabled,true);
    assert.ok(track(app).includes('#278451 0%, #278451 100%'));
    assert.doesNotMatch(track(app),/NaN|Infinity|calc\(/);
  });
  await test('an explicit empty recovery list overrides stale largest and legacy records', async()=>{
    const app=await fixture(release([],{largest_recovered_profile_id:id(80),profile_recoveries:[{profile_id:id(2)}]}));
    assert.equal(track(app),'--recovered-through: 0%');
    assert.equal(app.$('#size-options').querySelectorAll('.is-prior').length,0);
  });
  await test('unknown IDs cannot add colors or text to the generated track', async()=>{
    const app=await fixture(release([],{recovered_profile_ids:['another-release', ');color:red'],largest_recovered_profile_id:'another-release'}));
    assert.equal(track(app),'--recovered-through: 0%');
  });
  await test('legacy exact record metadata colors only its registered positions', async()=>{
    const value=release([],{profile_recoveries:[{profile_id:id(8)}],largest_recovered_profile_id:id(8)});
    delete value.recovered_profile_ids;
    const app=await fixture(value);
    assert.equal(greenStops(track(app)),2);
    assert.ok(track(app).includes(`#278451 ${point(6.5)}, #278451 ${point(7.5)}`));
    delete value.profile_recoveries;
    const largestOnly=await fixture(value);
    assert.equal(track(largestOnly),track(app));
  });
  await test('inherited prefix remains unchanged and clears exact segments after release navigation', async()=>{
    const exact=release();
    const inherited=release([8],{id:'fixture-inherited',version:'1',record_inheritance:true,
      profiles:profiles.map(profile=>({...profile,record_inheritance:true}))});
    const app=await createApp({schemes:[exact,inherited],hash:route(exact)});
    assert.match(track(app),/--recovery-track/);
    await app.navigate(route(inherited));
    assert.doesNotMatch(track(app),/--recovery-track/);
    assert.equal(track(app),`--recovered-through: ${point(7)}; --recovery-thumb: #278451`);
    await app.navigate(route(exact));
    assert.match(track(app),/--recovery-track/);
  });
  await test('the selected exact recovered stage stays visible through a green thumb, with gaps reset', async()=>{
    const value=release(); const app=await fixture(value,2);
    assert.match(track(app),/--recovery-thumb: #278451/);
    await app.navigate(route(value,3));
    assert.doesNotMatch(track(app),/--recovery-thumb/);
    await app.navigate(route(value,8));
    assert.match(track(app),/--recovery-thumb: #278451/);
  });
  await test('slider preview thumb follows the preview without changing the selected profile', async()=>{
    const app=await fixture();
    assert.doesNotMatch(track(app),/--recovery-thumb/);
    app.sandbox.renderProfiles(profiles[1]);
    assert.match(track(app),/--recovery-thumb: #278451/);
    app.sandbox.renderProfiles(profiles[2]);
    assert.doesNotMatch(track(app),/--recovery-thumb/);
    app.sandbox.renderProfiles();
    assert.equal(app.$('#size-slider-value').textContent,'4');
    assert.doesNotMatch(track(app),/--recovery-thumb/);
    assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
  });
  await test('inherited thumb follows the declared prefix and clears beyond it', async()=>{
    const value=release([8],{record_inheritance:true,
      profiles:profiles.map(profile=>({...profile,record_inheritance:true}))});
    const app=await fixture(value,4);
    assert.match(track(app),/--recovery-thumb: #278451/);
    await app.navigate(route(value,9));
    assert.doesNotMatch(track(app),/--recovery-thumb/);
  });
  await test('an intermediate configuration cannot color the different slider position used for display', async()=>{
    const intermediate={...profiles[1],id:'frodo-intermediate',instance_size:2.5,
      stage_number:null,stage_position:2.5,stage_context:{before_stage:2,after_stage:3}};
    const value=release([2],{default_progression:{stage_count:80},
      profiles:[...profiles,intermediate],recovered_profile_ids:[id(2),intermediate.id]});
    const app=await createApp({schemes:[value],hash:`#challenge/frodokem/${version}?profile=${intermediate.id}&view=protocol`});
    assert.equal(app.$('#size-slider-value').textContent,'—');
    assert.doesNotMatch(track(app),/--recovery-thumb/);
  });
  await test('gradient output is bounded by registered profiles, not arbitrary receipt lists', async()=>{
    const app=await fixture();
    assert.equal(app.sandbox.exactRecoveryTrack([],new Set([id(1)])),null);
    assert.equal(app.sandbox.exactRecoveryTrack(Array.from({length:1025},()=>profiles[0]),new Set([id(1)])),null);
    const unknown=new Set(Array.from({length:10000},(_,index)=>'unregistered-'+index));
    assert.equal(app.sandbox.exactRecoveryTrack(profiles,unknown),null);
    const css=fs.readFileSync(path.join(__dirname,'../web/styles.css'),'utf8');
    for(const selector of ['#size-slider::-webkit-slider-runnable-track','#size-slider::-moz-range-track']) {
      const rule=css.slice(css.indexOf(selector),css.indexOf('}',css.indexOf(selector)));
      assert.match(rule,/background:\s*var\(--recovery-track, linear-gradient/);
    }
    for(const selector of ['#size-slider::-webkit-slider-thumb','#size-slider::-moz-range-thumb']) {
      const rule=css.slice(css.indexOf(selector),css.indexOf('}',css.indexOf(selector)));
      assert.match(rule,/background:\s*var\(--recovery-thumb,\s*var\(--primary\)\)/);
    }
  });
  console.log(`${passed} stage-rail checks passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});

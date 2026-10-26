// Current Frodo/ML-KEM ladder metadata only; no native generation or real recoveries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
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
const tick = (value, sign, count = 80) => `calc(${point(value,count)} ${sign} min(2px, calc((100% - 14px) * ${1 / (4 * (count - 1))})))`;
const greenStops = value => (value.split('; --recovery-thumb:')[0].match(/#278451/g) || []).length;
async function fixture(value = release(), stage = 4) {
  return createApp({schemes:[value], hash:route(value, stage)});
}
const moduleSource = fs.readFileSync(path.join(__dirname, '../crucible/lattice_module_v4.py'), 'utf8');
const moduleVersion = moduleSource.match(/^SCHEME_VERSION = "([^"]+)"/m)[1];
const degrees = moduleSource.match(/^DEGREES = \(([^)]+)\)/m)[1].split(',').map(Number);
assert.equal(moduleVersion, 'compressed-research-v4');
assert.deepEqual(degrees, [8, 16, 32, 64, 128, 256]);
assert.match(moduleSource, /"record_inheritance": False/);
const moduleProfiles = degrees.map((n, index) => ({...profiles[index], id:`module-lwe-v4-tier-${index + 1}`,
  parameter_bits:n * 2, parameters:{n,rank:2}, shared_secret_bytes:32, encoding:{shared_secret_bytes:32}}));
const moduleId = stage => moduleProfiles[stage - 1].id;
async function moduleFixture(initial = [1], stage = 3) {
  let recovered = initial;
  const progress = () => ({id:'ml-kem',version:moduleVersion,participant_count:1,
    recovered_profile_ids:recovered.map(moduleId),
    largest_recovered_profile_id:recovered.length ? moduleId(Math.max(...recovered)) : null,
    next_profile_id:moduleProfiles.find((_, index) => !recovered.includes(index + 1))?.id ?? null,
    first_recovery:null});
  const value = () => ({...release([]),...progress(),name:'Module lattice encryption',profiles:moduleProfiles});
  const app = await createApp({schemes:[value()],hash:route(value(),stage),responder:(url,options,envelope)=>{
    if (url.pathname === '/api/schemes') return envelope(url.searchParams.has('progress_only') ?
      {projection:'progress',catalogue_revision:'module-stable',releases:[progress()]} :
      {schemes:[value()],catalogue_revision:'module-stable'});
  }});
  return {app,update:async stages=>{recovered=stages;await app.sandbox.refreshProfiles({background:true});}};
}
let passed = 0;
async function test(name, run) {
  let timeout;
  try { await Promise.race([run(), new Promise((_, reject) => {timeout=setTimeout(()=>reject(new Error('Timed out: '+name)), 10000);})]); }
  finally {clearTimeout(timeout);}
  passed++; console.log('PASS ' + name);
}
(async()=>{
  await test('Frodo isolated recoveries have bounded centered ticks without claiming intervening stages', async()=>{
    const app=await fixture(); const css=track(app);
    assert.equal(app.$('#size-slider').max,'79');
    assert.match(css,/--recovered-through: 0%; --recovery-track: linear-gradient/);
    assert.equal(greenStops(css),4,'Two distinct recovered ticks, not a single prefix');
    for (const position of [1,7]) for (const sign of ['-','+']) assert.ok(css.includes(tick(position,sign)));
    assert.ok(css.includes(`var(--line) ${tick(1,'+')}, var(--line) ${tick(7,'-')}`),'Unsolved gap remains gray');
    const buttons=app.$('#size-options').querySelectorAll('button');
    assert.deepEqual(buttons.filter(button=>button.classList.contains('is-prior')).map(button=>Number(button.textContent)),[2]);
    assert.doesNotMatch(buttons.find(button=>button.textContent==='3').getAttribute('aria-label'),/recovered|earlier stage/);
    assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
  });
  await test('isolated endpoint recoveries have bounded ticks and keep the middle unsolved', async()=>{
    const app=await fixture(release([1,80])); const css=track(app);
    assert.ok(css.includes(`#278451 ${tick(0,'-')}`));
    assert.ok(css.includes(`#278451 ${tick(79,'+')}`));
    assert.ok(css.includes(`var(--line) ${tick(0,'+')}, var(--line) ${tick(79,'-')}`));
    assert.equal(greenStops(css),4);
  });
  await test('adjacent exact recoveries end at thumb centers without extending into unsolved stages', async()=>{
    const app=await fixture(release([2,3])); const css=track(app);
    assert.equal(greenStops(css),2);
    assert.ok(css.includes(`#278451 ${point(1)}, #278451 ${point(2)}`));
  });
  await test('all registered stages recovered span exactly the first to last thumb centers', async()=>{
    const app=await fixture(release(Array.from({length:80},(_,index)=>index+1)));
    assert.equal(greenStops(track(app)),2);
    assert.ok(track(app).includes(`#278451 ${point(0)}, #278451 ${point(79)}`));
  });
  await test('a single-stage exact rail avoids invalid arithmetic', async()=>{
    const app=await fixture(release([1],{profiles:[profiles[0]]}),1);
    assert.equal(app.$('#size-slider').disabled,true);
    assert.equal(track(app),'--recovered-through: 0%; --recovery-thumb: #278451');
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
    assert.ok(track(app).includes(`#278451 ${tick(7,'-')}, #278451 ${tick(7,'+')}`));
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
  await test('fresh ML-KEM records repaint a focused rail without moving its selection or replacing controls', async()=>{
    const {app,update}=await moduleFixture([1,2,3],4);
    const slider=app.$('#size-slider'), buttons=app.$('#size-options').querySelectorAll('button');
    const hash=app.location.hash;
    assert.ok(track(app).includes(`#278451 ${point(2,6)}`),'Stages 1–3 end exactly at stage 3');
    assert.ok(!track(app).includes(point(2.5,6)),'No endpoint at stage 3.5');
    assert.doesNotMatch(track(app),/--recovery-thumb/);
    slider.focus();
    await update([1,2,3,4]);
    assert.ok(track(app).includes(`#278451 ${point(3,6)}`),'Fresh stage 4 extends to its exact center immediately');
    assert.match(track(app),/--recovery-thumb: #278451/);
    assert.equal(slider.value,'3');
    assert.equal(app.$('#size-slider-value').textContent,'4');
    assert.equal(app.document.activeElement,slider);
    assert.equal(app.location.hash,hash);
    assert.equal(vm.runInContext('state.profile.id',app.sandbox),moduleId(4));
    assert.equal(app.$('#size-options').querySelectorAll('button')[0],buttons[0],'No structural rerender during input focus');
    assert.ok(vm.runInContext('state.profileMetadataPending',app.sandbox));
    const refreshed=track(app);
    app.document.body.focus();
    await update([1,2,3,4]);
    assert.equal(track(app),refreshed);
    assert.equal(vm.runInContext('state.profileMetadataPending',app.sandbox),null);
    assert.equal(slider.value,'3');
    assert.equal(app.location.hash,hash);
    assert.equal(app.requests.some(request=>request.options.method==='POST'),false);
  });
  await test('a fresh exact recovery repaint preserves an unsent jump draft and its focus', async()=>{
    const {app,update}=await moduleFixture([1],3);
    const input=app.$('#profile-select'), hash=app.location.hash;
    input.value='5'; input.focus();
    await update([1,3]);
    assert.equal(input.value,'5');
    assert.equal(app.document.activeElement,input);
    assert.equal(app.$('#size-slider').value,'2');
    assert.match(track(app),/--recovery-thumb: #278451/);
    assert.ok(track(app).includes(`var(--line) ${tick(0,'+',6)}, var(--line) ${tick(2,'-',6)}`),'Unrecovered stage 2 stays gray');
    app.document.body.focus();
    await update([1,3]);
    assert.equal(input.value,'5','Deferred metadata rendering must not consume the draft');
    assert.equal(app.location.hash,hash);
    assert.equal(vm.runInContext('state.profile.id',app.sandbox),moduleId(3));
  });
  await test('fresh records style the active slider preview, not a different committed stage', async()=>{
    const {app,update}=await moduleFixture([1],3);
    const slider=app.$('#size-slider'), hash=app.location.hash;
    slider.focus(); slider.value='3'; slider.dispatchEvent({type:'input'});
    assert.equal(app.$('#size-slider-value').textContent,'4');
    await update([1,4]);
    assert.match(track(app),/--recovery-thumb: #278451/);
    await update([1,3]);
    assert.doesNotMatch(track(app),/--recovery-thumb/,'Stage 3 recovery must not color the stage 4 preview thumb');
    assert.equal(slider.value,'3');
    assert.equal(app.document.activeElement,slider);
    assert.equal(app.$('#size-slider-value').textContent,'4');
    assert.equal(vm.runInContext('state.profile.id',app.sandbox),moduleId(3));
    assert.equal(app.location.hash,hash);
    slider.dispatchEvent({type:'pointercancel'});
    assert.equal(slider.value,'2');
    assert.equal(app.$('#size-slider-value').textContent,'3');
    assert.match(track(app),/--recovery-thumb: #278451/);
  });
  await test('recovery-only repaint rejects stale release objects and changed control geometry', async()=>{
    const {app,update}=await moduleFixture([1],3);
    const stale=app.sandbox.selectedScheme();
    app.$('#size-slider').focus(); await update([1,3]);
    const fresh=track(app);
    app.sandbox.refreshProfileRecoveryStyles(stale);
    assert.equal(track(app),fresh,'An old release object cannot restore old colors');
    const current=app.sandbox.selectedScheme();
    current.profiles=[...current.profiles].reverse().map((profile,index)=>({...profile,stage_number:index+1}));
    current.recovered_profile_ids=[];
    app.sandbox.refreshProfileRecoveryStyles(current);
    assert.equal(track(app),fresh,'Different registered order waits for full control rendering');
    current.profiles=moduleProfiles;
    vm.runInContext('state.access={allowed:false};',app.sandbox);
    app.sandbox.refreshProfileRecoveryStyles(current);
    assert.equal(track(app),fresh,'This helper never paints a denied context');
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

// Real application behavior with exact-revision fixture evidence. The DOM double
// checks navigation, scope and lifecycle ownership; browser QA covers appearance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const graphHelper = fs.readFileSync(path.join(__dirname, '../web/graph-points.js'), 'utf8');
const user = {id: 'assumptions-reader', handle: 'reader'};
const computation = 'crucible-bit-ram-v1';
const pin = section => ({id: '00000000-0000-4000-8000-000000000001', revision: 4,
  content_sha256: 'a'.repeat(64), note_section: section});
const premise = (label, title, section, extra = {}) => ({id: section, label, title,
  status: 'unproved', source: pin(section), stages: [1, 2, 3], ...extra});
const scheme = () => ({id: 'assumption-fixture', version: 'v1', name: 'Assumption fixture', protocol: {},
  next_profile_id: 'p1', profiles: [1, 2, 3].map(n => ({id: 'p'+n, profile_id: 'p'+n, instance_size: n,
    parameters: {n}, initial_security_estimate: {model_id: 'frozen', bits: 20+n}})),
  initial_security_model: {id: 'frozen', label: 'Frozen reference', unit: 'log2 operations', kind: 'attack-reference'}});
const packet = () => ({format: 'crucible-note-justifications-v1', scheme_id: 'assumption-fixture', version: 'v1',
  sections: {security: pin('security'), correctness: pin('correctness')},
  scheme_assumptions: {
    security: {source: pin('security'), incomplete: false, argument_status:'conditional', scope:'Fresh passive shared-key recovery.', assumptions: [
      premise('A-11', 'Structured search remains hard', 'hardness'),
      premise('A-2', 'Shared algebraic premise', 'algebra')]},
    correctness: {source: pin('correctness'), incomplete: false, argument_status:'conditional', scope:'Fresh honest agreement.', assumptions: [
      premise('A-4', 'Decoder tail bound', 'decoding', {stages: [2, 3]})]},
  },
  graph_models: {'ram-fixture': {metric: 'ram', source: pin('attack-specification'), assumptions: [
    premise('A-88', 'Attack-only independence premise', 'attack-premise', {profile_ids: ['p1']}),
    premise('A-89', 'Other stage premise', 'other-stage', {profile_ids: ['p3']}),
    premise('A-90', 'An unresolved proof obligation', 'proof-gap', {status: 'incomplete'}),
  ]}},
  attack_comparison: {format: 'crucible-ram-attack-comparison-v1', scheme_id: 'assumption-fixture', version: 'v1',
    computation_model_id: computation, analysis_sha256: 'b'.repeat(64),
    models: [{id: 'ram-fixture', label: 'Explicit attack', unit: 'log2 bit operations', computation_model_id: computation,
      scope: 'Shared-secret recovery.', limitations: ['An upper bound, not a security lower bound.']}],
    estimates: [1, 2, 3].map(n => ({profile_id: 'p'+n, model_id: 'ram-fixture', bits: 30+n,
      computation_model_id: computation, success_lower_bound: '1/2', success_status: 'conditional-analytical-bound'}))},
});
const canonicalPin = (kind, id) => {const {note_section, ...source}=pin('unused');return {...source,[kind]:id};};
const argumentPacket = () => {
  const data=packet(); data.scheme_arguments={};
  for(const category of ['security','correctness']) {
    const group=data.scheme_assumptions[category];
    data.scheme_arguments[category]={id:category+'-argument',title:category+' reasoning',status:'conditional',
      conclusion:category==='security'?'The stated adversary recovers the shared key with probability at most epsilon.':'Honest parties agree except with probability delta.',
      scope:group.scope,source:canonicalPin('argument',category+'-argument'),reasoning_source:pin(category),
      origin:'canonical',scientific_adoption:false,dependencies:group.assumptions.map(p=>({
        statement:{id:p.id,label:p.label,title:p.title,definition:'Precisely stated premise: '+p.title,source:canonicalPin('statement',p.id)},
        stages:p.stages,configuration_ids:['native-configuration'],use:'The required bound applies to the stated experiment.'}))};
  }
  return data;
};
const session = (envelope, nextUser = user, allowed = true) => envelope({user: nextUser, csrf_token: 'fixture-csrf',
  access: {mode: 'beta', allowed, status: allowed ? 'active' : 'revoked'},
  capabilities: {curated_notes: false, ram_attack_comparison: true}});
const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
async function boot({data = packet(), respond} = {}) {
  const app = await createApp({schemes: [scheme()], user, hash: '#about',
    notebook: {mount() {return {show: async () => {}, hide() {}, setAccount() {}, invalidateReads() {}, warm: async () => 0};}},
    responder: (url, options, envelope) => {
      const custom = respond?.(url, options, envelope); if (custom !== undefined) return custom;
      if (url.pathname === '/api/session') return session(envelope);
      if (url.pathname === '/api/protocol/note-justifications') return data;
    }});
  vm.runInContext(graphHelper, app.sandbox, {filename: 'graph-points.js'});
  await app.navigate('#scheme/assumption-fixture/v1'); await flush();
  return app;
}
const dialog = app => app.$('#scheme-assumptions-dialog');
const content = app => app.$('#scheme-assumptions-content');
const group = (app, category) => content(app).querySelector('[data-assumption-category="'+category+'"]');
const rows = element => element.querySelectorAll('.scheme-assumption-link');
const noteQuery = anchor => new URLSearchParams(anchor.href.split('?')[1]);
async function open(app, category = 'security') {if (dialog(app).open) dialog(app).close(); app.$('#overview-'+category+'-button').click(); await flush(); assert.equal(dialog(app).open, true);}
function checkPin(anchor, source) {
  const query = noteQuery(anchor);
  assert.equal(anchor.href.startsWith('#notebook?'), true);
  assert.equal(query.get('note'), source.id);
  assert.equal(query.get('revision'), String(source.revision));
  assert.equal(query.get('sha256'), source.content_sha256);
  for(const key of ['note_section','statement','argument'])assert.equal(query.get(key),source[key]??null);
}
let passed = 0, completed = false;
async function test(name, fn) {await fn(); passed++; console.log('PASS '+name);}
process.once('beforeExit', () => {
  if (!completed && !process.exitCode) {console.error('Scheme-assumption checks stopped before completion'); process.exitCode = 1;}
});
(async () => {
  await test('separate scheme entries open only their own accessible argument without navigation', async () => {
    const app = await boot(), route = app.location.hash; await open(app);
    assert.equal(app.location.hash, route);
    assert.equal(app.$('#scheme-assumptions-title').textContent, 'Security argument');
    assert.equal(app.$('#scheme-assumptions-scheme').textContent, 'Assumption fixture');
    assert.equal(dialog(app).getAttribute('aria-labelledby'), 'scheme-assumptions-title');
    for (const category of ['security', 'correctness']) {
      await open(app, category);
      assert.equal(app.$('#overview-'+category+'-button').textContent.trim().toLowerCase(), category+' argument');
      assert.equal(content(app).querySelectorAll('[data-assumption-category]').length, 1);
      const section = group(app, category); assert.ok(section);
      const heading = app.document.querySelector('#'+section.getAttribute('aria-labelledby'));
      assert.ok(heading); assert.equal(heading.textContent.toLowerCase(), category+' argument');
      assert.equal(section.querySelector('.scheme-argument-premises').querySelector('h3').textContent, 'Assumptions');
      const read = section.querySelector('[aria-label="Read '+category+' argument"]');
      assert.ok(read); checkPin(read, pin(category));
    }
    assert.equal(rows(group(app, 'correctness')).length, 1);
    await open(app, 'security'); assert.equal(rows(group(app, 'security')).length, 2);
    assert.doesNotMatch(content(app).textContent, /Attack-only|A-88|Other stage|proof obligation/);
  });
  await test('canonical argument introduces its claim and scope before the exact argument link and premises', async () => {
    const data=argumentPacket(), app=await boot({data});await open(app);
    const section=group(app,'security'),intro=section.querySelector('.scheme-argument-introduction');
    assert.match(intro.querySelector('.scheme-argument-conclusion').textContent,/probability at most epsilon/);
    assert.match(intro.querySelector('.scheme-argument-scope').textContent,/Fresh passive shared-key recovery/);
    assert.equal(section.children[0],intro);assert.equal(section.children[1],section.querySelector('.scheme-argument-premises'));
    const read=intro.querySelector('a');assert.equal(read.textContent,'Read argument →');
    checkPin(read,canonicalPin('argument','security-argument'));
    const links=rows(section);assert.equal(links.length,2);checkPin(links[0],canonicalPin('statement','algebra'));
    assert.match(links[0].textContent,/Precisely stated premise/);
    assert.match(section.querySelector('.scheme-premise-use').textContent,/required bound applies/);
    assert.doesNotMatch(content(app).textContent,/scientifically approved|proved secure/i);
    read.click();await flush();assert.equal(dialog(app).open,false);
    assert.equal(new URLSearchParams(app.location.hash.split('?')[1]).get('argument'),'security-argument');
    assert.equal(vm.runInContext('state.noteOrigin',app.sandbox).label,'Assumption fixture');
  });
  await test('canonical missing premises are distinct from an authored claim of no unproved premises', async () => {
    for(const status of [null,'conditional','incomplete','unconditional']) {
      const data=argumentPacket();data.scheme_arguments.correctness.dependencies=[];data.scheme_arguments.correctness.status=status;
      const app=await boot({data});await open(app,'correctness');
      assert.equal(content(app).textContent.includes('source argument states no unproved premises'),status==='unconditional');
      assert.equal(content(app).textContent.includes('Correctness argument incomplete'),status==='incomplete');
      assert.doesNotMatch(content(app).textContent,/Unconditional|verified proof/);
      checkPin(content(app).querySelector('a'),canonicalPin('argument','correctness-argument'));
    }
  });
  await test('canonical statements stay separate from admission provenance and the argument roster', async () => {
    const data=argumentPacket(), argument=data.scheme_arguments.security;
    argument.resolved_conclusion_statement={id:'security-claim',definition:'The exact canonical security statement.',source:canonicalPin('statement','security-claim')};
    argument.presentation={target_id:'scheme-fixture-security',current_status:'selected',origin:'initial_admission',independent_review:false,roster_count:1,conclusion_source:canonicalPin('statement','security-claim')};
    const app=await boot({data});await open(app);
    assert.equal(content(app).querySelector('.scheme-argument-conclusion').textContent,'The exact canonical security statement.');
    assert.match(content(app).querySelector('.scheme-argument-admission').textContent,/Initial admission · not independently reviewed/);
    const actions=content(app).querySelector('.scheme-argument-actions').querySelectorAll('a');
    assert.equal(actions.length,3);checkPin(actions[1],canonicalPin('statement','security-claim'));
    assert.equal(new URLSearchParams(actions[2].href.split('?')[1]).get('statement_target'),'scheme-fixture-security');
    actions[2].click();await flush();assert.equal(dialog(app).open,false);
    assert.equal(new URLSearchParams(app.location.hash.split('?')[1]).get('statement_target'),'scheme-fixture-security');
  });
  await test('withdrawing the displayed argument preserves access to its claim and history without fallback', async () => {
    const data=argumentPacket(), argument=data.scheme_arguments.correctness;
    argument.source=null;argument.reasoning_source=null;argument.status='withdrawn';argument.dependencies=[];
    argument.presentation={target_id:'scheme-fixture-correctness',current_status:'withdrawn',origin:'reviewed',independent_review:true,roster_count:1,conclusion_source:canonicalPin('statement','correctness-claim')};
    const app=await boot({data});await open(app,'correctness');
    assert.equal(app.$('#overview-correctness-button').getAttribute('aria-disabled'),null);
    assert.match(content(app).textContent,/Displayed argument withdrawn/);
    assert.match(content(app).textContent,/No argument is currently displayed/);
    assert.equal(content(app).querySelector('[aria-label="Read correctness argument"]'),null);
    assert.equal(content(app).querySelector('.scheme-argument-premises'),null);
    assert.doesNotMatch(content(app).textContent,/unconditional|no unproved premises|proved false/i);
  });
  await test('withdrawn attack analysis stays visibly reference-only with an accessible argument history', async () => {
    const data=packet();data.graph_models['ram-fixture'].source=null;
    data.graph_models['ram-fixture'].assumptions=[];
    data.graph_models['ram-fixture'].presentation={target_id:'scheme-fixture-attack',current_status:'withdrawn',independent_review:true,numeric_curve_unchanged:true,conclusion_source:canonicalPin('statement','attack-claim')};
    const app=await boot({data});
    assert.equal(app.$('#overview-badge-security').textContent,'Reference only');
    assert.match(app.$('#overview-chart-security').textContent,/recorded values remain for reference/);
    const target=app.document.createElement('div');app.sandbox.rosterTestTarget=target;
    vm.runInContext("renderPointSources(selectedScheme(), {id:'p1',label:'Stage 1',position:1,model:{id:'ram-fixture'}}, rosterTestTarget)",app.sandbox);
    assert.match(target.textContent,/Reference only · no selected argument/);
    assert.equal(target.querySelectorAll('a').length,1);
    assert.equal(new URLSearchParams(target.querySelector('a').href.split('?')[1]).get('statement_target'),'scheme-fixture-attack');
    assert.doesNotMatch(target.textContent,/Attack specification/);
  });
  await test('malformed canonical pins never fall back to an older argument or become premise links', async () => {
    for(const extra of [{statement:'also-a-statement'},{argument:'constructor'},{argument:'bad]"'}]) {
      const data=argumentPacket();Object.assign(data.scheme_arguments.security.source,extra);
      const app=await boot({data}),button=app.$('#overview-security-button');
      assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.getAttribute('href'),null);
      button.click();await flush();assert.equal(dialog(app).open,false);
    }
    const data=argumentPacket();data.scheme_arguments.security.conclusion='<img src=x onerror=alert(1)> is literal text.';
    data.scheme_arguments.security.dependencies[0].statement.source.argument='ambiguous';
    const app=await boot({data});await open(app);
    assert.equal(rows(content(app)).length,1);assert.match(content(app).textContent,/Some listed premises are unavailable/);
    assert.equal(content(app).querySelector('img'),null);assert.match(content(app).textContent,/<img src=x onerror=alert\(1\)>/);
  });
  await test('group rows pair sorted assumption labels with meaningful, safely rendered titles', async () => {
    const data = packet(); data.scheme_assumptions.security.assumptions[0].title = '<img src=x> hardness';
    const app = await boot({data}); await open(app);
    const links = rows(group(app, 'security'));
    assert.match(links[0].textContent, /A-2.*Shared algebraic premise/);
    assert.match(links[1].textContent, /A-11.*<img src=x> hardness/);
    assert.equal(content(app).querySelector('img'), null);
    assert.equal(links.every(link => link.classList.contains('chart-assumption-link')), true);
    checkPin(links[0], pin('algebra')); checkPin(links[1], pin('hardness'));
  });
  await test('plain-language summaries lead while the exact argument remains one link away', async () => {
    const data = packet();
    data.scheme_assumptions.security.summary = 'Keeping the shared key secret from someone who sees one exchange.';
    data.scheme_assumptions.security.assumptions[0].summary = 'Finding the hidden value must remain difficult for the stated amount of work.';
    data.scheme_assumptions.security.assumptions[1].summary = '<img src=x onerror=alert(1)> must remain literal text.';
    const app = await boot({data}); await open(app);
    assert.match(content(app).textContent, /Keeping the shared key secret/);
    assert.match(content(app).querySelector('.scheme-argument-scope').textContent, /Fresh passive shared-key recovery/);
    const links = rows(group(app, 'security'));
    assert.match(links[0].textContent, /<img src=x onerror=alert\(1\)> must remain literal text/);
    assert.match(links[1].textContent, /Finding the hidden value must remain difficult/);
    assert.equal(content(app).querySelector('img'), null);
    checkPin(links[0], pin('algebra')); checkPin(links[1], pin('hardness'));
    checkPin(group(app, 'security').querySelector('[aria-label="Read security argument"]'), pin('security'));
    await open(app, 'correctness');
    assert.match(content(app).textContent, /Fresh honest agreement/);
  });
  await test('older assumption records remain readable and exact when a human title is absent', async () => {
    const data = packet(); delete data.scheme_assumptions.security.assumptions[0].title;
    data.scheme_assumptions.security.assumptions[1].title = 'A-2 · Shared algebraic premise';
    const app = await boot({data}); await open(app);
    const links = rows(group(app, 'security'));
    assert.equal((links[0].textContent.match(/A-2/g) || []).length, 1);
    assert.match(links[0].textContent, /Shared algebraic premise/);
    assert.match(links[1].textContent, /A-11/);
    assert.doesNotMatch(content(app).textContent, /undefined|\[object Object\]/);
    checkPin(links[1], pin('hardness'));
  });
  await test('formal statements lead with safe math while use and exact source stay separate', async () => {
    const data = packet();
    const p = data.scheme_assumptions.security.assumptions[0];
    p.kind = 'hardness'; p.summary = 'The older descriptive shortcut.';
    p.formal_statement = String.raw`Given \(g^a,g^b\), output \(g^{ab}\).` + '\n\n' +
      String.raw`\[\Pr[\mathcal A(g^a,g^b)=g^{ab}]\le\epsilon.\]` + '\n\n<img src=x> is text.';
    const app = await boot({data}), calls = [];
    app.sandbox.window.CrucibleMath = {render(element, text, options) {calls.push({text, options}); element.textContent = text;}};
    await open(app);
    const link = rows(group(app, 'security'))[1];
    assert.ok(link.querySelector('.assumption-formal-statement'));
    assert.equal(link.querySelectorAll('.assumption-formal-paragraph').length, 3);
    assert.equal(link.querySelector('.assumption-summary'), null);
    assert.deepEqual(calls.map(c => c.options.displayMode), [false, true, false]);
    assert.match(link.textContent, /Hardness assumption.*Used at stages 1–3/);
    assert.doesNotMatch(link.textContent, /older descriptive shortcut/);
    assert.equal(link.querySelector('img'), null);
    checkPin(link, pin('hardness'));
  });
  await test('reading a premise closes the dialog and preserves exact source and scheme return context', async () => {
    const app = await boot(); await open(app, 'correctness');
    const link = rows(group(app, 'correctness'))[0]; checkPin(link, pin('decoding'));
    const destination = link.href; link.click(); await flush();
    assert.equal(dialog(app).open, false); assert.equal(app.location.hash, destination);
    const origin = vm.runInContext('state.noteOrigin', app.sandbox);
    assert.equal(origin.noteId, pin('decoding').id);
    assert.equal(origin.label, 'Assumption fixture'); assert.match(origin.href, /^#scheme\/assumption-fixture\/v1/);
  });
  await test('modified note clicks retain native new-tab behavior and the open category list', async () => {
    const app = await boot(); await open(app);
    const link = rows(group(app, 'security'))[0], event = {type: 'click', ctrlKey: true, bubbles: true};
    link.dispatchEvent(event);
    assert.notEqual(event.defaultPrevented, true); assert.equal(dialog(app).open, true);
    checkPin(link, pin('algebra'));
  });
  await test('explicit empty categories differ from uncategorized or incomplete evidence', async () => {
    const data = packet();
    data.scheme_assumptions.security.assumptions = []; data.scheme_assumptions.security.incomplete = true;
    data.scheme_assumptions.correctness.assumptions = []; data.scheme_assumptions.correctness.argument_status = 'unconditional';
    const app = await boot({data}); await open(app);
    assert.match(group(app, 'security').textContent, /Security argument incomplete/i);
    assert.doesNotMatch(group(app, 'security').textContent, /Unconditional/i);
    await open(app, 'correctness'); assert.match(group(app, 'correctness').textContent, /source argument states no unproved premises within this scope/);
    for (const absent of [null, undefined]) {
      const older = packet(); older.scheme_assumptions = absent;
      const unknown = await boot({data: older}); await open(unknown);
      assert.match(group(unknown, 'security').textContent, /Premises have not been recorded/i);
      checkPin(group(unknown, 'security').querySelector('a'), pin('security'));
      await open(unknown, 'correctness'); assert.match(group(unknown, 'correctness').textContent, /Premises have not been recorded/i);
      assert.doesNotMatch(content(unknown).textContent, /Unconditional/i);
    }
  });
  await test('an empty list alone cannot claim an unconditional argument', async () => {
    for (const status of [null, undefined, 'conditional', 'unexpected']) {
      const data = packet(); data.scheme_assumptions.correctness.assumptions = [];
      data.scheme_assumptions.correctness.argument_status = status;
      const app = await boot({data}); await open(app, 'correctness');
      assert.match(content(app).textContent, /Premises have not been established/);
      assert.doesNotMatch(content(app).textContent, /Unconditional/);
      checkPin(group(app, 'correctness').querySelector('a'), pin('correctness'));
    }
  });
  await test('invalid exact pins never become navigable category or assumption links', async () => {
    const data = packet();
    data.scheme_assumptions.security.assumptions.push(premise('A-20', 'Unpinned premise', 'bad', {source: {...pin('bad'), revision: 0}}));
    data.scheme_assumptions.correctness.source = {...pin('correctness'), content_sha256: 'bad'};

    const app = await boot({data}); await open(app);
    assert.equal(rows(group(app, 'security')).length, 2);
    await open(app, 'correctness');
    assert.equal(rows(group(app, 'correctness')).length, 0);
    assert.doesNotMatch(content(app).textContent, /Unconditional/);
    assert.equal(content(app).querySelectorAll('a').some(a => a.href.includes('revision=0') || a.href.includes('sha256=bad')), false);
  });
  await test('malformed assumption members preserve valid links without claiming an empty known set', async () => {
    const data = packet(), malformed = [null, false, 3, 'invalid premise'];
    data.scheme_assumptions.security.assumptions.push(...malformed);
    data.scheme_assumptions.correctness.assumptions = [...malformed];
    data.graph_models['ram-fixture'].assumptions.push(...malformed);
    const app = await boot({data}); await open(app);
    assert.equal(rows(group(app, 'security')).length, 2);
    await open(app, 'correctness');
    assert.equal(rows(group(app, 'correctness')).length, 0);
    assert.match(group(app, 'correctness').textContent, /listed premises are unavailable/i);
    assert.doesNotMatch(group(app, 'correctness').textContent, /No additional premises listed/i);
    dialog(app).close();
    const chart = app.$('#overview-chart-security'), svg = chart.querySelector('svg');
    svg.getBoundingClientRect = () => ({left: 0, top: 0, width: 480, height: 240});
    svg.dispatchEvent({type: 'click', clientX: 48, clientY: 100, pointerType: 'mouse'});
    const links = chart.querySelector('.chart-point-readout').querySelectorAll('.chart-assumption-link');
    assert.equal(links.length, 1); checkPin(links[0], pin('attack-premise'));
  });
  await test('attack assumptions remain scoped to the selected attack and expose meaningful accessible names', async () => {
    const app = await boot(), chart = app.$('#overview-chart-security'), svg = chart.querySelector('svg');
    svg.getBoundingClientRect = () => ({left: 0, top: 0, width: 480, height: 240});
    svg.dispatchEvent({type: 'click', clientX: 48, clientY: 100, pointerType: 'mouse'});
    const readout = chart.querySelector('.chart-point-readout'), links = readout.querySelectorAll('.chart-assumption-link');
    assert.equal(links.length, 1); assert.match(links[0].textContent, /A-88/);
    assert.match(links[0].getAttribute('aria-label'), /Attack-only independence premise/);
    checkPin(links[0], pin('attack-premise')); assert.match(readout.textContent, /Assumptions/);
    assert.doesNotMatch(readout.textContent, /A-89|A-90|A-11|A-4/);
    const specification = readout.querySelectorAll('a').find(a => /Attack specification/.test(a.textContent));
    assert.ok(specification); checkPin(specification, pin('attack-specification'));
  });
  await test('closing and reopening uses already loaded evidence without adding writes or reads', async () => {
    const app = await boot(); await open(app);
    const reads = () => app.requests.filter(r => r.url.pathname === '/api/protocol/note-justifications').length;
    assert.equal(reads(), 1); dialog(app).close(); await open(app);
    assert.equal(reads(), 1);
    assert.equal(app.requests.every(r => !r.options.method || r.options.method === 'GET'), true);
  });
  await test('expired argument bindings refresh quietly and replace only the current authorized packet', async () => {
    let count=0;const held=deferred(),first=argumentPacket(),next=argumentPacket();
    next.scheme_arguments.security.source.revision=2;next.scheme_arguments.security.conclusion='A newly selected exact argument.';
    const app=await boot({respond:url=>url.pathname==='/api/protocol/note-justifications'?(++count===1?first:held.promise):undefined});
    await open(app);vm.runInContext('for (const entry of noteJustifications.values()) entry.loadedAt -= 16000; void loadNoteJustifications(selectedScheme());',app.sandbox);await flush();
    assert.match(content(app).textContent,/probability at most epsilon/);assert.equal(count,2);
    held.resolve(next);await flush();assert.match(content(app).textContent,/newly selected exact argument/);
    checkPin(content(app).querySelector('[aria-label="Read security argument"]'),next.scheme_arguments.security.source);
    assert.equal(dialog(app).open,true);
  });
  await test('an argument review invalidates scheme bindings without waiting for a page reload', async () => {
    let count=0;const first=argumentPacket(),next=argumentPacket();next.scheme_arguments.security.conclusion='The approved replacement.';
    const app=await boot({respond:(url,options,envelope)=>{
      if(url.pathname==='/api/protocol/note-justifications')return ++count===1?first:next;
      if(url.pathname==='/api/notebook/statement-workflow/updates/update-one/review')return envelope({update:{status:'approved'}});
    }});await open(app);
    await vm.runInContext("api('/api/notebook/statement-workflow/updates/update-one/review', {decision:'approve'})",app.sandbox);await flush();
    assert.equal(dialog(app).open,false);await open(app);assert.equal(count,2);assert.match(content(app).textContent,/approved replacement/);
  });
  await test('a failed binding refresh removes the old actionable argument', async () => {
    let count=0;const app=await boot({respond:(url,options,envelope)=>url.pathname==='/api/protocol/note-justifications'?(++count===1?argumentPacket():envelope({detail:'Unavailable'},404)):undefined});
    await open(app);vm.runInContext('for (const entry of noteJustifications.values()) entry.loadedAt -= 16000; void loadNoteJustifications(selectedScheme());',app.sandbox);await flush();
    assert.equal(dialog(app).open,false);assert.equal(content(app).textContent,'');assert.equal(app.$('#overview-security-button').getAttribute('aria-disabled'),'true');
  });
  await test('route changes, account switches and revoked access clear private dialog contents', async () => {
    for (const action of ['route', 'account', 'revoked']) {
      const app = await boot(); await open(app); assert.match(content(app).textContent, /Structured search/);
      if (action === 'route') await app.navigate('#about');
      else app.sandbox.setSession(session(app.envelope, action === 'account' ? {id: 'other', handle: 'other'} : user, action !== 'revoked'));
      await flush(); assert.equal(dialog(app).open, false); assert.equal(content(app).textContent, '');
      assert.doesNotMatch(app.$('#scheme-assumptions-title').textContent, /Assumption fixture/);
    }
  });
  await test('late evidence from a previous account cannot populate a new account or open its dialog', async () => {
    const held = deferred(), app = await boot({respond: url => url.pathname === '/api/protocol/note-justifications' ? held.promise : undefined});
    app.sandbox.setSession(session(app.envelope, {id: 'other', handle: 'other'}));
    held.resolve(packet()); await flush();
    assert.equal(dialog(app).open, false); assert.equal(content(app).textContent, '');
    assert.equal(app.sandbox.graphNoteSource(scheme()), null);
  });
  await test('a late evidence response cannot reopen the scheme dialog after navigation away', async () => {
    const held = deferred(), app = await boot({respond: url => url.pathname === '/api/protocol/note-justifications' ? held.promise : undefined});
    await app.navigate('#about'); held.resolve(packet()); await flush();
    assert.equal(app.location.hash, '#about'); assert.equal(dialog(app).open, false);
    assert.equal(content(app).textContent, '');
    await app.navigate('#scheme/assumption-fixture/v1'); await open(app);
    assert.match(content(app).textContent, /Structured search remains hard/);
    assert.equal(app.requests.filter(r => r.url.pathname === '/api/protocol/note-justifications').length, 1);
  });
  completed = true; console.log(`${passed} scheme-assumption frontend checks passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});

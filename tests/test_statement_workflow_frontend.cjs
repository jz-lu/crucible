const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const web = path.resolve(__dirname, '../web/notebook');
const store = require('../web/notebook/store.js');
const hash = 'a'.repeat(64), user = {id:1,handle:'alice'};
const source = {note_id:'note-one',revision:1,content_sha256:hash};
const statement = {id:'failure-bound',label:'A-12',title:'A bounded failure event',definition:'For the stated sampler, $\\delta \\leq 1/128$.'};
const argument = {id:'attack',title:'An attack using the bound',conclusion:'Success is at least seven eighths.',scope:'Configurations alpha and beta.',status:'conditional',dependencies:[{statement:{statement_id:'failure-bound'},configuration_ids:['alpha','beta'],required:'Failure at most 1/8.',mapping:'Native message failure.',justification:'Success is the complementary event.'}]};
const content = () => ({title:'Finite sampler audit',claim:'A small illustrative experiment.',conditions:'Not a claim about a deployed scheme.',body:'# Full record\n\nTechnical derivation stays here.',tags:['illustrative-trial','ai-written'],statements:[statement],arguments:[argument],extensions:{illustrative:true,statement_trial:'sampler-audit-v1'}});
const note = (value=content()) => ({id:'note-one',owner:user,can_edit:true,revision:1,content_sha256:hash,revisions:[{revision:1,content_sha256:hash,content:value,publication_status:'approved'}]});
const target = {id:'trial-attack',title:'Trial attack success',kind:'attack',scope:'The exact alpha and beta samplers.',configuration_ids:['alpha','beta']};
const resolved = {...argument,source,dependencies:argument.dependencies.map(u=>({...u,resolved_statement:{...statement,source}}))};
const conclusion = {id:'success-claim',label:'S-2',title:'Shared-secret recovery claim',definition:'The stated attack succeeds with probability at least seven eighths.'};
const canonicalContent = () => {const value=JSON.parse(JSON.stringify(content()));value.statements.push(conclusion);delete value.arguments[0].conclusion;value.arguments[0].conclusion_statement={statement_id:conclusion.id};return value;};
const canonicalResolved = () => ({...resolved,conclusion_statement:{statement_id:conclusion.id},resolved_conclusion_statement:{...conclusion,source}});
const rosterData = () => {
  const alternate={...resolved,id:'enumeration',title:'Independent enumeration',status:'unconditional',dependencies:[]};
  const chosen={id:'initial',argument:{...source,argument_id:'attack'},resolved_argument:canonicalResolved(),origin:'initial_admission',independent_review:false,status:'admitted',is_current:true};
  const other={id:'alternative',argument:{...source,argument_id:'enumeration'},resolved_argument:alternate,origin:'reviewed',independent_review:true,status:'admitted',is_current:false,reviewer:{handle:'reviewer'}};
  return {target:{...target,conclusion_statement:{...source,statement_id:conclusion.id}},current:{...chosen,id:'latest-roster-head',selection_id:'initial'},roster:[chosen,other],history:[]};
};
const update = () => ({id:'update-one',target,argument:{...source,argument_id:'attack'},resolved_argument:resolved,evidence:[source],resolved_evidence:[{source,title:'Finite sampler audit'}],packet_sha256:'b'.repeat(64),status:'pending',can_review:true,can_apply:true,reason:'Use the exact bound.',stale:false});
const byText = (root,text) => root.querySelectorAll('button').find(n=>n.textContent===text);
const query = anchor => new URLSearchParams({note:'note-one',revision:'1',sha256:hash,...anchor});
async function boot({record=note(),params=query({}),respond,reviewOnly=false,enabled=true,readerTools=false,realMarkdown=false}={}) {
  const app=await createApp({schemes:[],hash:'#about'});let sequence=0,controller,navigation=Promise.resolve();
  app.sandbox.crypto.randomUUID=()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;
  app.window.confirm=()=>true;app.window.localStorage={getItem:()=>null};
  app.window.CrucibleMath={render:(target,text)=>target.textContent=text,markdown:(target,text)=>target.textContent=text};
  for(const file of ['store.js','seed.js','statement-workflow.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(web,file),'utf8'),app.sandbox,{filename:file});
  if(realMarkdown)vm.runInContext(fs.readFileSync(path.join(web,'../math.js'),'utf8'),app.sandbox,{filename:'math.js'});
  const json=value=>vm.runInContext('('+JSON.stringify(value)+')',app.sandbox),requests=[],root=app.$('#notebook-workspace'),q=name=>root.querySelector(`[data-nb="${name}"]`);
  record=json(record);
  const api=async(url,body)=>{requests.push({url,body});const custom=await respond?.(url,body);if(custom!==undefined)return json(custom);
    if(url.startsWith('/api/notebook/notes/note-one'))return{note:record};
    if(url.startsWith('/api/notebook/notes?'))return{notes:[],next_after:null};
    if(url.startsWith('/api/notebook/statement-workflow/context'))return{source,statement:{...statement,source},argument:resolved,targets:[],related_uses:[],related_research:[]};
    if(url.endsWith('/statement-workflow/targets'))return{targets:[{target,current:{id:'adoption-one'}}]};
    if(url.endsWith('/statement-workflow/updates/update-one'))return{update:update()};
    if(url==='/api/notebook/curated-note-numbers')return{numbers:{}};
    throw Error('Unexpected '+url);};
  const navigate=route=>{app.history.pushState(null,'',route);navigation=controller.show(new URLSearchParams(route.split('?')[1]||''));return navigation;};
  controller=app.window.CrucibleNotebook.mount(root,{api,navigate,account:()=>user,reviewOnly,readerTools,statementWorkflowAvailable:()=>enabled});
  await controller.show(params);await flush();
  return{...app,root,q,requests,controller,show:p=>controller.show(p),whenNavigated:()=>navigation};
}
let passed=0;async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('source-backed headings share Python extraction fixtures and preserve authored records',async()=>{
    for(const fixture of JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/heading-passages.json'),'utf8'))){
      const content={body:fixture.body},location={heading:fixture.heading};
      if(fixture.invalid)assert.throws(()=>store.headingPassage(content,location),undefined,fixture.name);
      else{const found=store.headingPassage(content,location);assert.equal(found.start_line,fixture.start,fixture.name);assert.equal(found.end_line,fixture.end,fixture.name);const passage=store.resolvePassage(content,'p1-b-'+fixture.start+'-'+fixture.end);assert.equal(passage.definition,found.text,fixture.name);}
    }
    const value={body:'## A\nBody.',sections:[{id:'explanation',body:'## A\nSeparate.'}]};
    const item={id:'premise',location:{heading:'A'}};
    assert.equal(store.materializeStatement(value,item).definition,'## A\nBody.');
    assert.equal(store.materializeStatement(value,{...item,location:{heading:'A',section_id:'explanation'}}).definition,'## A\nSeparate.');
    assert.deepEqual(item,{id:'premise',location:{heading:'A'}});
  });
  await test('a heading reference renders once with bounded highlight, nested anchors and exact citation',async()=>{
    const value=content();value.arguments=[];value.body='Intro\n\n## Premise\nOnly this passage.\n### Detail\nNested text.\n## Next\nUnrelated.';
    value.statements=[{id:'premise',location:{heading:'Premise'}},{id:'detail',location:{heading:'Detail'}}];
    const app=await boot({record:note(value),params:query({statement:'premise'}),enabled:false,realMarkdown:true}),full=app.q('full');
    assert.equal(full.querySelectorAll('[data-note-statement="premise"]').length,1);
    assert.equal(full.querySelectorAll('[data-note-statement="detail"]').length,1);
    assert.equal(full.querySelectorAll('.nb-statement-block').length,0);
    assert.equal(full.querySelectorAll('.nb-linked-passage').length,4);
    assert.equal(full.querySelector('[data-markdown-start="7"]').classList.contains('nb-linked-passage'),false);
    assert.equal(app.document.activeElement.dataset.noteStatement,'premise');
    const cite=full.querySelector('[data-markdown-start="3"]').querySelector('.nb-passage-cite');cite.click();
    assert.match(app.q('dialog-body').textContent,/pinned to this saved revision/);
    assert.match(app.q('dialog-body').querySelector('input').value,/passage=p1-b-3-6/);
    byText(app.q('dialog'),'×').click();byText(full,'Edit').click();
    assert.equal(app.q('dialog-body').querySelector('.nb-statement-editor'),null);
    assert.equal(app.q('dialog-body').querySelector('[name="statement-definition-0"]'),null);
    app.q('dialog-body').querySelector('[name="body"]').value=value.body.replace('## Premise','## Renamed');
    app.q('dialog-body').querySelector('form').dispatchEvent({type:'submit',preventDefault(){}});await flush();
    assert.match(app.q('dialog-body').textContent,/Update the reference/);
    assert.equal(app.requests.some(item=>item.body && item.url!=='/api/notebook/locs'),false);
  });
  await test('body heading references select Research details even when a separate explanation exists',async()=>{
    const value=content();value.arguments=[];value.statements=[{id:'premise',location:{heading:'Premise'}}];value.body='## Premise\nPrecise text.';
    value.sections=[{id:'explanation',title:'Why it matters',body:'An accessible introduction.'}];
    const app=await boot({record:note(value),params:query({statement:'premise'}),enabled:false,realMarkdown:true});
    assert.equal(app.q('full').querySelector('.nb-full-text').hidden,false);
    assert.equal(app.q('full').querySelector('.nb-readable-view').hidden,true);
    assert.equal(app.document.activeElement.dataset.noteStatement,'premise');
  });
  await test('a heading conclusion appears only in its reasoning, and selected premises retain context links',async()=>{
    const value=content();value.sections=[{id:'reasoning',title:'Reasoning',body:'## Claim\nThis proposition appears once.\n## Proof\nThe derivation.'}];
    value.statements=[{id:'claim',location:{heading:'Claim',section_id:'reasoning'}}];
    value.arguments=[{id:'proof',title:'A derivation',conclusion_statement:{statement_id:'claim'},scope:'An illustrative experiment.',status:'unconditional',section_id:'reasoning',dependencies:[]}];
    const app=await boot({record:note(value),params:query({argument:'proof'}),enabled:false,realMarkdown:true});
    assert.equal(app.q('full').textContent.split('This proposition appears once.').length-1,1);
    assert.equal(app.q('full').querySelectorAll('[data-note-statement="claim"]').length,1);
    const linked=await boot({record:note(value),params:query({statement:'claim'}),realMarkdown:true,respond:(url)=>url.includes('/statement-workflow/context?')?{source,targets:[],related_uses:[{source,argument_id:'proof',title:'A related derivation'}]}:undefined});
    assert.equal(linked.requests.filter(row=>row.url.includes('/statement-workflow/context?')).length,1);
    assert.ok(linked.q('full').querySelectorAll('a').some(a=>a.textContent==='A related derivation'));
    assert.equal(linked.requests.some(row=>row.body),false);
  });
  await test('reading and copying use deterministic sections without allocating or marking a citation',async()=>{
    const value=content();delete value.statements;delete value.arguments;
    value.body='## Same\nFirst.\n### Detail\nIncluded.\n## Same\nSecond.';
    const record=note(value);record.section_refs_by_revision={'1':{'passage:p1-b-1-4':{number:'1',label:'56.1',kind:'Section',loc:'56.1@1',used:false}}};
    const app=await boot({record,params:query({passage:'p1-b-1-4'}),enabled:false,realMarkdown:true}),full=app.q('full');
    assert.equal(full.querySelectorAll('.nb-passage-cite').length,3);
    assert.equal(full.querySelectorAll('.nb-linked-passage').length,4);
    assert.equal(full.querySelector('[data-markdown-start="5"]').classList.contains('nb-linked-passage'),false);
    assert.equal(app.document.activeElement.dataset.notePassage,'p1-b-1-4');
    assert.equal(app.requests.some(row=>row.body),false);
    full.querySelector('[data-note-passage="p1-b-1-4"]').querySelector('.nb-passage-cite').click();
    await flush();
    assert.equal(app.q('dialog-body').querySelector('input').value,'[Section 56.1](loc:56.1@1)');
    assert.ok(byText(app.q('dialog-body'),'Copy Markdown'));
    assert.equal(app.requests.filter(row=>row.body).length,0);
    assert.equal(full.querySelector('[data-note-passage="p1-b-1-4"]').querySelector('.nb-passage-cite').textContent,'↗');
    record.section_refs_by_revision['1']['passage:p1-b-1-4'].used=true;
    const used=await boot({record,enabled:false,realMarkdown:true});
    assert.equal(used.q('full').querySelector('[data-note-passage="p1-b-1-4"]').querySelector('.nb-passage-cite').textContent,'[1]');
    const bad=await boot({record:note(value),params:query({passage:'p1-b-1-3'}),enabled:false,realMarkdown:true});
    assert.match(bad.q('full').textContent,/absent from the exact saved note/);
  });
  await test('short loc navigation resolves an exact revision and never falls back to latest',async()=>{
    const value=content();delete value.statements;delete value.arguments;value.body='## Premise\nText.';
    const record=note(value);record.passage_locs={'p1-b-1-2':'P.33'};
    record.section_refs_by_revision={'1':{'passage:p1-b-1-2':{number:'1',label:'56.1',kind:'Section',loc:'56.1@1',used:true}}};
    const app=await boot({record,params:query({loc:'P.33',passage:'p1-b-1-2'}),enabled:false,realMarkdown:true,
      respond:url=>url.startsWith('/api/notebook/locs/P.33?')?{loc:'P.33',exact_url:'/#notebook?'+query({passage:'p1-b-1-2'})}:undefined});
    assert.equal(app.q('full').querySelector('.nb-passage-cite').textContent,'[1]');
    assert.ok(app.q('full').querySelector('.nb-linked-passage'));
    assert.equal(app.requests.some(row=>row.body),false);
    const bad=await boot({params:new URLSearchParams({loc:'P.33',note:'note-one'}),enabled:false});
    assert.match(bad.q('full').textContent,/including its exact saved source/);
    assert.equal(bad.requests.length,0);
    const collision=await boot({record,params:query({loc:'P.33',passage:'p1-b-1-2'}),enabled:false,
      respond:url=>url.startsWith('/api/notebook/locs/P.33?')?{loc:'P.33',exact_url:'/#notebook?'+query({note:'another-source',passage:'p1-b-1-2'})}:undefined});
    assert.match(collision.q('full').textContent,/does not name the cited source/);
    assert.equal(collision.requests.filter(row=>row.url.startsWith('/api/notebook/notes/')).length,0);
  });
  await test('a readable companion root has a section citation, while body passages select Research details',async()=>{
    const value=content();delete value.statements;delete value.arguments;
    value.body='## Precise experiment\nTechnical source.';
    value.sections=[{id:'explanation',title:'What this means',body:'## Scope\nReadable text.'}];
    const app=await boot({record:note(value),params:query({passage:'p1-s-explanation-0-2'}),enabled:false,realMarkdown:true});
    assert.equal(app.document.activeElement.dataset.notePassage,'p1-s-explanation-0-2');
    assert.equal(app.q('full').querySelector('.nb-readable-view').hidden,false);
    assert.equal(app.q('full').querySelectorAll('.nb-passage-cite').length,3);
    const technical=await boot({record:note(value),params:query({passage:'p1-b-1-2'}),enabled:false,realMarkdown:true});
    assert.equal(technical.q('full').querySelector('.nb-full-text').hidden,false);
    assert.equal(technical.q('full').querySelector('.nb-readable-view').hidden,true);
    assert.equal(technical.document.activeElement.dataset.notePassage,'p1-b-1-2');
  });
  await test('shared placement grammar preserves literal code/math examples and only expands standalone IDs',async()=>{
    const fixtures=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/statement-placements.json'),'utf8'));
    for(const fixture of fixtures) assert.deepEqual(store.readableStatementBlocks(fixture.body).filter(part=>part.kind==='statement').map(part=>part.id),fixture.ids,fixture.name);
    assert.throws(()=>store.readableStatementBlocks('a'.repeat(40001)),/bounded/);
  });
  await test('ordinary notes embed canonical statements without requiring readable sections',async()=>{
    const value=content();value.body='Opening explanation.\n\n[[statement:failure-bound]]\n\nA consequence.';
    const app=await boot({record:note(value),params:query({statement:'failure-bound'}),enabled:false}),full=app.q('full');
    assert.equal(full.querySelectorAll('[data-note-statement="failure-bound"]').length,1);
    assert.equal(app.document.activeElement.dataset.noteStatement,'failure-bound');
    assert.ok(app.document.activeElement.closest('.nb-readable-view'));assert.ok(full.querySelector('.nb-reader-views'));
    assert.doesNotMatch(full.querySelector('.nb-readable-view').textContent,/\[\[statement:/);
    byText(full,'Edit').click();const editor=app.q('dialog-body');
    assert.equal(editor.querySelector('.nb-sections-editor'),null);
    assert.equal(byText(editor,'Insert in note'),undefined);
    assert.match(editor.querySelector('[name="body"]').value,/\[\[statement:failure-bound\]\]/);
    assert.equal(app.requests.some(row=>row.body),false);
  });
  await test('removing a statement removes its prose placement but preserves literal examples and blocks dangling argument uses',async()=>{
    const value=content();value.arguments=[];
    value.body='Before.\n\n[[statement:failure-bound]]\n\n```\n[[statement:failure-bound]]\n```\nAfter.';
    const app=await boot({record:note(value),enabled:false,readerTools:true});byText(app.q('full'),'Edit').click();
    byText(app.q('dialog-body'),'Remove statement').click();
    const body=app.q('dialog-body').querySelector('[name="body"]').value;
    assert.equal(store.readableStatementBlocks(body).filter(part=>part.kind==='statement').length,0);
    assert.match(body,/```\n\[\[statement:failure-bound\]\]\n```/);assert.match(body,/Before/);assert.match(body,/After/);
    assert.equal(app.q('dialog-body').querySelectorAll('.nb-statement-editor-row').length,0);
    const dependent=await boot({enabled:false,readerTools:true});byText(dependent.q('full'),'Edit').click();
    byText(dependent.q('dialog-body'),'Remove statement').click();
    assert.equal(dependent.q('dialog-body').querySelectorAll('.nb-statement-editor-row').length,1);
    assert.match(dependent.q('dialog-body').textContent,/An argument uses this statement/);
  });
  await test('body placements reject missing and duplicate identities while literal code stays literal',async()=>{
    assert.throws(()=>store.statementPlacements({...content(),body:'[[statement:missing]]'}),/canonical statement/);
    assert.throws(()=>store.statementPlacements({...content(),body:'[[statement:failure-bound]]\n[[statement:failure-bound]]'}),/only once/);
    const value=content();value.body='```\n[[statement:failure-bound]]\n```';
    assert.equal(store.statementPlacements(value).size,0);
    delete value.statements;delete value.arguments;value.body='[[statement:literal-example]]';
    const app=await boot({record:note(value),enabled:false});assert.match(app.q('full').textContent,/\[\[statement:literal-example\]\]/);
  });
  await test('precise premise navigation keeps a local return to its argument and clears it on independent entry',async()=>{
    const app=await boot({params:query({argument:'attack'})});
    await app.show(query({statement:'failure-bound'}));
    const nav=app.q('full').querySelector('.nb-destination-nav');assert.ok(nav);
    byText(nav,'← Argument').click();await app.whenNavigated();assert.equal(app.document.activeElement.dataset.noteArgument,'attack');
    await app.show(new URLSearchParams());await app.show(query({statement:'failure-bound'}));
    assert.equal(byText(app.document.activeElement,'← Argument'),undefined);
  });
  await test('passage links preserve the local argument return and keep navigation outside the cited range',async()=>{
    const value=JSON.parse(JSON.stringify(content()));delete value.statements;value.body='## Premise\nExact source.';
    value.arguments[0].dependencies[0].statement={passage:'p1-b-1-2'};
    const app=await boot({record:note(value),params:query({argument:'attack'}),enabled:false,realMarkdown:true});
    await app.show(query({passage:'p1-b-1-2'}));
    const nav=app.q('full').querySelector('.nb-destination-nav');assert.ok(byText(nav,'← Argument'));
    assert.equal(nav.closest('.nb-linked-passage'),null);
    byText(nav,'← Argument').click();await app.whenNavigated();assert.equal(app.document.activeElement.dataset.noteArgument,'attack');
  });
  await test('ordinary articles have no separate statement or argument inventory',async()=>{
    const value=content();value.arguments.push({...argument,id:'direct',title:'Direct enumeration',status:'unconditional',dependencies:[]});
    const app=await boot({record:note(value)}),full=app.q('full');
    assert.equal(full.querySelectorAll('.nb-statement-block').length,0);assert.equal(full.querySelectorAll('.nb-argument-block').length,0);
    assert.equal(full.querySelector('.nb-structured-research'),null);
    assert.doesNotMatch(full.textContent,/Statements & arguments|Direct enumeration/);
    assert.equal(full.querySelector('.nb-statements-list'),null);
    assert.equal(full.querySelector('.nb-reader-contents'),null);assert.equal(full.querySelector('.nb-statement-index'),null);
    const fullText=full.querySelector('.nb-full-text');assert.equal(fullText.open,true);assert.equal(fullText.hidden,true);
    assert.match(full.querySelector('.nb-readable-view').textContent,/Technical derivation/);
    byText(full,'Research details').click();assert.match(fullText.textContent,/No separate research details/);
    assert.doesNotMatch(full.textContent,/No readable explanation has been added\./);assert.ok(full.querySelector('.nb-reader-views'));
    assert.match(full.textContent,/Illustrative trial/);assert.equal(app.requests.some(x=>x.url.includes('/context')),false);
  });
  await test('article contents follows ordinary sections without a separate scientific index',async()=>{
    const value=content();value.sections=[{id:'reasoning',title:'An explanation',body:'Before.\n\n[[statement:failure-bound]]\n\nAfter.'},{id:'limits',title:'Limits',body:'The limits matter.'}];
    const app=await boot({record:note(value),enabled:false}),full=app.q('full'),contents=full.querySelector('.nb-reader-contents');
    assert.equal(contents.tagName,'NAV');assert.equal(contents.getAttribute('aria-label'),'Contents');
    assert.deepEqual(Array.from(contents.querySelectorAll('a'),link=>link.textContent),['An explanation','Limits']);
    assert.equal(full.querySelector('.nb-statement-index'),null);assert.equal(full.querySelector('.nb-argument-index'),null);
    assert.equal(full.querySelectorAll('[data-note-statement="failure-bound"]').length,1);
    const reads=app.requests.length;contents.querySelectorAll('a')[1].click();await app.whenNavigated();assert.equal(app.document.activeElement.closest('[data-note-section]').dataset.noteSection,'limits');assert.equal(app.requests.length,reads);
    await app.show(query({argument:'attack'}));assert.equal(app.document.activeElement.dataset.noteArgument,'attack');
    assert.equal(app.q('full').querySelector('.nb-readable-view').hidden,false);
  });
  await test('unplaced exact statements remain reachable without an upfront guide',async()=>{
    const value=content();delete value.arguments;
    const app=await boot({record:note(value),enabled:false}),full=app.q('full');
    assert.equal(full.querySelector('.nb-reader-contents'),null);assert.equal(full.querySelector('.nb-reader-structure'),null);
    await app.show(query({statement:'failure-bound'}));
    assert.equal(app.document.activeElement.dataset.noteStatement,'failure-bound');
    assert.equal(app.q('full').querySelectorAll('[data-note-statement="failure-bound"]').length,1);
    assert.equal(app.q('full').querySelector('.nb-statement-index'),null);
  });
  await test('the review reader retains its existing statement disclosure and section navigation',async()=>{
    const value=content();value.sections=[{id:'reasoning',title:'A readable explanation',body:'Exact authored reasoning.'}];
    const app=await boot({record:note(value),reviewOnly:true,params:query({section:'flagged'})}),full=app.q('full');
    assert.equal(full.querySelector('.nb-reader-outline'),null);assert.equal(full.querySelector('.nb-reader-contents'),null);
    assert.equal(full.querySelector('.nb-statements-list').open,false);assert.equal(full.querySelectorAll('.nb-statement-index').length,1);
    assert.equal(full.querySelector('.nb-statement-index').closest('.nb-structured-research')!==null,true);
    assert.ok(full.querySelector('.nb-note-contents'));assert.equal(full.querySelector('.nb-reader-views'),null);
  });
  await test('exact statement anchors focus the canonical block and carry revision/hash to server',async()=>{
    const app=await boot({params:query({statement:'failure-bound'})});assert.equal(app.document.activeElement.dataset.noteStatement,'failure-bound');
    const request=app.requests.find(x=>x.url.startsWith('/api/notebook/notes/note-one'));assert.match(request.url,/sha256=a{64}&statement=failure-bound/);
    app.q('full').querySelector('.nb-statement-block').querySelector('.nb-passage-cite').click();assert.match(app.root.querySelector('[name="statement-link"]').value,/statement=failure-bound\)$/);
  });
  await test('authored statements interleave with prose and exact links focus their surrounding explanation',async()=>{
    const value=canonicalContent();value.sections=[{id:'experiment',title:'The experiment',body:'Before the premise.\n\n[[statement:failure-bound]]\n\nBetween premise and conclusion.\n\n[[statement:success-claim]]\n\nAfter the conclusion.'}];
    const app=await boot({record:note(value),params:query({statement:'failure-bound'}),enabled:false}),full=app.q('full'),section=full.querySelector('.nb-readable-section');
    assert.equal(section.dataset.noteSection,'experiment');assert.equal(app.document.activeElement.closest('.nb-readable-section'),section);
    const children=[...section.querySelector('.nb-readable-flow').children].filter(child=>!child.classList.contains('nb-destination-nav'));
    assert.deepEqual(Array.from(children,x=>x.dataset.noteStatement || x.textContent),['Before the premise.\n','failure-bound','\nBetween premise and conclusion.\n','success-claim','\nAfter the conclusion.']);
    assert.equal(full.querySelectorAll('[data-note-statement="failure-bound"]').length,1);
    assert.equal(full.querySelectorAll('[data-note-statement="success-claim"]').length,1);
    assert.equal(section.textContent.split(statement.definition).length-1,1);
    assert.equal(full.querySelector('.nb-reader-contents'),null);
    assert.equal(full.querySelector('.nb-statements-list'),null);
    assert.equal(full.querySelector('.nb-statement-index'),null);
    assert.doesNotMatch(section.textContent,/\[\[statement:/);
  });
  await test('historical links use only explicit unique context and leave immutable text intact',async()=>{
    for(const relation of ['premise','conclusion','dependency','ambiguous','unlinked']) {
      const value=canonicalContent();value.sections=[{id:'reasoning',title:'Linked reasoning',body:'Original explanation remains exact.'},{id:'other',title:'Another section',body:'Unrelated words.'}];
      if(relation==='premise')value.extensions.assumptions=[{id:'failure-bound',section_id:'reasoning'}];
      if(['conclusion','dependency','ambiguous'].includes(relation))value.arguments[0].section_id='reasoning';
      if(relation==='ambiguous')value.arguments.push({...value.arguments[0],id:'second',section_id:'other'});
      const id=relation==='conclusion'?'success-claim':'failure-bound',before=JSON.stringify(value);
      const app=await boot({record:note(value),params:query({statement:id}),enabled:false}),selected=app.document.activeElement;
      assert.equal(selected.dataset.noteStatement,id);
      assert.equal(selected.closest('.nb-readable-section')?.dataset.noteSection || null,['ambiguous','unlinked'].includes(relation)?null:'reasoning',relation);
      assert.equal(app.q('full').textContent.split('Original explanation remains exact.').length-1,1);
      assert.equal(JSON.stringify(value),before);
    }
  });
  await test('unknown and duplicate placements fail closed without selecting a replacement',async()=>{
    for(const bodies of [['[[statement:missing]]'],['[[statement:failure-bound]]','[[statement:failure-bound]]']]){
      const value=content();value.sections=bodies.map((body,i)=>({id:'section-'+i,title:'Explanation',body}));
      const app=await boot({record:note(value),params:query({statement:'failure-bound'})});
      assert.match(app.q('full').querySelector('.nb-error').textContent,/placement|only once/);
      assert.equal(app.q('full').querySelector('.nb-statement-block'),null);assert.equal(app.requests.some(x=>x.url.includes('/context')),false);
    }
  });
  await test('a canonical conclusion uses its exact statement without promoting it to a theorem',async()=>{
    const value=canonicalContent(), app=await boot({record:note(value),params:query({argument:'attack'}),enabled:false,readerTools:true});
    const block=app.q('full').querySelector('.nb-argument-conclusion'),reference=block.querySelector('a');
    assert.match(block.textContent,/Conclusion.*S-2.*Shared-secret recovery claim.*seven eighths/s);
    assert.match(reference.href,/revision=1&sha256=a{64}&statement=success-claim$/);
    assert.doesNotMatch(block.textContent,/Theorem|Proved|Unconditional|undefined/);
    assert.equal(value.arguments[0].conclusion,undefined);assert.equal(app.q('full').querySelector('.nb-readable-view').hidden,false);
    byText(app.q('full'),'Edit').click();const stored=JSON.parse(app.root.querySelector('[name="statement-arguments"]').value);
    assert.deepEqual(stored[0].conclusion_statement,{statement_id:conclusion.id});assert.equal(stored[0].conclusion,undefined);
  });
  await test('external conclusion text is revealed only by matching authorized source context',async()=>{
    const foreign={note_id:'foreign-note',revision:7,content_sha256:'c'.repeat(64)},value=canonicalContent();
    value.arguments[0].conclusion_statement={...foreign,statement_id:conclusion.id};
    for(const authorized of [false,true]){
      const resolvedArgument={...canonicalResolved(),conclusion:'DO NOT SUBSTITUTE THIS DERIVED SHORTCUT',conclusion_statement:value.arguments[0].conclusion_statement,
        resolved_conclusion_statement:{...conclusion,source:authorized?foreign:source}};
      const app=await boot({record:note(value),params:query({argument:'attack'}),respond:url=>url.includes('/context?')?{argument:resolvedArgument}:undefined});
      const block=app.q('full').querySelector('.nb-argument-conclusion');
      assert.equal(block.textContent.includes(conclusion.definition),authorized);assert.doesNotMatch(block.textContent,/DO NOT SUBSTITUTE/);
      assert.match(block.querySelector('a').href,/note=foreign-note&revision=7&sha256=c{64}&statement=success-claim/);
    }
  });
  await test('conflicting or partially pinned conclusion authorities fail closed',async()=>{
    for(const change of [{conclusion:'Second conflicting definition'},{conclusion_statement:{statement_id:conclusion.id,note_id:'incomplete'}}]){
      const value=canonicalContent();Object.assign(value.arguments[0],change);
      const app=await boot({record:note(value)});assert.match(app.q('full').textContent,/one exact conclusion/);assert.equal(app.q('full').querySelector('.nb-argument-block'),null);
    }
  });
  await test('mixed, malformed and unpinned anchors cannot read or select a replacement',async()=>{
    for(const params of [query({statement:'failure-bound',argument:'attack'}),query({statement:'constructor'}),new URLSearchParams('note=note-one&statement=failure-bound')]){
      const app=await boot({params});assert.ok(app.q('full').querySelector('.nb-error'));assert.equal(app.requests.some(x=>x.url.startsWith('/api/notebook/notes/note-one')),false);
    }
    const absent=await boot({params:query({statement:'absent'})});assert.match(absent.q('full').textContent,/not present in this exact revision/);assert.equal(absent.q('full').querySelector('.nb-structured-research'),null);
  });
  await test('argument focus resolves dependency labels without replacing the source text',async()=>{
    const app=await boot({params:query({argument:'attack'})});assert.equal(app.document.activeElement.dataset.noteArgument,'attack');
    assert.match(app.q('full').querySelector('.nb-dependency-list').textContent,/A-12.*Failure at most 1\/8/s);
  });
  await test('a focused argument shows its reasoning once and keeps the exact section reachable',async()=>{
    const value=content();value.sections=[{id:'reasoning',title:'Why the bound suffices',body:'Unique readable derivation.\n\n[[statement:failure-bound]]\n\nThe exact premise is used above.'}];value.arguments[0]={...argument,section_id:'reasoning'};
    const app=await boot({record:note(value),params:query({argument:'attack'})}),full=app.q('full');
    assert.equal(full.querySelector('.nb-argument-block').classList.contains('nb-structured-selected'),true);
    assert.equal(full.querySelector('.nb-reader-contents'),null);
    assert.equal(full.querySelector('.nb-statements-list'),null);
    assert.equal(full.textContent.split('Unique readable derivation.').length-1,1);
    assert.match(full.querySelector('.nb-argument-reasoning').textContent,/Unique readable derivation/);
    assert.equal(full.querySelectorAll('[data-note-statement="failure-bound"]').length,1);assert.doesNotMatch(full.querySelector('.nb-argument-reasoning').textContent,/\[\[statement:/);
    assert.ok(full.querySelectorAll('a').some(a=>a.href.endsWith('note_section=reasoning')));
    assert.equal(full.querySelector('.nb-full-text').hidden,true);assert.equal(full.querySelector('.nb-readable-view').hidden,false);
  });
  await test('a placed conclusion is defined once inside its reasoning, including after context enrichment',async()=>{
    const value=canonicalContent();value.sections=[{id:'reasoning',title:'Why this follows',body:'The experiment first.\n\n[[statement:success-claim]]\n\nThe proof and its limits follow.'}];value.arguments[0].section_id='reasoning';
    const app=await boot({record:note(value),params:query({argument:'attack'}),respond:url=>url.includes('/context?')?{argument:{...canonicalResolved(),section_id:'reasoning'}}:undefined}),full=app.q('full');
    assert.equal(full.textContent.split(conclusion.definition).length-1,1);
    assert.match(full.querySelector('.nb-argument-conclusion').querySelector('a').href,/statement=success-claim$/);
    assert.equal(full.querySelector('.nb-argument-reasoning').querySelector('[data-note-statement="success-claim"]').textContent.includes(conclusion.definition),true);
  });
  await test('premise descriptions keep exact contextual links and one explanation without duplicate definitions',async()=>{
    const value=content();value.arguments[0]={...argument,dependencies:[{statement:{statement_id:statement.id},use:'Specialize to the actual sampler.'}]};
    const app=await boot({record:note(value),enabled:false,params:query({argument:'attack'})}),dependencies=app.q('full').querySelector('.nb-dependency-list');
    assert.equal(dependencies.textContent.includes(statement.definition),false);assert.match(dependencies.querySelector('a').href,/revision=1&sha256=a{64}&statement=failure-bound$/);assert.match(dependencies.textContent,/How this premise is used.*Specialize to the actual sampler/s);
    assert.equal(dependencies.querySelectorAll('.nb-use-details').length,1);
  });
  await test('a multi-argument note requires an explicit choice for an update',async()=>{
    const value=content();value.arguments.push({...argument,id:'second',title:'Another independent argument'});
    const app=await boot({readerTools:true,record:note(value)});byText(app.q('full'),'Propose an argument update').click();
    assert.ok(byText(app.q('dialog-body'),'Another independent argument'));
    assert.equal(app.requests.some(request=>request.url.endsWith('/statement-workflow/targets')),false);
    byText(app.q('dialog-body'),'Another independent argument').click();await flush();
    assert.match(app.q('dialog-body').querySelector('.nb-update-preview').textContent,/Another independent argument/);
  });
  await test('ordinary notes gain no required structure and edits preserve exact optional fields',async()=>{
    let saved;const app=await boot({respond:(url,body)=>{if(body&&url.endsWith('/revisions')){saved=body;return{note:note()};}}});
    byText(app.q('full'),'Edit').click();assert.equal(app.q('dialog-body').querySelector('.nb-statement-editor'),null);
    app.root.querySelector('[name="title"]').value='A clearer title';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.deepEqual(JSON.parse(JSON.stringify(saved.content.statements)),[statement]);assert.deepEqual(JSON.parse(JSON.stringify(saved.content.arguments)),[argument]);
    assert.equal(saved.expected_revision,1);assert.equal(saved.content_sha256,hash);
    const plain=content();delete plain.statements;delete plain.arguments;const other=await boot({record:note(plain)});assert.equal(other.q('full').querySelector('.nb-structured-research'),null);
  });
  await test('explicit review tooling retains legacy statement editing without exposing it in the normal editor',async()=>{
    const app=await boot({readerTools:true});byText(app.q('full'),'Edit').click();
    assert.equal(app.root.querySelector('[name="statement-title-0"]').value,statement.title);
    assert.equal(app.root.querySelector('[name="statement-definition-0"]').value,statement.definition);
    assert.equal(app.root.querySelector('[name="statement-placement-0"]').value,'[[statement:failure-bound]]');assert.equal(app.root.querySelector('[name="statement-placement-0"]').readOnly,true);
    assert.ok(byText(app.q('dialog-body'),'Add statement'));assert.ok(!app.q('dialog-body').querySelector('.nb-statement-editor').open);
  });
  await test('saving a dangling placement preserves the draft and makes no write request',async()=>{
    const value=content();value.sections=[{id:'reasoning',title:'Reasoning',body:'Before.\n\n[[statement:failure-bound]]\n\nAfter.'}];
    const app=await boot({record:note(value)});byText(app.q('full'),'Edit').click();
    app.root.querySelector('[name="readable-body-0"]').value='Before.\n\n[[statement:missing]]\n\nAfter.';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.equal(app.requests.some(x=>x.body&&x.url.endsWith('/revisions')),false);assert.match(app.q('dialog-body').textContent,/placement must name a canonical statement.*draft is still here/s);
    assert.match(app.root.querySelector('[name="readable-body-0"]').value,/\[\[statement:missing\]\]/);
  });
  await test('proposing an argument pins target adoption, sources and scope; uncertain retries are identical',async()=>{
    const writes=[];const app=await boot({params:query({argument:'attack'}),respond:(url,body)=>{if(body&&url.endsWith('/statement-workflow/updates')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('No receipt'),{status:503});return{update:{id:'update-one'}};}}});
    byText(app.q('full'),'Propose update').click();await flush();app.root.querySelector('[name="argument-reason"]').value='This exact argument supplies the required bound.';
    const choice=app.root.querySelector('[name="argument-target"]');assert.equal(choice.value,'');
    app.q('dialog-body').querySelector('form').dispatchEvent({type:'submit'});await flush();assert.equal(writes.length,0);
    assert.match(app.q('dialog-body').textContent,/Choose the exact result/);choice.value='trial-attack';choice.dispatchEvent({type:'change'});
    let form=app.q('dialog-body').querySelector('form');form.dispatchEvent({type:'submit'});await flush();assert.equal(writes.length,1);assert.equal(writes[0].expected_adoption_id,'adoption-one');assert.equal(writes[0].argument.argument_id,'attack');
    assert.equal(app.root.querySelector('[name="argument-reason"]').disabled,true);form.dispatchEvent({type:'submit'});await flush();await app.whenNavigated();assert.deepEqual(writes[1],writes[0]);assert.match(app.location.hash,/statement_update=update-one/);
  });
  await test('review refreshes the same route after a decision and does not offer approval on stale proposals',async()=>{
    let applied=false;const app=await boot({reviewOnly:true,params:new URLSearchParams({section:'proposals',statement_update:'update-one'}),respond:(url,body)=>{
      if(body&&url.endsWith('/review')){applied=true;return{update:{...update(),status:'approved'}};}
      if(url.endsWith('/updates/update-one'))return{update:applied?{...update(),status:'approved',is_current:true,can_review:false,assessment:{reason:'Checked finite enumeration.',reviewer:{handle:'reviewer'}}}:update()};
    }});
    app.root.querySelector('[name="argument-decision"]').value='approve';app.root.querySelector('[name="argument-assessment"]').value='Checked finite enumeration.';app.q('full').querySelector('form').dispatchEvent({type:'submit'});await flush();
    assert.match(app.q('full').textContent,/Applied.*Review decision/s);assert.equal(app.q('full').querySelector('form'),null);
    const stale=await boot({reviewOnly:true,params:new URLSearchParams({section:'proposals',statement_update:'update-one'}),respond:url=>url.endsWith('/updates/update-one')?{update:{...update(),stale:true,can_apply:false}}:undefined});
    assert.equal(stale.root.querySelector('[name="argument-decision"]').options.some(x=>x.value==='approve'),false);assert.match(stale.q('full').textContent,/cannot overwrite/);
  });
  await test('a newer roster event does not imply an earlier displayed selection was replaced',async()=>{
    const receipt={...update(),operation:'select_argument',status:'approved',is_current:false,can_review:false,assessment:{reason:'Selected this exact argument.',reviewer:{handle:'reviewer'}}};
    const app=await boot({params:new URLSearchParams({statement_update:'update-one'}),respond:url=>url.endsWith('/updates/update-one')?{update:receipt}:undefined});
    assert.equal(app.q('full').querySelector('.nb-update-state').textContent,'Applied');
    assert.doesNotMatch(app.q('full').textContent,/Previously applied|Replaced|No longer displayed/);
    assert.ok(app.q('full').querySelectorAll('a').some(a=>a.href==='#notebook?statement_target=trial-attack'));
  });
  await test('authored support and challenge links are evidence, not automatic verdicts',async()=>{
    const app=await boot({params:query({statement:'failure-bound'}),respond:url=>url.includes('/context?')?{targets:[],related_uses:[],related_research:[{source,title:'A scoped counterexample',relation:'challenges',scope:'Only beta under the actual sampler.'}]}:undefined});
    assert.match(app.q('full').textContent,/Related research.*A scoped counterexample.*Challenging note.*Only beta/s);assert.doesNotMatch(app.q('full').textContent,/Refuted|Broken/);
  });
  await test('incomplete arguments and withdrawn uses cannot look like current justifications',async()=>{
    const value=content();value.arguments[0]={...argument,status:'incomplete'};
    const app=await boot({record:note(value),params:query({argument:'attack'}),respond:url=>url.includes('/context?')?{targets:[{target,current:{status:'withdrawn'}}],related_uses:[],related_research:[]}:undefined});
    assert.equal(app.q('full').querySelector('.nb-argument-status').textContent,'Incomplete argument');
    const uses=app.q('full').querySelector('.nb-used-here');assert.equal(uses.querySelector('h4').textContent,'Previously used by');
  });
  await test('withdrawn alternatives are historical uses even when another argument remains displayed',async()=>{
    const data=rosterData();data.roster[0].status='withdrawn';data.roster[0].is_current=false;data.roster[1].is_current=true;
    data.current={...data.current,status:'selected',argument:data.roster[1].argument,resolved_argument:data.roster[1].resolved_argument};
    const app=await boot({params:query({argument:'attack'}),respond:url=>url.includes('/context?')?{argument:resolved,targets:[data]}:undefined});
    assert.equal(app.q('full').querySelector('.nb-used-here').querySelector('h4').textContent,'Previously used by');
  });
  await test('a full-text evidence note can propose a scoped withdrawal of the exact current argument',async()=>{
    const plain=content();delete plain.statements;delete plain.arguments;let sent;
    const app=await boot({readerTools:true,record:note(plain),respond:(url,body)=>{
      if(url.endsWith('/statement-workflow/targets'))return{targets:[{target,current:{id:'event-one',status:'selected',argument:{...source,argument_id:'attack'},resolved_argument:resolved}}]};
      if(body&&url.endsWith('/statement-workflow/updates')){sent=JSON.parse(JSON.stringify(body));return{update:{id:'update-one'}};}
    }});
    byText(app.q('full'),'Propose an argument update').click();await flush();
    assert.equal(app.root.querySelector('[name="argument-operation"]').value,'withdraw_argument');
    app.root.querySelector('[name="argument-target"]').value='trial-attack';
    app.root.querySelector('[name="argument-target"]').dispatchEvent({type:'change'});
    app.root.querySelector('[name="argument-reason"]').value='The exact beta count exceeds the claimed bound.';app.q('dialog-body').querySelector('form').dispatchEvent({type:'submit'});await flush();
    assert.equal(sent.operation,'withdraw_argument');assert.deepEqual(sent.argument,{...source,argument_id:'attack'});assert.equal(sent.expected_adoption_id,'event-one');assert.deepEqual(sent.evidence,[source]);
  });
  await test('withdrawn targets preserve the earlier argument without claiming the conclusion false',async()=>{
    const app=await boot({params:new URLSearchParams({statement_target:'trial-attack'}),respond:url=>url.endsWith('/targets/trial-attack')?{target,current:{status:'withdrawn',argument:{...source,argument_id:'attack'},withdrawn_argument:resolved,resolved_argument:null,reason:'Counterexample in beta.'},history:[]}:undefined});
    assert.match(app.q('full').textContent,/justification withdrawn.*Counterexample in beta.*No argument is currently selected/s);assert.match(app.q('full').textContent,/Previously used argument/);assert.doesNotMatch(app.q('full').textContent,/Scheme broken|Conclusion false/);
  });
  await test('target roster distinguishes initial admission from reviewed alternatives without claiming truth',async()=>{
    const data=rosterData(),app=await boot({params:new URLSearchParams({statement_target:target.id}),respond:url=>url.endsWith('/targets/'+target.id)?data:undefined});
    const full=app.q('full');assert.match(full.textContent,/Displayed argument.*Initial admission · not independently reviewed/s);
    const alternatives=full.querySelector('.nb-argument-roster');assert.match(alternatives.textContent,/Independent enumeration.*Independently reviewed by reviewer/s);
    assert.match(alternatives.querySelector('a').href,/argument=enumeration$/);
    assert.doesNotMatch(full.textContent,/Selected by a reviewer|Theorem|Mathematically verified/);
    byText(full,'Write an argument note').click();
    const reference=app.root.querySelector('[name="citation-0-url"]');assert.match(reference.value,/statement=success-claim$/);
    assert.equal(app.root.querySelector('[name="body"]').value,'');assert.equal(app.requests.some(r=>r.body),false);
  });
  await test('target readers keep premises compact and omit only an exactly repeated scope',async()=>{
    for(const identical of [true,false]){
      const data=rosterData();if(identical)data.target.scope=data.current.resolved_argument.scope;
      const app=await boot({params:new URLSearchParams({statement_target:target.id}),respond:url=>url.endsWith('/targets/'+target.id)?data:undefined});
      const card=app.q('full').querySelector('.nb-argument-block');assert.equal(Boolean(card.querySelector('.nb-argument-scope')),!identical);
      const premises=card.querySelector('.nb-dependency-list');assert.match(premises.textContent,/A-12.*bounded failure event/s);
      assert.doesNotMatch(premises.textContent,/For the stated sampler/);assert.match(premises.querySelector('a').href,/statement=failure-bound$/);
      assert.match(card.querySelector('.nb-argument-conclusion').textContent,/seven eighths/);
    }
  });
  await test('real target readers link back to their authorized scheme context only',async()=>{
    for(const context of [undefined,{scheme_id:'ml-kem',version:'compressed-research-v4'},{scheme_id:'ml-kem',version:'../about'}, {scheme_id:'ml-kem'}]){
      const data={...rosterData(),scheme_context:context};
      const app=await boot({params:new URLSearchParams({statement_target:target.id}),respond:url=>url.endsWith('/targets/'+target.id)?data:undefined});
      const back=app.q('full').querySelectorAll('a').find(a=>a.textContent==='← Scheme overview');
      if(context?.version==='compressed-research-v4'){
        assert.equal(back.href,'#scheme/ml-kem/compressed-research-v4');
        const previous=app.window.location.hash;back.dispatchEvent({type:'click',ctrlKey:true});assert.equal(app.window.location.hash,previous);
        back.click();await app.whenNavigated();assert.equal(app.window.location.hash,'#scheme/ml-kem/compressed-research-v4');
      }else assert.equal(back,undefined);
    }
  });
  await test('selecting an alternative proposes the exact argument against the latest roster head',async()=>{
    const data=rosterData();let sent;
    const app=await boot({params:new URLSearchParams({statement_target:target.id}),respond:(url,body)=>{
      if(url.endsWith('/targets/'+target.id))return data;if(url.endsWith('/statement-workflow/targets'))return{targets:[data]};
      if(body&&url.endsWith('/statement-workflow/updates')){sent=JSON.parse(JSON.stringify(body));return{update:{id:'update-one'}};}
    }});
    byText(app.q('full'),'Propose selection').click();await flush();
    assert.equal(app.root.querySelector('[name="argument-target"]').value,target.id);assert.equal(app.root.querySelector('[name="argument-operation"]').value,'select_argument');
    assert.equal(sent,undefined);app.root.querySelector('[name="argument-reason"]').value='Use independently enumerated reasoning.';
    app.q('dialog-body').querySelector('form').dispatchEvent({type:'submit'});await flush();
    assert.equal(sent.argument.argument_id,'enumeration');assert.equal(sent.expected_adoption_id,'latest-roster-head');
    assert.deepEqual(sent.evidence,[source]);
    assert.deepEqual(sent.target.conclusion_statement,data.target.conclusion_statement);assert.equal(sent.operation,'select_argument');
  });
  await test('new alternatives and withdrawals remain scoped review proposals, never immediate roster changes',async()=>{
    const data=rosterData();
    for(const operation of ['admit_argument','withdraw_argument']){
      let sent;const app=await boot({params:query({argument:'attack'}),respond:(url,body)=>{
        if(url.endsWith('/statement-workflow/targets'))return{targets:[data]};
        if(body&&url.endsWith('/statement-workflow/updates')){sent=JSON.parse(JSON.stringify(body));return{update:{id:'update-one'}};}
      }});
      byText(app.q('full'),'Propose update').click();await flush();
      app.root.querySelector('[name="argument-target"]').value=target.id;app.root.querySelector('[name="argument-target"]').dispatchEvent({type:'change'});
      app.root.querySelector('[name="argument-operation"]').value=operation;app.root.querySelector('[name="argument-operation"]').dispatchEvent({type:'change'});
      if(operation==='withdraw_argument'){
        const choice=app.root.querySelector('[name="argument-withdrawal"]');assert.equal(choice.value,'');
        choice.value='1';
      }
      assert.equal(sent,undefined);app.root.querySelector('[name="argument-reason"]').value='Scoped independent assessment is needed.';
      app.q('dialog-body').querySelector('form').dispatchEvent({type:'submit'});await flush();
      assert.equal(sent.operation,operation);assert.equal(sent.argument.argument_id,operation==='withdraw_argument'?'enumeration':'attack');
      assert.equal(sent.expected_adoption_id,'latest-roster-head');
    }
  });
  await test('an unavailable source shows only the preserved decision receipt with no missing-field artifacts or actions',async()=>{
    for (const targetValue of [undefined,{id:'trial-attack',title:'Update source unavailable'}]) {
      const app=await boot({reviewOnly:true,params:new URLSearchParams({section:'proposals',statement_update:'update-one'}),respond:url=>url.endsWith('/updates/update-one')?{update:{id:'update-one',...(targetValue?{target:targetValue}:{}),status:'approved',source_unavailable:true,can_review:false,can_apply:false,reason:'The decision is preserved, but an exact source is no longer available.',assessment:{decision:'approve',reviewer:{handle:'reviewer'}}}}:undefined});
      const full=app.q('full');assert.match(full.textContent,/Update source unavailable.*Decision preserved.*exact source is no longer available.*reviewer/s);
      assert.doesNotMatch(full.textContent,/Applies to|What changes|Evidence|undefined|Applied/);assert.equal(full.querySelector('form'),null);assert.equal(full.querySelector('.nb-argument-block'),null);
    }
  });
  await test('argument review detail returns to Proposals while ordinary update and target details hide library headings',async()=>{
    const reviewer=await boot({reviewOnly:true,params:new URLSearchParams({section:'proposals',statement_update:'update-one'})});
    const back=byText(reviewer.q('full'),'← Proposals');assert.ok(back);assert.equal(byText(reviewer.q('full'),'← Flagged requests'),undefined);
    back.click();await reviewer.whenNavigated();assert.equal(reviewer.location.hash,'#reviews?section=proposals');
    for (const params of [new URLSearchParams({statement_update:'update-one'}),new URLSearchParams({statement_target:'trial-attack'})]) {
      const app=await boot({params,respond:url=>url.endsWith('/targets/trial-attack')?{target,current:{status:'selected',argument:{...source,argument_id:'attack'},resolved_argument:resolved,reviewer:{handle:'reviewer'},reason:'Checked the scoped argument.'},history:[]}:undefined});
      assert.equal(app.root.querySelector('.nb-heading').hidden,true);assert.equal(app.q('library').hidden,true);assert.equal(app.q('full').hidden,false);assert.equal(app.root.classList.contains('nb-note-reader'),true);
      assert.ok(app.q('full').querySelector('.nb-full-title'));
    }
  });
  await test('untitled exact internal citations remain useful links with readable relation and scope',async()=>{
    const value=content();delete value.statements;delete value.arguments;
    const prefix='/#notebook?note=prior&revision=1&sha256='+hash;
    value.references=[
      {url:prefix+'&statement=failure-bound',relation:'responds_to',scope:'The exact bound being discussed.'},
      {url:prefix+'&argument=attack',relation:'challenges',scope:'Only this attack argument.'},
      {url:prefix},
      {title:'Original title',url:prefix+'&statement=failure-bound'},
      {url:'javascript:alert(1)',relation:'supports'},
      {url:'https://example.org/without-title'},
      {url:'/#notebook?note=prior&revision=1&statement=unbound'}
    ];
    const app=await boot({record:note(value)}),sources=app.q('full').querySelector('.nb-research-sources'),links=sources.querySelectorAll('a');
    assert.deepEqual(links.map(a=>a.textContent),['Exact statement','Exact argument','Exact note revision','Original title']);
    assert.match(sources.textContent,/Responds to.*exact bound.*Challenges.*Only this attack/s);
    assert.equal(links[0].href,prefix.slice(1)+'&statement=failure-bound');assert.ok(links.every(a=>a.href.startsWith('#notebook?')));
    assert.equal(app.requests.some(r=>r.url.includes('/notes/prior')),false);
  });
  await test('inert mathematical content cannot inject markup through statements',async()=>{
    const value=content();value.statements[0]={...statement,definition:'<img src=x onerror=alert(1)> $x$'};value.sections=[{id:'reasoning',title:'Context',body:'<script>alert(1)</script>\n\n[[statement:failure-bound]]\n\nAfter.'}];const app=await boot({record:note(value)});assert.equal(app.q('full').querySelectorAll('img,script').length,0);assert.match(app.q('full').textContent,/<img/);
  });
  await test('archive notices identify the exact archived revision without denying its citation',async()=>{
    for(const kind of ['revision','curated','unarchived-revision']) {
      const record=note();record.archive_reason='Retired demonstration.';
      if(kind==='revision')record.revisions[0].archive_reason=record.archive_reason;
      if(kind==='curated')record.source_kind='curated-research';
      const app=await boot({record,params:query({statement:'failure-bound'}),enabled:false});
      assert.equal(app.q('full').textContent.includes('This exact revision remains available for citations.'),kind!=='unarchived-revision');
      assert.equal(app.document.activeElement.dataset.noteStatement,'failure-bound');
    }
  });
  console.log(`\n${passed} statement workflow frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

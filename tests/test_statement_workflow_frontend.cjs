const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const web = path.resolve(__dirname, '../web/notebook');
const hash = 'a'.repeat(64), user = {id:1,handle:'alice'};
const source = {note_id:'note-one',revision:1,content_sha256:hash};
const statement = {id:'failure-bound',label:'A-12',title:'A bounded failure event',definition:'For the stated sampler, $\\delta \\leq 1/128$.'};
const argument = {id:'attack',title:'An attack using the bound',conclusion:'Success is at least seven eighths.',scope:'Configurations alpha and beta.',status:'conditional',dependencies:[{statement:{statement_id:'failure-bound'},configuration_ids:['alpha','beta'],required:'Failure at most 1/8.',mapping:'Native message failure.',justification:'Success is the complementary event.'}]};
const content = () => ({title:'Finite sampler audit',claim:'A small illustrative experiment.',conditions:'Not a claim about a deployed scheme.',body:'# Full record\n\nTechnical derivation stays here.',tags:['illustrative-trial'],statements:[statement],arguments:[argument],extensions:{illustrative:true,statement_trial:'sampler-audit-v1'}});
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
async function boot({record=note(),params=query({}),respond,reviewOnly=false,enabled=true}={}) {
  const app=await createApp({schemes:[],hash:'#about'});let sequence=0,controller,navigation=Promise.resolve();
  app.sandbox.crypto.randomUUID=()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;
  app.window.confirm=()=>true;app.window.localStorage={getItem:()=>null};
  app.window.CrucibleMath={render:(target,text)=>target.textContent=text,markdown:(target,text)=>target.textContent=text};
  for(const file of ['store.js','seed.js','statement-workflow.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(web,file),'utf8'),app.sandbox,{filename:file});
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
  controller=app.window.CrucibleNotebook.mount(root,{api,navigate,account:()=>user,reviewOnly,statementWorkflowAvailable:()=>enabled});
  await controller.show(params);await flush();
  return{...app,root,q,requests,controller,show:p=>controller.show(p),whenNavigated:()=>navigation};
}
let passed=0;async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('canonical statements and separate arguments render in the ordinary reader',async()=>{
    const value=content();value.arguments.push({...argument,id:'direct',title:'Direct enumeration',status:'unconditional',dependencies:[]});
    const app=await boot({record:note(value)}),full=app.q('full');
    assert.equal(full.querySelectorAll('.nb-statement-block').length,1);assert.equal(full.querySelectorAll('.nb-argument-block').length,2);
    assert.match(full.textContent,/A-12.*bounded failure event/s);
    assert.ok(full.querySelectorAll('.nb-argument-entry').every(entry=>!entry.open));
    assert.equal(full.querySelector('.nb-statements-list').open,false);
    const fullText=full.querySelector('.nb-full-text');assert.equal(fullText.open,false);
    assert.equal(fullText.querySelector('summary').textContent,'Full text');
    assert.match(fullText.querySelector('.nb-body').textContent,/Technical derivation/);
    assert.match(full.textContent,/No readable explanation has been added\./);
    assert.match(full.textContent,/Illustrative trial/);assert.equal(app.requests.some(x=>x.url.includes('/context')),false);
  });
  await test('exact statement anchors focus the canonical block and carry revision/hash to server',async()=>{
    const app=await boot({params:query({statement:'failure-bound'})});assert.equal(app.document.activeElement.dataset.noteStatement,'failure-bound');
    const request=app.requests.find(x=>x.url.startsWith('/api/notebook/notes/note-one'));assert.match(request.url,/sha256=a{64}&statement=failure-bound/);
    app.q('full').querySelector('.nb-statement-block').querySelector('.nb-structured-cite').click();assert.match(app.root.querySelector('[name="statement-link"]').value,/statement=failure-bound$/);
  });
  await test('a canonical conclusion uses its exact statement without promoting it to a theorem',async()=>{
    const value=canonicalContent(), app=await boot({record:note(value),params:query({argument:'attack'}),enabled:false});
    const block=app.q('full').querySelector('.nb-argument-conclusion'),reference=block.querySelector('a');
    assert.match(block.textContent,/Conclusion.*S-2.*Shared-secret recovery claim.*seven eighths/s);
    assert.match(reference.href,/revision=1&sha256=a{64}&statement=success-claim$/);
    assert.doesNotMatch(block.textContent,/Theorem|Proved|Unconditional|undefined/);
    assert.equal(value.arguments[0].conclusion,undefined);assert.equal(app.q('full').querySelector('.nb-full-text').open,false);
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
    const value=content();value.sections=[{id:'reasoning',title:'Why the bound suffices',body:'Unique readable derivation.'}];value.arguments[0]={...argument,section_id:'reasoning'};
    const app=await boot({record:note(value),params:query({argument:'attack'})}),full=app.q('full');
    assert.equal(full.querySelector('.nb-argument-block').classList.contains('nb-structured-selected'),true);
    assert.equal(full.querySelector('.nb-statements-list').open,false);
    assert.equal(full.textContent.split('Unique readable derivation.').length-1,1);
    assert.match(full.querySelector('.nb-argument-reasoning').textContent,/Unique readable derivation/);
    assert.ok(full.querySelectorAll('a').some(a=>a.href.endsWith('note_section=reasoning')));
    assert.equal(full.querySelector('.nb-full-text').open,false);
  });
  await test('new premise descriptions show the exact statement and one explanation',async()=>{
    const value=content();value.arguments[0]={...argument,dependencies:[{statement:{statement_id:statement.id},use:'Specialize to the actual sampler.'}]};
    const app=await boot({record:note(value),enabled:false,params:query({argument:'attack'})}),dependencies=app.q('full').querySelector('.nb-dependency-list');
    assert.match(dependencies.textContent,/For the stated sampler/);assert.match(dependencies.textContent,/How this premise is used.*Specialize to the actual sampler/s);
    assert.equal(dependencies.querySelectorAll('.nb-use-details').length,1);
  });
  await test('a multi-argument note requires an explicit choice for an update',async()=>{
    const value=content();value.arguments.push({...argument,id:'second',title:'Another independent argument'});
    const app=await boot({record:note(value)});byText(app.q('full'),'Propose an argument update').click();
    assert.ok(byText(app.q('dialog-body'),'Another independent argument'));
    assert.equal(app.requests.some(request=>request.url.endsWith('/statement-workflow/targets')),false);
    byText(app.q('dialog-body'),'Another independent argument').click();await flush();
    assert.match(app.q('dialog-body').querySelector('.nb-update-preview').textContent,/Another independent argument/);
  });
  await test('ordinary notes gain no required structure and edits preserve exact optional fields',async()=>{
    let saved;const app=await boot({respond:(url,body)=>{if(body&&url.endsWith('/revisions')){saved=body;return{note:note()};}}});
    byText(app.q('full'),'Edit').click();app.root.querySelector('[name="title"]').value='A clearer title';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.deepEqual(JSON.parse(JSON.stringify(saved.content.statements)),[statement]);assert.deepEqual(JSON.parse(JSON.stringify(saved.content.arguments)),[argument]);
    assert.equal(saved.expected_revision,1);assert.equal(saved.content_sha256,hash);
    const plain=content();delete plain.statements;delete plain.arguments;const other=await boot({record:note(plain)});assert.equal(other.q('full').querySelector('.nb-structured-research'),null);
  });
  await test('statement editing offers title and definition controls, not a mandatory JSON form',async()=>{
    const app=await boot();byText(app.q('full'),'Edit').click();
    assert.equal(app.root.querySelector('[name="statement-title-0"]').value,statement.title);
    assert.equal(app.root.querySelector('[name="statement-definition-0"]').value,statement.definition);
    assert.ok(byText(app.q('dialog-body'),'Add statement'));assert.ok(!app.q('dialog-body').querySelector('.nb-statement-editor').open);
  });
  await test('proposing an argument pins target adoption, sources and scope; uncertain retries are identical',async()=>{
    const writes=[];const app=await boot({respond:(url,body)=>{if(body&&url.endsWith('/statement-workflow/updates')){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('No receipt'),{status:503});return{update:{id:'update-one'}};}}});
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
    const app=await boot({record:note(plain),respond:(url,body)=>{
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
      let sent;const app=await boot({respond:(url,body)=>{
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
    const value=content();value.statements[0]={...statement,definition:'<img src=x onerror=alert(1)> $x$'};const app=await boot({record:note(value)});assert.equal(app.q('full').querySelectorAll('img,script').length,0);assert.match(app.q('full').textContent,/<img/);
  });
  console.log(`\n${passed} statement workflow frontend checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

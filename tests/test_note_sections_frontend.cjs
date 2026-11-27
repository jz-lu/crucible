const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const web = path.resolve(__dirname, '../web/notebook');
const store = require('../web/notebook/store.js');
const hash = 'a'.repeat(64), user = {id:1,handle:'alice'};
const source = () => ({title:'A readable research note',claim:'An exact scoped result.',conditions:'Under stated assumptions.',
  body:'# Complete derivation\n\nOriginal technical text <script>inert</script>.\n\n```text\nExact code\n```',tags:['algebra','ai-written'],
  sections:[{id:'idea',title:'The idea',body:'Readable explanation <img onerror=alert(1)>.'},{id:'limits',title:'What remains open',body:'A separate limitation.'}],
  extensions:{authored:'retained'},references:[]});
const note = (content=source()) => ({id:'note-one',number:7,owner:user,can_edit:true,can_review:false,revision:1,content_sha256:hash,
  publication_status:'private',revisions:[{revision:1,content_sha256:hash,content,publication_status:'private',created_at:100}]});
const query = (section, more={}) => new URLSearchParams({note:'note-one',revision:'1',sha256:hash,...(section?{note_section:section}:{}),...more});
const byText = (root,label) => root.querySelectorAll('button').find(button=>button.textContent===label);
async function boot({record=note(),params=new URLSearchParams({note:'note-one'}),respond,reviewOnly=false,curatedNotesAvailable=()=>true,noteOrigin}={}) {
  const app=await createApp({schemes:[],hash:'#about'});let account=user,sequence=0,controller,navigation=Promise.resolve();
  app.sandbox.crypto.randomUUID=()=>`test-${++sequence}`;app.sandbox.crypto.subtle=require('node:crypto').webcrypto.subtle;
  app.window.confirm=()=>true;app.window.localStorage={getItem:()=>null};
  // Root's bounded Markdown renderer has its own parser/security tests. This
  // transport fixture verifies that section strings reach it without HTML sinks.
  vm.runInContext(fs.readFileSync(path.join(web, '../math.js'),'utf8'),app.sandbox,{filename:'math.js'});
  const math = app.window.CrucibleMath;
  app.window.CrucibleMath={render:math.render,markdown:(target,text,options)=>{math.markdown(target,text,options);target.dataset.markdown='true';}};
  for(const file of ['store.js','seed.js','notebook.js'])vm.runInContext(fs.readFileSync(path.join(web,file),'utf8'),app.sandbox,{filename:file});
  const json=value=>vm.runInContext('('+JSON.stringify(value)+')',app.sandbox);record=json(record);
  const requests=[], root=app.$('#notebook-workspace'), q=name=>root.querySelector(`[data-nb="${name}"]`);
  const api=async(url,body,options)=>{requests.push({url,body,options});const custom=await respond?.(url,body,options);if(custom!==undefined)return json(custom);
    if(url==='/notebook/request-types.json')return JSON.parse(fs.readFileSync(path.join(web,'request-types.json'),'utf8'));
    if(url.startsWith('/api/notebook/notes/note-one'))return{note:record};
    if(url.startsWith('/api/notebook/notes?'))return{notes:[],next_after:null};
    if(url.startsWith('/api/notebook/research-requests?'))return{requests:[],next_after:null};
    if(url==='/api/notebook/curated-note-numbers')return{numbers:{}};
    if(url==='/map/notes.json')return vm.runInContext('({format:"crucible-theory-local-v1",notes:[],views:[]})',app.sandbox);
    throw Error('Unexpected '+url);};
  const navigate=route=>{app.history.pushState(null,'',route);navigation=controller.show(new URLSearchParams(route.split('?')[1]||''));return navigation;};
  controller=app.window.CrucibleNotebook.mount(root,{api,navigate,account:()=>account,reviewOnly,curatedNotesAvailable,noteOrigin});
  await controller.show(params);
  return{...app,root,q,requests,controller,show:next=>controller.show(next),whenNavigated:()=>navigation,setAccount:value=>{account=value;controller.setAccount(value);}};
}
let passed=0;async function test(name,run){await run();console.log('PASS '+name);passed++;}
(async()=>{
  await test('readable sections use the normal reader and Full text preserves the original record',async()=>{
    const app=await boot(),full=app.q('full');
    assert.equal(full.querySelectorAll('.nb-readable-section').length,2);
    assert.equal(full.querySelectorAll('[data-markdown]').length,2);
    assert.match(full.querySelector('.nb-readable').textContent,/<img onerror=alert\(1\)>/);
    assert.equal(full.querySelectorAll('img,script').length,0);
    const technical=full.querySelector('.nb-full-text');assert.ok(technical);assert.equal(technical.hidden,true);
    assert.match(technical.textContent,/Complete derivation.*Original technical text.*Exact code/s);
    assert.doesNotMatch(full.textContent,/No readable explanation has been added/);
    assert.ok(byText(full,'Edit'));assert.ok(byText(full,'Publish'));assert.ok(full.querySelector('.nb-number-label'));assert.equal(byText(full,'Cite this note'),undefined);
  });
  await test('ordinary body uses safe Markdown and preserves adjacent source-line destinations',async()=>{
    const content=source();delete content.sections;
    content.body='A **bold** result with [source](https://example.org/).\nA second source line.\n\n- First item\n- Second item\n\n<img onerror=bad()>よ';
    const app=await boot({record:note(content)}),full=app.q('full');
    assert.equal(full.querySelectorAll('strong').length,1);assert.equal(full.querySelectorAll('li').length,2);
    assert.ok(full.querySelector('a[href="https://example.org/"]'));assert.equal(full.querySelectorAll('img').length,0);
    await app.show(query(null,{location:'L2'}));
    assert.equal(app.document.activeElement.dataset.nbLocation,'L2');assert.ok(app.q('full').querySelector('.nb-destination-nav'));
    assert.equal(app.document.activeElement.querySelector('nav'),null);
  });
  await test('section citations retain all three exact pins and cached navigation focuses only the requested section',async()=>{
    const app=await boot({params:query('idea')});
    assert.equal(app.document.activeElement.dataset.noteSection,'idea');
    const request=app.requests.find(row=>row.url.includes('/notes/note-one'));
    const sent=new URL(request.url,'http://local').searchParams;
    assert.equal(sent.get('revision'),'1');assert.equal(sent.get('sha256'),hash);assert.equal(sent.get('section'),'idea');
    const link=app.q('full').querySelector('.nb-note-contents').querySelectorAll('a')[1];const priorHash=app.location.hash,reads=app.requests.length;assert.equal(link.getAttribute('href'),'#notebook?'+query('limits'));link.click();await app.whenNavigated();
    assert.equal(app.location.hash,priorHash);assert.equal(app.document.activeElement.dataset.noteSection,'limits');assert.equal(app.requests.length,reads);
    assert.equal(app.q('full').querySelectorAll('.nb-section-selected').length,1);
    assert.equal(app.q('full').querySelector('.nb-full-text').hidden,true);
  });
  await test('ordinary headings preserve authored titles without extra premise badges or citation changes',async()=>{
    const content=source(),title='A-25 · At most one eighth of inputs fail';
    content.sections[0].title=title;
    content.extensions.assumptions=[{section_id:'idea',label:'A-25',status:'unproved'}];
    const app=await boot({record:note(content),params:query('idea')});
    const block=app.q('full').querySelector('.nb-readable-section'),heading=block.querySelector('h2');
    assert.equal(heading.textContent,title);
    assert.equal(heading.querySelectorAll('.nb-assumption-label').length,0);
    assert.equal(app.q('full').querySelector('.nb-note-contents').querySelector('a').textContent,title);
    block.querySelector('.nb-section-cite').click();
    const citation=JSON.parse(app.q('dialog-body').querySelector('pre').textContent);
    assert.ok(citation.scope.startsWith('Section idea: '+title+'.'));
    assert.match(citation.url,/revision=1&sha256=a{64}&note_section=idea$/);
    byText(app.q('full'),'Edit').click();
    assert.equal(app.root.querySelector('[name="readable-title-0"]').value,title);
    assert.equal(app.requests.some(row=>row.body),false);
  });
  await test('review workspace keeps premise badges and only omits matching heading prefixes',async()=>{
    for(const [title,premise,badges] of [
      ['A-25 · A limitation',{section_id:'idea',label:'A-25',status:'incomplete'},0],
      ['A-25 · An ordinary section',{section_id:'limits',label:'A-25'},0],
      ['A-250 · A different premise',{section_id:'idea',label:'A-25'},1],
      ['A-25 has a wider scope',{section_id:'idea',label:'A-25'},1],
      ['A-25',{section_id:'idea',label:'A-25'},1],
    ]){
      const content=source();content.sections[0].title=title;content.extensions.assumptions=[premise];
      const app=await boot({record:note(content),reviewOnly:true,params:query('idea',{section:'flagged'})}),heading=app.q('full').querySelector('.nb-readable-section').querySelector('h3');
      assert.equal(heading.textContent,(badges?'A-25 ':'')+title);
      assert.equal(heading.querySelectorAll('.nb-assumption-label').length,badges);
    }
  });
  await test('malformed, ambiguous and unpinned section routes fail before a note read',async()=>{
    for(const params of [new URLSearchParams('note=note-one&note_section=idea'),query('idea',{location:'L1'}),query('constructor'),new URLSearchParams(query('idea')+'&note_section=limits'),query('idea',{view:'map'})]){
      const app=await boot({params});assert.ok(app.q('full').querySelector('.nb-error'));
      assert.equal(app.requests.some(row=>row.url.includes('/notes/note-one')),false);
      assert.equal(app.q('full').querySelector('.nb-readable'),null);
    }
  });
  await test('missing sections and mismatched hashes never substitute another revision or section',async()=>{
    for(const params of [query('absent'),query('idea',{sha256:'b'.repeat(64)})]){
      const app=await boot({params});assert.ok(app.q('full').querySelector('.nb-error'));
      assert.equal(app.q('full').querySelector('.nb-readable'),null);
      assert.equal(byText(app.q('full'),'Publish'),undefined);
    }
  });
  await test('duplicate or invalid stored section identifiers are rejected as a whole',async()=>{
    for(const sections of [[{id:'idea',title:'A',body:'A'},{id:'idea',title:'B',body:'B'}],[{id:'bad]"',title:'A',body:'A'}]]){
      const app=await boot({record:note({...source(),sections})});assert.match(app.q('full').textContent,/invalid readable sections/);
      assert.equal(app.q('full').querySelector('.nb-readable'),null);
    }
  });
  await test('Cite section emits a portable exact citation without reviewer navigation options',async()=>{
    const app=await boot({params:query('limits')});app.q('full').querySelector('.nb-section-cite').click();
    assert.equal(app.q('dialog-title').textContent,'Cite this section');
    const link=app.root.querySelector('[name="citation-link"]').value;
    assert.match(link,/revision=1&sha256=a{64}&note_section=idea$/);
    const reference=JSON.parse(app.q('dialog-body').querySelector('pre').textContent);
    assert.match(reference.scope,/Section idea: The idea/);assert.equal(new URL(reference.url,'http://local').hash.includes('review='),false);
  });
  await test('review navigation section and exact note section coexist without permitting self-review',async()=>{
    const record=note();record.revisions[0].publication_status='submitted';record.revisions[0].publication={requested_at:10};
    const app=await boot({record,reviewOnly:true,params:query('idea',{section:'flagged'})});
    assert.equal(app.document.activeElement.dataset.noteSection,'idea');assert.match(app.q('full').textContent,/Another reviewer must approve/);
    const route=app.q('full').querySelector('.nb-note-contents').querySelector('a').href;
    assert.match(route,/#reviews\?section=flagged&note=note-one/);assert.match(route,/note_section=idea/);
    assert.equal(app.q('full').querySelector('[name="reason"]'),null);
  });
  await test('single-body notes use Human Explanation with an explicit empty Research details view and preserve reviewer disclosures',async()=>{
    for(const sections of [undefined,[]])for(const reviewOnly of [false,true]){
      const content=source();if(sections===undefined)delete content.sections;else content.sections=sections;
      const app=await boot({record:note(content),reviewOnly});
      assert.equal(app.q('full').querySelector('.nb-readable'),null);
      if(reviewOnly)assert.match(app.q('full').textContent,/No readable explanation has been added\./);
      else {assert.doesNotMatch(app.q('full').textContent,/No readable explanation has been added\./);assert.ok(app.q('full').querySelector('.nb-reader-views'));}
      const disclosure=app.q('full').querySelector('.nb-full-text');
      assert.equal(disclosure.tagName,'DETAILS');assert.equal(disclosure.open,!reviewOnly);assert.equal(disclosure.hidden,!reviewOnly);
      if(reviewOnly){
        assert.equal(disclosure.querySelector('summary').textContent,'Full text');
        assert.match(disclosure.querySelector('.nb-body').textContent,/Complete derivation.*Original technical text.*Exact code/s);
      }else{
        assert.match(app.q('full').querySelector('.nb-readable-view').textContent,/Complete derivation.*Original technical text.*Exact code/s);
        byText(app.q('full'),'Research details').click();assert.equal(disclosure.hidden,false);
        assert.match(disclosure.textContent,/No separate research details have been added/);
        byText(app.q('full'),'Human Explanation').click();assert.equal(disclosure.hidden,true);
      }
    }
  });
  await test('passage citations select the panel containing the exact source with or without a readable companion',async()=>{
    for(const readable of [false,true])for(const reviewOnly of [false,true]){
      const content=source();if(!readable)delete content.sections;
      const app=await boot({record:note(content),reviewOnly,params:query(null,{location:'L3'})});
      const disclosure=app.q('full').querySelector('.nb-full-text');
      assert.equal(app.document.activeElement.getAttribute('data-nb-location'),'L3');
      if(readable||reviewOnly){
        assert.equal(disclosure.open,true);assert.equal(disclosure.hidden,false);
        assert.equal(app.document.activeElement.closest('.nb-full-text'),disclosure);
      }else{
        assert.equal(disclosure.hidden,true);
        assert.ok(app.document.activeElement.closest('.nb-readable-view'));
      }
    }
  });
  await test('editing a heading preserves section IDs, source body, authored metadata and optimistic revision pins',async()=>{
    let saved;const app=await boot({respond:(url,body)=>{if(body&&url.endsWith('/revisions')){saved=JSON.parse(JSON.stringify(body));return{note:note()};}}});
    byText(app.q('full'),'Edit').click();
    app.root.querySelector('[name="readable-title-0"]').value='A renamed explanation';
    app.root.querySelector('[name="readable-body-0"]').value='Clearer explanation.';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.equal(saved.content.sections[0].id,'idea');assert.equal(saved.content.sections[0].title,'A renamed explanation');
    assert.equal(saved.content.body,source().body);assert.deepEqual(saved.content.extensions,source().extensions);
    assert.equal(saved.expected_revision,1);assert.equal(saved.content_sha256,hash);
  });
  await test('new human notes have one readable editor and retry an uncertain save unchanged',async()=>{
    const writes=[];const app=await boot({params:new URLSearchParams(),respond:(url,body)=>{if(body&&url==='/api/notebook/notes'){writes.push(JSON.parse(JSON.stringify(body)));if(writes.length===1)throw Object.assign(Error('Receipt unavailable'),{status:503});return{note:note()};}}});
    app.q('new').click();const declaration=app.root.querySelector('[name="authorship"]');declaration.value='human-written';declaration.dispatchEvent({type:'change'});for(const [key,value]of Object.entries(source()))if(['title','body'].includes(key))app.root.querySelector(`[name="${key}"]`).value=value;
    assert.equal(byText(app.q('dialog-body'),'Add readable section'),undefined);
    const form=app.root.querySelector('.nb-form');form.dispatchEvent({type:'submit'});await flush();
    assert.equal(writes.length,1);assert.equal(writes[0].content.sections,undefined);
    assert.equal(app.root.querySelector('[name="body"]').disabled,true);
    app.root.querySelector('[name="body"]').value='Attempted mutation';form.dispatchEvent({type:'submit'});await flush();
    assert.deepEqual(writes[1],writes[0]);
  });
  await test('new notes save without adding a readable companion',async()=>{
    let saved;const app=await boot({params:new URLSearchParams(),respond:(url,body)=>{if(body&&url==='/api/notebook/notes'){saved=JSON.parse(JSON.stringify(body));return{note:note()};}}});
    app.q('new').click();const declaration=app.root.querySelector('[name="authorship"]');declaration.value='human-written';declaration.dispatchEvent({type:'change'});
    assert.equal(app.root.querySelectorAll('.nb-section-editor').length,0);
    assert.doesNotMatch(app.q('dialog-body').textContent,/Readable sections \(optional\)/);
    for(const key of ['title','body'])app.root.querySelector(`[name="${key}"]`).value=source()[key];
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.ok(saved);assert.equal(Object.hasOwn(saved.content,'sections'),false);
    assert.equal(saved.content.body,source().body);
  });
  await test('an incomplete readable section keeps the draft open and sends no mutation',async()=>{
    const app=await boot();byText(app.q('full'),'Edit').click();app.root.querySelector('[name="readable-body-0"]').value='';
    app.root.querySelector('.nb-form').dispatchEvent({type:'submit'});await flush();
    assert.match(app.q('dialog-body').textContent,/Each readable section needs/);assert.equal(app.requests.some(row=>row.body),false);
    assert.equal(app.root.querySelector('[name="readable-body-0"]').disabled,false);
  });
  await test('local corpus capability prevents direct curated links from loading bundled note assets',async()=>{
    const app=await boot({params:new URLSearchParams({note:'curated:old'}),curatedNotesAvailable:()=>false});
    assert.equal(app.requests.some(row=>row.url==='/map/notes.json'||row.url==='/api/notebook/curated-note-numbers'),false);
    assert.match(app.q('full').textContent,/could not be loaded/);
  });
  await test('portable workspaces retain sections and reject unsafe IDs before import',async()=>{
    const revision={revision:1,createdAt:100,...source()},workspace={format:store.FORMAT,notes:[{id:'source',revisions:[revision]}],views:[]};
    assert.deepEqual(store.validateSnapshot(workspace).notes[0].revisions[0].sections,source().sections);
    assert.deepEqual(store.authoredMetadata(source()).sections,source().sections);
    revision.sections=[{id:'constructor',title:'Bad',body:'Bad'}];assert.throws(()=>store.validateSnapshot(workspace),/stable IDs/);
  });
  await test('validated scheme context replaces the back control without changing canonical citations',async()=>{
    let seen;const app=await boot({params:query('idea'),noteOrigin:record=>{seen=record;return{href:'#scheme/fixture/v1?profile=p1',label:'Fixture scheme'};}});
    assert.equal(seen.id,'note-one');assert.equal(app.q('full').querySelector('.nb-back').textContent,'← Fixture scheme');
    assert.ok(app.q('full').querySelectorAll('.nb-note-contents a').every(link=>!link.href.includes('profile=')));
    const unsafe=await boot({noteOrigin:()=>({href:'javascript:alert(1)',label:'Unsafe'})});assert.equal(unsafe.q('full').querySelector('.nb-back').textContent,'← Notes');
  });
  console.log(`\n${passed} readable note section checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

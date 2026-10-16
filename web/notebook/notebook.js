/* Local notebook UI. Shared storage, permissions and agent APIs remain provisional. */
(() => {
  'use strict';
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn,cls='nb-link')=>{const n=el('button',cls,text);n.type='button';n.addEventListener('click',fn);return n;};
  const latest=n=>n.revisions[n.revisions.length-1];
  const math=(tag,cls,text)=>{const n=el(tag,cls);if(window.CrucibleMath)window.CrucibleMath.render(n,text);else n.textContent=text;return n;};
  function mount(root,{navigate}) {
    if(root.notebookController)return root.notebookController;
    let storage;try{storage=window.localStorage;}catch(_){storage=null;}
    const store=window.TheoryStore.create(window.CrucibleTheorySeed,{storage,namespace:'crucible-theory-prototype-v1'});
    let data=store.load(),search='',tag='',previewId=null,params=new URLSearchParams(),lastReadingRoute=null;
    root.classList.add('nb-root');
    root.innerHTML=`<div class="nb-heading"><h1>Research notebook</h1><details class="nb-tools"><summary>Draft tools</summary><div class="nb-tools-menu"><button type="button" data-nb="new">New note</button><button type="button" data-nb="files">Agent files</button></div></details></div><p class="nb-local">Preview · illustrative notes · saved in this browser</p><div data-nb="notice" class="nb-notice" role="status" hidden></div><section data-nb="library"><div class="nb-toolbar"><label class="nb-search"><span class="nb-sr-only">Search notes</span><input type="search" placeholder="Search notes or tags…" data-nb="search"></label><span data-nb="filter"></span><span class="nb-count" data-nb="count"></span></div><div class="nb-list" data-nb="list"></div></section><article class="nb-full" data-nb="full" hidden></article><dialog class="nb-dialog" aria-label="Notebook editor" data-nb="dialog"><div class="nb-dialog-heading"><h2 data-nb="dialog-title"></h2><button type="button" class="nb-close" data-nb="close" aria-label="Close notebook editor">×</button></div><div data-nb="dialog-body"></div></dialog>`;
    const q=name=>root.querySelector(`[data-nb="${name}"]`);
    const tools=root.querySelector('.nb-tools');let returnFocus=null,hasUnsavedDraft=()=>false;
    function notice(text,error=false){q('notice').textContent=text;q('notice').hidden=false;q('notice').classList.toggle('nb-error',error);}
    function url(id,revision=null){return '#notebook?'+new URLSearchParams({note:id,...(revision?{revision:String(revision)}:{})});}
    function openNote(id,revision=null){navigate(url(id,revision));}
    function backToList(){navigate('#notebook');requestAnimationFrame(()=>root.querySelector('.nb-title[aria-expanded="true"]')?.focus({preventScroll:true}));}
    function find(id){return data.notes.find(n=>n.id===id);}
    function show(nextParams=new URLSearchParams()){
      params=new URLSearchParams(nextParams);data=store.snapshot();const id=params.get('note');
      q('library').hidden=Boolean(id);q('full').hidden=!id;
      if(id)renderFull(id);else renderList();
      const readingRoute=id?params.toString():null;
      if(readingRoute&&readingRoute!==lastReadingRoute)requestAnimationFrame(()=>{const heading=q('full').querySelector('h2');heading?.focus({preventScroll:true});q('full').scrollIntoView({block:'start'});});
      lastReadingRoute=readingRoute;
    }
    function renderList(){
      q('search').value=search;q('filter').replaceChildren();
      if(tag)q('filter').append(button(`${tag} ×`,()=>{tag='';renderList();},'nb-tag'));
      const term=search.toLowerCase();const notes=data.notes.filter(n=>{const r=latest(n);return(!tag||r.tags.includes(tag))&&(!term||[r.title,r.claim,r.conditions,r.body,...r.tags].join(' ').toLowerCase().includes(term));});
      q('count').textContent=`${notes.length} ${notes.length===1?'note':'notes'}`;q('list').replaceChildren();
      if(!notes.length)q('list').append(el('p','nb-empty','No matching notes.'));
      notes.forEach((note,index)=>{
        const r=latest(note),expanded=previewId===note.id,item=el('section','nb-entry');
        const heading=el('h2');const title=button('',()=>{previewId=expanded?null:note.id;renderList();root.querySelectorAll('.nb-title')[index]?.focus({preventScroll:true});},'nb-title');
        title.setAttribute('aria-expanded',String(expanded));title.setAttribute('aria-controls',`nb-preview-${index}`);
        const marker=el('span','nb-marker',expanded?'−':'+');marker.setAttribute('aria-hidden','true');title.append(el('span','',r.title),marker);heading.append(title);item.append(heading);
        const preview=el('div','nb-preview');preview.id=`nb-preview-${index}`;preview.hidden=!expanded;
        if(expanded){preview.append(renderTags(r),math('p','nb-claim',r.claim),button('Read full note →',()=>openNote(note.id),'nb-link nb-read'));}
        item.append(preview);q('list').append(item);
      });
    }
    function renderTags(r){const tags=el('div','nb-tags');if(r.kind)tags.append(el('span','nb-kind',r.kind));r.tags.forEach(t=>tags.append(button(t,()=>{tag=t;search='';previewId=null;navigate('#notebook');q('search').focus();},'nb-tag')));return tags;}
    function section(title,text){const s=el('section','nb-section');s.append(el('h3','',title),math('p','',text));return s;}
    function bodyContent(text){const out=document.createDocumentFragment();let inCode=false,buffer=[];const flush=()=>{if(buffer.length){out.append(el('pre','',buffer.join('\n')));buffer=[];}};String(text).split('\n').forEach(line=>{if(line.startsWith('```')){if(inCode)flush();inCode=!inCode;return;}if(inCode){buffer.push(line);return;}if(!line.trim())return;if(/^#{1,3} /.test(line)){out.append(math('h3','',line.replace(/^#{1,3} /,'')));}else if(/^https:\/\/\S+$/.test(line.trim())){const a=el('a','',line.trim());a.href=line.trim();a.target='_blank';a.rel='noopener noreferrer';const p=el('p');p.append(a);out.append(p);}else out.append(math('p','',line));});flush();return out;}
    function renderFull(id){
      const full=q('full');full.replaceChildren(button('← Research notebook',backToList,'nb-link nb-back'));
      const note=find(id),requested=params.get('revision');const revision=note?(requested?note.revisions.find(r=>String(r.revision)===requested):latest(note)):null;
      if(!revision){full.append(el('h2','nb-full-title',note?'Revision not found':'Note not found'));return;}
      const heading=el('h2','nb-full-title',revision.title);heading.tabIndex=-1;
      full.append(heading,renderTags(revision),section('Claim',revision.claim),section('Conditions',revision.conditions));
      const body=el('section','nb-body');body.append(bodyContent(revision.body));full.append(body);
      const details=el('details','nb-details');details.append(el('summary','','Record details'));const records=el('div','nb-record');records.append(el('p','',`Note ID: ${note.id}`),el('p','',`Revision ${revision.revision} of ${note.revisions.length}`));
      const chooser=field('Read a revision','revision',revision.revision,{options:note.revisions.slice().reverse().map(r=>({value:r.revision,label:`Revision ${r.revision}`}))});chooser.input.addEventListener('change',()=>openNote(id,Number(chooser.input.value)));records.append(chooser.wrap);details.append(records);full.append(details);
      const edit=el('details','nb-details');edit.append(el('summary','','Note tools'));edit.append(button('Edit note',()=>editNote(note)));full.append(edit);
    }
    function openDialog(title){returnFocus=document.activeElement;hasUnsavedDraft=()=>false;tools.open=false;q('dialog-title').textContent=title;q('dialog-body').replaceChildren();q('dialog').showModal();return q('dialog-body');}
    function closeDialog(saved=false){if(saved!==true&&hasUnsavedDraft()&&!window.confirm('Discard the unsaved changes to this note?'))return;hasUnsavedDraft=()=>false;q('dialog').close();}
    q('dialog').addEventListener('cancel',e=>{e.preventDefault();closeDialog();});
    window.addEventListener('beforeunload',e=>{if(hasUnsavedDraft()){e.preventDefault();e.returnValue='';}});
    q('dialog').addEventListener('close',()=>{if(returnFocus?.isConnected)(tools.contains(returnFocus)?tools.querySelector('summary'):returnFocus).focus({preventScroll:true});});
    function field(label,name,value='',options={}){
      const wrap=el('label','nb-field'),caption=el('span','',label);caption.id=`nb-field-${name}`;wrap.append(caption);let input;
      if(options.options){input=el('select');options.options.forEach(o=>{const n=el('option','',o.label);n.value=o.value;input.append(n);});}
      else if(options.rows){input=el('textarea');input.rows=options.rows;}else{input=el('input');input.type=options.type||'text';}
      input.name=name;input.value=value??'';input.setAttribute('aria-labelledby',caption.id);if(options.required)input.required=true;if(options.max)input.maxLength=options.max;if(options.placeholder)input.placeholder=options.placeholder;
      wrap.append(input);if(options.help){const hint=el('small','',options.help);hint.id=`nb-hint-${name}`;input.setAttribute('aria-describedby',hint.id);wrap.append(hint);}return{wrap,input};
    }
    function editNote(note=null){
      const r=note?latest(note):null,container=openDialog(note?'Revise note':'New note');
      container.append(el('p','nb-hint',note?'Save a new revision. Earlier versions remain available.':'A precise claim, its conditions, and room to develop the mathematics. Motivated conjectures are welcome.'));
      const form=el('form','nb-form'),error=el('p','nb-error');error.setAttribute('role','alert');
      form.append(field('Title','title',r?.title,{required:true,max:240}).wrap,field('Claim','claim',r?.claim,{required:true,rows:3,max:20000}).wrap,field('Conditions & scope','conditions',r?.conditions,{required:true,rows:2,max:20000}).wrap,field('Note','body',r?.body,{required:true,rows:7,max:40000,help:'Plain text, headings, code blocks and \\(inline TeX\\).'}).wrap);
      const row=el('div','nb-fields-row');row.append(field('Tags','tags',r?.tags.join(', '),{max:4000,placeholder:'Comma-separated subjects'}).wrap,field('Evidence description','kind',r?.kind,{max:100,placeholder:'Conjecture, proof supplied…'}).wrap);form.append(row,error);
      const actions=el('div','nb-form-actions');actions.append(button('Cancel',closeDialog,'nb-button'));const save=el('button','nb-button nb-primary',note?'Save new revision':'Save note');save.type='submit';actions.append(save);form.append(actions);container.append(form);
      const originalFields=JSON.stringify(Array.from(new FormData(form).entries()));hasUnsavedDraft=()=>JSON.stringify(Array.from(new FormData(form).entries()))!==originalFields;
      form.addEventListener('submit',e=>{e.preventDefault();const f=new FormData(form);const value={...window.TheoryStore.authoredMetadata(r),title:f.get('title'),claim:f.get('claim'),conditions:f.get('conditions'),body:f.get('body'),tags:String(f.get('tags')).split(',').map(t=>t.trim()).filter(Boolean),kind:f.get('kind')};try{const result=note?store.reviseNote(note.id,r.revision,value):store.createNote(value);data=store.snapshot();previewId=result.id;closeDialog(true);openNote(result.id);notice(note?'New revision saved.':'Note saved to this browser.');}catch(err){error.textContent=err.message;}});
    }
    function download(name,text){const href=URL.createObjectURL(new Blob([text],{type:'application/json'}));const a=el('a');a.href=href;a.download=name;root.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1000);}
    function agentFiles(){
      const container=openDialog('Work with an agent');container.append(el('p','nb-hint','This draft exchanges files. Notes are saved in this browser, not shared through your Crucible account.'));
      container.append(button('Download notebook workspace',()=>download('crucible-notebook.json',store.exportJSON()),'nb-button'));
      const prompt='Read this Crucible research notebook. Propose a precise claim with its conditions and supporting argument, evidence or motivation. Label unproved conjectures explicitly. Use helpful tags freely. Add a note or append a revision; preserve every existing note and revision. Return the complete workspace JSON for import. Notes do not need to supply a visualization. Do not invent reviewed status.';
      const promptSection=el('section','nb-file-section');promptSection.append(el('h3','','A starting prompt'),el('pre','nb-prompt',prompt),button('Copy prompt',async()=>{try{await navigator.clipboard.writeText(prompt);notice('Prompt copied.');}catch(_){notice('Clipboard unavailable. Select and copy the prompt.',true);}}));container.append(promptSection);
      const importSection=el('section','nb-file-section');importSection.append(el('h3','','Import a revised workspace'),el('p','nb-hint','Export a copy first. Import replaces this local draft while preserving existing note history.'));
      const file=field('Workspace JSON','import','',{type:'file'});file.input.accept='.json,application/json';const summary=el('p','nb-hint');summary.setAttribute('role','status');const apply=button('Import workspace',()=>{},'nb-button nb-primary');apply.disabled=true;let pending=null,importRequest=0;
      file.input.addEventListener('change',async()=>{const request=++importRequest;pending=null;apply.disabled=true;summary.textContent='';const selected=file.input.files[0];if(!selected)return;if(selected.size>window.TheoryStore.MAX_BYTES){summary.textContent='Choose a JSON file smaller than 2 MiB.';return;}try{const text=await selected.text();if(request!==importRequest||file.input.files[0]!==selected||!q('dialog-body').contains(file.input))return;const parsed=window.TheoryStore.validateSnapshot(JSON.parse(text));pending=text;summary.textContent=`${parsed.notes.length} notes ready to import. Nothing has changed yet.`;apply.disabled=false;}catch(err){if(request===importRequest&&q('dialog-body').contains(file.input))summary.textContent=err.message;}});
      apply.addEventListener('click',()=>{try{store.importJSON(pending);data=store.snapshot();search='';tag='';previewId=null;closeDialog();navigate('#notebook');notice('Notebook workspace imported.');}catch(err){summary.textContent=err.message;}});
      importSection.append(file.wrap,summary,apply);container.append(importSection);
    }
    q('new').addEventListener('click',()=>editNote());q('files').addEventListener('click',agentFiles);q('close').addEventListener('click',closeDialog);
    q('search').addEventListener('input',e=>{search=e.target.value;renderList();});
    document.addEventListener('click',e=>{if(!tools.contains(e.target))tools.open=false;});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&tools.open){tools.open=false;tools.querySelector('summary').focus();}});
    q('dialog').addEventListener('click',e=>{if(e.target===q('dialog')){const r=q('dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog();}});
    window.addEventListener('storage',e=>{if(e.key==='crucible-theory-prototype-v1')notice('This notebook changed in another tab. Reload before editing.',true);});
    if(store.lastError)notice(store.lastError,true);
    const controller={show};root.notebookController=controller;return controller;
  }
  window.CrucibleNotebook=Object.freeze({mount});
})();

/* A read-only view of the global notebook content format. No server writes. */
(() => {
  'use strict';
  const {el,tag,label,noteHref,body}=NoteUI;
  const workspace=window.CRUCIBLE_NOTES, map=window.CRUCIBLE_LANDSCAPE;
  const notes=new Map(workspace.notes.map(n=>[n.id,n]));
  const schemes=new Map(map.schemes.map(s=>[s.id,s]));
  const library=document.getElementById('note-library'),reader=document.getElementById('note-reader');
  const list=document.getElementById('note-list'),search=document.getElementById('note-search');
  const role=document.getElementById('note-role'),tagSelect=document.getElementById('note-tag');
  const clear=document.getElementById('clear-filters');
  const roleName={'scheme-description':'Scheme description',comparison:'Comparison',procedure:'Procedure','research-lead':'Research lead','attack-analysis':'Attack analysis'};
  const originName={literature:'Literature','local-adaptation':'Local challenge adaptation','crucible-design':'Crucible design discussion'};
  const state={query:'',role:'',tag:''};
  const latest=n=>n.revisions[n.revisions.length-1];
  for(const t of [...new Set(workspace.notes.flatMap(n=>latest(n).tags))].sort()){
    const option=el('option','',label(t));option.value=t;tagSelect.append(option);
  }
  function chips(tags){const row=el('div','note-tags');tags.forEach(t=>row.append(tag(t)));return row;}
  function renderList(){
    library.hidden=false;reader.hidden=true;document.title='Research notes · Crucible';
    search.value=state.query;role.value=state.role;tagSelect.value=state.tag;
    const shown=workspace.notes.filter(n=>{
      const r=latest(n),m=r.extensions.research_note;
      return (!state.role||m.role===state.role)&&(!state.tag||r.tags.includes(state.tag))&&[r.title,r.claim,r.conditions,r.body,...r.tags].join(' ').toLowerCase().includes(state.query.toLowerCase());
    });
    document.getElementById('note-count').textContent=`${shown.length} of ${workspace.notes.length} notes`;
    clear.hidden=!(state.query||state.role||state.tag);list.replaceChildren();
    for(const n of shown){
      const r=latest(n),m=r.extensions.research_note,row=el('article','note-row');
      const meta=el('p','note-row-meta',`${roleName[m.role]||m.role} · ${originName[m.origin]||m.origin}`);
      const heading=el('h2'),link=el('a','',r.title);link.href=noteHref(n.id,r.revision);heading.append(link);
      row.append(meta,heading,el('p','note-excerpt',r.claim),chips(r.tags));list.append(row);
    }
    if(!shown.length)list.append(el('p','empty','No notes match these filters. Try a broader term or clear the filters.'));
  }
  function sourceList(refs){
    const list=el('ol','note-sources');
    for(const ref of refs){const item=el('li'),a=el('a','',(ref.id?'['+ref.id+'] ':'')+ref.title+' ↗');a.href=ref.url;a.target='_blank';a.rel='noopener noreferrer';item.append(a,el('p','',ref.scope));list.append(item);}
    return list;
  }
  function renderNote(id,number){
    const n=notes.get(id),r=n?.revisions.find(r=>r.revision===number);
    library.hidden=true;reader.hidden=false;reader.replaceChildren();
    const back=el('a','back-link','← All notes');back.href='notes.html';reader.append(back);
    if(!r){reader.append(el('h1','','Note not found'),el('p','intro','That note revision is not in this local collection.'));return;}
    const m=r.extensions.research_note,article=el('article','full-note');
    article.append(el('p','eyebrow',roleName[m.role]||m.role));
    const title=el('h1','',r.title);title.tabIndex=-1;article.append(title);
    article.append(el('p','note-byline',`${originName[m.origin]||m.origin} · ${r.kind} · Revision ${number}`),chips(r.tags));
    const claim=el('section','note-claim');claim.append(el('h2','','Claim'),el('p','',r.claim));article.append(claim);
    const conditions=el('section','note-conditions');conditions.append(el('h2','','Conditions & scope'),el('p','',r.conditions));article.append(conditions,body(r.body));
    const refs=el('section','note-reference-section');refs.append(el('h2','','Citations'));
    refs.append(r.references.length ? sourceList(r.references) : el('p','','No citations recorded for this revision.'));article.append(refs);
    const links=el('section','note-context');
    if(m.scheme_ids.length){
      links.append(el('h2','','In the map'));
      for(const sid of m.scheme_ids){const a=el('a','context-link',schemes.get(sid).name+' →');a.href=`index.html#scheme/${sid}`;links.append(a);}
    }
    const related=workspace.notes.filter(other=>other.id!==id&&latest(other).extensions.research_note.scheme_ids.some(s=>m.scheme_ids.includes(s)));
    if(related.length){links.append(el('h2','','Related notes'));for(const other of related){const rev=latest(other),a=el('a','context-link',rev.title+' →');a.href=noteHref(other.id,rev.revision);links.append(a);}}
    article.append(links);
    const provenance=el('details','note-provenance');provenance.append(el('summary','','Provenance & revision'));
    provenance.append(el('p','',m.prepared_by),el('p','',m.evidence_scope),el('p','','Local draft; no server account, sharing approval or mathematical review is asserted.'));
    provenance.append(el('p','',`Note: ${id}`),el('p','note-hash',`SHA-256: ${workspace.revision_hashes[id][number]}`));article.append(provenance);
    reader.append(article);document.title=r.title+' · Crucible';title.focus({preventScroll:true});window.scrollTo(0,0);
  }
  function readRoute(){
    const path=location.hash.slice(1).split('/');
    if(path[0]==='note'){let id='';try{id=decodeURIComponent(path[1]||'');}catch{}renderNote(id,Number(path[2]));return;}
    if(path[0]==='tag'){try{state.tag=decodeURIComponent(path.slice(1).join('/'));}catch{state.tag='';}state.query='';state.role='';}
    renderList();
  }
  function changed(){state.query=search.value;state.role=role.value;state.tag=tagSelect.value;history.replaceState(null,'','#');renderList();}
  search.addEventListener('input',changed);role.addEventListener('change',changed);tagSelect.addEventListener('change',changed);
  clear.addEventListener('click',()=>{state.query='';state.role='';state.tag='';history.replaceState(null,'','#');renderList();});
  window.addEventListener('hashchange',readRoute);readRoute();
})();

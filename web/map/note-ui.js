/* Shared, inert presentation helpers. Tags never grant authority. */
(() => {
  'use strict';
  const sets = {
    origin: new Set(['literature','crucible-research']),
    purpose: new Set(['scheme-description','comparison','attack-analysis','research-lead']),
    improvement: new Set(['improvement','optimization','parameter-selection','implementation','correctness']),
    object: new Set(['kem','pke','key-agreement']),
    procedure: new Set(['procedure','curation','scoring','continuity'])
  };
  const label = text => ({literature:'Literature','scheme-description':'Scheme',comparison:'Comparison',procedure:'Procedure',kem:'KEM',pke:'PKE',lpn:'LPN',rsa:'RSA','module-lwe':'Module LWE','plain-lwe':'Plain LWE','module-lwr':'Module LWR',ntru:'NTRU',minrank:'MinRank'})[text] || text.replaceAll('-',' ');
  const el = (tag,cls,text) => {const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const tagGroup = value => Object.keys(sets).find(group=>sets[group].has(value)) || 'subject';
  function tag(value) {
    const a=el('a',`note-tag tag-${tagGroup(value)}`,label(value));
    a.href=`notes.html#tag/${encodeURIComponent(value)}`;
    a.title=`Show notes tagged ${label(value)}`;return a;
  }
  const noteHref = (id,revision) => `notes.html#note/${encodeURIComponent(id)}/${revision}`;
  function body(text) {
    const root=el('div','note-body');let list=null,code=null;
    for(const line of text.split('\n')) {
      if(line.startsWith('```')) {if(code)code=null;else{code=el('pre');root.append(code);}list=null;continue;}
      if(code){code.textContent+=line+'\n';continue;}
      if(!line.trim())continue;
      if(line.startsWith('- ')){if(!list){list=el('ul');root.append(list);}list.append(el('li','',line.slice(2)));continue;}
      list=null;
      if(/^#{1,3} /.test(line))root.append(el('h2','',line.replace(/^#{1,3} /,'')));
      else root.append(el('p','',line));
    }
    return root;
  }
  window.NoteUI={el,tag,label,noteHref,body};
})();

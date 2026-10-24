/* Alternate views share the same KEM projection and selection callbacks. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.LandscapeViews=api;})(typeof window!=='undefined'?window:null,function(){
  'use strict';
  const NS='http://www.w3.org/2000/svg';
  const descriptions={cards:'Original · spacious family cards with descriptions a click away.',table:'Compact table · fastest to scan, with every KEM in one list.',branches:'Branch map · shows the browsing hierarchy from areas to families to schemes.',network:'Relationship network · emphasizes documented connections, with schemes grouped by area.'};
  function svgEl(tag,attributes={},text){const node=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attributes))node.setAttribute(k,String(v));if(text!==undefined)node.textContent=text;return node;}
  function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
  function grouped(entries,domains,families){const fm=new Map(families.map(f=>[f.id,f]));return domains.map(d=>({...d,entries:entries.filter(s=>fm.get(s.family).domain===d.id)})).filter(d=>d.entries.length);}
  function treeGeometry(entries,domains,families,width=1120){
    const groups=grouped(entries,domains,families),compact=width<850;
    const nodes=[],labels=[],edges=[];let y=compact?30:42;
    if(compact){
      for(const group of groups){labels.push({kind:'domain',text:group.name,x:20,y:y+4});y+=26;const start=y;
        for(const scheme of group.entries){nodes.push({id:scheme.id,x:37,y,w:width-57,h:46,subtitle:families.find(f=>f.id===scheme.family).name});edges.push({x1:22,y1:start+23,x2:37,y2:y+23});y+=54;}
        y+=18;
      }
      return {width,height:y,nodes,labels,edges,compact};
    }
    for(const group of groups){const groupStart=y;const familyCenters=[];
      for(const family of families.filter(f=>f.domain===group.id)){
        const children=group.entries.filter(s=>s.family===family.id);if(!children.length)continue;const start=y;
        for(const scheme of children){nodes.push({id:scheme.id,x:747,y,w:350,h:30});y+=36;}
        const center=(start+y-6)/2;
        labels.push({kind:'family',text:family.name,x:410,y:center});familyCenters.push(center);
        for(const node of nodes.filter(n=>children.some(s=>s.id===n.id)))edges.push({x1:675,y1:center,x2:747,y2:node.y+15});
      }
      const center=(groupStart+y-6)/2;
      labels.push({kind:'domain',text:group.name,x:155,y:center});
      familyCenters.forEach(fy=>edges.push({x1:335,y1:center,x2:395,y2:fy}));
      edges.push({x1:100,y1:0,x2:140,y2:center,root:true});y+=18;
    }
    const height=Math.max(240,y+10);edges.filter(e=>e.root).forEach(e=>e.y1=height/2);
    labels.push({kind:'root',text:'KEMs',x:40,y:height/2});
    return {width:1120,height,nodes,labels,edges,compact};
  }
  function networkGeometry(entries,domains,families,width=1120){
    const groups=grouped(entries,domains,families);const compact=width<900,nodes=[],regions=[];
    if(compact){let top=14;for(const group of groups){const columns=width<420?1:2,rows=Math.ceil(group.entries.length/columns),h=50+rows*68;
      regions.push({id:group.id,name:group.name,x:10,y:top,w:width-20,h});
      group.entries.forEach((s,i)=>{const col=i%columns,row=Math.floor(i/columns),w=(width-56-(columns-1)*16)/columns;nodes.push({id:s.id,domain:group.id,x:28+col*(w+16),y:top+42+row*68,w,h:50});});top+=h+14;}
      return {width,height:top,nodes,regions,compact};
    }
    const templates={
      lattices:{x:14,y:14,w:690,h:356,positions:[[350,80],[120,162],[580,162],[350,310],[120,270],[580,270]],nodeWidth:206},
      codes:{x:724,y:14,w:382,h:516,positions:[[915,95],[915,225],[915,350],[915,475]],nodeWidth:282},
      'number-theory':{x:14,y:388,w:690,h:142,positions:[[127,468],[355,468],[583,468]],nodeWidth:206}
    };
    for(const group of groups){const t=templates[group.id];regions.push({id:group.id,name:group.name,x:t.x,y:t.y,w:t.w,h:t.h});group.entries.forEach((s,i)=>{const [x,y]=t.positions[i];nodes.push({id:s.id,domain:group.id,x:x-t.nodeWidth/2,y:y-25,w:t.nodeWidth,h:50});});}
    return {width:1120,height:546,nodes,regions,compact};
  }
  function wrap(text,limit){const lines=[];let line='';for(const word of text.split(' ')){if(line&&(line+' '+word).length>limit){lines.push(line);line=word;}else line+=(line?' ':'')+word;}if(line)lines.push(line);return lines;}
  function schemeNode(geo,scheme,options,network){
    const isScored=options.scored.has(scheme.id),isHighlighted=options.highlights.has(scheme.id);
    const g=svgEl('g',{'class':`map-point${isHighlighted?' has-highlight':''}`,role:'button',tabindex:'0','data-scheme':scheme.id,'aria-label':`${scheme.name}${isScored?', scored':''}${isHighlighted?', highlighted':''}. View details.`,'aria-pressed':String(options.selected===scheme.id)});
    g.append(svgEl('title',{},scheme.name),svgEl('rect',{x:geo.x,y:geo.y,width:geo.w,height:geo.h,rx:7,'class':'point-box'}));
    const badgeWidth=isScored&&!network?58:0;const limit=Math.max(12,Math.floor((geo.w-26-badgeWidth)/7));
    const lines=wrap(scheme.name,limit);const text=svgEl('text',{x:geo.x+12,y:geo.y+geo.h/2-(lines.length-1)*7+(geo.subtitle?-5:4),'class':'point-label'});
    lines.forEach((line,i)=>text.append(svgEl('tspan',{x:geo.x+12,dy:i?14:0},line)));g.append(text);
    if(geo.subtitle)g.append(svgEl('text',{x:geo.x+12,y:geo.y+geo.h-7,'class':'point-subtitle'},geo.subtitle));
    if(isScored){const bx=geo.x+geo.w-56,by=network?geo.y-7:geo.y+geo.h/2-8;g.append(svgEl('rect',{x:bx,y:by,width:50,height:16,rx:5,'class':'scored-pill'}),svgEl('text',{x:bx+25,y:by+11,'text-anchor':'middle','class':'scored-text'},'Scored'));}
    if(isHighlighted)g.append(svgEl('circle',{cx:geo.x+geo.w-5,cy:geo.y+geo.h-5,r:3,'class':'highlight-mark'}));
    const open=()=>options.select(scheme.id,true);g.addEventListener('click',open);g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});return g;
  }
  function table(container,entries,options){
    const fm=new Map(options.families.map(f=>[f.id,f])),dm=new Map(options.domains.map(d=>[d.id,d]));
    const panel=el('div','compact-panel'),table=el('table','scheme-table'),head=el('thead'),row=el('tr');
    for(const text of ['KEM','Family','Collection']){const cell=el('th','',text);cell.scope='col';row.append(cell);}head.append(row);table.append(head);
    const body=el('tbody');for(const s of entries){const tr=el('tr');const name=el('td'),button=el('button','table-scheme',s.name);button.type='button';button.dataset.scheme=s.id;button.setAttribute('aria-pressed',String(options.selected===s.id));button.addEventListener('click',()=>options.select(s.id,true));name.append(button);
      const family=fm.get(s.family),info=el('td','table-family');info.append(el('span','',family.name),el('small','',dm.get(family.domain).name));
      const state=el('td','table-status');if(options.scored.has(s.id))state.append(el('span','scored-badge','Scored'));if(options.highlights.has(s.id))state.append(el('span','highlight-badge','Highlighted'));if(!state.childNodes.length)state.append(el('span','unscored-label','Mapped'));
      tr.append(name,info,state);body.append(tr);
    }table.append(body);panel.append(table);container.append(panel);
  }
  function branches(container,entries,options){
    const geo=treeGeometry(entries,options.domains,options.families,container.clientWidth||1120),svg=svgEl('svg',{viewBox:`0 0 ${geo.width} ${geo.height}`,'class':'branch-svg',role:'group','aria-label':'KEM areas, families and schemes'});
    svg.append(svgEl('title',{},'KEM branch map. Grouping does not establish independent security.'));
    for(const edge of geo.edges){const mid=(edge.x1+edge.x2)/2;svg.append(svgEl('path',{d:`M ${edge.x1} ${edge.y1} C ${mid} ${edge.y1}, ${mid} ${edge.y2}, ${edge.x2} ${edge.y2}`,'class':'tree-edge'}));}
    for(const item of geo.labels){const text=svgEl('text',{x:item.x,y:item.y,'class':`tree-${item.kind}`});const lines=wrap(item.text,item.kind==='domain'?23:40);lines.forEach((line,i)=>text.append(svgEl('tspan',{x:item.x,dy:i?15:0},line)));svg.append(text);}
    const sm=new Map(entries.map(s=>[s.id,s]));for(const n of geo.nodes)svg.append(schemeNode(n,sm.get(n.id),options,false));container.append(svg);
  }
  function network(container,entries,options){
    const geo=networkGeometry(entries,options.domains,options.families,options.width||container.clientWidth||1120),svg=svgEl('svg',{viewBox:`0 0 ${geo.width} ${geo.height}`,'class':'network-svg',role:'group','aria-label':'KEM relationship network'});
    svg.append(svgEl('title',{},'Documented relationships between KEMs. Position and distance are for layout only.'));
    for(const region of geo.regions){svg.append(svgEl('rect',{x:region.x,y:region.y,width:region.w,height:region.h,rx:13,'class':`network-region area-${region.id}`}));svg.append(svgEl('text',{x:region.x+18,y:region.y+25,'class':'network-heading'},region.name));}
    const nodes=new Map(geo.nodes.map(n=>[n.id,n])),edgeElements=[];
    for(const relation of options.relationships){const a=nodes.get(relation.from),b=nodes.get(relation.to);if(!a||!b)continue;
      const x1=a.x+a.w/2,y1=a.y+a.h/2,x2=b.x+b.w/2,y2=b.y+b.h/2;
      let cx=(x1+x2)/2,cy=(y1+y2)/2;
      if(!geo.compact&&Math.abs(x1-x2)<2)cx+=Math.abs(y1-y2)>180?315:115;
      else if(!geo.compact&&Math.abs(y1-y2)<2)cy-=45;
      const path=svgEl('path',{d:`M ${x1} ${y1} Q ${cx} ${cy}, ${x2} ${y2}`,'class':'relationship-edge'});path.append(svgEl('title',{},relation.label));svg.append(path);edgeElements.push({path,relation});
    }
    const emphasize=id=>edgeElements.forEach(({path,relation})=>{const active=relation.from===id||relation.to===id;path.classList.toggle('edge-active',Boolean(id)&&active);path.classList.toggle('edge-muted',Boolean(id)&&!active);});
    const sm=new Map(entries.map(s=>[s.id,s]));for(const n of geo.nodes){const g=schemeNode(n,sm.get(n.id),options,true);g.addEventListener('pointerenter',()=>emphasize(n.id));g.addEventListener('pointerleave',()=>emphasize(null));g.addEventListener('focus',()=>emphasize(n.id));g.addEventListener('blur',()=>emphasize(null));svg.append(g);}container.append(svg);
    container.append(el('p','network-caption','Lines show documented comparisons or relationships. Distance does not measure security independence.'));
  }
  function render(view,container,entries,options){({table,branches,network})[view](container,entries,options);}
  return {descriptions,render,treeGeometry,networkGeometry};
});

/* Local design prototype. Routing, access and session lifecycle belong to the
   notebook. Examples and local edits are in memory; this view makes no API writes. */
(() => {
  'use strict';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const button = (text, fn, cls = 'nb-button') => { const n = el('button', cls, text); n.type = 'button'; n.addEventListener('click', fn); return n; };
  const control = (n, key) => { n.dataset.rgControl = key; return n; };
  function field(label, name, value, options = {}) {
    const wrap = el('label', 'nb-field'), input = el(options.options ? 'select' : options.rows ? 'textarea' : 'input');
    wrap.append(el('span', '', label)); input.name = name;
    if (options.options) for (const [key, text] of options.options) { const o = el('option', '', text); o.value = key; input.append(o); }
    if (options.rows) input.rows = options.rows;
    if (options.max) input.maxLength = options.max;
    if (options.required) input.required = true;
    if (options.placeholder) input.placeholder = options.placeholder;
    input.value = value || ''; control(input, name); wrap.append(input);
    if (options.help) wrap.append(el('small', '', options.help));
    return {wrap, input};
  }
  function arrowIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    for (const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.5','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'})) svg.setAttribute(key,value);
    const path = document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d','M5 12h14m-6-6 6 6-6 6'); svg.append(path);
    return svg;
  }
  function mount(host, {navigate, settleScroll = () => {}, current = () => true}) {
    const {questions, notes, contributions, directions, openness} = structuredClone(window.CrucibleResearchGuideData);
    const pageSize = 6, levelKeys = Object.keys(openness), disclosures = new Set(), responseDrafts = new Map(), reviewDrafts = new Map();
    const sortOptions={concrete:'More concrete first',open:'More open-ended first','points-high':'Highest points first','points-low':'Lowest points first'};
    const initialDraft = () => ({source:'your-question', title:'', progress:'', verification:'', background:'Probability · Security definitions', openness:'approach'});
    let draft = initialDraft(), draftDirty = false, draftPhase = 'edit', sequence = 0, nextFocus = null, active = true, renderedKey = '', previousQuestion = '';
    const canAct = () => active && current();
    let state = {sort:'concrete', pool:'open', page:1, question:'', detail:'brief', compose:false};
    const root = el('div','rg-root');
    const heading = el('div','rg-heading'), headingLeft = el('div','rg-heading-copy');
    const collectionBack = control(button('← Research bounties', () => change({pool:'open', question:'', page:1}, 'collection-title'),'nb-link rg-collection-back'),'collection-back');
    const titleRow = el('div','rg-title-row'), title = control(el('h2','','Research bounties'),'collection-title'); title.tabIndex = -1;
    const draftBadge = el('span','nb-visibility','Example data'); draftBadge.title = 'Local design draft. Tasks, ratings, points and checks are illustrative; changes reset on reload.';
    titleRow.append(title,draftBadge); headingLeft.append(collectionBack,titleRow);
    const post = control(button('Suggest a bounty', () => change({compose:true}, 'post-title'),'nb-button'),'post');
    heading.append(headingLeft,post);
    const notice = el('p','nb-notice rg-notice'); notice.hidden = true; notice.setAttribute('role','status');
    const board = el('div','rg-board');
    const listHeading=el('div','rg-list-heading'),count=el('span','rg-count');count.setAttribute('role','status');
    const sort=field('Sort by','sort-order','concrete',{options:Object.entries(sortOptions)});sort.wrap.classList.add('rg-sort');
    sort.input.addEventListener('change',()=>change({sort:sort.input.value,page:1,question:''},'sort-order'));listHeading.append(count,sort.wrap);
    const entries=el('div','rg-grid'); entries.setAttribute('aria-label','Available bounties');
    const pagination=el('nav','rg-pagination'); pagination.setAttribute('aria-label','Bounty pages');
    const secondary=el('nav','rg-secondary'); secondary.setAttribute('aria-label','Your bounties and history');
    const mine=control(button('',()=>change({pool:'mine',question:'',page:1},'collection-title'),'rg-secondary-link'),'pool-mine');
    const past=control(button('Past bounties',()=>change({pool:'past',question:'',page:1},'collection-title'),'rg-secondary-link'),'pool-past');
    secondary.append(mine,past);
    const about=el('details','rg-about'); about.append(el('summary','','About these tasks'));
    about.append(el('p','','A checked brief has usable starting material, a clear scope and an expected contribution. It does not establish the scientific claim.'));
    about.append(el('p','','The scale runs from 1 (open-ended) to 5 (concrete). The middle positions are unnamed. It describes how much of the task is specified, independently of difficulty or reward.'),el('p','','Tasks, ratings, point values and verification records are examples. Points illustrate the proposed reward; the amounts are not a settled policy. Completion would require both poster and pipeline approval. Local changes reset on reload.'));
    board.append(listHeading,entries,pagination,secondary,about);
    const reader=el('section','rg-reader'); reader.hidden=true; reader.setAttribute('aria-label','Selected bounty');
    const back=control(button('← Research bounties',()=>change({question:'',detail:'brief'},'question-'+state.question),'nb-link rg-reader-back'),'reader-back');
    const detail=el('article','rg-detail'), detailHead=el('div','rg-detail-heading'), metadata=el('p','rg-meta'), questionTitle=control(el('h2'),'selected-title'); questionTitle.tabIndex=-1;
    const byline=el('p','rg-byline'); detailHead.append(metadata,questionTitle,byline);
    const tabs=el('div','rg-detail-tabs'); tabs.setAttribute('role','group'); tabs.setAttribute('aria-label','Bounty information');
    for(const [key,label] of [['brief','The task'],['notes','Starting notes'],['progress','Contributions']]){
      const b=control(button(label,()=>change({detail:key},'detail-'+key),'nb-link'),'detail-'+key); b.dataset.detail=key;tabs.append(b);
    }
    const body=el('div','rg-detail-body'); detail.append(detailHead,tabs,body);reader.append(back,detail);
    const composer=el('section','rg-composer');composer.hidden=true;composer.setAttribute('aria-label','Suggest a bounty');
    root.append(heading,notice,board,reader,composer);host.replaceChildren(root);

    const questionById=id=>Object.hasOwn(questions,id)?questions[id]:null;
    const own=q=>q.owner==='You';
    const pending=id=>contributions.filter(c=>c.question===id&&c.kind==='resolution'&&c.status==='pending');
    const poolMatches=(id,q,pool)=>pool==='mine'?own(q)&&q.status!=='closed':pool==='past'?q.status==='closed':q.taskChecked&&q.status!=='closed'&&q.status!=='draft'&&!(own(q)&&pending(id).length);
    function rowsFor(pool=state.pool){
      const rows=Object.entries(questions).filter(([id,q])=>poolMatches(id,q,pool));
      if(pool==='open')rows.sort(([,a],[,b])=>{
        const byPoints=state.sort.startsWith('points-'),left=byPoints?pointValue(a):scopeValue(a.openness),right=byPoints?pointValue(b):scopeValue(b.openness);
        if(left==null)return right==null?0:1;if(right==null)return -1;
        return (left-right)*(['open','points-low'].includes(state.sort)?1:-1);
      });return rows;
    }
    function route(next=state){
      const p=new URLSearchParams({view:'guide'});
      if(next.sort!=='concrete')p.set('sort',next.sort);
      for(const [key,value,fallback] of [['status',next.pool,'open'],['question',next.question,''],['detail',next.detail,'brief']])if(value!==fallback)p.set(key,value);
      if(next.page>1)p.set('page',String(next.page));if(next.compose)p.set('compose','bounty');return '#notebook?'+p;
    }
    function change(patch,focus){if(!canAct())return;nextFocus=focus;state={...state,...patch};navigate(route());}
    function announce(text){notice.textContent=text;notice.hidden=!text;}
    function section(label,text,cls=''){const n=el('section','rg-section '+cls);n.append(el('h3','',label));if(text)n.append(el('p','',text));return n;}
    function scopeValue(key){const position=openness[key]?.position;return position==null?null:levelKeys.length+1-position;}
    function pointValue(q){return Number.isFinite(q.points)&&q.points>=0?q.points:null;}
    function pointsLabel(q={}){
      const value=pointValue(q),n=el('span','rg-points',value==null?'Points not set':value.toLocaleString('en-US')+' points');
      n.title=value==null?'The reward for this proposal has not been set.':'Example reward amount. Completion would require poster and pipeline approval.';return n;
    }
    function scopeRating(key,reason){
      const value=scopeValue(key),n=el('span','rg-scope-rating'),ends=el('span','rg-rating-ends'),steps=el('span','rg-rating-steps');
      ends.append(el('span','','Open-ended'),el('span','','Concrete'));ends.setAttribute('aria-hidden','true');steps.setAttribute('aria-hidden','true');
      for(let index=1;index<=levelKeys.length;index++)steps.append(el('span',index===value?'rg-rating-current':'',String(index)));
      n.append(ends,steps);if(value==null)n.append(el('span','rg-rating-unset','Not yet rated'));
      n.title='1 = open-ended; '+levelKeys.length+' = concrete.'+(reason?' '+reason:'');n.setAttribute('role','img');
      n.setAttribute('aria-label',value==null?'Task scope not yet rated':'Task scope '+value+' out of '+levelKeys.length+': 1 is open-ended, '+levelKeys.length+' is concrete');if(value!=null)n.dataset.rating=String(value);return n;
    }
    function verificationSection(plan){
      const n=section('How it’s checked','','rg-verification');
      n.append(el('p','rg-verification-label','Proposed verification plan · local example'));
      if(plan){
        if(plan.method)n.append(el('p','rg-verification-method',plan.method));
        const checks=el('ul');plan.checks.forEach(text=>checks.append(el('li','',text)));n.append(checks);
        if(plan.accepts){const result=el('p','rg-verification-result');result.append(el('strong','','Completion: '),document.createTextNode(plan.accepts));n.append(result);}
      }else n.append(el('p','','A verification plan is required before this task opens.'));
      return n;
    }
    function notePreview(id,initiallyOpen=false){
      const note=notes[id],n=el('details','nb-details nb-card rg-note');n.open=disclosures.has(id)||(initiallyOpen&&!disclosures.has(id+':closed'));
      n.append(el('summary','',note.title),el('p','rg-note-meta','Example note · '+note.author+' · revision '+note.revision),el('p','',note.summary),el('p','',note.body));
      n.addEventListener('toggle',()=>{if(!n.isConnected)return;if(n.open){disclosures.add(id);disclosures.delete(id+':closed');}else{disclosures.delete(id);disclosures.add(id+':closed');}});return n;
    }
    function renderBrief(q){
      const facts=el('div','rg-task-facts');facts.append(scopeRating(q.openness,q.approachReason),pointsLabel(q),el('span','',q.background));
      body.append(facts,el('p','rg-context',q.context));
      const outcomes=section('What to contribute'),items=el('ul');q.progress.forEach(text=>items.append(el('li','',text)));outcomes.append(items);
      body.append(outcomes,verificationSection(q.verification),section('Start here',q.step,'nb-request-guidance rg-first-step'),section('Why it matters',q.next));
      const checks=el('details','nb-details rg-checks');checks.append(el('summary','',q.taskChecked?'Brief checked · example':'Brief awaiting a check'),el('p','',q.taskChecked?q.checkSummary:'Check the source revisions, scope and completion criteria before opening this task.'),el('p','','A claimed solution needs poster and pipeline approval. The reward policy is still being designed.'));body.append(checks);
      if(q.status==='closed')body.append(section('Outcome',q.outcome,'rg-outcome'));
      const links=el('div','rg-actions');links.append(button('Read starting notes',()=>change({detail:'notes'},'detail-notes'),'nb-button nb-primary'));
      if(q.taskChecked&&q.status!=='closed')links.append(button('Link a contribution',()=>change({detail:'contribute'},'contribution-note'),'nb-link'));
      body.append(links);
    }
    function renderNotes(q){q.notes.forEach((id,i)=>body.append(notePreview(id,i===0)));const links=el('div','rg-actions');if(q.taskChecked&&q.status!=='closed')links.append(button('Link a contribution',()=>change({detail:'contribute'},'contribution-note'),'nb-button nb-primary'));body.append(links);}
    function reviewForm(item,q,hostNode){
      const form=el('form','nb-form rg-review-form'),f=field('Why does this satisfy the task?','review-reason',reviewDrafts.get(item.id)||'',{required:true,rows:3,max:900});
      f.input.addEventListener('input',()=>{reviewDrafts.set(item.id,f.input.value);f.input.setCustomValidity('');});
      const submit=el('button','nb-button nb-primary','Approve completion in draft');submit.type='submit';
      form.append(f.wrap,el('p','nb-hint','Local example only. This records your decision; it cannot override pipeline approval.'),submit);
      form.addEventListener('submit',event=>{
        event.preventDefault();if(!canAct()||!own(q)||item.author==='You'||item.status!=='pending')return;
        if(!f.input.value.trim()){f.input.setCustomValidity('Explain the decision.');f.input.reportValidity();return;}
        item.approvals.poster='approved';item.assessment=f.input.value.trim();item.reviewer='You';reviewDrafts.delete(item.id);
        if(item.approvals.pipeline==='approved'){item.status='confirmed';q.status='closed';q.outcome=item.assessment;q.updated='Just now';}
        announce(item.status==='confirmed'?'Both approvals recorded in this draft. No real points or credits changed.':'Poster approval recorded. Pipeline approval is still pending.');
        change({pool:q.status==='closed'?'past':'mine',detail:'progress'},'detail-progress');
      });hostNode.replaceChildren(form);f.input.focus();
    }
    function renderProgress(q){
      const rows=contributions.filter(c=>c.question===state.question);
      if(q.status==='closed')body.append(section('What changed',q.outcome,'rg-outcome'));
      if(!rows.length)body.append(el('p','rg-empty-progress','No contributions yet. Start with the notes and link what you find.'));
      for(const item of rows){
        const card=el('article','rg-progress-item');
        card.append(el('p','rg-meta',item.kind==='resolution'?'Claimed solution':'Partial progress'),el('h3','',notes[item.note].title),el('p','rg-byline',item.author),el('p','rg-progress-summary',item.summary),notePreview(item.note));
        if(item.kind==='resolution'){
          const approvals=el('div','rg-approvals');
          for(const [key,label] of [['poster','Poster'],['pipeline','Pipeline']]){const approved=item.approvals?.[key]==='approved';const n=el('span',approved?'rg-approved':'',label+' · '+(approved?'approved':'pending'));approvals.append(n);}card.append(approvals);
          if(item.pipelineReason){const check=el('details','nb-details nb-card rg-note');check.append(el('summary','','Pipeline assessment · example'),el('p','',item.pipelineReason));card.append(check);}
          if(item.status==='confirmed')card.append(section('Decision',item.assessment,'rg-assessment'));
          else if(own(q)&&item.author!=='You'&&item.approvals?.poster!=='approved'){
            const action=el('div','rg-review-action');action.append(button('Review completion',()=>reviewForm(item,q,action),'nb-button'));card.append(action);
          }
        }else if(item.assessment)card.append(section('What this helped establish',item.assessment,'rg-assessment'));
        body.append(card);
      }
      if(q.taskChecked&&q.status!=='closed')body.append(button('Link a contribution',()=>change({detail:'contribute'},'contribution-note'),'nb-button'));
    }
    function renderContribution(q){
      const id=state.question;if(!responseDrafts.has(id))responseDrafts.set(id,{note:'your-comparison',kind:'partial',summary:''});
      const saved=responseDrafts.get(id),form=el('form','nb-form');
      body.append(el('h3','rg-form-title','Link your work'));
      const note=field('Contribution note','contribution-note',saved.note,{options:[['your-comparison','Comparison of two definitions · revision 2'],['your-run','Independent experiment log · revision 1']]}),kind=field('What does it offer?','contribution-kind',saved.kind,{options:[['partial','Useful partial progress'],['resolution','A solution to the stated task']]}),summary=field('What does it establish?','contribution-summary',saved.summary,{required:true,rows:3,max:700,placeholder:'State the result and its limits.'});
      const evidence=el('div');evidence.append(notePreview(saved.note));const error=el('p','nb-error');error.setAttribute('role','alert');
      for(const [key,f]of Object.entries({note,kind,summary}))f.input.addEventListener('input',()=>{saved[key]=f.input.value;f.input.setCustomValidity('');});
      note.input.addEventListener('change',()=>{saved.note=note.input.value;evidence.replaceChildren(notePreview(saved.note));});
      const submit=el('button','nb-button nb-primary','Add to draft');submit.type='submit';
      const footer=el('div','nb-form-actions');footer.append(button('Back to contributions',()=>change({detail:'progress'},'detail-progress')),submit);
      form.append(note.wrap,evidence,kind.wrap,summary.wrap,el('p','nb-hint','Example notes only. A claimed solution requires both poster and pipeline approval.'),error,footer);
      form.addEventListener('submit',event=>{
        event.preventDefault();if(!canAct())return;
        if(!summary.input.value.trim()){summary.input.setCustomValidity('Explain what the note established.');summary.input.reportValidity();return;}
        if(contributions.some(c=>c.question===id&&c.note===saved.note)){error.textContent='This example revision is already linked. Choose another note.';return;}
        contributions.push({id:'draft-progress-'+(++sequence),question:id,note:saved.note,kind:saved.kind,summary:saved.summary.trim(),author:'You',status:'pending',approvals:{poster:'pending',pipeline:'pending'}});
        responseDrafts.delete(id);announce('Contribution linked in this draft. No approval or points awarded.');change({detail:'progress'},'detail-progress');
      });body.append(form);
    }
    function renderEntries(rows){
      entries.replaceChildren();pagination.replaceChildren();const open=state.pool==='open';entries.classList.toggle('rg-grid',open);entries.classList.toggle('rg-management-list',!open);
      const pages=Math.max(1,Math.ceil(rows.length/pageSize));state.page=Math.min(state.page,pages);
      count.textContent=rows.length+(open?' open '+(rows.length===1?'bounty':'bounties'):' '+(rows.length===1?'bounty':'bounties'));
      if(!rows.length)entries.append(el('p','rg-no-results',open?'No open bounties yet.':state.pool==='mine'?'Your proposals and tasks awaiting your review will appear here.':'No past bounties in this draft.'));
      for(const [key,q]of rows.slice((state.page-1)*pageSize,state.page*pageSize)){
        const row=control(button('',()=>change({question:key,detail:state.pool==='mine'&&pending(key).length?'progress':'brief'},'selected-title'),open?'nb-card rg-question':'rg-management-row'),'question-'+key);row.dataset.question=key;
        if(open){
          const heading=el('span','nb-title-copy rg-card-heading'),labels=el('span','nb-note-status');
          labels.append(el('span','nb-visibility','Bounty'));heading.append(el('span','rg-question-title',q.title),labels);
          const skills=el('span','rg-skills');q.background.split(' · ').slice(0,2).forEach(skill=>skills.append(el('span','',skill)));
          const foot=el('span','rg-card-foot');foot.append(scopeRating(q.openness,q.approachReason),pointsLabel(q));
          row.append(heading,el('span','rg-question-summary',q.summary),skills,foot);
        }else{
          const text=el('span','rg-management-copy'),waiting=pending(key);
          const status=q.status==='closed'?'Completed':q.status==='draft'?'Brief awaiting a check':waiting.length?(waiting.some(c=>c.approvals?.poster!=='approved')?'Awaiting your review':'Awaiting pipeline approval'):'Open';
          text.append(el('span','rg-management-status',status),el('span','rg-question-title',q.title));row.append(text,arrowIcon());
        }entries.append(row);
      }
      if(pages>1){const prev=button('Previous',()=>change({page:state.page-1},'collection-title')),next=button('Next',()=>change({page:state.page+1},'collection-title'));prev.disabled=state.page===1;next.disabled=state.page===pages;pagination.append(prev,el('span','',state.page+' / '+pages),next);}
    }
    function renderComposer(){
      composer.replaceChildren();composer.append(button('← Back to bounties',()=>change({compose:false},'post'),'nb-link'));
      const h=control(el('h2','',draftPhase==='preview'?'Preview the task':'Suggest a bounty'),'compose-title');h.tabIndex=-1;composer.append(h);
      const source=notes[draft.source];
      if(draftPhase==='preview'){
        const preview=el('article','rg-post-preview'),facts=el('div','rg-task-facts');facts.append(scopeRating(draft.openness),pointsLabel());preview.append(el('p','rg-meta',directions[source.direction].label),el('h3','',draft.title),facts,section('Expected contribution',draft.progress),verificationSection({checks:[draft.verification.trim()]}),notePreview(draft.source));
        const save=button('Save proposal in draft',()=>{
          if(!canAct())return;const id='draft-bounty-'+(++sequence);
          questions[id]={title:draft.title.trim(),summary:draft.progress.trim(),context:source.summary,progress:[draft.progress.trim()],verification:{checks:[draft.verification.trim()]},step:'Read the starting note and identify one bounded piece to work on.',next:'Use the result to update the source question.',notes:[draft.source],background:draft.background.trim()||'See the starting note',subject:source.title,direction:source.direction,openness:draft.openness,author:'You',owner:'You',sponsor:'You',status:'draft',taskChecked:false,updated:'Just now'};
          draft=initialDraft();draftDirty=false;draftPhase='edit';announce('Proposal saved in this local draft. Its brief needs a check before joining the open board.');change({compose:false,pool:'mine',question:id,detail:'brief',page:1},'selected-title');
        },'nb-button nb-primary');
        const footer=el('div','nb-form-actions');footer.append(button('Edit task',()=>{draftPhase='edit';renderComposer();composer.querySelector('[name=post-title]').focus();}),save);
        composer.append(preview,el('p','nb-hint','The brief will be checked before the task opens. Reward policy is not set in this prototype.'),footer);return;
      }
      const form=el('form','nb-form'),sourceField=field('Starting note','post-source',draft.source,{options:[['your-question','Your note · An alternative sampling rule'],['novelty-note-1','Mara’s note · Proposal outline'],['assumption-note-1','Leo’s note · Proposed assumption'],['reproduce-note-1','Leo’s note · Reported result']],help:'Example notes. You can suggest work arising from someone else’s question.'}),titleField=field('The task','post-title',draft.title,{required:true,max:160,placeholder:'One clear question or piece of work'}),progressField=field('What would count as completion?','post-progress',draft.progress,{required:true,rows:3,max:700,placeholder:'Describe the evidence or result someone should provide.'}),verificationField=field('How will it be verified?','post-verification',draft.verification,{required:true,rows:3,max:1000,placeholder:'State the evidence, the check to run or review, and what passes.'}),shapeField=field('Open-ended → Concrete','post-openness',draft.openness,{options:[...levelKeys].reverse().map(key=>[key,String(scopeValue(key))+' / '+levelKeys.length])}),backgroundField=field('Useful background','post-background',draft.background,{max:180,placeholder:'e.g. Python, probability, group actions'});
      shapeField.wrap.append(el('small','','1 = open-ended; '+levelKeys.length+' = concrete.'));
      const row=el('div','nb-fields-row');row.append(shapeField.wrap,backgroundField.wrap);
      for(const [key,f]of Object.entries({source:sourceField,title:titleField,progress:progressField,verification:verificationField,openness:shapeField,background:backgroundField}))f.input.addEventListener('input',()=>{draft[key]=f.input.value;draftDirty=true;f.input.setCustomValidity('');});
      const submit=el('button','nb-button nb-primary','Preview task');submit.type='submit';const footer=el('div','nb-form-actions');footer.append(el('span','rg-form-retained','Kept while you browse this session'),submit);
      form.append(sourceField.wrap,titleField.wrap,progressField.wrap,verificationField.wrap,row,footer);
      form.addEventListener('submit',event=>{event.preventDefault();if(!canAct())return;for(const f of[titleField,progressField,verificationField])if(!f.input.value.trim()){f.input.setCustomValidity(f===verificationField?'Describe the evidence and completion checks.':'Describe a specific task.');f.input.reportValidity();return;}draftPhase='preview';renderComposer();composer.querySelector('[data-rg-control=compose-title]').focus();});composer.append(form);
    }
    function render(){
      const selected=Boolean(state.question),inReader=selected&&!state.compose;
      heading.hidden=inReader||state.compose;board.hidden=inReader||state.compose;reader.hidden=!inReader;composer.hidden=!state.compose;
      root.classList.toggle('rg-reading',inReader);title.textContent=state.pool==='open'?'Research bounties':state.pool==='mine'?'Your bounties':'Past bounties';
      post.textContent=draftDirty?'Continue proposal':'Suggest a bounty';collectionBack.hidden=state.pool==='open';
      if(state.compose)renderComposer();
      else if(inReader){
        back.textContent='← '+(state.pool==='open'?'Research bounties':state.pool==='mine'?'Your bounties':'Past bounties');
        const q=questionById(state.question);body.replaceChildren();
        if(!q){metadata.textContent='Local draft';questionTitle.textContent='This bounty is no longer in the draft';byline.textContent='Local additions reset on reload.';tabs.hidden=true;}
        else{
          tabs.hidden=false;metadata.textContent=directions[q.direction].label+' · '+(q.status==='closed'?'Completed':q.status==='draft'?'Brief awaiting a check':'Open task');questionTitle.textContent=q.title;byline.textContent='Posted by '+q.owner+' · '+(q.taskChecked?'Brief checked · example':'Not yet checked');
          for(const b of tabs.children){b.setAttribute('aria-pressed',String(b.dataset.detail===state.detail||(b.dataset.detail==='progress'&&state.detail==='contribute')));if(b.dataset.detail==='progress')b.textContent='Contributions ('+contributions.filter(c=>c.question===state.question).length+')';}
          if(state.detail==='notes')renderNotes(q);else if(state.detail==='progress')renderProgress(q);else if(state.detail==='contribute'&&q.taskChecked&&q.status!=='closed')renderContribution(q);else renderBrief(q);
        }
      }else{
        const open=state.pool==='open';sort.wrap.hidden=!open;sort.input.value=state.sort;secondary.hidden=!open;about.hidden=!open;
        renderEntries(rowsFor());const awaiting=Object.entries(questions).filter(([id,q])=>own(q)&&pending(id).some(c=>c.approvals?.poster!=='approved')).length;
        mine.replaceChildren(el('span','','Your bounties'));if(awaiting)mine.append(el('span','rg-review-count',awaiting+' awaiting your review'));mine.append(arrowIcon());
        past.replaceChildren(el('span','','Past bounties'),arrowIcon());
      }
      if(nextFocus){const target=Array.from(root.querySelectorAll('[data-rg-control]')).find(n=>n.dataset.rgControl===nextFocus&&!n.closest('[hidden]'));
        target?.focus({preventScroll:true});
        if(target&&nextFocus==='selected-title'){reader.scrollIntoView({block:'start'});settleScroll();}
        else if(target&&nextFocus.startsWith('question-')){target.scrollIntoView({block:'nearest'});settleScroll();}
        nextFocus=null;}
    }
    function show(params){
      if(!current())return;active=true;let pool=['mine','past'].includes(params.get('status'))?params.get('status'):params.get('status')==='review'?'mine':'open';
      const question=params.get('question')||'';if(!params.has('status')&&questionById(question)?.status==='closed')pool='past';
      const requestedSort=params.get('sort')==='guided'?'concrete':params.get('sort');
      const next={sort:Object.hasOwn(sortOptions,requestedSort)?requestedSort:'concrete',pool,page:Math.max(1,Math.min(1000,parseInt(params.get('page'),10)||1)),question,detail:['notes','progress','contribute'].includes(params.get('detail'))?params.get('detail'):'brief',compose:params.get('compose')==='bounty'};
      const key=JSON.stringify(next);if(key===renderedKey&&!nextFocus)return;
      if(previousQuestion&&!question&&!next.compose&&!nextFocus&&root.contains(document.activeElement))nextFocus='question-'+previousQuestion;
      state=next;render();renderedKey=key;previousQuestion=question;
    }
    const beforeUnload=event=>{if(current()&&(draftDirty||[...responseDrafts.values()].some(d=>d.summary.trim())||[...reviewDrafts.values()].some(s=>s.trim()))){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);
    return{show,route:()=>route(),hide:()=>{active=false;},destroy:()=>{active=false;window.removeEventListener('beforeunload',beforeUnload);disclosures.clear();responseDrafts.clear();reviewDrafts.clear();draft=initialDraft();root.remove();}};
  }
  window.CrucibleResearchGuide={mount};
})();

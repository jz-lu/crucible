/* Optional, note-owned statements and scoped argument updates. No proof inference. */
(() => {
  'use strict';
  const base = '/api/notebook/statement-workflow';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const button = (label, fn, cls = 'nb-link') => { const n = el('button', cls, label); n.type = 'button'; n.addEventListener('click', fn); return n; };
  const prose = text => { const n = el('div', 'nb-readable-prose'); if (window.CrucibleMath?.markdown) window.CrucibleMath.markdown(n, String(text || '')); else n.textContent = text || ''; return n; };
  const validId = id => typeof id === 'string' && /^[a-z][a-z0-9-]{0,79}$/.test(id) && !['prototype','constructor','__proto__'].includes(id);
  const validStatementRef = ref => {
    if (!ref || typeof ref !== 'object' || !validId(ref.statement_id)) return false;
    const external = ['note_id','revision','content_sha256'].filter(key => ref[key] != null);
    return !external.length || external.length === 3 && typeof ref.note_id === 'string' && ref.note_id.trim() &&
      Number.isSafeInteger(ref.revision) && ref.revision > 0 && /^[a-f0-9]{64}$/.test(ref.content_sha256);
  };
  const pin = (note, revision) => ({note_id:note.id,revision:revision.revision,content_sha256:revision.content_sha256});
  const exactRoute = (source, kind, id) => '#notebook?' + new URLSearchParams({note:source.note_id,revision:String(source.revision),sha256:source.content_sha256,...(kind && id ? {[kind]:id} : {})});
  const targetRoute = id => '#notebook?' + new URLSearchParams({statement_target:id});
  const updateRoute = (id, reviewing = false) => (reviewing ? '#reviews?section=proposals&' : '#notebook?') + new URLSearchParams({statement_update:id});
  const link = (label, href, ctx, cls = 'nb-link') => { const n = el('a', cls, label); n.href = href; n.addEventListener('click', e => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return; e.preventDefault(); ctx.navigate(href); }); return n; };
  const uncertain = error => !error.status || error.status >= 500;
  const stateLabel = state => ({pending:'Awaiting review',approved:'Applied',changes_requested:'Changes requested',declined:'Not adopted',decline:'Not adopted'})[state] || state || 'Not assessed';
  function validate(content) {
    for (const key of ['statements','arguments']) {
      if (content[key] === undefined) continue;
      const rows = content[key];
      if (!Array.isArray(rows) || rows.length > 32 || new Set(rows.map(x => x?.id)).size !== rows.length || rows.some(x => !x || !validId(x.id) || typeof x.title !== 'string' || !x.title.trim())) throw Error('This revision has invalid ' + key + '. Its content has not been substituted.');
      if (key === 'statements' && rows.some(x => typeof x.definition !== 'string' || !x.definition.trim() || x.definition.length > 12000 || typeof x.label !== 'string' || !x.label.trim() || x.label.length > 30)) throw Error('Each statement needs a short label and its precise definition (at most 12,000 characters).');
      if (key === 'arguments' && rows.some(x => (x.conclusion_statement != null ? x.conclusion != null || !validStatementRef(x.conclusion_statement) : typeof x.conclusion !== 'string' || !x.conclusion.trim() || x.conclusion.length > 6000) || typeof x.scope !== 'string' || !x.scope.trim() || x.scope.length > 6000 || !['conditional','unconditional','incomplete'].includes(x.status) || !Array.isArray(x.dependencies) || x.dependencies.length > 32 || x.status === 'unconditional' && x.dependencies.length || x.status === 'conditional' && !x.dependencies.length)) throw Error('A saved argument needs one exact conclusion, its scope and a consistent dependency list.');
      if (key === 'arguments' && rows.some(x => x.conclusion_statement && !x.conclusion_statement.note_id && !content.statements?.some(statement => statement.id === x.conclusion_statement.statement_id))) throw Error('The local conclusion statement is missing from this revision.');
      if (key === 'arguments') for (const argument of rows) for (const use of argument.dependencies) {
        if (!use || !validStatementRef(use.statement)) throw Error('Each premise must identify an exact statement.');
        const legacy = ['required','mapping','justification'].filter(key => use[key] != null);
        if (legacy.length && (legacy.length !== 3 || use.use != null) || ['use',...legacy].some(key => use[key] != null && (typeof use[key] !== 'string' || !use[key].trim()))) throw Error('Use one premise explanation, or preserve the complete historical application fields.');
      }
    }
  }
  function statementValue(ref, resolved, source, content) {
    if (!validStatementRef(ref)) return null;
    const target = ref.note_id ? ref : source;
    if (resolved?.id === ref.statement_id && resolved.source?.note_id === target.note_id &&
        resolved.source?.revision === target.revision && resolved.source?.content_sha256 === target.content_sha256) return resolved;
    return !ref.note_id ? content?.statements?.find(item => item.id === ref.statement_id) || null : null;
  }
  function conclusionContent(argument, source, content, ctx) {
    const out = el('section', 'nb-argument-conclusion');
    if (!argument.conclusion_statement) { out.append(prose(argument.conclusion)); return out; }
    const ref = argument.conclusion_statement, statement = statementValue(ref, argument.resolved_conclusion_statement, source, content);
    out.append(el('h4', '', 'Conclusion'));
    const target = statement?.source || (ref.note_id ? ref : source);
    out.append(link(statement ? [statement.label, statement.title].filter(Boolean).join(' · ') : 'Read exact conclusion', exactRoute(target, 'statement', ref.statement_id), ctx, 'nb-conclusion-link'));
    if (statement) out.append(prose(statement.definition));
    else out.append(el('p', 'nb-hint', 'Open the exact source to read this statement.'));
    return out;
  }
  function cite(source, kind, item, ctx) {
    const host = ctx.openDialog('Link to ' + kind), alive = ctx.dialogGuard();
    const href = new URL('/' + exactRoute(source, kind, item.id), window.location.href).href;
    const f = ctx.field('Exact ' + kind + ' link', 'statement-link', href); f.input.readOnly = true;
    const status = el('p', 'nb-hint'); status.setAttribute('role', 'status');
    host.append(el('p', 'nb-hint', item.title), f.wrap, status,
      button('Copy link', async () => { try { await navigator.clipboard.writeText(href); if (alive()) status.textContent = 'Copied.'; } catch (_) { if (alive()) status.textContent = 'Select and copy the link above.'; } }, 'nb-button'));
  }
  function dependency(use, source, content, ctx, {compact = false} = {}) {
    const row = el('li', 'nb-dependency'), ref = use.statement || {}, resolved = use.resolved_statement;
    const local = !ref.note_id && content?.statements?.find(s => s.id === ref.statement_id);
    const statement = resolved || local, target = resolved?.source || (ref.note_id ? ref : source);
    const heading = el('div', 'nb-dependency-heading');
    heading.append(link(statement ? [statement.label,statement.title].filter(Boolean).join(' · ') : 'Read exact premise', exactRoute(target, 'statement', ref.statement_id || statement?.id), ctx));
    row.append(heading);
    if (!compact && statement?.definition) row.append(prose(statement.definition));
    const application = use.use || [use.required, use.mapping, use.justification].filter(Boolean).join('\n\n');
    if (application || use.configuration_ids?.length) {
      const details = el('details', 'nb-use-details'); details.append(el('summary', '', 'How this premise is used'));
      if (application) details.append(prose(application));
      if (use.configuration_ids?.length) details.append(el('p', 'nb-configurations', 'Configurations: ' + use.configuration_ids.join(', ')));
      row.append(details);
    }
    return row;
  }
  function argumentCard(argument, source, content, ctx, {selected = false, propose = true, showTitle = true, compact = false, displayedScope = null} = {}) {
    const block = el('section', 'nb-argument-block' + (selected ? ' nb-structured-selected' : ''));
    block.dataset.noteArgument = argument.id; block.tabIndex = -1;
    const heading = el('div', 'nb-statement-heading'), title = el('h3', '', argument.title);
    const citation = button('↗', () => cite(source, 'argument', argument, ctx), 'nb-link nb-structured-cite'); citation.title = 'Link to this exact argument'; citation.setAttribute('aria-label', citation.title);
    if (showTitle) { heading.append(title, citation); block.append(heading); }
    block.append(conclusionContent(argument, source, content, ctx));
    if (argument.status === 'incomplete') block.append(el('p', 'nb-argument-status', 'Incomplete argument'));
    if (typeof displayedScope !== 'string' || displayedScope.trim() !== argument.scope?.trim()) {
      const scope = el('div', 'nb-argument-scope'); scope.append(el('strong', '', 'Scope'), prose(argument.scope)); block.append(scope);
    }
    const dependencies = el('div', 'nb-argument-dependencies');
    if (argument.dependencies.length) {
      dependencies.append(el('h4', '', argument.dependencies.length === 1 ? 'Assumption' : 'Assumptions'));
      const list = el('ol', 'nb-dependency-list'); argument.dependencies.forEach(use => list.append(dependency(use, source, content, ctx, {compact}))); dependencies.append(list);
    } else dependencies.append(el('p', 'nb-hint', argument.status === 'unconditional' ? 'The author states no unproved premises in this scope. Read the argument and its review.' : 'Dependencies have not been established. An empty list is not a proof.'));
    block.append(dependencies);
    const reasoning = content?.sections?.find(section => section.id === argument.section_id);
    if (selected && reasoning) {
      const explanation = el('section', 'nb-argument-reasoning');
      explanation.append(el('h4', '', 'Reasoning'), prose(reasoning.body)); block.append(explanation);
    }
    const actions = el('div', 'nb-structured-actions');
    if (argument.section_id) actions.append(link(selected && reasoning ? 'Link to reasoning section' : 'Read reasoning', exactRoute(source, 'note_section', argument.section_id), ctx));
    else actions.append(link('Read the full note', exactRoute(source), ctx));
    if (propose && ctx.enabled()) actions.append(button('Propose update', () => proposeDialog(source, argument, ctx), 'nb-button'));
    if (!showTitle) actions.append(citation);
    block.append(actions); return block;
  }
  function renderNote(note, revision, ctx) {
    const content = revision.content, source = pin(note, revision);
    const statements = content.statements || [], arguments_ = content.arguments || [];
    if (!statements.length && !arguments_.length) return null;
    const out = el('div', 'nb-structured-research');
    const selectedArgument = ctx.params.get('argument');
    if (arguments_.length) {
      const section = el('section', 'nb-argument-options');
      for (const argument of arguments_) {
        const selected = selectedArgument === argument.id;
        const card = argumentCard(argument, source, content, ctx, {selected,showTitle:selected});
        if (selected) section.append(card);
        else {
          const entry = el('details', 'nb-argument-entry'); entry.open = false;
          entry.append(el('summary', '', argument.title), card); section.append(entry);
        }
        if (selected && ctx.enabled()) enrichContext(card, source, 'argument', argument.id, content, ctx);
      }
      out.append(section);
    }
    const statementList = el('details', 'nb-statements-list');
    statementList.open = Boolean(ctx.params.get('statement'));
    statementList.append(el('summary', '', 'Statements' + (statements.length ? ' (' + statements.length + ')' : '')));
    for (const item of statements) {
      const selected = ctx.params.get('statement') === item.id;
      const block = el('section', 'nb-statement-block' + (selected ? ' nb-structured-selected' : '')); block.dataset.noteStatement = item.id; block.tabIndex = -1;
      const heading = el('div', 'nb-statement-heading'), title = el('h2', '', item.title);
      if (item.label) title.prepend(el('span', 'nb-assumption-label', item.label), ' ');
      const citation = button('↗', () => cite(source, 'statement', item, ctx), 'nb-link nb-structured-cite'); citation.title = 'Link to this exact statement'; citation.setAttribute('aria-label', citation.title);
      heading.append(title, citation); block.append(heading, prose(item.definition));
      if (item.definitions?.length) { const details = el('details', 'nb-use-details'); details.append(el('summary', '', 'Pinned definitions')); for (const ref of item.definitions) details.append(link('Read source definition', exactRoute(ref, ref.section_id ? 'note_section' : null, ref.section_id), ctx)); block.append(details); }
      statementList.append(block);
      if (selected && ctx.enabled()) enrichContext(block, source, 'statement', item.id, content, ctx);
    }
    if (statements.length) out.append(statementList);
    return out;
  }
  async function enrichContext(block, source, kind, id, content, ctx) {
    const after = el('div', 'nb-statement-context'); block.append(after);
    try {
      const data = await ctx.api(base + '/context?' + new URLSearchParams({note_id:source.note_id,revision:String(source.revision),sha256:source.content_sha256,[kind]:id}));
      if (!ctx.current()) return;
      if (data.argument) {
        const updated = argumentCard(data.argument, source, content, ctx);
        for (const cls of ['.nb-argument-conclusion', '.nb-argument-dependencies']) {
          const old = block.querySelector(cls), replacement = updated.querySelector(cls); old?.replaceChildren(...replacement.childNodes);
        }
      }
      const targets = Array.isArray(data.targets) ? data.targets : [];
      const historicalTarget = target => {
        if (kind === 'argument' && Array.isArray(target.roster)) {
          const uses = target.roster.filter(row => sameArgument(row.argument,{...source,argument_id:id}));
          if (uses.length) return uses.every(row => row.status === 'withdrawn');
        }
        return target.current?.status === 'withdrawn';
      };
      for (const historical of [false,true]) {
        const group = targets.filter(target => historicalTarget(target) === historical);
        if (!group.length) continue;
        const uses = el('div', 'nb-used-here'); uses.append(el('h4', '', historical ? 'Previously used by' : 'Used by'));
        for (const target of group) uses.append(link(target.title || target.target?.title || 'View adopted result', targetRoute(target.id || target.target?.id), ctx)); after.append(uses);
      }
      const related = Array.isArray(data.related_uses) ? data.related_uses : [];
      if (related.length) {
        const details = el('details', 'nb-use-details'); details.append(el('summary', '', 'Other arguments using this statement'));
        for (const use of related) {
          const ref = use.source || use.argument?.source || use.argument;
          if (ref?.note_id) details.append(link(use.title || use.argument?.title || 'Read argument', exactRoute(ref, 'argument', use.argument_id || use.argument?.id), ctx));
        }
        if (details.children.length > 1) after.append(details);
      }
      if (Array.isArray(data.related_arguments) && data.related_arguments.length) {
        const details = el('details', 'nb-use-details'); details.append(el('summary', '', 'Arguments for this statement'));
        for (const argument of data.related_arguments) if (argument.source?.note_id) details.append(link(argument.title || 'Read exact argument', exactRoute(argument.source, 'argument', argument.argument_id), ctx));
        if (details.children.length > 1) after.append(details);
      }
      if (Array.isArray(data.related_research) && data.related_research.length) {
        const research = el('section', 'nb-related-statements'); research.append(el('h4', '', 'Related research'));
        for (const item of data.related_research) {
          const row = el('div', 'nb-statement-evidence');
          row.append(link(item.title, exactRoute(item.source), ctx), el('span', 'nb-hint', (({supports:'Supporting note',challenges:'Challenging note',reproduces:'Reproduction',responds_to:'Response'})[item.relation] || 'Related note') + (item.author?.handle ? ' · ' + item.author.handle : '')));
          if (item.scope) row.append(prose(item.scope)); research.append(row);
        }
        after.append(research);
      }
    } catch (error) { if (ctx.current() && error.status !== 404) after.append(el('p', 'nb-hint', 'Current uses could not be checked.'), button('Retry', () => { after.remove(); enrichContext(block, source, kind, id, content, ctx); })); }
  }
  const sameArgument = (a, b) => Boolean(a && b && ['note_id','revision','content_sha256','argument_id'].every(key => a[key] === b[key]));
  function admittedArguments(target) {
    if (Array.isArray(target.roster)) return target.roster.filter(row => row.status === 'admitted' && row.argument);
    return target.current?.argument && target.current.status !== 'withdrawn' ? [{argument:target.current.argument,resolved_argument:target.current.resolved_argument,is_current:true}] : [];
  }
  function argumentProvenance(record) {
    if (record?.origin === 'initial_admission') return 'Initial admission · not independently reviewed';
    if (record?.independent_review === true) return 'Independently reviewed' + (record.reviewer?.handle ? ' by ' + record.reviewer.handle : '');
    return record?.reviewer?.handle ? 'Review recorded by ' + record.reviewer.handle : 'Admission recorded';
  }
  const operationName = operation => ({admit_argument:'Add an alternative argument',select_argument:'Change the displayed argument',withdraw_argument:'Withdraw an admitted argument'})[operation] || 'Change the displayed argument';
  async function proposeDialog(source, argument, ctx, initial = {}) {
    if (!ctx.current()) return;
    const evidenceSource = {note_id:source.note_id,revision:source.revision,content_sha256:source.content_sha256};
    const host = ctx.openDialog('Propose an argument update'), alive = ctx.dialogGuard();
    host.append(el('p', 'nb-update-intro', 'An independent reviewer decides whether to apply this change to the chosen result.'));
    const status = el('p', 'nb-hint'); status.setAttribute('role', 'status'); host.append(status);
    try {
      const data = await ctx.api(base + '/targets'); if (!alive()) return;
      if (!Array.isArray(data.targets) || !data.targets.length) { status.textContent = 'No update targets are available in this local trial yet.'; return; }
      const targets = data.targets.map(t => t.target ? {...t.target,current:t.current,roster:t.roster} : t);
      const form = el('form', 'nb-form'), target = ctx.field('Result to update', 'argument-target', targets.some(t => t.id === initial.targetId) ? initial.targetId : '', {required:true,options:[{value:'',label:'Choose the result'},...targets.map(t => ({value:t.id,label:t.title}))]});
      const operation = ctx.field('Requested change', 'argument-operation', initial.operation || (argument ? 'admit_argument' : 'withdraw_argument'), {options:[...(argument ? [{value:'admit_argument',label:'Add as an alternative'},{value:'select_argument',label:'Use as the displayed argument'}] : []),{value:'withdraw_argument',label:'Withdraw an admitted argument'}]});
      const withdrawal = ctx.field('Argument to withdraw', 'argument-withdrawal', '', {options:[]});
      const reason = ctx.field('What changes, and why?', 'argument-reason', '', {required:true,rows:4,max:10000});
      const preview = el('div', 'nb-update-preview'); let admitted = [];
      function paint() {
        const t = targets.find(t => t.id === target.input.value), withdrawing = operation.input.value === 'withdraw_argument';
        const previous = withdrawal.input.value !== '' ? admitted[Number(withdrawal.input.value)]?.argument : null;
        admitted = t ? admittedArguments(t) : [];
        const keep = admitted.findIndex(row => sameArgument(row.argument, previous || initial.withdrawArgument));
        withdrawal.input.replaceChildren();
        for (const [value,label] of [['','Choose the argument'],...admitted.map((row,index) => [String(index),row.resolved_argument?.title || 'Exact argument ' + (index + 1)])]) {
          const option = el('option','',label); option.value = value; withdrawal.input.append(option);
        }
        withdrawal.input.value = keep >= 0 ? String(keep) : admitted.length === 1 ? '0' : '';
        withdrawal.wrap.hidden = !withdrawing;
        preview.replaceChildren();
        if (t) preview.append(el('h4', '', 'Applies to'), prose(t.scope));
        if (withdrawing) preview.append(el('p','nb-hint','Only this argument is withdrawn. Other admitted arguments remain available; no conclusion is automatically declared false.'));
        else {
          preview.append(el('h4', '', 'Proposed argument'), el('p', '', argument.title));
          preview.append(el('p','nb-hint', operation.input.value === 'admit_argument' ? 'Adds an alternative without changing the displayed argument.' : 'Changes the displayed argument; other admitted arguments remain available.'));
        }
      }
      target.input.addEventListener('change', paint); operation.input.addEventListener('change', paint); paint();
      const error = el('p', 'nb-error'); error.setAttribute('role', 'alert');
      const actions = el('div', 'nb-form-actions'), submit = el('button', 'nb-button nb-primary', 'Submit update'); submit.type = 'submit';
      actions.append(button('Cancel', () => ctx.closeDialog(), 'nb-button'), submit); form.append(target.wrap, operation.wrap, withdrawal.wrap, preview, reason.wrap, error, actions); host.append(form);
      let pending = null, busy = false;
      ctx.protectDraft?.(() => Boolean(reason.input.value.trim() || target.input.value || pending || busy), 'Close this update draft? If a submission has no confirmed receipt, retry it here before starting another request.');
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (!alive() || busy) return;
        if (!reason.input.value.trim()) { error.textContent = 'Explain the change and the evidence supporting it.'; return; }
        const t = targets.find(t => t.id === target.input.value);
        if (!t) { error.textContent = 'Choose the exact result this update concerns.'; return; }
        const withdrawing = operation.input.value === 'withdraw_argument', withdrawn = withdrawal.input.value !== '' ? admitted[Number(withdrawal.input.value)] : null;
        if (withdrawing && !withdrawn?.argument) { error.textContent = 'Choose an admitted argument to withdraw.'; return; }
        pending ||= {target:{id:t.id,title:t.title,kind:t.kind,scope:t.scope,configuration_ids:t.configuration_ids,...(t.conclusion_statement ? {conclusion_statement:t.conclusion_statement} : {})},operation:operation.input.value,argument:withdrawing ? withdrawn.argument : {...evidenceSource,argument_id:argument.id},evidence:[evidenceSource],expected_adoption_id:t.current?.id || null,reason:reason.input.value.trim(),idempotency_key:crypto.randomUUID()};
        busy = true; submit.disabled = target.input.disabled = operation.input.disabled = withdrawal.input.disabled = reason.input.disabled = true; error.textContent = '';
        try { const result = await ctx.api(base + '/updates', pending); if (!alive()) return; if (!result.update?.id) throw Error('No confirmed update receipt.'); ctx.closeDialog(true); ctx.navigate(updateRoute(result.update.id)); }
        catch (err) { if (alive()) { error.textContent = err.message + (uncertain(err) ? ' Retry to confirm the same request.' : ''); if (!uncertain(err)) pending = null; } }
        finally { if (alive()) { busy = false; submit.disabled = false; target.input.disabled = operation.input.disabled = withdrawal.input.disabled = reason.input.disabled = Boolean(pending); submit.textContent = pending ? 'Retry the same update' : 'Submit update'; } }
      });
    } catch (error) { if (alive()) status.textContent = error.message; }
  }
  function rosterEntry(record, target, ctx) {
    const row = el('article','nb-roster-entry'), source = record.argument, argument = record.resolved_argument;
    if (!source || !argument) { row.append(el('p','nb-hint','This exact argument is unavailable.')); return row; }
    row.append(link(argument.title, exactRoute(source, 'argument', source.argument_id), ctx, 'nb-roster-title'));
    row.append(el('p','nb-roster-provenance', argumentProvenance(record) + (record.status === 'withdrawn' ? ' · Withdrawn' : record.is_current ? ' · Displayed' : '')));
    if (ctx.enabled() && record.status === 'admitted') {
      const actions = el('div','nb-roster-actions');
      if (!record.is_current) actions.append(button('Propose selection', () => proposeDialog(source, argument, ctx, {targetId:target.id,operation:'select_argument'})));
      actions.append(button('Propose withdrawal', () => proposeDialog(source, argument, ctx, {targetId:target.id,operation:'withdraw_argument',withdrawArgument:source})));
      row.append(actions);
    }
    return row;
  }
  async function readTarget(host, id, ctx) {
    try {
      const data = await ctx.api(base + '/targets/' + encodeURIComponent(id)); if (!ctx.current()) return;
      const target = data.target, roster = Array.isArray(data.roster) ? data.roster : [];
      const scheme = data.scheme_context;
      if (scheme && typeof scheme.scheme_id === 'string' && /^[a-z][a-z0-9-]{0,159}$/.test(scheme.scheme_id) &&
          typeof scheme.version === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}$/.test(scheme.version)) {
        host.append(link('← Scheme overview', '#scheme/' + encodeURIComponent(scheme.scheme_id) + '/' + encodeURIComponent(scheme.version), ctx, 'nb-link nb-back nb-scheme-back'));
      }
      const targetScope = prose(target.scope); targetScope.classList.add('nb-target-scope');
      host.append(el('h1', 'nb-full-title', target.title), el('p', 'nb-trial-marker', 'Local workflow trial'), targetScope);
      if (target.configuration_ids?.length) {
        const coverage = el('details','nb-use-details'); coverage.append(el('summary','','Configuration coverage'),el('p','nb-configurations',target.configuration_ids.join(', '))); host.append(coverage);
      }
      if (data.current?.status === 'withdrawn') {
        host.append(el('h2','nb-workflow-heading','Displayed justification withdrawn'), prose(data.current.reason), el('p','nb-hint','No argument is currently selected for display. Other admitted arguments remain available; the result has not been declared false.'));
        if (!roster.length && data.current.withdrawn_argument) { const prior = el('details','nb-details'); prior.append(el('summary','','Previously used argument'),argumentCard(data.current.withdrawn_argument,data.current.argument,null,ctx,{propose:false,compact:true,displayedScope:target.scope})); host.append(prior); }
      } else if (data.current?.resolved_argument) {
        const selected = roster.find(row => row.is_current) || data.current;
        host.append(el('h2', 'nb-workflow-heading', 'Displayed argument'), el('p','nb-roster-provenance',argumentProvenance(selected)), argumentCard(data.current.resolved_argument, data.current.argument, null, ctx, {propose:false,compact:true,displayedScope:target.scope}));
        if (data.current.reason) host.append(prose(data.current.reason));
        if (ctx.enabled()) host.append(button('Propose withdrawal', () => proposeDialog(data.current.argument, data.current.resolved_argument, ctx, {targetId:target.id,operation:'withdraw_argument',withdrawArgument:data.current.argument})));
      } else host.append(el('p', 'nb-hint', data.current?.argument ? 'The displayed source is unavailable.' : 'No argument has been selected for this result.'));
      const alternatives = roster.filter(row => row.status === 'admitted' && !row.is_current);
      if (alternatives.length) {
        const section = el('section','nb-argument-roster'); section.append(el('h2','nb-workflow-heading','Other admitted arguments'));
        for (const record of alternatives) section.append(rosterEntry(record,target,ctx)); host.append(section);
      }
      const retired = roster.filter(row => row.status === 'withdrawn');
      if (retired.length) { const section = el('details','nb-details nb-roster-history'); section.append(el('summary','','Withdrawn arguments')); for (const record of retired) section.append(rosterEntry(record,target,ctx)); host.append(section); }
      if (ctx.enabled() && ctx.composeRelated) {
        const conclusion = target.conclusion_statement, reference = conclusion ? exactRoute(conclusion, 'statement', conclusion.statement_id) : data.current?.argument ? exactRoute(data.current.argument, 'argument', data.current.argument.argument_id) : null;
        if (reference) host.append(button('Write an argument note', () => {
          if (ctx.current()) ctx.composeRelated({title:target.title,url:'/' + reference,relation:'responds_to',scope:target.scope});
        }, 'nb-button nb-write-argument'));
      }
      const history = el('details', 'nb-details'); history.append(el('summary', '', 'Adoption history'));
      for (const event of data.history || []) {
        const row = el('div', 'nb-update-history');
        if (Number.isFinite(event.created_at)) row.append(el('span', '', new Date(event.created_at * 1000).toLocaleDateString()));
        row.append(event.update_id ? link('Review decision', updateRoute(event.update_id, ctx.reviewOnly), ctx) : el('span','','Initial admission'));
        history.append(row);
      }
      if (history.children.length > 1) host.append(history);
    } catch (error) { if (ctx.current()) host.append(el('p', 'nb-error', error.message)); }
  }
  async function readUpdate(host, id, ctx) {
    try {
      const {update} = await ctx.api(base + '/updates/' + encodeURIComponent(id)); if (!ctx.current()) return;
      if (update.source_unavailable === true) {
        host.append(el('h1', 'nb-full-title', 'Update source unavailable'), el('p', 'nb-update-state', 'Decision preserved'),
          prose(update.reason || 'The decision is preserved, but an exact source is no longer available.'));
        if (update.assessment?.reviewer?.handle) host.append(el('p', 'nb-hint', 'Reviewed by ' + update.assessment.reviewer.handle));
        return;
      }
      host.append(el('h1', 'nb-full-title', update.target.title), el('p', 'nb-update-state', stateLabel(update.status)), el('p', 'nb-trial-marker', 'Local workflow trial'));
      const intro = el('section', 'nb-update-evidence'); intro.append(el('h2', '', operationName(update.operation)), link('View arguments for this result', targetRoute(update.target.id), ctx), prose(update.reason), el('h3', '', 'Applies to'), prose(update.target.scope)); host.append(intro);
      if (update.operation === 'withdraw_argument') host.append(el('p','nb-hint','This proposal withdraws only the identified argument. Other admitted arguments remain available; no conclusion is declared false.'));
      if (update.stale) host.append(el('p', 'nb-update-warning', 'The argument roster changed after this proposal was submitted. Review the new state and submit a fresh proposal; this one cannot overwrite it.'));
      if (update.resolved_argument) host.append(argumentCard(update.resolved_argument, update.argument, null, ctx, {propose:false}));
      const evidence = el('section', 'nb-update-evidence'); evidence.append(el('h2', '', 'Evidence'));
      for (const item of update.resolved_evidence || []) evidence.append(link(item.title || 'Exact source note', exactRoute(item.source, item.source.section_id ? 'note_section' : null, item.source.section_id), ctx));
      host.append(evidence);
      if (update.assessment) { host.append(el('h2', 'nb-workflow-heading', 'Review decision'), prose(update.assessment.reason), el('p', 'nb-hint', 'Reviewed by ' + (update.assessment.reviewer?.handle || 'a configured reviewer'))); }
      else if (update.can_review && ctx.reviewOnly) { if (update.publication_ready === false) host.append(el('p','nb-hint','All exact evidence and premise sources must be published before this update can be applied.')); reviewForm(host, update, ctx); }
      else if (update.can_review) host.append(link('Review update', updateRoute(id, true), ctx, 'nb-button'));
      else if (update.status === 'pending') host.append(el('p', 'nb-hint', 'An independent reviewer must assess this update.'));
      host.append(el('p', 'nb-hint', 'This trial changes the admitted arguments or their displayed selection. It does not change challenges, curve values or points.'));
    } catch (error) { if (ctx.current()) host.append(el('p', 'nb-error', error.message)); }
  }
  function reviewForm(host, update, ctx) {
    const form = el('form', 'nb-form nb-argument-review'), choice = ctx.field('Decision', 'argument-decision', 'changes_requested', {options:[{value:'changes_requested',label:'Request changes'},...(update.can_apply ? [{value:'approve',label:'Approve and apply'}] : []),{value:'decline',label:'Do not adopt'}]});
    const reason = ctx.field('Assessment and remaining limitations', 'argument-assessment', '', {required:true,rows:5,max:10000});
    const error = el('p', 'nb-error'); error.setAttribute('role', 'alert'); const submit = el('button', 'nb-button nb-primary', 'Record decision'); submit.type = 'submit';
    host.append(el('h2', 'nb-workflow-heading', 'Review this update')); form.append(choice.wrap, reason.wrap, error, submit); host.append(form);
    let pending = null, busy = false;
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy || !ctx.current()) return;
      if (!reason.input.value.trim()) { error.textContent = 'Record your reasoning and the scope checked.'; return; }
      pending ||= {packet_sha256:update.packet_sha256,decision:choice.input.value,reason:reason.input.value.trim(),idempotency_key:crypto.randomUUID()};
      busy = true; submit.disabled = choice.input.disabled = reason.input.disabled = true; error.textContent = '';
      try { const result = await ctx.api(base + '/updates/' + encodeURIComponent(update.id) + '/review', pending); if (!ctx.current()) return; if (!result.update?.id) throw Error('No confirmed decision receipt.'); host.replaceChildren(link('← Proposals', '#reviews?section=proposals', ctx, 'nb-link nb-back')); await readUpdate(host, update.id, ctx); }
      catch (err) { if (ctx.current()) { error.textContent = err.message + (uncertain(err) ? ' Retry the same decision to confirm its receipt.' : ''); if (!uncertain(err)) pending = null; } }
      finally { if (ctx.current()) { busy = false; submit.disabled = false; choice.input.disabled = reason.input.disabled = Boolean(pending); submit.textContent = pending ? 'Retry the same decision' : 'Record decision'; } }
    });
  }
  async function updatesList(host, ctx) {
    try {
      const {updates} = await ctx.api(base + '/updates'); if (!ctx.current() || !updates?.length) return;
      const section = el('section', 'nb-argument-update-list'); section.append(el('h2', 'nb-workflow-heading', 'Argument updates'));
      const row = update => { const item = el('article', 'nb-research-request'); item.append(link(update.target.title, updateRoute(update.id, ctx.reviewOnly), ctx, 'nb-title'), el('p', 'nb-hint', stateLabel(update.status))); return item; };
      const pending = updates.filter(update => update.status === 'pending'), past = updates.filter(update => update.status !== 'pending');
      for (const update of pending) section.append(row(update));
      if (!pending.length) section.append(el('p', 'nb-hint', 'No argument updates awaiting review.'));
      if (past.length) { const history = el('details', 'nb-details nb-past-argument-updates'); history.append(el('summary', '', 'Past argument updates')); for (const update of past) history.append(row(update)); section.append(history); }
      host.append(section);
    } catch (error) { if (ctx.current() && error.status !== 404) host.append(el('p', 'nb-error', 'Argument updates could not be loaded.')); }
  }
  function editor(content, field) {
    const host = el('details', 'nb-details nb-statement-editor'); host.append(el('summary', '', 'Statements & arguments (optional)'));
    const list = el('div'); host.append(list); let serial = 0, locked = false; const entries = [];
    function add(value = {}) {
      const i = serial++, block = el('div', 'nb-statement-editor-row');
      const title = field('Statement title', 'statement-title-' + i, value.title || '', {max:240});
      const label = field('Label', 'statement-label-' + i, value.label || '', {max:30,placeholder:'e.g. S-12 or A-12'});
      const definition = field('Precise statement', 'statement-definition-' + i, value.definition || '', {rows:5,max:12000});
      const refs = field('Pinned definitions (optional JSON)', 'statement-definitions-' + i, JSON.stringify(value.definitions || [], null, 2), {rows:3});
      const details = el('details', 'nb-use-details'); details.append(el('summary', '', 'Definition references'), refs.wrap);
      const item = {original:value,id:value.id || 'statement-' + crypto.randomUUID().replaceAll('-', '').slice(0,24),title:title.input,label:label.input,definition:definition.input,refs:refs.input,removed:false};
      const remove = button('Remove statement', () => { if (locked) return; item.removed = true; block.remove(); }, 'nb-link'); item.remove = remove; entries.push(item);
      block.append(title.wrap, label.wrap, definition.wrap, details, remove); list.append(block);
    }
    for (const value of content?.statements || []) add(value);
    const more = button('Add statement', () => { if (!locked && entries.filter(x => !x.removed).length < 32) add(); }, 'nb-button'); host.append(more);
    const args = field('Argument records (JSON)', 'statement-arguments', JSON.stringify(content?.arguments || [], null, 2), {rows:8});
    const advanced = el('details', 'nb-use-details'); advanced.append(el('summary', '', 'Argument structure'), el('p', 'nb-hint', 'Optional records linking a conclusion statement, reasoning and premises. Suitable for preparing with an agent; ordinary notes need none.'), args.wrap); host.append(advanced);
    const initial = JSON.stringify({statements:content?.statements || [],arguments:content?.arguments || []});
    function value() {
      const statements = entries.filter(x => !x.removed).map(x => { const definitions = JSON.parse(x.refs.value || '[]'); return {id:x.id,label:x.label.value.trim(),title:x.title.value.trim(),definition:x.definition.value,...(definitions.length || x.original.definitions !== undefined ? {definitions} : {})}; });
      const arguments_ = JSON.parse(args.input.value || '[]');
      const output = {}; if (statements.length || content?.statements !== undefined) output.statements = statements; if (arguments_.length || content?.arguments !== undefined) output.arguments = arguments_;
      validate(output); return output;
    }
    return {section:host,value,dirty:() => { try { const v = value(); return JSON.stringify({statements:v.statements || [],arguments:v.arguments || []}) !== initial; } catch (_) { return true; } },disable:yes => { locked = yes; more.disabled = args.input.disabled = yes; for (const x of entries) for (const input of [x.title,x.label,x.definition,x.refs,x.remove]) input.disabled = yes; }};
  }
  function contributionAction(note, revision, ctx) {
    if (!ctx.enabled()) return null;
    return button('Propose an argument update', () => {
      const arguments_ = revision.content.arguments || [];
      const selected = arguments_.find(argument => argument.id === ctx.params.get('argument'));
      if (selected || arguments_.length <= 1) return proposeDialog(pin(note, revision), selected || arguments_[0] || null, ctx);
      const host = ctx.openDialog('Choose an argument');
      for (const argument of arguments_) host.append(button(argument.title, () => proposeDialog(pin(note, revision), argument, ctx), 'nb-button nb-argument-choice'));
      host.append(button('Withdraw a current argument using this note as evidence', () => proposeDialog(pin(note, revision), null, ctx), 'nb-link nb-argument-choice'));
    });
  }
  window.CrucibleStatementWorkflow = Object.freeze({validate,renderNote,readUpdate,readTarget,updatesList,editor,exactRoute,contributionAction});
})();

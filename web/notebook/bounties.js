/* Production bounty view. Server records own terms, allowances and decisions. */
(() => {
  'use strict';
  const sorts = {concrete: 'More concrete first', open: 'More open-ended first', 'points-high': 'Highest points first', 'points-low': 'Lowest points first'};
  const statuses = {draft: 'Draft', submitted: 'Submitted for review', pending: 'Submitted for review', changes_requested: 'Changes requested', open: 'Open', under_review: 'Under assessment', frozen: 'Under assessment', completed: 'Completed', closed: 'Completed', withdrawn: 'Withdrawn', declined: 'Not admitted', cancelled: 'Cancelled'};
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = String(text); return node; };
  function button(text, action, cls = 'nb-button') {
    const node = el('button', cls, text); node.type = 'button'; node.addEventListener('click', action); return node;
  }
  function field(label, name, value, {rows, options, max = 2000, required = true} = {}) {
    const wrap = el('label', 'nb-field'), input = el(options ? 'select' : rows ? 'textarea' : 'input');
    wrap.append(el('span', '', label)); input.name = name; input.required = required;
    if (rows) input.rows = rows;
    if (!options) input.maxLength = max;
    if (options) for (const [key, text] of options) { const option = el('option', '', text); option.value = key; input.append(option); }
    input.value = value ?? ''; wrap.append(input); return {wrap, input};
  }
  function spectrum(value) {
    const root = el('span', 'bty-scope-rating'), ends = el('span', 'bty-rating-ends'), steps = el('span', 'bty-rating-steps');
    ends.append(el('span', '', 'Open-ended'), el('span', '', 'Concrete'));
    for (let i = 1; i <= 5; i++) steps.append(el('span', i === value ? 'bty-rating-current' : '', i));
    ends.setAttribute('aria-hidden', 'true'); steps.setAttribute('aria-hidden', 'true');
    root.setAttribute('role', 'img'); root.setAttribute('aria-label', `Scope ${value} of 5; 1 is open-ended, 5 is concrete`);
    root.append(ends, steps); return root;
  }
  function reward(value) {
    return el('span', 'bty-points', Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('en-US') + ' points' : 'Reward not set');
  }
  function reference(pin) {
    const number = pin.note_number ?? pin.number;
    const link = el(pin.unavailable ? 'span' : 'a', 'bty-note-ref', pin.unavailable ? 'Unavailable note' : pin.title || (Number.isInteger(number) ? '#' + String(number).padStart(4, '0') : 'Research note'));
    if (!pin.unavailable) link.href = '#notebook?' + new URLSearchParams({note: pin.note_id, revision: String(pin.revision), sha256: pin.content_sha256});
    link.append(el('small', '', 'Revision ' + pin.revision + ' · ' + pin.content_sha256.slice(0, 12))); return link;
  }
  function mount(host, {api, navigate, current = () => true, canWarm = () => true, settleScroll = () => {}, reviewOnly = false, client: suppliedClient}) {
    const client = suppliedClient || window.CrucibleBountyAPI.createClient(api, current);
    let active = false, destroyed = false, request = 0, state = {}, policy = null, allowance = null, selected = null, signature = '';
    let nextFocus = null, pageTimer = null, action = null;
    const drafts = new Map(), pendingWrites = new Map(), completedWrites = new Map();
    const owns = () => !destroyed && current();
    const present = () => active && owns();
    const root = el('div', 'bty-root'); host.replaceChildren(root);
    const heading = el('div', 'bty-heading'), title = el('h2', '', 'Research bounties'); title.tabIndex = -1;
    const titleRow = el('div', 'bty-title-row'); titleRow.append(title);
    const headingActions = el('div', 'bty-heading-actions'), allowanceNode = el('span', 'bty-allowance');
    const propose = button('Propose a bounty', () => change({compose: true, id: ''}, 'bounty-title'));
    headingActions.append(allowanceNode, propose); heading.append(titleRow, headingActions);
    const notice = el('div', 'bty-notice'); notice.setAttribute('role', 'status');
    const board = el('section'), listHeading = el('div', 'bty-list-heading'), count = el('span', 'bty-count');
    const sortField = field('Sort by', 'bounty-sort', 'concrete', {options: Object.entries(sorts)}); sortField.wrap.classList.add('bty-sort');
    sortField.input.addEventListener('change', () => change({sort: sortField.input.value, page: 1}, 'bounty-sort'));
    listHeading.append(count, sortField.wrap);
    const entries = el('div', 'bty-grid'), pagination = el('nav', 'bty-pagination'); pagination.setAttribute('aria-label', 'Bounty pages');
    const secondary = el('nav', 'bty-secondary'); secondary.setAttribute('aria-label', 'Bounty collections');
    const mine = button('Your activity', () => change({status: 'mine', page: 1}, 'heading'), 'bty-secondary-link');
    const past = button('Past bounties', () => change({status: 'past', page: 1}, 'heading'), 'bty-secondary-link');
    const open = button('← Research bounties', () => change({status: 'open', page: 1}, 'heading'), 'bty-secondary-link');
    const agent = button('Copy agent prompt', () => { void copyAgentPrompt(); }, 'bty-secondary-link'); agent.hidden = reviewOnly;
    secondary.append(mine, agent, past, open); board.append(listHeading, entries, pagination, secondary);
    const reader = el('section', 'bty-reader'), composer = el('section', 'bty-composer'); reader.hidden = composer.hidden = true;
    root.append(heading, notice, board, reader, composer);
    function announce(text, error = false) { notice.replaceChildren(); if (text) notice.append(el('p', error ? 'bty-error' : 'nb-notice', text)); }
    function agentPrompt() {
      const origin = new URL(window.location.href).origin;
      const client = `python3 crucible_cli.py --server ${origin}`;
      const first = state.id ? `bounty show ${state.id}` : 'bounty list';
      const setup = state.id ? `bounty init --bounty ${state.id} --path bounty-work` : 'bounty init --path bounty-work';
      return `Help me work on Crucible research bounties at ${origin}.\n\nClient: ${origin}/api/agent/client — save as crucible_cli.py and inspect before running. Reuse an existing connection with the needed permissions; otherwise connect with ${client} login --scope bounties:read --scope bounties:write --no-browser and let me approve it in my browser.\n\nRead ${client} guide, then ${client} guide --resource bounties.txt. Start with ${client} ${first}. If local work is useful, use a new directory: ${client} ${setup}. Read its README and exact task terms. Local initialization, reads and private draft saves use no monthly bounty allowance.\n\nKeep completion criteria and the verification plan explicit. Cite exact published note revisions and hashes; distinguish useful partial progress from a claimed solution. Saving a draft does not publish or admit a task. Linking a note does not approve a solution or award points.\n\nAllocate monthly bounty allowance only within my authorized request. Admission requests and boosts need a separately approved bounties:spend scope and an explicit --confirm-allowance amount matching the saved policy. Existing tokens gain no new permissions. Read the current allowance and costs; do not assume them. Never approve reviews through an agent. If a write response is lost, preserve .bounty-request.json and rerun the same command; do not create a replacement request. Return the saved task or contribution receipt and any unresolved questions.`;
    }
    async function copyAgentPrompt() {
      if (!present() || reviewOnly) return;
      const text = agentPrompt(), ownerRoute = route();
      try {
        await window.navigator.clipboard.writeText(text);
        if (present() && route() === ownerRoute) announce('Agent prompt copied.');
      } catch {
        if (!present() || route() !== ownerRoute) return;
        const box = el('textarea', 'bty-agent-prompt'); box.value = text; box.readOnly = true; box.rows = 8;
        box.setAttribute('aria-label', 'Agent prompt'); notice.replaceChildren(box); box.focus(); box.select?.();
      }
    }
    function route(next = state) {
      const params = new URLSearchParams(reviewOnly ? {section: 'bounties'} : {view: 'bounties'});
      if (next.status && next.status !== 'open') params.set('status', next.status);
      if (next.sort && next.sort !== 'concrete') params.set('sort', next.sort);
      if (next.page > 1) params.set('page', String(next.page));
      if (next.id) params.set('bounty', next.id);
      if (next.detail && next.detail !== 'brief') params.set('detail', next.detail);
      if (next.compose) params.set('compose', 'bounty');
      return (reviewOnly ? '#reviews?' : '#notebook?') + params;
    }
    function change(patch, focus) {
      if (!present()) return;
      nextFocus = focus; action = null; navigate(route({...state, ...patch}));
    }
    function focusAfter() {
      if (!nextFocus || !present()) return;
      const node = nextFocus === 'heading' ? title : root.querySelector('[data-bounty-focus="' + nextFocus + '"]') || root.querySelector('[name="' + nextFocus + '"]');
      (node && !node.disabled ? node : title).focus({preventScroll: true}); nextFocus = null; settleScroll();
    }
    function updateAllowance() {
      const stamp = JSON.stringify(allowance);
      if (allowanceNode.dataset.stamp === stamp) return;
      allowanceNode.dataset.stamp = stamp; allowanceNode.replaceChildren();
      if (reviewOnly || !allowance) return;
      allowanceNode.append(el('span', '', 'Monthly bounty allowance'), el('strong', '', `${allowance.available_units} of ${allowance.monthly_units} remaining`));
      allowanceNode.title = 'Unused allowance expires at the end of each UTC calendar month. Refunds return to their original month.';
      if (allowance.policy_status === 'pending') allowanceNode.append(el('small', '', 'Allocation policy pending'));
    }
    async function loadAllowance() {
      const token = request;
      try { const result = await client.allowance(); if (present() && token === request) { allowance = result.allowance; policy = result.policy || policy; updateAllowance(); } }
      catch (error) { if (present() && token === request && !error.retired) allowanceNode.textContent = 'Allowance unavailable'; }
    }
    function placeholder(target, list = false) {
      target.setAttribute('aria-busy', 'true'); target.replaceChildren();
      const reserve = el('div', 'bty-pending'); reserve.setAttribute('aria-hidden', 'true');
      if (list) { for (let i = 0; i < 3; i++) target.append(reserve.cloneNode(true)); }
      else target.append(reserve);
    }
    function errorView(target, error, retry, retained) {
      target.removeAttribute('aria-busy');
      if (error?.retired || !present()) return;
      const keep = retained && ![401, 403, 404, 410].includes(error?.status);
      if (!keep) target.replaceChildren();
      const message = el('div', 'bty-error bty-read-error'); message.setAttribute('role', 'alert');
      message.append(el('p', '', keep ? 'Refresh failed. Previously loaded information remains visible.' : error.message || 'Bounties could not be retrieved.'), button('Try again', retry));
      target.append(message);
    }
    function listOptions() { return {status: state.status, sort: state.sort, page: state.page, review: reviewOnly}; }
    function paintList(result) {
      policy = result.policy || policy;
      const nextSignature = JSON.stringify([state.status, state.sort, state.page, result]);
      entries.removeAttribute('aria-busy');
      entries.querySelectorAll('.bty-read-error').forEach(node => node.remove());
      if (signature === nextSignature && entries.childElementCount) { focusAfter(); return; }
      signature = nextSignature; entries.replaceChildren(); pagination.replaceChildren();
      const openBoard = state.status === 'open' && !reviewOnly;
      entries.classList.toggle('bty-grid', openBoard); entries.classList.toggle('bty-management-list', !openBoard);
      count.textContent = result.bounties.length ? (openBoard ? 'Open tasks' : reviewOnly ? 'Awaiting review' : '') : '';
      if (!result.bounties.length) entries.append(el('p', 'bty-no-results', reviewOnly ? 'No bounty reviews are waiting.' : state.status === 'mine' ? 'Your proposals and contributions will appear here.' : state.status === 'past' ? 'No completed bounties yet.' : 'No open bounties yet.'));
      for (const item of result.bounties) {
        const card = button('', () => change({id: item.id, detail: 'brief'}, 'reader-title'), openBoard ? 'nb-card bty-question' : 'bty-management-row');
        card.dataset.bounty = item.id; card.dataset.bountyFocus = item.id;
        const title = el('span', 'bty-question-title', item.title);
        if (openBoard) {
          const cardHeading = el('span', 'nb-title-copy bty-card-heading'), badge = el('span', 'nb-note-status'); badge.append(el('span', 'nb-visibility', 'Bounty')); cardHeading.append(title, badge);
          const tags = el('span', 'bty-skills'); item.background_tags.forEach(tag => tags.append(el('span', '', tag)));
          const footer = el('span', 'bty-card-foot'); footer.append(spectrum(item.scope), reward(item.reward_points));
          card.append(cardHeading, el('span', 'bty-question-summary', item.summary), tags, footer);
        } else {
          const text = el('span'); text.append(el('span', 'bty-management-status', statuses[item.status] || item.status), title); card.append(text, reward(item.reward_points));
        }
        entries.append(card);
      }
      if (state.page > 1 || result.pagination.has_more) {
        const previous = button('Previous', () => change({page: state.page - 1}, 'previous'));
        const next = button('Next', () => change({page: state.page + 1}, 'next'));
        previous.disabled = state.page <= 1; next.disabled = !result.pagination.has_more;
        previous.dataset.bountyFocus = 'previous'; next.dataset.bountyFocus = 'next';
        pagination.append(previous, el('span', '', 'Page ' + state.page), next);
      }
      focusAfter();
    }
    async function loadList() {
      const token = request, options = listOptions(), cached = client.cachedList(options);
      if (cached) paintList(cached); else { signature = ''; placeholder(entries, true); pagination.replaceChildren(); }
      try {
        const result = await client.list(options);
        if (!present() || token !== request) return;
        paintList(result); prepareNext(result, options);
      } catch (error) { if (present() && token === request) errorView(entries, error, () => { signature = ''; void loadList(); }, cached); }
    }
    function prepareNext(result, options) {
      clearTimeout(pageTimer);
      if (!result.pagination.has_more || !canWarm()) return;
      const token = request;
      pageTimer = setTimeout(() => {
        pageTimer = null;
        if (present() && token === request && canWarm() && !document.hidden && !window.navigator?.connection?.saveData) {
          void client.list({...options, page: options.page + 1}).catch(() => {});
        }
      }, 200);
    }
    function section(title, text) {
      const node = el('section', 'bty-section'); node.append(el('h3', '', title), el('p', '', text || '')); return node;
    }
    function draftFor(key, initial) {
      if (!drafts.has(key)) drafts.set(key, {values: initial, dirty: false});
      return drafts.get(key);
    }
    function attachForm(form, fields, draft, key) {
      for (const [name, field] of Object.entries(fields)) {
        field.input.disabled = pendingWrites.has(key);
        field.input.addEventListener('input', () => { draft.values[name] = field.input.value; draft.dirty = true; });
        form.append(field.wrap);
      }
    }
    function formActions(form, key, label, run) {
      if (completedWrites.has(key)) {
        const receipt = completedWrites.get(key);
        for (const input of form.querySelectorAll('input, textarea, select')) input.disabled = true;
        form.append(el('p', 'nb-notice', 'This request was saved while you were browsing.'), button('View bounty', () => {
          completedWrites.delete(key); drafts.delete(key); change({id: receipt.bounty.id, compose: false, detail: 'brief'}, 'reader-title');
        }));
        return;
      }
      const controls = el('div', 'nb-form-actions'), error = el('p', 'bty-error'); error.setAttribute('role', 'alert');
      const save = el('button', 'nb-button nb-primary', pendingWrites.has(key) ? 'Retry the same request' : label); save.type = 'submit';
      controls.append(save); form.append(error, controls);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (!present() || save.disabled) return;
        save.disabled = true; error.textContent = '';
        for (const input of form.querySelectorAll('input, textarea, select')) input.disabled = true;
        try { await run(error); }
        catch (failure) { if (present() && !failure.retired) error.textContent = failure.message || 'This request could not be completed.'; }
        finally {
          if (present() && save.isConnected) { save.disabled = false; save.textContent = pendingWrites.has(key) ? 'Retry the same request' : label; for (const input of form.querySelectorAll('input, textarea, select')) input.disabled = pendingWrites.has(key); }
        }
      });
    }
    async function mutate(key, make, perform, success) {
      let body = pendingWrites.get(key);
      if (!body) { body = {...make(), idempotency_key: crypto.randomUUID()}; pendingWrites.set(key, body); }
      const ownerRoute = route();
      try {
        const result = await perform(body);
        if (!owns()) return;
        pendingWrites.delete(key); client.invalidateReads(); allowance = null;
        if (present() && ownerRoute === route()) { policy = result.policy || policy; success(result); }
        else {
          completedWrites.set(key, result);
          if (drafts.has(key)) drafts.get(key).dirty = false;
        }
      } catch (error) {
        if (owns() && error.status >= 400 && error.status < 500) pendingWrites.delete(key);
        throw error;
      }
    }
    function saved(result, message) {
      selected = result.bounty; action = null; announce(message);
      change({id: selected.id, compose: false, detail: 'brief'}, 'reader-title');
    }
    function proposalForm(task = null) {
      const key = task ? 'edit:' + task.id : 'new';
      const terms = task?.terms || {};
      const draft = draftFor(key, {title: terms.title || '', summary: terms.summary || '', tags: (terms.background_tags || []).join(', '), scope: String(terms.scope || 3), completion: terms.completion_criteria || '', verification: terms.verification_plan || '', sources: terms.source_notes ? JSON.stringify(terms.source_notes, null, 2) : ''});
      if (task && !Number.isInteger(draft.baseVersion)) draft.baseVersion = task.version;
      composer.replaceChildren(button('← Research bounties', () => change({compose: false, detail: 'brief'}, 'heading'), 'nb-link'));
      const heading = el('h2', '', task ? 'Edit bounty draft' : 'Propose a bounty'); composer.append(heading);
      if (task && draft.baseVersion !== task.version && !pendingWrites.has(key)) {
        const reset = button('Discard local edits and load the latest draft', () => { drafts.delete(key); proposalForm(task); });
        composer.append(el('p', 'bty-form-hint', 'This draft changed elsewhere. Your local edits remain here until you choose to replace them.'), reset);
      }
      const form = el('form', 'nb-form bty-form'), v = draft.values;
      const fields = {
        title: field('The task', 'bounty-title', v.title, {max: 160}),
        summary: field('A short description', 'bounty-summary', v.summary, {rows: 3, max: 1200}),
        completion: field('What counts as completion?', 'bounty-completion', v.completion, {rows: 4, max: 6000}),
        verification: field('How will it be verified?', 'bounty-verification', v.verification, {rows: 4, max: 6000}),
        sources: field('Exact source-note citations', 'bounty-sources', v.sources, {rows: 4, max: 12000, required: false}),
        tags: field('Useful background', 'bounty-tags', v.tags, {max: 300, required: false}),
        scope: field('Open-ended → Concrete', 'bounty-scope', v.scope, {options: [1, 2, 3, 4, 5].map(n => [String(n), n + ' / 5'])})
      };
      attachForm(form, fields, draft, key);
      form.append(el('p', 'bty-form-hint', 'Paste a link or JSON from Cite this note. For several sources, use a JSON array. Saving a private draft uses no allowance; requesting admission is a separate step.'));
      formActions(form, key, 'Save private draft', async () => {
        await mutate(key, () => {
          const v = draft.values;
          return {...(task ? {expected_version: draft.baseVersion} : {}), terms: {title: v.title.trim(), summary: v.summary.trim(), background_tags: v.tags.split(',').map(t => t.trim()).filter(Boolean), scope: Number(v.scope), completion_criteria: v.completion.trim(), verification_plan: v.verification.trim(), source_notes: v.sources.trim() ? window.CrucibleBountyAPI.parseReferences(v.sources) : []}};
        }, body => task ? client.revise(task.id, body) : client.create(body), result => { drafts.delete(key); saved(result, 'Private bounty draft saved.'); });
      });
      composer.append(form); focusAfter();
    }
    function exactContext(task) { return {expected_version: task.version, terms_sha256: task.terms_sha256}; }
    function confirmAction(task, kind) {
      const key = kind + ':' + task.id;
      const panel = el('section', 'bty-action-panel'), form = el('form', 'nb-form bty-form');
      let text, cost;
      if (kind === 'boost') {
        cost = policy?.boost_cost_units;
        text = Number.isSafeInteger(policy?.points_per_boost) ? `Use ${cost} of your monthly bounty allowance to add ${policy.points_per_boost} reward points.` : 'The bounty allocation policy is not ready.';
      } else {
        cost = policy?.proposal_cost_units;
        text = Number.isSafeInteger(cost) ? `Reserve ${cost} of your monthly bounty allowance while this proposal is reviewed. Approval starts its ${policy?.proposal_reward_points ?? policy?.default_base_reward_points ?? policy?.points_per_boost}-point reward. If declined or withdrawn before admission, the reservation returns to its original month; expired allowance cannot be spent.` : 'The bounty allocation policy is not ready.';
      }
      if (kind !== 'boost' && task.reservation?.status === 'held') text = `Your existing reservation of ${task.reservation.units} remains attached to this proposal. Submitting the revised brief uses no additional allowance.`;
      panel.append(el('h3', '', kind === 'boost' ? 'Confirm bounty boost' : 'Request bounty admission'), el('p', 'bty-form-hint', text));
      if (task.pending_claim_count > 0 && kind === 'boost') panel.append(el('p', 'bty-form-hint', 'A solution is awaiting assessment. Your boost will return to its original month if an earlier submission receives the award; expired allowance cannot be spent.'));
      const cancel = button('Cancel', () => { action = null; signature = ''; paintDetail({bounty: task}); }); panel.append(cancel);
      if (policy?.status === 'ready' && Number.isSafeInteger(cost)) {
        formActions(form, key, kind === 'boost' ? 'Confirm boost' : 'Submit for review', () => mutate(key, () => ({...exactContext(task), policy_id: policy.id, confirm_cost_units: cost, ...(kind === 'boost' ? {units: 1} : {})}),
          body => kind === 'boost' ? client.boost(task.id, body) : client.admission(task.id, body), result => saved(result, kind === 'boost' ? 'Bounty boost recorded.' : 'Bounty submitted for review.')));
        panel.append(form);
      }
      return panel;
    }
    async function beginAction(kind) {
      if (!present() || !selected) return;
      const id = selected.id, token = request;
      try {
        const [result, wallet] = await Promise.all([client.detail(id, {refresh: true}), client.allowance({refresh: true})]);
        if (!present() || request !== token || state.id !== id) return;
        allowance = wallet.allowance; policy = result.policy || wallet.policy || policy; updateAllowance();
        if (!result.bounty.capabilities[kind === 'boost' ? 'boost' : 'request_admission']) throw Error('This action is no longer available. Refresh the bounty.');
        action = kind; signature = ''; paintDetail(result);
      } catch (error) { if (present() && token === request && !error.retired) announce(error.message, true); }
    }
    function contributionForm(task) {
      const key = 'contribute:' + task.id;
      const draft = draftFor(key, {note: '', claim: 'partial', explanation: ''}), form = el('form', 'nb-form bty-form');
      const fields = {note: field('Exact contribution-note citation', 'contribution-note', draft.values.note, {rows: 4, max: 6000}), claim: field('What does it offer?', 'contribution-claim', draft.values.claim, {options: [['partial', 'Useful partial progress'], ['solution', 'A solution to the stated task']]}), explanation: field('What does it establish?', 'contribution-explanation', draft.values.explanation, {rows: 4, max: 6000})};
      attachForm(form, fields, draft, key);
      form.append(el('p', 'bty-form-hint', 'Use an exact published note revision. Linking it does not publish a private draft or establish completion.'));
      formActions(form, key, 'Submit contribution', () => mutate(key, () => ({...exactContext(task), contribution_note: window.CrucibleBountyAPI.parseReferences(draft.values.note, {single: true})[0], claim: draft.values.claim, explanation: draft.values.explanation.trim()}), body => client.submit(task.id, body), result => { drafts.delete(key); saved(result, 'Contribution submitted for assessment.'); }));
      return form;
    }
    function withdrawalForm(task, cancellation = false) {
      const key = (cancellation ? 'cancel:' : 'withdraw:') + task.id, draft = draftFor(key, {reason: ''});
      const panel = el('section', 'bty-action-panel'), form = el('form', 'nb-form bty-form');
      panel.append(el('h3', '', cancellation ? 'Cancel bounty' : 'Withdraw bounty proposal'), el('p', 'bty-form-hint', cancellation ? 'Boosts return to their original month; expired allowance cannot be spent. The allowance used to publish this task is not returned.' : 'A pending reservation returns to its original month. An expired allowance cannot be spent.'));
      attachForm(form, {reason: field('Reason', 'withdrawal-reason', draft.values.reason, {rows: 3, max: 2000})}, draft, key);
      formActions(form, key, cancellation ? 'Confirm cancellation' : 'Confirm withdrawal', () => mutate(key, () => ({expected_version: task.version, reason: draft.values.reason.trim()}), body => cancellation ? client.cancel(task.id, body) : client.withdraw(task.id, body), result => { drafts.delete(key); saved(result, cancellation ? 'Bounty cancelled.' : 'Bounty proposal withdrawn.'); }));
      panel.append(form); return panel;
    }
    function decisionForm(task, submission, kind) {
      const key = 'decision:' + task.id + ':' + submission.id + ':' + kind;
      const draft = draftFor(key, {decision: 'approved', reason: ''}), form = el('form', 'nb-form bty-form');
      const fields = {decision: field('Decision', 'bounty-decision', draft.values.decision, {options: [['approved', 'Approve'], ['declined', 'Decline']]}), reason: field('Reason and evidence', 'bounty-decision-reason', draft.values.reason, {rows: 4, max: 6000})};
      attachForm(form, fields, draft, key);
      formActions(form, key, 'Record ' + (kind === 'poster' ? 'poster' : 'pipeline') + ' decision', () => mutate(key, () => ({expected_version: task.version, kind, decision: draft.values.decision, reason: draft.values.reason.trim()}), body => client.decision(task.id, submission.id, body), result => { drafts.delete(key); saved(result, 'Decision recorded.'); }));
      return form;
    }
    function paintContributions(body, task) {
      const submissions = task.submissions || [];
      if (!submissions.length) body.append(el('p', 'bty-empty-progress', 'No contributions yet.'));
      for (const item of submissions) {
        const row = el('article', 'bty-progress-item'); row.append(el('h3', '', item.claim === 'solution' ? 'Claimed solution' : 'Partial progress'));
        const author = el('a', 'bty-byline', item.author?.handle || 'Participant'); author.href = '#person/' + encodeURIComponent(item.author?.handle || '');
        row.append(author, el('p', 'bty-meta', ({pending:'Awaiting assessment', qualified:'Qualified for completion review', declined:'Not accepted', awarded:'Awarded', partial:'Partial progress'})[item.status] || item.status || ''), reference(item.contribution_note), el('p', 'bty-progress-summary', item.explanation || ''));
        const decisions = el('div', 'bty-approvals');
        for (const [kind, label] of [['poster', 'Poster'], ['pipeline', 'Pipeline']]) {
          const value = item[kind + '_decision']; const status = typeof value === 'string' ? value : value?.decision;
          decisions.append(el('span', status === 'approved' ? 'bty-approved' : '', label + ' · ' + (status || 'pending')));
        }
        row.append(decisions);
        if (item.capabilities?.accept || item.capabilities?.reject) row.append(decisionForm(task, item, 'poster'));
        if (reviewOnly && item.capabilities?.qualify) row.append(decisionForm(task, item, 'pipeline'));
        body.append(row);
      }
    }
    function admissionReview(task) {
      const key = 'review:' + task.id;
      const draft = draftFor(key, {decision: 'approved', reason: '', scope: false, evidence: false, verification: false, duplicates: false, ownership: false});
      const panel = el('section', 'bty-action-panel'), form = el('form', 'nb-form bty-form'); panel.append(el('h3', '', 'Admission review'));
      attachForm(form, {decision: field('Decision', 'admission-decision', draft.values.decision, {options: [['approved', 'Admit bounty'], ['changes_requested', 'Request changes'], ['declined', 'Decline']]}), reason: field('Review findings', 'admission-reason', draft.values.reason, {rows: 4, max: 6000})}, draft, key);
      for (const [name, label] of [['scope', 'Task scope and completion criteria'], ['evidence', 'Exact source evidence is accessible'], ['verification', 'Verification plan can assess completion'], ['duplicates', 'Duplicates and existing answers checked'], ['ownership', 'Responsible poster and independent reviewer']]) {
        const wrap = el('label', 'nb-check'), input = el('input'); input.type = 'checkbox'; input.checked = draft.values[name]; input.disabled = pendingWrites.has(key);
        input.addEventListener('change', () => { draft.values[name] = input.checked; draft.dirty = true; }); wrap.append(input, el('span', '', label)); form.append(wrap);
      }
      formActions(form, key, 'Record admission decision', () => mutate(key, () => ({...exactContext(task), decision: draft.values.decision, reason: draft.values.reason.trim(), checks: Object.fromEntries(['scope', 'evidence', 'verification', 'duplicates', 'ownership'].map(name => [name, draft.values[name]]))}), body => client.admissionDecision(task.id, body), result => { drafts.delete(key); saved(result, 'Admission decision recorded.'); }));
      panel.append(form); return panel;
    }
    function paintDetail(result) {
      const task = result.bounty; selected = task; policy = result.policy || policy; reader.removeAttribute('aria-busy');
      reader.querySelectorAll('.bty-read-error').forEach(node => node.remove());
      const nextSignature = JSON.stringify([state.detail, task, policy, action]);
      if (signature === nextSignature && reader.childElementCount) { focusAfter(); return; }
      // A quiet read must never overwrite an unsaved or uncertain decision.
      if (reader.querySelector('form') && signature && action === null && state.detail !== 'contribute') return;
      signature = nextSignature; reader.replaceChildren();
      reader.append(button('← ' + (reviewOnly ? 'Bounty reviews' : state.status === 'mine' ? 'Your activity' : state.status === 'past' ? 'Past bounties' : 'Research bounties'), () => change({id: '', detail: 'brief'}, task.id), 'nb-link bty-reader-back'));
      const article = el('article', 'bty-detail'), heading = el('div', 'bty-detail-heading'), title = el('h2', '', task.title); title.tabIndex = -1; title.dataset.bountyFocus = 'reader-title';
      heading.append(el('p', 'bty-meta', statuses[task.status] || task.status), title, el('p', 'bty-byline', 'Posted by ' + task.owner.handle + ' · brief revision ' + (task.revision || 1)));
      const tabs = el('nav', 'bty-detail-tabs'); tabs.setAttribute('aria-label', 'Bounty information');
      for (const [name, label] of [['brief', 'The task'], ['notes', 'Starting notes'], ['contributions', 'Contributions']]) {
        const tab = button(label, () => change({detail: name}, name), 'nb-link'); tab.dataset.bountyFocus = name;
        tab.setAttribute('aria-pressed', String(state.detail === name || name === 'contributions' && state.detail === 'contribute')); tabs.append(tab);
      }
      const body = el('div', 'bty-detail-body'); article.append(heading, tabs, body); reader.append(article);
      if (state.detail === 'notes') {
        const pins = task.sources || task.terms.source_notes || [];
        pins.forEach(pin => body.append(reference(pin)));
        if (!pins.length) body.append(el('p', 'bty-empty-progress', 'No starting notes are linked.'));
      }
      else if (state.detail === 'contribute' && task.capabilities.submit) body.append(contributionForm(task));
      else if (state.detail === 'contributions') paintContributions(body, task);
      else {
        const facts = el('div', 'bty-task-facts'); facts.append(spectrum(task.scope), reward(task.reward_points)); body.append(facts, el('p', 'bty-context', task.summary), section('Completion criteria', task.terms.completion_criteria), section('How it is verified', task.terms.verification_plan));
        const actions = el('div', 'bty-actions');
        if (task.capabilities.edit) actions.append(button('Edit draft', () => change({compose: true}, 'bounty-title')));
        if (task.capabilities.request_admission) actions.append(button('Submit for review', () => { void beginAction('admission'); }));
        if (task.capabilities.boost) actions.append(button('Boost this bounty', () => { void beginAction('boost'); }, 'nb-button nb-primary'));
        if (task.capabilities.submit) actions.append(button('Link a contribution', () => change({detail: 'contribute'}, 'contribution-note'), 'nb-link'));
        if (task.capabilities.cancel) actions.append(button('Cancel bounty', () => { action = 'cancel'; signature = ''; paintDetail({bounty: task}); }, 'nb-link'));
        if (task.capabilities.withdraw) actions.append(button('Withdraw proposal', () => { action = 'withdraw'; signature = ''; paintDetail({bounty: task}); }, 'nb-link'));
        if (!reviewOnly) actions.append(button('Copy agent prompt', () => { void copyAgentPrompt(); }, 'nb-link'));
        body.append(actions);
        if (action) body.append(['withdraw', 'cancel'].includes(action) ? withdrawalForm(task, action === 'cancel') : confirmAction(task, action));
        if (reviewOnly && task.capabilities.review_admission) body.append(admissionReview(task));
        if (task.decisions?.length) {
          const history = el('details', 'bty-decision-history'); history.append(el('summary', '', 'Review history'));
          for (const decision of task.decisions) history.append(section((({admission:'Admission', poster:'Poster', pipeline:'Pipeline'})[decision.kind] || 'Review') + ' · ' + decision.decision.replaceAll('_', ' '), decision.reason));
          body.append(history);
        }
        if (task.award) {
          const winner = task.submissions?.find(item => item.id === task.award.submission_id)?.author;
          body.append(section('Recorded award', `${task.award.points} points${winner ? ' · ' + winner.handle : ''}`));
        }
      }
      focusAfter();
    }
    async function loadDetail() {
      const token = request, id = state.id;
      let cached;
      try {
        cached = client.cachedDetail(id);
        if (state.compose) placeholder(composer);
        else if (cached && !reviewOnly) paintDetail(cached);
        else { signature = ''; placeholder(reader); }
        const result = await client.detail(id, {refresh: reviewOnly || state.compose});
        if (!present() || token !== request || state.id !== id) return;
        if (state.compose && result.bounty.capabilities.edit) { selected = result.bounty; policy = result.policy || policy; proposalForm(selected); }
        else paintDetail(result);
      } catch (error) { if (present() && token === request) errorView(state.compose ? composer : reader, error, () => { signature = ''; void loadDetail(); }, cached); }
    }
    async function show(params) {
      if (!owns()) return;
      active = true; request++; clearTimeout(pageTimer); pageTimer = null;
      const next = {status: ['mine', 'past'].includes(params.get('status')) ? params.get('status') : 'open', sort: Object.hasOwn(sorts, params.get('sort')) ? params.get('sort') : 'concrete', page: Math.max(1, Math.min(1000, parseInt(params.get('page'), 10) || 1)), id: params.get('bounty') || '', detail: ['notes', 'contributions', 'contribute'].includes(params.get('detail')) ? params.get('detail') : 'brief', compose: !reviewOnly && params.get('compose') === 'bounty'};
      if (JSON.stringify(next) !== JSON.stringify(state)) { signature = ''; action = null; }
      state = next;
      const inReader = Boolean(state.id) && !state.compose;
      board.hidden = inReader || state.compose; reader.hidden = !inReader; composer.hidden = !state.compose; heading.hidden = inReader || state.compose;
      title.textContent = reviewOnly ? 'Bounty reviews' : state.status === 'mine' ? 'Your activity' : state.status === 'past' ? 'Past bounties' : 'Research bounties';
      propose.hidden = reviewOnly; secondary.hidden = reviewOnly; sortField.wrap.hidden = reviewOnly || state.status !== 'open'; sortField.input.value = state.sort;
      mine.hidden = past.hidden = reviewOnly || state.status !== 'open'; open.hidden = reviewOnly || state.status === 'open';
      void loadAllowance();
      if (state.compose && !state.id) proposalForm();
      else if (state.id) await loadDetail();
      else await loadList();
    }
    function hide() { active = false; request++; clearTimeout(pageTimer); pageTimer = null; }
    function invalidateReads() {
      client.invalidateReads(); signature = '';
      // Invalidation is passive: page retirement and other workspace writes must
      // not launch new reads. The next owned show refreshes the exact route; a
      // valid write receipt retains independent route/session ownership.
    }
    const beforeUnload = event => { if (owns() && ([...drafts.values()].some(draft => draft.dirty) || pendingWrites.size)) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', beforeUnload);
    return {show, hide, route: () => route(), invalidateReads,
      async warm() { if (!owns() || !canWarm()) return {bytes: 0}; const result = await client.list({status: 'open', sort: 'concrete', page: 1}); return {bytes: client.bytes(result)}; },
      destroy() { hide(); destroyed = true; client.invalidateReads(); drafts.clear(); pendingWrites.clear(); completedWrites.clear(); window.removeEventListener('beforeunload', beforeUnload); root.remove(); }};
  }
  window.CrucibleBounties = {mount};
})();

/* Production bounty view. Server records own terms, allowances and decisions. */
(() => {
  'use strict';
  const sorts = {concrete: 'More concrete first', open: 'More open-ended first', 'points-high': 'Highest points first', 'points-low': 'Lowest points first'};
  const statuses = {draft: 'Draft', submitted: 'Submitted for review', pending: 'Submitted for review', changes_requested: 'Changes requested', open: 'Active', under_review: 'Under assessment', frozen: 'Under assessment', completed: 'Completed', closed: 'Completed', withdrawn: 'Withdrawn', declined: 'Not admitted', cancelled: 'Cancelled'};
  const el = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = String(text); return node; };
  function button(text, action, cls = 'nb-button') {
    const node = el('button', cls, text); node.type = 'button'; node.addEventListener('click', action); return node;
  }
  let fieldNumber = 0;
  function field(label, name, value, {rows, options, max = 2000, required = true, placeholder} = {}) {
    const wrap = el('label', 'nb-field'), input = el(options ? 'select' : rows ? 'textarea' : 'input');
    const labelRow = el('span'), caption = el('span', '', label); caption.id = 'bounty-field-' + ++fieldNumber; labelRow.append(caption);
    if (required) { const mark = el('span', 'nb-required-marker', ' *'); mark.setAttribute('aria-hidden', 'true'); labelRow.append(mark); }
    wrap.append(labelRow); input.name = name; input.required = required; input.setAttribute('aria-labelledby', caption.id);
    if (rows) input.rows = rows;
    if (!options) input.maxLength = max;
    if (placeholder) input.placeholder = placeholder;
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
    let nextFocus = null, pageTimer = null, action = null, pickerNumber = 0;
    let boostContext = null, boostEpoch = 0, boostBackdrop = null;
    const drafts = new Map(), pendingWrites = new Map(), completedWrites = new Map();
    const owns = () => !destroyed && current();
    const present = () => active && owns();
    const root = el('div', 'bty-root'); host.replaceChildren(root);
    const heading = el('div', 'bty-heading'), title = el(reviewOnly ? 'h2' : 'h1', '', 'Bounties'); title.tabIndex = -1;
    const titleRow = el('div', 'bty-title-row'); titleRow.append(title);
    const headingActions = el('div', 'bty-heading-actions'), allowanceNode = el('span', 'bty-allowance');
    const propose = button('Propose a bounty', () => change({compose: true, id: ''}, 'bounty-title'), 'nb-button nb-primary');
    headingActions.append(allowanceNode, propose); heading.append(titleRow, headingActions);
    const notice = el('div', 'bty-notice'); notice.setAttribute('role', 'status');
    const board = el('section'), listHeading = el('div', 'bty-list-heading'), count = el('span', 'bty-count');
    const sortField = field('Sort by', 'bounty-sort', 'concrete', {options: Object.entries(sorts), required:false}); sortField.wrap.classList.add('bty-sort');
    sortField.input.addEventListener('change', () => change({sort: sortField.input.value, page: 1}, 'bounty-sort'));
    listHeading.append(count, sortField.wrap);
    const entries = el('div', 'bty-grid'), pagination = el('nav', 'bty-pagination'); pagination.setAttribute('aria-label', 'Bounty pages');
    const secondary = el('nav', 'bty-secondary'); secondary.setAttribute('aria-label', 'Bounty collections');
    const past = button('Past bounties', () => change({status: 'past', page: 1}, 'heading'), 'bty-secondary-link');
    const open = button('← Bounties', () => change({status: 'open', page: 1}, 'heading'), 'bty-secondary-link');
    secondary.append(past, open); board.append(listHeading, entries, pagination, secondary);
    const reader = el('section', 'bty-reader'), composer = el('section', 'bty-composer'); reader.hidden = composer.hidden = true;
    const boostDialog = el('dialog', 'nb-dialog bty-boost-dialog'); boostDialog.setAttribute('aria-label', 'Boost bounty');
    root.append(heading, notice, board, reader, composer, boostDialog);
    function announce(text, error = false) { notice.replaceChildren(); notice.classList.toggle('nb-sr-only', !error); if (text) notice.append(el('p', error ? 'bty-error' : 'nb-notice', text)); }
    function route(next = state) {
      const params = new URLSearchParams(reviewOnly ? {section: 'bounties'} : {});
      if (next.status && next.status !== 'open') params.set('status', next.status);
      if (next.sort && next.sort !== 'concrete') params.set('sort', next.sort);
      if (next.page > 1) params.set('page', String(next.page));
      if (next.id) params.set('bounty', next.id);
      if (next.detail && next.detail !== 'brief') params.set('detail', next.detail);
      if (next.compose) params.set('compose', 'bounty');
      return (reviewOnly ? '#reviews' : '#bounties') + (params.size ? '?' + params : '');
    }
    function change(patch, focus) {
      if (!present()) return;
      nextFocus = focus; action = null; navigate(route({...state, ...patch}));
    }
    function receiptSelection(task) {
      // Private proposals belong to the owner's activity, not the open board.
      const collection = !reviewOnly && ['draft', 'pending', 'changes_requested', 'withdrawn', 'declined'].includes(task.status) && state.status !== 'mine'
        ? {status: 'mine', page: 1} : {};
      return {...collection, id: task.id, compose: false, detail: 'brief'};
    }
    function focusAfter() {
      if (!nextFocus || !present()) return;
      const node = nextFocus === 'heading' ? title : root.querySelector('[data-bounty-focus="' + nextFocus + '"]') || root.querySelector('[name="' + nextFocus + '"]');
      const target = node && !node.disabled ? node : title;
      const actionTransition = nextFocus.startsWith('action-');
      target.focus({preventScroll: true}); nextFocus = null; settleScroll();
      if (actionTransition) (target.closest('.bty-action-panel') || target).scrollIntoView({block: 'nearest'});
    }
    function updateAllowance() {
      updateAllowanceActions();
      const stamp = JSON.stringify(allowance);
      if (allowanceNode.dataset.stamp === stamp) return;
      allowanceNode.dataset.stamp = stamp; allowanceNode.replaceChildren();
      if (reviewOnly || !allowance) return;
      allowanceNode.append(el('span', '', 'Monthly bounty allowance'), el('strong', '', `${allowance.available_units} of ${allowance.monthly_units} remaining`));
      allowanceNode.title = 'Unused allowance expires at the end of each UTC calendar month. Refunds return to their original month.';
      if (allowance.policy_status === 'pending') allowanceNode.append(el('small', '', 'Allocation policy pending'));
    }
    function lacksAllowance(cost) {
      return Number.isSafeInteger(allowance?.available_units) && Number.isSafeInteger(cost) && allowance.available_units < cost;
    }
    function setAllowanceDisabled(control, disabled) {
      control.disabled = disabled;
      if (disabled) control.title = allowance.available_units === 0 ? 'No monthly bounty allowance remaining.' : 'Not enough monthly bounty allowance remaining.';
      else control.removeAttribute('title');
    }
    function updateAllowanceActions() {
      setAllowanceDisabled(propose, lacksAllowance(policy?.proposal_cost_units));
      root.querySelectorAll('[data-boost-task]').forEach(control => {
        const key = 'boost:' + control.dataset.boostTask;
        setAllowanceDisabled(control, lacksAllowance(policy?.boost_cost_units) && !pendingWrites.has(key) && !completedWrites.has(key));
      });
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
      if (signature === nextSignature && entries.childElementCount) { updateAllowanceActions(); focusAfter(); return; }
      signature = nextSignature; entries.replaceChildren(); pagination.replaceChildren();
      const openBoard = state.status === 'open' && !reviewOnly;
      entries.classList.toggle('bty-grid', openBoard); entries.classList.toggle('bty-management-list', !openBoard);
      count.textContent = reviewOnly ? 'Awaiting review' : state.status === 'mine' ? 'Your activity' : state.status === 'past' ? 'Past bounties' : 'Active bounties';
      if (!result.bounties.length) entries.append(el('p', 'bty-no-results', reviewOnly ? 'No bounty reviews are waiting.' : state.status === 'mine' ? 'Your proposals and contributions will appear here.' : state.status === 'past' ? 'No completed bounties yet.' : 'No active bounties yet.'));
      for (const item of result.bounties) {
        const openTask = button('', () => change({id: item.id, detail: 'brief'}, 'reader-title'), openBoard ? 'bty-card-open' : 'bty-management-row');
        const card = openBoard ? el('article', 'nb-card bty-question') : openTask;
        card.dataset.bounty = item.id; openTask.dataset.bountyFocus = item.id;
        const title = el('span', 'bty-question-title', item.title);
        if (openBoard) {
          const cardHeading = el('span', 'nb-title-copy bty-card-heading'); cardHeading.append(title);
          const overline = el('div', 'bty-card-overline'); overline.append(reward(item.reward_points));
          const tags = el('span', 'bty-skills'); item.background_tags.forEach(tag => tags.append(el('span', '', tag)));
          const footer = el('div', 'bty-card-foot'), rewards = el('div', 'bty-card-actions');
          if (item.status === 'open' && item.capabilities.boost === true) {
            const boost = button('Boost', () => { void openBoostDialog(item, boost); }, 'nb-button bty-card-boost');
            boost.dataset.boostTask = item.id;
            boost.dataset.bountyFocus = 'boost:' + item.id;
            boost.setAttribute('aria-label', 'Boost bounty: ' + item.title); rewards.append(boost);
          }
          footer.append(spectrum(item.scope), rewards);
          openTask.append(cardHeading, el('span', 'bty-question-summary', item.summary), tags);
          card.append(overline, openTask, footer);
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
      updateAllowanceActions(); focusAfter();
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
    function sourceNotePicker(form, draft, key) {
      const wrap = el('div', 'nb-field bty-note-picker'), label = el('label'), input = el('input');
      const identifier = 'bounty-source-' + ++pickerNumber, ownerRoute = route();
      input.id = identifier; input.name = 'bounty-sources'; input.type = 'search'; input.maxLength = 200;
      input.value = draft.sourceQuery || ''; input.placeholder = 'Search notes by title or #ID…';
      input.disabled = pendingWrites.has(key); input.setAttribute('autocomplete', 'off');
      input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-haspopup', 'listbox'); input.setAttribute('aria-expanded', 'false');
      label.setAttribute('for', identifier); label.textContent = 'Source-note citations';
      const tags = el('div', 'bty-source-tags'); tags.setAttribute('role', 'list'); tags.setAttribute('aria-label', 'Selected source notes');
      const help = el('p', 'bty-note-picker-help', 'Select up to 12 notes. Source notes must be published before you submit the bounty for review.');
      help.id = identifier + '-help'; input.setAttribute('aria-describedby', help.id);
      const popup = el('div', 'bty-note-dropdown'), list = el('div', 'bty-note-options'), status = el('p', 'bty-note-search-status');
      popup.hidden = true; list.id = identifier + '-options'; list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', 'Source notes');
      input.setAttribute('aria-controls', list.id); status.setAttribute('role', 'status');
      const more = button('Show more notes', () => { if (editable()) { input.focus({preventScroll: true}); void load(true); } }, 'nb-link bty-note-more');
      const retry = button('Try note search again', () => { if (editable()) { input.focus({preventScroll: true}); void load(); } }, 'nb-link bty-note-more');
      more.dataset.sourceControl = retry.dataset.sourceControl = 'true';
      for (const control of [more, retry]) control.addEventListener('pointerdown', event => event.preventDefault());
      popup.append(list, status, more, retry); wrap.append(label, input, tags, help, popup);
      let records = [], next = null, activeIndex = -1, generation = 0, timer = null, busy = false, failure = '', rows = [];
      const current = () => present() && form.isConnected && route() === ownerRoute;
      const editable = () => current() && !input.disabled && !pendingWrites.has(key) && !completedWrites.has(key);
      const noteLabel = note => Number.isSafeInteger(note.number) && note.number > 0 ? '#' + String(note.number).padStart(4, '0') : note.note_id;
      function close() {
        generation++; clearTimeout(timer); timer = null; popup.hidden = true;
        input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
      }
      function paintTags() {
        tags.replaceChildren();
        for (const pin of draft.values.sources) {
          const tag = el('span', 'bty-source-tag'); tag.setAttribute('role', 'listitem');
          const text = el('span', 'bty-source-id', noteLabel(pin)); text.title = (pin.title || pin.note_id) + ' · Revision ' + pin.revision;
          const remove = button('×', () => {
            if (!editable() || !remove.isConnected) return;
            draft.values.sources = draft.values.sources.filter(source => source !== pin); draft.dirty = true;
            paintTags(); input.focus({preventScroll: true}); if (!popup.hidden) void load();
          }, 'bty-source-remove');
          remove.dataset.sourceControl = 'true'; remove.disabled = input.disabled;
          remove.setAttribute('aria-label', 'Remove source ' + noteLabel(pin) + ', revision ' + pin.revision);
          remove.addEventListener('pointerdown', event => event.preventDefault());
          tag.append(text, remove); tags.append(tag);
        }
        tags.hidden = !draft.values.sources.length;
      }
      function highlight(index) {
        activeIndex = index;
        rows.forEach((row, position) => row.setAttribute('aria-selected', String(position === index)));
        if (rows[index]) { input.setAttribute('aria-activedescendant', rows[index].id); rows[index].scrollIntoView({block: 'nearest'}); }
        else input.removeAttribute('aria-activedescendant');
      }
      function choose(note, row) {
        if (!editable() || popup.hidden || !row.isConnected || draft.values.sources.length >= 12 ||
            draft.values.sources.some(pin => pin.note_id === note.note_id)) return;
        draft.values.sources.push({...note}); draft.dirty = true; draft.sourceQuery = ''; input.value = '';
        paintTags(); input.focus({preventScroll: true}); void load();
      }
      function paint() {
        list.replaceChildren(); rows = []; activeIndex = -1; input.removeAttribute('aria-activedescendant');
        const selected = new Set(draft.values.sources.map(pin => pin.note_id));
        for (const note of records.filter(note => !selected.has(note.note_id))) {
          const row = button('', () => choose(note, row), 'bty-note-option'); row.setAttribute('role', 'option'); row.tabIndex = -1;
          row.setAttribute('aria-label', noteLabel(note) + ' ' + (note.title || 'Untitled note') + ', revision ' + note.revision);
          row.id = identifier + '-option-' + rows.length; row.setAttribute('aria-selected', 'false'); row.dataset.sourceControl = 'true';
          row.disabled = input.disabled || draft.values.sources.length >= 12;
          const name = el('span', 'bty-note-option-title'); name.append(el('strong', '', noteLabel(note)), el('span', '', note.title || 'Untitled note'));
          row.append(name, el('small', '', 'Revision ' + note.revision + (note.publication_status === 'private' ? ' · Private draft' : '')));
          row.addEventListener('pointerdown', event => event.preventDefault());
          row.addEventListener('pointermove', () => { if (!row.disabled) highlight(rows.indexOf(row)); });
          rows.push(row); list.append(row);
        }
        list.setAttribute('aria-busy', String(busy));
        status.textContent = draft.values.sources.length >= 12 ? '12 notes selected. Remove a tag to add another.' : failure ||
          (busy ? 'Searching notes…' : rows.length ? '' : records.length ? 'These notes are already selected.' : input.value.trim() ? 'No matching notes.' : 'No notes are available.');
        more.hidden = !next || Boolean(failure) || draft.values.sources.length >= 12;
        more.disabled = busy || input.disabled; retry.hidden = !failure; retry.disabled = input.disabled;
      }
      async function load(append = false) {
        clearTimeout(timer); timer = null;
        if (!editable()) return;
        const token = ++generation, query = input.value.trim(), after = append ? next : null;
        popup.hidden = false; input.setAttribute('aria-expanded', 'true'); failure = ''; busy = true;
        if (!append) { records = []; next = null; }
        paint();
        if (draft.values.sources.length >= 12) { busy = false; paint(); return; }
        try {
          const result = await client.notes({query, after});
          if (!current() || input.disabled || token !== generation || query !== input.value.trim()) return;
          if (after && result.next_after === after) throw Error('The next note page could not be verified.');
          const known = new Set(records.map(note => note.note_id));
          records = [...records, ...result.notes.filter(note => !known.has(note.note_id))]; next = result.next_after;
          for (const pin of draft.values.sources) {
            const found = records.find(note => note.note_id === pin.note_id);
            if (found) { pin.number = found.number; if (!pin.title && pin.revision === found.revision) pin.title = found.title; }
          }
          busy = false; paintTags(); paint();
        } catch (error) {
          if (!current() || token !== generation) return;
          if (error.retired) { close(); return; }
          busy = false; records = []; next = null; failure = error.message || 'Notes could not be retrieved.'; paint();
        }
      }
      const open = () => { if (editable() && popup.hidden) void load(); };
      input.addEventListener('focus', open); input.addEventListener('click', open);
      input.addEventListener('input', () => {
        if (!editable()) return;
        draft.sourceQuery = input.value; generation++; clearTimeout(timer); records = []; next = null; busy = true; failure = '';
        popup.hidden = false; input.setAttribute('aria-expanded', 'true'); paint();
        timer = setTimeout(() => { if (editable()) void load(); }, 180);
      });
      input.addEventListener('keydown', event => {
        if (!editable()) return;
        if (event.key === 'Escape') { event.preventDefault(); close(); }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          if (popup.hidden) {
            const pending = load(), token = generation;
            void pending.then(() => { if (editable() && token === generation && !popup.hidden && rows.length) highlight(event.key === 'ArrowDown' ? 0 : rows.length - 1); });
            return;
          }
          if (rows.length && draft.values.sources.length < 12) highlight(activeIndex < 0 ? (event.key === 'ArrowDown' ? 0 : rows.length - 1) : (activeIndex + (event.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length);
        } else if (event.key === 'Enter') { event.preventDefault(); if (!popup.hidden && rows[activeIndex]) rows[activeIndex].click(); else open(); }
      });
      wrap.addEventListener('focusout', event => { if (event.relatedTarget?.closest?.('.bty-note-picker') !== wrap) close(); });
      form.addEventListener('submit', close); paintTags();
      if (draft.values.sources.some(pin => !Number.isSafeInteger(pin.number))) {
        // Resolve permanent display numbers without loading note bodies or
        // changing the exact revisions already selected in the saved draft.
        void client.noteIndex().then(result => {
          if (!current() || input.disabled) return;
          for (const pin of draft.values.sources) {
            const found = result.notes.find(note => note.note_id === pin.note_id);
            if (found) pin.number = found.number;
          }
          paintTags();
        }).catch(() => {}); // The full saved note ID remains a valid label.
      }
      return wrap;
    }
    function formActions(form, key, label, run) {
      if (completedWrites.has(key)) {
        const receipt = completedWrites.get(key);
        for (const input of form.querySelectorAll('input, textarea, select, [data-source-control]')) input.disabled = true;
        form.append(el('p', 'nb-notice', 'This request was saved while you were browsing.'), button('View bounty', () => {
          completedWrites.delete(key); drafts.delete(key); change(receiptSelection(receipt.bounty), 'reader-title');
        }));
        return;
      }
      const controls = el('div', 'nb-form-actions'), error = el('p', 'bty-error'); error.setAttribute('role', 'alert');
      const save = el('button', 'nb-button nb-primary', pendingWrites.has(key) ? 'Retry the same request' : label); save.type = 'submit';
      controls.append(save); form.append(error, controls);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (!present() || save.disabled) return;
        save.disabled = true; error.textContent = '';
        for (const input of form.querySelectorAll('input, textarea, select, [data-source-control]')) input.disabled = true;
        try { await run(error); }
        catch (failure) { if (present() && !failure.retired) error.textContent = failure.message || 'This request could not be completed.'; }
        finally {
          if (present() && save.isConnected) { save.disabled = false; save.textContent = pendingWrites.has(key) ? 'Retry the same request' : label; for (const input of form.querySelectorAll('input, textarea, select, [data-source-control]')) input.disabled = pendingWrites.has(key); }
        }
      });
    }
    async function mutate(key, make, perform, success) {
      // A hidden request may finish after a retry form has already reopened.
      // Consume its receipt before creating any new write identity.
      if (completedWrites.has(key)) {
        const receipt = completedWrites.get(key); completedWrites.delete(key); success(receipt); return;
      }
      let pending = pendingWrites.get(key);
      if (!pending) {
        pending = {body: {...make(), idempotency_key: crypto.randomUUID()}, uncertain: false};
        pendingWrites.set(key, pending);
      }
      const ownerRoute = route();
      try {
        const result = await perform(pending.body);
        if (!owns()) return;
        pendingWrites.delete(key); client.invalidateReads(); allowance = null;
        if (present() && ownerRoute === route()) { policy = result.policy || policy; success(result); }
        else {
          completedWrites.set(key, result);
          if (drafts.has(key)) drafts.get(key).dirty = false;
        }
      } catch (error) {
        if (owns()) {
          // A later rejection cannot prove that an earlier lost response did
          // not commit. Preserve its identity until the receipt is recovered.
          if (!pending.uncertain && error.status >= 400 && error.status < 500) pendingWrites.delete(key);
          else pending.uncertain = true;
        }
        throw error;
      }
    }
    function saved(result, message) {
      selected = result.bounty; action = null; announce(message);
      change(receiptSelection(selected), 'reader-title');
    }
    function proposalForm(task = null) {
      const key = task ? 'edit:' + task.id : 'new';
      const terms = task?.terms || {};
      const sources = (terms.source_notes || []).map(pin => {
        const detail = task?.sources?.find(item => item.note_id === pin.note_id && item.revision === pin.revision && item.content_sha256 === pin.content_sha256);
        return {...pin, number: detail?.number ?? detail?.note_number ?? null, title: detail?.title || ''};
      });
      const draft = draftFor(key, {title: terms.title || '', summary: terms.summary || '', tags: (terms.background_tags || []).join(', '), scope: String(terms.scope || 3), completion: terms.completion_criteria || '', verification: terms.verification_plan || '', sources});
      if (task && !Number.isInteger(draft.baseVersion)) draft.baseVersion = task.version;
      composer.replaceChildren(button('← Bounties', () => change({compose: false, detail: 'brief'}, 'heading'), 'nb-link'));
      const heading = el(reviewOnly ? 'h2' : 'h1', '', task ? 'Edit bounty draft' : 'Propose a bounty'); composer.append(heading);
      if (task && draft.baseVersion !== task.version && !pendingWrites.has(key)) {
        const reset = button('Discard local edits and load the latest draft', () => { drafts.delete(key); proposalForm(task); });
        composer.append(el('p', 'bty-form-hint', 'This draft changed elsewhere. Your local edits remain here until you choose to replace them.'), reset);
      }
      const form = el('form', 'nb-form bty-form'), v = draft.values;
      const fields = {
        title: field('The task', 'bounty-title', v.title, {max: 160, placeholder:'e.g. Reproduce the reference attack for a scheme'}),
        summary: field('A short description', 'bounty-summary', v.summary, {rows: 3, max: 1200, placeholder:'What should someone investigate, and why is it useful?'}),
        completion: field('What counts as completion?', 'bounty-completion', v.completion, {rows: 4, max: 6000, placeholder:'Describe the result, evidence, or artifact that would complete this task.'}),
        verification: field('How will it be verified?', 'bounty-verification', v.verification, {rows: 4, max: 6000, placeholder:'Explain how someone else can check the result or reproduce the work.'}),
        tags: field('Useful background', 'bounty-tags', v.tags, {max: 300, required: false, placeholder:'e.g. lattices, probability, Python'}),
        scope: field('Open-ended → Concrete', 'bounty-scope', v.scope, {options: [1, 2, 3, 4, 5].map(n => [String(n), n + ' / 5'])})
      };
      attachForm(form, {title: fields.title, summary: fields.summary, completion: fields.completion, verification: fields.verification}, draft, key);
      form.append(sourceNotePicker(form, draft, key));
      attachForm(form, {tags: fields.tags, scope: fields.scope}, draft, key);
      form.append(el('p', 'bty-form-hint', 'Saving a private draft uses no allowance; requesting admission is a separate step.'));
      formActions(form, key, 'Save private draft', async () => {
        await mutate(key, () => {
          const v = draft.values;
          return {...(task ? {expected_version: draft.baseVersion} : {}), terms: {title: v.title.trim(), summary: v.summary.trim(), background_tags: v.tags.split(',').map(t => t.trim()).filter(Boolean), scope: Number(v.scope), completion_criteria: v.completion.trim(), verification_plan: v.verification.trim(), source_notes: v.sources.map(({note_id, revision, content_sha256}) => ({note_id, revision, content_sha256}))}};
        }, body => task ? client.revise(task.id, body) : client.create(body), result => { drafts.delete(key); saved(result, 'Private bounty draft saved.'); });
      });
      composer.append(form); focusAfter();
    }
    function exactContext(task) { return {expected_version: task.version, terms_sha256: task.terms_sha256}; }
    function actionButton(text, kind, run, cls) {
      const trigger = button(text, run, cls); trigger.dataset.bountyFocus = 'action-' + kind;
      if (kind === 'boost') {
        trigger.dataset.boostTask = state.id;
        setAllowanceDisabled(trigger, lacksAllowance(policy?.boost_cost_units) && !pendingWrites.has('boost:' + state.id) && !completedWrites.has('boost:' + state.id));
      }
      return trigger;
    }
    function showAction(result, kind) {
      action = kind; signature = ''; nextFocus = 'action-panel'; paintDetail(result);
    }
    function closeAction(task, kind) {
      action = null; signature = ''; nextFocus = 'action-' + kind; paintDetail({bounty: task});
    }
    function confirmAction(task, kind, options = {}) {
      const key = kind + ':' + task.id;
      const recovering = kind === 'boost' && (pendingWrites.has(key) || completedWrites.has(key));
      const panel = el('section', 'bty-action-panel'), form = el('form', 'nb-form bty-form');
      let text, cost;
      if (kind === 'boost') {
        cost = policy?.boost_cost_units;
        text = Number.isSafeInteger(policy?.points_per_boost) ? `Use ${cost} of your monthly bounty allowance to add ${policy.points_per_boost} reward points.` : 'The bounty allocation policy is not ready.';
      } else {
        cost = policy?.proposal_cost_units;
        text = Number.isSafeInteger(cost) ? `Reserve ${cost} of your monthly bounty allowance while this proposal is reviewed. Approval starts its ${policy?.proposal_reward_points ?? policy?.default_base_reward_points ?? policy?.points_per_boost}-point reward. If declined or withdrawn before admission, the reservation returns to its original month; expired allowance cannot be spent.` : 'The bounty allocation policy is not ready.';
      }
      if (recovering) text = 'Check the result of your previous boost request. This reuses its original request; it does not create an additional boost.';
      const insufficient = kind === 'boost' && !recovering && lacksAllowance(cost);
      if (insufficient) text = 'No monthly bounty allowance remains for this boost.';
      if (kind !== 'boost' && task.reservation?.status === 'held') text = `Your existing reservation of ${task.reservation.units} remains attached to this proposal. Submitting the revised brief uses no additional allowance.`;
      if (!options.modal) panel.append(el('h3', '', kind === 'boost' ? 'Confirm bounty boost' : 'Request bounty admission'));
      panel.append(el('p', 'bty-form-hint', text));
      if (task.pending_claim_count > 0 && kind === 'boost') panel.append(el('p', 'bty-form-hint', 'A solution is awaiting assessment. Your boost will return to its original month if an earlier submission receives the award; expired allowance cannot be spent.'));
      const cancel = button('Cancel', () => options.close ? options.close() : closeAction(task, kind));
      const canConfirm = !insufficient && (recovering || policy?.status === 'ready' && Number.isSafeInteger(cost));
      if (!options.modal || !canConfirm) panel.append(cancel);
      if (canConfirm) {
        const policyId = policy?.id;
        formActions(form, key, kind === 'boost' ? 'Confirm boost' : 'Submit for review', async () => {
          if (options.allowed && !options.allowed()) return;
          options.busy?.(true);
          try { await mutate(key, () => ({...exactContext(task), policy_id: policyId, confirm_cost_units: cost, ...(kind === 'boost' ? {units: 1} : {})}),
            body => kind === 'boost' ? client.boost(task.id, body) : client.admission(task.id, body), options.success || (result => saved(result, kind === 'boost' ? 'Bounty boost recorded.' : 'Bounty submitted for review.'))); }
          finally { options.busy?.(false); }
        });
        panel.append(form);
        if (options.modal) form.querySelector('.nb-form-actions')?.prepend(cancel);
      }
      return panel;
    }
    function closeBoostDialog(force = false) {
      if (boostContext?.writing && !force) return;
      const trigger = boostContext?.trigger;
      boostContext = null; boostEpoch++; boostBackdrop = null;
      if (boostDialog.open) boostDialog.close();
      boostDialog.replaceChildren(); boostDialog.removeAttribute('aria-busy');
      if (!force && present() && trigger?.isConnected) trigger.focus({preventScroll: true});
    }
    async function openBoostDialog(task, trigger) {
      if (!present() || reviewOnly || boostDialog.open) return;
      const ownerRoute = route(), epoch = ++boostEpoch;
      const context = {trigger, writing: false}; boostContext = context;
      const valid = () => present() && boostDialog.open && boostContext === context && boostEpoch === epoch && route() === ownerRoute;
      const heading = el('div', 'nb-dialog-heading'), title = el('h2', '', 'Confirm bounty boost'); title.tabIndex = -1;
      const close = button('×', () => closeBoostDialog(), 'nb-close'); close.setAttribute('aria-label', 'Close boost confirmation');
      heading.append(title, close);
      const body = el('div', 'bty-boost-body'), taskTitle = el('p', 'bty-boost-task', task.title);
      boostDialog.replaceChildren(heading, taskTitle, body); boostDialog.showModal(); title.focus({preventScroll: true});
      let preparing = false;
      async function prepare() {
        if (!valid() || preparing) return;
        preparing = true;
        body.replaceChildren(); body.setAttribute('aria-busy', 'true');
        try {
          const [result, wallet] = await Promise.all([client.detail(task.id, {refresh: true}), client.allowance({refresh: true})]);
          if (!valid()) return;
          const recovering = pendingWrites.has('boost:' + task.id) || completedWrites.has('boost:' + task.id);
          if (!recovering && (result.bounty.status !== 'open' || !result.bounty.capabilities.boost)) throw Error('This bounty can no longer be boosted.');
          allowance = wallet.allowance; policy = result.policy || wallet.policy || policy; updateAllowance(); taskTitle.textContent = result.bounty.title;
          const panel = confirmAction(result.bounty, 'boost', {modal: true, close: () => closeBoostDialog(), allowed: valid,
            busy(value) { if (valid()) { context.writing = value; close.disabled = value; boostDialog.setAttribute('aria-busy', String(value)); panel.querySelectorAll('button').forEach(control => { if (control.type !== 'submit') control.disabled = value; }); } },
            success(receipt) {
              if (!valid()) { completedWrites.set('boost:' + task.id, receipt); return; }
              closeBoostDialog(true); announce('Bounty boost recorded.');
              const card = entries.querySelector('[data-bounty="' + task.id + '"]');
              const points = card?.querySelector('.bty-points'); if (points) points.textContent = reward(receipt.bounty.reward_points).textContent;
              if (trigger?.isConnected) trigger.focus({preventScroll: true});
              void loadAllowance();
              void client.list(listOptions()).then(result => {
                if (!present() || route() !== ownerRoute) return;
                const focused = document.activeElement?.dataset?.bountyFocus;
                if (focused?.startsWith('boost:')) nextFocus = focused;
                paintList(result);
              }).catch(() => {});
            }});
          body.replaceChildren(panel);
        } catch (error) {
          if (!valid() || error.retired) return;
          const message = el('p', 'bty-error', error.message || 'The bounty could not be checked.'); message.setAttribute('role', 'alert');
          body.replaceChildren(message, button('Try again', () => { void prepare(); }), button('Cancel', () => closeBoostDialog()));
        } finally { preparing = false; if (valid()) body.removeAttribute('aria-busy'); }
      }
      await prepare();
    }
    boostDialog.addEventListener('cancel', event => { event.preventDefault(); closeBoostDialog(); });
    const outsideBoostDialog = event => {
      if (event.target !== boostDialog || !boostDialog.open) return false;
      const bounds = boostDialog.getBoundingClientRect();
      return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom;
    };
    boostDialog.addEventListener('pointerdown', event => { boostBackdrop = event.isPrimary && event.button === 0 && outsideBoostDialog(event) ? boostEpoch : null; });
    boostDialog.addEventListener('pointercancel', () => { boostBackdrop = null; });
    boostDialog.addEventListener('click', event => { const outside = boostBackdrop === boostEpoch; boostBackdrop = null; if (outside && outsideBoostDialog(event)) closeBoostDialog(); });
    async function beginAction(kind) {
      if (!present() || !selected) return;
      const id = selected.id, token = request;
      try {
        const [result, wallet] = await Promise.all([client.detail(id, {refresh: true}), client.allowance({refresh: true})]);
        if (!present() || request !== token || state.id !== id) return;
        allowance = wallet.allowance; policy = result.policy || wallet.policy || policy; updateAllowance();
        if (!result.bounty.capabilities[kind === 'boost' ? 'boost' : 'request_admission']) throw Error('This action is no longer available. Refresh the bounty.');
        showAction(result, kind);
      } catch (error) { if (present() && token === request && !error.retired) announce(error.message, true); }
    }
    function contributionForm(task) {
      const key = 'contribute:' + task.id;
      const draft = draftFor(key, {note: '', explanation: ''}), form = el('form', 'nb-form bty-form');
      const fields = {note: field('Exact solution-note citation', 'contribution-note', draft.values.note, {rows: 4, max: 6000}), explanation: field('What does it establish?', 'contribution-explanation', draft.values.explanation, {rows: 4, max: 6000})};
      attachForm(form, fields, draft, key);
      form.append(el('p', 'bty-form-hint', 'Link the published note containing your solution. An independent reviewer checks it against the task criteria.'));
      formActions(form, key, 'Submit solution', () => mutate(key, () => ({...exactContext(task), contribution_note: window.CrucibleBountyAPI.parseReferences(draft.values.note, {single: true})[0], claim: 'solution', explanation: draft.values.explanation.trim()}), body => client.submit(task.id, body), result => { drafts.delete(key); saved(result, 'Solution submitted for independent review.'); }));
      return form;
    }
    function withdrawalForm(task) {
      const key = 'withdraw:' + task.id, draft = draftFor(key, {reason: ''});
      const panel = el('section', 'bty-action-panel'), form = el('form', 'nb-form bty-form');
      panel.append(el('h3', '', 'Withdraw bounty proposal'), el('p', 'bty-form-hint', 'A pending reservation returns to its original month. An expired allowance cannot be spent.'));
      attachForm(form, {reason: field('Reason', 'withdrawal-reason', draft.values.reason, {rows: 3, max: 2000})}, draft, key);
      formActions(form, key, 'Confirm withdrawal', () => mutate(key, () => ({expected_version: task.version, reason: draft.values.reason.trim()}), body => client.withdraw(task.id, body), result => { drafts.delete(key); saved(result, 'Bounty proposal withdrawn.'); }));
      panel.append(button('Cancel', () => closeAction(task, 'withdraw')), form); return panel;
    }
    function decisionForm(task, submission) {
      const kind = 'pipeline';
      const key = 'decision:' + task.id + ':' + submission.id + ':' + kind;
      const draft = draftFor(key, {decision: 'approved', reason: ''}), form = el('form', 'nb-form bty-form');
      const fields = {decision: field('Decision', 'bounty-decision', draft.values.decision, {options: [['approved', 'Approve'], ['declined', 'Decline']]}), reason: field('Reason and evidence', 'bounty-decision-reason', draft.values.reason, {rows: 4, max: 6000})};
      attachForm(form, fields, draft, key);
      formActions(form, key, 'Record reviewer decision', () => mutate(key, () => ({expected_version: task.version, kind, decision: draft.values.decision, reason: draft.values.reason.trim()}), body => client.decision(task.id, submission.id, body), result => { drafts.delete(key); saved(result, 'Decision recorded.'); }));
      return form;
    }
    function paintContributions(body, task) {
      const submissions = task.submissions || [];
      if (!submissions.length) body.append(el('p', 'bty-empty-progress', 'No contributions yet.'));
      for (const item of submissions) {
        const row = el('article', 'bty-progress-item'); row.append(el('h3', '', item.claim === 'solution' ? 'Claimed solution' : 'Partial progress'));
        const author = el('a', 'bty-byline', item.author?.handle || 'Participant'); author.href = '#person/' + encodeURIComponent(item.author?.handle || '');
        row.append(author, el('p', 'bty-meta', ({pending:'Awaiting independent review', qualified:'Approved; awaiting settlement', declined:'Not accepted', awarded:'Awarded', partial:'Partial progress'})[item.status] || item.status || ''), reference(item.contribution_note), el('p', 'bty-progress-summary', item.explanation || ''));
        const value = item.pipeline_decision; const status = typeof value === 'string' ? value : value?.decision;
        if (status) {
          const decisions = el('div', 'bty-approvals');
          decisions.append(el('span', status === 'approved' ? 'bty-approved' : '', 'Independent reviewer · ' + status));
          row.append(decisions);
        }
        if (reviewOnly && item.capabilities?.qualify) row.append(decisionForm(task, item));
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
      reader.append(button('← ' + (reviewOnly ? 'Bounty reviews' : state.status === 'mine' ? 'Your activity' : state.status === 'past' ? 'Past bounties' : 'Bounties'), () => change({id: '', detail: 'brief'}, task.id), 'nb-link bty-reader-back'));
      const article = el('article', 'bty-detail'), heading = el('div', 'bty-detail-heading'), title = el(reviewOnly ? 'h2' : 'h1', '', task.title); title.tabIndex = -1; title.dataset.bountyFocus = 'reader-title';
      heading.append(el('p', 'bty-meta', statuses[task.status] || task.status), title, el('p', 'bty-byline', 'Posted by ' + task.owner.handle));
      const facts = el('div', 'bty-task-facts'); facts.append(reward(task.reward_points), spectrum(task.scope)); heading.append(facts);
      const tabs = el('nav', 'bty-detail-tabs'); tabs.setAttribute('aria-label', 'Bounty information');
      for (const [name, label] of [['brief', 'The task'], ['notes', 'Starting notes'], ...(reviewOnly ? [['contributions', 'Submissions']] : [])]) {
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
      else if (reviewOnly && state.detail === 'contributions') paintContributions(body, task);
      else {
        body.append(el('p', 'bty-context', task.summary), section('Completion criteria', task.terms.completion_criteria), section('How it is verified', task.terms.verification_plan));
        const actions = el('div', 'bty-actions');
        if (task.capabilities.edit) actions.append(button('Edit draft', () => change({compose: true}, 'bounty-title')));
        if (task.capabilities.request_admission) actions.append(actionButton('Submit for review', 'admission', () => { void beginAction('admission'); }));
        if (task.capabilities.boost) actions.append(actionButton('Boost this bounty', 'boost', () => { void beginAction('boost'); }, 'nb-button nb-primary'));
        if (task.capabilities.submit) actions.append(button('Submit a solution', () => change({detail: 'contribute'}, 'contribution-note'), 'nb-link'));
        if (task.capabilities.withdraw) actions.append(actionButton('Withdraw proposal', 'withdraw', () => showAction({bounty: task}, 'withdraw'), 'nb-link'));
        body.append(actions);
        if (action) {
          const panel = action === 'withdraw' ? withdrawalForm(task) : confirmAction(task, action);
          const heading = panel.querySelector('h3'); heading.tabIndex = -1; heading.dataset.bountyFocus = 'action-panel';
          body.append(panel);
        }
        if (reviewOnly && task.capabilities.review_admission) body.append(admissionReview(task));
        if (reviewOnly && task.decisions?.length) {
          const history = el('details', 'bty-decision-history'); history.append(el('summary', '', 'Review history'));
          for (const decision of task.decisions) history.append(section((({admission:'Admission', poster:'Historical proposer assessment', pipeline:'Independent reviewer'})[decision.kind] || 'Review') + ' · ' + decision.decision.replaceAll('_', ' '), decision.reason));
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
      const next = {status: ['mine', 'past'].includes(params.get('status')) ? params.get('status') : 'open', sort: Object.hasOwn(sorts, params.get('sort')) ? params.get('sort') : 'concrete', page: Math.max(1, Math.min(1000, parseInt(params.get('page'), 10) || 1)), id: params.get('bounty') || '', detail: ['notes', 'contribute', ...(reviewOnly ? ['contributions'] : [])].includes(params.get('detail')) ? params.get('detail') : 'brief', compose: !reviewOnly && params.get('compose') === 'bounty'};
      if (JSON.stringify(next) !== JSON.stringify(state)) { signature = ''; action = null; closeBoostDialog(true); }
      state = next;
      const inReader = Boolean(state.id) && !state.compose;
      board.hidden = inReader || state.compose; reader.hidden = !inReader; composer.hidden = !state.compose; heading.hidden = inReader || state.compose;
      title.textContent = reviewOnly ? 'Bounty reviews' : 'Bounties';
      propose.hidden = reviewOnly; secondary.hidden = reviewOnly; sortField.wrap.hidden = reviewOnly || state.status !== 'open'; sortField.input.value = state.sort;
      past.hidden = reviewOnly || state.status !== 'open'; open.hidden = reviewOnly || state.status === 'open';
      void loadAllowance();
      if (state.compose && !state.id) proposalForm();
      else if (state.id) await loadDetail();
      else await loadList();
    }
    function hide() { active = false; request++; closeBoostDialog(true); clearTimeout(pageTimer); pageTimer = null; }
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

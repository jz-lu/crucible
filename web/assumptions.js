// A read-only, release-scoped view. Notebook publication is a separate process.
(() => {
  'use strict';
  const roles = {
    hardness: 'Hardness problem', model: 'Model premise', derived: 'Mathematical consequence',
    reduction: 'Proof obligation', implementation: 'Implementation requirement'
  };
  const purposes = {security: 'Secrecy', correctness: 'Honest agreement'};
  function create({root, api, route, isVisible, account}) {
    const document = root.ownerDocument;
    let request = 0, disposeReader = () => {};
    function clearView() { disposeReader(); disposeReader = () => {}; root.replaceChildren(); }
    const el = (tag, text, cls) => {
      const node = document.createElement(tag);
      if (text != null) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    function prose(value, cls) {
      const p = el('p', null, cls);
      if (window.CrucibleMath) window.CrucibleMath.render(p, String(value || ''));
      else p.textContent = value || '';
      return p;
    }
    function field(parent, title, value) {
      if (!value || (Array.isArray(value) && !value.length)) return;
      const section = el('div', null, 'assumption-field');
      section.append(el('h3', title));
      if (Array.isArray(value)) {
        const list = el('ul');
        for (const item of value) { const row = el('li'); row.append(prose(item)); list.append(row); }
        section.append(list);
      } else section.append(prose(value));
      parent.append(section);
    }
    function safeSource(source) {
      try {
        const url = new URL(source.url, window.location.href);
        if (url.protocol !== 'https:' && !(url.origin === new URL(window.location.href).origin && url.pathname.startsWith('/api/protocol/'))) return null;
        if (url.username || url.password) return null;
        return url.href;
      } catch { return null; }
    }
    function render(sheet, scheme, profile, selectedClaim) {
      clearView();
      const presentation = sheet.presentation;
      const byId = new Map(sheet.claims.map(claim => [claim.id, claim]));
      let reader, readerTrigger, readerRoute, disposed = false;
      function releaseScroll() {
        document.body.style.overflow = [...document.querySelectorAll('dialog')].some(item => item.open) ? 'hidden' : '';
      }
      function openReader(id, trigger) {
        const claim = byId.get(id);
        if (!reader || !claim || disposed || !isVisible()) return;
        if (!reader.open) readerTrigger = trigger || root.querySelector('.assumptions-overview').querySelector('a');
        const top = el('div', null, 'assumption-reader-top');
        top.append(el('span', scheme.name + ' · Supporting statement'));
        const close = el('button', 'Back to overview', 'text-button'); close.type = 'button';
        close.setAttribute('aria-label', 'Close supporting statement and return to overview');
        close.addEventListener('click', () => reader.close()); top.append(close);
        const content = el('div', null, 'assumption-reader-content');
        content.dataset.claimId = id;
        content.append(el('p', roles[claim.role], 'assumption-kind'));
        const title = el('h2', claim.title); title.id = 'assumption-reader-title'; title.tabIndex = -1;
        content.append(title, makeBody(claim));
        reader.replaceChildren(top, content);
        readerRoute = route(scheme, profile, id);
        window.history.replaceState(null, '', readerRoute);
        if (!reader.open) reader.showModal();
        document.body.style.overflow = 'hidden';
        reader.scrollTop = 0; title.focus();
      }
      function readingLink(id, label) {
        const link = el('a', label);
        link.href = route(scheme, profile, id);
        if (presentation?.overview) link.setAttribute('aria-haspopup', 'dialog');
        link.addEventListener('click', event => {
          if (!isVisible() || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          if (presentation?.overview) { event.preventDefault(); openReader(id, link); return; }
          const target = root.querySelector('[data-claim-id="' + id + '"]');
          if (!target) return;
          event.preventDefault();
          target.open = true;
          target.querySelector('summary').focus();
          target.scrollIntoView({block: 'nearest'});
          window.history.replaceState(null, '', link.href);
        });
        return link;
      }
      function makeBody(claim) {
        const body = el('div', null, 'assumption-body');
        let related;
        if (claim.summary) body.append(prose(claim.summary, 'assumption-lead'));
        if (claim.depends_on?.length) {
          const uses = el('div', null, 'assumption-field');
          uses.append(el('h3', presentation?.overview ? 'Related statements' : 'Uses in this argument'));
          const links = el('div', null, 'assumption-related');
          for (const id of claim.depends_on) links.append(readingLink(id, byId.get(id).title));
          uses.append(links, prose('Reading links; the scope and justification are stated below.', 'assumption-related-note'));
          if (!presentation?.overview) body.append(uses);
          else related = uses;
        }
        field(body, 'Precise statement', claim.precise_statement);
        field(body, 'Where it applies', claim.scope);
        field(body, 'How to investigate', claim.challenge);
        field(body, 'What remains open', claim.limitations);
        const sources = el('div', null, 'assumption-sources');
        for (const source of claim.sources || []) {
          const href = safeSource(source);
          if (!href) continue;
          const link = el('a', source.title || 'Source'); link.href = href;
          link.target = '_blank'; link.rel = 'noopener noreferrer';
          sources.append(link);
        }
        if (sources.childElementCount) { const section = el('div', null, 'assumption-field'); section.append(el('h3', 'Sources & evidence'), sources); body.append(section); }
        const note = scheme.evidence_note;
        if (typeof note?.id === 'string' && note.id.startsWith('curated:') && Number.isSafeInteger(note.revision) &&
            note.revision > 0 && /^[a-f0-9]{64}$/.test(note.content_sha256 || '')) {
          const link = el('a', 'Supporting research note →', 'assumption-permalink');
          link.href = '#notebook?' + new URLSearchParams({note: note.id, revision: note.revision, sha256: note.content_sha256});
          body.append(link);
        }
        if (related) body.append(related);
        const permalink = el('a', 'Link to this statement', 'assumption-permalink');
        permalink.href = route(scheme, profile, claim.id); body.append(permalink);
        return body;
      }
      if (presentation?.overview) {
        const overview = presentation.overview;
        const guide = el('div', null, 'assumptions-overview');
        const goal = el('section', null, 'assumptions-goal');
        goal.append(el('h2', 'The security goal'), prose(overview.goal), prose(overview.scope, 'assumptions-overview-scope'));
        guide.append(goal);
        for (const group of overview.groups) {
          const section = el('section', null, 'assumptions-overview-group');
          section.dataset.overviewGroup = group.id;
          const heading = el('h2', group.title); heading.id = 'assumptions-overview-' + group.id;
          section.setAttribute('aria-labelledby', heading.id);
          const items = el('ul');
          for (const item of group.items) {
            const row = el('li');
            const title = el('h3'); title.append(readingLink(item.claim_id, item.title));
            row.append(title, prose(item.text)); items.append(row);
          }
          section.append(heading, items); guide.append(section);
        }
        guide.append(prose(overview.boundary, 'assumptions-overview-boundary'));
        root.append(guide);
      } else if (presentation?.lead) root.append(prose(presentation.lead, 'assumptions-reading-guide'));
      if (!sheet.claims.length) {
        root.append(prose('An assumptions sheet has not been curated for this release yet.', 'assumptions-empty'));
      } else if (!presentation?.overview) {
        const makeCard = (claim, index) => {
          const details = el('details', null, 'assumption-card');
          details.dataset.claimId = claim.id;
          details.open = selectedClaim === claim.id;
          const summary = el('summary');
          if (!presentation) {
            const number = el('span', String(index + 1).padStart(2, '0'), 'assumption-number');
            number.setAttribute('aria-hidden', 'true'); summary.append(number);
          }
          const heading = el('span', null, 'assumption-label');
          heading.append(el('span', claim.title, 'assumption-title'));
          if (claim.purpose) heading.append(el('span', claim.purpose, 'assumption-purpose'));
          const support = (claim.supports || []).map(id => purposes[id] || id).join(' & ');
          heading.append(el('span', [roles[claim.role] || claim.role, support].filter(Boolean).join(' · '), 'assumption-kind'));
          const toggle = el('span', '+', 'assumption-toggle'); toggle.setAttribute('aria-hidden', 'true');
          summary.append(heading, toggle);
          const body = makeBody(claim);
          details.append(summary, body);
          return details;
        };
        if (presentation) {
          for (const group of presentation.sections) {
            const section = el('section', null, 'assumption-group');
            section.setAttribute('aria-labelledby', 'assumption-group-' + group.id);
            const title = el('h2', group.title); title.id = 'assumption-group-' + group.id;
            section.append(title);
            if (group.description) section.append(prose(group.description, 'assumption-group-description'));
            const list = el('div', null, 'assumption-list');
            for (const id of group.claim_ids) list.append(makeCard(byId.get(id)));
            section.append(list); root.append(section);
          }
        } else {
          const list = el('div', null, 'assumption-list');
          sheet.claims.forEach((claim, index) => list.append(makeCard(claim, index)));
          root.append(list);
        }
      }
      const scope = el('details', null, 'assumptions-scope');
      scope.append(el('summary', 'Curated draft · Scope & open work'));
      const body = el('div');
      body.append(prose('This is a partial map of the argument, not a security certification. A precise problem can be inspected and attacked; finite tests cannot certify that it is hard.'));
      for (const target of sheet.targets || []) field(body, target.title, target.statement);
      field(body, 'Current scope', sheet.limitations);
      body.append(prose('Challenging a premise can invalidate an argument without proving that the scheme itself fails. Correctness concerns all honest draws, including draws that the challenge service does not issue.'));
      const links = el('div', null, 'assumptions-data-links');
      const params = new URLSearchParams({scheme_id: scheme.id, version: scheme.version});
      for (const [label, endpoint] of [['Read structured sheet', 'assumptions'], ['Protocol specification', 'spec'], ['Release implementation', 'source']]) {
        const link = el('a', label); link.href = '/api/protocol/' + endpoint + '?' + params;
        link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link);
      }
      body.append(links); scope.append(body); root.append(scope);
      if (presentation?.overview) {
        reader = el('dialog', null, 'assumption-reader');
        reader.setAttribute('aria-labelledby', 'assumption-reader-title');
        reader.addEventListener('click', event => {
          if (event.target !== reader) return;
          const rect = reader.getBoundingClientRect();
          if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) reader.close();
        });
        reader.addEventListener('close', () => {
          if (reader.open) return;
          releaseScroll();
          if (disposed || !isVisible()) return;
          if (window.location.hash === readerRoute) window.history.replaceState(null, '', route(scheme, profile));
          if (readerTrigger?.isConnected) readerTrigger.focus();
          reader.replaceChildren();
        });
        disposeReader = () => { disposed = true; if (reader.open) reader.close(); reader.replaceChildren(); };
        root.append(reader);
      }
      if (selectedClaim && !sheet.claims.some(claim => claim.id === selectedClaim)) {
        root.prepend(prose('That statement is not in this draft.', 'assumptions-context'));
      }
      root.removeAttribute('aria-busy');
      if (selectedClaim && byId.has(selectedClaim)) {
        if (reader) openReader(selectedClaim);
        else root.querySelector('[data-claim-id="' + selectedClaim + '"]').scrollIntoView({block: 'nearest'});
      }
    }
    function validate(sheet, scheme) {
      if (!sheet || sheet.scheme_id !== scheme.id || String(sheet.scheme_version) !== String(scheme.version) ||
          !Array.isArray(sheet.claims) || sheet.claims.length > 30 ||
          new Set(sheet.claims.map(c => c.id)).size !== sheet.claims.length ||
          sheet.claims.some(c => !c || !/^[a-z0-9-]+$/.test(c.id) || typeof c.title !== 'string' ||
            typeof c.precise_statement !== 'string' || !Object.hasOwn(roles, c.role))) {
        throw new Error('The assumptions sheet does not match this release.');
      }
      const ids = new Set(sheet.claims.map(claim => claim.id));
      if (sheet.claims.some(claim =>
          (claim.purpose != null && (typeof claim.purpose !== 'string' || !claim.purpose.trim())) ||
          (claim.depends_on != null && (!Array.isArray(claim.depends_on) ||
            claim.depends_on.some(id => !ids.has(id) || id === claim.id))))) {
        throw new Error('The assumptions sheet has an invalid reading reference.');
      }
      if (sheet.presentation) {
        const {lead, sections} = sheet.presentation;
        if (typeof lead !== 'string' || !Array.isArray(sections) || !sections.length || sections.length > 8 ||
            sections.some(group => !group || !/^[a-z0-9-]+$/.test(group.id) || typeof group.title !== 'string' ||
              !Array.isArray(group.claim_ids) || !group.claim_ids.length || group.claim_ids.some(id => !ids.has(id)))) {
          throw new Error('The assumptions sheet has invalid sections.');
        }
        const listed = sections.flatMap(group => group.claim_ids);
        if (listed.length !== ids.size || new Set(listed).size !== ids.size ||
            new Set(sections.map(group => group.id)).size !== sections.length) {
          throw new Error('The assumptions sheet sections must include every statement once.');
        }
        const overview = sheet.presentation.overview;
        if (overview != null) {
          const nonempty = value => typeof value === 'string' && value.trim();
          if (!['goal', 'scope', 'boundary'].every(key => nonempty(overview[key])) ||
              !Array.isArray(overview.groups) || overview.groups.length !== 3 ||
              overview.groups.some((group, index) => !group || group.id !== ['evidence', 'assumptions', 'gaps'][index] ||
                !nonempty(group.title) || !Array.isArray(group.items) || !group.items.length || group.items.length > 4 ||
                group.items.some(item => !item || !nonempty(item.title) || !nonempty(item.text) || !ids.has(item.claim_id)))) {
            throw new Error('The assumptions sheet has an invalid reading overview.');
          }
        }
      }
    }
    async function show(scheme, profile = null, claim = null) {
      const current = ++request, owner = account();
      const active = () => current === request && owner === account() && isVisible();
      clearView();
      const pending = el('div', undefined, 'loading-state');
      const status = prose('Loading assumptions…', 'sr-only'); status.setAttribute('role', 'status');
      pending.append(status); root.append(pending);
      root.setAttribute('aria-busy', 'true');
      try {
        const params = new URLSearchParams({scheme_id: scheme.id, version: scheme.version});
        const data = await api('/api/protocol/assumptions?' + params);
        if (!active()) return;
        validate(data.sheet, scheme); render(data.sheet, scheme, profile, claim);
      } catch (error) {
        if (!active()) return;
        root.removeAttribute('aria-busy');
        clearView(); root.append(prose(error.message || 'Could not load this assumptions sheet.', 'notice notice-error'));
        const retry = el('button', 'Try again', 'text-button'); retry.type = 'button';
        retry.addEventListener('click', () => show(scheme, profile, claim)); root.append(retry);
      }
    }
    function clear() { request++; clearView(); root.removeAttribute('aria-busy'); }
    return {show, clear};
  }
  window.CrucibleAssumptions = Object.freeze({create});
})();

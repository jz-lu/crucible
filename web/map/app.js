/* Read-only network view, mounted inside the research notebook. */
(() => {
  'use strict';
  function mount(root, {currentReleases = () => []} = {}) {
    const data = window.CRUCIBLE_LANDSCAPE, notes = window.CRUCIBLE_NOTES;
    const {el, body, tag, noteHref} = window.NoteUI;
    const $ = selector => root.querySelector(selector);
    const schemes = new Map(data.schemes.map(s => [s.id, s]));
    const families = new Map(data.families.map(f => [f.id, f]));
    const domains = new Map(data.domains.map(d => [d.id, d]));
    const assumptions = new Map(data.assumptions.map(a => [a.id, a]));
    const highlights = new Map(data.highlights.map(h => [h.scheme, h.reason]));
    const scored = new Map(data.scoring_snapshot.releases.map(r => [r.scheme, r]));
    const noteById = new Map(notes.notes.map(n => [n.id, n]));
    const map = $('#landscape'), modal = $('#mobile-details'), detail = $('#mobile-inspector');
    const search = $('#search'), state = {filter:'all', query:'', selected:null};
    let visible = true, destroyed = false, resizeTimer, renderedWidth = 0;
    const descriptions = {
      all:'Documented KEMs, each backed by a research note.',
      scored:'KEMs with an enrolled challenge release, from the current map snapshot.',
      highlighted:'The selected focus within Scored.'
    };
    function matching() {
      return data.schemes.filter(s => {
        if (state.filter === 'scored' && !scored.has(s.id)) return false;
        if (state.filter === 'highlighted' && !highlights.has(s.id)) return false;
        const family = families.get(s.family), domain = domains.get(family.domain);
        return [s.name,s.summary,...s.tags,family.name,domain.name,...s.assumptions.map(id => assumptions.get(id).name)]
          .join(' ').toLowerCase().includes(state.query.toLowerCase());
      });
    }
    function render() {
      if (destroyed) return;
      // Measure before clearing the graph, so layout never observes an empty map.
      renderedWidth = map.clientWidth;
      const entries = matching(); map.replaceChildren(); map.classList.add('alternative-view');
      $('.workspace').dataset.view = 'network';
      if (entries.length) window.LandscapeViews.render('network', map, entries, {
        width:renderedWidth, domains:data.domains, families:data.families, relationships:data.relationships,
        highlights, scored, selected:state.selected, select:openScheme
      });
      else map.append(el('p', 'empty', 'No matches. Try another search or collection.'));
      $('#map-count').textContent = entries.length + (entries.length === 1 ? ' KEM' : ' KEMs');
      const totals = {all:data.schemes.length, scored:scored.size, highlighted:highlights.size};
      for (const button of root.querySelectorAll('[data-filter]')) {
        const filter = button.dataset.filter;
        button.textContent = filter[0].toUpperCase() + filter.slice(1) + ' · ' + totals[filter];
        button.setAttribute('aria-pressed', String(state.filter === filter));
      }
      $('#filter-description').textContent = descriptions[state.filter];
    }
    function popup(title, content) {
      $('#mobile-detail-title').textContent = title;
      detail.replaceChildren(content);
      if (!modal.open) modal.showModal();
      modal.scrollTop = 0;
    }
    function openScheme(id) {
      const scheme = schemes.get(id); if (!scheme) return;
      state.selected = id;
      for (const node of map.querySelectorAll('[data-scheme]')) {
        const selected = node.dataset.scheme === id;
        node.classList.toggle('is-selected', selected); node.setAttribute('aria-pressed', String(selected));
      }
      const card = el('article', 'detail-card');
      card.append(el('p', 'detail-path', families.get(scheme.family).name), el('h2', '', scheme.name));
      card.append(el('p', 'detail-summary', scheme.summary));
      const actions = el('div', 'detail-actions');
      const read = el('a', '', 'Read research note'); read.href = noteHref(scheme.note_ref.note_id, scheme.note_ref.revision);
      // The map's scoring snapshot is historical context, not navigation
      // authority. Resolve actions against the shell's current catalogue.
      const releases = currentReleases();
      const release = Array.isArray(releases) && releases.find(item => item.id === id && item.version != null);
      if (release) {
        const href = '#scheme/' + encodeURIComponent(release.id) + '/' + encodeURIComponent(release.version);
        const challenge = el('a', 'challenge-action', 'Open challenge');
        challenge.href = '#challenge/' + encodeURIComponent(release.id) + '/' + encodeURIComponent(release.version) + '?view=challenge';
        const parameters = el('a', '', 'View parameters');
        parameters.href = href;
        actions.append(challenge, read, parameters);
      } else actions.append(read);
      card.append(actions); popup('Scheme', card);
    }
    function openNote(id, revision) {
      const note = noteById.get(id), content = note?.revisions.find(r => r.revision === revision);
      if (!content) return;
      const article = el('article', 'map-note');
      article.append(el('h2', '', content.title));
      const tags = el('div', 'note-tags'); content.tags.forEach(value => tags.append(tag(value))); article.append(tags);
      article.append(el('h3', '', 'Claim'), el('p', '', content.claim),
        el('h3', '', 'Conditions & scope'), el('p', '', content.conditions), body(content.body));
      if (content.references.length) {
        article.append(el('h3', '', 'Sources'));
        const list = el('ol', 'note-sources');
        for (const reference of content.references) {
          const item = el('li'), link = el('a', '', reference.title + ' ↗');
          link.href = reference.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          item.append(link, el('p', '', reference.scope)); list.append(item);
        }
        article.append(list);
      }
      article.append(el('p', 'detail-footnote', 'Curated research note · revision ' + revision + '. Descriptive source material; not a mathematical certification.'));
      if (state.selected) {
        const back = el('button', 'related-button', '← Back to scheme'); back.type = 'button';
        back.addEventListener('click', () => openScheme(state.selected)); article.append(back);
      }
      popup('Research note', article);
    }
    function notesWithTag(value) {
      const article = el('article', 'map-note'); article.append(el('h2', '', value ? 'Notes tagged ' + window.NoteUI.label(value) : 'Map research notes'));
      const list = el('div', 'map-note-list');
      for (const note of notes.notes) {
        const latest = note.revisions[note.revisions.length - 1];
        if (value && !latest.tags.includes(value)) continue;
        const button = el('button', '', latest.title); button.type = 'button';
        button.addEventListener('click', () => openNote(note.id, latest.revision)); list.append(button);
      }
      article.append(list); popup('Research notes', article);
    }
    function onClick(event) {
      const link = event.target.closest('a'); if (!link) return;
      const href = link.getAttribute('href') || '';
      const match = href.match(/^(?:\/map\/)?notes\.html(?:#(.*))?$/);
      if (!match) return;
      event.preventDefault();
      let parts; try { parts = (match[1] || '').split('/').map(decodeURIComponent); } catch { return; }
      if (parts[0] === 'note') openNote(parts[1], Number(parts[2]));
      else notesWithTag(parts[0] === 'tag' ? parts[1] : '');
    }
    function centerNetwork() {
      const bounds = map.getBoundingClientRect();
      const inset = bounds.height <= window.innerHeight - 48 ? (window.innerHeight - bounds.height) / 2 : 24;
      window.scrollTo({top:Math.max(0, window.scrollY + bounds.top - inset),
        behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
    for (const button of root.querySelectorAll('[data-filter]')) button.addEventListener('click', () => {
      state.filter = button.dataset.filter; render(); centerNetwork();
    });
    search.addEventListener('input', () => { state.query = search.value.trim(); render(); });
    $('#close-details').addEventListener('click', () => modal.close());
    modal.addEventListener('click', event => {
      if (event.target !== modal || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
      const bounds = modal.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) modal.close();
    });
    modal.addEventListener('close', () => {
      const previous = state.selected; state.selected = null;
      if (!destroyed) {
        for (const node of map.querySelectorAll('[data-scheme]')) {
          node.classList.remove('is-selected'); node.setAttribute('aria-pressed', 'false');
          if (visible && node.dataset.scheme === previous) node.focus();
        }
      }
    });
    root.addEventListener('click', onClick);
    const onResize = () => { clearTimeout(resizeTimer); if (visible) resizeTimer = setTimeout(render, 120); };
    window.addEventListener('resize', onResize);
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (map.clientWidth > 0 && map.clientWidth !== renderedWidth) onResize(); }) : null;
    observer?.observe(map);
    return {
      show() { if (!destroyed) { visible = true; render(); } },
      hide() { visible = false; if (modal.open) modal.close(); },
      destroy() {
        destroyed = true; visible = false; clearTimeout(resizeTimer); observer?.disconnect();
        window.removeEventListener('resize', onResize); root.removeEventListener('click', onClick);
        if (modal.open) modal.close();
      }
    };
  }
  window.CrucibleResearchMap = Object.freeze({mount});
  // Existing direct links enter the same notebook shell instead of a second page.
  if (document.getElementById('landscape')) window.location.replace('/#notebook?view=map');
})();

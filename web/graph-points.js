// Shared chart interaction only. Each caller owns the scientific values,
// missing-data policy, axes, provenance and labels; this module invents none.
(() => {
  'use strict';
  const selections = new Map(), charts = new Map(), previewSnapshots = new WeakMap();
  let generation = 0;
  function capturePreview(svg) {
    const state = charts.get(svg)?.capturePreview();
    if (!state) return null;
    const token = Object.freeze({});
    previewSnapshots.set(token, state);
    return token;
  }
  const node = (tag, attrs = {}) => {
    const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value);
    return n;
  };
  function publish(key, id) {
    selections.set(key, id);
    for (const [chart, entry] of charts) {
      if (!chart.isConnected) { charts.delete(chart); continue; }
      if (entry.key === key) entry.update(id);
    }
  }
  function attach({svg, readout, key, points, x, y, describe, initialId = null, onReadout, onPreview, onSelection,
    interactionKey = null, previewState = null}) {
    charts.get(svg)?.dispose();
    for (const chart of charts.keys()) if (!chart.isConnected) charts.delete(chart);
    const valid = points.filter(p => typeof p.id === 'string' && Number.isFinite(p.position));
    const measured = p => p && Number.isFinite(p.value);
    if (!valid.length) return;
    if (!selections.has(key) && initialId && valid.some(p => p.id === initialId)) selections.set(key, initialId);
    let preview = null, cursor = 0, readoutKey = null, selectionKey = null, keyboardPreview = false, unavailablePreview = null;
    const owner = generation, route = window.location.href, entry = {key};
    const ownsChart = () => owner === generation && charts.get(svg) === entry && svg.isConnected;
    const canInteract = () => ownsChart() && !svg.closest('[hidden]');
    const saved = previewState && previewSnapshots.get(previewState);
    if (previewState) previewSnapshots.delete(previewState);
    if (saved && saved.generation === generation && saved.route === route && saved.key === key &&
        saved.container === svg.parentElement && typeof interactionKey === 'string' && interactionKey &&
        saved.interactionKey === interactionKey) {
      const point = valid.find(p => p.id === saved.profileId);
      keyboardPreview = true;
      if (measured(point)) { preview = point; cursor = valid.indexOf(point); }
      else unavailablePreview = saved.profileId;
    }
    entry.capturePreview = () => canInteract() && document.activeElement === svg && keyboardPreview &&
      route === window.location.href && typeof interactionKey === 'string' && interactionKey && (preview || unavailablePreview) ?
      {generation: owner, route, key, interactionKey, container: svg.parentElement, profileId: preview?.id || unavailablePreview} : null;
    const guide = node('line', {y1: 43, y2: 190, class: 'chart-selection-guide', visibility: 'hidden'});
    const selected = node('circle', {r: 5, class: 'chart-selected-dot', visibility: 'hidden'});
    const halo = node('circle', {r: 9, class: 'chart-hover-halo', visibility: 'hidden'});
    const hover = node('circle', {r: 5, class: 'chart-hover-dot', visibility: 'hidden'});
    const group = node('g', {'aria-hidden': 'true', class: 'chart-interaction-markers'});
    group.append(guide, selected, halo, hover); svg.append(group);
    const valueReadout = document.createElement('span'), selectionReadout = document.createElement('span');
    valueReadout.className = 'chart-point-values';
    selectionReadout.className = 'chart-selected-sources';
    readout.replaceChildren(valueReadout, selectionReadout);
    readout.classList.add('is-empty');
    const announcement = document.createElement('span'); announcement.className = 'sr-only chart-point-announcement';
    announcement.setAttribute('role', 'status'); announcement.setAttribute('aria-live', 'polite'); announcement.setAttribute('aria-atomic', 'true');
    readout.parentElement?.append(announcement);
    const show = (n, p) => {
      n.setAttribute('visibility', measured(p) ? 'visible' : 'hidden');
      if (measured(p)) { n.setAttribute('cx', x(p.position)); n.setAttribute('cy', y(p.value)); }
    };
    function update(id = selections.get(key)) {
      if (!ownsChart()) return;
      const pinned = valid.find(p => p.id === id);
      show(selected, pinned);
      guide.setAttribute('visibility', measured(pinned) ? 'visible' : 'hidden');
      if (measured(pinned)) { guide.setAttribute('x1', x(pinned.position)); guide.setAttribute('x2', x(pinned.position)); }
      show(halo, preview); show(hover, preview);
      // A selected attack is an inspector: its value and links stay pinned
      // while the preview dot moves. Resource readouts can follow hover.
      const current = onSelection && pinned ? pinned : preview || pinned;
      readout.classList.toggle('is-empty', !current);
      const committed = Boolean(current && current.id === id), nextReadoutKey = JSON.stringify([current?.id || null, committed]);
      // Blur and pointerleave often occur while a source link is receiving
      // focus/click. Preserve its DOM when the effective selection is unchanged.
      if (readoutKey !== nextReadoutKey) {
        valueReadout.replaceChildren();
        if (current) {
          if (onReadout) onReadout(current, valueReadout);
          else { const text = document.createElement('span'); text.textContent = describe(current); valueReadout.append(text); }
          onPreview?.(current, valueReadout, {committed});
        }
        readoutKey = nextReadoutKey;
      }
      if (selectionKey !== (pinned?.id || null)) {
        selectionReadout.replaceChildren();
        if (pinned) onSelection?.(pinned, selectionReadout);
        selectionKey = pinned?.id || null;
      }
      const selection = pinned ? ' Selected ' + describe(pinned) + '.' : id ? ' Selected configuration is unavailable in this chart.' : '';
      svg.setAttribute('aria-label', `${svg.dataset.chartLabel}.${selection} Arrow keys preview; Enter selects.`);
    }
    entry.update = update;
    entry.dispose = () => { group.remove(); announcement.remove(); readout.replaceChildren(); };
    charts.set(svg, entry);
    svg.dataset.chartLabel = svg.dataset.chartLabel || svg.getAttribute('aria-label') || svg.querySelector('title')?.textContent || 'Chart';
    // One focus stop per chart, rather than dozens of tiny point buttons.
    svg.setAttribute('role', 'group'); svg.setAttribute('tabindex', '0');
    // The original security SVG labels its static title and description with
    // aria-labelledby, which would override the interactive label below.
    const description = svg.getAttribute('aria-labelledby');
    if (description) { svg.setAttribute('aria-describedby', description); svg.removeAttribute('aria-labelledby'); }
    svg.setAttribute('aria-label', svg.dataset.chartLabel + '. Arrow keys preview; Enter selects.');
    readout.setAttribute('aria-live', 'off');
    function nearest(event) {
      if (!canInteract() || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return null;
      const rect = svg.getBoundingClientRect();
      if (!(rect.width > 0 && rect.height > 0)) return null;
      const px = (event.clientX - rect.left) * 480 / rect.width;
      const py = (event.clientY - rect.top) * 240 / rect.height;
      if (px < 40 || px > 473 || py < 35 || py > 198) return null;
      return valid.reduce((a, b) => Math.abs(x(a.position) - px) <= Math.abs(x(b.position) - px) ? a : b);
    }
    svg.addEventListener('pointermove', event => {
      if (!canInteract() || event.pointerType === 'touch') return;
      const next = nearest(event);
      keyboardPreview = false;
      if (next) unavailablePreview = null;
      if (preview?.id === next?.id) return;
      preview = next; update();
    });
    svg.addEventListener('pointerleave', () => { if (!canInteract()) return; preview = null; keyboardPreview = false; update(); });
    svg.addEventListener('click', event => {
      const point = nearest(event);
      if (!point) return;
      preview = point; keyboardPreview = false; unavailablePreview = null; cursor = valid.indexOf(point); publish(key, point.id);
      announcement.textContent = 'Selected ' + describe(point);
    });
    svg.addEventListener('keydown', event => {
      if (!canInteract() || !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' ', 'Escape'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Escape') {
        preview = null; keyboardPreview = false; unavailablePreview = null; update(); const pinned = valid.find(p => p.id === selections.get(key));
        announcement.textContent = pinned ? 'Selected ' + describe(pinned) : selections.has(key) ? 'The selected configuration is unavailable in this chart.' : 'Preview cleared.'; return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        if (unavailablePreview) { announcement.textContent = 'The previewed configuration is unavailable in this chart. Use an arrow key to preview another configuration.'; return; }
        const point = preview || valid.find(p => p.id === selections.get(key)) || (!selections.has(key) && valid[cursor]);
        if (!point) { announcement.textContent = 'The selected configuration is unavailable in this chart. Use an arrow key to preview another configuration.'; return; }
        preview = point; publish(key, point.id); announcement.textContent = 'Selected ' + describe(point); return;
      }
      const current = valid.findIndex(p => p.id === (preview?.id || selections.get(key)));
      if (current >= 0) cursor = current;
      cursor = event.key === 'Home' ? 0 : event.key === 'End' ? valid.length - 1 : Math.max(0, Math.min(valid.length - 1, cursor + (event.key === 'ArrowLeft' ? -1 : 1)));
      preview = valid[cursor]; keyboardPreview = true; unavailablePreview = null; update();
      announcement.textContent = 'Preview ' + describe(preview) + '. Press Enter to select.';
    });
    svg.addEventListener('blur', () => { if (!canInteract()) return; preview = null; keyboardPreview = false; update(); });
    update();
    if (unavailablePreview) announcement.textContent = 'The previewed configuration is unavailable in this chart. Use an arrow key to preview another configuration.';
  }
  window.CrucibleGraphPoints = Object.freeze({attach, capturePreview, select: publish, selected: key => selections.get(key) || null,
    clear: () => { generation++; for (const entry of charts.values()) entry.dispose(); charts.clear(); selections.clear(); }});
})();

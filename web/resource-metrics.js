/* Data-only resource publications. No uploaded HTML or code is rendered. */
(() => {
  'use strict';
  const cache = new Map(), selections = new Map();
  const key = scheme => JSON.stringify([scheme.id, String(scheme.version)]);
  const operations = [['keygen', 'Key generation'], ['encapsulate', 'Encapsulation'], ['decapsulate', 'Decapsulation']];
  const views = {
    public: [['public_key', 'Public key'], ['fresh', 'Fresh exchange'], ['private_key', 'Serialized private key']],
    cipher: [['ciphertext', 'Ciphertext'], ['cached', 'Exchange with cached key']],
    computation: [['warm', 'Operation time'], ['first', 'First-call time'], ['rss', 'Process RAM'], ['stack', 'Stack'], ['heap_stack', 'Tracked heap + stack']]
  };
  const labels = Object.fromEntries(Object.values(views).flat());
  const field = view => view === 'stack' ? 'stack_evidence' : view === 'heap_stack' ? 'memory_evidence' : 'native_evidence';
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
  function exact(value) {
    if (Number.isSafeInteger(value) && value >= 0) return String(value);
    if (value?.type === 'integer' && typeof value.decimal === 'string' && /^(0|[1-9][0-9]*)$/.test(value.decimal)) return value.decimal;
    return null;
  }
  function coordinate(value) {
    const text = exact(value);
    return text !== null && BigInt(text) <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(text) : null;
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function svg(tag, attrs = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function control(caption, values, current, change) {
    const label = element('label', caption + ' ', 'resource-control'), select = element('select');
    select.setAttribute('aria-label', caption);
    for (const [value, text] of values) { const option = element('option', text); option.value = value; select.append(option); }
    select.value = current; select.addEventListener('change', () => {
      const card = select.closest('.overview-card'), focused = document.activeElement === select;
      change(select.value);
      if (focused) card?.querySelector('select[aria-label="' + caption + '"]')?.focus();
    }); label.append(select); return label;
  }
  async function load(scheme, api, changed, {refresh = false} = {}) {
    const id = key(scheme), old = cache.get(id);
    if (old && !refresh && (old.status === 'loading' || Date.now() - (old.checkedAt || 0) < 60000)) return;
    const entry = {status: 'loading', data: old?.data}; cache.set(id, entry);
    try {
      const data = await api('/api/protocol/resources?' + new URLSearchParams({scheme_id: scheme.id, version: scheme.version}));
      if (cache.get(id) !== entry) return;
      if (data.scheme_id !== scheme.id || String(data.version) !== String(scheme.version)) throw new Error('Resource report names another release.');
      if (data.publication && (!Array.isArray(data.resources?.configurations) || !Array.isArray(data.publication.binding?.profiles))) throw new Error('Resource report is incomplete.');
      entry.data = data; entry.status = 'ready'; entry.checkedAt = Date.now();
    } catch (_) { if (cache.get(id) === entry) { entry.status = 'error'; entry.checkedAt = Date.now(); } }
    if (cache.get(id) === entry) changed();
  }
  function point(row, view, op, profile) {
    if (!row) return {value: null, text: 'Not measured'};
    if (['public_key', 'private_key', 'ciphertext', 'fresh', 'cached'].includes(view)) {
      const value = view === 'fresh' ? row.communication?.fresh_exchange_bytes : view === 'cached' ? row.communication?.cached_exchange_bytes : row.sizes_bytes?.[view];
      return {value: coordinate(value), text: exact(value) ?? 'Not declared'};
    }
    const evidence = row[field(view)]?.find(item => item.comparison_profile === profile);
    if (!evidence) return {value: null, text: 'Different measurement profile'};
    if (view === 'warm' || view === 'first') {
      const result = (view === 'warm' ? evidence.computation : evidence.first_call)?.[op];
      const stats = result?.duration_ns;
      if (result?.complete !== true || !evidence.platform_identified || number(stats?.median) === null) return {value: null, text: 'Not measured'};
      return {value: stats.median / 1000, text: String(stats.median / 1000), q1: number(stats.q1) === null ? null : stats.q1 / 1000, q3: number(stats.q3) === null ? null : stats.q3 / 1000, samples: stats.n};
    }
    if (view === 'rss') {
      const result = evidence.memory_components?.[op + ':rss'];
      return result?.complete === true ? {value: coordinate(result.values?.max), text: exact(result.values?.max) ?? 'Not measured', samples: result.values?.n} : {value: null, text: 'Not measured'};
    }
    const result = evidence.operations?.[op];
    return result?.measured === result?.planned && result?.measured > 0 ? {value: coordinate(result.max_observed_bytes), text: exact(result.max_observed_bytes) ?? 'Not measured', samples: result.measured} : {value: null, text: 'Not measured'};
  }
  function render({scheme, cardId, target, profiles, profileId, profileLabel, stagePosition, named, redraw, api}) {
    const entry = cache.get(key(scheme));
    const card = target.closest('.overview-card');
    const legacy = card.querySelector('.overview-metric-options');
    const caption = card.querySelector('.reference-timing-caption');
    if (!entry?.data?.publication || !views[cardId]) {
      if (legacy) legacy.hidden = false;
      if (caption) caption.hidden = false;
      return false;
    }
    if (legacy) legacy.hidden = true;
    if (caption) caption.hidden = true;
    const data = entry.data, publication = data.publication, binding = publication.binding;
    const savedKey = key(scheme) + cardId;
    if (!selections.has(savedKey)) selections.set(savedKey, {view: views[cardId][0][0], op: 'keygen', profiles: {}});
    const choice = selections.get(savedKey), view = choice.view, timed = ['warm', 'first'].includes(view);
    const computational = cardId === 'computation', unit = timed ? 'Microseconds' : 'Bytes';
    const rows = new Map(data.resources.configurations.map(row => [row.configuration, row]));
    const mapping = new Map(binding.profiles.map(row => [row.profile_id, row.configuration]));
    const evidence = [...rows.values()].flatMap(row => row[field(view)] || []);
    const methods = [...new Set(evidence.map(row => row.comparison_profile))];
    if (!methods.includes(choice.profiles[field(view)])) choice.profiles[field(view)] = methods[0];
    const method = choice.profiles[field(view)];
    const detailsOpen = target.querySelector('details')?.open === true;
    target.replaceChildren();
    document.querySelector('#overview-title-' + cardId).textContent = computational ? 'Computation & memory' : labels[view];
    document.querySelector('#overview-badge-' + cardId).textContent = 'Repo measured';
    const controls = element('div', undefined, 'resource-controls');
    controls.append(control('Measure', views[cardId], view, value => { choice.view = value; redraw(); }));
    if (computational) controls.append(control('Operation', operations, choice.op, value => { choice.op = value; redraw(); }));
    if (computational && methods.length > 1) controls.append(control('Measurement profile', methods.map(value => [value, value.slice(0, 12)]), method, value => { choice.profiles[field(view)] = value; redraw(); }));
    target.append(controls);
    const points = profiles.map((profile, index) => ({...point(rows.get(mapping.get(profileId(profile))), view, choice.op, method), label: profileLabel(profile, scheme), x: named ? index + 1 : stagePosition(profile), id: profileId(profile)}));
    const measured = points.filter(p => p.value !== null && Number.isFinite(p.x));
    const xs = points.map(p => p.x).filter(Number.isFinite), low = Math.min(...xs), high = Math.max(...xs);
    const maximum = Math.max(1, ...measured.map(p => Math.max(p.value, p.q3 ?? 0)));
    const x = value => high === low ? 255 : 48 + (value - low) * 417 / (high - low);
    const y = value => 190 - value * 147 / maximum;
    const chart = svg('svg', {viewBox: '0 0 480 240', role: 'img', class: 'overview-svg', 'aria-label': `${labels[view]} by ${named ? 'parameter set' : 'stage'}`});
    chart.append(svg('title', {}, labels[view]), svg('desc', {}, `${measured.length} comparable observations. Missing values are gaps. Timing whiskers show sample quartiles.`));
    chart.append(svg('text', {x: 48, y: 19, class: 'chart-axis-title'}, unit));
    for (let i = 0; i <= 3; i++) chart.append(svg('line', {x1: 48, x2: 465, y1: 43 + i * 49, y2: 43 + i * 49, class: 'chart-gridline'}));
    chart.append(svg('path', {d: 'M48 43V190H465', class: 'chart-axis'}));
    for (const value of [0, maximum]) chart.append(svg('text', {x: 40, y: y(value) + 4, 'text-anchor': 'end', class: 'chart-tick'}, value.toLocaleString('en-US', {maximumSignificantDigits: 4})));
    if (xs.length) for (const value of [...new Set([low, xs[Math.floor((xs.length - 1) / 2)], high])]) chart.append(svg('text', {x: x(value), y: 210, 'text-anchor': 'middle', class: 'chart-tick'}, value));
    chart.append(svg('text', {x: 250, y: 235, 'text-anchor': 'middle', class: 'chart-axis-title'}, named ? 'Parameter set (see values)' : 'Challenge stage'));
    let path = '', connected = false;
    for (const p of points) {
      if (p.value === null || !Number.isFinite(p.x)) { connected = false; continue; }
      path += `${connected ? 'L' : 'M'}${x(p.x)} ${y(p.value)} `; connected = true;
    }
    if (!named) chart.append(svg('path', {d: path, class: 'chart-series'}));
    for (const p of measured) {
      if (timed && p.q1 !== null && p.q3 !== null) chart.append(svg('line', {x1: x(p.x), x2: x(p.x), y1: y(p.q1), y2: y(p.q3), class: 'resource-whisker'}));
      const dot = svg('circle', {cx: x(p.x), cy: y(p.value), r: 3, class: 'chart-point', 'data-profile-id': p.id, 'data-value': p.value});
      dot.append(svg('title', {}, `${p.label}: ${p.text} ${unit.toLowerCase()}`)); chart.append(dot);
    }
    if (!measured.length) chart.append(svg('text', {x: 250, y: 110, 'text-anchor': 'middle', class: 'chart-empty-label'}, 'No comparable measurements'));
    target.append(chart);
    const notes = {
      public_key: 'Complete serialized public key. Shared built-in parameters are recorded in the evidence.',
      private_key: 'Serialized private-key storage. This is local storage, not transmitted communication.',
      fresh: 'Primitive payload for setup delivery, one public key and one ciphertext. Application framing and certificates are outside this profile.',
      ciphertext: 'Complete serialized ciphertext.', cached: 'Ciphertext payload with the recipient key and required public setup already cached.',
      warm: 'Median of 100 native operation timings. Whiskers are sample quartiles; process startup is outside the timed interval.',
      first: 'Median first-call time across five fresh workers, shown separately from warm operation timing.',
      rss: 'Maximum observed fresh-worker resident RAM, including initialization, dependencies and caller buffers. Baseline is retained in the evidence; it is not subtracted.',
      stack: 'Maximum observed stack watermark in the 8 MiB probe. Reserved but unwritten stack can be missed.',
      heap_stack: 'Maximum observed tracked heap plus stack in the same Massif snapshot, including estimated allocator overhead. Code, static data and some allocations are outside this instrument.'
    };
    target.append(element('p', `${notes[view]} ${measured.length} of ${points.length} points shown.`, 'overview-chart-note'));
    if (['rss', 'stack', 'heap_stack'].includes(view)) target.append(element('p', 'Observed peaks do not prove a worst-case device RAM requirement.', 'overview-chart-note'));
    if (entry.status === 'error') target.append(element('p', 'Refresh failed. The last loaded publication remains visible.', 'overview-chart-note'));
    const details = element('details'), summary = element('summary', 'Values, method & source'); details.append(summary); details.open = detailsOpen;
    const table = element('table'), header = element('tr');
    for (const title of ['Configuration', unit, 'Samples']) header.append(element('th', title));
    const thead = element('thead'); thead.append(header); const tbody = element('tbody');
    for (const p of points) { const tr = element('tr'); tr.append(element('td', p.label), element('td', p.text), element('td', p.samples ?? '—')); tbody.append(tr); }
    table.append(thead, tbody); const scroll = element('div', undefined, 'overview-values-scroll'); scroll.tabIndex = 0; scroll.append(table); details.append(scroll);
    const source = publication.source;
    details.append(element('p', `Main implementation: ${binding.implementation.id}. Published ${new Date(publication.received_at * 1000).toLocaleString()}.`, 'overview-chart-note'));
    if (computational) {
      const record = evidence.find(row => row.comparison_profile === method);
      const methodDetails = {comparison_profile: method, implementation_sha256: binding.implementation.sha256,
        environment: record?.environment || record?.profile?.environment, method: record?.profile,
        compiler: record?.compiler || record?.profile?.compiler};
      details.append(element('pre', JSON.stringify(methodDetails, null, 2), 'resource-method'));
    }
    const links = element('p', undefined, 'resource-evidence-links');
    const evidenceLink = element('a', 'Download measurements & evidence');
    if (/^[a-f0-9]{64}$/.test(publication.id)) evidenceLink.href = '/api/resource-publications/' + publication.id + '/download';
    links.append(evidenceLink);
    if (source.repository === 'countableclouds/crucible-schemes' && /^[a-f0-9]{40}$/.test(source.commit)) {
      const commit = element('a', 'Source commit ' + source.commit.slice(0, 8)); commit.href = 'https://github.com/' + source.repository + '/tree/' + source.commit; links.append(commit);
      if (/^[1-9][0-9]+$/.test(source.run_id)) { const run = element('a', 'Measurement workflow'); run.href = 'https://github.com/' + source.repository + '/actions/runs/' + source.run_id; links.append(run); }
    }
    details.append(links, element('p', 'Repository workflow provenance and receipt consistency checks. Security, correctness guarantees and side-channel resistance are assessed separately.', 'overview-chart-note'));
    const refresh = element('button', 'Refresh resource measurements', 'text-button'); refresh.type = 'button'; refresh.addEventListener('click', () => load(scheme, api, redraw, {refresh: true})); details.append(refresh); target.append(details);
    return true;
  }
  function fallback({scheme, cardId, target, api, redraw}) {
    if (!views[cardId] || cache.get(key(scheme))?.status !== 'error') return;
    target.append(element('p', 'Latest repo measurements could not be loaded. Existing reference data is shown.', 'overview-chart-note'));
    const retry = element('button', 'Retry resource report', 'text-button'); retry.type = 'button';
    retry.addEventListener('click', () => load(scheme, api, redraw, {refresh: true})); target.append(retry);
  }
  window.CrucibleResources = {load, render, fallback, clear: () => { cache.clear(); selections.clear(); }, point, exact, coordinate};
})();

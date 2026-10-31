// Apply the nonsensitive color preference before any document content paints.
// Like RESI, an unset preference follows the operating system until toggled.
(() => {
  if (window.CrucibleTheme || typeof document === 'undefined') return;
  const root = document.documentElement, key = 'crucible.theme';
  if (typeof root?.setAttribute !== 'function') return;
  let preference = null, media = null, bound = false;
  const valid = value => value === 'light' || value === 'dark';
  try { const saved = window.localStorage.getItem(key); if (valid(saved)) preference = saved; } catch {}
  try { media = window.matchMedia('(prefers-color-scheme: dark)'); } catch {}
  const current = () => preference || (media?.matches ? 'dark' : 'light');
  function apply() {
    const theme = current(), next = theme === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#171a18' : '#ebede6');
    const button = document.querySelector('#theme-toggle');
    if (button) {
      button.setAttribute('aria-label', `Switch to ${next} theme`);
      button.setAttribute('title', `Switch to ${next} theme`);
      button.setAttribute('data-next-theme', next);
      const label = document.querySelector('#theme-toggle-label');
      if (label) label.textContent = `${next === 'dark' ? 'Dark' : 'Light'} mode`;
      button.hidden = false;
      if (!bound) {
        bound = true;
        button.addEventListener('click', () => {
          preference = current() === 'dark' ? 'light' : 'dark';
          try { window.localStorage.setItem(key, preference); } catch {}
          apply();
        });
      }
    }
  }
  window.CrucibleTheme = Object.freeze({apply});
  apply();
  // Do not wait for deferred application code, authentication or data requests.
  if (document.readyState === 'loading') document.addEventListener('readystatechange', apply);
  media?.addEventListener?.('change', apply);
  window.addEventListener?.('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply();
  });
})();

// Public route structure only: no account, scheme description or research data
// is needed to choose the first frame. Unknown routes stay deliberately neutral.
(() => {
  if (window.CruciblePendingPage) return;
  const presentationKey = 'crucible.pending-presentation';
  const bountyAvailable = () => window.CrucibleFeatures?.bounties !== false &&
    (typeof document === 'undefined' ||
      (document.documentElement || document.querySelector?.('html'))?.getAttribute?.('data-bounties-enabled') !== 'false');
  function remember(allowed) {
    if (typeof allowed !== 'boolean') return;
    // This hint changes only the public frame on the next document load. It
    // contains no identity or rights and is never used to authorize a request.
    try { window.sessionStorage.setItem(presentationKey, allowed ? 'workspace' : 'gate'); } catch {}
  }
  function describe(hash = window.location?.hash || '') {
    if (typeof hash !== 'string' || hash.length > 4096) return {page: 'neutral', title: 'Crucible'};
    const [path, query = ''] = hash.split('?');
    const params = new URLSearchParams(query);
    if (['', '#', '#challenge'].includes(path)) return {page: 'catalogue', title: 'KEM Schemes'};
    if (path === '#people') return {page: 'people', title: 'Leaderboard'};
    const legacyBounty = path === '#notebook' && !params.has('location') &&
      params.get('review') !== '1' && params.get('research_review') !== '1' &&
      (['bounties', 'guide'].includes(params.get('view')) || params.has('bounty') || params.get('compose') === 'bounty');
    if (path === '#bounties' || legacyBounty) {
      if (!bountyAvailable()) return {page: 'notebook-notes', title: 'Research notebook'};
      return {page: params.has('bounty') || params.get('compose') === 'bounty' ? 'list' : 'notebook-bounties', title: 'Bounties'};
    }
    if (path === '#notebook') {
      if (params.get('review') === '1' || params.get('research_review') === '1') return {page: 'list', title: 'Review center'};
      if (params.has('note') || params.has('research_request') || params.has('research') || params.has('bounty') || params.get('compose') === 'bounty') return {page: 'list', title: 'Research notebook'};
      const view = params.get('view');
      return {page: ['bounties', 'guide'].includes(view) ? 'notebook-bounties' : view === 'timeline' ? 'notebook-timeline' : view === 'map' ? 'notebook-map' : 'notebook-notes', title: 'Research notebook'};
    }
    if (path.startsWith('#person/')) {
      let title = 'Profile';
      try {
        const value = decodeURIComponent(path.slice('#person/'.length));
        if (value && value.length <= 100 && !/[\/\u0000-\u001f\u007f]/.test(value)) title = value;
      } catch { /* Malformed addresses never become markup or private reads. */ }
      return {page: 'profile', title};
    }
    if (/^#scheme\/[^/]+\/[^/]+\/assumptions$/.test(path)) return {page: 'list', title: 'Assumptions'};
    if (/^#scheme\/[^/]+\/[^/]+$/.test(path)) return {page: 'chart', title: 'Scheme'};
    if (/^#challenge\/[^/]+\/[^/]+$/.test(path)) return {page: 'list', title: 'Challenge'};
    if (path === '#reviews' || (path === '#propose' && params.get('review') === '1')) return {page: 'list', title: 'Review center'};
    const title = {'#about': 'About us', '#propose': 'Propose a scheme', '#connect': 'Connect an agent',
      '#join': 'Private beta', '#auth': 'Sign in', '#reset-password': 'Reset password'}[path];
    return {page: 'neutral', title: title || 'Crucible'};
  }
  function section(hash = window.location?.hash || '') {
    if (typeof hash !== 'string' || hash.length > 4096) return null;
    const [path, query = ''] = hash.split('?'), params = new URLSearchParams(query);
    if (path === '#reviews' || params.get('review') === '1' || params.get('research_review') === '1') return null;
    if (path === '#bounties' || path === '#notebook' && !params.has('location') &&
        (['bounties', 'guide'].includes(params.get('view')) || params.has('bounty') || params.get('compose') === 'bounty')) return bountyAvailable() ? 'bounties' : 'notebook';
    if (['', '#', '#challenge', '#propose'].includes(path) || /^#(?:scheme|challenge)\//.test(path)) return 'challenge';
    return {'#people': 'people', '#notebook': 'notebook', '#about': 'about', '#research': 'research'}[path] || null;
  }
  function apply() {
    if (typeof document === 'undefined') return;
    const root = document.documentElement || document.querySelector('html');
    if (typeof root?.setAttribute !== 'function') return;
    // app.js owns the resolved gate, including errors. A late bootstrap or
    // queued DOM-ready callback must not overwrite that newer state.
    if (document.body && document.body.getAttribute('data-access-pending') !== 'true') {
      // The app can win the script race. Copy only its resolved presentation
      // label; do not change its completed gate, heading or navigation.
      const resolved = document.body.getAttribute('data-access-view');
      if (resolved === 'workspace' || resolved === 'gate') remember(resolved === 'workspace');
      return;
    }
    const access = document.querySelector('#access-workspace');
    if (access && access.getAttribute('aria-busy') !== 'true') return;
    let hint;
    try { hint = window.sessionStorage.getItem(presentationKey); } catch {}
    const described = describe();
    const route = hint === 'gate' ? {page: 'gate', title: 'Private beta'} :
      hint !== 'workspace' && described.page === 'catalogue' ? {page: 'neutral', title: 'Crucible'} : described;
    root.setAttribute('data-pending-page', route.page);
    root.classList.toggle('pending-notebook', ['Research notebook', 'Bounties'].includes(route.title));
    const title = document.querySelector('#access-title');
    if (title) { title.textContent = route.title; title.removeAttribute('aria-hidden'); }
    const selected = route.page === 'gate' ? null : section();
    for (const kind of ['challenge', 'bounties', 'people', 'notebook', 'research', 'about']) {
      const selector = '#nav-' + kind;
      const tab = document.querySelector(selector);
      if (!tab) continue;
      tab.classList.toggle('selected', kind === selected);
      if (kind === selected) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    }
    return route;
  }
  window.CruciblePendingPage = Object.freeze({describe, section, apply, remember});
  apply();
  if (typeof document !== 'undefined' && typeof (document.documentElement || document.querySelector('html'))?.setAttribute === 'function') {
    // Interactive fires once parsing finishes, before a stalled deferred app
    // script can hold DOMContentLoaded. The route frame must not wait for it.
    if (document.readyState === 'loading') document.addEventListener('readystatechange', apply, {once: true});
    window.addEventListener?.('hashchange', apply);
  }
})();

// Keep the logo's reserved frame, but paint the whole decoded image at once.
// This is independent of access/API readiness. Without this small script the
// normal image remains a fallback; a failed image still leaves the wordmark.
(() => {
  if (typeof document === 'undefined') return;
  const root = document.querySelector('html');
  if (!root || root.classList.contains('decode-brand-marks')) return;
  // A late script must not re-hide a completed image that can already be on
  // screen. Only incomplete images need the decode gate introduced below.
  for (const image of document.querySelectorAll('.brand-mark')) {
    if (image.complete && image.naturalWidth) image.dataset.logoReady = 'true';
  }
  root.classList.add('decode-brand-marks');
  const pending = new WeakSet();
  function reveal(image) {
    if (!image?.classList?.contains('brand-mark') || !image.complete || !image.naturalWidth || pending.has(image)) return;
    const source = image.currentSrc || image.src;
    pending.add(image);
    const finish = () => {
      pending.delete(image);
      if (image.complete && image.naturalWidth && (image.currentSrc || image.src) === source) image.dataset.logoReady = 'true';
    };
    // Some browsers reject decode() for an otherwise valid completed image.
    // Completion plus naturalWidth provides the ordinary load-event fallback.
    if (typeof image.decode === 'function') image.decode().then(finish, finish);
    else finish();
  }
  document.addEventListener('load', event => reveal(event.target), true);
  const scan = () => document.querySelectorAll('.brand-mark').forEach(reveal);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan, {once: true});
  else scan();
})();

// Start the access check while the larger UI scripts are still downloading.
// app.js consumes this one response through its normal API/error handling.
(() => {
  if (window.CrucibleSessionBootstrap) return;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  const response = fetch('/api/session', {
    method: 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: {Accept: 'application/json'}, signal: controller.signal
  });
  // The UI may not be ready yet when a connection fails. Retain the rejection
  // for its consumer without emitting an unhandled-rejection event meanwhile.
  response.catch(() => {});
  window.CrucibleSessionBootstrap = {response, controller, finish: () => clearTimeout(timeout)};
})();

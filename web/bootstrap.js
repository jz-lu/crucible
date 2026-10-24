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

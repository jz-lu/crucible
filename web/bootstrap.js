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

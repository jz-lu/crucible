// Render only explicitly selected fields and explicit \(inline TeX\) spans.
// Prose is always appended as text; there is no page-wide auto-render or HTML input.
(() => {
  'use strict';

  const MAX_TEXT_LENGTH = 64000;
  const MAX_EXPRESSION_LENGTH = 2000;
  const MAX_EXPRESSIONS = 64;
  const script = document.currentScript;
  const assetURL = (name, path) => {
    const value = script?.getAttribute('data-katex-' + name);
    return value === path || (typeof value === 'string' && value.startsWith(path + '?v=') && /^[a-f0-9]{24}$/.test(value.slice(path.length + 3))) ? value : path;
  };
  const scriptURL = assetURL('script', '/vendor/katex/katex.min.js');
  const styleURL = assetURL('style', '/vendor/katex/katex.min.css');
  const pending = new WeakMap();
  const assetLoads = new Map(), attempts = new Map();
  let rendererReady = typeof window.katex?.render === 'function';
  let rendererLoad = null;

  function pendingElements() {
    return [...(document.querySelectorAll?.('[data-crucible-math-pending]') || [])].filter(element => {
      const entry = pending.get(element);
      if (entry && element.firstChild === entry.node && entry.node.textContent === entry.source) return true;
      pending.delete(element); element.removeAttribute('data-crucible-math-pending'); return false;
    });
  }
  function loadAsset(path, stylesheet) {
    if (assetLoads.has(path)) return assetLoads.get(path);
    const attempt = (attempts.get(path) || 0) + 1; attempts.set(path, attempt);
    const element = document.createElement(stylesheet ? 'link' : 'script');
    const url = path + (attempt > 1 ? (path.includes('?') ? '&' : '?') + 'asset_attempt=' + attempt : '');
    if (stylesheet) { element.rel = 'stylesheet'; element.href = url; }
    else { element.async = true; element.src = url; }
    const task = new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => finish(new Error('Math formatting timed out.')), 15000);
      function finish(error) {
        if (settled) return;
        settled = true; clearTimeout(timer); element.onload = element.onerror = null;
        if (error) {
          element.remove();
          if (assetLoads.get(path) === task) assetLoads.delete(path);
          reject(error);
        } else resolve();
      }
      element.onload = () => finish(!stylesheet && typeof window.katex?.render !== 'function' ?
        new Error('Math formatting is unavailable.') : undefined);
      element.onerror = () => finish(new Error('Math formatting is unavailable.'));
    });
    assetLoads.set(path, task); document.querySelector('head').append(element);
    return task;
  }
  function ensureRenderer() {
    if (rendererLoad || !document.querySelector?.('head')) return;
    rendererLoad = Promise.all([
      loadAsset(styleURL, true),
      loadAsset(scriptURL, false)
    ]).then(() => {
      if (typeof window.katex?.render !== 'function') throw new Error('Math formatting is unavailable.');
      rendererReady = true;
      for (const element of pendingElements()) render(element, pending.get(element).source);
    }).catch(() => {
      rendererLoad = null;
      for (const element of pendingElements()) {
        if (element.querySelector('.math-retry')) continue;
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'text-button math-retry';
        retry.textContent = 'Retry math formatting';
        retry.addEventListener('click', () => {
          for (const pendingElement of pendingElements()) pendingElement.querySelector('.math-retry')?.remove();
          ensureRenderer();
        });
        element.append(' ', retry);
      }
    });
  }

  function delimiter(source, token, from) {
    let at = source.indexOf(token, from);
    while (at !== -1) {
      let preceding = 0;
      for (let i = at - 1; i >= 0 && source[i] === '\\'; i--) preceding++;
      if (preceding % 2 === 0) return at;
      at = source.indexOf(token, at + token.length);
    }
    return -1;
  }

  function render(element, value) {
    const source = String(value ?? '');
    const document = element.ownerDocument;
    const katex = window.katex;
    pending.delete(element);
    element.removeAttribute?.('data-crucible-math-pending');
    element.replaceChildren();
    const start = delimiter(source, '\\(', 0);
    const hasExpression = start >= 0 && delimiter(source, '\\)', start + 2) >= 0;
    if (source.length > MAX_TEXT_LENGTH || !hasExpression) {
      element.append(source); return element;
    }
    if (!rendererReady || !katex || typeof katex.render !== 'function') {
      // Keep source only under a weak DOM key. Detached/replaced private content
      // is never retained in a global queue or resurrected by a late download.
      const fallback = element.ownerDocument.createElement('span');
      fallback.textContent = source; element.append(fallback);
      pending.set(element, {source, node: fallback});
      element.setAttribute('data-crucible-math-pending', '');
      ensureRenderer();
      return element;
    }

    let cursor = 0;
    let expressions = 0;
    while (cursor < source.length && expressions < MAX_EXPRESSIONS) {
      const start = delimiter(source, '\\(', cursor);
      if (start === -1) break;
      const end = delimiter(source, '\\)', start + 2);
      if (end === -1) break;
      element.append(source.slice(cursor, start));
      const original = source.slice(start, end + 2);
      const expression = source.slice(start + 2, end).trim();
      if (!expression || expression.length > MAX_EXPRESSION_LENGTH) {
        element.append(original);
      } else {
        const span = document.createElement('span');
        span.className = 'math-inline';
        try {
          katex.render(expression, span, {
            displayMode: false,
            output: 'htmlAndMathml',
            trust: false,
            throwOnError: false,
            strict: 'error',
            maxSize: 10,
            maxExpand: 1000,
            // Never let \gdef in one expression change another expression.
            macros: {},
          });
          if (span.querySelector('.katex-error')) span.textContent = original;
        } catch (_) {
          // Error messages can contain the input: preserve the source as text.
          span.textContent = original;
        }
        element.append(span);
      }
      cursor = end + 2;
      expressions++;
    }
    element.append(source.slice(cursor));
    return element;
  }

  window.CrucibleMath = Object.freeze({render});
})();

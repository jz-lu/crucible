// Render only explicitly selected fields and explicit \(inline TeX\) spans.
// Prose is always appended as text; there is no page-wide auto-render or HTML input.
(() => {
  'use strict';

  const MAX_TEXT_LENGTH = 64000;
  const MAX_EXPRESSION_LENGTH = 2000;
  const MAX_EXPRESSIONS = 64;
  // A readable section contains several paragraphs and definitions. Keep the
  // smaller bound for individual fields, with a separate bounded section budget.
  const MAX_READING_EXPRESSIONS = 128;
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
  let readingLayoutPending = false;

  function scheduleReadingLayout() {
    if (readingLayoutPending || typeof window.requestAnimationFrame !== 'function' || typeof window.getComputedStyle !== 'function') return;
    readingLayoutPending = true;
    window.requestAnimationFrame(() => {
      readingLayoutPending = false;
      // Inspect only live, visible reading content. No retained private DOM or
      // per-expression observers; measure first, then update styles together.
      const changes = [];
      for (const span of document.querySelectorAll('.nb-root:not(.nb-review-workspace).nb-note-reader .math-inline')) {
        if (!span.getClientRects().length) continue;
        const block = span.closest('p,li,h1,h2,h3,h4,h5,h6,.nb-reading-equation') || span.parentElement;
        const style = window.getComputedStyle(block);
        const available = block.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0);
        if (available <= 0) continue;
        const groups = [...span.querySelectorAll('.katex-html > .katex-base, .katex-html > .base')];
        const wide = groups.some(group => group.getBoundingClientRect().width > available + 1);
        if (span.classList.contains('math-wide') !== wide) changes.push({span,wide});
      }
      for (const {span,wide} of changes) {
        span.classList.toggle('math-wide',wide);
        if (wide) span.tabIndex = 0; else span.removeAttribute('tabindex');
      }
    });
  }
  window.addEventListener?.('resize', scheduleReadingLayout, {passive:true});
  document.fonts?.addEventListener?.('loadingdone', scheduleReadingLayout);

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
      for (const element of pendingElements()) render(element, pending.get(element).source, pending.get(element).options);
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

  function render(element, value, options = {}) {
    const source = String(value ?? '');
    const document = element.ownerDocument;
    const katex = window.katex;
    pending.delete(element);
    element.removeAttribute?.('data-crucible-math-pending');
    element.replaceChildren();
    const opening = options.displayMode ? '\\[' : '\\(', closing = options.displayMode ? '\\]' : '\\)';
    const start = delimiter(source, opening, 0);
    const hasExpression = start >= 0 && delimiter(source, closing, start + 2) >= 0;
    if (source.length > MAX_TEXT_LENGTH || !hasExpression) {
      element.append(source); return element;
    }
    if (!rendererReady || !katex || typeof katex.render !== 'function') {
      // Keep source only under a weak DOM key. Detached/replaced private content
      // is never retained in a global queue or resurrected by a late download.
      const fallback = element.ownerDocument.createElement('span');
      fallback.textContent = source; element.append(fallback);
      pending.set(element, {source, node: fallback, options: {displayMode: options.displayMode === true}});
      element.setAttribute('data-crucible-math-pending', '');
      ensureRenderer();
      return element;
    }

    let cursor = 0;
    let expressions = 0;
    while (cursor < source.length && expressions < MAX_EXPRESSIONS) {
      const start = delimiter(source, opening, cursor);
      if (start === -1) break;
      const end = delimiter(source, closing, start + 2);
      if (end === -1) break;
      element.append(source.slice(cursor, start));
      const original = source.slice(start, end + 2);
      const expression = source.slice(start + 2, end).trim();
      if (!expression || expression.length > MAX_EXPRESSION_LENGTH) {
        element.append(original);
      } else {
        const span = document.createElement('span');
        span.className = options.displayMode ? 'math-display' : 'math-inline';
        try {
          katex.render(expression, span, {
            displayMode: options.displayMode === true,
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
    scheduleReadingLayout();
    return element;
  }

  // A bounded, inert reading format. This deliberately shares the application's
  // lazy renderer and its retry/detached-node policy, rather than adding a
  // second Markdown/TeX loader. Full-text source citations still use body lines.
  function markdown(target, value, {onBlock, lineOffset = 0} = {}) {
    const source = String(value ?? '');
    const doc = target.ownerDocument;
    target.replaceChildren();
    if (source.length > MAX_TEXT_LENGTH) { target.append(source); return target; }
    const node = (tag, text) => { const n = doc.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
    let expressions = 0;
    function inline(parent, text) {
      const token = /\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|\/(?!\/)[^\s)]+|#[^\s)]+)\)/g;
      let at = 0, match;
      while ((match = token.exec(text))) {
        parent.append(text.slice(at, match.index));
        if (match[0].startsWith('\\(') || match[0].startsWith('\\[')) {
          if (delimiter(text, match[0].slice(0, 2), match.index) !== match.index) {
            parent.append(match[0]); at = token.lastIndex; continue;
          }
          const span = node('span');
          const displayMode = match[0].startsWith('\\[');
          if (displayMode) span.className = 'nb-reading-equation';
          if (++expressions <= MAX_READING_EXPRESSIONS) render(span, match[0], {displayMode}); else span.textContent = match[0];
          parent.append(span);
        } else if (match[3] !== undefined) {
          const link = node('a', match[3]);
          try {
            const internal = /^[/#]/.test(match[4]);
            const base = new URL(window.location?.href || 'https://crucible.invalid/');
            const url = new URL(match[4], base);
            if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error();
            if (internal && (url.origin !== base.origin || /[\\\u0000-\u001f\u007f]/.test(match[4]))) throw new Error();
            link.href = internal ? match[4] : url.href; link.rel = 'noreferrer noopener'; parent.append(link);
          } catch (_) { parent.append(match[0]); }
        } else parent.append(node(match[1] !== undefined ? 'strong' : 'code', match[1] ?? match[2]));
        at = token.lastIndex;
      }
      parent.append(text.slice(at));
    }
    const lines = source.replace(/\r\n?/g, '\n').split('\n');
    const structure = source.trim() && window.TheoryStore?.markdownStructure?.(source);
    const headings = structure ? new Map(structure.headings.map(item => [item.line,item])) : null;
    const fences = structure ? new Set(structure.fence_lines) : null, mathLines = structure ? new Set(structure.math_lines) : null;
    const headingAt = i => headings ? headings.get(i + 1) : (() => {
      const match = /^(#{1,6})[ \t]+(.+)$/.exec(lines[i]);
      return match ? {level:match[1].length,title:match[2].replace(/[ \t]+#+[ \t]*$/, '').trim()} : null;
    })();
    const fenceAt = (line, i) => (!fences || fences.has(i + 1)) && /^ {0,3}(`{3,}|~{3,})/.exec(line);
    const mathAt = (line, i) => (!mathLines || mathLines.has(i + 1)) && ['\\[','$$'].includes(line.trim());
    const append = (block, start, end, kind) => {
      if (onBlock) onBlock(block, start + 1, end, kind);
      else { block.setAttribute('data-markdown-start', start + 1 + lineOffset); block.setAttribute('data-markdown-end', end + lineOffset); target.append(block); }
    };
    for (let i = 0; i < lines.length;) {
      const startLine = i, line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (mathAt(line, i)) {
        const closing = line.trim() === '$$' ? '$$' : '\\]';
        const start = i++;
        while (i < lines.length && delimiter(lines[i], closing, 0) === -1) i++;
        const block = node('div'); block.className = 'nb-reading-equation';
        let text = lines.slice(start, i).join('\n'), endLine = i;
        if (i < lines.length) {
          endLine = i + 1;
          const end = delimiter(lines[i], closing, 0) + 2;
          text += '\n' + lines[i].slice(0, end);
          const remainder = lines[i].slice(end);
          if (remainder.trim()) lines[i] = remainder;
          else i++;
        }
        if (++expressions <= MAX_READING_EXPRESSIONS) render(block, text, {displayMode: true}); else block.textContent = text;
        append(block, startLine, endLine, 'math'); continue;
      }
      const fence = fenceAt(line, i);
      if (fence) {
        const body = []; i++;
        while (i < lines.length) {
          const close = /^ {0,3}(`+|~+)[ \t]*$/.exec(lines[i]);
          if (close && close[1][0] === fence[1][0] && close[1].length >= fence[1].length) break;
          body.push(lines[i++]);
        }
        if (i < lines.length) i++;
        const pre = node('pre'); pre.append(node('code', body.join('\n'))); append(pre, startLine, i, 'code'); continue;
      }
      const heading = headingAt(i);
      if (heading) { const h = node('h' + Math.min(heading.level + 2, 6)); inline(h, heading.title); i++; append(h, startLine, i, 'heading'); continue; }
      if (/^[-*] /.test(line)) {
        const list = node('ul');
        while (i < lines.length && /^[-*] /.test(lines[i])) { const li = node('li'); inline(li, lines[i++].slice(2)); list.append(li); }
        append(list, startLine, i, 'list'); continue;
      }
      const body = [];
      while (i < lines.length && lines[i].trim() && !mathAt(lines[i], i) && !headingAt(i) && !/^[-*] /.test(lines[i]) && !fenceAt(lines[i], i)) body.push(lines[i++]);
      const p = node('p'); inline(p, body.join('\n')); append(p, startLine, i, 'paragraph');
    }
    return target;
  }

  window.CrucibleMath = Object.freeze({render, markdown});
})();

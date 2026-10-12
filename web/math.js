// Render only explicitly selected fields and explicit \(inline TeX\) spans.
// Prose is always appended as text; there is no page-wide auto-render or HTML input.
(() => {
  'use strict';

  const MAX_TEXT_LENGTH = 64000;
  const MAX_EXPRESSION_LENGTH = 2000;
  const MAX_EXPRESSIONS = 64;

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
    element.replaceChildren();
    if (!katex || typeof katex.render !== 'function' || source.length > MAX_TEXT_LENGTH) {
      element.append(source);
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

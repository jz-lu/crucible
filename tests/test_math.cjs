// Exercise the real vendored renderer with a DOM double that forbids HTML input.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(document, tag = '', text = null) {
    this.ownerDocument = document;
    this.tagName = tag.toUpperCase();
    this.nodeType = text === null ? (tag === '#fragment' ? 11 : 1) : 3;
    this.data = text;
    this.childNodes = [];
    this.style = {};
    this.attributes = new Map();
    this.className = '';
  }
  set innerHTML(_) { throw new Error('HTML input is forbidden'); }
  get firstChild() { return this.childNodes[0] || null; }
  get textContent() { return this.nodeType === 3 ? this.data : this.childNodes.map(n => n.textContent).join(''); }
  set textContent(value) { this.replaceChildren(String(value)); }
  setAttribute(key, value) { this.attributes.set(key, String(value)); if (key === 'class') this.className = String(value); }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  appendChild(node) {
    if (node.nodeType === 11) this.childNodes.push(...node.childNodes);
    else this.childNodes.push(node);
    return node;
  }
  removeChild(node) { this.childNodes.splice(this.childNodes.indexOf(node), 1); return node; }
  append(...nodes) { for (const node of nodes) this.appendChild(typeof node === 'string' ? this.ownerDocument.createTextNode(node) : node); }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  querySelectorAll(selector) {
    const found = [];
    const match = node => selector.startsWith('.') ? node.className.split(/\s+/).includes(selector.slice(1)) : node.tagName === selector.toUpperCase();
    const visit = node => { for (const child of node.childNodes) { if (match(child)) found.push(child); visit(child); } };
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const document = {
  compatMode: 'CSS1Compat',
  createElement(tag) { return new Node(this, tag); },
  createElementNS(_, tag) { return this.createElement(tag); },
  createTextNode(text) { return new Node(this, '', String(text)); },
  createDocumentFragment() { return new Node(this, '#fragment'); },
};
const context = {document, console};
context.window = context;
vm.createContext(context);
const web = path.resolve(__dirname, '../web');
vm.runInContext(fs.readFileSync(path.join(web, 'vendor/katex/katex.min.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(web, 'math.js'), 'utf8'), context);
const target = () => document.createElement('p');
const render = text => { const node = target(); context.CrucibleMath.render(node, text); return node; };
let groups = 0;
function check(name, run) { run(); groups++; console.log('ok - ' + name); }

check('explicit inline expressions render with MathML and preserve prose as text', () => {
  const node = render(String.raw`Given \(c = g^r \bmod p\), compute \(\mathbb{Z}_q\).`);
  assert.equal(node.querySelectorAll('.math-inline').length, 2);
  assert.equal(node.querySelectorAll('.katex').length, 2);
  assert.equal(node.querySelectorAll('math').length, 2);
  assert.equal(node.childNodes[0].nodeType, 3);
  assert.equal(node.childNodes[0].textContent, 'Given ');
  assert.equal(node.childNodes.at(-1).textContent, '.');
});

check('dollars, display delimiters, escaped delimiters, and plain HTML stay literal', () => {
  for (const text of ['$x^2$', '$$x^2$$', String.raw`\[x^2\]`, String.raw`\\(x^2\\)`, '<img src=x onerror=alert(1)>']) {
    const node = render(text);
    assert.equal(node.textContent, text);
    assert.equal(node.querySelectorAll('.katex').length, 0);
    assert.equal(node.querySelectorAll('img').length, 0);
  }
});

check('malformed input is readable text and replaces earlier content', () => {
  for (const text of [String.raw`before \(x^2`, String.raw`\(\frac{a}{\)`, String.raw`\(\)`]) {
    const node = target();
    node.append(document.createElement('img'));
    context.CrucibleMath.render(node, text);
    assert.equal(node.textContent, text);
    assert.equal(node.querySelectorAll('img').length, 0);
  }
});

check('untrusted commands cannot create links, external images, or custom HTML', () => {
  for (const text of [String.raw`\(\href{javascript:alert(1)}{x}\)`, String.raw`\(\includegraphics{https://example.invalid/a.png}\)`, String.raw`\(\htmlClass{injected}{x}\)`, String.raw`\(\text{<script>alert(1)</script>}\)`]) {
    const node = render(text);
    assert.equal(node.querySelectorAll('a').length, 0);
    assert.equal(node.querySelectorAll('img').length, 0);
    assert.equal(node.querySelectorAll('script').length, 0);
    assert.equal(node.querySelectorAll('.injected').length, 0);
  }
});

check('global macros cannot persist into another expression', () => {
  render(String.raw`\(\gdef\custom{x}\custom\)`);
  const node = render(String.raw`\(\custom\)`);
  assert.ok(node.textContent.includes('\\custom'));
});

check('input, expression count, and expansion bounds keep oversized input literal', () => {
  const longExpression = '\\(' + 'x'.repeat(2001) + '\\)';
  assert.equal(render(longExpression).textContent, longExpression);
  const longText = 'a'.repeat(64001) + String.raw`\(x\)`;
  assert.equal(render(longText).textContent, longText);
  assert.equal(render(String.raw`\(x\)`.repeat(70)).querySelectorAll('.katex').length, 64);
  assert.equal(render(String.raw`\(\def\loop{\loop}\loop\)`).querySelectorAll('.katex').length, 0);
});

check('missing library and unexpected rendering errors preserve safe text', () => {
  const saved = context.katex;
  const text = String.raw`safe \(<img src=x>\)`;
  context.katex = undefined;
  assert.equal(render(text).textContent, text);
  context.katex = {render() { throw new Error('<img src=x>'); }};
  const node = render(text);
  assert.equal(node.textContent, text);
  assert.equal(node.querySelectorAll('img').length, 0);
  context.katex = saved;
});

console.log(`${groups} math rendering groups passed.`);

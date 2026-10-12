// A small HTML-backed DOM double for navigation regressions, not a layout engine.
// The app runs unmodified; network responses and browser history stay in memory.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
const decode = text => text.replace(/&(?:amp|lt|gt|quot|#39);/g, value => ({'&amp;':'&', '&lt;':'<', '&gt;':'>', '&quot;':'"', '&#39;':"'"}[value]));
class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  dispatchEvent(event) { event.target ||= this; event.currentTarget = this; event.preventDefault ||= () => { event.defaultPrevented = true; }; event.stopPropagation ||= () => { event.cancelBubble = true; }; for (const fn of this.listeners.get(event.type) || []) fn(event); if (event.bubbles && !event.cancelBubble && this.parentElement) this.parentElement.dispatchEvent(event); return !event.defaultPrevented; }
}
function matches(element, selector) {
  if (!element.tagName) return false;
  if (selector.endsWith(':checked')) return element.checked && matches(element, selector.slice(0, -8));
  const attributes = [...selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)];
  if (attributes.some(([,name,value]) => element.getAttribute(name) === null || (value !== undefined && element.getAttribute(name) !== value))) return false;
  selector = selector.replace(/\[[^\]]+\]/g, '');
  const id = selector.match(/#([\w-]+)/)?.[1];
  if (id && element.id !== id) return false;
  for (const [,name] of selector.matchAll(/\.([\w-]+)/g)) if (!element.classList.contains(name)) return false;
  const tag = selector.match(/^[\w-]+/)?.[0];
  return !tag || element.tagName === tag.toUpperCase();
}
class Element extends Events {
  constructor(tag, document) {
    super(); this.tagName = tag.toUpperCase(); this.ownerDocument = document; this.attributes = new Map(); this.childNodes = []; this.parentElement = null; this.style = {}; this.value = ''; this.disabled = false; this.hidden = false; this.open = false;
    this.dataset = new Proxy({}, {get: (_, key) => this.getAttribute('data-' + String(key).replace(/[A-Z]/g, c => '-' + c.toLowerCase())) || undefined, set: (_, key, value) => { this.setAttribute('data-' + String(key).replace(/[A-Z]/g, c => '-' + c.toLowerCase()), value); return true; }});
    this.classList = {contains: name => this.className.split(/\s+/).includes(name), add: name => { if (!this.classList.contains(name)) this.className = (this.className + ' ' + name).trim(); }, remove: name => { this.className = this.className.split(/\s+/).filter(item => item !== name).join(' '); }, toggle: (name, force) => { const on = force ?? !this.classList.contains(name); this.classList[on ? 'add' : 'remove'](name); return on; }};
  }
  get id() { return this.getAttribute('id') || ''; } set id(value) { this.setAttribute('id', value); }
  get className() { return this.getAttribute('class') || ''; } set className(value) { this.setAttribute('class', value); }
  get href() { return this.getAttribute('href') || ''; } set href(value) { this.setAttribute('href', value); }
  get children() { return this.childNodes.filter(child => child instanceof Element); }
  get isConnected() { for (let node = this; node; node = node.parentElement) if (node === this.ownerDocument) return true; return false; }
  get firstChild() { return this.childNodes[0] || null; }
  get childElementCount() { return this.children.length; }
  get options() { return this.children.filter(child => child.tagName === 'OPTION'); }
  get textContent() { return this.childNodes.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  set textContent(value) { this.replaceChildren(String(value ?? '')); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); if (name === 'hidden') this.hidden = true; if (name === 'disabled') this.disabled = true; if (name === 'open') this.open = true; if (name === 'value') this.value = String(value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'hidden') this.hidden = false; }
  append(...items) { for (const item of items) { if (item instanceof Element) item.parentElement = this; this.childNodes.push(item instanceof Element ? item : String(item)); } }
  prepend(...items) { const previous = this.childNodes; this.childNodes = []; this.append(...items, ...previous); }
  replaceChildren(...items) { for (const child of this.children) child.parentElement = null; this.childNodes = []; this.append(...items); }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(item => item.trim()); const result = [];
    const visit = node => { for (const child of node.children) { if (selectors.some(item => matches(child, item))) result.push(child); visit(child); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { for (let element = this; element; element = element.parentElement) if (matches(element, selector)) return element; return null; }
  focus() { this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
  reset() { for (const element of this.querySelectorAll('input, textarea, select')) element.value = ''; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatchEvent({type: 'close'}); }
  click() { if (this.disabled) return; const event = {type: 'click', bubbles: true}; this.dispatchEvent(event); if (!event.defaultPrevented && this.tagName === 'A' && this.href.startsWith('#')) this.ownerDocument.defaultView.location.hash = this.href; }
}
function parseHTML(source) {
  const document = new Element('document', null); document.ownerDocument = document; document.createElement = tag => new Element(tag, document); document.hidden = false;
  const stack = [document];
  for (const token of source.match(/<!--[\s\S]*?-->|<![^>]*>|<[^>]+>|[^<]+/g)) {
    if (token.startsWith('<!')) continue;
    if (token.startsWith('</')) { const tag = token.slice(2, -1).trim().toUpperCase(); while (stack.length > 1) if (stack.pop().tagName === tag) break; continue; }
    if (token.startsWith('<')) {
      const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
      const element = document.createElement(tag);
      for (const [, name, double, single, bare] of token.slice(tag.length + 1, -1).matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) element.setAttribute(name, decode(double ?? single ?? bare ?? ''));
      stack.at(-1).append(element); if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(element);
    } else stack.at(-1).append(decode(token));
  }
  document.body = document.querySelector('body'); document.activeElement = document.body; return document;
}
async function flush() { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); }
async function createApp({schemes, user = null, hash = '#challenge', responder} = {}) {
  const web = path.resolve(__dirname, '../web');
  const document = parseHTML(fs.readFileSync(path.join(web, 'index.html'), 'utf8'));
  const window = new Events(); window.scrollTo = () => {}; document.defaultView = window;
  let current = hash; const stack = [hash]; let position = 0;
  const applyHash = (value, notify) => { const parsed = String(value).split('#')[1]; current = parsed === undefined ? String(value) : '#' + parsed; if (notify) queueMicrotask(() => window.dispatchEvent({type: 'hashchange'})); };
  const location = {get hash() { return current; }, set hash(value) { if (value === current) return; stack.splice(position + 1); stack.push(value); position++; applyHash(value, true); }, get pathname() { return '/'; }, get search() { return ''; }, get href() { return 'http://127.0.0.1:8000/' + current; }};
  const history = {replaceState: (_, __, url) => { applyHash(url, false); stack[position] = current; }, pushState: (_, __, url) => { applyHash(url, false); stack.splice(position + 1); stack.push(current); position++; }, back: () => { if (position) { applyHash(stack[--position], false); window.dispatchEvent({type: 'popstate'}); window.dispatchEvent({type: 'hashchange'}); } }};
  window.location = location; window.history = history;
  const requests = []; const epoch = Date.now() / 1000;
  const envelope = payload => ({server_time: epoch, round: {id: Math.floor(epoch / 3600), starts_at: Math.floor(epoch / 3600) * 3600, ends_at: (Math.floor(epoch / 3600) + 1) * 3600}, ...payload});
  const fetch = async (input, options = {}) => {
    const url = new URL(input, 'http://127.0.0.1:8000'); requests.push({url, options});
    let data;
    if (responder) data = await responder(url, options, envelope);
    if (data === undefined) {
      if (url.pathname === '/api/session') data = envelope({user, csrf_token: 'test-csrf'});
      else if (url.pathname === '/api/schemes') data = envelope({schemes});
      else if (url.pathname === '/api/challenges') data = envelope({challenge: null});
      else if (url.pathname === '/api/leaderboard') data = envelope({entries: []});
      else if (url.pathname === '/api/targets') data = envelope({targets: []});
      else throw new Error('Unexpected mocked request: ' + url.pathname);
    }
    return {ok: true, status: 200, json: async () => data};
  };
  const sandbox = {document, window, location, history, fetch, URL, URLSearchParams, AbortController, Blob, TextEncoder, console, navigator: {}, crypto: {randomUUID: () => 'test-uuid'}, setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1};
  vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(web, 'app.js'), 'utf8'), sandbox, {filename: 'app.js'}); await flush();
  return {document, window, location, history, requests, sandbox, envelope, $: selector => { const element = document.querySelector(selector); if (!element) throw new Error('Missing element: ' + selector); return element; }, navigate: async next => { location.hash = next; await flush(); }};
}
module.exports = {createApp, flush};

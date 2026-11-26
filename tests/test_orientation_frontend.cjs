// Isolated tour controller: navigation is a callback; no application/network runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('./frontend-dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../web/orientation.js'), 'utf8');
const stages = [
  {route:'#challenge', target:'.catalogue-heading', title:'Find a scheme', body:'Browse the schemes.'},
  {route:'#notebook', target:'.nb-heading', title:'Read the evidence', body:'Inspect an existing note.'},
  {route:'#bounties', target:'.bty-heading', title:'Find a task', body:'Read an open research task.'},
  {route:'#activity?kind=notes', target:'.activity-tabs', title:'Follow your work', body:'Find your notes and their status.'},
];

function harness({identity='researcher:one', steps=stages, targets=true, navigate}={}) {
  const document = parseHTML('<body><button id="launcher">Take a tour</button><main><h1>Current page</h1><button id="site-action">Publish</button></main></body>');
  const prototype = Object.getPrototypeOf(document);
  prototype.removeEventListener = function(type, callback) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== callback)); };
  prototype.hasAttribute = function(name) { return this.getAttribute(name) !== null; };
  prototype.getClientRects = function() { return this.isConnected && !this.closest('[hidden]') ? [this.getBoundingClientRect()] : []; };
  prototype.getBoundingClientRect = function() { return this.rect || {left:20, top:120, right:400, bottom:180, width:380, height:60}; };
  const listeners = new Map(), timers = new Map(), observers = new Set();
  let currentIdentity = identity, hash = '#home', timerId = 0, siteActions = 0;
  const window = {
    innerWidth:1200, innerHeight:800,
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    dispatch(type) { for (const callback of [...(listeners.get(type) || [])]) callback({type}); },
    getComputedStyle() { return {visibility:'visible'}; },
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  window.location = {get hash() { return hash; }, set hash(value) { hash=value; window.dispatch('hashchange'); }};
  document.defaultView = window;
  const calls = [], launcher = document.querySelector('#launcher');
  document.querySelector('#site-action').addEventListener('click', () => siteActions++);
  const addTarget = (selector, rect) => { const element=document.createElement('h2'); element.className=selector.slice(1); element.textContent='Existing page content'; element.rect=rect; document.querySelector('main').append(element); return element; };
  if (targets) for (const stage of stages) addTarget(stage.target);
  const sandbox = {window, document, MutationObserver:class {
    constructor(callback) { this.callback=callback; }
    observe() { observers.add(this); }
    disconnect() { observers.delete(this); }
  }, fetch() { throw Error('The tour must never request data'); }};
  vm.runInNewContext(source, sandbox, {filename:'orientation.js'});
  const controller = window.CrucibleOrientation.create({launcher, context:()=>currentIdentity, steps:()=>steps, navigate:route=>{calls.push(route); if (navigate) navigate(route, window); else window.location.hash=route;}});
  return {controller, window, document, calls, timers, observers, launcher, listeners, addTarget,
    setIdentity(value) { currentIdentity=value; },
    mutate() { for (const observer of [...observers]) observer.callback(); },
    expire() { for (const [id, timer] of [...timers]) { timers.delete(id); timer.callback(); } },
    siteActions:()=>siteActions,
    dialog:()=>document.querySelector('.orientation-tour'),
    button(label) { return document.querySelector('.orientation-controls').querySelectorAll('button').find(button=>button.textContent===label); },
  };
}
let passed=0;
function test(name, run) { run(); console.log('PASS '+name); passed++; }
const closed = app => { assert.equal(app.dialog(),null); assert.equal(app.observers.size,0); assert.equal(app.timers.size,0); for(const callbacks of app.listeners.values()) assert.equal(callbacks.size,0); };

test('creation is inert and explicit start only navigates the supplied read routes',()=>{
  const app=harness(); assert.equal(app.dialog(),null); assert.deepEqual(app.calls,[]);
  assert.equal(app.controller.start(),true); assert.equal(app.controller.start(),false);
  assert.equal(app.dialog().open,true); assert.deepEqual(app.calls,['#challenge']);
  assert.equal(app.button('Back').disabled,true);
  app.button('Next').click(); app.button('Back').click(); app.button('Next').click(); app.button('Next').click(); app.button('Next').click();
  assert.equal(app.document.querySelector('.orientation-progress').textContent,'Step 4 of 4');
  app.button('Finish').click(); closed(app);
  assert.equal(app.window.location.hash,'#activity?kind=notes');
  assert.deepEqual(app.calls,['#challenge','#notebook','#challenge','#notebook','#bounties','#activity?kind=notes']);
  assert.equal(app.siteActions(),0); assert.equal(app.document.activeElement,app.launcher);
});

test('no account, an existing modal, or an action route prevents start',()=>{
  const anonymous=harness({identity:null}); assert.equal(anonymous.controller.start(),false); assert.deepEqual(anonymous.calls,[]);
  const busy=harness(), other=busy.document.createElement('dialog'); busy.document.body.append(other); other.showModal();
  assert.equal(busy.controller.start(),false); assert.deepEqual(busy.calls,[]); other.close(); assert.equal(busy.controller.start(),true); busy.controller.cancel();
  for(const route of ['#notebook?compose=proposal','#bounties?compose=bounty','https://outside.invalid/']) {
    const invalid=harness({steps:[{...stages[0],route}]}); assert.equal(invalid.controller.start(),false); assert.deepEqual(invalid.calls,[]);
  }
});

test('title and description are labelled text and the native modal owns focus',()=>{
  const app=harness({steps:[{...stages[0],title:'<img src=x>',body:'<script>publish()</script>'}]}); app.controller.start();
  const title=app.document.querySelector('.orientation-title'),body=app.document.querySelector('.orientation-body');
  assert.equal(title.textContent,'<img src=x>'); assert.equal(body.textContent,'<script>publish()</script>');
  assert.equal(app.document.querySelector('img'),null); assert.equal(app.document.querySelector('script'),null);
  assert.equal(app.dialog().getAttribute('aria-labelledby'),title.id); assert.equal(app.dialog().getAttribute('aria-describedby'),body.id);
  assert.equal(app.document.activeElement,title); assert.equal(app.document.querySelector('.orientation-ring').getAttribute('aria-hidden'),'true');
  app.controller.cancel(); closed(app);
});

test('close button, Escape, backdrop, native close and repeated cancellation clean up',()=>{
  for(const action of ['button','escape','backdrop','native','cancel']) {
    const app=harness(); app.controller.start();
    app.document.querySelector('.orientation-card').dispatchEvent({type:'click',bubbles:true}); assert.ok(app.dialog());
    if(action==='button') app.button('Close tour').click();
    if(action==='escape') { const event={type:'cancel'}; app.dialog().dispatchEvent(event); assert.equal(event.defaultPrevented,true); }
    if(action==='backdrop') app.dialog().dispatchEvent({type:'click'});
    if(action==='native') app.dialog().close();
    if(action==='cancel') app.controller.cancel();
    app.controller.cancel(); closed(app); assert.deepEqual(app.calls,['#challenge']); assert.equal(app.document.activeElement,app.launcher);
  }
});

test('Tab wraps at the fixed controls while normal intermediate traversal stays native',()=>{
  const app=harness(); app.controller.start();
  const pressTab = (focused, shiftKey=false) => {
    focused.focus(); const event={type:'keydown',key:'Tab',shiftKey}; app.dialog().dispatchEvent(event); return event;
  };
  const title=app.document.querySelector('.orientation-title'), close=app.button('Close tour'), next=app.button('Next');
  assert.equal(pressTab(close).defaultPrevented,true); assert.equal(app.document.activeElement,next);
  assert.equal(pressTab(next,true).defaultPrevented,true); assert.equal(app.document.activeElement,close);
  assert.equal(pressTab(title,true).defaultPrevented,true); assert.equal(app.document.activeElement,close);
  assert.equal(pressTab(next).defaultPrevented,undefined);
  next.click(); const back=app.button('Back'); assert.equal(back.disabled,false);
  assert.equal(pressTab(close).defaultPrevented,true); assert.equal(app.document.activeElement,back);
  assert.equal(pressTab(back,true).defaultPrevented,true); assert.equal(app.document.activeElement,close);
  assert.equal(pressTab(next,true).defaultPrevented,undefined);
  assert.equal(pressTab(back).defaultPrevented,undefined);
  app.controller.cancel(); closed(app);
});

test('changed account and manual route changes stop the tour before another callback',()=>{
  for(const trigger of ['check','next','hash','popstate']) {
    const app=harness(); app.controller.start();
    if(trigger==='hash') app.window.location.hash='#notebook?note=example';
    else if(trigger==='popstate') { app.setIdentity(null); app.window.dispatch('popstate'); }
    else { app.setIdentity('researcher:two'); if(trigger==='check') assert.equal(app.controller.checkContext(),false); else app.button('Next').click(); }
    closed(app); assert.deepEqual(app.calls,['#challenge']); assert.equal(app.siteActions(),0);
  }
  const mismatch=harness({navigate:(_,window)=>{window.location.hash='#home';}}); assert.equal(mismatch.controller.start(),false); closed(mismatch);
  const failure=harness({navigate:()=>{throw Error('Navigation unavailable');}}); assert.equal(failure.controller.start(),false); closed(failure);
});

test('pagehide cancels without moving focus and hidden launcher falls back to page heading',()=>{
  const app=harness(); app.controller.start(); const focused=app.document.activeElement;
  app.window.dispatch('pagehide'); closed(app); assert.equal(app.document.activeElement,focused);
  const fallback=harness(); fallback.controller.start(); fallback.launcher.hidden=true; fallback.controller.cancel(); closed(fallback);
  assert.equal(fallback.document.activeElement,fallback.document.querySelector('h1'));
  assert.equal(fallback.document.querySelector('h1').hasAttribute('tabindex'),false);
});

test('late targets gain an outline, layout follows scroll, and target waiting is bounded',()=>{
  const app=harness({targets:false}); app.controller.start();
  assert.equal(app.document.querySelector('.orientation-title').textContent,'Find a scheme');
  assert.equal(app.document.querySelector('.orientation-ring').hidden,true); assert.equal(app.observers.size,1);
  assert.equal([...app.timers.values()][0].delay,8000);
  const target=app.addTarget('.catalogue-heading',{left:30,top:80,right:430,bottom:140,width:400,height:60}); app.mutate();
  const ring=app.document.querySelector('.orientation-ring'); assert.equal(ring.hidden,false); assert.equal(ring.style.top,'74px');
  target.rect={...target.rect,top:20,bottom:80}; app.window.dispatch('scroll'); assert.equal(ring.style.top,'14px');
  app.expire(); assert.equal(app.observers.size,0); assert.equal(app.timers.size,0);
  app.button('Next').click(); assert.equal(ring.hidden,true); app.expire();
  app.button('Next').click(); assert.equal(app.window.location.hash,'#bounties'); app.controller.cancel(); closed(app);
});

test('card avoids the highlighted corner and uses a bottom card on mobile',()=>{
  const app=harness(); app.controller.start();
  const card=app.document.querySelector('.orientation-card'), target=app.document.querySelector('.catalogue-heading');
  card.rect={width:360,height:220}; target.rect={left:800,top:550,right:1180,bottom:780,width:380,height:230};
  app.window.dispatch('resize'); assert.equal(card.style.left,'16px'); assert.equal(card.style.top,'564px');
  app.window.innerWidth=390; card.rect={width:358,height:220}; app.window.dispatch('resize');
  assert.equal(card.style.left,'16px'); assert.equal(card.style.top,'564px'); app.controller.cancel();
});

test('the supplied shorter itinerary controls progress and no excluded route is visited',()=>{
  const app=harness({steps:stages.filter(step=>step.route!=='#bounties')}); app.controller.start();
  app.button('Next').click(); app.button('Next').click(); assert.equal(app.document.querySelector('.orientation-progress').textContent,'Step 3 of 3');
  app.button('Finish').click(); assert.deepEqual(app.calls,['#challenge','#notebook','#activity?kind=notes']); closed(app);
});

console.log(`${passed} orientation frontend tests passed`);

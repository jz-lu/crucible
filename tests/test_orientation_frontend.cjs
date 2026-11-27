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

function harness({identity='researcher:one', steps=stages, targets=true, navigate,features=null,initialHash='#home',animationFrames=false}={}) {
  const document = parseHTML('<body><button id="launcher">Take a tour</button><main><h1>Current page</h1><button id="site-action">Publish</button></main></body>');
  const prototype = Object.getPrototypeOf(document);
  prototype.removeEventListener = function(type, callback) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== callback)); };
  prototype.hasAttribute = function(name) { return this.getAttribute(name) !== null; };
  prototype.getClientRects = function() { return this.isConnected && !this.closest('[hidden]') ? [this.getBoundingClientRect()] : []; };
  prototype.getBoundingClientRect = function() { return this.rect || {left:20, top:120, right:400, bottom:180, width:380, height:60}; };
  const listeners = new Map(), timers = new Map(), observers = new Set(), frames = [];
  let currentIdentity = identity, hash = initialHash, timerId = 0, siteActions = 0;
  const window = {
    innerWidth:1200, innerHeight:800,
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    dispatch(type) { for (const callback of [...(listeners.get(type) || [])]) callback({type}); },
    getComputedStyle() { return {visibility:'visible'}; },
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  if(animationFrames) window.requestAnimationFrame=callback=>frames.push(callback);
  window.location = {get hash() { return hash; }, set hash(value) { hash=value; window.dispatch('hashchange'); }};
  document.defaultView = window;
  const calls = [], layouts = [], stops = [], launcher = document.querySelector('#launcher');
  document.querySelector('#site-action').addEventListener('click', () => siteActions++);
  const addTarget = (selector, rect) => { const element=document.createElement('h2'); element.className=selector.slice(1); element.textContent='Existing page content'; element.rect=rect; document.querySelector('main').append(element); return element; };
  if (targets) for (const stage of stages) addTarget(stage.target);
  const sandbox = {window, document, MutationObserver:class {
    constructor(callback) { this.callback=callback; }
    observe() { observers.add(this); }
    disconnect() { observers.delete(this); }
  }, fetch() { throw Error('The tour must never request data'); }};
  vm.runInNewContext(source, sandbox, {filename:'orientation.js'});
  const presentation={create:()=>{const svg=document.createElement('svg');svg.className='guide-overlay';return svg;},place:value=>{layouts.push(value);if(value.minimized)value.overlay.setAttribute('hidden','');else value.overlay.removeAttribute('hidden');}};
  const controller = window.CrucibleOrientation.create({launcher,presentation,onStop:reason=>stops.push(reason),context:()=>currentIdentity, steps:route=>features?steps.filter(step=>step.route===(route||hash)):steps,overview:features?()=>features:undefined,isHome:()=>hash==='#home',allowedRoute:features?route=>stages.some(step=>step.route===route)||route===hash:undefined,navigate:route=>{calls.push(route); if (navigate) navigate(route, window); else window.location.hash=route;}});
  return {controller, window, document, calls, layouts, stops, timers, observers, launcher, listeners, addTarget,
    setIdentity(value) { currentIdentity=value; },
    mutate() { for (const observer of [...observers]) observer.callback(); },
    expire() { for (const [id, timer] of [...timers]) { timers.delete(id); timer.callback(); } },
    frame() { for(const callback of frames.splice(0)) callback(); },
    siteActions:()=>siteActions,
    dialog:()=>document.querySelector('.orientation-tour'),
    button(label) { return document.querySelector('.orientation-card').querySelectorAll('button').find(button=>button.textContent===label||button.getAttribute('aria-label')===label); },
  };
}
let passed=0;
function test(name,run){run();console.log('PASS '+name);passed++;}
const closed=app=>{assert.equal(app.dialog(),null);assert.equal(app.document.querySelector('.orientation-card'),null);assert.equal(app.observers.size,0);assert.equal(app.timers.size,0);for(const callbacks of app.listeners.values())assert.equal(callbacks.size,0);};

test('creation is inert and an explicit tour follows only its four read routes',()=>{
 const app=harness();assert.equal(app.dialog(),null);assert.deepEqual(app.calls,[]);
 assert.equal(app.controller.start(),true);assert.equal(app.controller.start(),false);assert.equal(app.button('Back').disabled,true);
 app.button('Next').click();app.button('Back').click();app.button('Next').click();app.button('Next').click();app.button('Next').click();
 assert.equal(app.document.querySelector('.orientation-progress').textContent,'4 / 4');app.button('Finish').click();closed(app);
 assert.deepEqual(app.calls,['#challenge','#notebook','#challenge','#notebook','#bounties','#activity?kind=notes']);
 assert.equal(app.window.location.hash,'#activity?kind=notes');assert.equal(app.siteActions(),0);assert.deepEqual(app.stops,['complete']);
});

test('no account, an existing editor or an action route prevents start',()=>{
 const anonymous=harness({identity:null});assert.equal(anonymous.controller.start(),false);assert.deepEqual(anonymous.calls,[]);
 const busy=harness(),other=busy.document.createElement('dialog');busy.document.body.append(other);other.showModal();
 assert.equal(busy.controller.start(),false);other.close();assert.equal(busy.controller.start(),true);busy.controller.cancel();
 for(const route of ['#notebook?compose=proposal','#bounties?compose=bounty','https://outside.invalid/']){
  const invalid=harness({steps:[{...stages[0],route}]});assert.equal(invalid.controller.start(),false);assert.deepEqual(invalid.calls,[]);
 }
});

test('the native modal labels its title and body and keeps copied text inert',()=>{
 const app=harness({steps:[{...stages[0],title:'<img src=x>',body:'<script>publish()</script>'}]});app.controller.start();
 const title=app.document.querySelector('.orientation-title'),body=app.document.querySelector('.orientation-body');
 assert.equal(title.textContent,'<img src=x>');assert.equal(body.textContent,'<script>publish()</script>');assert.equal(app.document.querySelector('img'),null);
 assert.equal(app.dialog().getAttribute('aria-labelledby'),title.id);assert.equal(app.dialog().getAttribute('aria-describedby'),body.id);
 assert.equal(app.document.activeElement,title);assert.equal(app.button('Exit guide').textContent,'×');app.controller.cancel();closed(app);
});

test('the cross, native close and explicit cancellation exit and clean up once',()=>{
 for(const action of ['cross','native','cancel']){
  const app=harness();app.controller.start();
  if(action==='cross')app.button('Exit guide').click();if(action==='native')app.dialog().close();if(action==='cancel')app.controller.cancel();
  app.controller.cancel();closed(app);assert.deepEqual(app.calls,['#challenge']);assert.equal(app.document.activeElement,app.launcher);assert.deepEqual(app.stops,['close']);
 }
});

test('Hide guide, Escape and the backdrop minimize without ending progress',()=>{
 for(const action of ['button','escape','backdrop']){
  const app=harness();app.controller.start();app.button('Next').click();
  if(action==='button')app.button('Hide guide').click();
  if(action==='escape'){const event={type:'cancel'};app.dialog().dispatchEvent(event);assert.equal(event.defaultPrevented,true);}
  if(action==='backdrop')app.dialog().dispatchEvent({type:'click'});
  assert.equal(app.dialog().open,false);assert.ok(app.document.querySelector('.orientation-card').classList.contains('is-minimized'));assert.deepEqual(app.stops,[]);
  assert.equal(app.observers.size,0);assert.equal(app.timers.size,0);assert.equal(app.layouts.at(-1).minimized,true);
  app.window.location.hash='#challenge';assert.ok(app.dialog());app.button('Continue guide').click();
  assert.equal(app.dialog().open,true);assert.equal(app.window.location.hash,'#notebook');assert.equal(app.document.querySelector('.orientation-progress').textContent,'2 / 4');
  app.button('Hide guide').click();app.button('Exit guide').click();closed(app);assert.equal(app.siteActions(),0);
 }
});

test('a minimized guide cannot open above a new editing dialog',()=>{
 const app=harness();app.controller.start();app.controller.minimize();const calls=app.calls.length;
 const editor=app.document.createElement('dialog');app.document.body.append(editor);editor.showModal();
 assert.equal(app.controller.start(),false);assert.equal(app.calls.length,calls);assert.equal(app.dialog().open,false);
 editor.close();assert.equal(app.controller.start(),true);assert.equal(app.dialog().open,true);app.controller.cancel();closed(app);
});

test('Tab wraps across the clear header controls and bottom navigation',()=>{
 const app=harness();app.controller.start();
 const press=(control,shiftKey=false)=>{control.focus();const event={type:'keydown',key:'Tab',shiftKey};app.dialog().dispatchEvent(event);return event;};
 const hide=app.button('Hide guide'),next=app.button('Next'),close=app.button('Exit guide'),title=app.document.querySelector('.orientation-title');
 assert.equal(press(next).defaultPrevented,true);assert.equal(app.document.activeElement,hide);
 assert.equal(press(hide,true).defaultPrevented,true);assert.equal(app.document.activeElement,next);
 assert.equal(press(title,true).defaultPrevented,true);assert.equal(app.document.activeElement,close);
 assert.equal(press(close).defaultPrevented,undefined);app.controller.cancel();closed(app);
});

test('session changes and expanded manual navigation retire the tour before another callback',()=>{
 for(const trigger of ['check','next','hash','minimized-session']){
  const app=harness();app.controller.start();
  if(trigger==='hash')app.window.location.hash='#notebook?note=example';
  else {if(trigger==='minimized-session')app.controller.minimize();app.setIdentity('researcher:two');if(trigger==='next')app.button('Next').click();else app.controller.checkContext();}
  closed(app);assert.deepEqual(app.calls,['#challenge']);assert.equal(app.siteActions(),0);
 }
 const failure=harness({navigate:()=>{throw Error('Unavailable');}});assert.equal(failure.controller.start(),false);closed(failure);
});

test('pagehide cancels without moving focus and a hidden launcher falls back to a heading',()=>{
 const app=harness();app.controller.start();const focused=app.document.activeElement;app.window.dispatch('pagehide');closed(app);assert.equal(app.document.activeElement,focused);
 const fallback=harness();fallback.controller.start();fallback.launcher.hidden=true;fallback.controller.cancel();closed(fallback);assert.equal(fallback.document.activeElement,fallback.document.querySelector('h1'));
});

test('late targets use the shared presentation and the target watch is bounded',()=>{
 const app=harness({targets:false});app.controller.start();assert.equal(app.layouts.at(-1).target,null);assert.equal(app.observers.size,1);
 assert.equal([...app.timers.values()][0].delay,8000);const target=app.addTarget('.catalogue-heading');app.mutate();
 assert.equal(app.layouts.at(-1).target,target);assert.ok(target.classList.contains('orientation-target'));
 app.window.dispatch('scroll');assert.equal(app.layouts.at(-1).target,target);app.expire();assert.equal(app.observers.size,0);assert.equal(app.timers.size,0);
 app.button('Next').click();assert.equal(target.classList.contains('orientation-target'),false);app.controller.cancel();closed(app);
});

test('a shorter capability-based itinerary omits excluded pages',()=>{
 const app=harness({steps:stages.filter(step=>step.route!=='#bounties')});app.controller.start();app.button('Next').click();app.button('Next').click();
 assert.equal(app.document.querySelector('.orientation-progress').textContent,'3 / 3');app.button('Finish').click();
 assert.deepEqual(app.calls,['#challenge','#notebook','#activity?kind=notes']);closed(app);
});

const features=[{route:'#challenge',title:'Schemes',body:'Explore constructions.',icon:'schemes'},{route:'#notebook',title:'Notes',body:'Read and share evidence.',icon:'notes'}];
test('Home overview is inert until a feature is chosen and can summarize another page',()=>{
 const app=harness({features});assert.equal(app.controller.start(),true);assert.deepEqual(app.calls,[]);
 assert.equal(app.document.querySelector('.orientation-title').textContent,'Explore Crucible');
 app.document.querySelectorAll('.orientation-feature')[1].click();assert.deepEqual(app.calls,['#notebook']);
 assert.equal(app.document.querySelector('.orientation-title').textContent,'Read the evidence');
 app.button('All features').click();assert.equal(app.window.location.hash,'#notebook');
 assert.equal(app.document.querySelector('.orientation-title').textContent,'Explore Crucible');app.controller.cancel();closed(app);
});
test('page help highlights only the current page without navigating or reloading it',()=>{
 const app=harness({features,initialHash:'#notebook'});app.controller.start();
 assert.equal(app.document.querySelector('.orientation-title').textContent,'Read the evidence');assert.deepEqual(app.calls,[]);
 app.button('Finish').click();closed(app);assert.equal(app.window.location.hash,'#notebook');assert.equal(app.siteActions(),0);
});
test('a minimized overview resumes in place and owner changes retire its choices',()=>{
 const app=harness({features});app.controller.start();app.controller.minimize();app.window.location.hash='#notebook';
 app.button('Continue guide').click();assert.equal(app.document.querySelector('.orientation-title').textContent,'Explore Crucible');assert.deepEqual(app.calls,[]);
 app.setIdentity('another-owner');app.controller.checkContext();closed(app);
});
test('page tour reveals its native target after navigation resets scroll',()=>{
 const app=harness({features,animationFrames:true});app.controller.start();
 const target=app.document.querySelector('.catalogue-heading');let reveals=0;
 target.scrollIntoView=()=>reveals++;
 app.document.querySelector('.orientation-feature').click();
 target.rect={top:920,bottom:1000};app.frame();assert.equal(reveals,0);app.frame();assert.equal(reveals,1);
 app.controller.cancel();closed(app);
});
test('deferred target reveals retire with their step, minimized guide or account',()=>{
 for(const retire of ['step','minimize','owner','close']){
  const app=harness({animationFrames:true});app.controller.start();
  const target=app.document.querySelector('.catalogue-heading');let reveals=0;
  target.scrollIntoView=()=>reveals++;target.rect={top:920,bottom:1000};
  if(retire==='step')app.button('Next').click();
  if(retire==='minimize')app.controller.minimize();
  if(retire==='owner')app.setIdentity('another-owner');
  if(retire==='close')app.controller.cancel();
  app.frame();app.frame();assert.equal(reveals,0);app.controller.cancel();closed(app);
 }
});
test('a tall phone target leaves room for the prompt instead of centering beneath it',()=>{
 for(const offscreen of [false,true]){
  const app=harness({features});app.window.innerWidth=320;app.window.innerHeight=850;
  app.controller.start();const target=app.document.querySelector('.catalogue-heading'),reveals=[];
  const centered={top:260,bottom:592,width:263,height:332};
  target.rect=offscreen?{...centered,top:572,bottom:904}:centered;
  target.scrollIntoView=options=>{reveals.push(options.block);if(options.block==='center')target.rect=centered;};
  app.document.querySelector('.orientation-card').rect={width:288,height:277};
  app.document.querySelector('.orientation-feature').click();assert.deepEqual(reveals,offscreen?['center','start']:['start']);
  app.controller.cancel();closed(app);
 }
});
console.log(`${passed} orientation frontend tests passed`);

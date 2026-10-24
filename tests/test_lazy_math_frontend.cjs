const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const source = fs.readFileSync(path.join(__dirname, '../web/math.js'), 'utf8');
const scheme = {id:'fixture',version:'1',name:'Fixture',profiles:[],protocol:{}};
const assets = app => app.document.querySelectorAll('script, link').filter(node =>
  (node.src || node.href || '').startsWith('/vendor/katex/'));
async function fixture({versioned = true, malformed = false} = {}) {
  const app = await createApp({schemes:[scheme],user:{id:'alice',handle:'alice'}});
  const script = app.document.querySelectorAll('script').find(node => node.getAttribute('src') === '/math.js');
  if (versioned) for (const [name,file] of [['script','js'],['style','css']]) script.setAttribute('data-katex-'+name,
    malformed ? 'https://foreign.invalid/code.'+file : '/vendor/katex/katex.min.'+file+'?v='+'a'.repeat(24));
  app.document.currentScript = script;
  const timers = new Map(); let sequence=0;
  app.sandbox.setTimeout=(callback,delay)=>{const id=++sequence;timers.set(id,{callback,delay});return id;};
  app.sandbox.clearTimeout=id=>timers.delete(id);
  vm.runInContext(source,app.sandbox,{filename:'math.js'});
  app.document.currentScript=null;
  const target=()=>{const node=app.document.createElement('p');app.$('#catalogue-workspace').append(node);return node;};
  const rendered=[];
  const renderer=()=>{app.window.katex={render(expression,node){rendered.push(expression);node.className='rendered-math';node.textContent=expression;}};};
  return {...app,timers,target,renderer,rendered,render:(node,text)=>app.window.CrucibleMath.render(node,text)};
}
let passed=0;
async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
(async()=>{
  await test('landing is usable without loading a renderer, and ordinary prose never starts it',async()=>{
    const app=await fixture();
    assert.equal(app.$('#catalogue-workspace').hidden,false);
    assert.equal(assets(app).length,0);
    for(const text of ['Plain text','$x$',String.raw`\\(x\\)`,String.raw`\(unclosed`,'a'.repeat(64001)+String.raw`\(x\)`]) {
      const node=app.target();app.render(node,text);assert.equal(node.textContent,text);
    }
    assert.equal(assets(app).length,0);assert.equal(app.timers.size,0);
  });
  await test('first math starts both fixed versioned assets in parallel and hydrates after both finish',async()=>{
    const app=await fixture(),one=app.target(),two=app.target();
    app.render(one,String.raw`Before \(x^2\) after`);app.render(two,String.raw`\(y\)`);
    assert.equal(assets(app).length,2);
    const [css,js]=assets(app);assert.match(css.href,/\.css\?v=a{24}$/);assert.match(js.src,/\.js\?v=a{24}$/);assert.equal(js.async,true);
    app.renderer();js.onload();await flush();assert.equal(app.rendered.length,0,'CSS is still pending');
    css.onload();await flush();assert.deepEqual(app.rendered,['x^2','y']);
    assert.equal(one.textContent,'Before x^2 after');assert.equal(one.getAttribute('data-crucible-math-pending'),null);
    assert.equal(app.timers.size,0);
  });
  await test('late renderer hydrates only current owned nodes, never detached or replaced private content',async()=>{
    const app=await fixture(),replaced=app.target(),detached=app.target(),updated=app.target();
    app.render(replaced,String.raw`\(old-private\)`);app.render(detached,String.raw`\(removed-private\)`);app.render(updated,String.raw`\(old-version\)`);
    replaced.textContent='Access unavailable';detached.remove();app.render(updated,String.raw`\(current\)`);
    app.renderer();for(const asset of assets(app))asset.onload();await flush();
    assert.deepEqual(app.rendered,['current']);assert.equal(replaced.textContent,'Access unavailable');
    assert.equal(detached.textContent,String.raw`\(removed-private\)`);assert.equal(updated.textContent,'current');
  });
  await test('script failure keeps safe text and successful CSS; retry has a fresh URL and ignores old callbacks',async()=>{
    const app=await fixture(),node=app.target();app.render(node,String.raw`\(x\)`);
    const [css,old]=assets(app),late=old.onload;css.onload();old.onerror();await flush();
    assert.match(node.textContent,/\\\(x\\\).*Retry math formatting/);
    node.querySelector('.math-retry').click();await flush();
    const current=assets(app).find(asset=>asset.src);assert.notEqual(current,old);assert.match(current.src,/\?v=a{24}&asset_attempt=2$/);
    assert.equal(assets(app).filter(asset=>asset.href).length,1);late();await flush();assert.equal(app.rendered.length,0);
    app.renderer();current.onload();await flush();assert.equal(node.textContent,'x');assert.deepEqual(app.rendered,['x']);
  });
  await test('a loaded script without its renderer is a retryable failure, not a cached success',async()=>{
    const app=await fixture(),node=app.target();app.render(node,String.raw`\(x\)`);
    const [css,old]=assets(app);css.onload();old.onload();await flush();
    assert.match(node.textContent,/Retry math formatting/);
    node.querySelector('.math-retry').click();await flush();
    const fresh=assets(app).find(asset=>asset.src);assert.notEqual(fresh,old);assert.match(fresh.src,/asset_attempt=2$/);
    assert.equal(assets(app).filter(asset=>asset.href).length,1);
    app.renderer();fresh.onload();await flush();assert.equal(node.textContent,'x');
  });
  await test('stylesheet timeout can retry while its original request is still held',async()=>{
    const app=await fixture(),node=app.target();app.render(node,String.raw`\(x\)`);
    const [old,js]=assets(app),late=old.onload;
    app.renderer();js.onload();const timer=[...app.timers.values()][0];assert.equal(timer.delay,15000);timer.callback();await flush();
    node.querySelector('.math-retry').click();await flush();
    const fresh=assets(app).find(asset=>asset.href);assert.notEqual(fresh,old);assert.match(fresh.href,/asset_attempt=2$/);
    late();await flush();assert.equal(app.rendered.length,0);fresh.onload();await flush();assert.deepEqual(app.rendered,['x']);
  });
  await test('configured asset metadata cannot request another origin or arbitrary path',async()=>{
    const app=await fixture({malformed:true});app.render(app.target(),String.raw`\(x\)`);
    assert.deepEqual(assets(app).map(node=>node.href||node.src),['/vendor/katex/katex.min.css','/vendor/katex/katex.min.js']);
  });
  console.log(`\n${passed} lazy math checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

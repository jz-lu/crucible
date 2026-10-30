const {test, expect, gate} = require('./helpers.cjs');
const http = require('node:http');

// Serve the actual ASGI-rendered public document in two network chunks. Unlike
// route.fulfill(), this lets Chromium genuinely parse the head before body bytes
// exist. Everything stays in owned loopback contexts with synthetic data.
for (const [hint,hash,layout,title] of [['gate','#challenge','gate','Private beta'],['workspace','#people','people','Leaderboard']]) {
  test(`inline bootstrap selects the ${hint} frame and starts one access read before body bytes arrive`,async({browser,request})=>{
    const fixture='http://127.0.0.1:8029';
    const response=await request.get(fixture+'/',{headers:{'Accept-Encoding':'identity'},maxRedirects:0});
    expect(response.status()).toBe(200);
    const html=await response.text(),split=html.indexOf('<body');expect(split).toBeGreaterThan(0);
    expect(html.slice(0,split)).toContain('<script data-bootstrap>');
    const body=gate(),sessionArrived=gate(),sessionRelease=gate(),sessionDelivered=gate();
    const errors=[],requests=[];let sessionStarted=false;
    const server=http.createServer((req,res)=>{
      if(req.method!=='GET'||req.url!=='/'){res.writeHead(404);res.end();return;}
      res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',
        'Content-Security-Policy':response.headers()['content-security-policy']});
      res.write(html.slice(0,split));body.promise.then(()=>res.end(html.slice(split)));
    });
    await new Promise(done=>server.listen(0,'127.0.0.1',done));
    const origin='http://127.0.0.1:'+server.address().port;
    const context=await browser.newContext({viewport:{width:390,height:1000},serviceWorkers:'block'});
    const allowed=new Set(['/','/app.js','/math.js','/assumptions.js','/proposal-retractions.js','/resource-metrics.js','/assets/crucible-logo.png','/api/session',
      '/assets/resi-logo.png','/vendor/resi/cardo-normal-400-latin.woff2',
      '/vendor/resi/cardo-normal-700-latin.woff2','/vendor/resi/plex-sans-normal-100-700-latin.woff2']);
    try {
      await context.addInitScript(hint=>{
        sessionStorage.setItem('crucible.pending-presentation',hint);
        window.parserFrameLayouts=[];window.parserCsp=[];
        document.addEventListener('securitypolicyviolation',event=>window.parserCsp.push(event.effectiveDirective));
        function frame(){
          if(document.body)window.parserFrameLayouts.push([...document.querySelectorAll('.pending-page-preview')]
            .filter(element=>element.getClientRects().length&&getComputedStyle(element).display!=='none').map(element=>element.dataset.pendingLayout));
          if(!window.parserFramesDone)requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
      },hint);
      await context.route('**/*',async route=>{
        const req=route.request(),url=new URL(req.url());requests.push({path:url.pathname,method:req.method()});
        if(url.origin!==origin||req.method()!=='GET'||!allowed.has(url.pathname)){errors.push('unexpected-request');return route.abort();}
        if(url.pathname==='/')return route.continue();
        const result=await request.get(fixture+url.pathname+url.search,{maxRedirects:0,timeout:10000});
        if(url.pathname==='/api/session') {
          sessionStarted=true;
          try {sessionArrived.resolve();await sessionRelease.promise;await route.fulfill({response:result});}
          finally {sessionDelivered.resolve();}
        } else await route.fulfill({response:result});
      });
      const page=await context.newPage();page.on('pageerror',()=>errors.push('pageerror'));
      const navigation=page.goto(origin+'/'+hash,{waitUntil:'domcontentloaded'});
      await sessionArrived.promise;
      expect(await page.evaluate(()=>({bodyExists:!!document.body,page:document.documentElement.dataset.pendingPage,bootstrap:!!window.CrucibleSessionBootstrap})))
        .toEqual({bodyExists:false,page:layout,bootstrap:true});
      expect(requests.filter(row=>row.path==='/api/session')).toHaveLength(1);
      body.resolve();await navigation;
      await expect(page.locator('#access-title')).toHaveText(title);
      await expect(page.locator(`[data-pending-layout="${layout}"]`)).toBeVisible();
      await expect(page.locator('#access-workspace')).toHaveAttribute('aria-busy','true');
      await expect(page.locator('#catalogue-workspace')).toBeHidden();await expect(page.locator('#people-workspace')).toBeHidden();
      await page.evaluate(()=>new Promise(requestAnimationFrame));
      const frames=await page.evaluate(()=>{window.parserFramesDone=true;return window.parserFrameLayouts;});
      expect(frames.length).toBeGreaterThan(0);expect(frames.every(frame=>frame.length===1&&frame[0]===layout)).toBe(true);
      expect(requests.filter(row=>row.path.startsWith('/api/')&&row.path!=='/api/session')).toEqual([]);
      expect(requests.filter(row=>row.path==='/bootstrap.js')).toEqual([]);
      sessionRelease.resolve();await sessionDelivered.promise;
      await expect(page.locator('#access-title')).toHaveText('Private beta');
      await expect(page.locator('#access-sign-in')).toBeVisible();
      expect(requests.filter(row=>row.path==='/api/session')).toHaveLength(1);
      expect(await page.evaluate(()=>window.parserCsp)).toEqual([]);expect(errors).toEqual([]);
    } finally {
      body.resolve();sessionRelease.resolve();
      if(sessionStarted)await sessionDelivered.promise;
      await context.close();server.closeAllConnections();await new Promise(done=>server.close(done));
    }
  });
}

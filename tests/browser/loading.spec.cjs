const {test, expect, signIn, holdResponse} = require('./helpers.cjs');

async function geometry(locator) {
  return locator.evaluate(element => {
    const box = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {x: box.x, y: box.y, width: box.width, height: box.height,
      font: style.fontFamily, size: style.fontSize, weight: style.fontWeight, lineHeight: style.lineHeight};
  });
}

for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
  test(`catalogue fills its stable ${name} layout without visible loading copy or animation`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await signIn(page);
    // The setup document must not consume a catalogue hold intended for the
    // next cold document through its own post-login preparation.
    await page.goto('about:blank');
    const session = await holdResponse(page, url => url.pathname === '/api/session');
    const catalogue = await holdResponse(page, url => url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'navigation');
    const reads = [];
    page.on('request', request => reads.push(new URL(request.url()).pathname));
    let accessHeading, accessTable, pendingHeading, pendingTable, pendingHeader, pendingFirstRow;
    try {
      // A changed document URL forces a cold bootstrap; changing only the
      // fragment is deliberately handled as a warm SPA navigation.
      await page.goto('/?loading-check=' + name + '#challenge', {waitUntil: 'domcontentloaded'}); await session.arrived;
      await expect(page.locator('#access-loading-preview')).toBeVisible();
      expect(reads.filter(path => path === '/api/schemes' || path.startsWith('/notebook/') || path.startsWith('/map/'))).toEqual([]);
      await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
      accessHeading = await geometry(page.locator('#access-title'));
      accessTable = await geometry(page.locator('#access-loading-preview [data-pending-layout="catalogue"]'));
      expect(await page.locator('#access-description').evaluate(element => getComputedStyle(element).clipPath)).toBe('inset(50%)');
      await page.screenshot({path: testInfo.outputPath('pending-access.png')});
      session.release(); await session.delivered; await catalogue.arrived;
      await expect(page.locator('#catalogue-workspace')).toBeVisible();
      await expect(page.locator('#route-loading')).toBeHidden();
      await expect(page.locator('#refresh-catalogue')).toBeDisabled();
      await expect(page.locator('#nav-about')).toBeEnabled();
      await expect(page.locator('#kem-catalogue a, #kem-catalogue button')).toHaveCount(0);
      pendingHeading = await geometry(page.locator('#catalogue-title'));
      pendingTable = await geometry(page.locator('#kem-catalogue .kem-table-wrap'));
      pendingHeader = await geometry(page.locator('#kem-catalogue thead'));
      pendingFirstRow = await geometry(page.locator('#kem-catalogue tbody tr').first());
      for (const field of ['x', 'y']) expect(pendingHeading[field], `heading ${field} after access`).toBeCloseTo(accessHeading[field], 0);
      for (const field of ['font', 'size', 'weight', 'lineHeight']) expect(pendingHeading[field]).toBe(accessHeading[field]);
      for (const field of ['x', 'y', 'width']) expect(pendingTable[field], `table ${field} after access`).toBeCloseTo(accessTable[field], 0);
      expect(await page.locator('.catalogue-placeholder').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none');
      expect(await page.locator('#kem-catalogue [role="status"]').evaluate(element => getComputedStyle(element).clipPath)).toBe('inset(50%)');
      await page.locator('#nav-about').focus();
      await page.screenshot({path: testInfo.outputPath('pending-catalogue.png')});
    } finally {
      session.release(); catalogue.release();
      await session.delivered; await catalogue.delivered;
      await session.remove(); await catalogue.remove();
    }
    await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
    await expect(page.locator('#kem-catalogue')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('#refresh-catalogue')).toBeEnabled();
    await expect(page.locator('#nav-about')).toBeFocused();
    const readyHeading = await geometry(page.locator('#catalogue-title'));
    const readyTable = await geometry(page.locator('#kem-catalogue .kem-table-wrap'));
    expect(readyHeading).toEqual(pendingHeading);
    for (const field of ['x', 'y', 'width']) expect(readyTable[field], `table ${field} after data`).toBeCloseTo(pendingTable[field], 0);
    const readyHeader = await geometry(page.locator('#kem-catalogue thead'));
    const readyFirstRow = await geometry(page.locator('#kem-catalogue tbody tr').first());
    expect(readyHeader.height, 'help control does not resize table heading').toBeCloseTo(pendingHeader.height, 0);
    expect(readyFirstRow.y, 'first row keeps its position').toBeCloseTo(pendingFirstRow.y, 0);
    // Mobile records have variable wrapped names and optional recovery values;
    // reserve a name line, not a height fitted to this one-scheme fixture.
    if (name === 'desktop') expect(Math.abs(readyFirstRow.height - pendingFirstRow.height)).toBeLessThan(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('ready-catalogue.png')});
  });
}

const pendingRoutes = [
  ['#challenge', 'neutral', 'Crucible'],
  ['#people', 'people', 'Leaderboard'],
  ['#notebook', 'notebook-notes', 'Research notebook'],
  ['#notebook?view=map', 'notebook-map', 'Research notebook'],
  ['#notebook?view=timeline', 'notebook-timeline', 'Research notebook'],
  ['#person/browser_alice', 'profile', 'browser_alice'],
  ['#about', 'neutral', 'About us'],
  ['#scheme/teaching-dh-kem/4', 'chart', 'Scheme'],
];

for (const [route, layout, title] of pendingRoutes) {
  test(`cold ${route} keeps public navigation and only its own pending layout before access resolves`, async ({page}, testInfo) => {
    const appScript = await holdResponse(page, url => url.pathname === '/app.js');
    const session = await holdResponse(page, url => url.pathname === '/api/session');
    const reads = [];
    page.on('request', request => reads.push(new URL(request.url()).pathname));
    const primary = page.locator('.primary-nav');
    const notebook = layout.startsWith('notebook-');
    const heading = page.locator(notebook ? '#notebook-frame-template .nb-heading h1' : '#access-title');
    const frameLayout = notebook ? 'notebook' : layout;
    const privateReads = () => reads.filter(path => (path.startsWith('/api/') && path !== '/api/session') ||
      path.startsWith('/notebook/') || path.startsWith('/map/'));
    try {
      await page.goto('/' + route, {waitUntil: 'commit'}); await appScript.arrived;await session.arrived;
      await expect(heading).toHaveText(title);
      await expect(heading).toBeVisible();
      if (!notebook) await expect(heading).not.toHaveAttribute('aria-hidden','true');
      await expect(page.locator('html')).toHaveAttribute('data-pending-page', layout);
      await expect(primary).toBeVisible(); await expect(primary).toHaveAttribute('inert', '');
      await expect(primary).toContainText('Schemes'); await expect(primary).toContainText('Research notebook');
      await expect(page.locator('#account-handle')).toBeHidden();
      await expect(page.locator('#account-handle')).toBeEmpty();
      await page.locator('#nav-about').evaluate(element => element.focus());
      await expect(page.locator('#nav-about')).not.toBeFocused();
      expect(privateReads()).toEqual([]);
      expect(reads.filter(path=>path==='/bootstrap.js')).toEqual([]);
      await expect(page.locator('head > script[data-bootstrap]')).toHaveCount(1);
      await expect(heading).toHaveText(title);
      const selected = page.locator('#access-loading-preview [data-pending-layout="' + frameLayout + '"]');
      await expect(selected).toBeVisible();
      expect(await page.locator('#access-loading-preview .pending-page-preview').evaluateAll(elements =>
        elements.filter(element => element.getClientRects().length && getComputedStyle(element).display !== 'none')
          .map(element => element.dataset.pendingLayout))).toEqual([frameLayout]);
      if (notebook) expect(await selected.locator('[data-notebook-panel]').evaluateAll(elements => elements
        .filter(element => element.getClientRects().length && getComputedStyle(element).display !== 'none')
        .map(element => element.dataset.notebookPanel))).toEqual([layout.slice('notebook-'.length)]);
      if (layout !== 'catalogue') await expect(page.locator('#access-loading-preview .kem-table')).toBeHidden();
      const beforeApp = await geometry(heading);
      await expect(primary).toBeVisible(); await expect(primary).toHaveAttribute('inert', '');
      expect(privateReads()).toEqual([]);
      await expect(page.locator('#catalogue-workspace')).toBeHidden();
      await expect(page.locator('#notebook-workspace')).toBeHidden();
      await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
      await page.screenshot({path:testInfo.outputPath('route-pending-shell.png')});

      appScript.release(); await appScript.delivered;
      await expect(heading).toHaveText(title);
      expect(await geometry(heading)).toEqual(beforeApp);
      await expect(selected).toBeVisible();
      await expect(primary).toBeVisible(); await expect(primary).toHaveAttribute('inert', '');
      expect(reads.filter(path => path === '/api/session')).toHaveLength(1);
      expect(privateReads()).toEqual([]);
    } finally {
      appScript.release(); session.release();
      await appScript.delivered; await session.delivered;
      await appScript.remove(); await session.remove();
    }
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    await expect(page.locator('.access-heading-space')).toBeHidden();
    await expect(primary).toBeHidden();
    expect(privateReads()).toEqual([]);
    await page.screenshot({path:testInfo.outputPath('signed-out-gate.png')});
  });
}

for (const width of [1440,390]) {
  test(`a returning signed-out ${width}px gate reload never flashes the KEM layout`, async ({page},testInfo) => {
    await page.setViewportSize({width,height:1000});
    await page.goto('/#challenge');
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    expect(await page.evaluate(()=>sessionStorage.getItem('crucible.pending-presentation'))).toBe('gate');
    const before=await geometry(page.locator('#access-workspace'));
    const heading=await geometry(page.locator('#access-title'));
    const gateParts=()=>page.evaluate(()=>Object.fromEntries([
      '.site-header','.header-inner','.account-actions','#access-workspace','.access-heading',
      '#access-title','#access-description','#access-auth-actions','.pending-gate-actions',
    ].map(selector=>{
      const element=document.querySelector(selector),rect=element.getBoundingClientRect(),style=getComputedStyle(element);
      return [selector,{x:rect.x,y:rect.y,width:rect.width,height:rect.height,display:style.display,
        padding:style.padding,margin:style.margin,gap:style.gap,font:style.font,flexDirection:style.flexDirection}];
    })));
    const settledParts=await gateParts();
    await page.addInitScript(()=>{
      window.gateReloadFrames=[];
      function frame(){
        const catalogue=document.querySelector('#access-loading-preview [data-pending-layout="catalogue"]');
        window.gateReloadFrames.push(Boolean(catalogue?.getClientRects().length&&getComputedStyle(catalogue).display!=='none'));
        if(!window.gateReloadComplete)requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
    const reads=[];page.on('request',request=>reads.push(new URL(request.url()).pathname));
    const pending=await holdResponse(page,url=>url.pathname==='/api/session');
    try {
      await page.reload({waitUntil:'domcontentloaded'});await pending.arrived;
      await expect(page.locator('#access-title')).toHaveText('Private beta');
      await expect(page.locator('#access-loading-preview [data-pending-layout="gate"]')).toBeVisible();
      await expect(page.locator('#access-loading-preview .kem-table')).toBeHidden();
      await testInfo.attach('gate-geometry',{body:JSON.stringify({settled:settledParts,pending:await gateParts()},null,2),contentType:'application/json'});
      expect(await geometry(page.locator('#access-workspace'))).toEqual(before);
      expect(await geometry(page.locator('#access-title'))).toEqual(heading);
      await expect(page.locator('#account-handle')).toBeHidden();
      await expect(page.locator('#catalogue-workspace')).toBeHidden();
      await expect(page.locator('#notebook-workspace')).toBeHidden();
      expect(reads.filter(path=>path.startsWith('/api/')&&path!=='/api/session')).toEqual([]);
      expect(await page.evaluate(()=>window.gateReloadFrames.some(Boolean))).toBe(false);
      await page.screenshot({path:testInfo.outputPath('returning-gate-pending-'+width+'.png')});
    } finally {pending.release();await pending.delivered;await pending.remove();}
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    expect(await geometry(page.locator('#access-workspace'))).toEqual(before);
    expect(await page.evaluate(()=>{window.gateReloadComplete=true;return window.gateReloadFrames.some(Boolean);})).toBe(false);
    expect(reads.filter(path=>path.startsWith('/api/')&&path!=='/api/session')).toEqual([]);
  });
}

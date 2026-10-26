const {test, expect, signIn, holdResponse} = require('./helpers.cjs');

const views = {
  notes: {route: '#notebook', body: '[data-nb="library"]',
    hold: url => url.pathname === '/api/notebook/notes' && url.searchParams.get('scope') === 'all' && url.searchParams.get('limit') === '25'},
  map: {route: '#notebook?view=map', body: '[data-nb="map"]', hold: url => url.pathname === '/map/styles.css'},
  timeline: {route: '#notebook?view=timeline', body: '[data-nb="timeline"]', hold: url => url.pathname === '/api/notebook/timeline'},
};

async function trackedHold(page, predicate) {
  const held = await holdResponse(page, predicate);
  const state = {...held, started: false};
  state.arrived = held.arrived.then(response => {state.started = true; return response;});
  return state;
}

async function frameGeometry(root, view, pending) {
  return root.evaluate((element, {view, pending}) => {
    const box = selector => {
      const target = element.querySelector(selector);
      if (!target) throw Error('Missing notebook geometry element: ' + selector);
      const rect = target.getBoundingClientRect(), style = getComputedStyle(target);
      return {x: rect.x + scrollX, y: rect.y + scrollY, width: rect.width, height: rect.height,
        font: style.font, letterSpacing: style.letterSpacing};
    };
    return {
      heading: box('.nb-heading'), title: box('.nb-heading h1'), navigation: box('.nb-views'),
      tab: box(pending ? `[data-notebook-tab="${view}"]` : `[data-nb="${view}-view"]`),
      body: box(pending ? `[data-notebook-panel="${view}"]` :
        `[data-nb="${view === 'notes' ? 'library' : view}"]`),
    };
  }, {view, pending});
}

function sameFrame(actual, expected, phase) {
  for (const part of ['heading', 'title', 'navigation', 'tab', 'body']) {
    // Records and the real map may legitimately determine a different body
    // height. Their top edge and available width should not jump between frames.
    const coordinates = part === 'body' ? ['x', 'y', 'width'] : ['x', 'y', 'width', 'height'];
    for (const coordinate of coordinates) {
      expect(Math.abs(actual[part][coordinate] - expected[part][coordinate]),
        `${phase}: ${part} ${coordinate}`).toBeLessThanOrEqual(1);
    }
    if (part === 'title' || part === 'tab') {
      expect(actual[part].font, `${phase}: ${part} font`).toBe(expected[part].font);
      expect(actual[part].letterSpacing, `${phase}: ${part} spacing`).toBe(expected[part].letterSpacing);
    }
  }
}

async function noVisibleLoading(root) {
  const visible = await root.evaluate(element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT), visible = [];
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!/\b(?:Loading|Checking)\b/i.test(text.textContent)) continue;
      let shown = true;
      for (let ancestor = text.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (ancestor.hidden || style.display === 'none' || style.visibility === 'hidden' ||
            style.clipPath === 'inset(50%)') {shown = false; break;}
      }
      if (shown && text.parentElement.getClientRects().length) visible.push(text.textContent.trim());
    }
    return visible;
  });
  expect(visible, 'no visible transient read-status copy').toEqual([]);
}

async function pendingFrameChecks(frame, view) {
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute('aria-hidden', 'true');
  await expect(frame).toHaveAttribute('inert', '');
  await expect(frame.locator('.nb-heading h1')).toHaveText('Research notebook');
  expect(await frame.locator('button,input,select').evaluateAll(elements => elements.every(element => element.disabled))).toBe(true);
  expect(await frame.locator('[data-notebook-panel]').evaluateAll(elements => elements
    .filter(element => element.getClientRects().length && getComputedStyle(element).display !== 'none')
    .map(element => element.dataset.notebookPanel))).toEqual([view]);
  await expect(frame.locator('.nb-entry')).toHaveCount(0);
  await expect(frame.locator('.nb-count')).toBeEmpty();
  await expect(frame.locator('.nb-timeline-total')).toBeEmpty();
  await expect(frame.locator('.nb-timeline-plot svg')).toHaveCount(0);
  await expect(frame.locator('.nb-map-meta')).toBeEmpty();
  await noVisibleLoading(frame);
}

for (const width of [1440, 390]) for (const [view, config] of Object.entries(views)) {
  test(`${view} keeps one ${width}px notebook frame from access through assets and data`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await signIn(page);
    // Preserve real authentication and the presentation hint, but stop setup
    // document preparation before installing this new document's barriers.
    await page.goto('about:blank');
    const session = await trackedHold(page, url => url.pathname === '/api/session');
    const script = await trackedHold(page, url => url.pathname === '/notebook/notebook.js');
    const data = await trackedHold(page, config.hold);
    const requests = [];
    page.on('request', request => requests.push({path: new URL(request.url()).pathname, method: request.method()}));
    const template = page.locator('#notebook-frame-template');
    const workspace = page.locator('#notebook-workspace');
    let initial, shadowSurface, shadowStylesheet;
    try {
      await page.goto('/' + config.route);
      await session.arrived;
      await expect(page.locator('html')).toHaveAttribute('data-pending-page', 'notebook-' + view);
      await pendingFrameChecks(template, view);
      expect(requests.filter(request => request.path.startsWith('/notebook/') || request.path.startsWith('/map/') ||
        (request.path.startsWith('/api/') && request.path !== '/api/session')),
        'fresh session must authorize all protected preparation').toEqual([]);
      await expect(page.locator('#access-loading-preview [data-pending-layout="catalogue"]')).toBeHidden();
      await expect(page.locator('#access-loading-preview [data-pending-layout="chart"]')).toBeHidden();
      await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
      await expect(page.locator('body')).not.toContainText('Alice browser-only private note');
      initial = await frameGeometry(template, view, true);
      expect(initial.body.width).toBeGreaterThan(250);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: testInfo.outputPath('session-pending.png'), fullPage: true});

      session.release(); await session.delivered; await script.arrived;
      const shell = workspace.locator('.notebook-frame');
      await pendingFrameChecks(shell, view);
      sameFrame(await frameGeometry(shell, view, true), initial, 'notebook script pending');
      await expect(workspace.locator('.nb-entry')).toHaveCount(0);
      await expect(workspace.locator('#landscape svg')).toHaveCount(0);
      await expect(workspace.locator('.nb-timeline-plot svg')).toHaveCount(0);
      await page.screenshot({path: testInfo.outputPath('script-pending.png'), fullPage: true});

      script.release(); await script.delivered; await data.arrived;
      await expect(workspace.locator('[data-nb="' + view + '-view"]')).toBeVisible();
      await expect(workspace.locator(config.body)).toBeVisible();
      sameFrame(await frameGeometry(workspace, view, false), initial, 'selected data pending');
      await expect(workspace.locator('.nb-entry')).toHaveCount(0);
      await expect(workspace.locator('.nb-timeline-plot svg')).toHaveCount(0);
      await expect(workspace.locator('#landscape svg')).toHaveCount(0);
      await noVisibleLoading(workspace);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (view === 'map') {
        shadowStylesheet = await workspace.locator('link[rel="stylesheet"][href^="/map/styles.css"]').elementHandle();
        expect(shadowStylesheet).not.toBeNull();
        shadowSurface = await shadowStylesheet.evaluateHandle(element => element.getRootNode().host);
        expect(await shadowSurface.evaluate(element => element.isConnected && element.hidden)).toBe(true);
        expect(requests.filter(request => request.path === '/map/styles.css')).toHaveLength(1);
      }
      await page.locator('#nav-about').focus();
      await page.screenshot({path: testInfo.outputPath('data-pending.png'), fullPage: true});
      data.release(); await data.delivered;
      if (view === 'notes') await expect(workspace.locator('#nb-note-list')).toContainText('Alice browser-only private note');
      if (view === 'timeline') await expect(workspace.locator('[data-nb="timeline"] svg')).toBeVisible();
      if (view === 'map') {
        await expect(workspace.locator('#landscape svg')).toBeVisible();
        expect(await shadowSurface.evaluate(element => element.isConnected && !element.hidden)).toBe(true);
        expect(await shadowStylesheet.evaluate(element => element.isConnected)).toBe(true);
        expect(requests.filter(request => request.path === '/map/styles.css')).toHaveLength(1);
      }
      sameFrame(await frameGeometry(workspace, view, false), initial, 'ready');
      await expect(page.locator('#nav-about')).toBeFocused();
      await noVisibleLoading(workspace);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(requests.filter(request => request.path.startsWith('/api/notebook/notes/') || request.path === '/map/notes-data.js'),
        'frame preparation never needs individual note bodies').toEqual([]);
      expect(requests.filter(request => request.method !== 'GET')).toEqual([]);
      await page.screenshot({path: testInfo.outputPath('ready.png'), fullPage: true});
    } finally {
      for (const hold of [session, script, data]) hold.release();
      for (const hold of [session, script, data]) {
        if (hold.started) await hold.delivered;
        await hold.remove();
      }
      if (shadowStylesheet) await shadowStylesheet.dispose();
      if (shadowSurface) await shadowSurface.dispose();
    }
  });
}

for (const width of [1440, 390]) {
  test(`complete notebook pages preserve ${width}px scroll and keyboard focus without new page reads`, async ({page}) => {
    await page.setViewportSize({width, height: 1000});
    const indexRead = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/notebook/notes' && url.searchParams.get('projection') === 'index' && response.status() === 200;
    });
    await signIn(page);
    const response = await indexRead;
    const index = await response.json();
    expect(index.complete).toBe(true);
    const pages = Math.ceil(index.notes.length / 25);
    expect(pages).toBeGreaterThan(1); expect(pages).toBeLessThanOrEqual(20);
    // Reenter through the real navigation so the controller consumes the
    // already prepared index; no cache injection or preference override.
    await page.locator('#nav-about').click();
    await expect(page.locator('#about-workspace')).toBeVisible();
    await page.locator('#nav-notebook').click();
    const workspace = page.locator('#notebook-workspace');
    const select = workspace.locator('[data-nb="page-select"]');
    const list = workspace.locator('#nb-note-list');
    await expect(select).toBeVisible(); await expect(select).toBeEnabled();
    await expect(select.locator('option')).toHaveCount(pages);
    await expect(list.locator('.nb-entry')).toHaveCount(25);
    const requests = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if ((url.pathname === '/api/notebook/notes' && url.searchParams.get('projection') !== 'index') ||
          url.pathname.startsWith('/api/notebook/notes/')) requests.push(request.url());
    });
    await select.scrollIntoViewIfNeeded(); await select.focus();
    const before = await page.evaluate(() => scrollY);
    expect(before).toBeGreaterThan(0);
    const oldHeight = await list.evaluate(element => element.getBoundingClientRect().height);
    await select.selectOption(String(pages - 1));
    await expect(list.locator('.nb-entry')).toHaveCount(index.notes.length - 25 * (pages - 1));
    await expect(select).toBeFocused();
    expect(Math.abs(await page.evaluate(() => scrollY) - before)).toBeLessThanOrEqual(1);
    expect(await list.evaluate(element => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(oldHeight);
    const previous = workspace.locator('[data-nb="prev"]');
    await previous.focus(); await previous.click();
    await expect(select).toHaveValue(String(pages - 2));
    await expect(list.locator('.nb-entry')).toHaveCount(25);
    if (pages === 2) await expect(select).toBeFocused();
    else await expect(previous).toBeFocused();
    expect(Math.abs(await page.evaluate(() => scrollY) - before)).toBeLessThanOrEqual(1);
    expect(requests).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

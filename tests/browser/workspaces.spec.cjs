const {test, expect, signIn, notebookReady, gate, holdResponse} = require('./helpers.cjs');

test('GitHub return opens the requested page without a continuation round trip', async ({page}) => {
  const requests = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await page.route('https://github.com/login/oauth/authorize?*', route => {
    const query = new URL(route.request().url()).searchParams;
    return route.fulfill({status: 302, headers: {location: 'http://127.0.0.1:8029/api/auth/github/callback?' +
      new URLSearchParams({state: query.get('state'), code: 'synthetic-browser-code'})}, body: ''});
  });
  await page.goto('/#notebook');
  await page.locator('#access-github').click();
  await notebookReady(page);
  await expect(page.locator('#account-handle')).toHaveText('browser_alice');
  expect(requests).toContain('/api/auth/github/callback');
  expect(requests).not.toContain('/api/auth/github/pending');
  expect(new URL(page.url()).hash).toBe('#notebook');
});

test('the landing page works before unused math assets and equations hydrate when needed', async ({page}) => {
  const release = gate(), started = [];
  await page.route(url => /^\/vendor\/katex\/katex\.min\.(js|css)$/.test(url.pathname), async route => {
    started.push(new URL(route.request().url()).pathname);
    await release.promise;
    await route.continue();
  });
  try {
    await signIn(page);
    await page.locator('#nav-challenge').click();
    await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
    expect(started).toEqual([]);
    // Use the real math helper on connected content; no backend/provider mocks.
    await page.evaluate(() => {
      const field = document.createElement('p');
      field.id = 'browser-math-target';
      document.querySelector('#catalogue-workspace').append(field);
      window.CrucibleMath.render(field, 'Equation: \\(x^2\\).');
    });
    await expect.poll(() => started.length).toBe(2);
    await expect(page.locator('#browser-math-target')).toContainText('Equation:');
    await expect(page.locator('#browser-math-target .katex')).toHaveCount(0);
    await page.locator('#nav-about').click();
    await expect(page.locator('#about-workspace')).toBeVisible();
    release.resolve();
    await expect(page.locator('#browser-math-target .katex')).toHaveCount(1);
    await expect(page.locator('#about-workspace')).toBeVisible();
  } finally {release.resolve();}
});

test('cached navigation paints while its fresh read is held; the brand never reloads the document', async ({page}, testInfo) => {
  await signIn(page); await notebookReady(page);
  const reads = [];
  page.on('request', request => reads.push({method: request.method(), type: request.resourceType()}));
  await page.locator('#nav-people').click();
  await expect(page.locator('#people-content')).toContainText('browser_alice');
  await page.locator('#nav-about').click();
  const pending = await holdResponse(page, url => url.pathname === '/api/people');
  try {
    await page.locator('#nav-people').click();
    await pending.arrived;
    // The refresh is still held, so this can only be the known previous view.
    await expect(page.locator('#people-workspace')).toBeVisible();
    await expect(page.locator('#people-content')).toContainText('browser_alice');
  } finally {pending.release(); await pending.delivered; await pending.remove();}
  const catalogue = await holdResponse(page, url => url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'navigation');
  try {
    await page.getByRole('banner').getByRole('link', {name: 'Crucible home', exact: true}).click();
    await catalogue.arrived;
    const preview = page.locator('#route-loading .loading-preview');
    await expect(preview).toBeVisible();
    await expect(preview).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('#route-loading')).toHaveAttribute('aria-busy', 'true');
    const bounds = await preview.boundingBox();
    expect(bounds.width).toBeGreaterThan(50); expect(bounds.height).toBeGreaterThan(50);
    const panel = await page.locator('#route-loading').boundingBox();
    const footer = await page.locator('.site-footer').boundingBox();
    expect(panel.y + panel.height, 'loading content stays above the site footer').toBeLessThanOrEqual(footer.y);
    await page.screenshot({path: testInfo.outputPath('held-route-preview.png')});
  } finally {catalogue.release(); await catalogue.delivered; await catalogue.remove();}
  await expect(page.locator('#catalogue-workspace')).toBeVisible();
  await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
  expect(reads.filter(read => read.type === 'document')).toEqual([]);
  expect(reads.filter(read => read.method !== 'GET')).toEqual([]);
});

test('initial access has a neutral loading preview without revealing private content', async ({page}, testInfo) => {
  const pending = await holdResponse(page, url => url.pathname === '/api/session');
  let cardWidth;
  const privateReads = [];
  page.on('request', request => {
    if (/^\/(?:map\/|notebook\/|api\/notebook)/.test(new URL(request.url()).pathname)) privateReads.push(request.url());
  });
  try {
    await page.goto('/#notebook', {waitUntil: 'domcontentloaded'}); await pending.arrived;
    const preview = page.locator('#access-loading-preview');
    await expect(preview).toBeVisible();
    await expect(preview).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('#access-description')).toHaveText('Checking access…');
    const bounds = await preview.boundingBox();
    expect(bounds.width).toBeGreaterThan(50); expect(bounds.height).toBeGreaterThan(20);
    cardWidth = (await page.locator('#access-workspace').boundingBox()).width;
    await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
    expect(privateReads).toEqual([]);
    await page.screenshot({path: testInfo.outputPath('held-access-preview.png')});
  } finally {pending.release(); await pending.delivered; await pending.remove();}
  await expect(page.locator('#access-loading-preview')).toBeHidden();
  await expect(page.locator('#access-auth-actions')).toBeVisible();
  expect((await page.locator('#access-workspace').boundingBox()).width).toBeCloseTo(cardWidth, 0);
});

test('anonymous and revoked readers cannot fetch private assets or notebook data', async ({page, context}) => {
  await page.goto('/#notebook');
  await expect(page.locator('#access-auth-actions')).toBeVisible();
  for (const path of ['/map/notes-data.js', '/notebook/notebook.js', '/api/notebook/notes']) {
    const response = await context.request.get(path);
    expect(response.status()).toBe(401);
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  await context.addCookies([{name: 'crucible_session', value: 'synthetic-revoked-browser-cookie',
    url: 'http://127.0.0.1:8029', sameSite: 'Strict'}]);
  await page.reload();
  await expect(page.locator('#access-workspace')).toBeVisible();
  await expect(page.locator('#access-description')).toContainText('Your beta access is not active');
  for (const path of ['/map/notes-data.js', '/notebook/notebook.js', '/api/notebook/notes']) {
    const response = await context.request.get(path);
    expect(response.status()).toBe(403);
    expect(response.headers()['cache-control']).toBe('no-store');
  }
  await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
  await expect(page.locator('#notebook-workspace')).toBeHidden();
});

test('a response authorized for Alice cannot reappear after logout and Bob signs in', async ({page}) => {
  await signIn(page); await notebookReady(page);
  const pending = await holdResponse(page, url => /^\/api\/notebook\/notes\/[^/]+$/.test(url.pathname));
  try {
    await page.locator('#nb-note-list .nb-title').filter({hasText: 'Alice browser-only private note'}).click();
    await pending.arrived;
    await page.getByRole('button', {name: 'Read full note →', exact: true}).click();
    await page.locator('#logout-button').click();
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    await signIn(page, 'bob', {navigate: false});
    await page.locator('#nav-notebook').click();
    await notebookReady(page, 'bob');
  } finally {pending.release(); await pending.delivered; await pending.remove();}
  await expect(page.locator('#nb-note-list')).not.toContainText('Alice browser-only private note');
  await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
  await expect(page.locator('#account-handle')).toHaveText('browser_bob');
});

test('the map downloads concurrently, keeps one stylesheet, and supports a native keyboard dialog', async ({page}) => {
  await signIn(page); await notebookReady(page);
  const names = new Set(['catalogue.js', 'notes-data.js', 'note-ui.js', 'map-views.js', 'app.js', 'index.html', 'styles.css']);
  const started = [], release = gate();
  await page.route(url => url.origin === 'http://127.0.0.1:8029' && url.pathname.startsWith('/map/') && names.has(url.pathname.slice(5)), async route => {
    started.push(new URL(route.request().url()).pathname);
    await release.promise;
    await route.continue();
  });
  try {
    await page.getByRole('button', {name: 'Scheme map', exact: true}).click();
    // All requests must be started before any response is released. This fails
    // the old serial waterfall without relying on a fragile millisecond budget.
    await expect.poll(() => new Set(started).size).toBe(7);
    await expect(page.locator('#landscape svg')).not.toBeVisible();
  } finally {release.resolve();}
  const graph = page.locator('#landscape svg');
  await expect(graph).toBeVisible();
  await expect(page.locator('#landscape [data-scheme]')).toHaveCount(13);
  const box = await graph.boundingBox();
  expect(box.width).toBeGreaterThan(500); expect(box.height).toBeGreaterThan(200);
  expect(started.filter(path => path === '/map/styles.css')).toHaveLength(1);
  const node = page.locator('#landscape [data-scheme]').first();
  await node.focus(); await page.keyboard.press('Enter');
  const dialog = page.locator('#mobile-details');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-labelledby', 'mobile-detail-title');
  await page.keyboard.press('Tab');
  expect(await dialog.evaluate(element => element.contains(element.getRootNode().activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(node).toBeFocused();
  await page.getByRole('button', {name: 'Notes', exact: true}).click();
  await notebookReady(page);
  const count = started.length;
  await page.getByRole('button', {name: 'Scheme map', exact: true}).click();
  await expect(graph).toBeVisible();
  expect(started).toHaveLength(count);
});

test('mobile note navigation fits the viewport and moves keyboard focus into the warm reader', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await signIn(page); await notebookReady(page);
  const title = page.locator('#nb-note-list .nb-title').filter({hasText: 'Alice browser-only private note'});
  await title.click();
  const read = page.getByRole('button', {name: 'Read full note →', exact: true});
  await read.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.nb-full-title')).toHaveText('Alice browser-only private note');
  await expect(page.locator('.nb-full-title')).toBeFocused();
  await page.getByRole('button', {name: '← Research notebook', exact: true}).click();
  await notebookReady(page);
  if (!await read.isVisible()) await title.click();
  await read.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.nb-full-title')).toBeVisible();
  await expect(page.locator('.nb-full-title')).toBeFocused();
  const width = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, viewport: innerWidth}));
  expect(width.scroll).toBeLessThanOrEqual(width.viewport + 1);
});

for (const workspace of ['notebook', 'map']) test(`a stalled ${workspace} script times out visibly and a fresh retry loads it`, async ({page}) => {
  await page.goto('/#about');
  await signIn(page, 'alice', {navigate: false});
  if (workspace === 'map') {
    await page.locator('#nav-notebook').click(); await notebookReady(page);
  }
  const seen = gate(), release = gate(); let calls = 0;
  await page.route(url => url.origin === 'http://127.0.0.1:8029' && url.pathname === (workspace === 'map' ? '/map/app.js' : '/notebook/notebook.js'), async route => {
    calls++;
    if (calls === 1) {seen.resolve(); await release.promise; await route.abort();}
    else await route.continue();
  });
  try {
    if (workspace === 'map') await page.getByRole('button', {name: 'Scheme map', exact: true}).click();
    else await page.locator('#nav-notebook').click();
    await seen.promise;
    const target = page.locator(workspace === 'map' ? '[data-nb="map"]' : '#notebook-workspace');
    await expect(target).toContainText(workspace === 'map' ? 'The map could not be loaded.' : 'The notebook took too long to load. Please try again.', {timeout: 20000});
    await target.getByRole('button', {name: 'Try again', exact: true}).click();
    await expect.poll(() => calls, {message: 'retry starts a fresh asset request'}).toBe(2);
    if (workspace === 'map') await expect(page.locator('#landscape svg')).toBeVisible();
    else await notebookReady(page);
    expect(calls).toBe(2);
  } finally {release.resolve();}
});

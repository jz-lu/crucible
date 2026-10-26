const {test, expect, gate} = require('./helpers.cjs');

test('Stop-loading recovers the initial access read once without opening private content', async ({page}) => {
  const first = gate(), retry = gate(), releaseFirst = gate(), releaseRetry = gate();
  const held = [], protectedReads = [];
  let reads = 0;
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (/^\/(?:notebook\/|map\/|api\/(?:notebook|schemes))/.test(pathname)) protectedReads.push(pathname);
  });
  await page.route('**/api/session', route => {
    const number = ++reads;
    if (number > 2) return route.continue();
    const task = (async () => {
      const response = await route.fetch();
      (number === 1 ? first : retry).resolve();
      await (number === 1 ? releaseFirst : releaseRetry).promise;
      await route.fulfill({response}).catch(() => {}); // The stopped first document request is retired.
    })();
    held.push(task); return task;
  });
  try {
    await page.goto('/#people', {waitUntil:'domcontentloaded'}); await first.promise;
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Page.stopLoading'); await retry.promise;
    expect(reads).toBe(2);
    await expect(page.locator('#people-workspace')).not.toBeVisible();
    await expect(page.locator('#global-message')).not.toBeVisible();
    expect(protectedReads).toEqual([]);
    releaseFirst.resolve(); releaseRetry.resolve(); await Promise.all(held);
    await expect(page.locator('#access-sign-in')).toBeVisible();
    await expect(page.locator('#global-message')).not.toBeVisible();
    expect(reads).toBe(2); expect(protectedReads).toEqual([]);
  } finally {releaseFirst.resolve();releaseRetry.resolve();await Promise.allSettled(held);}
});

test('persistent browser offline stops after two access attempts and exposes a working explicit retry', async ({page,context}) => {
  const arrived = gate(), release = gate(), held = [];
  const protectedReads = []; let reads = 0;
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (/^\/(?:notebook\/|map\/|api\/(?:notebook|schemes))/.test(pathname)) protectedReads.push(pathname);
  });
  await page.route('**/api/session', route => {
    if (++reads > 1) return route.continue();
    const task = (async () => {const response = await route.fetch();arrived.resolve();await release.promise;await route.fulfill({response}).catch(() => {});})();
    held.push(task);return task;
  });
  try {
    await page.goto('/#notebook', {waitUntil:'domcontentloaded'});await arrived.promise;
    await context.setOffline(true);
    const cdp = await context.newCDPSession(page);await cdp.send('Page.stopLoading');
    await expect(page.locator('#access-description')).toContainText('Access could not be checked.');
    await expect(page.locator('#access-retry')).toBeVisible();
    await expect(page.locator('#global-message')).not.toBeVisible();
    await expect(page.locator('#notebook-workspace')).not.toBeVisible();
    expect(reads).toBe(2);expect(protectedReads).toEqual([]);
    release.resolve();await Promise.all(held);await context.setOffline(false);
    await page.locator('#access-retry').click();
    await expect(page.locator('#access-sign-in')).toBeVisible();
    expect(reads).toBe(3);expect(protectedReads).toEqual([]);
  } finally {release.resolve();await Promise.allSettled(held);await context.setOffline(false);}
});

const {signIn} = require('./helpers.cjs');
const authenticatedPhases = [
  {name:'catalogue', hash:'#challenge', path:'/api/schemes', query:'projection=navigation', ready:'#catalogue-workspace .kem-name'},
  {name:'People', hash:'#people', path:'/api/people', query:'limit=50&offset=0&include_records=false', ready:'#people-content .people-ranking'},
  {name:'Notes', hash:'#notebook', path:'/api/notebook/notes', query:'limit=25&include_curated=true&scope=all', ready:'#nb-note-list .nb-entry'},
];
function phaseMatches(phase, url) {
  return url.pathname === phase.path && url.searchParams.toString() === phase.query;
}
async function authenticatedColdDocument(page) {
  await signIn(page);
  // Retain only the real fixture cookie. The setup document's preparation must
  // not consume holds or supply a cache for the document being tested.
  await page.goto('about:blank');
  await page.addInitScript(() => { window.__reloadRecoveryDocument = crypto.randomUUID(); });
}

for (const phase of authenticatedPhases) {
  test(`Stop-loading recovers one authenticated ${phase.name} startup read without retrying writes`, async ({page}) => {
    await authenticatedColdDocument(page);
    const first = gate(), retry = gate(), releaseFirst = gate(), releaseRetry = gate();
    const held = [], writes = [], failures = [];
    let reads = 0;
    const matcher = url => url.origin === 'http://127.0.0.1:8029' && phaseMatches(phase, url);
    await page.route(matcher, route => {
      const number = ++reads;
      if (number > 2) return route.fallback();
      const task = (async () => {
        const response = await route.fetch();
        (number === 1 ? first : retry).resolve();
        await (number === 1 ? releaseFirst : releaseRetry).promise;
        await route.fulfill({response}).catch(() => {}); // Stop retires the first request.
      })();
      held.push(task); return task;
    });
    page.on('request', request => { if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname); });
    page.on('requestfailed', request => { if (phaseMatches(phase, new URL(request.url()))) failures.push(request.failure()?.errorText); });
    try {
      await page.goto('/?authenticated-stop=' + phase.name + phase.hash, {waitUntil:'domcontentloaded'});
      await first.promise;
      await expect(page.locator('#account-handle')).toHaveText('browser_alice');
      const documentId = await page.evaluate(() => window.__reloadRecoveryDocument);
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Page.stopLoading');
      await expect.poll(() => reads, {message:'The interrupted foreground GET recovers exactly once'}).toBe(2);
      await retry.promise;
      expect(reads).toBe(2);
      expect(failures).toContain('net::ERR_ABORTED');
      expect(await page.evaluate(() => window.__reloadRecoveryDocument)).toBe(documentId);
      await expect(page.locator('#global-message')).not.toBeVisible();
      await expect(page.locator('body')).not.toContainText('The server could not be reached.');
      expect(writes).toEqual([]);
      releaseFirst.resolve(); releaseRetry.resolve(); await Promise.all(held);
      await expect(page.locator(phase.ready).first()).toBeVisible();
      await expect(page.locator('#global-message')).not.toBeVisible();
      await expect(page.locator('body')).not.toContainText('The server could not be reached.');
      expect(await page.evaluate(() => window.__reloadRecoveryDocument)).toBe(documentId);
      expect(reads).toBe(2); expect(writes).toEqual([]);
    } finally {
      releaseFirst.resolve(); releaseRetry.resolve(); await Promise.allSettled(held); await page.unroute(matcher);
    }
  });
}

for (const phase of [{name:'session', hash:'#challenge', path:'/api/session', query:'', ready:'#catalogue-workspace .kem-name'}, ...authenticatedPhases]) {
  test(`real reload replaces a document with its authenticated ${phase.name} read pending`, async ({page}) => {
    await authenticatedColdDocument(page);
    const arrived = gate(), release = gate(), held = [], writes = [];
    let reads = 0;
    const matcher = url => url.origin === 'http://127.0.0.1:8029' && phaseMatches(phase, url);
    await page.route(matcher, route => {
      if (++reads > 1) return route.fallback();
      const task = (async () => {
        const response = await route.fetch(); arrived.resolve(); await release.promise;
        await route.fulfill({response}).catch(() => {}); // The old document cannot consume this response.
      })();
      held.push(task); return task;
    });
    page.on('request', request => { if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname); });
    try {
      await page.goto('/?authenticated-reload=' + phase.name + phase.hash, {waitUntil:'domcontentloaded'});
      await arrived.promise;
      const previousDocument = await page.evaluate(() => window.__reloadRecoveryDocument);
      await page.reload({waitUntil:'domcontentloaded'});
      await expect(page.locator(phase.ready).first()).toBeVisible();
      expect(await page.evaluate(() => window.__reloadRecoveryDocument)).not.toBe(previousDocument);
      release.resolve(); await Promise.all(held);
      await expect(page.locator('#account-handle')).toHaveText('browser_alice');
      await expect(page.locator('#global-message')).not.toBeVisible();
      await expect(page.locator('body')).not.toContainText('The server could not be reached.');
      expect(writes).toEqual([]);
    } finally { release.resolve(); await Promise.allSettled(held); await page.unroute(matcher); }
  });
}

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

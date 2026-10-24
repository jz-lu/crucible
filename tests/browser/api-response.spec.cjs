const {test, expect, signIn, notebookReady, gate} = require('./helpers.cjs');

function privateReads(page) {
  const paths = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/^\/(?:notebook\/|map\/|api\/(?:notebook\/|schemes))/.test(path)) paths.push(path);
  });
  return paths;
}

test('an unexpected successful service page needs an explicit fresh access retry', async ({page}) => {
  let reads = 0;
  const protectedPaths = privateReads(page);
  await page.route('**/api/session', route => {
    if (++reads === 1) return route.fulfill({status: 200, contentType: 'text/html',
      body: '<!doctype html><title>Synthetic service page</title>'});
    return route.continue();
  });
  await page.goto('/#notebook');
  await expect(page.locator('#global-message-text')).toHaveText('The server returned an unexpected page. Please try again.');
  await expect(page.locator('#notebook-workspace')).not.toBeVisible();
  expect(protectedPaths).toEqual([]);
  expect(reads).toBe(1);
  await page.locator('#retry-button').click();
  await expect(page.locator('#global-message')).not.toBeVisible();
  await expect(page.locator('#access-sign-in')).toBeVisible();
  expect(reads).toBe(2);
  expect(protectedPaths).toEqual([]);
  await signIn(page, 'alice', {navigate: false});
  await notebookReady(page);
});

test('an interrupted response body stays private until a user retries fresh access', async ({page}) => {
  await signIn(page);
  await notebookReady(page);
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    let interrupted = false;
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (!interrupted && new URL(args[0], location.href).pathname === '/api/session' && response.ok) {
        interrupted = true;
        // A real browser Response whose stream fails after headers. The actual
        // loopback request still authenticates normally; this only breaks its body.
        return new Response(new ReadableStream({start(controller) {
          controller.enqueue(new TextEncoder().encode('{'));
          queueMicrotask(() => controller.error(new TypeError('Synthetic interrupted body')));
        }}), {status: 200, headers: {'Content-Type': 'application/json'}});
      }
      return response;
    };
  });
  const protectedPaths = privateReads(page);
  await page.reload();
  await expect(page.locator('#global-message-text')).toHaveText('The connection was interrupted before the response finished. Please try again.');
  await expect(page.locator('#notebook-workspace')).not.toBeVisible();
  expect(protectedPaths).toEqual([]);
  await page.locator('#retry-button').click();
  await notebookReady(page);
  await expect(page.locator('#global-message')).not.toBeVisible();
  await expect(page.locator('#account-handle')).toHaveText('browser_alice');
});

test('rapid reloads discard pending access responses without stale private content', async ({page}) => {
  await signIn(page);
  await notebookReady(page);
  const arrivals = [gate(), gate(), gate()], release = gate();
  const held = [];
  let reads = 0;
  await page.route('**/api/session', route => {
    const index = reads++;
    if (index >= arrivals.length) return route.continue();
    const task = (async () => {
      const response = await route.fetch();
      arrivals[index].resolve();
      await release.promise;
      // The originating document was deliberately replaced by a later reload.
      await route.fulfill({response}).catch(() => {});
    })();
    held.push(task);
    return task;
  });
  const protectedPaths = privateReads(page);
  try {
    for (const arrival of arrivals) {
      await page.reload({waitUntil: 'domcontentloaded'});
      await arrival.promise;
      await expect(page.locator('#notebook-workspace')).not.toBeVisible();
      expect(protectedPaths).toEqual([]);
    }
    await page.reload();
    await notebookReady(page);
    release.resolve();
    await Promise.all(held);
    await expect(page.locator('#global-message')).not.toBeVisible();
    await expect(page.locator('#account-handle')).toHaveText('browser_alice');
    expect(reads).toBe(4);
  } finally {
    release.resolve();
    await Promise.allSettled(held);
  }
});

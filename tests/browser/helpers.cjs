const {test: base, expect} = require('playwright/test');

const test = base.extend({
  page: async ({page}, use) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // Tests only talk to their owned loopback app. Unexpected third-party
    // traffic is a failure, not a dependency on production/provider services.
    const external = [];
    await page.context().route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === 'http://127.0.0.1:8029') return route.fallback();
      external.push(url.origin); return route.abort();
    });
    await use(page);
    expect(errors, 'uncaught browser errors').toEqual([]);
    expect(external, 'unexpected external requests').toEqual([]);
  },
});

async function signIn(page, actor = 'alice', {navigate = true} = {}) {
  if (navigate) await page.goto('/#notebook');
  await page.locator('#access-sign-in').click();
  await page.locator('#auth-form').getByLabel('Public handle', {exact: true}).fill('browser_' + actor);
  await page.locator('#auth-form').getByLabel('Password', {exact: true}).fill('Synthetic browser fixture password');
  await page.locator('#auth-submit').click();
  await expect(page.locator('#account-handle')).toHaveText('browser_' + actor);
  await expect(page.locator('#auth-dialog')).not.toBeVisible();
}

async function notebookReady(page, actor = 'alice') {
  await expect(page.locator('#nb-note-list')).toBeVisible();
  await expect(page.locator('#nb-note-list')).toContainText(actor[0].toUpperCase() + actor.slice(1) + ' browser-only private note');
}

function gate() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

// Hold a real response after the server has authorized/read it. This creates
// a deterministic race without fake payloads or time-based speed assertions.
async function holdResponse(page, predicate) {
  const arrived = gate(), release = gate(), delivered = gate();
  let held = false;
  const matcher = url => url.origin === 'http://127.0.0.1:8029' && predicate(url);
  await page.route(matcher, async route => {
    if (held) return route.fallback();
    held = true;
    try {
      const response = await route.fetch();
      arrived.resolve(response);
      await release.promise;
      await route.fulfill({response});
    } finally {delivered.resolve();}
  });
  return {arrived: arrived.promise, delivered: delivered.promise, release: () => release.resolve(),
    remove: () => page.unroute(matcher)};
}

module.exports = {test, expect, signIn, notebookReady, gate, holdResponse};

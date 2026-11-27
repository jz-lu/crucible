const {test, expect, holdResponse, signIn} = require('./helpers.cjs');

const palette = {
  light: {background: 'rgb(235, 237, 230)', color: 'rgb(40, 60, 48)', meta: '#ebede6'},
  dark: {background: 'rgb(23, 26, 24)', color: 'rgb(204, 212, 204)', meta: '#171a18'},
};

async function expectTheme(page, theme) {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('#theme-toggle')).toBeVisible();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', palette[theme].meta);
  const browserTheme = await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  await expect(page.locator('#site-icon')).toHaveAttribute('href', new RegExp('/assets/crucible-icon-' + browserTheme + '\\.svg\\?v=[a-f0-9]{24}$'));
  expect(await page.locator('body').evaluate(element => ({
    background: getComputedStyle(element).backgroundColor,
    color: getComputedStyle(element).color,
  }))).toEqual({background: palette[theme].background, color: palette[theme].color});
  // The light logo palette remains stable on first paint and both reload paths.
  await expect(page.locator('.site-header .brand-mark')).toHaveCSS('filter',
    new RegExp(`url\\("#crucible-vessel-${theme}"\\)`));
}

for (const osTheme of ['dark', 'light']) {
  const selected = 'dark';
  test(`saved dark first paint survives storage and reloads on a ${osTheme} OS`, async ({page, context}) => {
    await page.emulateMedia({colorScheme: osTheme});
    // Observe actual rendering opportunities, rather than checking only the final
    // DOM. This runs before the real CSP-hashed bootstrap on each document load.
    await page.addInitScript(() => {
      localStorage.setItem('crucible.theme', 'dark');
      window.themeFrames = [];
      window.themeCspViolations = [];
      document.addEventListener('securitypolicyviolation', event => window.themeCspViolations.push(event.violatedDirective));
      const sample = () => {
        if (document.body && window.themeFrames.length < 240) {
          window.themeFrames.push({theme: document.documentElement.getAttribute('data-theme'),
            background: getComputedStyle(document.body).backgroundColor});
        }
        if (window.themeFrames.length < 240) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.goto('/#challenge');
    await expectTheme(page, selected);
    expect(await page.evaluate(() => localStorage.getItem('crucible.theme'))).toBe('dark');
    await signIn(page, 'alice', {navigate: false});
    await expect(page.locator('#kem-catalogue')).toContainText('Diffie');

    for (const forced of [false, true]) {
      const session = await holdResponse(page, url => url.pathname === '/api/session');
      try {
        if (forced) {
          const client = await context.newCDPSession(page);
          try {
            await Promise.all([
              page.waitForEvent('domcontentloaded'),
              client.send('Page.reload', {ignoreCache: true}),
            ]);
          } finally {await client.detach();}
        } else {
          await page.reload({waitUntil: 'domcontentloaded'});
        }
        await session.arrived;
        await expectTheme(page, selected);
        await expect(page.locator('#access-workspace')).toHaveAttribute('aria-busy', 'true');
        // Ensure at least two frames in the blocked authorization state.
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        session.release(); await session.delivered;
        await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
        await expectTheme(page, selected);
        const frames = await page.evaluate(() => window.themeFrames);
        expect(frames.length).toBeGreaterThan(0);
        expect(frames.every(frame => frame.theme === selected && frame.background === palette[selected].background),
          `no opposite-theme frame during ${forced ? 'forced' : 'ordinary'} reload`).toBe(true);
        expect(await page.evaluate(() => window.themeCspViolations)).toEqual([]);
      } finally {session.release(); await session.delivered; await session.remove();}
    }
    await page.emulateMedia({colorScheme: selected});
    await page.emulateMedia({colorScheme: osTheme});
    await expectTheme(page, selected);
  });
}

test('denied browser storage preserves system appearance and beta sign-in', async ({page}) => {
  await page.emulateMedia({colorScheme: 'dark'});
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', {
    configurable: true, get() {throw new DOMException('Blocked by browser policy', 'SecurityError');},
  }));
  await page.goto('/');
  await expectTheme(page, 'dark');
  await page.reload();
  await expectTheme(page, 'dark');
  await expect(page.locator('#access-sign-in')).toBeVisible();
});

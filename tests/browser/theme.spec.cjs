const {test, expect, holdResponse, signIn} = require('./helpers.cjs');

const palette = {
  light: {background: 'rgb(235, 237, 230)', color: 'rgb(40, 60, 48)', meta: '#ebede6'},
  dark: {background: 'rgb(23, 26, 24)', color: 'rgb(204, 212, 204)', meta: '#171a18'},
};
const opposite = theme => theme === 'dark' ? 'light' : 'dark';

async function expectTheme(page, theme) {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('#theme-toggle')).toHaveAttribute('aria-label', `Switch to ${opposite(theme)} theme`);
  await expect(page.locator('#theme-toggle-label')).toHaveText(`${opposite(theme) === 'dark' ? 'Dark' : 'Light'} mode`);
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', palette[theme].meta);
  expect(await page.locator('body').evaluate(element => ({
    background: getComputedStyle(element).backgroundColor,
    color: getComputedStyle(element).color,
  }))).toEqual({background: palette[theme].background, color: palette[theme].color});
  // The saved theme must select the vessel's matching RESI color on first
  // paint and after both kinds of reload, without replacing the decoded PNG.
  await expect(page.locator('.site-header .brand-mark')).toHaveCSS('filter',
    new RegExp(`url\\("#crucible-vessel-${theme}"\\)`));
}

for (const selected of ['dark', 'light']) {
  test(`explicit ${selected} survives ordinary and forced reloads with the opposite OS preference`, async ({page, context}) => {
    await page.emulateMedia({colorScheme: opposite(selected)});
    // Observe actual rendering opportunities, rather than checking only the final
    // DOM. This runs before the real CSP-hashed bootstrap on each document load.
    await page.addInitScript(() => {
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
    await expectTheme(page, opposite(selected));
    await page.locator('#theme-toggle').click();
    await expectTheme(page, selected);
    expect(await page.evaluate(() => localStorage.getItem('crucible.theme'))).toBe(selected);
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
    await page.emulateMedia({colorScheme: opposite(selected)});
    await expectTheme(page, selected);
  });
}

test('denied browser storage keeps the theme control usable without promising reload persistence', async ({page}) => {
  await page.emulateMedia({colorScheme: 'light'});
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', {
    configurable: true, get() {throw new DOMException('Blocked by browser policy', 'SecurityError');},
  }));
  await page.goto('/');
  await expectTheme(page, 'light');
  await page.locator('#theme-toggle').click();
  await expectTheme(page, 'dark');
  await page.reload();
  // Storage denial removes the persistence mechanism. The OS preference is the
  // documented fallback, without crashing access or introducing other storage.
  await expectTheme(page, 'light');
  await expect(page.locator('#access-sign-in')).toBeEnabled();
});

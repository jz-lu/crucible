const {test, expect, signIn, notebookReady, useDataSaver} = require('./helpers.cjs');

const destinations = [
  ['#nav-challenge', '#catalogue-title', 'Schemes'],
  ['#nav-bounties', '.bty-title-row h1', 'Bounties'],
  ['#nav-notebook', '[data-nb="notes-title"]', 'Notes'],
  ['#nav-about', '#about-workspace h1', 'About us'],
];
async function headerGeometry(page) {
  return page.locator('.header-inner, .site-header .brand, .primary-nav .nav-button:visible, .account-actions').evaluateAll(elements =>
    elements.map(element => {
      const {x, y, width, height} = element.getBoundingClientRect();
      return {x, y, width, height};
    }));
}

for (const width of [1440, 390]) test(`the Scheme map button opens at the document top at ${width}px`, async ({page}) => {
  await page.setViewportSize({width, height: 400}); await useDataSaver(page);
  await signIn(page); await notebookReady(page);
  await page.locator('#nav-challenge').click(); await expect(page.locator('#catalogue-title')).toBeVisible();
  await page.evaluate(() => scrollTo(0, 25)); expect(await page.evaluate(() => scrollY)).toBe(25);
  await page.locator('#scheme-map-button').click();
  await expect(page.locator('[data-nb="notes-title"]')).toHaveText('Scheme map');
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
  await expect(page.locator('#landscape svg')).toBeVisible();
  expect(await page.evaluate(() => scrollY)).toBe(0);
});
function sameGeometry(actual, expected, label) {
  expect(actual.length).toBe(expected.length);
  for (let index = 0; index < actual.length; index++) for (const axis of ['x','y','width','height']) {
    expect(Math.abs(actual[index][axis] - expected[index][axis]), `${label}: header item ${index} ${axis}`).toBeLessThan(0.5);
  }
}

for (const width of [1440, 1000, 700, 390]) {
  test(`main tabs keep a stable header and aligned page titles at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 800}); await useDataSaver(page);
    await signIn(page); await notebookReady(page);
    await page.evaluate(() => scrollTo(0, 0));
    let header, title;
    for (const [navigation, heading, label] of destinations) {
      await page.locator(navigation).click();
      const target = page.locator(heading); await expect(target).toBeVisible();
      await expect(target).toHaveText(label);
      await expect.poll(() => page.evaluate(() => scrollY), `${label} opens at the top`).toBe(0);
      await page.evaluate(() => document.fonts.ready);
      const geometry = await headerGeometry(page);
      if (header) sameGeometry(geometry, header, label); else header = geometry;
      {
        const actual = await target.evaluate(element => {
          const {x,y} = element.getBoundingClientRect(), style = getComputedStyle(element);
          return {x,y,font: style.font,spacing: style.letterSpacing};
        });
        if (title) {
          expect(Math.abs(actual.x - title.x), `${label}: title left edge`).toBeLessThan(0.5);
          expect(Math.abs(actual.y - title.y), `${label}: title top edge`).toBeLessThan(0.5);
          expect(actual.font, `${label}: title font`).toBe(title.font);
          expect(actual.spacing).toBe(title.spacing);
        } else title = actual;
      }
    }
    await page.locator('#nav-notebook').click(); await notebookReady(page);
    await page.screenshot({path: testInfo.outputPath(`main-tabs-${width}.png`), fullPage: true});
  });

  test(`every main-tab click, including the selected tab, resets scroll at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height: 800}); await useDataSaver(page);
    await signIn(page); await notebookReady(page);
    for (const [navigation, heading] of [...destinations, destinations[2]]) {
      await page.locator('#nav-notebook').click(); await notebookReady(page);
      await page.evaluate(() => scrollTo(0, 25));
      expect(await page.evaluate(() => scrollY)).toBe(25);
      await page.locator(navigation).click(); await expect(page.locator(heading)).toBeVisible();
      await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    }
    await page.evaluate(() => scrollTo(0, 25));
    await page.locator('#nav-about').click();
    await expect(page.locator('#about-title')).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
    await page.goBack(); await notebookReady(page);
    await expect.poll(() => page.evaluate(() => scrollY), 'Back retains the previous Notes position').toBe(25);
  });
}

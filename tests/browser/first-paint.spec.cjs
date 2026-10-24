const {test, expect, holdResponse} = require('./helpers.cjs');

test('the complete decoded logo fills its reserved frame without partial paint or movement', async ({page}) => {
  const image = await holdResponse(page, url => url.pathname === '/assets/crucible-logo.png');
  try {
    await page.goto('/', {waitUntil: 'domcontentloaded'}); await image.arrived;
    const logo = page.locator('.site-header .brand-mark');
    await expect(page.locator('html')).toHaveClass(/decode-brand-marks/);
    await expect(logo).toBeHidden();
    const before = await logo.boundingBox();
    expect(before.width).toBe(38); expect(before.height).toBe(54);
    await expect(page.locator('.site-header .brand > span')).toBeVisible();
    image.release(); await image.delivered;
    await expect(logo).toHaveAttribute('data-logo-ready', 'true');
    await expect(logo).toBeVisible();
    expect(await logo.boundingBox()).toEqual(before);
    expect(await logo.evaluate(img => ({complete: img.complete, width: img.naturalWidth, height: img.naturalHeight})))
      .toEqual({complete: true, width: 189, height: 192});
    await expect(page.locator('#access-title')).toHaveText('Private beta');
  } finally {image.release(); await image.delivered; await image.remove();}
});

test('an interrupted image leaves a usable wordmark and a reload recovers normally', async ({page}) => {
  const matcher = '**/assets/crucible-logo.png*';
  await page.route(matcher, route => route.abort('connectionreset'));
  await page.goto('/');
  const logo = page.locator('.site-header .brand-mark');
  await expect(logo).toBeHidden();
  await expect(page.locator('.site-header .brand > span')).toBeVisible();
  await expect(page.locator('#access-sign-in')).toBeEnabled();
  const before = await logo.boundingBox();
  await page.unroute(matcher); await page.reload();
  await expect(logo).toHaveAttribute('data-logo-ready', 'true');
  await expect(logo).toBeVisible();
  expect(await logo.boundingBox()).toEqual(before);
});

test('an already completed image remains visible if the early script arrives late', async ({page}) => {
  await page.goto('/');
  await expect(page.locator('.site-header .brand-mark')).toHaveAttribute('data-logo-ready', 'true');
  const script = await holdResponse(page, url => url.pathname === '/bootstrap.js');
  try {
    await page.reload({waitUntil: 'domcontentloaded'}); await script.arrived;
    const logo = page.locator('.site-header .brand-mark');
    await expect(logo).toBeVisible();
    expect(await logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
    await logo.evaluate(img => {
      window.lateLogoVisibility = [];
      window.lateLogoObserver = new MutationObserver(() => window.lateLogoVisibility.push(getComputedStyle(img).visibility));
      window.lateLogoObserver.observe(document.documentElement, {attributes: true, subtree: true});
    });
    script.release(); await script.delivered;
    await expect(logo).toHaveAttribute('data-logo-ready', 'true');
    await expect(logo).toBeVisible();
    const observed = await page.evaluate(() => {window.lateLogoObserver.disconnect(); return window.lateLogoVisibility;});
    expect(observed.length).toBeGreaterThan(0);
    expect(observed.every(value => value === 'visible'), 'late bootstrap never re-hides the completed image').toBe(true);
  } finally {
    script.release(); await script.delivered; await script.remove();
    await page.evaluate(() => window.lateLogoObserver?.disconnect());
  }
});

test('a slow stylesheet retains the fixed critical canvas and settles to the normal style', async ({page}) => {
  const stylesheet = await holdResponse(page, url => url.pathname === '/styles.css');
  const violations = [];
  await page.addInitScript(() => {
    window.fixtureCspViolations = [];
    document.addEventListener('securitypolicyviolation', event => window.fixtureCspViolations.push(event.violatedDirective));
  });
  try {
    const response = await page.goto('/', {waitUntil: 'commit'}); await stylesheet.arrived;
    expect(response.headers()['content-security-policy']).not.toContain('unsafe-inline');
    const critical = await page.locator('head > style').textContent();
    expect(critical).toContain('background:#f6f9fd');
    expect(critical).toContain('font:14px/1.6 -apple-system');
    stylesheet.release(); await stylesheet.delivered;
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    expect(await page.locator('body').evaluate(element => {
      const style = getComputedStyle(element);
      return {background: style.backgroundColor, color: style.color, size: style.fontSize, height: style.lineHeight};
    })).toEqual({background: 'rgb(246, 249, 253)', color: 'rgb(45, 69, 99)', size: '14px', height: '22.4px'});
    expect(await page.evaluate(() => window.fixtureCspViolations)).toEqual(violations);
  } finally {stylesheet.release(); await stylesheet.delivered; await stylesheet.remove();}
});

test('an interrupted stylesheet keeps the same base typography and canvas without exposing private workspaces', async ({page}) => {
  const baseStyle = element => {
    const style = getComputedStyle(element);
    return {background: style.backgroundColor, color: style.color, size: style.fontSize,
      height: style.lineHeight, family: style.fontFamily};
  };
  await page.goto('/');
  const normal = await page.locator('body').evaluate(baseStyle);
  await page.route('**/styles.css*', route => route.abort('connectionreset'));
  await page.reload();
  const fallback = await page.locator('body').evaluate(baseStyle);
  expect(fallback).toEqual(normal);
  await expect(page.locator('#access-title')).toHaveText('Private beta');
  await expect(page.locator('#catalogue-workspace')).toBeHidden();
  await expect(page.locator('#notebook-workspace')).toBeHidden();
  await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
});

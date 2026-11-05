const {test, expect, holdResponse, signIn, useDataSaver} = require('./helpers.cjs');
const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const criticalFontPaths = [
  '/vendor/resi/cardo-normal-700-latin.woff2',
  '/vendor/resi/plex-sans-normal-100-700-latin.woff2',
];

test('the complete decoded logo fills its reserved frame without partial paint or movement', async ({page}) => {
  const image = await holdResponse(page, url => url.pathname === '/assets/crucible-mark.svg');
  try {
    await page.goto('/', {waitUntil: 'domcontentloaded'}); await image.arrived;
    const logo = page.locator('.site-header .brand-mark');
    await expect(page.locator('html')).toHaveClass(/decode-brand-marks/);
    await expect(logo).toBeHidden();
    const before = await logo.boundingBox();
    expect(before.width).toBe(38); expect(before.height).toBe(54);
    await expect(page.locator('.site-header .brand-wordmark')).toBeVisible();
    image.release(); await image.delivered;
    await expect(logo).toHaveAttribute('data-logo-ready', 'true');
    await expect(logo).toBeVisible();
    expect(await logo.boundingBox()).toEqual(before);
    expect(await logo.evaluate(img => ({complete: img.complete, width: img.naturalWidth, height: img.naturalHeight})))
      .toEqual({complete: true, width: 189, height: 192});
    await expect(page.locator('#home-workspace')).toBeHidden();
  } finally {image.release(); await image.delivered; await image.remove();}
});

test('an interrupted image leaves a usable wordmark and a reload recovers normally', async ({page}) => {
  const matcher = '**/assets/crucible-mark.svg*';
  await page.route(matcher, route => route.abort('connectionreset'));
  await page.goto('/');
  const logo = page.locator('.site-header .brand-mark');
  await expect(logo).toBeHidden();
  await expect(page.locator('.site-header .brand-wordmark')).toBeVisible();
  await expect(page.locator('#access-sign-in')).toBeVisible();
  const before = await logo.boundingBox();
  await page.unroute(matcher); await page.reload();
  await expect(logo).toHaveAttribute('data-logo-ready', 'true');
  await expect(logo).toBeVisible();
  expect(await logo.boundingBox()).toEqual(before);
});

test('replaying the retained early-script endpoint cannot hide a completed logo or duplicate access', async ({page}) => {
  const sessions=[];page.on('request',request=>{if(new URL(request.url()).pathname==='/api/session')sessions.push(request.url());});
  await page.goto('/');
  await expect(page.locator('.site-header .brand-mark')).toHaveAttribute('data-logo-ready', 'true');
  try {
    const logo = page.locator('.site-header .brand-mark');
    await expect(logo).toBeVisible();
    expect(await logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
    await logo.evaluate(img => {
      window.lateLogoVisibility = [];
      window.lateLogoObserver = new MutationObserver(() => window.lateLogoVisibility.push(getComputedStyle(img).visibility));
      window.lateLogoObserver.observe(document.documentElement, {attributes: true, subtree: true});
    });
    await page.addScriptTag({url:'/bootstrap.js'});
    await expect(logo).toHaveAttribute('data-logo-ready', 'true');
    await expect(logo).toBeVisible();
    const observed = await page.evaluate(() => {window.lateLogoObserver.disconnect(); return window.lateLogoVisibility;});
    expect(observed.every(value => value === 'visible'), 'replayed bootstrap never re-hides the completed image').toBe(true);
    expect(sessions).toHaveLength(1);
  } finally {
    await page.evaluate(() => window.lateLogoObserver?.disconnect());
  }
});

async function assertInlineMainStyle(page, response, {notebookPrepared = false} = {}) {
  const policies = (await response.headersArray()).filter(header => header.name.toLowerCase() === 'content-security-policy');
  expect(policies).toHaveLength(1);
  expect(policies[0].value).not.toContain('unsafe-inline');
  expect(policies[0].value.split(';').map(value => value.trim()).filter(value => value.startsWith('font-src ')))
    .toEqual(["font-src 'self' data:"]);
  const css = readFileSync(resolve(__dirname, '../../web/styles.css'), 'utf8')
    .replace(/@font-face\s*\{[^}]*\}/g, face => {
      const asset = criticalFontPaths.find(path => face.includes(`url('${path}')`));
      if (!asset) return face;
      const bytes = readFileSync(resolve(__dirname, '../../web' + asset));
      return face.replace(`url('${asset}')`, `url('data:font/woff2;base64,${bytes.toString('base64')}')`)
        .replace(/font-display:\s*[^;]+;/, 'font-display: block;');
    })
    .replace(/url\('(\/vendor\/resi\/[^']+\.woff2)'\)/g, (_, asset) => {
      const bytes = readFileSync(resolve(__dirname, '../../web' + asset));
      const version = createHash('sha256').update(bytes).digest('hex').slice(0,24);
      return `url('${asset}?v=${version}')`;
    });
  await expect(page.locator('head > style[data-main-style]')).toHaveCount(1);
  expect(await page.locator('head > style[data-main-style]').textContent()).toBe(css);
  expect(await page.locator('head > link[rel="preload"][as="font"]').evaluateAll(links =>
    links.map(link => new URL(link.href).pathname))).toEqual(['/vendor/resi/cardo-normal-400-latin.woff2']);
  expect(await page.evaluate(() => [
    document.fonts.check('700 36px Cardo'),
    document.fonts.check('400 14px "IBM Plex Sans"'),
  ]), 'critical fonts decode with the first styled frame').toEqual([true, true]);
  const styles = await page.locator('head > style').allTextContents();
  expect(styles).toHaveLength(2); // Existing critical canvas plus exact main CSS.
  expect(policies[0].value.match(/'sha256-[^']+'/g)).toHaveLength(3);
  const bootstrap=readFileSync(resolve(__dirname,'../../web/bootstrap.js'),'utf8').replace(/\r\n?/g,'\n');
  await expect(page.locator('head > script[data-bootstrap]')).toHaveCount(1);
  expect(await page.locator('head > script[data-bootstrap]').textContent()).toBe(bootstrap);
  const scriptPolicy=policies[0].value.split(';').map(value=>value.trim()).find(value=>value.startsWith('script-src '));
  expect(scriptPolicy).toContain("'sha256-"+createHash('sha256').update(bootstrap).digest('base64')+"'");
  await expect(page.locator('head > script[src*="/bootstrap.js"]')).toHaveCount(0);
  for (const style of styles) {
    const hash = createHash('sha256').update(style).digest('base64');
    expect(policies[0].value).toContain("'sha256-" + hash + "'");
  }
  expect(await page.locator('head > script[defer]').evaluateAll(scripts => {
    const main = document.querySelector('head > style[data-main-style]');
    return scripts.map(script => ({path: new URL(script.src).pathname,
      beforeStyle: Boolean(script.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING)}));
  })).toEqual(['/math.js', '/assumptions.js', '/proposal-retractions.js', '/resource-metrics.js', '/app.js']
    .map(path => ({path, beforeStyle: true})));
  // Main/critical CSS is inline before admission. Once admitted, optional
  // notebook preparation may add its own stylesheet without affecting first
  // paint. Check exact paths so neither external main CSS nor an unexpected
  // dependency can hide behind that exception.
  expect(await page.locator('head > link[rel="stylesheet"]').evaluateAll(links =>
    links.map(link => new URL(link.href).pathname)))
    .toEqual(notebookPrepared ? ['/notebook/notebook.css'] : []);
}

async function blockExternalMainStyle(page) {
  const attempted = [];
  await page.route('**/styles.css*', route => {
    attempted.push(route.request().url()); return route.abort('connectionreset');
  });
  for (const path of criticalFontPaths) {
    await page.route('**' + path + '*', route => {
      attempted.push(route.request().url()); return route.abort('connectionreset');
    });
  }
  await page.addInitScript(() => {
    window.fixtureCspViolations = [];
    document.addEventListener('securitypolicyviolation', event => window.fixtureCspViolations.push(event.violatedDirective));
  });
  return attempted;
}

const bodyStyle = element => {
  const style = getComputedStyle(element);
  return {background: style.backgroundColor, color: style.color, size: style.fontSize, height: style.lineHeight};
};
const expectedBodyStyle = {background: 'rgb(235, 237, 230)', color: 'rgb(40, 60, 48)', size: '14px', height: '22.4px'};

// The external endpoint remains available for tools, but first paint must not
// depend on requesting it. Holding authorization also checks the pending shell.
test('the public shell is fully styled before access resolves without requesting external main CSS', async ({page}) => {
  const attempted = await blockExternalMainStyle(page);
  const session = await holdResponse(page, url => url.pathname === '/api/session');
  try {
    const response = await page.goto('/', {waitUntil: 'domcontentloaded'}); await session.arrived;
    await assertInlineMainStyle(page, response);
    await expect(page.locator('#home-workspace')).toBeHidden();
    await expect(page.locator('#home-progress')).toBeHidden();
    expect(await page.locator('body').evaluate(bodyStyle)).toEqual(expectedBodyStyle);
    expect(await page.locator('.site-header .brand-mark').boundingBox()).toMatchObject({width: 38, height: 54});
    await expect(page.locator('#catalogue-workspace')).toBeHidden();
    await expect(page.locator('#notebook-workspace')).toBeHidden();
    await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
    session.release(); await session.delivered;
    await expect(page.locator('#access-sign-in')).toBeVisible();
    await expect(page.locator('#home-workspace')).toBeHidden();
    expect(attempted).toEqual([]);
    expect(await page.evaluate(() => window.fixtureCspViolations)).toEqual([]);
  } finally {session.release(); await session.delivered; await session.remove();}
});

test('an admitted catalogue and ordinary reload retain full typography and layout with external main CSS blocked', async ({page}) => {
  const attempted = await blockExternalMainStyle(page);
  const response = await page.goto('/#challenge');
  await assertInlineMainStyle(page, response);
  await signIn(page, 'alice', {navigate: false});
  await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
  await expect(page.locator('#refresh-catalogue')).toBeEnabled();
  const catalogueStyle = () => page.locator('#catalogue-workspace').evaluate(element => {
    const heading = element.querySelector('h1'), title = getComputedStyle(heading);
    return {paddingTop: getComputedStyle(element).paddingTop, titleSize: title.fontSize,
      titleWeight: title.fontWeight, titleFamily: title.fontFamily,
      heading: {x: heading.getBoundingClientRect().x, y: heading.getBoundingClientRect().y},
      headerCells: [...element.querySelectorAll('thead th')].map(cell => cell.getBoundingClientRect().width),
      overflow: document.documentElement.scrollWidth > innerWidth};
  });
  const before = await catalogueStyle();
  expect(before).toMatchObject({paddingTop: '31px', titleSize: '32px', titleWeight: '700', overflow: false});
  expect(before.titleFamily).toContain('Cardo');
  expect(before.headerCells).toHaveLength(4);
  expect(before.headerCells.every(width => width > 0)).toBe(true);
  const reloaded = await page.reload();
  // Deliberately wait for preparation to exercise the post-admission state,
  // instead of racing the idle queue with a blanket no-stylesheets assertion.
  await expect(page.locator('head > link[rel="stylesheet"][href="/notebook/notebook.css"]')).toHaveCount(1);
  await assertInlineMainStyle(page, reloaded, {notebookPrepared: true});
  await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
  await expect(page.locator('#refresh-catalogue')).toBeEnabled();
  expect(await catalogueStyle()).toEqual(before);
  expect(await page.locator('body').evaluate(bodyStyle)).toEqual(expectedBodyStyle);
  expect(attempted).toEqual([]);
  expect(await page.evaluate(() => window.fixtureCspViolations)).toEqual([]);
});


async function holdAboutStartup(page) {
  const app = await holdResponse(page, url => url.pathname === '/app.js');
  const session = await holdResponse(page, url => url.pathname === '/api/session');
  const received = new Set();
  for (const response of [app, session]) void response.arrived.then(() => received.add(response));
  return {app, session, arrived:Promise.all([app.arrived, session.arrived]), async close() {
    app.release(); session.release();
    await Promise.all([...received].map(response => response.delivered));
    await app.remove(); await session.remove();
  }};
}

const aboutBounds = page => page.evaluate(() => Object.fromEntries([
  '.site-header', '#about-title', '#about-workspace .about-intro',
  '#about-mission-title', '#about-community-title',
].map(selector => {
  const box = document.querySelector(selector).getBoundingClientRect();
  return [selector, {x:box.x, y:box.y, width:box.width, height:box.height}];
})));

function trackProtectedReads(page) {
  const reads = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if ((path.startsWith('/api/') && path !== '/api/session') || path.startsWith('/notebook/') || path.startsWith('/map/')) reads.push(path);
  });
  return reads;
}

test('an admitted About reload paints its real static content before app and session', async ({page}) => {
    const width = 390;
    await page.setViewportSize({width, height:1000}); await page.emulateMedia({reducedMotion:'reduce'}); await useDataSaver(page);
    await signIn(page); await page.locator('#nav-about').click();
    await expect(page.locator('#about-workspace')).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('crucible.pending-presentation'))).toBe('workspace');
    // Retire the setup document so it cannot consume the new document's holds.
    await page.goto('about:blank');
    const held = await holdAboutStartup(page), reads = trackProtectedReads(page);
    try {
      await page.goto('/?about-reload=' + width + '#about', {waitUntil:'commit'}); await held.arrived;
      const about = page.locator('#about-workspace');
      await expect(page.locator('html')).toHaveAttribute('data-pending-page','about');
      await expect(about).toBeVisible(); await expect(about).toContainText('Our principles');
      await expect(page.locator('#access-workspace')).toBeHidden(); await expect(page.locator('#route-loading')).toBeHidden();
      await expect(page.locator('#catalogue-workspace')).toBeHidden(); await expect(page.locator('#notebook-workspace')).toBeHidden();
      await expect(page.locator('.primary-nav')).toHaveAttribute('inert','');
      await page.evaluate(() => document.fonts.ready);
      const before = await aboutBounds(page), original = await about.elementHandle();
      expect(reads).toEqual([]);
      held.app.release(); await held.app.delivered;
      await expect(page.locator('body')).toHaveAttribute('data-access-pending','true');
      await expect(about).toBeVisible(); await expect(page.locator('#access-workspace')).toBeHidden();
      expect(await aboutBounds(page)).toEqual(before); expect(reads).toEqual([]);
      held.session.release(); await held.session.delivered;
      await expect(page.locator('body')).not.toHaveAttribute('data-access-pending','true');
      await expect(page.locator('.primary-nav')).not.toHaveAttribute('inert','');
      await expect(page.locator('#nav-about')).toHaveAttribute('aria-current','page');
      await expect(about).toBeVisible(); await expect(page.locator('#access-workspace')).toBeHidden();
      await expect(page.locator('#route-loading')).toBeHidden();
      expect(await aboutBounds(page)).toEqual(before);
      expect(await original.evaluate(element => element.isConnected)).toBe(true);
      expect(reads).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally { await held.close(); }
});

test('About access failure stays gated, retry respects an expired session and the gate survives reload', async ({page, context}) => {
  await page.emulateMedia({reducedMotion:'reduce'}); await useDataSaver(page);
  await signIn(page); await page.locator('#nav-about').click(); await expect(page.locator('#about-workspace')).toBeVisible();
  await page.goto('about:blank'); await context.clearCookies();
  const reads = trackProtectedReads(page), app = await holdResponse(page, url => url.pathname === '/app.js');
  await page.route('**/api/session', route => route.abort('connectionrefused'));
  try {
    await page.goto('/?about-failed-session#about', {waitUntil:'commit'}); await app.arrived;
    await expect(page.locator('#about-workspace')).toBeVisible(); await expect(page.locator('#access-workspace')).toBeHidden();
    app.release(); await app.delivered;
    await expect(page.locator('#access-description')).toContainText('Access could not be checked');
    await expect(page.locator('#access-retry')).toBeEnabled();
    await expect(page.locator('#about-workspace')).toBeHidden(); expect(reads).toEqual([]);
    await page.unroute('**/api/session'); await page.locator('#access-retry').click();
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    await expect(page.locator('#access-create-account')).toBeVisible(); await expect(page.locator('#about-workspace')).toBeHidden();
    expect(await page.evaluate(() => sessionStorage.getItem('crucible.pending-presentation'))).toBe('gate');
    expect(reads).toEqual([]);
  } finally { app.release(); await app.delivered; await app.remove(); await page.unroute('**/api/session'); }
  const held = await holdAboutStartup(page);
  try {
    await page.reload({waitUntil:'commit'}); await held.arrived;
    await expect(page.locator('html')).toHaveAttribute('data-pending-page','gate');
    await expect(page.locator('#about-workspace')).toBeHidden(); await expect(page.locator('#access-workspace')).toBeVisible();
    await expect(page.locator('#access-title')).toHaveText('Private beta'); expect(reads).toEqual([]);
    held.app.release(); await held.app.delivered; held.session.release(); await held.session.delivered;
    await expect(page.locator('#access-create-account')).toBeVisible(); await expect(page.locator('#about-workspace')).toBeHidden();
    expect(reads).toEqual([]);
  } finally { await held.close(); }
});

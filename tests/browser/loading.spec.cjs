const {test, expect, signIn, holdResponse} = require('./helpers.cjs');

async function geometry(locator) {
  return locator.evaluate(element => {
    const box = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {x: box.x, y: box.y, width: box.width, height: box.height,
      font: style.fontFamily, size: style.fontSize, weight: style.fontWeight, lineHeight: style.lineHeight};
  });
}

for (const [name, width] of [['desktop', 1440], ['mobile', 390]]) {
  test(`catalogue fills its stable ${name} layout without visible loading copy or animation`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await page.emulateMedia({reducedMotion: 'reduce'});
    await signIn(page);
    const session = await holdResponse(page, url => url.pathname === '/api/session');
    const catalogue = await holdResponse(page, url => url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'navigation');
    const reads = [];
    page.on('request', request => reads.push(new URL(request.url()).pathname));
    let accessHeading, accessTable, pendingHeading, pendingTable, pendingHeader, pendingFirstRow;
    try {
      // A changed document URL forces a cold bootstrap; changing only the
      // fragment is deliberately handled as a warm SPA navigation.
      await page.goto('/?loading-check=' + name + '#challenge', {waitUntil: 'domcontentloaded'}); await session.arrived;
      await expect(page.locator('#access-loading-preview')).toBeVisible();
      expect(reads.filter(path => path === '/api/schemes' || path.startsWith('/notebook/') || path.startsWith('/map/'))).toEqual([]);
      await expect(page.locator('body')).not.toContainText('ALICE_PRIVATE_BROWSER_BODY');
      accessHeading = await geometry(page.locator('#access-title'));
      accessTable = await geometry(page.locator('#access-loading-preview'));
      expect(await page.locator('#access-description').evaluate(element => getComputedStyle(element).clipPath)).toBe('inset(50%)');
      await page.screenshot({path: testInfo.outputPath('pending-access.png')});
      session.release(); await session.delivered; await catalogue.arrived;
      await expect(page.locator('#catalogue-workspace')).toBeVisible();
      await expect(page.locator('#route-loading')).toBeHidden();
      await expect(page.locator('#refresh-catalogue')).toBeDisabled();
      await expect(page.locator('#nav-about')).toBeEnabled();
      await expect(page.locator('#kem-catalogue a, #kem-catalogue button')).toHaveCount(0);
      pendingHeading = await geometry(page.locator('#catalogue-title'));
      pendingTable = await geometry(page.locator('#kem-catalogue .kem-table-wrap'));
      pendingHeader = await geometry(page.locator('#kem-catalogue thead'));
      pendingFirstRow = await geometry(page.locator('#kem-catalogue tbody tr').first());
      for (const field of ['x', 'y']) expect(pendingHeading[field], `heading ${field} after access`).toBeCloseTo(accessHeading[field], 0);
      for (const field of ['font', 'size', 'weight', 'lineHeight']) expect(pendingHeading[field]).toBe(accessHeading[field]);
      for (const field of ['x', 'y', 'width']) expect(pendingTable[field], `table ${field} after access`).toBeCloseTo(accessTable[field], 0);
      expect(await page.locator('.catalogue-placeholder').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none');
      expect(await page.locator('#kem-catalogue [role="status"]').evaluate(element => getComputedStyle(element).clipPath)).toBe('inset(50%)');
      await page.locator('#nav-about').focus();
      await page.screenshot({path: testInfo.outputPath('pending-catalogue.png')});
    } finally {
      session.release(); catalogue.release();
      await session.delivered; await catalogue.delivered;
      await session.remove(); await catalogue.remove();
    }
    await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
    await expect(page.locator('#kem-catalogue')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('#refresh-catalogue')).toBeEnabled();
    await expect(page.locator('#nav-about')).toBeFocused();
    const readyHeading = await geometry(page.locator('#catalogue-title'));
    const readyTable = await geometry(page.locator('#kem-catalogue .kem-table-wrap'));
    expect(readyHeading).toEqual(pendingHeading);
    for (const field of ['x', 'y', 'width']) expect(readyTable[field], `table ${field} after data`).toBeCloseTo(pendingTable[field], 0);
    const readyHeader = await geometry(page.locator('#kem-catalogue thead'));
    const readyFirstRow = await geometry(page.locator('#kem-catalogue tbody tr').first());
    expect(readyHeader.height, 'help control does not resize table heading').toBeCloseTo(pendingHeader.height, 0);
    expect(readyFirstRow.y, 'first row keeps its position').toBeCloseTo(pendingFirstRow.y, 0);
    // Mobile records have variable wrapped names and optional recovery values;
    // reserve a name line, not a height fitted to this one-scheme fixture.
    if (name === 'desktop') expect(Math.abs(readyFirstRow.height - pendingFirstRow.height)).toBeLessThan(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('ready-catalogue.png')});
  });
}

for (const route of ['#notebook', '#notebook?view=map']) {
  test(`cold ${route} has no catalogue title flash before the app starts`, async ({page}, testInfo) => {
    const appScript = await holdResponse(page, url => url.pathname === '/app.js');
    const session = await holdResponse(page, url => url.pathname === '/api/session');
    try {
      await page.goto('/' + route, {waitUntil: 'commit'}); await appScript.arrived; await session.arrived;
      await expect(page.locator('#access-title')).toBeEmpty();
      await expect(page.locator('#access-title')).toHaveAttribute('aria-hidden', 'true');
      expect((await page.locator('#access-title').boundingBox()).height).toBeGreaterThan(30);
      expect(await page.locator('#access-loading-preview thead').evaluate(element => getComputedStyle(element).visibility)).toBe('hidden');
      await expect(page.locator('.primary-nav')).toBeHidden();
      await page.screenshot({path: testInfo.outputPath('held-app-shell.png')});
      appScript.release(); await appScript.delivered;
      await expect(page.locator('#access-title')).toHaveText('Research notebook');
    } finally {
      appScript.release(); session.release();
      await appScript.delivered; await session.delivered; await appScript.remove(); await session.remove();
    }
    await expect(page.locator('#access-title')).toHaveText('Private beta');
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    await expect(page.locator('.access-heading-space')).toBeHidden();
    await expect(page.locator('.primary-nav')).toBeHidden();
    await page.screenshot({path: testInfo.outputPath('signed-out-gate.png')});
  });
}

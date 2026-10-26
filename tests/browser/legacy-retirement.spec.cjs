const {test, expect, signIn, notebookReady, useDataSaver} = require('./helpers.cjs');

for (const width of [1440, 390]) {
  test(`retired findings links preserve assumptions and notebook navigation at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await useDataSaver(page);
    const requests = [];
    page.on('request', request => requests.push(new URL(request.url()).pathname));
    await page.goto('/#research?target=finite-field-dlog');
    await signIn(page, 'alice', {navigate: false});
    await expect(page.locator('#catalogue-workspace')).toBeVisible();
    await expect(page).toHaveURL(/#challenge$/);
    await expect(page.locator('#nav-research, #research-workspace, #finding-dialog')).toHaveCount(0);

    await page.locator('.kem-name').first().click();
    await expect(page.locator('#overview-workspace')).toBeVisible();
    await page.locator('#overview-assumptions').click();
    await expect(page.locator('#assumptions-workspace')).toBeVisible();
    // The portable DH4 fixture has the honest uncurated-sheet fallback. Its
    // release-specific scope and supporting links still load normally.
    await expect(page.locator('#assumptions-content')).toContainText('Shared-secret recovery');
    await expect(page.locator('#assumptions-content')).toContainText('An assumptions sheet has not been curated for this release yet.');
    expect(requests).toContain('/api/protocol/assumptions');
    await page.screenshot({path: testInfo.outputPath('assumptions.png'), fullPage: true});

    await page.locator('#nav-notebook').click();
    await notebookReady(page);
    await expect(page.locator('#notebook-workspace')).toContainText('Alice browser-only private note');
    expect(requests.some(path => path === '/api/targets' || path.startsWith('/api/contributions'))).toBe(false);
  });
}

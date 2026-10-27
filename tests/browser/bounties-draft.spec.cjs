const {test, expect, signIn, notebookReady} = require('./helpers.cjs');

for (const width of [1440, 390, 320]) {
  test(`bounty draft navigation and local state stay coherent at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    const writes = [];
    page.on('request', request => {
      if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/')) writes.push(request.url());
    });
    await signIn(page); await notebookReady(page); writes.length = 0;
    const workspace = page.locator('#notebook-workspace');
    await workspace.locator('[data-nb="guide-view"]').click();
    const guide = workspace.locator('.rg-root');
    await expect(guide.locator('[data-rg-control="collection-title"]')).toHaveText('Research bounties');
    await expect(guide.locator('.rg-title-row')).toContainText('Example data');
    expect(await workspace.locator('.nb-visualization-tabs button').allTextContents()).toEqual(['Scheme map', 'Bounties', 'Timeline']);
    await expect(guide.locator('input[type="search"]')).toHaveCount(0);
    await expect(guide.locator('.rg-question').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await guide.locator('[name="sort-order"]').selectOption('points-high');
    await expect(page).toHaveURL(/sort=points-high/);
    const first = guide.locator('.rg-question').first();
    const title = await first.locator('.rg-question-title').textContent();
    await page.screenshot({path:testInfo.outputPath('bounty-board.png'), fullPage:true});
    await first.click();
    await expect(guide.locator('[data-rg-control="selected-title"]')).toHaveText(title);
    await expect(guide.locator('.rg-detail-body')).toContainText('What to contribute');
    await expect(guide.locator('.rg-detail-body')).toContainText('How it’s checked');
    await page.goBack();
    await expect(guide.locator('.rg-board')).toBeVisible();
    await expect(guide.locator('[name="sort-order"]')).toHaveValue('points-high');

    await guide.locator('[data-rg-control="post"]').click();
    await guide.locator('[name="post-title"]').fill('A local-only browser draft');
    await guide.locator('[name="post-progress"]').fill('Provide a reproducible example and its limits.');
    await guide.locator('[name="post-verification"]').fill('Re-run the example independently and compare its stated output.');
    await workspace.locator('[data-nb="notes-view"]').click();
    await notebookReady(page);
    await workspace.locator('[data-nb="guide-view"]').click();
    await expect(guide.locator('[name="post-title"]')).toHaveValue('A local-only browser draft');
    await guide.getByRole('button', {name:'Preview task', exact:true}).click();
    await expect(guide.locator('.rg-post-preview')).toContainText('Re-run the example independently');
    await guide.getByRole('button', {name:'Save proposal in draft', exact:true}).click();
    await expect(guide.locator('[data-rg-control="selected-title"]')).toHaveText('A local-only browser draft');
    await expect(guide.locator('.rg-detail-heading')).toContainText('Brief awaiting a check');
    expect(writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('bounty-local-draft.png'), fullPage:true});
    await page.reload();
    await expect(workspace.locator('.rg-root [data-rg-control="selected-title"]')).toHaveText('This bounty is no longer in the draft');
  });
}

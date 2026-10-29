const {test, expect, signIn, holdResponse} = require('./helpers.cjs');

test('scheme record to ready challenge and manual tools is one complete usable journey', async ({page}) => {
  const failures = [];
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (/^\/api\/(leaderboard|challenges|targets)/.test(path) && response.status() >= 400) {
      failures.push({path, status: response.status()});
    }
  });
  await signIn(page, 'bob');
  await page.locator('#nav-challenge').click();
  await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
  await page.locator('#kem-catalogue a').first().click();
  await expect(page.locator('#overview-workspace')).toBeVisible();
  await page.locator('#overview-challenges').click();
  const takeOn = page.getByRole('link', {name: 'Take on this stage →', exact: true});
  await expect(takeOn).toBeVisible();
  await takeOn.click();
  await expect(page.locator('#challenge-active')).toBeVisible();
  await expect(page.locator('#tab-challenge')).toBeFocused();
  await expect(page.locator('#use-agent')).toBeEnabled();
  await page.locator('#use-agent').click();
  await expect(page.locator('#agent-instructions')).toBeVisible();
  await expect(page.locator('#agent-instructions-text')).not.toBeEmpty();
  await page.locator('#manual-tools > summary').click();
  const download = page.waitForEvent('download');
  await page.locator('#download-button').click();
  expect((await download).suggestedFilename()).toMatch(/\.json$/);
  await page.setViewportSize({width: 390, height: 1000});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('#open-submission').click();
  await expect(page.locator('#submission-dialog')).toBeVisible();
  await expect(page.locator('#candidate')).toBeEnabled();
  await page.locator('#candidate').fill('00'.repeat(32));
  await page.locator('#method-name').fill('Unsent fixture method');
  await page.keyboard.press('Escape');
  await expect(page.locator('#submission-dialog')).toBeHidden();
  await page.locator('#open-submission').click();
  await expect(page.locator('#submission-dialog')).toBeVisible();
  await expect(page.locator('#candidate')).toHaveValue('00'.repeat(32));
  await expect(page.locator('#method-name')).toHaveValue('Unsent fixture method');
  await page.keyboard.press('Escape');
  await expect(page.locator('#submission-dialog')).toBeHidden();
  await expect(page.locator('#open-submission')).toBeFocused();
  await page.locator('#tab-leaderboard').click();
  await expect(takeOn).toBeVisible();
  const fresh = await holdResponse(page, url => url.pathname === '/api/challenges');
  try {
    await takeOn.click(); await fresh.arrived;
    await page.locator('#nav-about').focus();
    fresh.release(); await fresh.delivered;
    await expect(page.locator('#challenge-active')).toBeVisible();
    await expect(page.locator('#nav-about')).toBeFocused();
  } finally {fresh.release(); await fresh.delivered; await fresh.remove();}
  expect(failures).toEqual([]);
});

test('a maximum-length unbroken tag wraps without clipping in mobile previews, readers and filters', async ({page}) => {
  await page.setViewportSize({width: 390, height: 1000});
  await signIn(page, 'bob');
  const tag = 'T'.repeat(100), title = 'Long tag browser fixture';
  const id = await page.evaluate(async ({tag, title}) => {
    const result = await api('/api/notebook/notes', {idempotency_key: crypto.randomUUID(),
      content: {title, claim: 'Synthetic browser summary.', conditions: 'Disposable fixture only.',
        body: 'Synthetic browser body.', tags: [tag]}});
    return result.note.id;
  }, {tag, title});
  await page.evaluate(id => {window.location.hash = '#notebook?note=' + id;}, id);
  const reader = page.locator('[data-nb="full"]');
  await expect(reader.locator('.nb-full-title')).toHaveText(title);
  await expect(reader.getByRole('button', {name: tag, exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', {name: '← Research notebook', exact: true}).click();
  const entry = page.locator('#nb-note-list .nb-entry').filter({hasText: title});
  await entry.locator('.nb-title').click();
  const chip = entry.getByRole('button', {name: tag, exact: true});
  await expect(chip).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await chip.click();
  await expect(page.locator('#nb-note-list')).not.toHaveAttribute('aria-busy', 'true');
  const filterChip = page.locator('[data-nb="filter"] .nb-tag');
  await expect(filterChip).toContainText(tag);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await filterChip.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await filterChip.click();
  await expect(page.locator('[data-nb="filter"] .nb-tag')).toHaveCount(0);
});

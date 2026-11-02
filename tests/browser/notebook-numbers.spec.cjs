const {test, expect, signIn, notebookReady} = require('./helpers.cjs');

for (const width of [1440, 390, 320]) {
  test(`permanent note references stay quiet, searchable and exact at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    // Mutating fixtures own a separate actor so read-only neighboring suites
    // continue to see their exact seeded account, regardless of file order.
    await signIn(page, 'numbers');
    await notebookReady(page, 'numbers');
    const title = `Numeric reference browser fixture ${width}`;
    const created = await page.evaluate(async title => api('/api/notebook/notes', {
      idempotency_key: crypto.randomUUID(),
      content: {title, claim: 'Disposable reference and layout check.',
        conditions: 'Synthetic browser fixture only.', body: 'A private fixture body.', tags: ['fixture']},
    }), title);
    const note = created.note;
    expect(note.number).toBeGreaterThan(0);
    const label = '#' + String(note.number).padStart(4, '0');
    const workspace = page.locator('#notebook-workspace');
    await workspace.locator('[name="visibility"]').selectOption('mine');
    const row = workspace.locator('.nb-entry').filter({hasText: title});
    await expect(row).toHaveCount(1);
    await expect(row.locator('.nb-note-number')).toHaveText(label);
    const dialog = workspace.locator('dialog[data-nb="dialog"]');
    await row.locator('.nb-number-action').click();
    await expect(dialog).toHaveAttribute('aria-label','Cite this note');
    await expect(row.locator('.nb-title')).toHaveAttribute('aria-expanded','false');
    const listCitation=await dialog.locator('[name="citation-link"]').inputValue();
    expect(listCitation).toContain('revision=1');expect(listCitation).toContain('sha256='+note.content_sha256);
    await page.keyboard.press('Escape');await expect(row.locator('.nb-number-action')).toBeFocused();
    await row.locator('.nb-expand').click();
    await expect(row.locator('.nb-expand')).toHaveAttribute('aria-expanded','true');
    await expect(row.locator('.nb-expand')).toBeFocused();
    await expect(row.locator('.nb-note-number')).toHaveText(label);
    const placement = await row.evaluate(element => {
      const text=element.querySelector('.nb-title-text').getBoundingClientRect();
      const reference=element.querySelector('.nb-note-number').getBoundingClientRect(),marker=element.querySelector('.nb-marker').getBoundingClientRect();
      return {textLeft:text.left,textRight:text.right,titleRight:element.querySelector('.nb-title').getBoundingClientRect().right,referenceRight:reference.right,markerLeft:marker.left,
        referenceFont:parseFloat(getComputedStyle(element.querySelector('.nb-note-number')).fontSize)};
    });
    expect(placement.textLeft).toBeGreaterThan(placement.referenceRight);
    expect(placement.markerLeft).toBeGreaterThan(placement.textRight);
    expect(placement.markerLeft-placement.titleRight).toBeLessThanOrEqual(12);
    expect(placement.textLeft-placement.referenceRight).toBeLessThanOrEqual(12);
    expect(placement.referenceFont).toBeLessThanOrEqual(11);
    expect(await row.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('expanded-reference.png'), fullPage: true});

    await row.locator('.nb-title').click();
    await expect(row.locator('.nb-note-number')).toHaveText(label);
    await workspace.locator('[data-nb="search"]').fill(label);
    await expect(workspace.locator('.nb-entry')).toHaveCount(1);
    await expect(row).toContainText(title);
    await row.locator('.nb-title').click();
    await row.locator('.nb-read').click();
    const reader = workspace.locator('[data-nb="full"]');
    await expect(reader.locator('.nb-full-title')).toHaveText(title);
    await expect(reader.getByRole('heading',{level:1,name:title,exact:true})).toBeVisible();
    await expect(workspace.locator('.nb-heading')).toBeHidden();
    for(const name of ['files','publish-all','new'])await expect(workspace.locator('[data-nb="'+name+'"]')).toBeHidden();
    expect((await reader.boundingBox()).width).toBeGreaterThanOrEqual((await workspace.boundingBox()).width-2);
    await expect(reader.locator('.nb-number-action')).toHaveText(label);
    await reader.locator('.nb-number-action').click();
    await expect(dialog.locator('.nb-citation-number')).toHaveText(label + ' · revision 1');
    const citation = await dialog.locator('[name="citation-link"]').inputValue();
    expect(citation).toBe(listCitation);
    expect(citation).toContain('note=' + encodeURIComponent(note.id));
    expect(citation).toContain('revision=1');
    expect(citation).toContain('sha256=' + note.content_sha256);
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await dialog.locator('[data-nb="close"]').click();
    await page.reload();
    await expect(reader.locator('.nb-number-action')).toHaveText(label);
    await expect(reader.locator('.nb-full-title')).toHaveText(title);
    await page.screenshot({path: testInfo.outputPath('reader-reference.png'), fullPage: true});
    await reader.locator('.nb-back').click();await expect(workspace.locator('.nb-heading')).toBeVisible();
  });
}

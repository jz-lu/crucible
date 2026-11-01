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
    await row.locator('.nb-title').click();
    await expect(row.locator('.nb-note-number')).toHaveText(label);
    const placement = await row.evaluate(element => {
      const text=element.querySelector('.nb-title-text'),fragments=Array.from(text.getClientRects()),last=fragments.at(-1);
      const reference=element.querySelector('.nb-note-number').getBoundingClientRect(),marker=element.querySelector('.nb-marker').getBoundingClientRect();
      return {textLeft:text.getBoundingClientRect().left,textRight:last.right,textBottom:last.bottom,
        referenceLeft:reference.left,referenceRight:reference.right,referenceTop:reference.top,markerLeft:marker.left,
        referenceFont:parseFloat(getComputedStyle(element.querySelector('.nb-note-number')).fontSize)};
    });
    if(placement.referenceTop<placement.textBottom){
      expect(placement.referenceLeft-placement.textRight).toBeGreaterThanOrEqual(0);
      expect(placement.referenceLeft-placement.textRight).toBeLessThanOrEqual(12);
    } else expect(placement.referenceLeft-placement.textLeft).toBeLessThanOrEqual(12);
    expect(placement.markerLeft-placement.referenceRight).toBeGreaterThanOrEqual(0);
    expect(placement.markerLeft-placement.referenceRight).toBeLessThanOrEqual(10);
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
    await expect(reader.locator('.nb-number-action')).toHaveText(label);
    await reader.locator('.nb-number-action').click();
    const dialog = workspace.locator('dialog[data-nb="dialog"]');
    await expect(dialog.locator('.nb-citation-number')).toHaveText(label + ' · revision 1');
    const citation = await dialog.locator('[name="citation-link"]').inputValue();
    expect(citation).toContain('note=' + encodeURIComponent(note.id));
    expect(citation).toContain('revision=1');
    expect(citation).toContain('sha256=' + note.content_sha256);
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await dialog.locator('[data-nb="close"]').click();
    await page.reload();
    await expect(reader.locator('.nb-number-action')).toHaveText(label);
    await expect(reader.locator('.nb-full-title')).toHaveText(title);
    await page.screenshot({path: testInfo.outputPath('reader-reference.png'), fullPage: true});
  });
}

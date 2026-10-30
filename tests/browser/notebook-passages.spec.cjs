const {test, expect, signIn, notebookReady} = require('./helpers.cjs');

for (const width of [1440, 390]) {
  test(`passage citations select the exact saved body at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await signIn(page, 'numbers');
    await notebookReady(page, 'numbers');
    const body = '# Two separate statements\n\nFirst statement: \\(x^2 \\geq 0\\) for real \\(x\\).\n\nSecond statement: its conditions remain part of this passage.\n\n```text\nA literal <tag>, not markup.\n```';
    const {note} = await page.evaluate(async ({width, body}) => api('/api/notebook/notes', {
      idempotency_key: crypto.randomUUID(),
      content: {title: `Passage browser fixture ${width}`, claim: 'Disposable passage checks.',
        conditions: 'Synthetic browser fixture only.', body, tags: ['fixture']},
    }), {width, body});
    const route = '/#notebook?' + new URLSearchParams({note: note.id, revision: '1', sha256: note.content_sha256});
    await page.goto(route);
    const reader = page.locator('#notebook-workspace [data-nb="full"]');
    await expect(reader.locator('.nb-passage')).toHaveCount(4);
    await expect(reader.locator('[data-nb-location="L3"] .katex')).toHaveCount(2);
    await expect(reader.locator('[data-nb-location="L7"] pre')).toHaveText('A literal <tag>, not markup.');
    const cite = reader.getByRole('button', {name: 'Cite passage at line 5', exact: true});
    await cite.focus();
    await page.keyboard.press('Enter');
    const dialog = page.locator('#notebook-workspace dialog[data-nb="dialog"]');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('h2')).toHaveText('Cite this passage');
    const citation = await dialog.locator('[name="citation-link"]').inputValue();
    expect(citation).toContain('revision=1&sha256=' + note.content_sha256 + '&location=L5');
    const reference = JSON.parse(await dialog.locator('.nb-prompt').textContent());
    expect(reference.url).toContain('&location=L5');
    expect(reference.scope).toContain('Body, line 5');
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('passage-citation.png'), fullPage: true});
    await dialog.locator('[data-nb="close"]').click();
    await page.goto(citation);
    const selected = reader.locator('.nb-passage-selected');
    await expect(selected).toHaveAttribute('data-nb-location', 'L5');
    await expect(selected).toBeFocused();
    await expect(selected).toContainText('Second statement');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('selected-passage.png'), fullPage: true});
    await page.reload();
    await expect(selected).toBeFocused();
    await expect(selected).toHaveAttribute('data-nb-location', 'L5');
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(selected).toHaveAttribute('data-nb-location', 'L5');
    await page.screenshot({path: testInfo.outputPath('selected-passage-dark.png'), fullPage: true});
    await page.goto(route + '&location=L2');
    await expect(reader.locator('.nb-error')).toContainText('not present in this saved revision');
    await expect(reader.locator('.nb-body')).toHaveCount(0);
    await reader.getByRole('button', {name: 'Read this exact revision'}).click();
    await expect(reader.locator('.nb-body')).toBeVisible();
    expect(page.url()).not.toContain('location=');
    expect(page.url()).toContain('sha256=' + note.content_sha256);
  });
}

const {test, expect, signIn} = require('./helpers.cjs');

for (const width of [1440, 390]) {
  test(`Private draft deletion requires confirmation at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, 'alice');
    const saved = await page.evaluate(async () => (await api('/api/notebook/notes', {
      idempotency_key:crypto.randomUUID(), content:{title:'Disposable deletion browser fixture',
        claim:'Only this test-created draft may be deleted.', conditions:'Isolated local test database.',
        body:'No participant data.', tags:['fixture']}})).note);
    await page.goto('/#notebook?note=' + saved.id);
    const full = page.locator('[data-nb="full"]');
    await full.getByRole('button', {name:'Edit', exact:true}).click();
    const dialog = page.locator('#notebook-workspace dialog[data-nb="dialog"]');
    await dialog.getByRole('button', {name:'Delete', exact:true}).click();
    await expect(dialog).toHaveAttribute('aria-label', 'Delete note?');
    await expect(dialog).toContainText('This cannot be undone.');
    await expect(dialog).toContainText('Disposable deletion browser fixture');
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('permanent-delete-confirmation.png'), fullPage:true});
    await dialog.getByRole('button', {name:'Cancel', exact:true}).click();
    await expect(dialog).not.toBeVisible();
    expect(await page.evaluate(async id => (await api('/api/notebook/notes/' + id)).note.id, saved.id)).toBe(saved.id);
    await full.getByRole('button', {name:'Edit', exact:true}).click();
    await dialog.getByRole('button', {name:'Delete', exact:true}).click();
    const deletion = page.waitForResponse(response => response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/notebook/notes/' + saved.id + '/delete');
    await dialog.getByRole('button', {name:'Delete', exact:true}).click();
    const response = await deletion;
    expect(response.request().postDataJSON()).toMatchObject({permanent:true,
      revision:saved.revision, content_sha256:saved.content_sha256});
    expect(await response.json()).toMatchObject({deleted:true, permanent:true});
    await expect(dialog).not.toBeVisible();
    await expect(page.locator('#nb-note-list')).not.toContainText('Disposable deletion browser fixture');
    const options = await page.locator('[name="visibility"] option').allTextContents();
    expect(options).toContain('Human-written');
    expect(options).not.toContain('Trash');
    expect(options).not.toContain('Literature');
  });
}

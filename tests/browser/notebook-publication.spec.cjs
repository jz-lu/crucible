const {test, expect, signIn} = require('./helpers.cjs');

for (const [width, actor] of [[1440, 'bulkdesktop'], [390, 'bulkmobile']]) {
  test(`Publish all confirms exact revisions and renders compact receipts at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, actor);
    const title = 'A'.repeat(180);
    await page.evaluate(async title => {
      await api('/api/notebook/notes', {idempotency_key:crypto.randomUUID(), content:{title,
        claim:'Synthetic bulk-publication summary.', conditions:'Disposable browser test only.',
        body:'This body should not be fetched by bulk publication.', tags:['fixture']}});
    }, title);
    const posts = [], bodies = [], receipts = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (/\/api\/notebook\/notes\/[^/]+$/.test(url.pathname) && request.method() === 'GET') bodies.push(request.url());
      if (url.pathname.endsWith('/publication-requests')) posts.push({url, body:request.postDataJSON()});
    });
    page.on('response', response => {
      if (new URL(response.url()).pathname.endsWith('/publication-requests')) receipts.push(response.json());
    });
    const dialog = page.locator('#notebook-workspace dialog');
    const publish = page.locator('[data-nb="publish-all"]');
    await publish.click();
    await expect(dialog.getByRole('button', {name:'Send 2 publish requests', exact:true})).toBeEnabled();
    await expect(dialog.locator('.nb-publication-batch li')).toHaveCount(2);
    await expect(dialog.locator('.nb-publication-batch')).toContainText(title);
    expect(posts).toEqual([]); expect(bodies).toEqual([]);
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await dialog.locator('.nb-publication-batch').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('publish-all-confirmation.png'), fullPage:true});
    await dialog.getByRole('button', {name:'Cancel', exact:true}).click();
    await expect(dialog).not.toBeVisible(); expect(posts).toEqual([]);
    await expect(publish).toBeFocused();
    await publish.click();
    await dialog.getByRole('button', {name:'Send 2 publish requests', exact:true}).click();
    await expect(dialog.locator('.nb-batch-status')).toHaveText('2 confirmed · 0 skipped');
    expect(posts).toHaveLength(2); expect(bodies).toEqual([]);
    for (const entry of posts) {
      expect(entry.url.searchParams.get('summary')).toBe('true');
      expect(entry.body.revision).toBe(1);
      expect(entry.body.content_sha256).toMatch(/^[0-9a-f]{64}$/);
    }
    for (const data of await Promise.all(receipts)) {
      expect(data.format).toBe('crucible-publication-request-v1');
      expect(data.note).toBeUndefined();
      expect(data.publication_request.status).toBe('submitted');
    }
    await page.screenshot({path:testInfo.outputPath('publish-all-confirmed.png'), fullPage:true});
    await dialog.getByRole('button', {name:'Close', exact:true}).click();
    await publish.click();
    await expect(dialog.locator('.nb-batch-status')).toHaveText('No eligible private notes to publish.');
    expect(posts).toHaveLength(2);
  });
}

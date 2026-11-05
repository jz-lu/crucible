const {test, expect, signIn} = require('./helpers.cjs');

for (const [width, actor, reviewReader] of [[1440, 'alice', false], [390, 'queuereviewer', true]]) {
  test(`Author cancels a pending publication and deletes its private content at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, actor);
    const saved = await page.evaluate(async width => {
      const note = (await api('/api/notebook/notes', {idempotency_key:crypto.randomUUID(),
        content:{title:'Synthetic cancellation fixture ' + width, claim:'Local lifecycle check.',
          conditions:'Disposable browser data.', body:'Private cancellation sentinel.', tags:['fixture']}})).note;
      await api('/api/notebook/notes/' + note.id + '/publication-requests', {
        revision:note.revision, content_sha256:note.content_sha256, idempotency_key:crypto.randomUUID()});
      return note;
    }, width);
    await page.goto('/#notebook?filter=mine&q=' + encodeURIComponent('Synthetic cancellation fixture ' + width));
    const row = page.locator('#notebook-workspace .nb-entry').filter({hasText:'Synthetic cancellation fixture ' + width});
    await row.locator('.nb-expand').click();
    const cancel = row.getByRole('button', {name:'Cancel publish request', exact:true});
    await expect(cancel).toBeVisible(); await cancel.click();
    const dialog = page.locator('#notebook-workspace dialog[data-nb="dialog"]');
    await expect(dialog).toContainText('This revision stays private');
    await dialog.getByRole('button', {name:'Keep request', exact:true}).click();
    const prefix = reviewReader ? '/#reviews?section=flagged&note=' : '/#notebook?note=';
    await page.goto(prefix + saved.id + '&revision=1');
    const workspace = page.locator(reviewReader ? '#review-notebook-workspace' : '#notebook-workspace');
    const reader = workspace.locator('[data-nb="full"]');
    const readerCancel = reader.getByRole('button', {name:'Cancel publish request', exact:true});
    await expect(readerCancel).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await readerCancel.click();
    const confirmation = workspace.locator('dialog[data-nb="dialog"]');
    const response = page.waitForResponse(value => value.request().method() === 'POST' &&
      new URL(value.url()).pathname === `/api/notebook/notes/${saved.id}/publication-cancellations`);
    await confirmation.getByRole('button', {name:'Cancel publish request', exact:true}).click();
    expect((await (await response).json()).publication).toMatchObject({note_id:saved.id, revision:1,
      content_sha256:saved.content_sha256, status:'cancelled'});
    await expect(confirmation).not.toBeVisible();
    await expect(reader).toContainText('Publish request cancelled');
    await expect(reader.getByRole('button', {name:'Cancel publish request', exact:true})).toHaveCount(0);
    await page.screenshot({path:testInfo.outputPath('cancelled-publication.png'), fullPage:true});
    await page.goto('/#notebook?note=' + saved.id);
    await page.locator('#notebook-workspace [data-nb="full"]').getByRole('button', {name:'Edit', exact:true}).click();
    await dialog.getByRole('button', {name:'Delete', exact:true}).click();
    await expect(dialog).toContainText('Minimal audit and usage records remain');
    await dialog.getByRole('button', {name:'Delete', exact:true}).click();
    await expect(dialog).not.toBeVisible();
    expect(await page.evaluate(async id => (await fetch('/api/notebook/notes/' + id)).status, saved.id)).toBe(410);
  });
}

for (const width of [1440, 390]) {
  test(`Reviewer screening findings are collapsed and inert at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, 'queuereviewer');
    const saved = await page.evaluate(async () => (await api('/api/notebook/review-queue?summary=true')).notes[0]);
    // Only synthetic screening evidence is overlaid on the real authorized
    // fixture read. No worker or model runs; API scoping has separate tests.
    await page.route('**/api/notebook/notes/' + saved.id + '*', async route => {
      const response = await route.fetch(), data = await response.json();
      const revision = data.note.revisions.find(item => item.revision === saved.revision);
      revision.publication.screening = {revision:revision.revision, content_sha256:revision.content_sha256,
        status:'human_review', message:'Awaiting human publication review. Automated screening has not approved this request.',
        reviewer:{reason:null,outcome:'human_review_needed',concerns:[{category:'privacy_or_credentials',
          field_label:'Body',field_id:'f00003',line_start:1,line_end:1,
          explanation:'<img src=x onerror=alert(1)> is inert synthetic text. The model suggested checking disclosure context.',
          next_step:'Ask whether the example is intentionally public.'}],
          limitations:[{field_label:'Additional metadata',field_id:'f00007',reason:'The model could not interpret this synthetic field.'}]}};
      await route.fulfill({response,json:data});
    });
    await page.goto('/#reviews?section=publication&note=' + saved.id + '&revision=' + saved.revision);
    const panel = page.locator('.nb-screening'), disclosure = panel.locator('summary');
    await expect(disclosure).toHaveText('Publication screening');
    await expect(panel.locator('.nb-screening-content')).not.toBeVisible();
    await disclosure.scrollIntoViewIfNeeded();
    await page.screenshot({path:testInfo.outputPath('screening-review-collapsed.png')});
    await disclosure.click();
    await expect(panel).toContainText('Submitted revision ' + saved.revision);
    await expect(panel).toContainText(saved.content_sha256);
    await expect(panel).toContainText('may be mistaken');
    await expect(panel).toContainText('<img src=x onerror=alert(1)>');
    await expect(panel.locator('img,a,script,button')).toHaveCount(0);
    await expect(page.locator('[name="reason"]')).toHaveValue('');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({path:testInfo.outputPath('screening-review-expanded.png')});
  });
}

for (const [width, actor, mode] of [[1440, 'alice', 'automatic'], [390, 'bob', 'shadow']]) {
  test(`Screening notice binds explicit consent at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, actor);
    const saved = await page.evaluate(async () => (await api('/api/notebook/notes', {
      idempotency_key:crypto.randomUUID(), content:{title:'Synthetic screening consent fixture',
        claim:'Browser confirmation only.', conditions:'Disposable local data.', body:'No model call occurs.', tags:['fixture']}})).note);
    const policy = {mode, screening_required:true, policy_id:'a'.repeat(64), processor:'OpenAI', model:'gpt-6-luna',
      notice:'This exact revision will be sent to OpenAI for a publication-safety check. Private drafts are not sent. API data is not used for training by default; standard monitoring retention can be up to 30 days, with exceptions.',
      privacy_url:'https://developers.openai.com/api/docs/guides/your-data'};
    // The server remains disabled and never runs a provider. Overlay only the
    // synthetic notice to exercise the real browser form; backend worker tests
    // separately verify enabled consent, dispatch and publication transitions.
    await page.route('**/api/notebook/notes/*', async route => {
      if (route.request().method() !== 'GET' || !/\/api\/notebook\/notes\/[^/]+$/.test(new URL(route.request().url()).pathname)) return route.fallback();
      const response = await route.fetch(), data = await response.json();
      if (data.note?.id === saved.id) data.note.publication_policy = policy;
      await route.fulfill({response, json:data});
    });
    await page.goto('/#notebook?note=' + saved.id);
    await page.locator('[data-nb="full"]').getByRole('button', {name:'Publish', exact:true}).click();
    const dialog = page.locator('#notebook-workspace dialog[data-nb="dialog"]');
    await expect(dialog).toContainText('sent to OpenAI');
    await expect(dialog).toContainText('up to 30 days');
    await expect(dialog).toContainText('Sending the request confirms this processing');
    await expect(dialog).toContainText(mode === 'automatic' ? 'after a passing automated check' : 'evaluation only');
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('screening-consent.png'), fullPage:true});
    const received = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === `/api/notebook/notes/${saved.id}/publication-requests`);
    await dialog.getByRole('button', {name:'Send publish request', exact:true}).click();
    const body = (await received).postDataJSON();
    expect(body).toMatchObject({revision:saved.revision, content_sha256:saved.content_sha256,
      screening_consent:true, screening_policy_id:policy.policy_id});
    await expect(dialog).not.toBeVisible();
    await expect(page.locator('[data-nb="full"]')).toContainText('Publish request pending');
  });
}

for (const [width, actor] of [[1440, 'bulkdesktop'], [390, 'bulkmobile']]) {
  test(`Publish all confirms exact revisions and renders compact receipts at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, actor);
    // Map preparation is now explicit. Visit it once so the publication flow
    // still exercises a retained map dialog beside its own notebook dialog.
    await page.locator('#nav-challenge').click();
    await page.locator('#scheme-map-button').click();
    await expect(page.locator('#landscape svg')).toBeVisible();
    await page.locator('#nav-notebook').click();
    const dialog = page.locator('#notebook-workspace dialog[data-nb="dialog"]');
    const individual = page.locator('.nb-card-actions').getByRole('button', {name:'Publish', exact:true}).first();
    await individual.click();
    await expect(dialog).toHaveAttribute('aria-label', 'Request publication');
    await expect(dialog.locator('.nb-publication-note h3')).toContainText('browser-only private note');
    await expect(dialog.getByRole('button', {name:'Send publish request', exact:true})).toBeEnabled();
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('publish-one-confirmation.png'), fullPage:true});
    await dialog.getByRole('button', {name:'Cancel', exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(individual).toBeFocused();
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
    // The earlier map visit leaves its shadow-root dialog mounted and hidden.
    // Target only this flow's notebook dialog when confirming publication.
    const mapDialog = page.locator('#notebook-workspace #mobile-details');
    await expect(mapDialog).toBeAttached();
    await expect(mapDialog).not.toBeVisible();
    await expect(page.locator('#notebook-workspace dialog')).toHaveCount(2);
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

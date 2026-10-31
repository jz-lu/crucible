const {test, expect, signIn} = require('./helpers.cjs');

for (const width of [1440,390]) test(`source notes can be searched and selected as removable ID tags at ${width}px`, async ({page},testInfo) => {
  await page.setViewportSize({width,height:width === 390 ? 844 : 1000});
  await signIn(page,'numbers');
  const notes = await page.evaluate(async width => {
    const results = [];
    for (const name of ['Alpha','Beta']) {
      const result = await api('/api/notebook/notes',{idempotency_key:crypto.randomUUID(),content:{
        title:`Picker ${width} ${name}`, claim:'Synthetic source-picker verification.',
        conditions:'Disposable local browser fixture.',body:'This is test data, not a research claim.',tags:['fixture']}});
      results.push({id:result.note.id,number:result.note.number,revision:result.note.revision,content_sha256:result.note.content_sha256,name});
    }
    return results;
  },width);
  const label = note => '#' + String(note.number).padStart(4,'0');
  const panel = () => page.locator('[data-nb="bounties"]:visible');
  await page.locator('#nav-bounties').click();
  await expect(panel().getByRole('heading',{name:'Bounties',exact:true,level:1})).toBeVisible();
  await panel().getByRole('button',{name:'Propose a bounty',exact:true}).click();
  for (const [name,value] of [['The task',`Source picker ${width}`],['A short description','A disposable source-picker test.'],
    ['What counts as completion?','Verify the selected exact source pins.'],['How will it be verified?','Inspect this disposable browser receipt.']]) {
    await panel().getByLabel(name,{exact:true}).fill(value);
  }
  const search = panel().getByRole('combobox',{name:'Source-note citations'});
  const options = () => panel().getByRole('listbox',{name:'Source notes'}).getByRole('option');
  await search.click();
  await expect(search).toHaveAttribute('aria-expanded','true');
  await expect(panel().getByRole('listbox',{name:'Source notes'})).toBeVisible();
  await expect(options().first()).toBeVisible();
  await page.screenshot({path:testInfo.outputPath(`source-dropdown-${width}.png`),fullPage:true});
  await search.fill(`Picker ${width} Alpha`);
  const alpha = panel().getByRole('option',{name:new RegExp(`Picker ${width} Alpha`)});
  await expect(alpha).toBeVisible(); await expect(options()).toHaveCount(1);
  await alpha.click();
  const tags = () => panel().getByRole('list',{name:'Selected source notes'});
  await expect(tags()).toContainText(label(notes[0]));
  await search.fill(`Picker ${width} Beta`);
  await expect(panel().getByRole('option',{name:new RegExp(`Picker ${width} Beta`)})).toBeVisible();
  await search.press('ArrowDown'); await search.press('Enter');
  await expect(tags().getByRole('listitem')).toHaveCount(2);
  await expect(tags()).toContainText(label(notes[1]));
  await panel().getByRole('button',{name:`Remove source ${label(notes[0])}, revision ${notes[0].revision}`,exact:true}).click();
  await expect(tags().getByRole('listitem')).toHaveCount(1);
  await search.fill(`Picker ${width} Alpha`); await alpha.click();
  await expect(tags().getByRole('listitem')).toHaveCount(2);
  await search.press('Escape');
  await expect(search).toHaveAttribute('aria-expanded','false');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`source-tags-${width}.png`),fullPage:true});

  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/bounties' && r.request().method() === 'POST');
  await panel().getByRole('button',{name:'Save private draft',exact:true}).click();
  const saved = await response; expect(saved.status()).toBe(200);
  const receipt = await saved.json(), expected = notes.map(({id,revision,content_sha256}) => ({note_id:id,revision,content_sha256}));
  expect(receipt.bounty.terms.source_notes.sort((a,b) => a.note_id.localeCompare(b.note_id)))
    .toEqual(expected.sort((a,b) => a.note_id.localeCompare(b.note_id)));
  await panel().getByRole('button',{name:'← Your activity',exact:true}).click();
  await expect(panel().getByRole('heading',{name:'Bounties',exact:true,level:1})).toBeVisible();
  await expect(panel().getByRole('heading',{name:'Your activity',exact:true})).toHaveCount(0);
  await panel().locator(`[data-bounty="${receipt.bounty.id}"]`).click();
  await panel().getByRole('button',{name:'Edit draft',exact:true}).click();
  await expect(tags().getByRole('listitem')).toHaveCount(2);
  await expect(tags()).toContainText(label(notes[0])); await expect(tags()).toContainText(label(notes[1]));
});

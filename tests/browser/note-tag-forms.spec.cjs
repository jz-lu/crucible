const {test,expect,signIn,useDataSaver}=require('./helpers.cjs');

for(const width of [1440,390])test(`form placeholders and searchable note tags work at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);
  await signIn(page,width===1440?'formsdesktop':'formsmobile');
  await page.evaluate(async()=>api('/api/notebook/notes',{
    idempotency_key:crypto.randomUUID(),content:{title:'Tag discovery fixture',claim:'Synthetic tag vocabulary.',
      conditions:'Disposable test account only.',body:'No research claim.',tags:['autocomplete-lattice','autocomplete-lattice-attacks','comma, subject']}
  }));
  await page.locator('[data-nb="new"]').click();
  const dialog=page.locator('#notebook-workspace dialog[data-nb="dialog"]');
  for(const name of ['title','claim','conditions','body','kind']){
    await expect(dialog.locator('[name="'+name+'"]')).toHaveAttribute('placeholder',/.+/);
    await expect(dialog.locator('[name="'+name+'"]')).toHaveValue('');
  }
  const input=dialog.getByRole('combobox',{name:'Tags',exact:true});
  await expect(input).toHaveAttribute('placeholder',/.+/);
  await expect(dialog.locator('.nb-required-marker:visible')).toHaveCount(4);
  await expect(dialog.getByRole('checkbox',{name:'Scheme proposal',exact:true})).not.toBeChecked();
  await expect(dialog.locator('.nb-specification')).toBeHidden();
  await expect(dialog).not.toContainText('Enter chooses the first');
  await expect(dialog).not.toContainText('Optional, self-reported label; not verified');
  const bodyBox=await dialog.locator('[name="body"]').boundingBox(),evidenceBox=await dialog.locator('[name="kind"]').boundingBox(),tagsBox=await input.boundingBox();
  expect(Math.abs(bodyBox.width-evidenceBox.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(bodyBox.width-tagsBox.width)).toBeLessThanOrEqual(1);
  expect(evidenceBox.y).toBeGreaterThan(bodyBox.y+bodyBox.height);
  expect(tagsBox.y).toBeGreaterThan(evidenceBox.y+evidenceBox.height);
  const posted=[];page.on('request',request=>{
    if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/notebook/notes')posted.push(request.postDataJSON());
  });
  await input.fill('autocomplete-lat');
  await expect(dialog.getByRole('listbox',{name:'Matching tags'}).getByRole('option')).toHaveText(['autocomplete-lattice','autocomplete-lattice-attacks']);
  await input.press('ArrowDown');await input.press('Enter');
  await expect(dialog.getByRole('button',{name:'Remove tag autocomplete-lattice-attacks',exact:true})).toBeVisible();
  await input.fill('autocomplete-lat');await input.press('Enter');
  await expect(dialog.getByRole('button',{name:'Remove tag autocomplete-lattice',exact:true})).toBeVisible();
  await input.fill('new-subject-'+width);await input.press('Enter');
  await expect(dialog.getByRole('button',{name:'Remove tag new-subject-'+width,exact:true})).toBeVisible();
  await input.fill('comma, subject');
  await dialog.getByRole('option',{name:'comma, subject',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'Remove tag comma, subject',exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Remove tag autocomplete-lattice-attacks',exact:true}).click();
  await dialog.getByLabel('Human-written',{exact:true}).check();
  await expect(dialog.getByRole('button',{name:'Remove tag human-written',exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Remove tag human-written',exact:true}).click();
  await expect(dialog.getByLabel('Human-written',{exact:true})).not.toBeChecked();
  expect(posted).toEqual([]);await expect(dialog).toBeVisible();
  await input.fill('autocomplete');await expect(dialog.getByRole('listbox',{name:'Matching tags'}).getByRole('option').first()).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('note-tag-suggestions-'+width+'.png'),fullPage:true});
  await input.press('Escape');await expect(input).toHaveAttribute('aria-expanded','false');await expect(dialog).toBeVisible();
  await input.fill('');
  await dialog.locator('[name="title"]').fill('Saved tag picker fixture '+width);
  await dialog.locator('[name="claim"]').fill('A synthetic save check.');
  await dialog.locator('[name="conditions"]').fill('Disposable fixture only.');
  await dialog.locator('[name="body"]').fill('Preserve selected tags without splitting comma-containing names.');
  // Saving also commits unfinished tag input against the current suggestions.
  await input.fill('last-tag-'+width);
  await dialog.getByRole('button',{name:'Save private note',exact:true}).click();
  await expect(dialog).toBeHidden();expect(posted).toHaveLength(1);
  expect(posted[0].content.tags).toEqual(['autocomplete-lattice','new-subject-'+width,'comma, subject','last-tag-'+width]);
  await page.locator('[data-nb="full"]').getByRole('button',{name:'Edit',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'Remove tag comma, subject',exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.locator('#nav-challenge').click();await page.locator('#propose-scheme-button').click();
  await page.getByRole('link',{name:'Write a proposal note →',exact:true}).click();
  await expect(dialog).toHaveAttribute('aria-label','New proposal note');
  await expect(dialog.locator('.nb-required-marker:visible')).toHaveCount(5);
  await expect(dialog.getByRole('checkbox',{name:'Scheme proposal',exact:true})).toHaveCount(0);
  await expect(dialog.locator('select[name="note-type"]')).toHaveCount(0);
  await expect(dialog.locator('.nb-specification')).toBeVisible();
  await expect(dialog.locator('[name="title"]')).toHaveAttribute('placeholder',/scheme/);
  for(const name of ['title','claim','conditions','body'])await expect(dialog.locator('[name="'+name+'"]')).toHaveAttribute('placeholder',/.+/);
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.locator('#nav-bounties').click();
  const bounties=page.locator('[data-nb="bounties"]');await bounties.getByRole('button',{name:'Propose a bounty',exact:true}).click();
  await expect(bounties.locator('.bty-form .nb-required-marker')).toHaveCount(5);
  for(const name of ['bounty-title','bounty-summary','bounty-completion','bounty-verification','bounty-tags']){
    await expect(bounties.locator('[name="'+name+'"]')).toHaveAttribute('placeholder',/.+/);
    await expect(bounties.locator('[name="'+name+'"]')).toHaveValue('');
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:testInfo.outputPath('bounty-placeholders-'+width+'.png'),fullPage:true});
});

const {test,expect,signIn,useDataSaver}=require('./helpers.cjs');

for(const width of [1440,390])test(`note type survives draft saves and can change without losing text at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);
  await signIn(page,width===1440?'formsdesktop':'formsmobile');
  await page.locator('[data-nb="new"]').click();
  const dialog=page.locator('#notebook-workspace dialog[data-nb="dialog"]'),type=dialog.getByLabel('Note type',{exact:true});
  await expect(type).toHaveValue('research-note');await expect(dialog.locator('.nb-specification')).toBeHidden();
  const title='A proposal draft chosen from New note '+width;
  await dialog.locator('[name="title"]').fill(title);
  await dialog.locator('[name="claim"]').fill('Synthetic draft for testing note type.');
  await dialog.locator('[name="conditions"]').fill('Disposable browser data only.');
  await dialog.locator('[name="body"]').fill('No proposed construction or research claim.');
  await type.selectOption('scheme-proposal');
  await expect(dialog.locator('[name="title"]')).toHaveValue(title);
  await expect(dialog.locator('.nb-specification')).toBeVisible();
  await expect(dialog.locator('.nb-specification summary')).toContainText('KEM specification *');
  await expect(dialog.locator('.nb-specification')).toContainText('You can save a draft before attaching it.');
  const submissions=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.endsWith('/proposal-submissions'))submissions.push(request.url());});
  await page.screenshot({path:testInfo.outputPath('scheme-note-form-'+width+'.png'),fullPage:true});
  await dialog.getByRole('button',{name:'Save proposal draft',exact:true}).click();
  await expect(dialog).toBeHidden();const reader=page.locator('[data-nb="full"]');
  await expect(reader.locator('.nb-full-title')).toHaveText(title);
  const saved=await page.evaluate(async()=>{
    const id=new URLSearchParams(location.hash.split('?')[1]).get('note');return(await api('/api/notebook/notes/'+id)).note;
  });
  expect(saved.revisions.at(-1).content.extensions.note_type).toBe('scheme-proposal');
  expect(saved.revisions.at(-1).content.extensions.kem_specification).toBeUndefined();
  expect(submissions).toEqual([]);
  await reader.getByRole('button',{name:'Edit',exact:true}).click();
  await expect(type).toHaveValue('scheme-proposal');
  await type.selectOption('research-note');await expect(dialog.locator('.nb-specification')).toBeHidden();
  await expect(dialog.locator('[name="body"]')).toHaveValue('No proposed construction or research claim.');
  await dialog.getByRole('button',{name:'Save new revision',exact:true}).click();await expect(dialog).toBeHidden();
  await reader.getByRole('button',{name:'Edit',exact:true}).click();
  await expect(type).toHaveValue('research-note');await expect(dialog.locator('[name="title"]')).toHaveValue(title);
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
});

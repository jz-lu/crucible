const {test,expect,signIn} = require('./helpers.cjs');
for (const width of [1440,390]) test(`note guidance works with title and prose only at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:1000});await signIn(page);
  const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
  await page.goto('/#home?guide=tour');await page.locator('[data-contribution="note"]').click();
  await page.locator('#participation-guide').getByRole('button',{name:'Open the editor',exact:true}).click();
  const dialog=page.locator('#notebook-workspace dialog[data-nb="dialog"]');
  await dialog.locator('[name="title"]').fill('An unsaved guide check');
  await dialog.locator('[name="body"]').fill('An ordinary note needs a title and a human explanation.');
  await expect(page.locator('#live-guide-title')).toHaveText('Save your private draft.');
  await expect(dialog.locator('[name="claim"]')).toHaveCount(0);
  await expect(dialog.locator('[name="conditions"]')).toHaveCount(0);
  expect(writes).toEqual([]);
});

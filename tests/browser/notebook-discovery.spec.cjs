const {test,expect,signIn,useDataSaver}=require('./helpers.cjs');

for (const width of [1440,390]) test(`note votes and publication age fit beside titles at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);
  await signIn(page,width===1440?'discoverydesktop':'discoverymobile');
  const saved=await page.evaluate(async()=> (await api('/api/notebook/notes',{
    idempotency_key:crypto.randomUUID(),content:{title:'A long research note title that wraps naturally alongside vote totals and publication age',
      claim:'Display-only browser fixture.',conditions:'Synthetic layout evidence.',body:'No research claims.',tags:['fixture']}
  })).note);
  const timestamp=Math.floor(Date.now()/1000)-3660;
  // The real authorized note body is unchanged. Public metadata is a layout
  // fixture; backend discovery tests cover actual votes and publication rules.
  await page.route('**/api/notebook/notes**',async route=>{
    if(route.request().method()!=='GET')return route.fallback();
    const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
    const data=await response.json();
    for(const note of data.notes || (data.note?[data.note]:[])) if(note.id===saved.id){
      note.published_at=timestamp;note.rating={score:4,raters:3,upvotes:2,double_upvotes:1};
    }
    await route.fulfill({response,json:data});
  });
  await page.locator('.brand').first().click();await page.locator('#nav-notebook').click();
  const row=page.locator('.nb-entry').filter({has:page.getByRole('button',{name:new RegExp('A long research note title')})});
  await expect(row.locator('.nb-note-votes')).toHaveText('▲4');
  await expect(row.locator('time')).toHaveText('1 hr ago');
  await expect(row.locator('time')).toHaveAttribute('datetime',new Date(timestamp*1000).toISOString());
  await expect(row.locator('time')).toHaveAttribute('title',/^Published /);
  const bounds=await row.evaluate(element=>{
    const title=element.querySelector('h2').getBoundingClientRect(),aside=element.querySelector('.nb-entry-aside').getBoundingClientRect();
    return {titleRight:title.right,asideLeft:aside.left,right:aside.right,rowRight:element.getBoundingClientRect().right};
  });
  expect(bounds.titleRight).toBeLessThanOrEqual(bounds.asideLeft);expect(bounds.right).toBeLessThanOrEqual(bounds.rowRight);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await row.locator('.nb-title').click();
  await page.screenshot({path:testInfo.outputPath('note-votes-'+width+'.png'),fullPage:true});
  await row.getByRole('button',{name:'Read full note →'}).click();
  await expect(page.locator('[data-nb="full"] .nb-note-votes')).toHaveText('▲4');
  await expect(page.locator('[data-nb="full"] time')).toHaveAttribute('title',/^Published /);
});

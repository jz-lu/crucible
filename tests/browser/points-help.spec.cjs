const {test, expect, signIn, useDataSaver, holdResponse} = require('./helpers.cjs');
async function geometry(locator) {
  return locator.evaluate(element => {
    const {x,y,width,height} = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {x,y,width,height,font:style.font};
  });
}
for (const width of [1440,390]) {
  test(`Leaderboard explains points without spending or moving navigation at ${width}px`, async ({page},testInfo) => {
    await page.setViewportSize({width,height:900}); await useDataSaver(page); await signIn(page);
    await page.locator('#nav-people').click(); await expect(page.locator('#people-content')).toContainText('browser_alice');
    await page.evaluate(() => document.fonts.ready);
    const trigger = page.getByRole('button',{name:'How points work',exact:true});
    const title = await page.locator('#people-title').boundingBox(), button = await trigger.boundingBox();
    expect(button.x).toBeGreaterThan(title.x + title.width);
    expect(Math.abs(button.y + button.height / 2 - title.y - title.height / 2)).toBeLessThan(1);
    const nav = await geometry(page.locator('.header-inner')), writes = [];
    page.on('request',request => { if (request.method() !== 'GET') writes.push(request.url()); });
    await trigger.focus(); await trigger.press('Enter');
    const dialog = page.getByRole('dialog',{name:'How points work',exact:true});
    await expect(dialog).toBeVisible(); await expect(dialog.getByRole('button',{name:'Close points explanation'})).toBeFocused();
    for (const name of ['Record breakthroughs','Admitted schemes','Research bounties','Research notes','Bounty allowance']) await expect(dialog.getByRole('heading',{name,exact:true})).toHaveCount(1);
    await expect(dialog).toContainText('2,000 points'); await expect(dialog).toContainText('+100 points per completed active week');
    await expect(dialog).toContainText('review approval alone earns 0 points');
    expect(await geometry(page.locator('.header-inner'))).toEqual(nav);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath(`points-help-${width}.png`)});
    await dialog.evaluate(element => {element.scrollTop = element.scrollHeight;});
    await expect(dialog.getByRole('heading',{name:'Bounty allowance',exact:true})).toBeVisible();
    await expect(dialog.getByRole('button',{name:'Close points explanation'})).toBeVisible();
    await page.screenshot({path:testInfo.outputPath(`points-help-bottom-${width}.png`)});
    await page.keyboard.press('Escape'); await expect(dialog).toBeHidden(); await expect(trigger).toBeFocused();
    expect(writes).toEqual([]);
    await trigger.click(); await page.evaluate(() => {location.hash='#about';});
    await expect(dialog).toBeHidden(); await expect(page.locator('#about-title')).toBeVisible();
    // Native dialog.close() hides the dialog before its queued close event
    // releases the shared scroll lock. Observe that event's completed effect.
    await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test(`the points explanation is available during a held Leaderboard read at ${width}px`, async ({page}) => {
    await page.setViewportSize({width,height:900}); await useDataSaver(page); await signIn(page);
    await page.goto('about:blank');
    const session = await holdResponse(page,url => url.pathname === '/api/session');
    const people = await holdResponse(page,url => url.pathname === '/api/people');
    try {
      await page.goto('/?points-guide-frame='+width+'#people'); await session.arrived;
      const pendingTitle = await geometry(page.locator('#access-title'));
      const pendingButton = await geometry(page.locator('.pending-points-help'));
      session.release(); await session.delivered; await people.arrived;
      const trigger = page.locator('#points-help-button'); await expect(trigger).toBeVisible();
      expect(await geometry(page.locator('#people-title'))).toEqual(pendingTitle);
      expect(await geometry(trigger)).toEqual(pendingButton);
      await trigger.click(); await expect(page.locator('#points-help-dialog')).toBeVisible();
      people.release(); await people.delivered; await expect(page.locator('#points-help-dialog')).toBeVisible();
      await page.keyboard.press('Escape'); await expect(trigger).toBeFocused();
    } finally {
      session.release(); people.release(); await session.delivered; await people.delivered;
      await session.remove(); await people.remove();
    }
  });
}

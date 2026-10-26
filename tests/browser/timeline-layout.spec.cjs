const {test, expect, signIn, notebookReady, holdResponse} = require('./helpers.cjs');

for (const width of [1440,390]) {
  test('cold Timeline keeps its ' + width + 'px frame while the real aggregate loads beside notebook assets', async ({page},testInfo) => {
    await page.setViewportSize({width,height:1000});
    const reads=[];page.on('request',request=>{const url=new URL(request.url());reads.push({path:url.pathname,projection:url.searchParams.get('projection')});});
    // Admission can immediately warm both resources. Establish the cold
    // response barriers before login, not after its UI assertions complete.
    const aggregate=await holdResponse(page,url=>url.pathname==='/api/notebook/timeline');
    const asset=await holdResponse(page,url=>url.pathname==='/notebook/notebook.js');
    const target=page.locator('#notebook-workspace [data-nb="timeline"]');
    const geometry=()=>target.evaluate(root=>{
      const rect=element=>{const box=element.getBoundingClientRect();return{x:box.x,y:box.y,width:box.width,height:box.height};};
      return {card:rect(root.querySelector('.nb-timeline-card')),heading:rect(root.querySelector('.nb-timeline-heading')),
        title:rect(root.querySelector('h2')),plot:rect(root.querySelector('.nb-timeline-plot')),
        overflow:document.documentElement.scrollWidth>innerWidth};
    });
    let before,card;
    try {
      await page.goto('/#about'); await signIn(page,'alice',{navigate:false});
      await page.evaluate(()=>{location.hash='#notebook?view=timeline';});
      await asset.arrived;await aggregate.arrived;
      await expect(page.locator('#notebook-workspace .notebook-frame .nb-views')).toBeVisible();
      await expect(page.locator('#notebook-workspace .notebook-frame [data-notebook-panel="timeline"]')).toBeVisible();
      await expect(page.locator('#notebook-workspace')).toContainText('Research notebook');
      expect(reads.filter(read=>read.path==='/api/notebook/timeline')).toHaveLength(1);
      expect(reads.filter(read=>read.path==='/api/notebook/notes')).toEqual([]);
      asset.release();await asset.delivered;
      await expect(target.locator('.nb-timeline-frame')).toBeVisible();
      await expect(target.locator('.nb-timeline-frame')).toHaveAttribute('aria-hidden','true');
      await expect(target.locator('svg')).toHaveCount(0);
      await expect(target.locator('.nb-timeline-total')).toBeEmpty();
      expect(await target.locator('[role=status]').evaluate(element=>getComputedStyle(element).clipPath)).toBe('inset(50%)');
      before=await geometry();card=await target.locator('.nb-timeline-card').elementHandle();
      expect(before.plot.height).toBe(width<=650?280:340);expect(before.overflow).toBe(false);
      await page.screenshot({path:testInfo.outputPath('timeline-cold-'+width+'.png')});
      // User focus elsewhere must survive replacement of only the plot contents.
      await page.locator('#notebook-workspace [data-nb="notes-view"]').focus();
    } finally {
      asset.release();aggregate.release();await asset.delivered;await aggregate.delivered;
      await asset.remove();await aggregate.remove();
    }
    await expect(target.locator('svg')).toBeVisible();
    await expect(target).not.toHaveAttribute('aria-busy','true');
    await expect(target.locator('.nb-timeline-total')).toHaveText(/\d+ notes?/);
    expect(await card.evaluate(element=>element.isConnected)).toBe(true);
    const after=await geometry();
    for(const part of ['card','heading','title','plot']) for(const coordinate of ['x','y','width','height']) {
      expect(Math.abs(after[part][coordinate]-before[part][coordinate]),part+' '+coordinate).toBeLessThanOrEqual(1);
    }
    expect(after.overflow).toBe(false);
    await expect(page.locator('#notebook-workspace [data-nb="notes-view"]')).toBeFocused();
    expect(reads.filter(read=>read.path==='/api/notebook/timeline')).toHaveLength(1);
    // The optional queue may proceed to its compact index after the aggregate
    // is released. Timeline itself never loads a note page or a note body.
    expect(reads.filter(read=>read.path==='/api/notebook/notes'&&read.projection!=='index')).toEqual([]);
    expect(reads.filter(read=>read.path.startsWith('/api/notebook/notes/'))).toEqual([]);
    await page.screenshot({path:testInfo.outputPath('timeline-ready-'+width+'.png')});
  });
}

for (const width of [1440,390]) {
  test('the Timeline tab preserves the actual ' + width + 'px Notes scroll position through a held first read', async ({page}) => {
    await page.setViewportSize({width,height:1000});
    const pending=await holdResponse(page,url=>url.pathname==='/api/notebook/timeline');
    await signIn(page);await notebookReady(page);
    expect(await page.locator('#nb-note-list .nb-entry').count()).toBeGreaterThan(5);
    await page.evaluate(()=>{scrollTo(0,150);return new Promise(requestAnimationFrame);});
    const before=await page.evaluate(()=>scrollY);expect(before).toBe(150);
    const tab=page.locator('#notebook-workspace [data-nb="timeline-view"]');
    const bounds=await tab.boundingBox();expect(bounds.y).toBeGreaterThan(0);expect(bounds.y+bounds.height).toBeLessThan(1000);
    try {
      await tab.click();await pending.arrived;
      await expect(page.locator('#notebook-workspace .nb-timeline-frame')).toBeVisible();
      expect(await page.evaluate(()=>scrollY)).toBe(before);
    } finally {pending.release();await pending.delivered;await pending.remove();}
    await expect(page.locator('#notebook-workspace [data-nb="timeline"] svg')).toBeVisible();
    await expect(page.locator('#notebook-workspace [data-nb="timeline"]')).not.toHaveAttribute('aria-busy','true');
    expect(await page.evaluate(()=>scrollY)).toBe(before);
    await expect(tab).toBeFocused();
  });
}

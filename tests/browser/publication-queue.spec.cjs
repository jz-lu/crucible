const {test, expect, signIn, holdResponse, useDataSaver} = require('./helpers.cjs');

for (const width of [1440,390]) test(`publish requests prepare two pages, then only the next page at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});
  const requests=[],queuePath='/api/notebook/review-queue';
  page.on('request',request=>requests.push({url:new URL(request.url()),method:request.method()}));
  const firstResponse=page.waitForResponse(response=>new URL(response.url()).pathname===queuePath&&!new URL(response.url()).searchParams.has('after'));
  const secondResponse=page.waitForResponse(response=>new URL(response.url()).pathname===queuePath&&new URL(response.url()).searchParams.has('after'));
  await signIn(page,'queuereviewer');
  const first=await firstResponse,second=await secondResponse;
  const firstData=await first.json(),secondData=await second.json();
  expect(firstData.notes).toHaveLength(25);expect(secondData.notes).toHaveLength(25);
  for(const response of [first,second])expect(new URL(response.url()).searchParams.get('summary')).toBe('true');
  for(const note of [...firstData.notes,...secondData.notes])expect(note.revisions[0].content.body).toBeUndefined();
  const root=page.locator('#review-notebook-workspace'),list=root.locator('[data-nb="list"]');
  await expect(root).not.toBeVisible();await expect(list.locator('.nb-entry')).toHaveCount(0);
  const third=await holdResponse(page,url=>url.pathname===queuePath&&url.searchParams.get('after')===secondData.next_after);
  try{
    await page.evaluate(()=>{location.hash='#reviews?section=publication';});
    await expect(list.locator('.nb-entry')).toHaveCount(25);
    await expect(list).toContainText(firstData.notes[0].revisions[0].content.title);
    await expect(list).not.toHaveAttribute('aria-busy','true');
    expect(requests.filter(request=>request.url.pathname===queuePath)).toHaveLength(2);
    await root.getByRole('button',{name:'Next page',exact:true}).click();
    await expect(list).toContainText(secondData.notes[0].revisions[0].content.title);
    await expect(list.locator('.nb-entry')).toHaveCount(25);
    await expect(list).not.toHaveAttribute('aria-busy','true');
    await third.arrived;
    expect(requests.filter(request=>request.url.pathname===queuePath)).toHaveLength(3);
    expect(requests.filter(request=>request.url.pathname.startsWith('/api/notebook/notes/'))).toHaveLength(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('publication-queue-prepared-page.png'),fullPage:true});
    // A click while the speculative next read is pending joins the same GET.
    await root.getByRole('button',{name:'Next page',exact:true}).click();
    third.release();await third.delivered;
    await expect(list.locator('.nb-entry')).toHaveCount(2);
    await expect(list).not.toHaveAttribute('aria-busy','true');
    expect(requests.filter(request=>request.url.pathname===queuePath)).toHaveLength(3);
    expect(requests.filter(request=>request.method!=='GET').map(request=>request.url.pathname)).toEqual(['/api/auth/login']);
  }finally{third.release();await third.remove();}
});

for (const width of [1440,390]) test(`publish requests retain cached summaries and refresh exact review readers at ${width}px`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});
  await useDataSaver(page); // Deliberately exercise cold reads and ordinary refreshes.
  const requests=[];
  page.on('request',request=>requests.push({url:new URL(request.url()),method:request.method()}));
  await signIn(page,'queuereviewer');
  const firstResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/notebook/review-queue');
  await page.evaluate(()=>{location.hash='#reviews?section=publication';});
  const response=await firstResponse, data=await response.json();
  expect(response.status()).toBe(200);
  expect(new URL(response.url()).searchParams.get('summary')).toBe('true');
  const candidate=data.notes.find(note=>note.revisions[0].content.title==='Queueauthor browser-only private note');
  expect(candidate).toBeTruthy();expect(candidate.revisions[0].summary).toBe(true);
  expect(candidate.revisions[0].content.body).toBeUndefined();
  const root=page.locator('#review-notebook-workspace'),list=root.locator('[data-nb="list"]');
  const row=list.locator('.nb-entry').filter({hasText:'Queueauthor browser-only private note'});
  await expect(row).toBeVisible();await row.locator('.nb-title').click();
  await expect(row.locator('.nb-preview')).toBeVisible();
  await row.evaluate(element=>{window.queueRowForTest=element;});
  expect(requests.filter(request=>request.url.pathname==='/api/notebook/notes/'+candidate.id)).toHaveLength(0);
  const holds=[];
  const holdQueue=async()=>{
    const hold=await holdResponse(page,url=>url.pathname==='/api/notebook/review-queue'&&url.searchParams.get('summary')==='true');
    holds.push(hold);return hold;
  };
  const holdReader=async()=>{
    const hold=await holdResponse(page,url=>url.pathname==='/api/notebook/notes/'+candidate.id&&url.searchParams.get('revision')===String(candidate.revision));
    holds.push(hold);return hold;
  };
  try{
    await page.evaluate(()=>{location.hash='#about';});await expect(page.locator('#about-workspace')).toBeVisible();
    const refresh=await holdQueue();await page.evaluate(()=>{location.hash='#reviews?section=publication';});
    await refresh.arrived;
    await expect(row).toBeVisible();await expect(list).toHaveAttribute('aria-busy','true');
    expect(await row.evaluate(element=>element===window.queueRowForTest)).toBe(true);
    await expect(row.locator('.nb-preview')).toBeVisible();await expect(list.locator('.nb-reading')).toHaveCount(0);
    await row.locator('.nb-title').focus();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('publication-queue-held-refresh.png'),fullPage:true});
    refresh.release();await refresh.delivered;await refresh.remove();
    await expect(list).not.toHaveAttribute('aria-busy','true');
    expect(await row.evaluate(element=>element===window.queueRowForTest)).toBe(true);
    await expect(row.locator('.nb-title')).toBeFocused();

    for(let visit=0;visit<2;visit++){
      const reader=await holdReader();await row.getByRole('button',{name:'Read full note →',exact:true}).click();
      await reader.arrived;
      const full=root.locator('[data-nb="full"]');await expect(full).toBeVisible();
      await expect(full.locator('.nb-publication-review')).toHaveCount(0);
      await expect(full).not.toContainText('QUEUEAUTHOR_PRIVATE_BROWSER_BODY');
      expect(requests.filter(request=>request.url.pathname==='/api/notebook/notes/'+candidate.id&&request.method==='GET')).toHaveLength(visit+1);
      reader.release();await reader.delivered;await reader.remove();
      await expect(full).toContainText('QUEUEAUTHOR_PRIVATE_BROWSER_BODY');
      await expect(full.locator('.nb-publication-review')).toBeVisible();
      const returning=await holdQueue();await full.getByRole('button',{name:'← Publish requests',exact:true}).click();
      await returning.arrived;
      await expect(row).toBeVisible();await expect(list.locator('.nb-reading')).toHaveCount(0);
      expect(await row.evaluate(element=>element===window.queueRowForTest)).toBe(true);
      await expect(full).not.toBeVisible();
      returning.release();await returning.delivered;await returning.remove();
      await expect(list).not.toHaveAttribute('aria-busy','true');
    }
    expect(requests.filter(request=>request.method!=='GET').map(request=>request.url.pathname)).toEqual(['/api/auth/login']);
  }finally{
    for(const hold of holds)hold.release();
    for(const hold of holds)await hold.remove();
  }
});

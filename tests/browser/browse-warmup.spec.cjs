const {test,expect,signIn,holdResponse}=require('./helpers.cjs');
const indexPath=url=>url.pathname==='/api/notebook/notes'&&url.searchParams.get('projection')==='index';
const list=page=>page.locator('#nb-note-list');
const titles=page=>list(page).locator('.nb-title-copy > span:first-child');
const scope=page=>page.locator('#notebook-workspace [name=visibility]');

test('admitted background summaries warm without hidden rendering and give exact local category, tag and page results',async({page})=>{
  const requests=[],indexReads=[];
  page.on('request',request=>{const url=new URL(request.url());requests.push({path:url.pathname,search:url.search,method:request.method()});});
  // Only the authorized compact response is expanded into a deterministic
  // paging fixture. No account/database is changed and no fake body is served.
  await page.route(indexPath,async route=>{
    const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
    const data=await response.json();indexReads.push(data.viewer_id);
    const source=data.notes.find(note=>note.owner?.handle==='browser_alice');expect(source).toBeTruthy();
    data.notes=Array.from({length:31},(_,i)=>{
      const content={...source.revisions[0].content,title:'Warm summary '+String(i).padStart(2,'0'),
        claim:'Synthetic compact browsing claim.',tags:i%2?['warm-tag']:['human-written']};
      return {...source,id:'warm-summary-'+i,can_edit:false,can_review:false,
        revisions:[{...source.revisions[0],content}],created_at:source.created_at-i};
    });
    await route.fulfill({response,json:data});
  });
  await page.goto('/#about');await expect(page.locator('#access-sign-in')).toBeVisible();
  expect(requests.some(row=>row.path.startsWith('/api/notebook/')||row.path.startsWith('/notebook/')||row.path.startsWith('/map/'))).toBe(false);
  await signIn(page,'alice',{navigate:false});
  // Navigation starts after timeline/index have completed in the serial queue.
  await page.waitForResponse(response=>new URL(response.url()).searchParams.get('projection')==='navigation');
  expect(indexReads).toHaveLength(1);
  await expect(page.locator('#about-workspace')).toBeVisible();
  await expect(page.locator('#notebook-workspace')).toBeHidden();
  await expect(page.locator('#notebook-workspace .nb-entry')).toHaveCount(0);
  await expect(page.locator('#notebook-workspace [data-nb=timeline] .nb-timeline-card')).toHaveCount(0);
  await expect(page.locator('#notebook-workspace [data-nb=map] svg')).toHaveCount(0);
  const before=requests.length;
  await page.locator('#nav-notebook').click();
  await expect(titles(page)).toHaveCount(25);await expect(titles(page).first()).toHaveText('Warm summary 00');
  await page.getByRole('button',{name:'Next page',exact:true}).click();
  await expect(titles(page)).toHaveCount(6);await expect(titles(page).first()).toHaveText('Warm summary 25');
  await scope(page).selectOption('human');
  await expect(titles(page)).toHaveCount(16);
  expect(await titles(page).allTextContents()).toEqual(Array.from({length:16},(_,i)=>'Warm summary '+String(i*2).padStart(2,'0')));
  await page.evaluate(()=>{location.hash='#notebook?tag=warm-tag';});
  await expect(titles(page)).toHaveCount(15);
  expect(await titles(page).allTextContents()).toEqual(Array.from({length:15},(_,i)=>'Warm summary '+String(i*2+1).padStart(2,'0')));
  await expect(list(page)).not.toHaveAttribute('aria-busy','true');
  expect(requests.slice(before).filter(row=>row.path==='/api/notebook/notes')).toEqual([]);
  expect(requests.some(row=>/^\/api\/notebook\/notes\//.test(row.path)||row.path==='/map/notes.json'||row.path==='/map/notes-data.js')).toBe(false);
  expect(requests.filter(row=>row.method==='POST').map(row=>row.path)).toEqual(['/api/auth/login']);
});

test('first Timeline entry paints its warmed aggregate while fresh validation is held and sign-out clears it',async({page})=>{
  const timeline=page.locator('#notebook-workspace [data-nb=timeline]');
  const reads=[];page.on('request',request=>reads.push(new URL(request.url()).pathname));
  await page.goto('/#about');await signIn(page,'alice',{navigate:false});
  await page.waitForResponse(response=>new URL(response.url()).searchParams.get('projection')==='navigation');
  // Advance only the browser Date past the recent-intent window. Network and
  // timers remain real; this first entry must validate the earlier aggregate.
  await page.clock.setFixedTime(Date.now()+11000);
  const pending=await holdResponse(page,url=>url.pathname==='/api/notebook/timeline');
  try {
    await page.evaluate(()=>{location.hash='#notebook?view=timeline';});await pending.arrived;
    await expect(timeline.locator('svg')).toBeVisible();await expect(timeline).toHaveAttribute('aria-busy','true');
    await expect(page.locator('#notebook-workspace [data-nb=map] svg')).toHaveCount(0);
    await page.locator('#nav-about').focus();pending.release();await pending.delivered;
    await expect(timeline).not.toHaveAttribute('aria-busy','true');await expect(page.locator('#nav-about')).toBeFocused();
  } finally {pending.release();await pending.delivered;await pending.remove();}
  expect(reads.filter(path=>path==='/api/notebook/timeline')).toHaveLength(2);
  expect(reads.some(path=>/^\/api\/notebook\/notes\//.test(path)||path==='/map/notes.json')).toBe(false);
  await page.locator('#logout-button').click();
  await expect(page.locator('#access-workspace')).toBeVisible();
  await expect(page.locator('#notebook-workspace .nb-entry')).toHaveCount(0);
  await expect(page.locator('#notebook-workspace [data-nb=timeline] svg')).toHaveCount(0);
  await signIn(page,'bob',{navigate:false});
  await page.locator('#nav-notebook').click();
  await expect(list(page)).toContainText('Bob browser-only private note');
  await expect(page.locator('#notebook-workspace')).not.toContainText('Alice browser-only private note');
});

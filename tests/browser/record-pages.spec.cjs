const {test, expect, signIn, useDataSaver, gate, holdResponse} = require('./helpers.cjs');

const history = page => page.locator('#activity-workspace');
const historyContent = page => page.locator('#history-content');
const activityRefresh = page => page.locator('#refresh-activity');

async function startAtAbout(page) {
  // These cases establish cold geometry then exercise foreground-cache reuse.
  // Fully warmed People/history behavior has separate normal-preference cases.
  await useDataSaver(page);
  await page.goto('/#about');
  await signIn(page, 'alice', {navigate: false});
  await expect(page.locator('#about-workspace')).toBeVisible();
}

async function noVisibleLoading(page, selector) {
  expect(await page.locator(selector).evaluate(root => [...root.querySelectorAll('[role=status],.loading-state,.loader')]
    .filter(element => {
      const style = getComputedStyle(element), box = element.getBoundingClientRect();
      return box.width > 2 && box.height > 2 && style.visibility !== 'hidden' && style.clipPath === 'none' &&
        !element.closest('[hidden]') && (element.matches('.loader') || (/loading/i.test(element.textContent) &&
        !element.querySelector('.sr-only')));
    }).length)).toBe(0);
}

function sameRect(actual, expected, label) {
  for (const key of ['x', 'y', 'width', 'height']) {
    expect(Math.abs(actual[key] - expected[key]), label + ' ' + key).toBeLessThanOrEqual(1);
  }
}

async function peopleGeometry(page) {
  return page.locator('#people-workspace').evaluate(root => {
    const rect = element => {const b = element.getBoundingClientRect(); return {x:b.x, y:b.y, width:b.width, height:b.height};};
    const table = root.querySelector('.people-table');
    return {title:rect(root.querySelector('h1')), refresh:rect(root.querySelector('#refresh-people')),
      header:rect(table.tHead), columns:[...table.tHead.rows[0].cells].map(rect),
      firstRow:rect(table.tBodies[0].rows[0]), overflow:document.documentElement.scrollWidth > innerWidth};
  });
}

async function historyGeometry(page) {
  return history(page).evaluate(root => {
    const rect = element => {const b = element.getBoundingClientRect(); return {x:b.x, y:b.y, width:b.width, height:b.height};};
    return {title:rect(root.querySelector('h1')), refresh:rect(root.querySelector('#refresh-activity')),
      overflow:document.documentElement.scrollWidth > innerWidth};
  });
}

// Render-only rows exercise page layout without solving or submitting any
// challenge. Every history read first goes through the real fixture's access
// checks; denied responses are never changed into successful fixture data.
async function historyFixture(page, count = 30) {
  const rows = Array.from({length:count}, (_, index) => ({id:'browser-layout-' + index,
    scheme_id:'teaching-dh-kem', scheme_version:'4', profile_id:'tier-1', instance_size:1,
    method_name:'Synthetic layout method ' + index, received_at:1700000000 - index,
    status:index % 2 ? 'incorrect' : 'correct'}));
  const fixture = {rows, reads:0, next:null, failNext:false};
  fixture.holdNext = () => {
    const arrived = gate(), release = gate(), delivered = gate();
    const pending = {arrived:arrived.promise, delivered:delivered.promise, release:release.resolve,
      arrive:arrived.resolve, released:release.promise, deliver:delivered.resolve};
    fixture.next = pending; return pending;
  };
  await page.route('**/api/me/submissions', async route => {
    fixture.reads++;
    const pending = fixture.next, selected = fixture.rows.slice(), fail = fixture.failNext;
    fixture.next = null; fixture.failNext = false;
    try {
      const response = await route.fetch();
      if (response.status() !== 200) return await route.fulfill({response});
      if (fail) return await route.fulfill({response,status:503,json:{detail:'Synthetic temporary history failure.'}});
      const data = await response.json();
      if (pending) {pending.arrive(); await pending.released;}
      await route.fulfill({response, json:{...data, submissions:selected}});
    } finally {pending?.deliver();}
  });
  return fixture;
}

for (const width of [1440, 390]) {
  test('Leaderboard keeps its ' + width + 'px table geometry and warm focus while reads are held', async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000}); await startAtAbout(page);
    const pending = await holdResponse(page, url => url.pathname === '/api/people');
    let before;
    try {
      await page.locator('#nav-people').click(); await pending.arrived;
      await expect(page.locator('#people-content .people-preview')).toBeVisible();
      await expect(page.locator('#people-content .people-preview')).toHaveAttribute('aria-hidden', 'true');
      await expect(page.locator('#people-content .people-preview a,#people-content .people-preview button')).toHaveCount(0);
      await noVisibleLoading(page, '#people-workspace');
      before = await peopleGeometry(page);
      await page.screenshot({path:testInfo.outputPath('leaderboard-cold-' + width + '.png')});
      expect(before.overflow).toBe(false);
    } finally {pending.release(); await pending.delivered; await pending.remove();}
    await expect(page.locator('#people-content')).toContainText('browser_alice');
    await expect(page.locator('#people-content .people-preview')).toHaveCount(0);
    const after = await peopleGeometry(page);
    await page.screenshot({path:testInfo.outputPath('leaderboard-settled-' + width + '.png')});
    for (const key of ['title','refresh','header','firstRow']) sameRect(after[key], before[key], key);
    expect(after.columns).toHaveLength(before.columns.length);
    after.columns.forEach((column, index) => sameRect(column, before.columns[index], 'column ' + index));
    expect(after.overflow).toBe(false);

    const row = await page.locator('#people-content .person-link').filter({hasText:'browser_alice'}).elementHandle();
    await page.locator('#nav-about').click();
    const fresh = await holdResponse(page, url => url.pathname === '/api/people');
    try {
      await page.locator('#nav-people').click(); await fresh.arrived;
      expect(await row.evaluate(element => element.isConnected)).toBe(true);
      await page.locator('#people-content .person-link').filter({hasText:'browser_alice'}).focus();
      await noVisibleLoading(page, '#people-workspace');
      fresh.release(); await fresh.delivered;
      await expect(page.locator('#refresh-people')).toBeEnabled();
      expect(await row.evaluate(element => element.isConnected && document.activeElement === element)).toBe(true);
    } finally {fresh.release(); await fresh.delivered; await fresh.remove();}

    // Real authorization/read, with only a synthetic score changed so this
    // refresh must repaint rows instead of taking the unchanged-data shortcut.
    const changed = gate(), ready = gate(), delivered = gate();
    const matcher = url => url.pathname === '/api/people';
    await page.route(matcher, async route => {
      try {
        const response = await route.fetch(); expect(response.status()).toBe(200);
        const data = await response.json();
        data.people = data.people.map(person => ({...person, points:person.points + 1}));
        ready.resolve(); await changed.promise; await route.fulfill({response,json:data});
      } finally {delivered.resolve();}
    });
    try {
      await page.locator('#refresh-people').click(); await ready.promise;
      expect(await row.evaluate(element => element.isConnected)).toBe(true);
      await page.locator('#people-content .person-link').filter({hasText:'browser_alice'}).focus();
      changed.resolve(); await delivered.promise;
      await expect(page.locator('#refresh-people')).toBeEnabled();
      expect(await row.evaluate(element => element.isConnected)).toBe(false);
      await expect(page.locator('#people-content .person-link').filter({hasText:'browser_alice'})).toBeFocused();
    } finally {changed.resolve(); await delivered.promise; await page.unroute(matcher);}
  });

  test('My activity keeps its ' + width + 'px heading and page layout through cold, cached, many and empty results', async ({page}, testInfo) => {
    await page.setViewportSize({width,height:1000}); await startAtAbout(page);
    const fixture = await historyFixture(page), pending = fixture.holdNext();
    let before;
    try {
      await page.locator('#history-button').click(); await pending.arrived;
      await expect(page).toHaveURL(/#activity$/);
      await expect(page.locator('#history-content .history-preview')).toBeVisible();
      await noVisibleLoading(page, '#activity-workspace');
      before = await historyGeometry(page);
      expect(before.overflow).toBe(false);
      await page.screenshot({path:testInfo.outputPath('activity-cold-' + width + '.png')});
    } finally {pending.release(); await pending.delivered;}
    await expect(historyContent(page).locator('button.history-item')).toHaveCount(30);
    await expect(page.locator('#activity-notes')).not.toHaveAttribute('aria-busy','true');
    const many = await historyGeometry(page);
    for (const key of ['title','refresh']) sameRect(many[key],before[key],key);
    expect(many.overflow).toBe(false);
    expect(await historyContent(page).evaluate(element => getComputedStyle(element).overflowY)).toBe('visible');
    await page.screenshot({path:testInfo.outputPath('activity-many-' + width + '.png'),fullPage:true});
    await expect(page.locator('#activity-workspace h2')).toHaveText(['Schemes','Bounties','Notes']);
    fixture.rows=[];await page.locator('#nav-about').click();const empty=fixture.holdNext();
    try {
      await page.locator('#history-button').click();await empty.arrived;
      await expect(historyContent(page).locator('button.history-item')).toHaveCount(30);
      empty.release();await empty.delivered;
      await expect(historyContent(page)).toContainText('No submissions yet.');
      const after=await historyGeometry(page);for(const key of ['title','refresh'])sameRect(after[key],before[key],key);
    } finally {empty.release();await empty.delivered;}
    await page.reload();await expect(history(page)).toBeVisible();
    await expect(page.locator('#activity-title')).toHaveText('My activity');
    await expect(historyContent(page)).toContainText('No submissions yet.');
  });

  test('populated activity includes attacks, proposed schemes and bounty participation at ' + width + 'px',async({page},testInfo)=>{
    await page.setViewportSize({width,height:1000});await startAtAbout(page);await historyFixture(page,2);
    const session=await (await page.request.get('/api/session')).json();
    await page.route('**/api/notebook/notes?*',async route=>{
      const url=new URL(route.request().url());if(url.searchParams.get('scope')!=='mine')return route.fallback();
      const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
      const make=(id,title,tags)=>({id,revision:1,created_at:1700000000,publication_status:'private',revisions:[{revision:1,created_at:1700000000,content:{title,tags}}]});
      await route.fulfill({response,json:{notes:[make('fixture-attack','Investigating a reduced parameter attack',['attack-analysis']),make('fixture-proposal','A proposed construction for review',['scheme-description','proposal'])],next_after:null}});
    });
    await page.route('**/api/bounties?*',async route=>{
      const url=new URL(route.request().url());if(url.searchParams.get('status')!=='mine')return route.fallback();
      const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
      await route.fulfill({response,json:{bounties:[{id:'fixture-own',title:'Document the scheme assumptions',status:'pending',owner:session.user,created_at:1700000000},{id:'fixture-contribution',title:'Reproduce the reference analysis',status:'open',owner:{id:'other'},created_at:1700000000}],pagination:{offset:0,limit:25,has_more:false}}});
    });
    await page.locator('#history-button').click();
    await expect(page.locator('#activity-notes')).toContainText('Proposed scheme');await expect(page.locator('#activity-notes')).toContainText('Attack');
    await expect(page.locator('#activity-notes .activity-scheme-tag')).toHaveText('Proposed scheme');
    await expect(page.locator('#activity-bounties')).toContainText('Proposed by you');await expect(page.locator('#activity-bounties')).toContainText('You contributed');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:testInfo.outputPath('activity-populated-'+width+'.png'),fullPage:true});
  });

  test('activity and leaderboard titles match Schemes, Bounties and Notes at ' + width + 'px',async({page})=>{
    await page.setViewportSize({width,height:1000});await startAtAbout(page);
    const styles=[];
    for(const [button,selector] of [['#nav-challenge','#catalogue-title'],['#nav-bounties','.bty-title-row h1'],['#nav-notebook','#notebook-workspace .nb-title-row h1'],['#nav-people','#people-title'],['#history-button','#activity-title']]){
      await page.locator(button).click();const heading=page.locator(selector);await expect(heading).toBeVisible();
      styles.push(await heading.evaluate(element=>{const style=getComputedStyle(element);return {family:style.fontFamily,size:style.fontSize,weight:style.fontWeight,line:style.lineHeight,spacing:style.letterSpacing,height:element.getBoundingClientRect().height};}));
    }
    for(const style of styles.slice(1))expect(style).toEqual(styles[0]);
  });
}

test('submission details return to activity and late reads cannot restore a departed page',async({page})=>{
  await startAtAbout(page);const fixture=await historyFixture(page,1);
  await page.route('**/api/submissions/browser-layout-0',async route=>{
    const response=await route.fetch();expect(response.status()).toBe(404);
    await route.fulfill({response,status:200,json:{submission:{...fixture.rows[0],handle:'browser_alice',elapsed_seconds:1,
      method:{name:'Synthetic detail fixture',pseudocode:'Display-only fixture; no attack executed.'},compute:{}}}});
  });
  await page.locator('#history-button').click();const row=historyContent(page).locator('button.history-item').first();await expect(row).toBeVisible();
  await row.click();await expect(page.locator('#recovery-title')).toHaveText('Synthetic detail fixture');
  await page.keyboard.press('Escape');await expect(row).toBeFocused();await expect(history(page)).toBeVisible();
  fixture.rows=[{...fixture.rows[0],method_name:'Updated synthetic method'}];
  const refreshed=fixture.holdNext();
  try {
    await activityRefresh(page).click();await refreshed.arrived;await row.click();
    const close=page.locator('#recovery-dialog [data-close]');await close.focus();
    refreshed.release();await refreshed.delivered;
    await expect(historyContent(page)).toContainText('Updated synthetic method');await expect(close).toBeFocused();
    await page.keyboard.press('Escape');await expect(row).toBeFocused();
  } finally {refreshed.release();await refreshed.delivered;}
  await page.locator('#nav-about').click();const pending=fixture.holdNext();
  try {
    await page.locator('#history-button').click();await pending.arrived;await page.locator('#nav-about').click();
    const before=await historyContent(page).textContent();fixture.rows=[];
    pending.release();await pending.delivered;await expect(history(page)).toBeHidden();await expect(historyContent(page)).toHaveText(before);
  } finally {pending.release();await pending.delivered;}
  await page.goBack();await expect(history(page)).toBeVisible();await expect(historyContent(page)).toContainText('No submissions yet.');
});

test('revoked access clears every activity section and retires pending reads',async({page,context})=>{
  await startAtAbout(page);const fixture=await historyFixture(page,1);await page.locator('#history-button').click();
  await expect(historyContent(page)).toContainText('Synthetic layout method 0');
  const pending=fixture.holdNext();
  try {
    await activityRefresh(page).click();await pending.arrived;
    await context.addCookies([{name:'crucible_session',value:'synthetic-revoked-browser-cookie',url:'http://127.0.0.1:8029',sameSite:'Strict'}]);
    await page.locator('#nav-people').click();await expect(page.locator('#access-workspace')).toBeVisible();
    pending.release();await pending.delivered;
    for(const selector of ['#history-content','#activity-bounties','#activity-notes'])await expect(page.locator(selector)).toBeEmpty();
    await expect(history(page)).toBeHidden();await expect(page.locator('#history-button')).toBeHidden();
  } finally {pending.release();await pending.delivered;}
});

test('a legacy-server record reserves its heading without an interim label before the real result', async ({page}) => {
  // Rolling upgrades/old servers omit the snapshot. Exercise the retained
  // individual-read fallback, rather than expecting that read on new servers.
  await page.route('**/api/schemes?*', async route => {
    const response = await route.fetch();
    if (response.status() !== 200) return route.fulfill({response});
    const data = await response.json();
    const strip = release => {delete release.recovery_summary; (release.previous_releases || []).forEach(strip);};
    (data.schemes || []).forEach(strip); if (data.scheme) strip(data.scheme);
    await route.fulfill({response,json:data});
  });
  await startAtAbout(page);
  const pending = await holdResponse(page, url => url.pathname === '/api/leaderboard');
  const writes = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/') && !['GET','HEAD'].includes(request.method())) writes.push(request.method());
  });
  const title = page.locator('#leaderboard-title'), refresh = page.locator('#refresh-leaderboard');
  let header, control;
  try {
    await page.evaluate(() => {location.hash = '#challenge/teaching-dh-kem/4?profile=dh-20&view=leaderboard';});
    await pending.arrived;
    await expect(page.locator('#panel-leaderboard')).toBeVisible();
    await expect(title).toBeEmpty();
    await expect(title).toBeHidden();
    await expect(page.locator('#tier-record')).not.toContainText('Recovery record');
    await noVisibleLoading(page, '#tier-record');
    await expect(refresh).toBeDisabled();
    header = await page.locator('.tier-record-heading').boundingBox(); control = await refresh.boundingBox();
    expect(header.height).toBeGreaterThan(20);
    await page.locator('#nav-about').focus();
  } finally {pending.release(); await pending.delivered; await pending.remove();}
  await expect(title).toHaveText('Unsolved');
  await expect(page.getByRole('link', {name:'Take on this stage →',exact:true})).toBeVisible();
  await expect(refresh).toBeEnabled();
  sameRect(await page.locator('.tier-record-heading').boundingBox(), header, 'record header');
  sameRect(await refresh.boundingBox(), control, 'record refresh');
  await expect(page.locator('#nav-about')).toBeFocused();
  expect(writes).toEqual([]);
});

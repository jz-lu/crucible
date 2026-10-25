const {test, expect, signIn, useDataSaver, gate, holdResponse} = require('./helpers.cjs');

const history = page => page.locator('#history-dialog');
const historyContent = page => page.locator('#history-content');
const historyClose = page => history(page).getByRole('button', {name: 'Close submissions dialog'});

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
  return history(page).evaluate(dialog => {
    const rect = element => {const b = element.getBoundingClientRect(); return {x:b.x, y:b.y, width:b.width, height:b.height};};
    return {frame:rect(dialog), title:rect(dialog.querySelector('h2')), close:rect(dialog.querySelector('[data-close]')),
      viewport:innerHeight, overflow:document.documentElement.scrollWidth > innerWidth};
  });
}

// Render-only rows exercise modal sizing without solving or submitting any
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

  test('My submissions keeps its ' + width + 'px dialog frame through cold, cached, many and empty results', async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000}); await startAtAbout(page);
    const fixture = await historyFixture(page), pending = fixture.holdNext();
    let before;
    try {
      await page.locator('#history-button').click(); await pending.arrived;
      await expect(page.locator('.history-preview')).toBeVisible();
      await expect(page.locator('.history-preview')).toHaveAttribute('aria-hidden', 'true');
      await noVisibleLoading(page, '#history-dialog');
      before = await historyGeometry(page);
      await page.screenshot({path:testInfo.outputPath('submissions-cold-' + width + '.png')});
      expect(before.frame.y).toBeGreaterThanOrEqual(0);
      expect(before.frame.y + before.frame.height).toBeLessThanOrEqual(before.viewport);
      expect(before.overflow).toBe(false);
    } finally {pending.release(); await pending.delivered;}
    await expect(historyContent(page).locator('button.history-item')).toHaveCount(30);
    const many = await historyGeometry(page);
    await page.screenshot({path:testInfo.outputPath('submissions-many-' + width + '.png')});
    for (const key of ['frame','title','close']) sameRect(many[key], before[key], key);
    expect(await historyContent(page).evaluate(element => element.scrollHeight > element.clientHeight &&
      ['auto','scroll'].includes(getComputedStyle(element).overflowY))).toBe(true);
    await page.keyboard.press('Escape'); await expect(page.locator('#history-button')).toBeFocused();

    fixture.rows = []; const empty = fixture.holdNext();
    try {
      await page.locator('#history-button').click(); await empty.arrived;
      await expect(historyContent(page).locator('button.history-item')).toHaveCount(30);
      const cached = await historyGeometry(page);
      for (const key of ['frame','title','close']) sameRect(cached[key], before[key], 'cached ' + key);
      await historyClose(page).focus(); empty.release(); await empty.delivered;
      await expect(historyContent(page)).toContainText('No submissions yet.');
      await expect(historyClose(page)).toBeFocused();
      const absent = await historyGeometry(page);
      for (const key of ['frame','title','close']) sameRect(absent[key], before[key], 'empty ' + key);
      expect(fixture.reads).toBe(2);
    } finally {empty.release(); await empty.delivered;}
    await page.keyboard.press('Escape'); await expect(page.locator('#history-button')).toBeFocused();

    // A fresh page has no memory cache. Failed-read Retry must not close/reopen
    // the modal or leave focus on its replaced error button while pending.
    await page.reload(); await expect(page.locator('#about-workspace')).toBeVisible();
    fixture.failNext = true; await page.locator('#history-button').click();
    const retry = historyContent(page).getByRole('button', {name:'Try again'});
    await expect(retry).toBeVisible(); const failed = await historyGeometry(page);
    const retried = fixture.holdNext();
    try {
      await retry.focus(); await retry.click(); await retried.arrived;
      await expect(historyClose(page)).toBeFocused();
      await expect(page.locator('.history-preview')).toBeVisible();
      const held = await historyGeometry(page);
      for (const key of ['frame','title','close']) sameRect(held[key], failed[key], 'retry ' + key);
      retried.release(); await retried.delivered;
      await expect(historyContent(page)).toContainText('No submissions yet.');
      await expect(historyClose(page)).toBeFocused();
      expect(fixture.reads).toBe(4);
    } finally {retried.release(); await retried.delivered;}
    await page.keyboard.press('Escape'); await expect(page.locator('#history-button')).toBeFocused();
  });
}

test('nested submission details return focus to their history row and a closed dialog ignores late history data', async ({page}) => {
  await startAtAbout(page); const fixture = await historyFixture(page, 1);
  await page.route('**/api/submissions/browser-layout-0', async route => {
    // The route is authorized by the real boundary, but this deliberately
    // nonexistent UI-only record must not pretend to be a persisted receipt.
    const response = await route.fetch();
    expect(response.status()).toBe(404);
    await route.fulfill({response,status:200,json:{submission:{...fixture.rows[0],handle:'browser_alice',
      elapsed_seconds:1,method:{name:'Synthetic detail fixture',pseudocode:'Display-only fixture; no attack executed.'},compute:{}}}});
  });
  await page.locator('#history-button').click();
  await expect(historyContent(page).locator('button.history-item')).toBeVisible();
  await page.keyboard.press('Escape');
  fixture.rows = [{...fixture.rows[0],method_name:'Updated synthetic layout method'}];
  const refreshed = fixture.holdNext();
  try {
    await page.locator('#history-button').click(); await refreshed.arrived;
    const row = historyContent(page).locator('button.history-item').first();
    const oldRow = await row.elementHandle(), before = await historyGeometry(page);
    await row.click();
    await expect(page.locator('#recovery-title')).toHaveText('Synthetic detail fixture');
    const closeDetails = page.locator('#recovery-dialog [data-close]'); await closeDetails.focus();
    refreshed.release(); await refreshed.delivered;
    await expect(historyContent(page)).toContainText('Updated synthetic layout method');
    expect(await oldRow.evaluate(element => element.isConnected)).toBe(false);
    await expect(closeDetails).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.locator('#recovery-dialog')).toBeHidden(); await expect(row).toBeFocused();
    const after = await historyGeometry(page);
    for (const key of ['frame','title','close']) sameRect(after[key], before[key], key);
  } finally {refreshed.release(); await refreshed.delivered;}
  await page.keyboard.press('Escape'); await expect(page.locator('#history-button')).toBeFocused();
  const pending = fixture.holdNext();
  try {
    await page.locator('#history-button').click(); await pending.arrived;
    await page.keyboard.press('Escape'); await expect(page.locator('#history-button')).toBeFocused();
    pending.release(); await pending.delivered;
    await expect(history(page)).toBeHidden(); await expect(historyContent(page)).toBeEmpty();
    await expect(page.locator('#history-button')).toBeFocused();
  } finally {pending.release(); await pending.delivered;}
});

test('revoked access removes cached submissions and a formerly authorized pending read cannot restore them', async ({page,context}) => {
  await startAtAbout(page); const fixture = await historyFixture(page, 1);
  await page.locator('#history-button').click();
  await expect(historyContent(page)).toContainText('Synthetic layout method 0');
  await page.keyboard.press('Escape');
  const pending = fixture.holdNext();
  try {
    await page.locator('#history-button').click(); await pending.arrived;
    await expect(historyContent(page)).toContainText('Synthetic layout method 0');
    await context.addCookies([{name:'crucible_session',value:'synthetic-revoked-browser-cookie',
      url:'http://127.0.0.1:8029',sameSite:'Strict'}]);
    await page.keyboard.press('Escape');
    await page.locator('#nav-people').click();
    await expect(page.locator('#access-workspace')).toBeVisible();
    await expect(page.locator('#access-description')).toContainText('Your beta access is not active');
    pending.release(); await pending.delivered;
    await expect(history(page)).toBeHidden(); await expect(historyContent(page)).toBeEmpty();
    await expect(page.locator('body')).not.toContainText('Synthetic layout method 0');
    await expect(page.locator('#history-button')).toBeHidden();
  } finally {pending.release(); await pending.delivered;}
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

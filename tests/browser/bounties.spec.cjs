const {test, expect, signIn, useDataSaver, holdResponse} = require('./helpers.cjs');

// UI-only synthetic tasks follow the disposable app's real session. This does
// not replace the backend lifecycle tests, publish tasks or award any points.
const id = '11111111-1111-4111-8111-111111111111';
const policy = {id:'synthetic-bounty-policy',status:'ready',monthly_units:10,proposal_cost_units:1,boost_cost_units:1,proposal_reward_points:100,points_per_boost:100};
const terms = {title:'Reproduce a bounded fixture',summary:'A synthetic browser task; no research claim.',background_tags:['Reproduction'],scope:4,completion_criteria:'Produce an exact independent comparison.',verification_plan:'Compare pinned outputs and document disagreements.',source_notes:[]};
const task = {...terms,id,terms,sources:[],status:'open',version:1,revision:1,terms_sha256:'a'.repeat(64),owner:{id:'22222222-2222-4222-8222-222222222222',handle:'fixture_poster'},reward_points:100,pending_claim_count:0,capabilities:{boost:true,submit:true},submissions:[],decisions:[],award:null};
async function fixture(page, {review = false} = {}) {
  const reads = [];
  await page.route(url => url.pathname.startsWith('/api/bounties') || url.pathname === '/api/me/bounty-allowance', async route => {
    expect(route.request().method()).toBe('GET');
    const url = new URL(route.request().url()); reads.push(url.pathname + url.search);
    const response = await route.fetch({url: url.origin + '/api/session'});
    const session = await response.json();
    expect(response.status()).toBe(200); expect(session.user).toBeTruthy(); expect(session.access.allowed).toBe(true);
    const item = review ? {...task,status:'pending',capabilities:{review_admission:true}} : task;
    const body = url.pathname === '/api/me/bounty-allowance' ? {allowance:{available_units:7,monthly_units:10,policy_id:policy.id,policy_status:'ready',version:1},policy} :
      url.pathname === '/api/bounties/' + id ? {bounty:item,policy} : {bounties:[item],pagination:{offset:Number(url.searchParams.get('offset')),limit:6,has_more:false},policy};
    await route.fulfill({response,json:body});
  });
  return reads;
}
for (const width of [1440, 390]) test(`bounty board, exact detail and proposal retain a compact ${width}px layout`, async ({page}, testInfo) => {
  await page.setViewportSize({width,height:width === 390 ? 844 : 1000}); await useDataSaver(page);
  const reads = await fixture(page);
  await signIn(page); await page.locator('#nav-bounties').click();
  const panel = page.locator('[data-nb="bounties"]');
  await expect(page).toHaveURL(/#bounties$/);
  await expect(page.locator('#nav-bounties')).toHaveAttribute('aria-current','page');
  await expect(page.locator('#nav-notebook')).not.toHaveAttribute('aria-current','page');
  await expect(page.locator('.primary-nav button:visible')).toHaveText(['Schemes','Bounties','Research notebook','About us']);
  const schemesTab = await page.locator('#nav-challenge').boundingBox(), bountyTab = await page.locator('#nav-bounties').boundingBox();
  expect(bountyTab.x).toBeGreaterThan(schemesTab.x);
  expect(bountyTab.y).toBeCloseTo(schemesTab.y,0);
  await expect(page.locator('#notebook-workspace > .nb-heading')).toBeHidden();
  await expect(page.locator('#notebook-workspace > .nb-views')).toBeHidden();
  await expect(page.locator('[data-nb="bounties-view"]')).toHaveCount(0);
  await expect(panel.locator('.bty-question')).toHaveCount(1);
  await expect(panel.locator('.bty-allowance')).toContainText('7 of 10 remaining');
  await expect(panel.getByRole('heading', {name:'Bounties',exact:true})).toBeVisible();
  await expect(panel.getByRole('searchbox')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('bounty-board-' + width + '.png'),fullPage:true});
  const before = reads.filter(path => path.startsWith('/api/bounties?')).length;
  await page.locator('#nav-notebook').click();
  await expect(page.locator('#notebook-workspace > .nb-views')).toBeVisible();
  await page.locator('#nav-bounties').click();
  await expect(panel.locator('.bty-question')).toHaveCount(1);
  expect(reads.filter(path => path.startsWith('/api/bounties?')).length).toBe(before);
  await panel.locator('.bty-question').click();
  await expect(panel.getByRole('heading',{name:'Completion criteria'})).toBeVisible();
  await expect(panel.getByRole('heading',{name:'How it is verified'})).toBeVisible();
  const boost = panel.getByRole('button',{name:'Boost this bounty',exact:true});
  await boost.focus(); await boost.press('Enter');
  await expect(panel.getByRole('heading',{name:'Confirm bounty boost'})).toBeFocused();
  const confirmation = await panel.locator('.bty-action-panel').boundingBox();
  expect(confirmation.y).toBeGreaterThanOrEqual(0);
  // scrollIntoView rounds the scroll offset to physical pixels.
  expect(confirmation.y + confirmation.height).toBeLessThanOrEqual(page.viewportSize().height + 1);
  await page.keyboard.press('Tab');
  await expect(panel.getByRole('button',{name:'Cancel',exact:true})).toBeFocused();
  await expect(panel.locator('.bty-action-panel')).toContainText('100 reward points');
  await panel.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(boost).toBeFocused();
  await panel.getByRole('button',{name:'← Bounties',exact:true}).click();
  await panel.getByRole('button',{name:'Propose a bounty',exact:true}).click();
  await panel.getByLabel('The task',{exact:true}).fill('Unsaved local fixture');
  await page.locator('#nav-notebook').click(); await page.locator('#nav-bounties').click();
  await expect(panel.getByLabel('The task',{exact:true})).toHaveValue('Unsaved local fixture');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('bounty-proposal-' + width + '.png'),fullPage:true});
});

for (const width of [1440,390]) test(`cold bounty navigation reserves only its own ${width}px page before reads finish`, async ({page}) => {
  await page.setViewportSize({width,height:width === 390 ? 844 : 1000}); await useDataSaver(page);
  await signIn(page);
  await expect(page.locator('#nb-note-list')).toContainText('Alice browser-only private note');
  const script = await holdResponse(page,url => url.pathname === '/app.js');
  const session = await holdResponse(page,url => url.pathname === '/api/session');
  const board = await holdResponse(page,url => url.pathname === '/api/bounties');
  let boardStarted = false;
  board.arrived.then(() => { boardStarted = true; });
  const reads = [];
  page.on('request',request => reads.push(new URL(request.url()).pathname));
  try {
    await page.goto('/?bounty-cold=' + width + '#bounties',{waitUntil:'commit'});
    await script.arrived; await session.arrived;
    await expect(page.locator('#nav-bounties')).toHaveAttribute('aria-current','page');
    const frame = page.locator('#notebook-frame-template');
    await expect(frame.locator('.bty-shell-heading')).toBeVisible();
    await expect(frame.locator('.nb-heading')).toBeHidden();
    await expect(frame.locator('.nb-views')).toBeHidden();
    const pendingHeading = await frame.locator('.bty-shell-heading h2').boundingBox();
    expect(reads.filter(path => path.startsWith('/api/bounties') || path.startsWith('/api/notebook/notes'))).toEqual([]);
    script.release(); session.release(); await board.arrived;
    const panel = page.locator('[data-nb="bounties"]:visible');
    await expect(panel.getByRole('heading',{name:'Bounties',level:1})).toBeVisible();
    const heading = await panel.getByRole('heading',{name:'Bounties',level:1}).boundingBox();
    for (const dimension of ['x','y']) expect(Math.abs(heading[dimension] - pendingHeading[dimension])).toBeLessThan(2);
    await expect(panel).not.toContainText('No open bounties yet.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    script.release(); session.release(); board.release();
    await script.delivered; await session.delivered; if (boardStarted) await board.delivered;
    await script.remove(); await session.remove(); await board.remove();
  }
});

test('old bounty deep links select the main tab and preserve the requested collection', async ({page}) => {
  await useDataSaver(page); await fixture(page); await signIn(page);
  await page.goto('/#notebook?view=bounties&status=mine');
  await expect(page).toHaveURL(/#bounties\?status=mine$/);
  await expect(page.locator('#nav-bounties')).toHaveAttribute('aria-current','page');
  await expect(page.locator('[data-nb="bounties"]:visible .bty-management-row')).toHaveCount(1);
});

test('a server-provided unavailable capability hides the main tab and redirects its links to Notes', async ({page}) => {
  await useDataSaver(page); await signIn(page);
  await expect(page.locator('#nb-note-list')).toContainText('Alice browser-only private note');
  // Exercise the explicit server flag used by the pending availability release.
  await page.route(url => url.pathname === '/', async route => {
    const response = await route.fetch();
    await route.fulfill({response,body:(await response.text()).replace('<html','<html data-bounties-enabled="false"')});
  });
  const reads = []; page.on('request',request => reads.push(new URL(request.url()).pathname));
  await page.goto('/?bounty-disabled=1#bounties?status=mine');
  await expect(page).toHaveURL(/#notebook$/);
  await expect(page.locator('#nav-bounties')).toBeHidden();
  await expect(page.locator('#nav-notebook')).toHaveAttribute('aria-current','page');
  await expect(page.locator('#nb-note-list')).toBeVisible();
  expect(reads.filter(path => path.startsWith('/api/bounties') || path === '/api/me/bounty-allowance')).toEqual([]);
});

test('a signed-out main bounty link does not read private tasks', async ({page}) => {
  const reads = []; page.on('request',request => reads.push(new URL(request.url()).pathname));
  await page.goto('/#bounties?status=mine');
  await expect(page.locator('#access-sign-in')).toBeVisible();
  await expect(page.locator('#notebook-workspace')).toBeHidden();
  expect(reads.filter(path => path.startsWith('/api/bounties') || path === '/api/me/bounty-allowance' || path === '/api/notebook/notes')).toEqual([]);
});

test('bounty admission decisions remain in the dedicated reviewer workspace', async ({page},testInfo) => {
  await useDataSaver(page); await fixture(page,{review:true});
  await signIn(page,'queuereviewer');
  await page.goto('/#reviews?section=bounties');
  const panel = page.locator('#review-notebook-workspace [data-nb="bounties"]');
  await expect(page.locator('#review-nav-bounties')).toHaveAttribute('aria-current','page');
  await expect(panel.locator('.bty-management-row')).toHaveCount(1);
  await panel.locator('.bty-management-row').click();
  await expect(panel.getByRole('heading',{name:'Admission review'})).toBeVisible();
  await expect(panel.getByRole('checkbox')).toHaveCount(5);
  await expect(panel.getByRole('button',{name:'Record admission decision'})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Boost this bounty'})).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('bounty-review.png'),fullPage:true});
});

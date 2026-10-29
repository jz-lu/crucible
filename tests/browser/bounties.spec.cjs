const {test, expect, signIn, useDataSaver} = require('./helpers.cjs');

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
  await page.setViewportSize({width,height:1000}); await useDataSaver(page);
  const reads = await fixture(page);
  await signIn(page); await page.locator('[data-nb="bounties-view"]').click();
  const panel = page.locator('[data-nb="bounties"]');
  await expect(panel.locator('.bty-question')).toHaveCount(1);
  await expect(panel.locator('.bty-allowance')).toContainText('7 of 10 remaining');
  await expect(panel.getByRole('heading', {name:'Research bounties',exact:true})).toBeVisible();
  await expect(panel.getByRole('searchbox')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('bounty-board-' + width + '.png'),fullPage:true});
  const before = reads.filter(path => path.startsWith('/api/bounties?')).length;
  await page.locator('[data-nb="notes-view"]').click();
  await page.locator('[data-nb="bounties-view"]').click();
  await expect(panel.locator('.bty-question')).toHaveCount(1);
  expect(reads.filter(path => path.startsWith('/api/bounties?')).length).toBe(before);
  await panel.locator('.bty-question').click();
  await expect(panel.getByRole('heading',{name:'Completion criteria'})).toBeVisible();
  await expect(panel.getByRole('heading',{name:'How it is verified'})).toBeVisible();
  await panel.getByRole('button',{name:'Boost this bounty',exact:true}).click();
  await expect(panel.getByRole('heading',{name:'Confirm bounty boost'})).toBeVisible();
  await expect(panel.locator('.bty-action-panel')).toContainText('100 reward points');
  await panel.getByRole('button',{name:'Cancel',exact:true}).click();
  await panel.getByRole('button',{name:'← Research bounties',exact:true}).click();
  await panel.getByRole('button',{name:'Propose a bounty',exact:true}).click();
  await panel.getByLabel('The task',{exact:true}).fill('Unsaved local fixture');
  await page.locator('[data-nb="notes-view"]').click(); await page.locator('[data-nb="bounties-view"]').click();
  await expect(panel.getByLabel('The task',{exact:true})).toHaveValue('Unsaved local fixture');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('bounty-proposal-' + width + '.png'),fullPage:true});
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

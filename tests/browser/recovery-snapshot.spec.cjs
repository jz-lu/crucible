const {test, expect, signIn, holdResponse, gate} = require('./helpers.cjs');
const recordRoute = stage => '#challenge/teaching-dh-kem/4?profile=dh-' + stage + '&view=leaderboard';

for (const width of [1440,390]) test('complete recovery snapshot browses stages locally at ' + width + 'px', async ({page}) => {
  await page.setViewportSize({width,height:1000});
  const reads = [];
  page.on('request', request => {const url=new URL(request.url()); if(url.pathname.startsWith('/api/')) reads.push({path:url.pathname,query:url.search,method:request.method()});});
  await page.goto('/' + recordRoute(20));
  await expect(page.locator('#access-sign-in')).toBeVisible();
  expect(reads.some(row=>row.path==='/api/schemes'||row.path==='/api/leaderboard')).toBe(false);
  await signIn(page,'bob',{navigate:false});
  await expect(page.locator('#leaderboard-title')).toHaveText('Unsolved');
  const initial=reads.length;
  // Check the first animation frame after actual keyboard navigation. There is
  // no fulfilled/held per-stage response that could hide a rendering wait.
  for (const stage of [21,22,23]) {
    await page.locator('#next-size').focus();
    await page.locator('#next-size').press('Enter');
    const frame=await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>resolve({
      title:document.querySelector('#leaderboard-title').textContent,
      state:document.querySelector('#tier-record').dataset.state,
      focus:document.activeElement.id, hash:location.hash,
    }))));
    expect(frame.title).toBe('Unsolved');expect(frame.state).toBe('unsolved');
    expect(frame.focus).toBe('next-size');expect(frame.hash).toContain('profile=dh-'+stage);
  }
  expect(reads.slice(initial)).toEqual([]);
  const pending=await holdResponse(page,url=>url.pathname==='/api/schemes'&&url.searchParams.has('progress_only'));
  try {
    await page.locator('#refresh-leaderboard').click();await pending.arrived;
    await expect(page.locator('#leaderboard-title')).toHaveText('Unsolved');
    await expect(page.locator('#refresh-leaderboard')).toBeDisabled();
    await page.locator('#nav-about').focus();
    pending.release();await pending.delivered;
    await expect(page.locator('#refresh-leaderboard')).toBeEnabled();
    await expect(page.locator('#nav-about')).toBeFocused();
  } finally {pending.release();await pending.delivered;await pending.remove();}
  expect(reads.filter(row=>row.query.includes('progress_only'))).toHaveLength(1);
  expect(reads.some(row=>row.path==='/api/leaderboard'||row.path.startsWith('/api/submissions/'))).toBe(false);
  expect(reads.filter(row=>row.method==='POST').map(row=>row.path)).toEqual(['/api/auth/login']);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('a snapshot refresh changes inherited winners without removing focused results or fetching details',async({page})=>{
  let handle='browser_alice', phase='initial', pending=null;
  const detailReads=[];
  page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/submissions/'))detailReads.push(request.url());});
  // Change only public rendering data after real fixture authorization/reads.
  // This proves UI ownership, not submission acceptance or backend ranking.
  await page.route('**/api/schemes?*',async route=>{
    const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
    const data=await response.json();
    const decorate=release=>{
      if(release.id==='teaching-dh-kem'&&release.version==='4') {
        for(const [id,row] of Object.entries(release.recovery_summary.profiles)) if(Number(id.slice(3))<=22) row.winner={
          rank:1,submission_id:'synthetic-render-receipt',handle,profile_id:'dh-22',scheme_id:release.id,scheme_version:release.version,
          instance_size:22,is_inherited:id!=='dh-22',received_at:1700000000,method:{name:phase==='initial'?'First display title':'Updated display title'}};
      }
      (release.previous_releases||[]).forEach(decorate);
    };
    (data.schemes||data.releases||[]).forEach(decorate);if(data.scheme)decorate(data.scheme);
    if(new URL(route.request().url()).searchParams.has('progress_only')&&pending){pending.arrive.resolve();await pending.release.promise;}
    await route.fulfill({response,json:data});pending?.deliver.resolve();
  });
  await page.goto('/#about');await signIn(page,'bob',{navigate:false});
  await page.evaluate(route=>{location.hash=route;},recordRoute(20));
  await expect(page.locator('.tier-winner-name')).toHaveText('browser_alice');
  await expect(page.locator('#leaderboard-content')).toContainText('Via Stage 22');
  const button=page.locator('.tier-record-result');await button.focus();
  phase='fresh';pending={arrive:gate(),release:gate(),deliver:gate()};
  // Call the same public Refresh handler without moving keyboard focus away
  // from the result the user is reading while the fresh response is held.
  const task=page.evaluate(()=>refreshLeaderboard());
  try {
    await pending.arrive.promise;
    await expect(button).toBeFocused();await expect(page.locator('#leaderboard-content')).toContainText('First display title');
    pending.release.resolve();await pending.deliver.promise;await task;
    await expect(page.locator('#leaderboard-content')).toContainText('Updated display title');await expect(button).toBeFocused();
  } finally {pending.release.resolve();await task;}
  expect(detailReads).toEqual([]);
});

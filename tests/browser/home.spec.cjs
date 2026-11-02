const {test,expect,signIn,useDataSaver,holdResponse}=require('./helpers.cjs');
for(const width of [1440,390]) {
 test('public welcome is useful before sign-in and keeps live research private at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);
  const pending=await holdResponse(page,url=>url.pathname==='/api/session');const protectedReads=[];
  page.on('request',request=>{const path=new URL(request.url()).pathname;if(path.startsWith('/api/')&&path!=='/api/session')protectedReads.push(path);});
  try{
   await page.goto('/',{waitUntil:'domcontentloaded'});await pending.arrived;
   await expect(page.locator('#home-title')).toContainText('Put it to the test.');await expect(page.locator('#home-workspace')).toBeVisible();
   await expect(page.locator('.home-eyebrow')).toHaveText('Cryptography 2.0');await expect(page.locator('#home-welcome')).toBeHidden();
   await expect(page.locator('#home-progress')).toBeHidden();await expect(page.locator('#access-workspace')).toBeHidden();
   expect(protectedReads).toEqual([]);pending.release();await pending.delivered;
   await expect(page.locator('#home-primary')).toBeEnabled();await expect(page.locator('#home-primary')).toHaveText('Sign in to Crucible →');
   await expect(page.locator('#home-access-note')).toContainText('invitation');expect(protectedReads).toEqual([]);
   expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
   await page.screenshot({path:testInfo.outputPath('welcome-public-'+width+'.png'),fullPage:true});
   await page.locator('#home-primary').click();await expect(page.locator('#auth-dialog')).toBeVisible();
   await page.keyboard.press('Escape');await expect(page.locator('#home-primary')).toBeFocused();
   await page.locator('#home-bounty-guide').click();await expect(page.locator('#access-workspace')).toBeVisible();expect(protectedReads).toEqual([]);
   await page.getByRole('banner').getByRole('link',{name:'Crucible home',exact:true}).click();await expect(page.locator('#home-workspace')).toBeVisible();
   await page.locator('.footer-brand').click();await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
  }finally{pending.release();await pending.delivered;await pending.remove();}
 });
 test('members see live progress, open bounties and their activity shortcut at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);await signIn(page);
  // Authorize the real reads first. The task rows below are display-only layout
  // fixtures and do not create research evidence, recoveries or bounty awards.
  await page.route('**/api/bounties?*',async route=>{
   const url=new URL(route.request().url());if(url.searchParams.get('limit')!=='3')return route.fallback();
   const response=await route.fetch();if(response.status()!==200)return route.fulfill({response});
   await route.fulfill({response,json:{bounties:[{id:'layout-research',title:'Review a scheme’s parameter-growth argument',status:'open',reward_points:300},{id:'layout-reproduce',title:'Reproduce a reference attack',status:'open',reward_points:100}]}});
  });
  const held=await holdResponse(page,url=>url.pathname==='/api/schemes'&&url.searchParams.get('projection')==='navigation');
  try{
   await page.getByRole('banner').getByRole('link',{name:'Crucible home',exact:true}).click();await held.arrived;
   await expect(page.locator('#home-primary')).toHaveText('My activity →');await expect(page.locator('#home-bounties')).toContainText('300 points');
   await expect(page.locator('#home-welcome')).toHaveText('Welcome back, browser_alice.');
   const welcome=await page.locator('#home-welcome').boundingBox(),activity=await page.locator('#home-primary').boundingBox();
   expect(welcome.y+welcome.height).toBeLessThan(activity.y);
   expect(await page.locator('#home-welcome').evaluate(element=>parseFloat(getComputedStyle(element).fontSize))).toBe(16);
   expect(await page.locator('#home-welcome').evaluate(element=>getComputedStyle(element).color)).toBe(
    await page.locator('#home-primary').evaluate(element=>getComputedStyle(element).backgroundColor));
   await expect(page.locator('#home-schemes .home-placeholder')).toHaveCount(3);
   held.release();await held.delivered;
   await expect(page.locator('#home-schemes')).toContainText('Diffie');await expect(page.locator('#home-schemes .home-stage-strip')).toBeVisible();
   expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
   const columns=await page.locator('.home-progress-panel').evaluateAll(elements=>elements.map(element=>element.getBoundingClientRect().width));
   expect(columns).toHaveLength(2);expect(Math.abs(columns[0]-columns[1])).toBeLessThanOrEqual(1);
   await page.screenshot({path:testInfo.outputPath('welcome-member-'+width+'.png'),fullPage:true});
   await page.locator('#home-propose-scheme').click();await expect(page).toHaveURL(/#propose$/);await expect(page.locator('#proposal-workspace')).toBeVisible();
   await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
   await page.goBack();await expect(page.locator('#home-workspace')).toBeVisible();
   await page.locator('#home-propose-bounty').click();await expect(page).toHaveURL(/#bounties\?compose=bounty$/);
   await expect(page.getByRole('heading',{name:'Propose a bounty',exact:true})).toBeVisible();await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
   await page.goBack();await expect(page.locator('#home-workspace')).toBeVisible();
   await page.locator('.home-map-card').click();await expect(page).toHaveURL(/#challenge\?view=map$/);await expect(page.locator('#notebook-workspace')).toBeVisible();
   await page.goBack();await expect(page.locator('#home-workspace')).toBeVisible();
   await page.locator('#home-primary').click();await expect(page.locator('#activity-workspace')).toBeVisible();
  }finally{held.release();await held.delivered;await held.remove();}
 });
}

test('password sign-in personalizes home and sign-out removes its private highlights',async({page})=>{
 await useDataSaver(page);await page.goto('/');await expect(page.locator('#home-primary')).toBeEnabled();await page.locator('#home-primary').click();
 await page.locator('#auth-password-options').evaluate(details=>details.open=true);
 await page.locator('#auth-handle').fill('browser_alice');await page.locator('#auth-password').fill('Synthetic browser fixture password');await page.locator('#auth-submit').click();
 await expect(page.locator('#auth-dialog')).toBeHidden();await expect(page.locator('#home-primary')).toHaveText('My activity →');await expect(page.locator('#home-schemes')).toContainText('Diffie');
 await page.locator('#logout-button').click();await expect(page.locator('#home-primary')).toHaveText('Sign in to Crucible →');await expect(page.locator('#home-progress')).toBeHidden();
 await expect(page.locator('#home-schemes')).toBeEmpty();await expect(page.locator('#home-bounties')).toBeEmpty();
});

test('a session transport failure leaves the public explanation readable with a usable retry',async({page})=>{
 await page.route('**/api/session',route=>route.abort('connectionrefused'));
 await page.goto('/');await expect(page.locator('#home-workspace')).toBeVisible();
 await expect(page.locator('#global-message')).toContainText('Sign-in could not be checked');await expect(page.locator('#retry-button')).toBeEnabled();
 await page.unroute('**/api/session');await page.locator('#retry-button').click();await expect(page.locator('#home-primary')).toBeEnabled();await expect(page.locator('#global-message')).toBeHidden();
});


test('GitHub sign-in returns to the personalized welcome page',async({page})=>{
 await useDataSaver(page);await page.goto('/');const origin=new URL(page.url()).origin;
 await page.route('https://github.com/login/oauth/authorize?*',route=>{
  const query=new URL(route.request().url()).searchParams;
  return route.fulfill({status:302,headers:{location:origin+'/api/auth/github/callback?'+new URLSearchParams({state:query.get('state'),code:'synthetic-browser-code'})},body:''});
 });
 await page.locator('#home-primary').click();await page.locator('#auth-github').click();
 await expect(page).toHaveURL(/#home$/);await expect(page.locator('#home-primary')).toHaveText('My activity →');await expect(page.locator('#home-schemes')).toContainText('Diffie');
});

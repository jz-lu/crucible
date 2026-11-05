const {test,expect,signIn,useDataSaver,holdResponse}=require('./helpers.cjs');
for(const width of [1440,390]) {
 test('home stays private while access resolves and offers account creation or sign-in at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);
  const pending=await holdResponse(page,url=>url.pathname==='/api/session');const protectedReads=[];
  page.on('request',request=>{const path=new URL(request.url()).pathname;if(path.startsWith('/api/')&&path!=='/api/session')protectedReads.push(path);});
  try{
   await page.goto('/',{waitUntil:'domcontentloaded'});await pending.arrived;
   await expect(page.locator('#home-workspace')).toBeHidden();await expect(page.locator('#access-workspace')).toBeVisible();
   expect(protectedReads).toEqual([]);pending.release();await pending.delivered;
   await expect(page.locator('#access-title')).toHaveText('Private beta');
   await expect(page.locator('#access-description')).toHaveText('An invitation is required to join.');
   await expect(page.locator('#access-create-account')).toBeVisible();await expect(page.locator('#access-sign-in')).toHaveText('Sign in');
   await expect(page.locator('#access-workspace')).not.toContainText('Welcome back');
   expect(protectedReads).toEqual([]);expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
   const tagline=page.locator('#access-branding .scrutiny-tagline');
   await expect(tagline).toHaveText('Find new cryptography. Test its limits.');
   const brand=await tagline.boundingBox(),card=await page.locator('#access-workspace').boundingBox();
   expect(brand.y+brand.height).toBeLessThan(card.y);
   const line=await tagline.evaluate(element=>({height:element.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(element).lineHeight)}));
   expect(line.height).toBeLessThanOrEqual(line.lineHeight+1);
   await page.screenshot({path:testInfo.outputPath('account-entry-'+width+'.png'),fullPage:true});
   await page.locator('#access-sign-in').click();await expect(page.locator('#auth-title')).toHaveText('Welcome back');
   await expect(page.locator('#auth-github')).toBeVisible();await expect(page.locator('#auth-password-summary')).toBeVisible();
   await expect(page.locator('#auth-github')).toHaveClass(/button-primary/);await expect(page.locator('#auth-create-account')).toBeVisible();
   const password=page.locator('#auth-password-summary');
   await expect(password).not.toHaveClass(/\bbutton\b/);await expect(password).toHaveCSS('border-top-width','0px');
   await expect(password).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
   const githubArrow=page.locator('#auth-github .auth-choice-arrow'),passwordArrow=password.locator('.auth-choice-arrow');
   await expect(passwordArrow).toHaveText('→');
   await expect(passwordArrow).toHaveCSS('opacity','1');await expect(githubArrow).toHaveCSS('opacity','1');
   const smaller=await password.evaluate(element=>parseFloat(getComputedStyle(element).fontSize)<parseFloat(getComputedStyle(document.querySelector('#auth-github')).fontSize));
   expect(smaller).toBe(true);
   await expect(password).toHaveCSS('font-size','11px');
   await page.screenshot({path:testInfo.outputPath('continue-panel-'+width+'.png'),fullPage:true});
   await page.locator('#auth-github').hover();await expect(githubArrow).toHaveCSS('opacity','1');await expect(passwordArrow).toHaveCSS('opacity','1');
   await password.hover();await expect(passwordArrow).toHaveCSS('opacity','1');await expect(githubArrow).toHaveCSS('opacity','1');
   await expect(passwordArrow).toHaveCSS('text-decoration-line','none');
   await page.screenshot({path:testInfo.outputPath('password-hover-'+width+'.png'),fullPage:true});
   await page.locator('#auth-title').hover();await expect(passwordArrow).toHaveCSS('opacity','1');
   await password.press('Enter');await expect(page.locator('#auth-form')).toBeVisible();
   await page.locator('#auth-methods-back').click();await expect(page.locator('#auth-github')).toBeVisible();
   await page.keyboard.press('Escape');await expect(page.locator('#access-sign-in')).toBeFocused();
   for(const hash of ['#home','#challenge','#about','#people','#notebook','#bounties']){
    await page.goto('/'+hash);await expect(page.locator('#access-create-account')).toBeVisible();
    await expect(page.locator('#home-workspace')).toBeHidden();await expect(page.locator('.primary-nav')).toBeHidden();
   }
   expect(protectedReads).toEqual([]);
   await page.getByRole('banner').getByRole('link',{name:'Crucible home',exact:true}).click();
   await expect(page.locator('#access-title')).toHaveText('Private beta');await expect(page.locator('#home-workspace')).toBeHidden();
  }finally{pending.release();await pending.delivered;await pending.remove();}
 });
 test('create account accepts an existing invitation without consuming it at '+width+'px',async({page})=>{
  await page.setViewportSize({width,height:1000});await page.goto('/');
  await page.locator('#access-create-account').click();await expect(page.locator('#auth-title')).toHaveText('Create an account');
  await expect(page.locator('#auth-description')).toContainText('invitation-only');
  await page.locator('#auth-invitation-link').fill('https://elsewhere.invalid/#join?token=cri_' + 'v'.repeat(43));
  await page.locator('#auth-invitation-form button[type=submit]').click();await expect(page.locator('#auth-invitation-error')).toBeVisible();
  await expect(page.locator('#auth-invitation-error')).toContainText('different site');
  await page.locator('#auth-invitation-link').fill('https://crucible.fail/#join?token=cri_'+'e'.repeat(43));
  await page.locator('#auth-invitation-form button[type=submit]').click();
  await expect(page.locator('#auth-invitation-error')).toContainText('This invitation is for crucible.fail');
  await expect(page.locator('#auth-invitation-error')).toContainText('this local preview');
  const token='cri_'+'v'.repeat(43),origin=new URL(page.url()).origin;
  await page.locator('#auth-invitation-link').fill(origin+'/#join?token='+token);
  await page.locator('#auth-invitation-form button[type=submit]').click();
  await expect(page).toHaveURL(url=>url.hash==='#join?token='+token);
  await expect(page.locator('#auth-title')).toHaveText('Create an account');await expect(page.locator('#auth-github')).toBeVisible();
  await expect(page.locator('#auth-dialog')).toBeVisible();await expect(page.locator('#home-workspace')).toBeHidden();
 });
 test('members see live progress, open bounties and their activity shortcut at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);await signIn(page);
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
   await expect(page.locator('#home-title')).toHaveText('Find new cryptography. Test its limits.');
   await expect(page.locator('#home-workspace')).not.toContainText('Welcome back');
   await expect(page.locator('#home-schemes .home-placeholder')).toHaveCount(3);
   held.release();await held.delivered;
   await expect(page.locator('#home-schemes')).toContainText('Diffie');await expect(page.locator('#home-schemes .home-stage-strip')).toBeVisible();
   expect((await page.locator('#home-schemes .home-stage-strip > span').first().boundingBox()).width).toBeGreaterThan(0);
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
 await useDataSaver(page);await page.goto('/');await expect(page.locator('#access-sign-in')).toBeVisible();await page.locator('#access-sign-in').click();
 await page.locator('#auth-password-summary').click();
 await page.locator('#auth-handle').fill('browser_alice');await page.locator('#auth-password').fill('Synthetic browser fixture password');await page.locator('#auth-submit').click();
 await expect(page.locator('#auth-dialog')).toBeHidden();await expect(page.locator('#home-primary')).toHaveText('My activity →');await expect(page.locator('#home-schemes')).toContainText('Diffie');
 await page.locator('#logout-button').click();await expect(page.locator('#access-create-account')).toBeVisible();await expect(page.locator('#home-workspace')).toBeHidden();await expect(page.locator('#home-progress')).toBeHidden();
 await expect(page.locator('#home-schemes')).toBeEmpty();await expect(page.locator('#home-bounties')).toBeEmpty();
});

test('a session transport failure keeps home private and offers a usable retry',async({page})=>{
 await page.route('**/api/session',route=>route.abort('connectionrefused'));
 await page.goto('/');await expect(page.locator('#home-workspace')).toBeHidden();
 await expect(page.locator('#access-description')).toContainText('Access could not be checked');await expect(page.locator('#access-retry')).toBeEnabled();
 await page.unroute('**/api/session');await page.locator('#access-retry').click();await expect(page.locator('#access-create-account')).toBeVisible();await expect(page.locator('#global-message')).toBeHidden();
});


test('GitHub sign-in opens the private home page',async({page},testInfo)=>{
 await useDataSaver(page);await page.goto('/');const origin=new URL(page.url()).origin;
 await page.route('https://github.com/login/oauth/authorize?*',route=>{
  const query=new URL(route.request().url()).searchParams;
  return route.fulfill({status:302,headers:{location:origin+'/api/auth/github/callback?'+new URLSearchParams({state:query.get('state'),code:'synthetic-browser-code'})},body:''});
 });
 await page.locator('#access-sign-in').click();
 await page.screenshot({path:testInfo.outputPath('sign-in-choices.png'),fullPage:true});
 await page.locator('#auth-github').click();
 await expect(page).toHaveURL(/#home$/);await expect(page.locator('#home-primary')).toHaveText('My activity →');await expect(page.locator('#home-schemes')).toContainText('Diffie');
});

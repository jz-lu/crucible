const {test,expect,signIn,useDataSaver,holdResponse, accountAction, navigationAction} = require('./helpers.cjs');
async function expectSpotlightClear(page,{orientation=false}={}) {
 const panelSelector=orientation?'.orientation-card':'#participation-guide',targetSelector=orientation?'.orientation-target':'.guide-target';
 const svg=page.locator('.guide-overlay:not([hidden])');
 await expect(page.locator(targetSelector).first()).toBeVisible();await expect(svg.locator('.guide-lead')).toHaveAttribute('d',/^M/);
 await expect.poll(()=>page.evaluate(({panelSelector,targetSelector,orientation})=>{
  const panel=document.querySelector(panelSelector).getBoundingClientRect(),target=document.querySelector(targetSelector),box=target.getBoundingClientRect();
  const hit=document.elementFromPoint(box.x+box.width/2,box.y+box.height/2);
  return {contained:panel.left>=0&&panel.right<=innerWidth&&panel.top>=0&&panel.bottom<=innerHeight,
   overlap:Math.max(0,Math.min(panel.right,box.right)-Math.max(panel.left,box.left))*Math.max(0,Math.min(panel.bottom,box.bottom)-Math.max(panel.top,box.top)),
   targetClickable:orientation||target===hit||target.contains(hit)};
 },{panelSelector,targetSelector,orientation})).toEqual({contained:true,overlap:0,targetClickable:true});
 await expect(svg.locator('.guide-lead')).toHaveCSS('animation-name','none');
 const transform=await svg.evaluate(element=>{const matrix=element.getScreenCTM();return {a:matrix.a,d:matrix.d,e:matrix.e,f:matrix.f};});
 for(const property of ['a','d'])expect(transform[property]).toBeCloseTo(1,4);
 for(const property of ['e','f'])expect(transform[property]).toBeCloseTo(0,4);
 await expect(svg.locator('mask')).toHaveAttribute('x','0');await expect(svg.locator('mask')).toHaveAttribute('y','0');
}

async function expectFooterClearOfTheme(page) {
 await page.locator('.footer-brand').scrollIntoViewIfNeeded();
 await expect(page.locator('.footer-brand')).toBeVisible();
 await expect(page.locator('.resi-footer-brand')).toHaveCount(0);
}
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
   await expect(page.locator('.footer-brand')).toBeHidden();
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
 test('members choose between two simple paths at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);await signIn(page);
  const reads=[];page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))reads.push(request.url());});
  await page.getByRole('banner').getByRole('link',{name:'Crucible home',exact:true}).click();
  await expect(page.locator('.home-weekly-card')).toBeVisible();await expect(page.locator('#home-tour')).toBeVisible();
  await expect(page.locator('#home-title')).toHaveText('Find new cryptography. Test its limits.');
  await expect(page.locator('#home-entry .home-path')).toHaveCount(0);await expect(page.locator('#home-walkthrough')).toBeHidden();
  expect(reads.filter(url=>/\/api\/challenges/.test(url))).toEqual([]);
  await expect(page.locator('#home-featured-challenge a')).toBeVisible();
  await expect(page.locator('.primary-nav')).toBeVisible();
  await expect(page.locator('#home-challenge-guide')).toHaveCount(0);await expect(page.locator('#home-get-started')).toBeVisible();
  await expect(page.locator('.home-bounty-action,.home-challenge-start,.home-start-copy')).toHaveCount(0);
  await expect(page.locator('#home-highlights > section')).toHaveCount(2);
  const cards=await page.locator('#home-highlights').boundingBox(),other=await page.locator('.home-explore').boundingBox(),started=await page.locator('#home-get-started').boundingBox();expect(other.y).toBeGreaterThan(started.y+started.height);expect(other.y+other.height).toBeLessThan(cards.y);await expect(page.locator('#home-intro-trigger')).toHaveText('New to Crucible? Start with a guided challenge.');await expect(page.locator('#home-tour')).toHaveText('Or try a bounty or propose a scheme.');
  await expect(page.locator('.home-explore a')).toHaveText(['try a bounty','propose a scheme']);expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:testInfo.outputPath('two-paths-'+width+'.png'),fullPage:true});
  await expectFooterClearOfTheme(page);
  await page.locator('.footer-brand').click();await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
  await page.locator('#home-bounties-link').click();await expect(page).toHaveURL(/#bounties$/);await expect(page.locator('.bty-count')).toBeHidden();await page.locator('#site-brand').click();
  await page.locator('#home-propose-link').click();await expect(page.locator('#proposal-workspace')).toBeVisible();await page.locator('#site-brand').click();
  const intro=page.locator('#home-intro-trigger'),start=page.locator('#home-get-started'),color=await start.evaluate(el=>getComputedStyle(el).backgroundColor);await intro.hover();await expect(start).not.toHaveCSS('background-color',color);await intro.click();await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');await expect(page.locator('.live-guide-progress')).toHaveText('1 / 3');await page.locator('#participation-guide').getByRole('button',{name:'Exit guide',exact:true}).click();
 });
 test('challenge guidance is offered on the challenge page and restarts at step one at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await useDataSaver(page);await page.goto('/#home');await signIn(page,'alice',{navigate:false});
  const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
  const launch=page.locator('.home-weekly-card'),dialog=page.locator('#challenge-start-dialog');
  await launch.click();await expect(page).toHaveURL(/#challenge\/.+view=challenge/);await expect(page.locator('#workspace')).toBeVisible();await expect(dialog).toBeVisible();await expect(page.locator('#challenge-start-guided')).toBeFocused();
  await page.screenshot({path:testInfo.outputPath('challenge-choice-'+width+'.png')});
  await page.keyboard.press('Escape');await expect(dialog).toBeHidden();await expect(page.locator('#workspace')).toBeFocused();await expect(page).toHaveURL(/#challenge\//);await page.locator('#site-brand').click();
  await launch.click();await page.getByRole('button',{name:'Close challenge choices',exact:true}).click();await expect(dialog).toBeHidden();await page.locator('#site-brand').click();
  await launch.click();await page.locator('#challenge-start-own').click();await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#participation-guide')).toBeHidden();await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
  for(let attempt=0;attempt<2;attempt++){await page.locator('#site-brand').click();await launch.click();await expect(dialog).toBeVisible();await page.locator('#challenge-start-guided').click();await expect(page.locator('#participation-guide')).toBeVisible();await expect(page.locator('#participation-guide')).not.toHaveClass(/is-minimized/);await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');await expect(page.locator('.live-guide-progress')).toHaveText('1 / 3');await page.getByRole('button',{name:'Hide guide',exact:true}).click();}
  expect(writes).toEqual([]);
 });
 for(const approach of ['agent','manual'])test('challenge guide follows the live '+approach+' workspace at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);
  await page.goto('/#home');await signIn(page,'alice',{navigate:false});
  const writes=[];page.on('request',request=>{if(request.method()==='POST')writes.push(new URL(request.url()).pathname);});
  await page.evaluate(()=>navigateTop('#home?guide=challenge'));
  await expect(page.locator('#catalogue-workspace')).toBeVisible();expect(writes).toEqual([]);
  await expect(page.locator('#live-guide-title')).toHaveText('Choose a scheme.');
  await expect(page.locator('#participation-guide .button-primary')).toHaveText('Choose this scheme');
  await expectSpotlightClear(page);
  await page.getByRole('button',{name:'Hide guide',exact:true}).hover();
  await expect(page.getByRole('button',{name:'Hide guide',exact:true})).toHaveCSS('text-decoration-line','none');
  await page.locator('#catalogue-workspace h1').click();
  await expect(page.getByRole('button',{name:'Continue guide',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Exit guide',exact:true})).toBeVisible();
  await expect(page.locator('.guide-overlay')).toBeHidden();
  await page.getByRole('button',{name:'Continue guide',exact:true}).click();
  await expect(page.locator('#live-guide-title')).toHaveText('Choose a scheme.');
  await expectSpotlightClear(page);
  await page.reload();await expect(page.locator('#live-guide-title')).toHaveText('Choose a scheme.');
  await page.locator('.kem-name[href*="teaching-dh-kem"]').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Open the challenge.');
  await page.locator('#overview-challenges').click();
  await expect(page.locator('#workspace')).toBeVisible();await expect(page.locator('#participation-guide')).toBeVisible();
  await expect(page).toHaveURL(/profile=dh-20[^#]*view=challenge/);
  await expect(page.locator('#tab-challenge')).toHaveAttribute('aria-selected','true');
  expect(writes).toEqual([]);
  if(await page.locator('#issue-button').isVisible())await page.locator('#participation-guide .button-primary').click();
  await expect(page.locator('#challenge-active')).toBeVisible();
  if(width===390)await page.setViewportSize({width:320,height:1000});
  await expect.poll(()=>page.locator('#participation-guide').evaluate(panel=>{
   const first=panel.querySelector('.button-primary').getBoundingClientRect(),second=panel.querySelector('.live-guide-actions .text-button').getBoundingClientRect();
   return Math.abs((first.top+first.height/2)-(second.top+second.height/2));
  })).toBeLessThan(1);
  if(width===390)await page.setViewportSize({width,height:1000});
  await page.locator('#participation-guide').getByRole('button',{name:approach==='agent'?'Use an agent':'Use my own tools',exact:true}).click();
  await expect(page.locator('#live-guide-title')).toHaveText(approach==='agent'?'Give the challenge to your agent.':'Download your challenge inputs.');
  await page.screenshot({path:testInfo.outputPath('live-'+approach+'-'+width+'.png'),fullPage:true});
  await page.locator('#participation-guide').getByRole('button',{name:'I have a result',exact:true}).click();
  await expect(page.locator('#submission-dialog #participation-guide')).toBeVisible();
  await expect(page.locator('#live-guide-title')).toHaveText('Enter your recovered key.');
  await page.locator('#candidate').fill('0'.repeat(64));
  await expect(page.locator('#live-guide-title')).toHaveText('Enter your recovered key.');
  await expect(page.locator('#candidate')).toBeFocused();
  await expectSpotlightClear(page);
  expect(writes.filter(path=>path.endsWith('/submit'))).toEqual([]);
  await page.locator('#participation-guide').getByRole('button',{name:'Exit guide',exact:true}).click();
  await expect(page.locator('#participation-guide')).toBeHidden();await expect(page.locator('#submission-dialog')).toBeVisible();
 });
 test('feature chooser opens the real note editor with live guidance at '+width+'px',async({page})=>{
  await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);await page.goto('/#home');await signIn(page,'alice',{navigate:false});
  await page.goto('/#home?guide=tour');await expect(page.locator('[data-contribution]')).toHaveCount(3);
  await page.locator('[data-contribution="note"]').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Write your note.');
  await page.locator('[data-nb="new"]').click();
  const dialog=page.locator('[data-nb="dialog"]');await expect(dialog).toBeVisible();
  await expect(dialog.locator('#participation-guide')).toBeVisible();await expect(page.locator('#live-guide-title')).toHaveText('Give your idea a title.');
  await dialog.locator('[name="title"]').fill('Unsaved local layout check');
  await expect(page.locator('#live-guide-title')).toHaveText('Explain the question or result.');
  await expect(dialog.locator('[name="title"]')).toBeFocused();
  await expectSpotlightClear(page);
  await page.locator('#participation-guide .guide-back').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Give your idea a title.');
  await expect(dialog.locator('[name="title"]')).toHaveValue('Unsaved local layout check');
  await page.locator('#participation-guide .guide-next').click();await page.locator('#participation-guide .guide-next').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Explain the question or result.');
  await page.getByRole('button',{name:'Hide guide',exact:true}).click();
  await expect(page.locator('.guide-overlay')).toBeHidden();
  await dialog.locator('[name="claim"]').fill('Unsaved summary while the hint is hidden.');
  await page.getByRole('button',{name:'Continue guide',exact:true}).click();
  await expect(page.locator('#live-guide-title')).toHaveText('Set the scope.');
  await expectSpotlightClear(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 });

}

test('sign-out clears a pending challenge guide and ignores its late response',async({page})=>{
 await useDataSaver(page);await page.goto('/#home');
 const held=await holdResponse(page,url=>url.pathname==='/api/schemes'&&url.searchParams.get('projection')==='navigation');
 try{
  await signIn(page,'alice',{navigate:false});await held.arrived;await page.evaluate(()=>navigateTop('#home?guide=challenge'));
  await accountAction(page, '#logout-button');await expect(page.locator('#access-create-account')).toBeVisible();
  held.release();await held.delivered;await expect(page.locator('#home-guide-content')).toBeEmpty();await expect(page.locator('#home-workspace')).toBeHidden();
 }finally{held.release();await held.delivered;await held.remove();}
});

test('a session transport failure keeps home private and offers a usable retry',async({page})=>{
 await page.route('**/api/session',route=>route.abort('connectionrefused'));
 await page.goto('/');await expect(page.locator('#home-workspace')).toBeHidden();
 await expect(page.locator('#access-description')).toContainText('Access could not be checked');await expect(page.locator('#access-retry')).toBeEnabled();
 await page.unroute('**/api/session');await page.locator('#access-retry').click();await expect(page.locator('#access-create-account')).toBeVisible();
});

test('GitHub sign-in opens the two paths on the private home page',async({page})=>{
 await useDataSaver(page);await page.goto('/');const origin=new URL(page.url()).origin;
 await page.route('https://github.com/login/oauth/authorize?*',route=>{
  const query=new URL(route.request().url()).searchParams;
  return route.fulfill({status:302,headers:{location:origin+'/api/auth/github/callback?'+new URLSearchParams({state:query.get('state'),code:'synthetic-browser-code'})},body:''});
 });
 await page.locator('#access-sign-in').click();await page.locator('#auth-github').click();
 await expect(page).toHaveURL(/#home$/);await expect(page.locator('.home-weekly-card')).toBeVisible();await expect(page.locator('#home-tour')).toBeVisible();
});

for(const width of [1440,390])test('featured opportunities and an optional site tour work at '+width+'px',async({page},testInfo)=>{
 await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);
 // Deterministic response data for visual ranking; real bounty lifecycle
 // coverage separately checks featured cards against an admitted API record.
 const rows=[{id:'featured-low',title:'Compare two attack methods',status:'open',reward_points:100},
  {id:'featured-top',title:'Reproduce a reference attack',status:'open',reward_points:800},
  {id:'featured-middle',title:'Test a scheme’s boundary cases',status:'open',reward_points:400}];
 await page.route('**/api/bounties?status=open&sort=points-high&limit=3',route=>route.fulfill({json:{bounties:rows}}));
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 await expect(page.locator('#home-featured-challenge a')).toBeVisible();await expect(page.locator('.home-bounty')).toHaveCount(3);
 const policy=await(await page.request.get('/api/schemes?projection=navigation')).json();
 const identity=(await page.locator('.home-weekly-card').getAttribute('href')).split('?')[0].split('/').slice(1).map(decodeURIComponent);
 const enrolled=policy.recovery_scoring.enrolled_releases.some(item=>item.id===identity[0]&&item.version===identity[1]);
 if(enrolled)await expect(page.locator('.home-weekly-points strong')).toHaveText(policy.recovery_scoring.record_advance_points.toLocaleString('en-US'));
 else{await expect(page.locator('.home-weekly-points')).toHaveCount(0);await expect(page.locator('.home-weekly-reward-note')).toHaveCount(0);}
 await expect(page.getByRole('progressbar',{name:'Your verified progress'})).toHaveAttribute('aria-valuenow','0');
 await expect(page.locator('.home-stage-labels')).toContainText('Your best:');await expect(page.locator('.home-stage-labels')).toContainText('Record:');await expect(page.locator('.home-bounty-icon')).toBeVisible();
 await expect(page.locator('.home-bounty h3')).toHaveText([rows[1].title,rows[2].title,rows[0].title]);
 await expect(page.locator('.home-bounty-points strong')).toHaveText(['800','400','100']);
 const featured=await page.locator('.home-weekly-card').getAttribute('href');
 expect(featured).toMatch(/^#challenge\/.+\?profile=.+&view=challenge$/);
 await page.reload();await expect(page.locator('.home-weekly-card')).toHaveAttribute('href',featured);
 await expect(page.locator('#participation-guide')).toBeHidden();await expect(page.locator('#how-it-works')).toBeVisible();
 expect(await page.locator('#how-it-works').evaluate(element=>element.nextElementSibling.id)).toBe('account-menu');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.screenshot({path:testInfo.outputPath('featured-home-'+width+'.png'),fullPage:true});
 const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 await page.locator('#how-it-works').click();
 await expect(page.locator('.orientation-title')).toHaveText('Explore Crucible');await expect(page).toHaveURL(/#home$/);
 await expect(page.locator('.orientation-feature')).toHaveCount(7);
 await page.locator('.orientation-card .guide-hide').hover();await expect(page.locator('.orientation-card .guide-hide')).toHaveCSS('text-decoration-line','none');
 await page.locator('.orientation-tour').click({position:{x:4,y:4}});await expect(page.locator('.orientation-card')).toHaveClass(/is-minimized/);
 await expect(page.locator('.orientation-begin')).toBeHidden();await expect(page.locator('.orientation-card button:visible')).toHaveText(['Continue guide','×']);
 await page.getByRole('button',{name:'Continue guide',exact:true}).click();await expect(page.locator('.orientation-title')).toHaveText('Explore Crucible');
 for(const [feature,title,route] of [['Schemes','Schemes',/#challenge$/],['Notes','Research notes',/#notebook$/],['Bounties','Research bounties',/#bounties$/],['My activity','Your activity',/#activity\?kind=notes$/]]){
  await page.locator('.orientation-feature').filter({has:page.locator('strong',{hasText:feature})}).click();
  await expect(page.locator('.orientation-title')).toHaveText(title);await expect(page).toHaveURL(route);await expectSpotlightClear(page,{orientation:true});
  const before=page.url();await page.locator('.orientation-controls .guide-next').click();await expect(page).toHaveURL(before);await expectSpotlightClear(page,{orientation:true});
  await page.getByRole('button',{name:'All features',exact:true}).click();await expect(page.locator('.orientation-title')).toHaveText('Explore Crucible');
 }
 await page.getByRole('button',{name:'Exit guide',exact:true}).click();expect(writes).toEqual([]);
});

test('closing the optional tour restores an active submission guide',async({page})=>{
 await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 await page.goto('/#home?guide=tour');await page.locator('[data-contribution="note"]').click();
 await expect(page.locator('#live-guide-title')).toHaveText('Write your note.');
 await page.locator('#how-it-works').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.locator('#how-it-works').click();await expect(page.locator('.orientation-title')).toHaveText('Research notes');
 await page.getByRole('button',{name:'Exit guide',exact:true}).click();
 await expect(page).toHaveURL(/#notebook$/);await expect(page.locator('#live-guide-title')).toHaveText('Write your note.');
});

for(const width of [1440,390])test('compact account controls and tagline links work at '+width+'px',async({page},testInfo)=>{
 await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'reduce'});await useDataSaver(page);
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 await expect(page.locator('.home-weekly-card')).toBeVisible();
 await expect(page.locator('#how-it-works')).toHaveText('?');
 await expect(page.locator('#account-menu-panel')).toBeHidden();
 await page.locator('#account-menu-trigger').hover();await expect(page.locator('#account-menu-panel')).toBeVisible();
 await expect(page.locator('#account-handle')).toHaveText('browser_alice');await expect(page.locator('#history-button')).toBeVisible();
 await page.mouse.move(4,950);await expect(page.locator('#account-menu-panel')).toBeHidden();
 await page.locator('#account-menu-trigger').click();await expect(page.locator('#account-menu-panel')).toBeVisible();
 await page.keyboard.press('Escape');await expect(page.locator('#account-menu-panel')).toBeHidden();await expect(page.locator('#account-menu-trigger')).toBeFocused();
 for(const feature of ['bounties','challenge']){
  await page.locator('[data-home-feature="'+feature+'"]').hover();
  await expect(page.locator('#home-connections')).toHaveCount(0);
  const expected=feature==='challenge'?'.home-weekly':'#home-bounties';
  await expect(page.locator(expected)).toHaveClass(/is-feature-linked/);
  await expect(page.locator('.home-bounty.is-feature-linked')).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('tagline-'+feature+'-'+width+'.png'),fullPage:true});
 }
 await page.locator('[data-home-feature="challenge"]').click();await expect(page).toHaveURL(/#home$/);
 await expect(page.locator('.home-weekly')).toHaveClass(/is-feature-linked/);await expect(page.locator('[data-home-feature="challenge"]')).toHaveAttribute('aria-pressed','true');
 await page.locator('.home-weekly-card').click();await expect(page.locator('#challenge-start-dialog')).toBeVisible();
 await page.locator('#challenge-start-own').click();await expect(page.locator('#workspace')).toBeVisible();await expect(page.locator('#participation-guide')).toBeHidden();
 await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
 await accountAction(page,'#history-button');await expect(page.locator('#activity-workspace')).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
});

// Compare the cutout against Chrome's actual rounded-border hit testing, rather
// than deriving an expected path using the guide's own geometry functions.
test('guide washes use rounded shading within the full native bounds',async({page})=>{
 await useDataSaver(page);await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 const measurements=await page.evaluate(()=>{
  const target=document.createElement('button');target.type='button';target.textContent='Corner alignment';
  target.style.cssText='position:fixed;left:31.25px;top:240.5px;width:220.5px;height:121.25px;min-height:0;margin:0;padding:0;z-index:5;background:#fff;border:1px solid #777;';
  const panel=document.createElement('section');panel.className='live-guide';panel.textContent='Alignment check';
  const overlay=createGuideOverlay('corner-verification-mask');document.body.append(target,overlay,panel);
  try{const measurements=['6px','12px 26px 38px 4px / 8px 18px 24px 10px','50%','999px','80% 40% 65% 10% / 85% 70% 45% 20%'].map(radius=>{
   target.style.borderRadius=radius;positionGuide({panel,overlay,target});
   const box=target.getBoundingClientRect(),cutout=overlay.querySelector('.guide-cutouts'),ring=overlay.querySelector('.guide-ring').getBBox();
   return {radius,cornerFilled:cutout.isPointInFill(new DOMPoint(box.left+.25,box.top+.25)),bounds:[ring.x-box.left,ring.y-box.top,box.right-ring.x-ring.width,box.bottom-ring.y-ring.height],rounded:cutout.getAttribute('d').includes('A'),stroke:getComputedStyle(overlay.querySelector('.guide-ring')).stroke,fill:getComputedStyle(overlay.querySelector('.guide-ring')).fill};
  });
   const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.style.cssText=target.style.cssText;checkbox.style.width='20px';checkbox.style.height='20px';checkbox.style.borderRadius='0';document.body.append(checkbox);
   try{
    positionGuide({panel,overlay,target:checkbox});const box=checkbox.getBoundingClientRect(),cutout=overlay.querySelector('.guide-cutouts'),ring=overlay.querySelector('.guide-ring').getBBox();
    const point=new DOMPoint(box.left+1,box.top+1);
    measurements.push({radius:'Native checkbox',mismatches:cutout.isPointInFill(point)===(document.elementFromPoint(point.x,point.y)===checkbox)?[]:['Small checkbox corners were clipped'],bounds:[ring.x-box.left,ring.y-box.top,box.right-ring.x-ring.width,box.bottom-ring.y-ring.height],rounded:cutout.getAttribute('d').includes('A')});
   }finally{checkbox.remove();}
   return measurements;
  }finally{target.remove();overlay.remove();panel.remove();}
 });
 for(const measurement of measurements){
  if(measurement.radius!=='Native checkbox'){expect(measurement.cornerFilled,measurement.radius).toBe(false);expect(measurement.stroke).toBe('none');expect(measurement.fill).not.toBe('none');}expect(measurement.rounded).toBe(true);
  for(const inset of measurement.bounds)expect(inset,measurement.radius).toBeCloseTo(0,2);
 }
});

test('touching either tagline phrase reveals its panel without leaving Home',async({browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,reducedMotion:'reduce'});
 try{
  await useDataSaver(page);await page.goto('http://127.0.0.1:8029/#home');await signIn(page,'alice',{navigate:false});
  await expect(page.locator('.home-weekly-card')).toBeVisible();
  const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
  for(const feature of ['bounties','challenge']){
   const control=page.locator('[data-home-feature="'+feature+'"]'),panel=page.locator(feature==='bounties'?'#home-bounties':'.home-weekly');
   await control.tap();await expect(control).toHaveAttribute('aria-pressed','true');await expect(panel).toHaveClass(/is-feature-linked/);
   await expect(panel).toBeInViewport();await expect(page).toHaveURL(/#home$/);
  }
  await page.touchscreen.tap(4,4);await expect(page.locator('#home-entry')).not.toHaveAttribute('data-preview');expect(writes).toEqual([]);
 }finally{await page.close();}
});

for(const width of [1440,390,320])test('your points remain beside Leaderboard across pages at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:1000});await useDataSaver(page);
 // Explicit display fixture for a large balance; the real endpoint's owner and
 // authentication are retained. Backend tests check canonical score equality.
 await page.route('**/api/me/points',async route=>{
  const response=await route.fetch(),data=await response.json();data.account_points.points=12340;
  await route.fulfill({response,json:data});
 });
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 const badge=page.locator('#account-points');await expect(page.locator('#account-points-value')).toHaveText('12.3K');
 await expect(badge).toHaveAttribute('aria-label',/12,340/);
 await expect(page.locator('#theme-toggle')).toBeVisible();
 const bounds=await page.locator('.account-actions').evaluate(element=>[...element.children].filter(item=>item.getClientRects().length).map(item=>{const r=item.getBoundingClientRect();return {id:item.id,left:r.left,right:r.right,center:r.top+r.height/2};}));
 for(const box of bounds){expect(box.left).toBeGreaterThanOrEqual(0);expect(box.right).toBeLessThanOrEqual(width);expect(Math.abs(box.center-bounds[0].center)).toBeLessThan(3);}
 const tabs=await page.locator('.primary-nav .nav-button:visible').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().top));
 if(tabs.length)expect(Math.max(...tabs)-Math.min(...tabs)).toBeLessThan(2);
 await expect(page.locator('#nav-challenge')).toHaveCSS('font-family',/IBM Plex Sans/);
 await navigationAction(page, '#nav-about');await expect(badge).toBeVisible();await expect(page.locator('#account-points-value')).toHaveText('12.3K');
 await badge.click();await expect(page).toHaveURL(/#person\/browser_alice$/);await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
 await expect(badge).toBeVisible();await accountAction(page,'#logout-button');await expect(badge).toBeHidden();
});

for(const width of [1440,390])test('weekly cards keep their reward through compact refresh and route changes at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:1000});await useDataSaver(page);await page.emulateMedia({reducedMotion:'reduce'});
 // A display fixture enrolls the disposable portable scheme. It creates no
 // recoveries or awards; the full native preview checks real enrolled releases.
 let enrolled=[];
 await page.route('**/api/schemes*',async route=>{
  const response=await route.fetch(),data=await response.json();
  if(Array.isArray(data.schemes))enrolled=data.schemes.map(item=>({id:item.id,version:String(item.version)}));
  if(data.recovery_scoring)data.recovery_scoring={...data.recovery_scoring,record_advance_points:137,enrolled_releases:enrolled};
  await route.fulfill({response,json:data});
 });
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});
 const weekly=page.locator('.home-weekly-card');await expect(weekly).toBeVisible();
 await expect(page.locator('.home-weekly-points strong')).toHaveText('137');
 const href=await weekly.getAttribute('href');
 await expect(page.locator('.home-weekly')).not.toContainText('Try this challenge');
 const base=await page.locator('.home-weekly').evaluate(el=>getComputedStyle(el).backgroundColor);await page.locator('.home-weekly').hover();await expect(page.locator('.home-weekly')).toHaveCSS('background-color',base);await expect(page.locator('.home-weekly')).not.toHaveCSS('box-shadow','none');
 await page.locator('.home-weekly').getByRole('link',{name:'View all challenges'}).click();
 await expect(page).toHaveURL(/#challenge$/);await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
 await expect(page.locator('.catalogue-weekly-card')).toHaveAttribute('href',href);
 await expect(page.locator('.catalogue-weekly-points strong')).toHaveText('137');
 await page.evaluate(()=>refreshProfiles({background:true}));
 await expect(page.locator('.catalogue-weekly-points strong')).toHaveText('137');
 await page.locator('.catalogue-weekly-card').click();await expect(page.locator('#challenge-start-dialog')).toBeVisible();
 await page.locator('#challenge-start-dialog [data-close]').click();await navigationAction(page, '#nav-bounties');
 await page.locator('.site-header .brand-wordmark').click();await expect(page.locator('.home-weekly-points strong')).toHaveText('137');
 await expect(weekly).toHaveAttribute('href',href);
 await page.goto('/#home?guide=tour');await expect(page.locator('[data-contribution]')).toHaveCount(3);
 await expect(page.locator('#participation-guide')).toBeHidden();await expect(page.locator('.guide-overlay:not([hidden])')).toHaveCount(0);
 await page.locator('[data-contribution="note"]').click();await expect(page.locator('#live-guide-title')).toHaveText('Write your note.');
});

for(const width of [1440,390])test('Get started opens the first teaching challenge with its guide at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:1000});await useDataSaver(page);await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('/#home');await signIn(page,width===390?'discoverymobile':'discoverydesktop',{navigate:false});const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 const data=await(await page.request.get('/api/schemes?projection=navigation')).json(),intro=data.schemes.find(s=>s.id==='teaching-dh-kem');
 await page.locator('#home-get-started').click();await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');
 const route=new URL(page.url()).hash;expect(route).toContain('/teaching-dh-kem/'+intro.version+'?profile='+intro.profiles[0].id);
 await expect(page.locator('#participation-guide .live-guide-progress')).toHaveText('1 / 3');await expectSpotlightClear(page);
 const before=page.url();await page.locator('#how-it-works').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.locator('#how-it-works').click();await expect(page.locator('.orientation-title')).toHaveText('Choose a level');await expect(page).toHaveURL(before);
 await page.locator('.orientation-controls .guide-next').click();await expect(page.locator('.orientation-title')).toHaveText('Your challenge');await expect(page).toHaveURL(before);
 await page.getByRole('button',{name:'Exit guide',exact:true}).click();await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');expect(writes).toEqual([]);
});

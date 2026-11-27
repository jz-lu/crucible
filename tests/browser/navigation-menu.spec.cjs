const {test,expect,signIn,navigationAction}=require('./helpers.cjs');

for(const width of [320,390,768,1100,1440])test('navigation stays on one row and offers usable compact choices at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:900});await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});await page.evaluate(()=>document.fonts.ready);
 const layout=await page.evaluate(()=>{
  const center=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return r.top+r.height/2;};
  return {centers:['#site-brand','#account-points','#how-it-works','#account-menu-trigger','#theme-toggle'].map(center),overflow:document.documentElement.scrollWidth>innerWidth,
   left:document.querySelector('#site-brand').getBoundingClientRect().left,right:document.querySelector('.site-header').getBoundingClientRect().right-document.querySelector('#theme-toggle').getBoundingClientRect().right};
 });
 expect(layout.overflow).toBe(false);expect(Math.max(...layout.centers)-Math.min(...layout.centers)).toBeLessThan(1);
 expect(layout.left).toBeLessThanOrEqual(24);expect(layout.right).toBeLessThanOrEqual(24);
 await expect(page.locator('.site-header #theme-toggle')).toBeVisible();await expect(page.locator('#theme-toggle')).toHaveText('');
 await page.setViewportSize({width,height:600});await page.evaluate(()=>window.scrollTo(0,500));
 expect(await page.evaluate(()=>scrollY)).toBeGreaterThan(0);expect((await page.locator('.site-header').boundingBox()).y).toBe(0);
 const theme=await page.locator('html').getAttribute('data-theme');await page.locator('#theme-toggle').click();await expect(page.locator('html')).toHaveAttribute('data-theme',theme==='dark'?'light':'dark');
 await page.setViewportSize({width,height:900});await page.evaluate(()=>window.scrollTo(0,0));
 const toggle=page.locator('#primary-nav-toggle');
 if(width<=1180){
  await expect(toggle).toBeVisible();await expect(page.locator('#primary-nav-panel')).toBeHidden();
  await toggle.focus();await page.keyboard.press('ArrowDown');await expect(page.locator('#nav-challenge')).toBeFocused();
  await page.keyboard.press('Escape');await expect(toggle).toBeFocused();await expect(toggle).toHaveAttribute('aria-expanded','false');
  await navigationAction(page,'#nav-notebook');await expect(page.locator('#notebook-workspace')).toBeVisible();
  await expect(page.locator('#primary-nav-panel')).toBeHidden();await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(0);
  await navigationAction(page,'#nav-people');await expect(page.locator('#people-workspace')).toBeVisible();
  await toggle.click();await expect(page.locator('#nav-menu-people')).toHaveAttribute('aria-current','page');
  await page.locator('#account-menu-trigger').click();await expect(toggle).toHaveAttribute('aria-expanded','false');
  await page.locator('#account-menu-trigger').click();await toggle.click();
  await page.setViewportSize({width:1440,height:900});await expect(toggle).toBeHidden();
  await page.setViewportSize({width,height:900});await expect(page.locator('#primary-nav-panel')).toBeHidden();
 }else{
  await expect(toggle).toBeHidden();await expect(page.locator('#nav-challenge')).toBeVisible();await expect(page.locator('#nav-people')).toBeVisible();
 }
});

for(const width of [1440,390])test('Home stays free of a saved guide after brand navigation and refresh at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:900});await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});await page.locator('#home-get-started').click();
 await expect(page.locator('#participation-guide')).toBeVisible();
 await page.locator('#site-brand .brand-mark').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.locator('#site-brand .brand-mark').click();await expect(page.locator('#home-workspace')).toBeVisible();
 await expect(page.locator('#participation-guide')).toBeHidden();await expect(page.locator('.guide-overlay:not([hidden])')).toHaveCount(0);
 await page.reload();await expect(page.locator('#home-get-started')).toBeVisible();await expect(page.locator('#participation-guide')).toBeHidden();
 expect(await page.evaluate(()=>sessionStorage.getItem('crucible-participation-guide-v1'))).toBeNull();
 await expect(page.locator('#home-entry .home-first-flow')).toHaveCount(0);await expect(page.locator('#home-entry')).not.toContainText('101');
});

for(const width of [1440,390])test('feature overview starts a guided proposal and exposes points help at '+width+'px',async({page})=>{
 await page.setViewportSize({width,height:900});await page.emulateMedia({reducedMotion:'reduce',colorScheme:width===390?'dark':'light'});
 await page.goto('/#home');await signIn(page,'alice',{navigate:false});const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 await page.locator('#how-it-works').click();const proposal=page.locator('.orientation-feature.is-featured');
 await expect(proposal).toContainText('Propose a scheme');await proposal.click();
 await expect(page.locator('#proposal-workspace')).toBeVisible();await expect(page.locator('#live-guide-title')).toHaveText('Write your proposal note.');
 expect(writes).toEqual([]);await page.locator('#site-brand').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.locator('#site-brand').click();
 await page.locator('#how-it-works').click();await page.locator('.orientation-points').click();
 await expect(page.locator('#points-help-dialog')).toBeVisible();await expect(page.locator('#points-help-records')).toBeVisible();
 await page.getByRole('button',{name:'Close points explanation'}).click();await expect(page.locator('#home-workspace')).toBeVisible();
 await expect(page.locator('#account-points-popover')).toBeHidden();await page.locator('#account-points').hover();await page.locator('#account-points').focus();await expect(page.locator('#account-points-popover')).toBeVisible();
 await page.locator('#account-points-help').click();await expect(page.locator('#points-help-dialog')).toBeVisible();
 await page.getByRole('button',{name:'Close points explanation'}).click();await expect(page.locator('#account-points-popover')).toBeHidden();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await expect(page.locator('#account-points-popover')).toBeHidden();
 const initial=await page.locator('html').getAttribute('data-theme');await page.locator('#theme-toggle').click();
 const chosen=initial==='dark'?'light':'dark';await expect(page.locator('html')).toHaveAttribute('data-theme',chosen);
 await page.reload();await expect(page.locator('html')).toHaveAttribute('data-theme',chosen);await expect(page.locator('#participation-guide')).toBeHidden();
});

for(const reduced of [false,true])test('tabs sweep into the right-aligned icon menu with motion '+(reduced?'reduced':'enabled'),async({page},testInfo)=>{
 await page.setViewportSize({width:1220,height:900});await page.emulateMedia({reducedMotion:reduced?'reduce':'no-preference'});await page.goto('/#home');await signIn(page,'alice',{navigate:false});await page.evaluate(()=>document.fonts.ready);
 const tab=page.locator('#nav-notebook'),base=await tab.evaluate(el=>getComputedStyle(el).color);await tab.hover();await expect(tab).not.toHaveCSS('color',base);
 await page.mouse.move(3,850);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.setViewportSize({width:1170,height:900});const toggle=page.locator('#primary-nav-toggle');await expect(toggle).toBeVisible();await expect(toggle).toHaveText('');
 if(reduced)await expect(page.locator('.navigation-sweep')).toHaveCount(0);else{
  await expect(page.locator('.navigation-sweep')).toHaveCount(1);
  const motion=await page.locator('.navigation-sweep').evaluate(el=>{const a=el.firstElementChild.getAnimations()[0];const frames=a.effect.getKeyframes();for(const child of el.children)for(const animation of child.getAnimations()){animation.pause();animation.currentTime=180;}return {frames,interactive:el.querySelectorAll('a,button').length,hidden:el.getAttribute('aria-hidden'),inert:el.hasAttribute('inert')};});
  expect(motion.interactive).toBe(0);expect(motion.hidden).toBe('true');expect(motion.inert).toBe(true);expect(motion.frames.at(-1).transform).toMatch(/^translate\([\d.]+px/);
  await page.screenshot({path:testInfo.outputPath('navigation-sweep.png')});
 }
 const menu=await toggle.boundingBox(),points=await page.locator('#account-points').boundingBox();expect(points.x-menu.x-menu.width).toBeGreaterThan(0);expect(points.x-menu.x-menu.width).toBeLessThanOrEqual(28);
 await toggle.click();await expect(page.locator('.navigation-sweep')).toHaveCount(0);await expect(page.locator('#primary-nav-panel')).toBeVisible();await page.keyboard.press('Escape');await expect(toggle).toBeFocused();
 await page.locator('#account-points').hover();const help=page.locator('#account-points-help'),popover=page.locator('#account-points-popover');await expect(popover).toBeVisible();await help.hover();await expect(help).toHaveCSS('text-decoration-line','none');await expect(popover).toHaveCSS('box-shadow','none');await expect(help).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
 const popup=await popover.boundingBox();await expect.poll(async()=>{const help=await popover.boundingBox();return help.y-points.y-points.height;}).toBeCloseTo(14,0);expect(Math.abs(popup.x+popup.width/2-points.x-points.width/2)).toBeLessThan(1);
 if(!reduced)await expect(help.locator('.action-arrow')).toHaveCSS('transform','matrix(1, 0, 0, 1, 4, 0)');
 await page.setViewportSize({width:1220,height:900});await expect(toggle).toBeHidden();await expect(page.locator('#nav-notebook')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

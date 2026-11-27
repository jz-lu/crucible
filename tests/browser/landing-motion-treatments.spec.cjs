const {test,expect,signIn}=require('./helpers.cjs');
const sample=control=>control.evaluate(el=>{
 const panel=document.getElementById(el.getAttribute('aria-controls')),box=panel.getBoundingClientRect(),effect=getComputedStyle(panel,'::after');
 return {x:parseFloat(el.style.getPropertyValue('--phrase-x')),strength:Number(el.style.getPropertyValue('--phrase-strength')),u:parseFloat(panel.style.getPropertyValue('--preview-x')),v:parseFloat(panel.style.getPropertyValue('--preview-y')),panelStrength:Number(panel.style.getPropertyValue('--preview-strength')),mask:effect.maskImage,background:getComputedStyle(panel).backgroundColor,shadow:getComputedStyle(panel).boxShadow,width:box.width};
});
for(const width of [1440,390])test('localized glow and shimmer follow the cursor at '+width+'px',async({page},testInfo)=>{
 await page.setViewportSize({width,height:1000});await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/#home');await signIn(page,'alice',{navigate:false});await page.evaluate(()=>document.fonts.ready);
 await expect(page.locator('.home-weekly-card')).toBeVisible();await expect(page.locator('#home-connections')).toHaveCount(0);
 const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 const cta=page.locator('#home-get-started'),arrow=cta.locator('.action-arrow');
 await expect(cta).toHaveCSS('animation-name','get-started-beacon');await expect(arrow).toHaveCSS('animation-name','get-started-nudge');
 const nudge=await arrow.evaluate(el=>{const a=el.getAnimations()[0];a.pause();a.currentTime=0;const start=getComputedStyle(el).transform;a.currentTime=3360;const end=getComputedStyle(el).transform;a.play();return {start,end};});expect(nudge.start).not.toBe(nudge.end);
 const paths=[];
 for(const feature of ['bounties','challenge']){
  const control=page.locator('[data-home-feature='+feature+']'),baseline=await sample(control),bounds=await control.boundingBox();
  await control.hover({position:{x:bounds.width*.2,y:bounds.height*.5}});await expect(page.locator('#home-entry')).toHaveAttribute('data-preview',feature);
  await expect.poll(async()=>(await sample(control)).strength).toBeGreaterThan(.99);const first=await sample(control);
  expect(first.mask).toContain('radial-gradient');expect(first.background).toBe(baseline.background);if(feature==='bounties')expect(first.shadow).toBe(baseline.shadow);else expect(first.shadow).not.toBe('none');
  await control.hover({position:{x:bounds.width*.8,y:bounds.height*.5}});
  await expect.poll(async()=>Math.abs((await sample(control)).x-bounds.width*.8)).toBeLessThan(.2);const last=await sample(control);
  expect(Math.abs(last.u-first.u)).toBeGreaterThan(30);expect(last.u).toBeGreaterThan(10);expect(last.u).toBeLessThan(90);expect(last.v).toBeGreaterThan(10);expect(last.v).toBeLessThan(90);
  if(feature==='bounties'){expect(last.u).toBeGreaterThan(first.u);await expect(control.locator('.home-phrase-glow')).toBeVisible();}else{expect(last.u).toBeLessThan(first.u);await expect(control.locator('.home-phrase-dots')).toBeVisible();}
  await expect(control).toHaveCSS('text-decoration-line','none');paths.push(last);await page.screenshot({path:testInfo.outputPath(feature+'-localized.png'),fullPage:true,animations:'allow'});
 }
 expect(paths[0].v).not.toBe(paths[1].v);
 await page.mouse.move(3,3);await expect.poll(async()=>(await sample(page.locator('[data-home-feature=challenge]'))).panelStrength).toBe(0);
 const all=page.getByRole('link',{name:'View all bounties'});await all.hover();await expect(all).toHaveCSS('text-decoration-line','none');await expect(all.locator('.action-arrow')).toHaveCSS('transform','matrix(1, 0, 0, 1, 4, 0)');
 await page.locator('#how-it-works').click();const points=page.locator('.orientation-points');await expect(points).toContainText('How points work');await expect(points.locator('..')).toHaveClass('orientation-features');
 for(const [kind,selector] of [['bounties','#home-bounties-title svg'],['schemes','#home-weekly-title svg']]){
  const match=await page.evaluate(({kind,selector})=>{const shape=el=>[...el.querySelectorAll('path,circle')].map(n=>n.outerHTML).join('');return shape(document.querySelector('.orientation-feature-icon[data-icon='+kind+']'))===shape(document.querySelector(selector));},{kind,selector});expect(match).toBe(true);
 }
 await page.getByRole('button',{name:'Exit guide',exact:true}).click();
 await page.reload();await expect(page.locator('.home-weekly-card')).toBeVisible();await page.evaluate(()=>document.fonts.ready);
 const discovery=page.locator('[data-home-feature=bounties]'),bounds=await discovery.boundingBox();await discovery.hover({position:{x:bounds.width*.8,y:bounds.height*.5}});await expect.poll(async()=>(await sample(discovery)).strength).toBeGreaterThan(.99);const fresh=await sample(discovery);expect([fresh.u,fresh.v]).not.toEqual([paths[0].u,paths[0].v]);
 await page.emulateMedia({reducedMotion:'reduce'});await expect(cta).toHaveCSS('animation-name','none');await expect(arrow).toHaveCSS('animation-name','none');
 await page.mouse.move(3,3);await discovery.focus();await expect(discovery).toBeFocused();await expect.poll(async()=>(await sample(discovery)).panelStrength).toBe(1);await expect(discovery.locator('.home-phrase-glow')).toHaveCSS('animation-name','none');
 expect(writes).toEqual([]);
});

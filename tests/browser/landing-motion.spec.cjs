const {test, expect} = require('./helpers.cjs');

test('account controls and RESI appear only after the tagline finishes', async ({page}) => {
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/');
  const samples=await page.evaluate(()=>{
    const animations=document.getAnimations().filter(animation=>['scrutiny-reveal','landing-arrive'].includes(animation.animationName));
    for(const animation of animations)animation.pause();
    const reveal=animations.find(animation=>animation.animationName==='scrutiny-reveal'&&animation.effect.target.closest('#access-branding'));
    const finish=reveal.effect.getTiming().delay+reveal.effect.getTiming().duration;
    const card=animations.find(animation=>animation.effect.target.id==='access-workspace');
    const footer=animations.find(animation=>animation.effect.target.classList.contains('site-footer'));
    const stateAt=time=>{
      for(const animation of animations)animation.currentTime=time;
      return ['#access-workspace','.site-footer'].map(selector=>{
        const style=getComputedStyle(document.querySelector(selector));return {visibility:style.visibility,opacity:Number(style.opacity)};
      });
    };
    return {finish,cardStart:card.effect.getTiming().delay,footerStart:footer.effect.getTiming().delay,
      before:stateAt(0),afterText:stateAt(finish+1),complete:stateAt(3000)};
  });
  expect(samples.finish).toBeLessThan(600);
  expect(samples.cardStart).toBeGreaterThan(samples.finish);expect(samples.footerStart).toBeGreaterThan(samples.finish);
  expect(samples.before.every(item=>item.visibility==='hidden'&&item.opacity===0)).toBe(true);
  expect(samples.afterText.every(item=>item.visibility==='hidden'&&item.opacity===0)).toBe(true);
  expect(samples.complete.every(item=>item.visibility==='visible'&&item.opacity===1)).toBe(true);
});

for(const layout of ['centered','left'])for(const width of [1440,390]) {
  test('the '+layout+' flame stays centered on the key and fades softly over two seconds at '+width+'px',async({page},testInfo)=>{
    await page.setViewportSize({width,height:1000});
    await page.emulateMedia({reducedMotion:'no-preference',colorScheme:'light'});
    await page.goto('/#home?layout='+layout);
    await expect(page.locator('#access-workspace')).toHaveCSS('opacity','1');
    const fire=page.locator('.brand-fire-ghost'),key=page.locator('.brand-key-sun');
    await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/,{timeout:3000});
    const resting=await fire.boundingBox();
    await page.locator('#site-brand').click();
    await expect(page.locator('.site-header .brand')).toHaveClass(/brand-ignited/);
    const sample=async(elapsed,tips=elapsed)=>page.evaluate(({elapsed,tips})=>{
      const fire=document.querySelector('.brand-fire-ghost'),key=document.querySelector('.brand-key-sun'),shape=document.querySelector('.brand-flame-shape');
      const elements=[fire,key,...document.querySelectorAll('.brand-flame-shape'),document.querySelector('.brand-flame-grain')];
      for(const element of elements)for(const animation of element.getAnimations()){
        animation.pause();animation.currentTime=animation.animationName==='flame-flicker'?tips:elapsed;
      }
      const box=fire.getBoundingClientRect();
      const keyBox=key.getBoundingClientRect();
      return {height:box.height,width:box.width,bottom:box.bottom,center:box.left+box.width/2,keyCenter:keyBox.left+keyBox.width/2,
        opacity:Number(getComputedStyle(fire).opacity),key:Number(getComputedStyle(key).opacity),
        transform:getComputedStyle(fire).transform,d:getComputedStyle(shape).d,flickerDuration:shape.getAnimations()[0].effect.getTiming().duration};
    },{elapsed,tips});
    const rising=await sample(250,0);
    expect(rising.opacity).toBeGreaterThan(0);expect(rising.opacity).toBeLessThan(.5);
    expect(rising.key).toBeGreaterThan(0);expect(rising.key).toBeLessThan(.9);
    const initial=await sample(600,0),flicker=await sample(900,190),later=await sample(1100,394),held=await sample(1299,0);
    expect(initial.height).toBeGreaterThan(resting.height);expect(initial.height).toBeLessThan(resting.height*1.15);
    expect(initial.width).toBeGreaterThan(resting.width*.9);expect(initial.width).toBeLessThan(resting.width*1.15);
    expect(initial.flickerDuration).toBeLessThan(800);
    expect(flicker.d).not.toBe(initial.d);expect(later.d).not.toBe(flicker.d);
    expect(Math.abs(later.height-initial.height)).toBeGreaterThan(.5);
    for(const frame of [initial,flicker,later,held]){
      expect(frame.transform).toBe(initial.transform);
      expect(Math.abs(frame.bottom-resting.y-resting.height)).toBeLessThan(.1);
      expect(Math.abs(frame.center-frame.keyCenter)).toBeLessThan(.5);
      expect(frame.height).toBeLessThan(initial.height*1.12);
      expect(frame.opacity).toBe(.5);expect(frame.key).toBe(.9);
    }
    await expect(fire.locator('image')).toHaveCount(0);await expect(fire).toHaveCSS('filter','none');
    await expect(fire.locator('.brand-flame-core')).toHaveCSS('fill',/crucible-flame-fade/);
    await expect(fire.locator('.brand-flame-grain')).toHaveCSS('fill',/crucible-flame-dots/);
    await expect(fire.locator('.brand-flame-grain')).toHaveAttribute('mask','url(#crucible-flame-edge)');
    await expect(page.locator('#crucible-flame-dots circle')).toHaveCount(1);
    await expect(page.locator('#crucible-flame-fade stop').last()).toHaveAttribute('stop-opacity','0');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await sample(900,190);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.screenshot({path:testInfo.outputPath('logo-click-'+layout+'-'+width+'.png'),fullPage:true,animations:'allow'});
    const fading=await sample(1650,190),last=await sample(1999,190);
    expect(fading.opacity).toBeGreaterThan(0);expect(fading.opacity).toBeLessThan(.5);
    expect(fading.key).toBeGreaterThan(0);expect(fading.key).toBeLessThan(.9);
    expect(fading.transform).toBe(initial.transform);expect(last.opacity).toBeLessThan(.01);expect(last.key).toBeLessThan(.01);
    await fire.evaluate(element=>element.getAnimations().forEach(animation=>animation.finish()));
    await expect(page.locator('.site-header .brand')).not.toHaveClass(/brand-ignited/);
    await expect(fire).toHaveCSS('opacity','0');await expect(key).toHaveCSS('opacity','0');
    await expect(fire).toHaveCSS('transform','matrix(1, 0, 0, 1, 0, 0)');
    await page.locator('#site-brand').focus();await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  });
}

test('each reload plays one brief fire, while hover, focus and routes do not replay it',async({page})=>{
  await page.addInitScript(()=>{
    window.logoStarts=0;
    document.addEventListener('animationstart',event=>{if(event.animationName==='flame-visit')window.logoStarts++;});
  });
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/');
  const brand=page.locator('#site-brand'),fire=page.locator('.brand-fire-ghost');
  await expect.poll(()=>page.evaluate(()=>window.logoStarts)).toBe(1);
  await expect(brand).not.toHaveClass(/brand-ignited/,{timeout:3000});
  await expect(fire).toHaveCSS('opacity','0');await expect(page.locator('.brand-key-sun')).toHaveCSS('opacity','0');
  await page.locator('.site-header .brand-mark').hover();await brand.focus();
  await page.evaluate(()=>{location.hash='#about';});
  await expect(page).toHaveURL(/#about$/);
  expect(await page.evaluate(()=>window.logoStarts)).toBe(1);
  await expect(brand).not.toHaveClass(/brand-ignited/);
  await page.reload();await expect.poll(()=>page.evaluate(()=>window.logoStarts)).toBe(1);
  await expect(brand).not.toHaveClass(/brand-ignited/,{timeout:3000});
});

test('rapid logo clicks restart the full fire without stale cancellation and still navigate home',async({page})=>{
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/#about');
  const brand=page.locator('#site-brand'),fire=page.locator('.brand-fire-ghost');
  await expect(page.locator('#access-workspace')).toHaveCSS('opacity','1');
  await brand.click();await expect(page).toHaveURL(/#home$/);
  await fire.evaluate(element=>{
    window.previousLogoAnimation=element.getAnimations().find(animation=>animation.animationName==='flame-visit');
    window.previousLogoAnimation.currentTime=1700;
  });
  await brand.click();
  const current=await fire.evaluateHandle(element=>element.getAnimations().find(animation=>animation.animationName==='flame-visit'));
  expect(await current.evaluate(animation=>animation!==window.previousLogoAnimation&&animation.effect.getTiming().duration===2000)).toBe(true);
  await page.mouse.move(0,0);
  // Let cancellation of the old CSS animation dispatch before checking ownership.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  expect(await current.evaluate(animation=>animation.playState)).toBe('running');
  await expect(brand).toHaveClass(/brand-ignited/);
  await expect(brand).not.toHaveClass(/brand-ignited/,{timeout:3000});
  await expect(fire).toHaveCSS('opacity','0');await expect(page.locator('.brand-key-sun')).toHaveCSS('opacity','0');
});

test('reduced motion shows the full landing immediately and keeps the logo still',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto('/');
  for(const selector of ['#access-workspace','.site-footer']){
    await expect(page.locator(selector)).toHaveCSS('animation-name','none');
    await expect(page.locator(selector)).toHaveCSS('opacity','1');
  }
  await page.locator('.site-header .brand-mark').hover();await page.locator('#site-brand').click();
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  await expect(page.locator('.brand-ember-effect')).toBeHidden();
});

for(const width of [1440,390])for(const theme of ['light','dark']) {
  test('nearby tagline letters become dots without changing color at '+width+'px in '+theme,async({page},testInfo)=>{
    await page.setViewportSize({width,height:1000});
    await page.emulateMedia({reducedMotion:'no-preference',colorScheme:theme});
    await page.goto('/');await expect(page.locator('#access-workspace')).toHaveCSS('opacity','1');
    const wording=page.locator('#access-branding .scrutiny-wording'),letters=wording.locator('.scrutiny-letters'),dots=wording.locator('.scrutiny-dots');
    const original=await letters.evaluate(element=>getComputedStyle(element).color);
    await expect(letters).toHaveCSS('mask-image','none');
    const box=await wording.boundingBox();
    await wording.hover({position:{x:box.width*.45,y:box.height/2}});
    await expect(wording).toHaveClass(/is-dotted/);
    await expect.poll(()=>wording.evaluate(element=>parseFloat(element.style.getPropertyValue('--dot-x')))).toBeCloseTo(box.width*.45,0);
    const point=await wording.evaluate(element=>parseFloat(element.style.getPropertyValue('--dot-x')));
    await expect.poll(()=>wording.evaluate(element=>parseFloat(element.style.getPropertyValue('--dot-strength')))).toBeGreaterThan(.98);
    await expect(letters).toHaveCSS('color',original);await expect(dots).toHaveCSS('color',original);
    await expect(letters).toHaveCSS('mask-image',/radial-gradient/);await expect(dots).toHaveCSS('mask-image',/radial-gradient/);
    expect(await dots.evaluate(element=>getComputedStyle(element,'::before').backgroundImage)).toContain('radial-gradient');
    await expect(page.getByRole('heading',{name:'Find new cryptography. Test its limits.',exact:true})).toHaveCount(1);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.screenshot({path:testInfo.outputPath('tagline-dots-'+width+'-'+theme+'.png'),fullPage:true,animations:'allow'});
    await wording.hover({position:{x:box.width*.75,y:box.height/2}});
    await expect.poll(()=>wording.evaluate(element=>parseFloat(element.style.getPropertyValue('--dot-x')))).toBeGreaterThan(point+box.width*.25);
    await page.locator('.site-footer').hover();
    await expect(wording).not.toHaveClass(/is-dotted/);await expect(letters).toHaveCSS('mask-image','none');await expect(dots).toHaveCSS('opacity','0');
  });
}

test('tagline dots ease in before the pointer enters the text, and ease out on departure',async({page},testInfo)=>{
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/');
  await expect(page.locator('#access-workspace')).toHaveCSS('opacity','1');
  const wording=page.locator('#access-branding .scrutiny-wording'),box=await wording.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y-110);
  await expect(wording).not.toHaveClass(/is-dotted/);
  await page.mouse.move(box.x+box.width/2,box.y-45);
  await expect(wording).toHaveClass(/is-dotted/);
  const strength=()=>wording.evaluate(element=>parseFloat(element.style.getPropertyValue('--dot-strength'))||0);
  await expect.poll(strength).toBeGreaterThan(.25);
  const approaching=await strength();expect(approaching).toBeLessThan(.7);
  await page.screenshot({path:testInfo.outputPath('tagline-approach.png'),fullPage:true,animations:'allow'});
  // The nearest part of the glyphs is already inside the soft radial mask.
  expect(await wording.locator('.scrutiny-dots').evaluate(element=>getComputedStyle(element).maskImage)).toContain('100px');
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await expect.poll(strength).toBeGreaterThan(.98);
  await page.mouse.move(box.x+box.width/2,box.y-110);
  const fading=await strength();expect(fading).toBeGreaterThan(0);
  await expect(wording).not.toHaveClass(/is-dotted/);
  await wording.hover();await expect(wording).toHaveClass(/is-dotted/);
  await page.evaluate(()=>window.dispatchEvent(new PointerEvent('pointerout',{relatedTarget:null})));
  await expect(wording).not.toHaveClass(/is-dotted/);
});

test('browser-appearance tab icons are public SVG assets independent of the site theme',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce',colorScheme:'light'});await page.goto('/');
  for(const theme of ['light','dark']){
    await page.emulateMedia({colorScheme:theme});
    await expect(page.locator('#site-icon')).toHaveAttribute('href',new RegExp('/assets/crucible-icon-'+theme+'\\.svg\\?v=[a-f0-9]{24}$'));
    const href=await page.locator('#site-icon').getAttribute('href');
    expect(href).toMatch(new RegExp('/assets/crucible-icon-'+theme+'\\.svg\\?v=[a-f0-9]{24}$'));
    const response=await page.request.get(href);
    expect(response.status()).toBe(200);expect(response.headers()['content-type']).toContain('image/svg+xml');
    expect(response.headers()['cache-control']).toContain('immutable');
    expect(await response.text()).toContain('<path');expect(await response.text()).not.toContain('<image');
    await page.locator('#theme-toggle').click();
    await expect(page.locator('#site-icon')).toHaveAttribute('href',href);
  }
});

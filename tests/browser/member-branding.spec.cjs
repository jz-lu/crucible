const {test, expect, signIn} = require('./helpers.cjs');

// Permit the configured owned loopback app when a local run uses another port.
test.beforeEach(async ({page, baseURL}) => {
  const origin = new URL(baseURL).origin;
  await page.route(url => url.origin === origin, route => route.continue());
});

for (const width of [1440, 640, 390]) for (const theme of ['light', 'dark']) {
  test('members retain the shared logo, flame and dotted tagline at ' + width + 'px in ' + theme, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await page.emulateMedia({colorScheme:theme, reducedMotion:'no-preference'});
    await page.goto('/#home');
    await signIn(page, 'alice', {navigate:false});
    await expect(page.locator('#home-workspace')).toBeVisible();
    const mark = page.locator('#site-brand .brand-mark'), brand = page.locator('#site-brand');
    await expect(mark).toBeVisible();await expect(mark).toHaveAttribute('data-logo-ready', 'true');
    expect(await mark.evaluate(element => element.naturalWidth)).toBeGreaterThan(0);
    const wording = page.locator('#home-title .scrutiny-wording');
    const reveal = await wording.evaluate(element => element.getAnimations().map(animation => ({name:animation.animationName, duration:animation.effect.getTiming().duration})));
    expect(reveal.some(animation => animation.name === 'scrutiny-reveal' && animation.duration === 520)).toBe(true);
    await wording.evaluate(element => element.getAnimations().forEach(animation => animation.finish()));
    await expect(page.locator('#home-title')).toHaveText('Find new cryptography. Test its limits.');
    expect(await wording.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const baselineColor = await wording.evaluate(element => getComputedStyle(element).color);
    await mark.hover();await expect(brand).toHaveClass(/brand-ignited/);
    const measurements = await page.evaluate(() => {
      const mark = document.querySelector('#site-brand .brand-mark'), fire = document.querySelector('.brand-fire-ghost'), key = document.querySelector('.brand-key-sun');
      for (const element of [fire, key]) for (const animation of element.getAnimations()) { animation.pause();animation.currentTime = 900; }
      const image = mark.getBoundingClientRect(), overlay = document.querySelector('.brand-ember-effect').getBoundingClientRect();
      return {image:{x:image.x,y:image.y,width:image.width,height:image.height},overlay:{x:overlay.x,y:overlay.y,width:overlay.width,height:overlay.height},fire:getComputedStyle(fire).opacity,key:getComputedStyle(key).opacity,
        primary:getComputedStyle(document.documentElement).getPropertyValue('--primary').trim(),pigment:getComputedStyle(document.querySelector('.brand-flame-pigment')).stopColor};
    });
    expect(measurements.overlay).toEqual(measurements.image);
    expect(Number(measurements.fire)).toBe(.5);expect(Number(measurements.key)).toBe(.9);
    expect(measurements.primary).toBe(theme === 'light' ? '#28513f' : '#a6cbb7');
    expect(measurements.pigment).toBe(theme === 'light' ? 'rgb(40, 81, 63)' : 'rgb(166, 203, 183)');
    await page.screenshot({path:testInfo.outputPath('member-logo-'+width+'-'+theme+'.png'),fullPage:true,animations:'allow'});
    await page.locator('.brand-fire-ghost').evaluate(element => element.getAnimations().forEach(animation => animation.finish()));
    await expect(brand).not.toHaveClass(/brand-ignited/);
    const box = await wording.boundingBox();
    await page.mouse.move(box.x + box.width*.45, box.y - 45);
    await expect(wording).toHaveClass(/is-dotted/);
    await wording.hover({position:{x:box.width*.45,y:box.height/2}});
    await expect.poll(() => wording.evaluate(element => Number(element.style.getPropertyValue('--dot-strength')))).toBeGreaterThan(.98);
    await expect(wording.locator('.scrutiny-letters')).toHaveCSS('mask-image', /radial-gradient/);
    await expect(wording.locator('.scrutiny-letters')).toHaveCSS('color', baselineColor);
    await expect(wording.locator('.scrutiny-dots')).toHaveCSS('color', baselineColor);
    await page.screenshot({path:testInfo.outputPath('member-dots-'+width+'-'+theme+'.png'),fullPage:true,animations:'allow'});
    await page.mouse.move(0, 0);await expect(wording).not.toHaveClass(/is-dotted/);
    await mark.hover();await expect(brand).toHaveClass(/brand-ignited/);
    await page.mouse.move(0, 0);await expect(brand).not.toHaveClass(/brand-ignited/, {timeout:1000});
    await page.locator('#nav-challenge').click();await expect(page.locator('#catalogue-workspace')).toBeVisible();
    await mark.hover();await expect(brand).toHaveClass(/brand-ignited/);
  });
}

test('members with reduced motion see the complete logo and heading without hover animations', async ({page}) => {
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto('/#home');await signIn(page, 'alice', {navigate:false});
  const mark = page.locator('#site-brand .brand-mark');await expect(mark).toBeVisible();
  await mark.hover();await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  await expect(page.locator('.brand-ember-effect')).toBeHidden();
  await page.locator('#home-title .scrutiny-wording').hover();
  await expect(page.locator('#home-title .scrutiny-letters')).toHaveCSS('mask-image', 'none');
  await expect(page.locator('#home-title .scrutiny-wording')).toHaveCSS('animation-name', 'none');
});

for (const screen of ['beta landing', 'member home']) test('the key starts halfway through typing and fades slowly on the ' + screen, async ({page}, testInfo) => {
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.goto('/');
  if (screen === 'member home') await signIn(page, 'alice', {navigate:false});
  const brand=page.locator('#site-brand'),key=page.locator('.brand-key-sun');
  await expect(brand).toHaveClass(/brand-typing/);
  const samples=await page.evaluate(async selector=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const element=document.querySelector('.brand-key-sun');
    const reveal=document.querySelector(selector).getAnimations().find(animation=>animation.animationName==='scrutiny-reveal');
    const animation=element.getAnimations().find(animation=>animation.animationName==='key-typing-glow');
    const typing=reveal.effect.getTiming(),glow=animation.effect.getTiming();
    const startOffset=Number(animation.startTime)+glow.delay-(Number(reveal.startTime)+typing.delay+typing.duration/2);
    animation.pause();reveal.pause();
    const at=elapsed=>{animation.currentTime=glow.delay+elapsed;return Number(getComputedStyle(element).opacity);};
    return {startOffset,duration:glow.duration,before:at(-1),lit:at(500),held:at(2999),fading:at(3750),last:at(4499)};
  },screen==='member home'?'#home-title .scrutiny-wording':'#access-branding .scrutiny-wording');
  expect(Math.abs(samples.startOffset)).toBeLessThan(50);
  expect(samples.duration).toBe(4500);expect(samples.before).toBe(0);expect(samples.lit).toBeCloseTo(.8);
  expect(samples.held).toBeCloseTo(.8);expect(samples.fading).toBeGreaterThan(0);expect(samples.fading).toBeLessThan(.4);expect(samples.last).toBeLessThan(.01);
  await key.evaluate(element=>element.getAnimations().forEach(animation=>animation.finish()));
  await expect(brand).not.toHaveClass(/brand-typing/);await expect(key).toHaveCSS('opacity','0');
  await page.screenshot({path:testInfo.outputPath('typing-key-faded.png'),fullPage:true});
});

test('signup placeholders state the existing username and password rules separately', async ({page}) => {
  await page.goto('/#join?token=cri_' + 'v'.repeat(43));await page.locator('#auth-password-summary').click();
  await expect(page.locator('#auth-handle-label')).toHaveText('Crucible username');
  await expect(page.locator('#auth-handle')).toHaveAttribute('placeholder', '3–32 letters, numbers or _');
  await expect(page.locator('#auth-password')).toHaveAttribute('placeholder', '12–128 characters');
  await page.locator('#auth-handle').fill('crypto friend');
  expect(await page.locator('#auth-handle').evaluate(element => element.validity.patternMismatch)).toBe(true);
  await expect(page.locator('#auth-handle-hint')).toContainText('No spaces');
});

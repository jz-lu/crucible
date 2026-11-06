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
    await page.evaluate(() => document.fonts.ready);
    const alignment = await page.evaluate(() => {
      const center = element => { const box = element.getBoundingClientRect();return box.y + box.height / 2; };
      const wordmark = document.querySelector('#site-brand .brand-wordmark');
      return {wordmark:center(wordmark),mark:center(document.querySelector('#brand-animate')),
        navigation:[...document.querySelectorAll('.primary-nav .nav-button')].filter(element => element.getClientRects().length).map(center),
        account:center(document.querySelector('.account-actions')),font:getComputedStyle(wordmark).fontFamily,
        overflow:document.documentElement.scrollWidth > innerWidth};
    });
    expect(alignment.font).toContain('Georgia');expect(alignment.overflow).toBe(false);
    expect(Math.abs(alignment.wordmark - alignment.mark), 'wordmark and complete icon share a centre').toBeLessThan(1);
    const navigationCenter = width > 700 ? alignment.wordmark : alignment.navigation[0];
    for (const center of alignment.navigation) expect(Math.abs(center - navigationCenter), 'navigation labels share their row centre').toBeLessThan(1);
    if (width > 700) expect(Math.abs(alignment.account - alignment.wordmark), 'account controls share the title centre').toBeLessThan(1);
    const wording = page.locator('#home-title .scrutiny-wording');
    await expect(wording).toHaveCSS('animation-name','scrutiny-reveal');
    await expect(wording).toHaveCSS('animation-duration','0.52s');
    await wording.evaluate(element => element.getAnimations().forEach(animation => animation.finish()));
    await expect(page.locator('#home-title')).toHaveText('Find new cryptography. Test its limits.');
    expect(await wording.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    const baselineColor = await wording.evaluate(element => getComputedStyle(element).color);
    await page.locator('#brand-animate').click();await expect(brand).toHaveClass(/brand-ignited/);
    const measurements = await page.evaluate(() => {
      const mark = document.querySelector('#site-brand .brand-mark'), fire = document.querySelector('.brand-fire-ghost'), key = document.querySelector('.brand-key-sun');
      for (const element of [fire, key]) for (const animation of element.getAnimations()) { animation.pause();animation.currentTime = 500; }
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
    await page.locator('#brand-animate').click();await expect(brand).toHaveClass(/brand-ignited/);
    await page.mouse.move(0, 0);await expect(brand).not.toHaveClass(/brand-ignited/, {timeout:3000});
    await page.locator('#nav-challenge').click();await expect(page.locator('#catalogue-workspace')).toBeVisible();
    await page.locator('#brand-animate').click();await expect(brand).toHaveClass(/brand-ignited/);
  });
}

test('members with reduced motion see the complete logo and heading without hover animations', async ({page}) => {
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto('/#home');await signIn(page, 'alice', {navigate:false});
  const mark = page.locator('#site-brand .brand-mark');await expect(mark).toBeVisible();
  await mark.hover();await page.locator('#brand-animate').click();await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  await expect(page.locator('.brand-ember-effect')).toBeHidden();
  await page.locator('#home-title .scrutiny-wording').hover();
  await expect(page.locator('#home-title .scrutiny-letters')).toHaveCSS('mask-image', 'none');
  await expect(page.locator('#home-title .scrutiny-wording')).toHaveCSS('animation-name', 'none');
});

for (const width of [1440, 390]) test('entering beta glows once without replaying on member routes at ' + width + 'px', async ({page}) => {
  await page.setViewportSize({width, height:1000});
  await page.addInitScript(() => {
    window.logoStarts = 0;
    document.addEventListener('animationstart', event => {
      if (event.animationName === 'flame-visit') window.logoStarts++;
    });
  });
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto('/');
  await expect(page.locator('#access-workspace')).toHaveCSS('opacity','1');
  await expect.poll(() => page.evaluate(() => window.logoStarts)).toBe(1);
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  await signIn(page, 'alice', {navigate:false});
  await expect(page.locator('#home-workspace')).toBeVisible();
  await expect(page.locator('#site-brand')).toHaveClass(/brand-ignited/);
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-typing/);
  await expect.poll(() => page.evaluate(() => window.logoStarts)).toBe(2);
  const timing = await page.locator('.brand-fire-ghost').evaluate(element => {
    const animation = element.getAnimations().find(item => item.animationName === 'flame-visit');
    const {delay, duration} = animation.effect.getTiming();
    return {delay, duration};
  });
  expect(timing).toEqual({delay:0, duration:2000});
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/, {timeout:3000});
  await expect(page.locator('.brand-key-sun')).toHaveCSS('opacity','0');
  await page.locator('#nav-challenge').click();await expect(page.locator('#catalogue-workspace')).toBeVisible();
  await page.getByRole('banner').getByRole('link', {name:'Crucible home', exact:true}).click();
  await expect(page.locator('#home-workspace')).toBeVisible();
  expect(await page.evaluate(() => window.logoStarts)).toBe(2);
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/);
  await page.reload();
  await expect(page.locator('#home-workspace')).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.logoStarts)).toBe(1);
  await expect(page.locator('#site-brand')).not.toHaveClass(/brand-ignited/, {timeout:3000});
  expect(await page.evaluate(() => window.logoStarts)).toBe(1);
  await page.locator('#logout-button').click();await expect(page.locator('#access-workspace')).toBeVisible();
  await signIn(page, 'alice', {navigate:false});
  await expect(page.locator('#home-workspace')).toBeVisible();
  await expect(page.locator('#site-brand')).toHaveClass(/brand-ignited/);
  await expect.poll(() => page.evaluate(() => window.logoStarts)).toBe(2);
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

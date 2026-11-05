const {test, expect} = require('./helpers.cjs');

for (const layout of ['centered', 'left']) for (const width of [1440, 390]) for (const theme of ['light', 'dark']) {
  test(layout + ' landing keeps one alignment at ' + width + 'px in ' + theme, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await page.emulateMedia({colorScheme: theme, reducedMotion: 'reduce'});
    await page.goto('/#home?layout=' + layout);
    await expect(page.locator('#access-sign-in')).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-beta-landing', layout);
    await expect(page.locator('.site-header .brand-wordmark')).toHaveText('crucible');
    await expect(page.locator('.site-header .account-actions')).toBeHidden();
    await expect(page.locator('.site-header .brand-mark')).toHaveAttribute('data-logo-ready', 'true');
    const composition = await page.evaluate(() => {
      const textBox = selector => {
        const range = document.createRange();range.selectNodeContents(document.querySelector(selector));
        const box = range.getBoundingClientRect();return {left:box.left,center:box.left+box.width/2};
      };
      const brand = document.querySelector('.site-header .brand').getBoundingClientRect();
      const buttons = ['#access-create-account','#access-sign-in'].map(selector => document.querySelector(selector).getBoundingClientRect());
      const start = Math.min(...buttons.map(box=>box.left)),end = Math.max(...buttons.map(box=>box.right));
      return {brand:{left:brand.left,center:brand.left+brand.width/2},
        tagline:textBox('#access-branding .scrutiny-wording'),title:textBox('#access-title'),description:textBox('#access-description'),
        actions:{left:start,center:(start+end)/2},
        headerBorder:getComputedStyle(document.querySelector('.site-header')).borderBottomWidth,
        cardBorder:getComputedStyle(document.querySelector('#access-workspace')).borderTopWidth,
        overflow:document.documentElement.scrollWidth>innerWidth};
    });
    const axis = layout === 'left' ? 'left' : 'center';
    for (const part of ['tagline','title','description','actions']) expect(Math.abs(composition[part][axis]-composition.brand[axis]),part+' shares the brand alignment').toBeLessThanOrEqual(1);
    expect(composition).toMatchObject({headerBorder:'0px',cardBorder:'0px',overflow:false});
    await page.screenshot({path:testInfo.outputPath('landing-'+layout+'-'+width+'-'+theme+'.png'),fullPage:true});
    await page.locator('#access-sign-in').click();
    await expect(page.locator('#auth-title')).toHaveText('Welcome back');
    const title = await page.locator('#auth-title').evaluate(element => {
      const range=document.createRange();range.selectNodeContents(element);
      return {lines:range.getClientRects().length,overflow:element.scrollWidth>element.clientWidth};
    });
    expect(title).toEqual({lines:1,overflow:false});
    const fontsMatch=await page.evaluate(()=>{
      const tabs=getComputedStyle(document.querySelector('.nav-button'));
      return [...document.querySelectorAll('dialog h2')].every(element=>{
        const heading=getComputedStyle(element);return heading.fontFamily===tabs.fontFamily&&heading.fontWeight===tabs.fontWeight;
      });
    });
    expect(fontsMatch).toBe(true);
    await expect(page.locator('#auth-github')).toHaveClass(/button-primary/);
    await page.keyboard.press('Escape');await expect(page.locator('#access-sign-in')).toBeFocused();
  });
}

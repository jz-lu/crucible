const {test, expect} = require('./helpers.cjs');

for (const width of [1280, 390]) {
  test('landing, invitation paste, and direct invite are distinct at ' + width + 'px', async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 720});
    await page.emulateMedia({reducedMotion: 'reduce', colorScheme: 'light'});
    await page.goto('/#home?layout=centered');
    await expect(page.locator('#access-create-account')).toBeVisible();
    await expect(page.locator('#access-existing-account')).toBeHidden();
    await expect(page.locator('#access-github')).toBeHidden();
    await page.screenshot({path: testInfo.outputPath('01-landing-' + width + '.png'), fullPage: true});

    await page.locator('#access-create-account').click();
    await expect(page.locator('#auth-invitation-form')).toBeVisible();
    await expect(page.locator('#auth-sign-in-methods')).toBeHidden();
    await page.screenshot({path: testInfo.outputPath('02-paste-invitation-' + width + '.png'), fullPage: true});
    await page.keyboard.press('Escape');

    await page.goto('/#join?token=cri_' + 'v'.repeat(43));
    const existing = page.locator('#auth-existing-account');
    await expect(page.locator('#auth-dialog')).toBeVisible();
    await expect(page.locator('#auth-title')).toHaveText('Create an account');
    await expect(existing).toBeVisible();
    await expect(existing).toHaveCSS('border-top-width', '0px');
    const box = await existing.boundingBox(), actions = await page.locator('#auth-sign-in-methods').boundingBox();
    const password = await page.locator('#auth-password-summary').boundingBox();
    expect(box.width).toBeLessThan(actions.width * .8);expect(box.height).toBeLessThan(34);
    expect(box.y).toBeGreaterThanOrEqual(password.y + password.height);
    await expect(page.locator('#auth-github')).toBeVisible();
    await expect(page.locator('#auth-password-summary')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.screenshot({path: testInfo.outputPath('03-direct-invitation-' + width + '.png'), fullPage: true});

    await existing.click();
    await expect(page.locator('#auth-title')).toHaveText('Welcome back');
    await expect(page.locator('#auth-github')).toBeVisible();
    expect(new URL(page.url()).hash).not.toContain('token=');
  });
}

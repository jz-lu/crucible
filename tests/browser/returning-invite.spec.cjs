const {test, expect} = require('./helpers.cjs');

for (const [width, inDialog] of [[1440, false], [390, true]]) {
  test(`an unavailable invitation has a normal sign-in escape at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    // A synthetic unavailable token exercises the real strict invitation check.
    // GitHub is intercepted locally; the existing fixture account still uses
    // the application's real CSRF, OAuth binding and membership checks.
    const unavailable = 'x'.repeat(43), posts = [];
    page.on('request', request => {
      if (request.method() === 'POST') posts.push({path: new URL(request.url()).pathname, body: request.postDataJSON()});
    });
    await page.route('https://github.com/login/oauth/authorize?*', route => {
      const query = new URL(route.request().url()).searchParams;
      return route.fulfill({status: 302, headers: {location: 'http://127.0.0.1:8029/api/auth/github/callback?' +
        new URLSearchParams({state: query.get('state'), code: 'synthetic-browser-code'})}, body: ''});
    });
    await page.goto('/#join?token=' + unavailable);
    await expect(page.locator('#access-existing-account')).toBeVisible();
    if (inDialog) await page.locator('#access-sign-in').click();
    const failure = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/github/start');
    await page.locator(inDialog ? '#auth-github' : '#access-github').click();
    expect((await failure).status()).toBe(403);
    await expect(page.locator(inDialog ? '#auth-github-error' : '#access-error')).toHaveText('This invitation is invalid or unavailable.');
    await page.screenshot({path: testInfo.outputPath('unavailable-invitation.png'), fullPage: true});
    await page.locator(inDialog ? '#auth-existing-account' : '#access-existing-account').click();
    expect(new URL(page.url()).hash).toBe('#challenge');
    await expect(page.locator('#auth-title')).toHaveText('Welcome back.');
    await expect(page.locator('#auth-github')).toBeFocused();
    await expect(page.locator('#register-mode')).toBeHidden();
    await expect(page.locator('#auth-github-error')).toBeHidden();
    expect(posts).toHaveLength(1);
    expect(posts[0].body.invitation_token).toBe(unavailable);
    // Keyboard cancellation returns to a visible control, not the now-hidden
    // invitation escape. Reopening normal sign-in must not restore the token.
    await page.keyboard.press('Escape');
    await expect(page.locator('#access-github')).toBeFocused();
    await page.locator('#access-github').click();
    await expect(page.locator('#account-handle')).toHaveText('browser_alice');
    await expect(page.locator('#kem-catalogue')).toContainText('Diffie');
    expect(posts.map(row => row.path)).toEqual(['/api/auth/github/start', '/api/auth/github/start']);
    expect(posts[1].body).toEqual({mode: 'login', return_route: '#challenge'});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

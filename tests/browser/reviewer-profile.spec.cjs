const {test, expect, signIn} = require('./helpers.cjs');

for (const width of [1440, 390]) {
  test(`Reviewer profile keeps account controls without achievements at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, 'queuereviewer');
    await page.goto('/#person/browser_queuereviewer');
    const profile = page.locator('#person-content');
    await expect(profile.locator('.person-role-stat > dd')).toHaveText('Reviewer');
    await expect(profile.locator('.person-points-stat,.person-score-breakdown,.person-place-link,.person-proposals-stat,.person-section')).toHaveCount(0);
    await expect(page.locator('#edit-researcher-profile')).toBeVisible();
    await expect(page.locator('#profile-review-link')).toBeVisible();
    await expect(page.locator('#profile-account')).toBeVisible();
    await page.locator('#edit-researcher-profile').click();
    await expect(page.locator('#researcher-position')).toBeEnabled();
    await page.locator('#researcher-display-name').fill('Reviewer Example');
    await page.locator('#researcher-affiliation').fill('Example Institute');
    await page.locator('#save-researcher-profile').click();
    await expect(page.locator('#researcher-profile-status')).toHaveText('Profile saved.');
    await page.locator('#close-researcher-profile').click();
    await expect(page.locator('#researcher-profile-summary')).toContainText('Reviewer Example');
    await expect(page.locator('#researcher-profile-summary')).toContainText('Example Institute');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('reviewer-profile.png'), fullPage:true});
    await page.getByRole('button', {name:'Leaderboard', exact:true}).click();
    await expect(page.locator('#people-content .person-link').first()).toBeVisible();
    await expect(page.locator('#people-content')).not.toContainText('browser_queuereviewer');
  });
  test(`Other participants see reviewer details without owner controls or achievements at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page);
    await page.goto('/#person/browser_queuereviewer');
    const profile = page.locator('#person-content');
    await expect(profile.locator('.person-role-stat > dd')).toHaveText('Reviewer');
    await expect(profile.locator('.person-points-stat,.person-score-breakdown,.person-place-link,.person-proposals-stat,.person-section')).toHaveCount(0);
    await expect(page.locator('#edit-researcher-profile')).toBeHidden();
    await expect(page.locator('#profile-review-link')).toBeHidden();
    await expect(page.locator('#profile-account')).toBeHidden();
  });
}

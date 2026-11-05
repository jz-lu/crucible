const {test, expect, signIn} = require('./helpers.cjs');

for (const width of [1440, 390]) {
  test(`Reviewer profile has a role instead of a competitive score at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await signIn(page, 'queuereviewer');
    await page.goto('/#person/browser_queuereviewer');
    const profile = page.locator('#person-content');
    await expect(profile.locator('.person-role-stat > dd')).toHaveText('Reviewer');
    await expect(profile.locator('.person-points-stat,.person-score-breakdown,.person-place-link')).toHaveCount(0);
    await expect(profile.locator('.person-proposals-stat > dt')).toHaveText('Accepted proposals');
    await expect(profile.locator('#personal-records')).toBeVisible();
    await expect(profile.locator('#recent-solves')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('reviewer-profile.png'), fullPage:true});
    await page.getByRole('button', {name:'Leaderboard', exact:true}).click();
    await expect(page.locator('#people-content .person-link').first()).toBeVisible();
    await expect(page.locator('#people-content')).not.toContainText('browser_queuereviewer');
  });
}

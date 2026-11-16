const {test, expect, signIn, holdResponse} = require('./helpers.cjs');
const {readdirSync, readFileSync} = require('node:fs');
const {join} = require('node:path');
const outbox = () => process.env.CRUCIBLE_BROWSER_OUTBOX;
const signupURL = () => JSON.parse(readFileSync(join(outbox(), 'signup.json'), 'utf8')).url;

for (const width of [1440, 390]) test('one shared link admits a verified new account at ' + width + 'px', async ({page}, testInfo) => {
  await page.setViewportSize({width, height:1000});
  await page.goto(signupURL());
  await expect(page.locator('#auth-title')).toHaveText('Join Crucible');
  await expect(page.locator('#auth-signup-expiry')).toContainText('keeps access');
  await expect(page.locator('.primary-nav')).toBeHidden();
  const recipient = 'shared-' + width + '@example.invalid';
  await page.locator('#auth-signup-email').fill(recipient);
  await page.screenshot({path:testInfo.outputPath('shared-signup-email.png'),fullPage:true});
  await page.locator('#auth-signup-submit').click();
  await expect(page.locator('#auth-title')).toHaveText('Create an account');
  await expect(page).toHaveURL(/#join\?token=cri_/);
  await page.locator('#auth-password-summary').click();
  await page.locator('#auth-handle').fill('shared_signup_' + width);
  await page.locator('#auth-password').fill('Synthetic shared signup password');
  const before = new Set(readdirSync(outbox()));
  await page.locator('#auth-submit').click();
  await expect(page.locator('#auth-title')).toHaveText('Verify your email');
  const delivered = () => readdirSync(outbox()).filter(name => name.endsWith('.eml') && !before.has(name))
    .map(name => readFileSync(join(outbox(), name), 'utf8')).find(message => message.includes(recipient));
  await expect.poll(delivered).toBeTruthy();
  await expect(page.locator('.primary-nav')).toBeHidden();
  const code = delivered().match(/verification code is:\s*(\d{8})/)[1];
  await page.locator('#access-code').fill(code);
  await page.locator('#access-code-form button[type=submit]').click();
  await expect(page.locator('#auth-title')).toHaveText("You're in.");
  await page.locator('#auth-enter').click();
  await expect(page.locator('#account-handle')).toHaveText('shared_signup_' + width);
  await expect(page.locator('#home-workspace')).toBeVisible();
  await expect(page.locator('#signup-links-button')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('leaving signup while inspection is pending cannot reopen its dialog', async ({page}) => {
  const pending = await holdResponse(page, url => url.pathname === '/api/access/signup-links/inspect');
  try {
    await page.goto(signupURL());await pending.arrived;
    await page.evaluate(() => {location.hash = '#home';});
    await expect(page.locator('#auth-dialog')).not.toBeVisible();
    pending.release();await pending.delivered;
    await expect(page.locator('#auth-dialog')).not.toBeVisible();
  } finally { pending.release();await pending.remove(); }
});

test('an existing member opening a shared link returns to their workspace', async ({page}) => {
  await signIn(page, 'alice');await page.goto(signupURL());
  await expect(page.locator('#notebook-workspace')).toBeVisible();
  await expect(page).not.toHaveURL(/#signup/);
  await expect(page.locator('#auth-dialog')).not.toBeVisible();
  await expect(page.locator('#account-handle')).toHaveText('browser_alice');
});

test('closing a pending email request leaves signup usable and ignores the old response', async ({page}) => {
  const pending = await holdResponse(page, url => url.pathname === '/api/access/signup-links/invitations');
  try {
    await page.goto(signupURL());await expect(page.locator('#auth-signup-form')).toBeVisible();
    await page.locator('#auth-signup-email').fill('reopened@example.invalid');
    await page.locator('#auth-signup-submit').click();await pending.arrived;
    await page.locator('#auth-dialog [data-close]').click();
    await page.locator('#access-create-account').click();
    await expect(page.locator('#auth-signup-form')).toBeVisible();
    pending.release();await pending.delivered;
    await expect(page.locator('#auth-signup-submit')).toBeEnabled();
    await expect(page.locator('#auth-signup-form')).toBeVisible();
    await page.locator('#auth-signup-email').fill('reopened@example.invalid');
    await page.locator('#auth-signup-submit').click();
    await expect(page.locator('#auth-title')).toHaveText('Create an account');
  } finally { pending.release();await pending.remove(); }
});

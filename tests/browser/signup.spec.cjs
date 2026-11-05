const {test, expect} = require('./helpers.cjs');
const {readdirSync, readFileSync} = require('node:fs');
const {join} = require('node:path');
const password = 'Synthetic signup verification password';

test('a pasted expired invitation reports expiry and keeps existing sign-in available', async ({page}) => {
  await page.goto('/');
  await page.locator('#access-create-account').click();
  const origin = new URL(page.url()).origin;
  await page.locator('#auth-invitation-link').fill(origin + '/#join?token=cri_' + 'e'.repeat(43));
  await page.locator('#auth-invitation-form button[type=submit]').click();
  await expect(page.locator('#auth-title')).toHaveText('Invitation expired');
  await expect(page.locator('#auth-issue-signin')).toBeVisible();
  await expect(page.locator('#register-mode')).toBeHidden();
});

test('a new password account completes real mailbox verification using local test mail', async ({page}, testInfo) => {
  const outbox = process.env.CRUCIBLE_BROWSER_OUTBOX;
  expect(outbox).toBeTruthy();
  await page.goto('/');
  await page.locator('#access-create-account').click();
  const origin = new URL(page.url()).origin;
  await page.locator('#auth-invitation-link').fill(origin + '/#join?token=cri_' + 'm'.repeat(43));
  await page.locator('#auth-invitation-form button[type=submit]').click();
  await page.locator('#auth-password-summary').click();
  await page.locator('#auth-handle').fill('mail_flow_preview');
  await page.locator('#auth-password').fill(password);
  const before = new Set(readdirSync(outbox));
  await page.locator('#auth-submit').click();
  await expect(page.locator('#auth-title')).toHaveText('Verify your email');
  const delivered = () => readdirSync(outbox).filter(name => name.endsWith('.eml') && !before.has(name))
    .map(name => readFileSync(join(outbox, name), 'utf8'))
    .find(message => message.includes('invite-verification@example.invalid'));
  await expect.poll(delivered).toBeTruthy();
  const code = delivered().match(/verification code is:\s*(\d{8})/)[1];
  await page.locator('#access-code').fill(code);
  await page.locator('#access-code-form button[type=submit]').click();
  await expect(page.locator('#auth-title')).toHaveText("You're in.");
  await page.locator('#auth-enter').click();
  await expect(page.locator('#access-workspace')).toBeHidden();
  await expect(page.locator('#account-handle')).toHaveText('mail_flow_preview');
  await expect(page.locator('.primary-nav')).toBeVisible();
  await page.screenshot({path: testInfo.outputPath('signup-complete.png'), fullPage: true});
});

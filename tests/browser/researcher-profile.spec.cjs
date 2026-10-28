const {randomUUID} = require('node:crypto');
const {test, expect, signIn, holdResponse} = require('./helpers.cjs');
const fields = ['display_name', 'affiliation', 'position', 'website'];

async function owner(page) {
  await signIn(page);
  await page.locator('#account-handle').click();
  await expect(page.locator('#person-content')).toHaveAttribute('aria-busy', 'false');
  const response = await page.request.get('/api/me/profile');
  expect(response.ok()).toBeTruthy();
  return (await response.json()).profile;
}
async function restore(page, original) {
  const [session, response] = await Promise.all([page.request.get('/api/session'), page.request.get('/api/me/profile')]);
  expect(session.ok()).toBeTruthy(); expect(response.ok()).toBeTruthy();
  const result = await page.request.put('/api/me/profile', {headers: {Origin: new URL(page.url()).origin, 'X-CSRF-Token': (await session.json()).csrf_token},
    data: {...Object.fromEntries(fields.map(key => [key, original[key]])), expected_revision: (await response.json()).profile.revision, idempotency_key: randomUUID()}});
  expect(result.ok()).toBeTruthy();
}
async function edit(page) {
  await page.locator('#edit-researcher-profile').click();
  await expect(page.locator('#researcher-affiliation')).toBeEnabled();
}

for (const width of [1440, 390]) {
  test(`optional researcher profile preserves identity, draft and responsive layout at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 900});
    const original = await owner(page);
    try {
      await expect(page.locator('#account-handle')).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('#nav-people')).not.toHaveAttribute('aria-current', 'page');
      const headerHeight = await page.locator('.site-header').evaluate(node => node.getBoundingClientRect().height);
      await page.locator('#nav-people').click();
      await expect(page.locator('#nav-people')).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('#account-handle')).not.toHaveAttribute('aria-current', 'page');
      await page.locator('#account-handle').click();
      await expect(page.locator('#account-handle')).toHaveAttribute('aria-current', 'page');
      expect(await page.locator('.site-header').evaluate(node => node.getBoundingClientRect().height)).toBe(headerHeight);
      const help = page.locator('.person-score-breakdown > summary');
      await expect(help).toHaveAccessibleName('Points breakdown');
      await expect(help).toHaveText('?');
      await help.focus(); await page.keyboard.press('Enter');
      await expect(page.locator('.person-score-help-panel')).toBeVisible();
      await page.evaluate(() => loadPerson(state.personHandle, {preserve: true}));
      await expect(help).toBeFocused();
      await expect(page.locator('.person-score-help-panel')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.screenshot({path: testInfo.outputPath('profile-points-help.png'), fullPage: true});
      await page.keyboard.press('Escape');
      await expect(page.locator('.person-score-help-panel')).toBeHidden();
      await expect(help).toBeFocused();
      await help.click(); await page.locator('#person-title').click();
      await expect(page.locator('.person-score-help-panel')).toBeHidden();
      await edit(page);
      await page.locator('#researcher-affiliation').fill('Example Institute for Cryptographic Research');
      await page.locator('#researcher-position').fill('Research fellow');
      await page.locator('#researcher-display-name').fill('Alice Example');
      await page.locator('#researcher-website').fill('https://example.org/research');
      // A real score read must not replace the editor or its focused draft.
      await page.locator('#researcher-position').focus();
      await page.evaluate(() => loadPerson(state.personHandle, {preserve: true}));
      await expect(page.locator('#researcher-position')).toBeFocused();
      await expect(page.locator('#researcher-affiliation')).toHaveValue('Example Institute for Cryptographic Research');
      await page.locator('#nav-about').click();
      await page.locator('#account-handle').click();
      await edit(page);
      await expect(page.locator('#researcher-display-name')).toHaveValue('Alice Example');
      await page.locator('#save-researcher-profile').click();
      await expect(page.locator('#researcher-profile-status')).toHaveText('Profile saved.');
      await expect(page.locator('#person-title')).toHaveText('browser_alice');
      await expect(page.locator('#account-handle')).toHaveText('browser_alice');
      const summary = page.locator('#researcher-profile-summary');
      await expect(summary).toContainText('Example Institute for Cryptographic Research · Research fellow');
      await expect(summary).toContainText('Self-reported');
      await expect(summary.getByRole('link')).toHaveAttribute('href', 'https://example.org/research');
      await expect(summary.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer nofollow');
      await expect(page.locator('#researcher-profile-form')).toContainText('Visible to other Crucible members');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.screenshot({path: testInfo.outputPath('researcher-profile-editor.png'), fullPage: true});
      await page.locator('#close-researcher-profile').click();
      await expect(page.locator('#edit-researcher-profile')).toBeFocused();
      await page.screenshot({path: testInfo.outputPath('researcher-profile-saved.png'), fullPage: true});
      await page.evaluate(() => loadPerson(state.personHandle, {preserve: true}));
      await page.locator('#nav-about').click();
      const fresh = await holdResponse(page, url => url.pathname === '/api/people/browser_alice');
      try {
        await page.locator('#account-handle').click(); await fresh.arrived;
        await expect(summary).toBeVisible(); await expect(summary).toContainText('Example Institute for Cryptographic Research');
        fresh.release(); await fresh.delivered;
        await expect(page.locator('#person-content')).toHaveAttribute('aria-busy', 'false');
        await expect(summary).toBeVisible();
      } finally {fresh.release(); await fresh.remove();}
      // Another member's page uses its existing detail payload, no owner GET.
      const extra = []; page.on('request', req => {if (new URL(req.url()).pathname === '/api/me/profile') extra.push(req.url());});
      await page.goto('/#person/browser_bob');
      await expect(page.locator('#person-title')).toHaveText('browser_bob');
      await expect(page.locator('#nav-people')).not.toHaveAttribute('aria-current', 'page');
      await expect(page.locator('#account-handle')).not.toHaveAttribute('aria-current', 'page');
      await expect(page.locator('#person-content')).toHaveAttribute('aria-busy', 'false');
      await expect(page.locator('#edit-researcher-profile')).toBeHidden();
      expect(extra).toEqual([]);
    } finally {await restore(page, original);}
  });
}

test('uncertain profile save replays the same write, while a revision conflict retains the draft for explicit confirmation', async ({page}) => {
  const original = await owner(page);
  const writes = []; let interrupt = true;
  const handler = async route => {
    if (route.request().method() !== 'PUT') return route.fallback();
    writes.push(route.request().postData());
    if (!interrupt) return route.fallback();
    interrupt = false;
    const response = await route.fetch(); // Real synthetic save succeeds; its response is lost.
    expect(response.ok()).toBeTruthy(); await route.abort('failed');
  };
  await page.route('**/api/me/profile', handler);
  try {
    await edit(page);
    await page.locator('#researcher-affiliation').fill('Saved once');
    await page.locator('#save-researcher-profile').click();
    await expect(page.locator('#save-researcher-profile')).toHaveText('Retry save');
    expect(writes).toHaveLength(1);
    await expect(page.locator('#researcher-affiliation')).toBeDisabled();
    await page.locator('#save-researcher-profile').click();
    await expect(page.locator('#researcher-profile-status')).toHaveText('Profile saved.');
    expect(writes).toHaveLength(2); expect(writes[1]).toBe(writes[0]);
    const confirmed = (await (await page.request.get('/api/me/profile')).json()).profile;
    expect(confirmed.revision).toBe(original.revision + 1);
    await page.locator('#researcher-affiliation').fill('My retained draft');
    const session = await (await page.request.get('/api/session')).json();
    const other = await page.request.put('/api/me/profile', {headers: {Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token}, data: {
      ...Object.fromEntries(fields.map(key => [key, confirmed[key]])), affiliation: 'Concurrent editor',
      expected_revision: confirmed.revision, idempotency_key: randomUUID()}});
    expect(other.ok()).toBeTruthy();
    await page.locator('#save-researcher-profile').click();
    await expect(page.locator('#researcher-profile-conflict')).toContainText('Concurrent editor');
    await expect(page.locator('#researcher-affiliation')).toHaveValue('My retained draft');
    await expect(page.locator('#save-researcher-profile')).toHaveText('Save my changes');
    const unchanged = (await (await page.request.get('/api/me/profile')).json()).profile;
    expect(unchanged.affiliation).toBe('Concurrent editor');
    await page.locator('#save-researcher-profile').click();
    await expect(page.locator('#researcher-profile-status')).toHaveText('Profile saved.');
    expect((await (await page.request.get('/api/me/profile')).json()).profile.affiliation).toBe('My retained draft');
  } finally {await page.unroute('**/api/me/profile', handler); await restore(page, original);}
});

const {test, expect} = require('playwright/test');
const {randomUUID} = require('node:crypto');

for (const width of [1440, 390]) test(`real awards agree on Leaderboard and profiles at ${width}px`, async ({page}, testInfo) => {
  const errors = [], external = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === 'http://127.0.0.1:8030') return route.continue();
    external.push(url.origin); return route.abort();
  });
  await page.setViewportSize({width, height: 1000});
  await page.goto('/#people');
  await page.locator('#access-sign-in').click();
  await page.locator('#auth-form').getByLabel('Crucible username', {exact: true}).fill('solver');
  await page.locator('#auth-form').getByLabel('Password', {exact: true}).fill('Synthetic points fixture password');
  await page.locator('#auth-submit').click();
  await expect(page.locator('#account-handle')).toHaveText('solver');
  await expect(page.locator('#people-content .people-table tbody tr')).toHaveCount(3);
  page.on('request', request => {if (request.method() !== 'GET') writes.push(request.url());});

  const rows = page.locator('#people-content .people-table tbody tr');
  await expect(rows.nth(0).locator('td')).toHaveText(['1', 'solver', '2,403', '1']);
  await expect(rows.nth(1).locator('td')).toHaveText(['2', 'poster', '100', '0']);
  await expect(rows.nth(2).locator('td')).toHaveText(['3', 'supporter', '0', '0']);
  await expect(rows.getByRole('link', {name: /^reviewer2?$/})).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({path: testInfo.outputPath(`points-leaderboard-${width}.png`), fullPage: true});
  await rows.nth(0).getByRole('link', {name: 'solver', exact: true}).click();
  const points = page.locator('.person-stats > div').filter({has: page.getByLabel('Points breakdown', {exact: true})});
  await expect(points.locator(':scope > dd')).toHaveText('2,403');
  await page.getByLabel('Points breakdown', {exact: true}).click();
  for (const [label, amount] of [['Record breakthroughs','100'], ['Admitted schemes','2,100'], ['Research bounties','200'], ['Research notes','3']]) {
    await expect(page.locator('.person-score-parts > div').filter({has: page.getByText(label, {exact: true})}).locator('dd')).toHaveText(amount);
  }
  await page.screenshot({path: testInfo.outputPath(`points-breakdown-${width}.png`), fullPage: true});
  await page.reload();
  await expect(points.locator(':scope > dd')).toHaveText('2,403');
  await page.locator('#nav-people').click();
  await expect(rows.nth(0).locator('td')).toHaveText(['1', 'solver', '2,403', '1']);
  await page.locator('#refresh-people').click();
  await expect(page.locator('#refresh-people')).toBeEnabled();
  await expect(rows.nth(0).locator('td')).toHaveText(['1', 'solver', '2,403', '1']);
  expect(writes, 'Viewing and refreshing points must not create awards').toEqual([]);
  expect(errors, 'Uncaught browser errors').toEqual([]);
  expect(external, 'Unexpected external requests').toEqual([]);
});

test('an open Leaderboard refreshes after a real bounty award without double counting', async ({page, playwright}) => {
  const origin = 'http://127.0.0.1:8030', clients = [];
  async function actor(handle) {
    const client = await playwright.request.newContext({baseURL: origin, extraHTTPHeaders: {Origin: origin}});
    clients.push(client);
    let csrf = (await (await client.get('/api/session')).json()).csrf_token;
    const post = async (path, data, status = 200) => {
      const response = await client.post(path, {data, headers: {'X-CSRF-Token': csrf}});
      expect(response.status(), await response.text()).toBe(status);
      return response.json();
    };
    csrf = (await post('/api/auth/login', {handle, password: 'Synthetic points fixture password'})).csrf_token;
    return {client, post};
  }
  try {
    const solver = await actor('solver'), poster = await actor('poster'), reviewer = await actor('reviewer');
    await page.goto('/#people');
    await page.context().addCookies((await solver.client.storageState()).cookies);
    await page.reload();
    const row = page.locator('#people-content tbody tr').filter({has: page.getByRole('link', {name: 'solver', exact: true})});
    const assertPoints = async amount => {
      await page.locator('#refresh-people').click();
      await expect(page.locator('#refresh-people')).toBeEnabled();
      await expect(row.locator('td').nth(2)).toHaveText(amount.toLocaleString('en-US'));
      const profile = await (await solver.client.get('/api/people/solver')).json();
      expect(profile.person.points).toBe(amount);
      expect(Object.values(profile.person.score_breakdown).reduce((a, b) => a + b, 0)).toBe(amount);
    };
    await assertPoints(2403);

    const note = (await solver.post('/api/notebook/notes', {idempotency_key: randomUUID(), content: {
      title: 'Synthetic live points refresh evidence', claim: 'Disposable interface verification.',
      conditions: 'No scientific or performance claim.', body: 'Verify a real award reaches an already open Leaderboard.', tags: ['fixture']}})).note;
    const revision = {revision: note.revision, content_sha256: note.content_sha256};
    await solver.post('/api/notebook/notes/' + note.id + '/publication-requests', {...revision, idempotency_key: randomUUID()});
    await reviewer.post('/api/notebook/notes/' + note.id + '/publication-reviews', {...revision,
      idempotency_key: randomUUID(), decision: 'approved', reason: 'Synthetic workflow evidence is explicitly labeled.'});
    await assertPoints(2403);

    let value = await poster.post('/api/bounties', {idempotency_key: randomUUID(), terms: {
      title: 'Synthetic award refresh check', summary: 'Verify the cached Leaderboard updates.', background_tags: [], scope: 4,
      completion_criteria: 'Publish the exact synthetic note describing the points refresh verification.',
      verification_plan: 'An independent reviewer inspects the pinned note.', source_notes: []}});
    const base = '/api/bounties/' + value.bounty.id;
    const change = extra => ({expected_version: value.bounty.version, idempotency_key: randomUUID(), ...extra});
    value = await poster.post(base + '/admission-requests', change({terms_sha256: value.bounty.terms_sha256,
      policy_id: value.policy.id, confirm_cost_units: 1}));
    value = await reviewer.post(base + '/admission-decisions', change({terms_sha256: value.bounty.terms_sha256,
      decision: 'approved', reason: 'The disposable task has explicit scope and exact evidence.',
      checks: {scope: true, evidence: true, verification: true, duplicates: true, ownership: true}}));
    expect(value.bounty.reward_points).toBe(100);
    value = await solver.post(base + '/submissions', change({terms_sha256: value.bounty.terms_sha256,
      contribution_note: {note_id: note.id, ...revision}, claim: 'solution',
      explanation: 'This published synthetic note supplies the exact disposable evidence.'}));
    const path = base + '/submissions/' + value.receipt.submission_id + '/decisions';
    const retired = await poster.post(path, change({kind: 'poster', decision: 'approved', reason: 'The exact published note satisfies the synthetic criteria.'}), 410);
    expect(retired.detail).toContain('Poster completion decisions are retired');
    value = await (await solver.client.get(base)).json();
    expect(value.bounty.award).toBeNull();
    await assertPoints(2403);
    const finalBody = change({kind: 'pipeline', decision: 'approved', reason: 'Independent check of the exact synthetic evidence.'});
    value = await reviewer.post(path, finalBody);
    expect(value.bounty.award.points).toBe(100);
    // The already rendered row stays stable until explicit revalidation.
    await expect(row.locator('td').nth(2)).toHaveText('2,403');
    await assertPoints(2503);
    expect((await reviewer.post(path, finalBody)).receipt).toEqual(value.receipt);
    await page.reload();
    await expect(row.locator('td').nth(2)).toHaveText('2,503');
    await assertPoints(2503);
  } finally {
    for (const client of clients) await client.dispose();
  }
});

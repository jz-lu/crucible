const {test, expect, signIn} = require('./helpers.cjs');
test.use({actionTimeout: 10000});

// All task mutations below use the rendered form and the real disposable API.
// The contribution note is setup through the real notebook publication workflow;
// it is explicitly synthetic, and no participant or hosted data is involved.
test('a reviewed bounty spends explicit allowance and awards one independently approved solution', async ({page}) => {
  test.setTimeout(90000);
  const panel = () => page.locator('[data-nb="bounties"]:visible');
  async function as(actor) {
    if (await page.locator('#logout-button').isVisible()) {
      await page.locator('#logout-button').click();
      await expect(page.locator('#access-sign-in')).toBeVisible();
    }
    await signIn(page, actor);
  }
  async function write(path, click) {
    const receipt = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === 'POST');
    await click(); const response = await receipt;
    expect(response.status()).toBe(200); return response.json();
  }
  async function visit(id, reviewer = false, detail = '') {
    await page.goto('/' + (reviewer ? '#reviews?section=bounties' : '#notebook?view=bounties') + '&bounty=' + id + (detail ? '&detail=' + detail : ''));
    await expect(panel().getByRole('heading', {name:'Synthetic full bounty journey', exact:true})).toBeVisible();
  }
  await as('queueauthor');
  await page.locator('#nav-bounties').click();
  await expect(page).toHaveURL(/#bounties$/);
  await panel().getByRole('button', {name:'Propose a bounty', exact:true}).click();
  for (const [label,value] of [
    ['The task','Synthetic full bounty journey'],
    ['A short description','Disposable interface verification, not a research claim.'],
    ['What counts as completion?','A published synthetic note documenting this disposable workflow.'],
    ['How will it be verified?','The task poster and a distinct reviewer inspect the pinned synthetic evidence.']
  ]) await panel().getByLabel(label, {exact:true}).fill(value);
  const created = await write('/api/bounties', () => panel().getByRole('button', {name:'Save private draft',exact:true}).click());
  const id = created.bounty.id, base = '/api/bounties/' + id;
  expect(created.allowance.available_units).toBe(10);
  await panel().getByRole('button', {name:'← Your activity',exact:true}).click();
  const proposal = () => panel().locator('[data-bounty="' + id + '"]');
  await expect(proposal()).toContainText('Draft');
  await expect(proposal()).toBeFocused();
  await proposal().click();
  await panel().getByRole('button', {name:'Submit for review',exact:true}).click();
  const pending = await write(base + '/admission-requests', () => panel().locator('.bty-action-panel').getByRole('button', {name:'Submit for review',exact:true}).click());
  expect(pending.bounty.status).toBe('pending'); expect(pending.allowance.available_units).toBe(9);
  await panel().getByRole('button', {name:'← Your activity',exact:true}).click();
  await expect(proposal()).toContainText('Submitted for review');
  await page.reload();
  await expect(proposal()).toContainText('Submitted for review');

  await as('queuereviewer'); await visit(id, true);
  for (const box of await panel().getByRole('checkbox').all()) await box.check();
  await panel().getByLabel('Review findings', {exact:true}).fill('Synthetic task has explicit completion and verification criteria; no scientific claim is made.');
  const admitted = await write(base + '/admission-decisions', () => panel().getByRole('button', {name:'Record admission decision',exact:true}).click());
  expect(admitted.bounty.status).toBe('open'); expect(admitted.bounty.reward_points).toBe(100);

  await as('bob'); await visit(id);
  await panel().getByRole('button', {name:'Boost this bounty',exact:true}).click();
  const boosted = await write(base + '/boosts', () => panel().getByRole('button', {name:'Confirm boost',exact:true}).click());
  expect(boosted.bounty.reward_points).toBe(200); expect(boosted.allowance.available_units).toBe(9);

  await as('alice');
  const pin = await page.evaluate(async () => {
    const result = await api('/api/notebook/notes', {idempotency_key:crypto.randomUUID(), content:{
      title:'Synthetic bounty evidence', claim:'This note exists only in an isolated browser fixture.',
      conditions:'No mathematical or performance claim.', body:'The disposable participant workflow is being verified.', tags:['fixture']}});
    const note = result.note, ref = {note_id:note.id, revision:note.revision, content_sha256:note.content_sha256};
    await api('/api/notebook/notes/' + note.id + '/publication-requests', {...ref, note_id:undefined, idempotency_key:crypto.randomUUID()});
    return ref;
  });
  await as('queuereviewer');
  await page.evaluate(async pin => api('/api/notebook/notes/' + pin.note_id + '/publication-reviews', {
    revision:pin.revision, content_sha256:pin.content_sha256, idempotency_key:crypto.randomUUID(),
    decision:'approved', reason:'Disposable synthetic evidence is explicitly labeled; this is not technical certification.'}), pin);

  await as('alice'); await visit(id);
  await panel().getByRole('button', {name:'Link a contribution',exact:true}).click();
  await panel().getByLabel('Exact contribution-note citation', {exact:true}).fill(JSON.stringify(pin));
  await panel().getByRole('combobox', {name:'What does it offer?', exact:true}).selectOption('solution');
  await panel().getByLabel('What does it establish?', {exact:true}).fill('The exact published fixture note supplies the requested synthetic evidence.');
  const submitted = await write(base + '/submissions', () => panel().getByRole('button', {name:'Submit contribution',exact:true}).click());
  expect(submitted.bounty.award).toBeNull(); const submissionId = submitted.receipt.submission_id;

  await as('queueauthor'); await visit(id, false, 'contributions');
  await panel().getByLabel('Reason and evidence', {exact:true}).fill('The pinned synthetic note meets this disposable task’s completion criteria.');
  const accepted = await write(base + '/submissions/' + submissionId + '/decisions', () => panel().getByRole('button', {name:'Record poster decision',exact:true}).click());
  expect(accepted.bounty.award).toBeNull();
  await as('queuereviewer'); await visit(id, true, 'contributions');
  await panel().getByLabel('Reason and evidence', {exact:true}).fill('Independent inspection confirms the exact stated synthetic completion criteria.');
  const completed = await write(base + '/submissions/' + submissionId + '/decisions', () => panel().getByRole('button', {name:'Record pipeline decision',exact:true}).click());
  expect(completed.bounty.status).toBe('completed'); expect(completed.bounty.award.points).toBe(200);
  await expect(panel()).toContainText('Recorded award'); await expect(panel()).toContainText('200 points · browser_alice');
  await page.reload(); await expect(panel()).toContainText('200 points · browser_alice');
  await page.goto('/#person/browser_alice');
  await expect(page.locator('.person-stats > div').filter({has:page.getByLabel('Points breakdown', {exact:true})}).locator(':scope > dd')).toHaveText('200');
  await page.getByLabel('Points breakdown', {exact:true}).click();
  await expect(page.locator('.person-score-parts > div').filter({has:page.getByText('Research bounties', {exact:true})}).locator('dd')).toHaveText('200');
});

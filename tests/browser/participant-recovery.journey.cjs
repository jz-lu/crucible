const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {execFileSync} = require('node:child_process');
const {test, expect, signIn} = require('./helpers.cjs');

test('participant downloads public inputs, submits a real recovery and finds its durable receipt', async ({page}) => {
  // Only the disposable, deliberately weak DH fixture. The solver receives
  // exactly the downloaded public JSON, never a database or a private key.
  await signIn(page, 'queueauthor');
  await page.goto('/#challenge/teaching-dh-kem/4?profile=dh-22&view=challenge');
  await page.locator('#issue-button').click();
  await expect(page.locator('#challenge-active')).toBeVisible();
  await page.locator('#manual-tools > summary').click();
  const downloaded = page.waitForEvent('download');
  await page.locator('#download-button').click();
  const challengeJSON = readFileSync(await (await downloaded).path(), 'utf8');
  const candidate = execFileSync(process.env.CRUCIBLE_BROWSER_PYTHON || 'python', ['-B', '-c',
    'import json,sys; from scripts.solve_teaching_frontier import recover; print(recover(json.load(sys.stdin)))'],
    {cwd:resolve(__dirname, '../..'), input:challengeJSON, encoding:'utf8', timeout:15000}).trim();
  expect(candidate).toMatch(/^[0-9a-f]{64}$/);

  await page.locator('#open-submission').click();
  await page.locator('#candidate').fill(candidate);
  await page.locator('#method-name').fill('Disposable browser recovery journey');
  await page.locator('#method-pseudocode').fill('Apply baby-step giant-step to the public toy DH inputs, then the published transcript KDF. This is a disposable interface test.');
  await page.locator('#hardware').fill('CI test runner; toy-fixture validation only');
  await page.locator('#wall-seconds').fill('1');
  await page.locator('#precomputation').fill('None');
  await page.locator('#peak-memory').fill('Not measured');
  await page.locator('#compute-notes').fill('Synthetic browser journey; the entered duration is a fixture value, not a benchmark.');
  const submitted = page.waitForResponse(response => /\/api\/challenges\/[^/]+\/submit$/.test(new URL(response.url()).pathname));
  await page.locator('#submit-answer').click();
  const receipt = await submitted, body = await receipt.json();
  expect(receipt.status()).toBe(200); expect(body.submission.status).toBe('correct');
  expect(JSON.stringify(body)).not.toContain(candidate);
  await expect(page.locator('#submission-dialog')).toBeHidden();
  await expect(page.locator('#challenge-status')).toHaveText('Recovered');
  await expect(page.locator('#use-agent')).toHaveText('Inspect with an agent →');
  await page.locator('#use-agent').click();
  await expect(page.locator('#agent-instructions-text')).toContainText('cannot accept a new answer');

  await page.getByRole('button', {name:'My submissions', exact:true}).click();
  const record = page.locator('#history-content [data-submission-id]').filter({hasText:'Disposable browser recovery journey'});
  await expect(record).toHaveCount(1); await record.click();
  await expect(page.locator('#recovery-dialog')).toBeVisible();
  await expect(page.locator('#recovery-title')).toHaveText('Disposable browser recovery journey');
  await expect(page.locator('#recovery-content')).toContainText('Not measured');
  await expect(page.locator('#recovery-content')).not.toContainText(candidate);
});

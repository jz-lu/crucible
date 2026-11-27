const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {execFileSync} = require('node:child_process');
const {test, expect, signIn, useDataSaver} = require('./helpers.cjs');

// These are real submissions to the isolated, disposable browser server.
// The recovery solver sees only the public download. All prose explicitly
// identifies synthetic test data; no hosted research or accounts are used.
async function startChallenge(page, actor, approach) {
  await useDataSaver(page); await page.goto('/#home'); await signIn(page, actor, {navigate:false});
  await page.locator('#home-get-started').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');
  await page.locator('#participation-guide .button-primary').click();
  await expect(page.locator('#challenge-active')).toBeVisible();
  await expect(page.locator('#live-guide-title')).toHaveText('Choose how to work.');
  await page.locator('#participation-guide').getByRole('button',{name:approach==='agent'?'Use an agent':'Use my own tools',exact:true}).click();
}
function recover(publicJSON) {
  return execFileSync(process.env.CRUCIBLE_BROWSER_PYTHON || 'python', ['-B','-c',
    'import json,sys; from scripts.solve_teaching_frontier import recover; print(recover(json.load(sys.stdin)))'],
    {cwd:resolve(__dirname,'../..'),input:publicJSON,encoding:'utf8',timeout:15000}).trim();
}
async function startContribution(page, kind, actor = 'alice') {
  await useDataSaver(page); await page.goto('/#home'); await signIn(page, actor, {navigate:false});
  await page.goto('/#home?guide=tour'); await page.locator('[data-contribution="' + kind + '"]').click();
  if(kind==='note'||kind==='scheme')await page.locator('#participation-guide .button-primary').click();
}
async function writeNote(page, title) {
  const dialog=page.locator('[data-nb="dialog"]'); await expect(dialog).toBeVisible();
  for(const [name,value] of [['title',title],['claim','Synthetic guided interface verification; no research claim.'],
    ['conditions','Only the disposable local browser fixture.'],['body','This note checks the real save and submission workflow. It establishes no mathematical result.']])
    await dialog.locator('[name="'+name+'"]').fill(value);
  await expect(page.locator('#live-guide-title')).toHaveText('Save your private draft.');
  const saved=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/notebook/notes');
  await page.locator('[data-guide-action="save-note"]').click();const response=await saved;expect(response.status()).toBe(200);
  return (await response.json()).note;
}
async function publishNote(page) {
  await expect(page.locator('#live-guide-title')).toHaveText('Submit your saved note.');
  await page.locator('#participation-guide .button-primary').click();
  await expect(page.locator('.nb-publication-request')).toBeVisible();
  const dialog=page.locator('[data-nb="dialog"]');
  for(const box of await dialog.getByRole('checkbox').all()) if(await box.isVisible())await box.check();
  const sent=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/publication-requests'));
  await expect(page.locator('#participation-guide .button-primary')).toHaveText('Send publish request');
  await page.locator('#participation-guide .button-primary').click();const response=await sent;expect(response.status()).toBe(200);return response.json();
}
for(const width of [1440,390]) test('a follow-along manual guide ends in a verified real recovery at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});
  await startChallenge(page,width===1440?'queueauthor':'bob','manual');
  const download=page.waitForEvent('download');await page.locator('#participation-guide .button-primary').click();
  const candidate=recover(readFileSync(await(await download).path(),'utf8'));expect(candidate).toMatch(/^[a-f0-9]{64}$/);
  await expect(page.locator('#live-guide-title')).toHaveText('Recover the secret locally.');
  await page.reload();await expect(page.locator('#live-guide-title')).toHaveText('Recover the secret locally.');
  await page.locator('#participation-guide .button-primary').click();await expect(page.locator('#submission-dialog')).toBeVisible();
  await expect(page.locator('#live-guide-title')).toHaveText('Enter your recovered key.');
  await page.locator('#candidate').fill(candidate);await expect(page.locator('#live-guide-title')).toHaveText('Enter your recovered key.');
  await page.locator('#participation-guide .guide-next').click();await expect(page.locator('#live-guide-title')).toHaveText('Choose your method.');
  await page.locator('#participation-guide .guide-next').click();await expect(page.locator('#live-guide-title')).toHaveText('Name your method.');
  await page.locator('#method-name').fill('Synthetic guided public-input recovery '+width);
  await page.locator('#participation-guide .guide-next').click();await expect(page.locator('#live-guide-title')).toHaveText('Describe your method.');
  await page.locator('#method-pseudocode').fill('Baby-step giant-step on the downloaded toy DH inputs, followed by the published transcript KDF. Disposable UI verification.');
  await page.locator('#participation-guide .guide-next').click();await expect(page.locator('#live-guide-title')).toHaveText('Record your hardware.');
  for(const [id,value] of [['hardware','Disposable CI runner'],['workers','1'],['wall-seconds','1'],['precomputation','None'],['peak-memory','Not measured']]){await page.locator('#'+id).fill(value);await page.locator('#participation-guide .guide-next').click();}
  await expect(page.locator('#live-guide-title')).toHaveText('Review and submit your answer.');
  await page.locator('#participation-guide .guide-back').click();await expect(page.locator('#live-guide-title')).toHaveText('Record peak memory.');await expect(page.locator('#peak-memory')).toHaveValue('Not measured');await page.locator('#participation-guide .guide-next').click();
  await page.screenshot({path:testInfo.outputPath('guided-review-'+width+'.png'),fullPage:true});
  const submitted=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/submit')&&r.request().method()==='POST');
  await page.locator(width===390?'#submit-answer':'#participation-guide .button-primary').click();const response=await submitted,body=await response.json();
  expect(response.status()).toBe(200);expect(body.submission.status).toBe('correct');expect(JSON.stringify(body)).not.toContain(candidate);
  await expect(page.locator('#live-guide-title')).toHaveText('Recovery recorded.');
  const balance=await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/me/points');
  expect(balance.ok()).toBe(true);const score=(await balance.json()).account_points;
  await expect(page.locator('#account-points-value')).toHaveText(score.points.toLocaleString('en-US'));
  await page.locator('#participation-guide .button-primary').click();await expect(page.locator('#history-content')).toContainText('Synthetic guided public-input recovery '+width);
  await page.locator('#site-brand .brand-wordmark').click();await page.reload();await page.locator('#home-get-started').click();await expect(page.locator('#live-guide-title')).toHaveText('Let’s try a challenge.');await expect(page.locator('.live-guide-progress')).toHaveText('1 / 3');await expect(page.locator('#participation-guide .guide-next')).toHaveText('View result');
});

test('the agent guide notices a real result submitted by a separate worker',async({page,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await startChallenge(page,'alice','agent');
  await page.locator('#participation-guide .guide-next').click();await expect(page.locator('#live-guide-title')).toHaveText('Recover the secret locally.');
  expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('get --challenge-id');
  const downloaded=await page.request.get(await page.locator('#download-button').getAttribute('href'));expect(downloaded.ok()).toBe(true);
  const publicJSON=await downloaded.text(),challenge=JSON.parse(publicJSON).challenge,candidate=recover(publicJSON);
  const csrf=await page.evaluate(()=>state.csrf),origin=new URL(page.url()).origin,headers={'Origin':origin,'X-CSRF-Token':csrf};
  const method=await page.request.post('/api/methods',{headers,data:{name:'Synthetic separate worker',pseudocode:'Public-input toy DH solver; disposable guide-poll verification.'}});
  expect(method.ok()).toBe(true);const methodId=(await method.json()).method.id;
  const response=await page.request.post('/api/challenges/'+challenge.id+'/submit',{headers,data:{candidate,method_id:methodId,
    compute:{hardware:'Disposable CI runner',workers:1,wall_seconds:1,precomputation:'None',peak_memory:'Not measured'},idempotency_key:crypto.randomUUID()}});
  expect(response.ok()).toBe(true);expect((await response.json()).submission.status).toBe('correct');
  await expect(page.locator('#live-guide-title')).toHaveText('Recovery recorded.',{timeout:15000});
});

for(const width of [1440,390])test('the note guide follows a real private save through publication submission at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});await startContribution(page,'note');
  const note=await writeNote(page,'Synthetic guided note '+width);expect(note.publication_status).toBe('private');
  await expect(page.locator('#participation-guide')).not.toContainText('Submission recorded');
  const result=await publishNote(page);expect(result.note.id).toBe(note.id);
  await expect(page.locator('#live-guide-title')).toHaveText('Publication request recorded.');
  await page.screenshot({path:testInfo.outputPath('note-submitted-'+width+'.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('the scheme guide submits the exact saved proposal revision for real review',async({page})=>{
  await startContribution(page,'scheme','formsdesktop');const dialog=page.locator('[data-nb="dialog"]');
  const specification=execFileSync(process.env.CRUCIBLE_BROWSER_PYTHON||'python',['-B','-c',
    'import sys,json; sys.path.insert(0,"tests"); from crucible.protocol_specs import builtin_spec; from proposal_fixtures import correctness_review_spec,tier_design; s=builtin_spec("teaching-dh-kem","4"); s["tier_design"]=tier_design(); s=correctness_review_spec(s); s["name"]="Synthetic guided proposal"; print(json.dumps(s))'],
    {cwd:resolve(__dirname,'../..'),encoding:'utf8'});
  await dialog.locator('[name="title"]').fill('Synthetic guided proposal');await dialog.locator('[name="claim"]').fill('Disposable proposal-submission UI check.');
  await dialog.locator('[name="conditions"]').fill('Not novel research; isolated fixture only.');await dialog.locator('[name="body"]').fill('This fixture reuses the registered toy DH specification to exercise review intake.');
  await expect(page.locator('#live-guide-title')).toHaveText('Attach your scheme specification.');
  await dialog.locator('[name="kem-specification"]').setInputFiles({name:'synthetic-proposal.json',mimeType:'application/json',buffer:Buffer.from(specification)});
  await expect(page.locator('#live-guide-title')).toHaveText('Complete this citation.');
  for(const input of await dialog.locator('.nb-citation-row [aria-required="true"]').all()) {
    if(!(await input.inputValue()).trim())await input.fill('Reference retained from the registered fixture specification; no independent verification is claimed by this synthetic test.');
  }
  for(const input of await dialog.locator('.nb-citation-row input[name$="-url"]').all()) {
    const value=await input.inputValue();if(value.startsWith('/api/protocol/source?'))await input.fill(new URL(value,page.url()).href);
  }
  await expect(page.locator('#live-guide-title')).toHaveText('Save your private draft.');
  const saved=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/notebook/notes');
  await page.locator('#participation-guide .button-primary').click();const note=(await(await saved).json()).note;
  await expect(page.locator('#live-guide-title')).toHaveText('Submit your scheme for review.');await page.locator('#participation-guide .button-primary').click();
  await expect(page.locator('#live-guide-title')).toHaveText('Submit this revision for review.');await expect(page.locator('#participation-guide .button-primary')).toBeEnabled();
  const submitted=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/proposal-submissions'));
  await page.locator('#participation-guide .button-primary').click();const response=await submitted,body=await response.json();
  expect(response.status()).toBe(200);expect(body.proposal.source_note).toMatchObject({id:note.id,revision:note.revision});
  await expect(page.locator('#live-guide-title')).toHaveText('Scheme review submission recorded.');
});

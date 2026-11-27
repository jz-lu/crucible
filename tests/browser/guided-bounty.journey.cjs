const {test,expect,signIn,useDataSaver, accountAction, navigationAction} = require('./helpers.cjs');

// Set up an independently admitted, explicitly synthetic bounty and published
// evidence through the real APIs/UI in the disposable participant database.
// The guided participant then submits through the actual contribution form.
test('the bounty guide follows a participant through a real evidence submission',async({page})=>{
 test.setTimeout(90000);await useDataSaver(page);
 const panel=()=>page.locator('[data-nb="bounties"]:visible');
 async function as(actor){
  if(await page.locator('#account-menu').isVisible()){await accountAction(page, '#logout-button');await expect(page.locator('#access-sign-in')).toBeVisible();}
  await signIn(page,actor);
 }
 async function write(path,click){const received=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===path);await click();const response=await received;expect(response.status()).toBe(200);return response.json();}
 await as('queueauthor');await navigationAction(page, '#nav-bounties');await panel().getByRole('button',{name:'Propose a bounty',exact:true}).click();
 for(const [label,value]of [['The task','Synthetic guided bounty'],['A short description','Disposable follow-along interface verification.'],['What counts as completion?','A synthetic note recording the test workflow, with no research claim.'],['How will it be verified?','An independent reviewer inspects the exact cited test revision.']])await panel().getByLabel(label,{exact:true}).fill(value);
 const created=await write('/api/bounties',()=>panel().getByRole('button',{name:'Save private draft',exact:true}).click()),id=created.bounty.id;
 await panel().getByRole('button',{name:'Submit for review',exact:true}).click();
 await write('/api/bounties/'+id+'/admission-requests',()=>panel().locator('.bty-action-panel').getByRole('button',{name:'Submit for review',exact:true}).click());
 await as('alice');
 const pin=await page.evaluate(async()=>{
  const saved=await api('/api/notebook/notes',{idempotency_key:crypto.randomUUID(),content:{title:'Synthetic guided bounty evidence',claim:'Disposable guide verification.',conditions:'No mathematical claim.',body:'This note records an isolated interface test.',tags:['fixture']}});
  const note=saved.note,ref={note_id:note.id,revision:note.revision,content_sha256:note.content_sha256};
  await api('/api/notebook/notes/'+note.id+'/publication-requests',{revision:ref.revision,content_sha256:ref.content_sha256,idempotency_key:crypto.randomUUID()});return ref;
 });
 await as('queuereviewer');await page.goto('/#reviews?section=bounties&bounty='+id);
 await expect(panel().getByRole('heading',{name:'Synthetic guided bounty',exact:true})).toBeVisible();
 for(const box of await panel().getByRole('checkbox').all())await box.check();
 await panel().getByLabel('Review findings',{exact:true}).fill('Synthetic fixture has explicit scope, evidence and independent verification; this is not scientific approval.');
 await write('/api/bounties/'+id+'/admission-decisions',()=>panel().getByRole('button',{name:'Record admission decision',exact:true}).click());
 await page.evaluate(async pin=>api('/api/notebook/notes/'+pin.note_id+'/publication-reviews',{revision:pin.revision,content_sha256:pin.content_sha256,idempotency_key:crypto.randomUUID(),decision:'approved',reason:'Explicitly synthetic test record.'}),pin);
 await as('alice');await page.goto('/#home');
 await expect(page.locator('.home-bounty[href*="'+id+'"]')).toContainText('Synthetic guided bounty');
 await page.goto('/#home?guide=tour');await page.locator('[data-contribution="bounty"]').click();
 await expect(page.locator('#live-guide-title')).toHaveText('Choose a bounty to contribute to.');
 await panel().locator('[data-bounty="'+id+'"]').click();
 await expect(page.locator('#live-guide-title')).toHaveText('Prepare evidence for this task.');
 await page.locator('#participation-guide').getByRole('button',{name:'Use an existing published note',exact:true}).click();
 await expect(page.locator('#live-guide-title')).toHaveText('Link the published evidence.');
 await panel().getByLabel('Exact contribution-note citation',{exact:true}).fill(JSON.stringify(pin));
 await expect(page.locator('#live-guide-title')).toHaveText('Explain what your work establishes.');
 await panel().getByLabel('What does it establish?',{exact:true}).fill('The exact synthetic note records the disposable workflow; this is partial test evidence only.');
 await expect(page.locator('#live-guide-title')).toHaveText('Review and submit your contribution.');
 const result=await write('/api/bounties/'+id+'/submissions',()=>page.locator('#participation-guide .button-primary').click());
 expect(result.receipt.submission_id).toBeTruthy();expect(result.bounty.award).toBeNull();
 await expect(page.locator('#live-guide-title')).toHaveText('Bounty contribution recorded.');
 await expect(page.locator('#participation-guide')).toContainText('does not imply an award');
});

const {test,expect,signIn,useDataSaver}=require('./helpers.cjs');
const next=page=>page.locator('#participation-guide .guide-next').click();
const back=page=>page.locator('#participation-guide .guide-back').click();
const title=(page,text)=>expect(page.locator('#live-guide-title')).toHaveText(text);
async function start(page,actor){
 await useDataSaver(page);await page.goto('/#home');await signIn(page,actor,{navigate:false});await page.locator('#home-get-started').click();await page.waitForFunction(()=>!state.challengeLoading);await title(page,'Let’s try a challenge.');
}
async function geometry(page,label,testInfo){
 await expect.poll(()=>page.evaluate(()=>{
  const p=document.querySelector('#participation-guide'),target=liveGuideHighlight,extra=liveGuide?.extraTarget;if(!p||p.hidden||!target)return null;
  const panel=p.getBoundingClientRect(),rects=[target,extra].filter(el=>el&&el.getClientRects().length).map(el=>el.getBoundingClientRect()),r=target.getBoundingClientRect(),header=p.closest('dialog')?0:document.querySelector('.site-header').getBoundingClientRect().bottom;
  const overlap=rects.some(r=>Math.min(panel.right,r.right)-Math.max(panel.left,r.left)>1&&Math.min(panel.bottom,r.bottom)-Math.max(panel.top,r.top)>1);
  const mask=document.querySelector('#guide-cutout-mask')?.parentElement.parentElement,m=mask?.getScreenCTM();
  const radius=parseFloat(getComputedStyle(target).borderTopLeftRadius)||0,corners=guideRectangle(target).corners;
  return {contained:panel.left>=15&&panel.right<=innerWidth-15&&panel.top>=header+15&&panel.bottom<=innerHeight-15,overlap,visible:rects.every(r=>r.top>=header-1&&r.bottom<=innerHeight+1),overflow:document.documentElement.scrollWidth>innerWidth,identity:!!m&&m.a===1&&m.d===1&&Math.abs(m.e)<.1&&Math.abs(m.f)<.1,corner:radius<=0||Math.abs(corners[0][0]-radius)<.1};
 })).toEqual({contained:true,overlap:false,visible:true,overflow:false,identity:true,corner:true});
 if(testInfo)await page.screenshot({path:testInfo.outputPath(label+'.png'),fullPage:false,animations:'allow'});
}
for(const [width,actor] of [[1440,'alice'],[390,'bob'],[320,'formsdesktop']])test('Get started has reversible steps and always restarts at step one at '+width+'px',async({page,context},testInfo)=>{
 await page.setViewportSize({width,height:900});await page.emulateMedia({reducedMotion:'no-preference',colorScheme:width===390?'dark':'light'});await context.grantPermissions(['clipboard-read','clipboard-write']);await start(page,actor);
 const posts=[];page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
 await geometry(page,'first',testInfo);await back(page);await expect(page).toHaveURL(/#home$/);expect(posts).toEqual([]);
 await page.locator('#home-get-started').click();await title(page,'Let’s try a challenge.');await next(page);await title(page,'Choose how to work.');const id=await page.evaluate(()=>state.challenge.id);expect(posts.filter(p=>p.startsWith('/api/challenges'))).toHaveLength(1);
 await geometry(page,'choice',testInfo);await back(page);await title(page,'Let’s try a challenge.');await next(page);await title(page,'Choose how to work.');
 await page.getByRole('button',{name:'Use my own tools',exact:true}).click();await title(page,'Download your challenge inputs.');await geometry(page,'manual-inputs',testInfo);await back(page);await title(page,'Choose how to work.');
 await page.locator('#participation-guide').getByRole('button',{name:'Use an agent',exact:true}).click();await title(page,'Give the challenge to your agent.');await geometry(page,'agent-prompt',testInfo);
 await expect(page.locator('#agent-instructions-text')).toContainText(id);await next(page);await title(page,'Recover the secret locally.');expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain("get --challenge-id '"+id+"'");
 await page.locator('#participation-guide').getByRole('button',{name:'Show agent prompt',exact:true}).click();await title(page,'Give the challenge to your agent.');await expect(page.locator('#agent-instructions')).toBeVisible();await geometry(page,'reopened-prompt',testInfo);
 await back(page);await title(page,'Choose how to work.');await page.getByRole('button',{name:'Use my own tools',exact:true}).click();await title(page,'Download your challenge inputs.');
 const download=page.waitForEvent('download');await next(page);const file=await download;expect(await file.failure()).toBeNull();await title(page,'Recover the secret locally.');await geometry(page,'working',testInfo);
 await page.locator('#participation-guide .guide-hide').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.reload();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.getByRole('button',{name:'Continue guide',exact:true}).click();await title(page,'Recover the secret locally.');
 await back(page);await title(page,'Download your challenge inputs.');await page.locator('#participation-guide').getByRole('button',{name:'Continue',exact:true}).click();await next(page);await title(page,'Enter your recovered key.');if(width===320)await page.setViewportSize({width,height:640});await geometry(page,'key',testInfo);
 await page.locator('#candidate').fill('00'.repeat(32));await title(page,'Enter your recovered key.');await next(page);await title(page,'Choose your method.');await next(page);await title(page,'Name your method.');await page.locator('#method-name').fill('Synthetic navigation draft');await next(page);await title(page,'Describe your method.');await page.locator('#method-pseudocode').fill('Disposable navigation test; no answer is submitted.');await next(page);
 const fields=[['hardware','Record your hardware.','Synthetic test machine'],['workers','Parallel workers.','2'],['wall-seconds','Record elapsed time.','1.5'],['precomputation','Record preparation.','None'],['peak-memory','Record peak memory.','Not measured']];
 for(const [id,heading,value] of fields){await title(page,heading);await geometry(page,id,testInfo);await page.locator('#'+id).fill(value);await title(page,heading);await next(page);}
 await title(page,'Review and submit your answer.');await geometry(page,'review',testInfo);
 for(const [id,heading,value] of [...fields].reverse()){await back(page);await title(page,heading);await expect(page.locator('#'+id)).toHaveValue(value);await geometry(page,'back-'+id);}
 await back(page);await title(page,'Describe your method.');await back(page);await title(page,'Name your method.');await back(page);await title(page,'Choose your method.');await back(page);await title(page,'Enter your recovered key.');await back(page);await expect(page.locator('#submission-dialog')).toBeHidden();await title(page,'Recover the secret locally.');
 await page.locator('#site-brand .brand-wordmark').click();await expect(page.locator('#participation-guide')).toHaveClass(/is-minimized/);await page.locator('#site-brand .brand-wordmark').click();await page.reload();await expect(page.locator('#participation-guide')).toBeHidden();await page.locator('#home-get-started').click();await page.waitForFunction(()=>!state.challengeLoading);await title(page,'Let’s try a challenge.');await expect(page.locator('.live-guide-progress')).toHaveText('1 / 3');expect(await page.evaluate(()=>state.challenge.id)).toBe(id);expect(posts.filter(p=>p.startsWith('/api/challenges'))).toHaveLength(1);expect(posts.some(p=>p.endsWith('/submit'))).toBe(false);
});

test('blocked clipboard stays at the prompt and offers an honest manual copy fallback',async({page},testInfo)=>{
 await start(page,'discoverydesktop');await next(page);await title(page,'Choose how to work.');await page.locator('#participation-guide').getByRole('button',{name:'Use an agent',exact:true}).click();
 await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw Error('Synthetic denied clipboard');};});await next(page);await title(page,'Copy the prompt manually.');await expect(page.locator('#agent-copy-status')).toBeVisible();expect(await page.evaluate(()=>liveGuide.toolsReady)).not.toBe(true);await geometry(page,'clipboard-blocked',testInfo);
 await next(page);expect(await page.evaluate(()=>String(getSelection()))).toContain('get --challenge-id');await page.locator('#participation-guide').getByRole('button',{name:'I copied it',exact:true}).click();await title(page,'Recover the secret locally.');
});

test('a failed download never advances the guide and a real retry downloads its own instance',async({page})=>{
 await start(page,'discoverymobile');await next(page);await page.getByRole('button',{name:'Use my own tools',exact:true}).click();
 await page.route('**/api/challenges/*/download',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic temporary download failure'})}));
 await next(page);await title(page,'Download your challenge inputs.');await expect(page.locator('.live-guide-copy')).toHaveText('Download failed. Try again.');expect(await page.evaluate(()=>liveGuide.toolsReady)).not.toBe(true);
 await page.unroute('**/api/challenges/*/download');const downloaded=page.waitForEvent('download');await next(page);expect(await(await downloaded).failure()).toBeNull();await title(page,'Recover the secret locally.');
});

test('imported results go to review and saved methods keep the correct Back path',async({page})=>{
 await start(page,'formsmobile');await next(page);await page.getByRole('button',{name:'Use my own tools',exact:true}).click();await page.locator('#participation-guide').getByRole('button',{name:'I have a result',exact:true}).click();
 const document=await page.evaluate(()=>recoveryTemplate(state.challenge));document.candidate='00'.repeat(32);document.method={name:'Synthetic imported guide draft',pseudocode:'Disposable import and navigation check; no recovery is claimed.'};document.compute={hardware:'Synthetic fixture runner',workers:1,wall_seconds:1,precomputation:'None',peak_memory:'Not measured',notes:''};
 const posts=[];page.on('request',r=>{if(r.method()==='POST')posts.push(new URL(r.url()).pathname);});
 await page.locator('#recovery-file').setInputFiles({name:'synthetic-guide-draft.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(document))});await expect(page.locator('#recovery-file-status')).toBeVisible();await title(page,'Review and submit your answer.');await expect(page.locator('#candidate')).toHaveValue(document.candidate);await expect(page.locator('#peak-memory')).toHaveValue('Not measured');expect(posts).toEqual([]);
 await back(page);await title(page,'Record peak memory.');await next(page);await title(page,'Review and submit your answer.');
 const method=await page.request.post('/api/methods',{headers:await page.evaluate(()=>({'Origin':location.origin,'X-CSRF-Token':state.csrf})),data:{name:'Synthetic saved guide method',pseudocode:'Disposable method-selection check; no research claim.'}});expect(method.ok()).toBe(true);const methodId=(await method.json()).method.id;
 await page.locator('#submission-dialog [data-close]').click();await title(page,'Recover the secret locally.');await next(page);await expect(page.locator('#submission-dialog')).toBeVisible();await page.locator('#method-select').focus();await page.locator('#method-select').selectOption(methodId);await title(page,'Choose your method.');await next(page);await title(page,'Record your hardware.');await back(page);await title(page,'Choose your method.');await expect(page.locator('#method-select')).toHaveValue(methodId);expect(posts.some(p=>p.endsWith('/submit'))).toBe(false);
});

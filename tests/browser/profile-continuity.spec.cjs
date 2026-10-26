const {test, expect, signIn, gate, holdResponse} = require('./helpers.cjs');

async function start(page) {
  await page.goto('/#about');await signIn(page,'alice',{navigate:false});
  await expect(page.locator('#about-workspace')).toBeVisible();
}
async function frame(root) {
  return root.evaluate(element=>{
    const rect=node=>{const b=node.getBoundingClientRect(),s=getComputedStyle(node);return{x:b.x,y:b.y,width:b.width,height:b.height,font:s.font};};
    return {back:rect(element.querySelector('.profile-backline')),
      backLabel:rect(element.querySelector('.scheme-back')),
      heading:rect(element.querySelector(element.id==='access-workspace'?'#access-title':element.id==='person-workspace'?'#person-title':'h1')),stats:rect(element.querySelector('.person-stats')),
      breakdown:rect(element.querySelector('.person-score-breakdown')),
      sections:[...element.querySelectorAll('.person-disclosure')].map(rect),
      overflow:document.documentElement.scrollWidth>innerWidth};
  });
}
function sameFrame(actual,expected,phase) {
  for(const key of ['back','backLabel','heading','stats','breakdown']) {
    for(const coordinate of ['x','y','width','height'])expect(Math.abs(actual[key][coordinate]-expected[key][coordinate]),phase+' '+key+' '+coordinate).toBeLessThanOrEqual(1);
    expect(actual[key].font,phase+' '+key+' font').toBe(expected[key].font);
  }
  expect(actual.sections).toHaveLength(3);expect(expected.sections).toHaveLength(3);
  actual.sections.forEach((section,index)=>{for(const coordinate of ['x','y','width','height'])expect(Math.abs(section[coordinate]-expected.sections[index][coordinate]),phase+' section'+index+' '+coordinate).toBeLessThanOrEqual(1);});
  expect(actual.overflow).toBe(false);
}
async function inertPreview(root) {
  const preview=root.locator('.person-preview');await expect(preview).toBeVisible();
  await expect(preview).toHaveAttribute('aria-hidden','true');
  await expect(preview.locator('a,button,summary,details,input')).toHaveCount(0);
  expect(await preview.innerText()).not.toMatch(/\b\d+\b/);
  await expect(preview.locator('.person-stats dd')).toHaveText(['','']);
}

async function heldRenderingResponse(page,predicate,change,fetchOptions) {
  const arrived=gate(),release=gate(),delivered=gate();let used=false;
  const matcher=url=>url.origin==='http://127.0.0.1:8029'&&predicate(url);
  await page.route(matcher,async route=>{
    if(used)return route.fallback();used=true;
    try {
      const response=await route.fetch(fetchOptions);expect(response.status()).toBe(200);
      const data=await response.json();change(data);arrived.resolve();await release.promise;
      await route.fulfill({response,json:data});
    } finally {delivered.resolve();}
  });
  return {arrived:arrived.promise,delivered:delivered.promise,release:release.resolve,remove:()=>page.unroute(matcher)};
}

const profiles=[...[1440,390].flatMap(width=>['alice','bob'].map(actor=>({width,actor,label:actor==='alice'?'own':'other'}))),
  {width:390,actor:'bob',label:'long-handle',handle:'u'.repeat(32)},
  {width:390,actor:'alice',label:'reviewer-own',reviewer:true}];
for(const {width,actor,label,handle:requestedHandle,reviewer=false} of profiles) {
  test(`cold ${label} profile keeps its ${width}px public frame through access, catalogue and profile reads`,async({page},testInfo)=>{
    await page.setViewportSize({width,height:1000});await start(page);
    // Retain only the real authenticated cookie/presentation hint. Stop the
    // setup document before its idle reads can consume the new one-shot holds.
    await page.goto('about:blank');
    const handle=requestedHandle||'browser_'+actor;
    // Rendering-only capability variation after real authenticated session
    // validation. No privileged API is requested and server rights stay intact.
    const session=reviewer ? await heldRenderingResponse(page,url=>url.pathname==='/api/session',data=>{
      expect(data.user.handle).toBe('browser_alice');expect(data.access.allowed).toBe(true);
      data.capabilities.review_protocol_proposals=true;
    }) : await holdResponse(page,url=>url.pathname==='/api/session');
    const catalogue=await holdResponse(page,url=>url.pathname==='/api/schemes'&&url.searchParams.get('projection')==='navigation');
    const profile=requestedHandle ? await heldRenderingResponse(page,url=>url.pathname==='/api/people/'+handle,
      data=>{expect(data.person.handle).toBe('browser_bob');data.person.handle=handle;},
      {url:'http://127.0.0.1:8029/api/people/browser_bob?include_milestones=false'}) :
      await holdResponse(page,url=>url.pathname==='/api/people/'+handle);
    const reads=[];page.on('request',request=>reads.push(new URL(request.url()).pathname));
    let initial,accountFrames;
    try {
      await page.goto('/?profile-layout='+width+'-'+label+'#person/'+handle,{waitUntil:'domcontentloaded'});await session.arrived;
      const access=page.locator('#access-workspace');await expect(access.locator('#access-title')).toHaveText(handle);
      await expect(access.locator('[data-pending-layout="profile"]')).toBeVisible();await inertPreview(access);
      expect(reads.filter(value=>value.startsWith('/api/')&&value!=='/api/session')).toEqual([]);
      await expect(page.locator('#person-workspace')).toBeHidden();
      initial=await frame(access);await page.screenshot({path:testInfo.outputPath('profile-access-'+width+'-'+label+'.png')});
      session.release();await session.delivered;await catalogue.arrived;
      const navigation=page.locator('#route-loading.profile-route-preview');await expect(navigation).toBeVisible();
      await expect(navigation.locator('h1')).toHaveText(handle);await inertPreview(navigation);
      sameFrame(await frame(navigation),initial,'catalogue pending');
      // Once access is confirmed, the exact read may overlap catalogue labels.
      await profile.arrived;expect(reads.filter(value=>value==='/api/people/'+handle)).toHaveLength(1);
      catalogue.release();await catalogue.delivered;await profile.arrived;
      const target=page.locator('#person-workspace');await expect(target).toBeVisible();await inertPreview(target);
      sameFrame(await frame(target),initial,'profile pending');
      if(actor==='bob') {
        await expect(page.locator('#account-sign-in')).toBeHidden();
        await expect(page.locator('#agent-connections')).toBeHidden();
      } else {
        // These controls are authorized by the completed fresh session, not
        // by the independent public profile-statistics response.
        await expect(page.locator('#account-sign-in')).toBeVisible();
        await expect(page.locator('#agent-connections')).toBeVisible();
        accountFrames=await Promise.all(['#account-sign-in','#agent-connections'].map(selector=>page.locator(selector).boundingBox()));
      }
      if(reviewer)await expect(page.locator('#profile-review-link')).toBeVisible();
      await page.locator('#nav-about').focus();
      await page.screenshot({path:testInfo.outputPath('profile-pending-'+width+'-'+label+'.png')});
      // While the foreground profile is pending, only its required reads run.
      // After it paints, the normal idle queue may prepare other workspaces.
      expect(reads.filter(value=>value.startsWith('/api/')&&!['/api/session','/api/schemes','/api/people/'+handle].includes(value))).toEqual([]);
      profile.release();await profile.delivered;
      await expect(target.locator('.person-preview')).toHaveCount(0);
      await expect(target.locator('.person-stats dd')).toHaveText(['0','0']);
      await expect(target.locator('.person-disclosure[open]')).toHaveCount(0);
      sameFrame(await frame(target),initial,'profile ready');
      if(accountFrames) {
        const ready=await Promise.all(['#account-sign-in','#agent-connections'].map(selector=>page.locator(selector).boundingBox()));
        ready.forEach((box,index)=>{for(const coordinate of ['x','y','width','height'])expect(Math.abs(box[coordinate]-accountFrames[index][coordinate]),'account settings '+coordinate).toBeLessThanOrEqual(1);});
      }
      await expect(page.locator('#nav-about')).toBeFocused();
      if(actor==='bob')await expect(page.locator('#account-sign-in')).toBeHidden();
      expect(reads.filter(value=>value==='/api/people/'+handle)).toHaveLength(1);
      expect(reads.filter(value=>value==='/api/session')).toHaveLength(1);
      expect(reads.filter(value=>value.startsWith('/api/notebook/notes/')||value.startsWith('/api/submissions/'))).toEqual([]);
      await page.screenshot({path:testInfo.outputPath('profile-ready-'+width+'-'+label+'.png')});
    } finally {
      for(const hold of [session,catalogue,profile])hold.release();
      for(const hold of [session,catalogue,profile])await hold.remove();
    }
  });
}

test('a cached profile revisit keeps admitted content and focused disclosure through a failed fresh read',async({page})=>{
  await start(page);await page.evaluate(()=>{location.hash='#person/browser_bob';});
  await expect(page.locator('#person-content .person-stats')).toBeVisible();
  const summary=page.locator('#personal-records > summary');await summary.click();
  const stats=await page.locator('#person-content .person-stats').elementHandle();
  await page.locator('#nav-about').click();
  const arrived=gate(),release=gate(),delivered=gate();
  const matcher=url=>url.pathname==='/api/people/browser_bob';
  await page.route(matcher,async route=>{try{const response=await route.fetch();expect(response.status()).toBe(200);arrived.resolve();await release.promise;await route.fulfill({response,status:503,json:{detail:'Synthetic profile read failed.'}});}finally{delivered.resolve();}});
  try {
    await page.evaluate(()=>{location.hash='#person/browser_bob';});await arrived.promise;
    expect(await stats.evaluate(element=>element.isConnected)).toBe(true);
    await expect(page.locator('#person-content .person-preview')).toHaveCount(0);
    await expect(page.locator('#personal-records')).toHaveAttribute('open','');await summary.focus();
    release.resolve();await delivered.promise;
    await expect(page.locator('#toast')).toContainText('Synthetic profile read failed.');
    expect(await stats.evaluate(element=>element.isConnected)).toBe(true);
    await expect(summary).toBeFocused();await expect(page.locator('#personal-records')).toHaveAttribute('open','');
  } finally {release.resolve();await page.unroute(matcher);}
});

test('profile result dialog returns focus to its replacement receipt after a profile repaint',async({page})=>{
  const record={submission_id:'browser-profile-layout',scheme_id:'teaching-dh-kem',scheme_version:'4',scheme_name:'Diffie–Hellman KEM',
    profile_id:'dh-20',tier:1,stage_number:1,instance_size:20,is_current:true,received_at:1700000000,scoring_eligible:true,score:{total:1}};
  let reads=0;
  // Rendering-only receipt: real profile authorization runs first. No challenge
  // is solved or submitted, and denied responses are never converted to data.
  await page.route('**/api/people/browser_alice?*',async route=>{
    const response=await route.fetch();expect(response.status()).toBe(200);const data=await response.json();
    data.person.records=[record];data.person.points=++reads;
    await route.fulfill({response,json:data});
  });
  await page.route('**/api/submissions/browser-profile-layout',async route=>{
    const response=await route.fetch();expect(response.status()).toBe(404);
    await route.fulfill({response,status:200,json:{submission:{...record,id:record.submission_id,status:'correct',handle:'browser_alice',elapsed_seconds:1,method:{name:'Synthetic profile details',pseudocode:'Display fixture only.'},compute:{}}}});
  });
  // Install rendering fixtures before admission can warm the same profile.
  await start(page);
  await page.locator('#account-handle').click();await page.locator('#personal-records > summary').click();
  const button=page.locator('#personal-records [data-submission-id="browser-profile-layout"]');
  const original=await button.elementHandle();await button.click();
  await expect(page.locator('#recovery-title')).toHaveText('Synthetic profile details');
  const close=page.locator('#recovery-dialog [data-close]');await close.focus();
  await page.evaluate(()=>loadPerson('browser_alice',{preserve:true}));
  expect(await original.evaluate(element=>element.isConnected)).toBe(false);
  await expect(close).toBeFocused();await expect(page.locator('#personal-records')).toHaveAttribute('open','');
  await page.keyboard.press('Escape');await expect(page.locator('#recovery-dialog')).toBeHidden();await expect(button).toBeFocused();
  expect(reads).toBe(2);
});

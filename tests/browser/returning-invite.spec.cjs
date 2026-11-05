const {test,expect,signIn}=require('./helpers.cjs');
const token=letter=>'cri_'+letter.repeat(43);
const password='Synthetic browser fixture password';
async function provider(page,code='synthetic-browser-code'){
 const origin=new URL(page.url()).origin;
 await page.route('https://github.com/login/oauth/authorize?*',route=>{
  const state=new URL(route.request().url()).searchParams.get('state');
  return route.fulfill({status:302,headers:{location:origin+'/api/auth/github/callback?'+new URLSearchParams({state,code})},body:''});
 });
}
for(const width of [1440,390]){
 for(const [letter,label] of [['r','used within seven days'],['o','used after seven days'],['e','expired']])for(const method of ['github','password']){
  test(method+' signs into the same member through an invitation '+label+' at '+width+'px',async({page},testInfo)=>{
   await page.setViewportSize({width,height:1000});const posts=[];
   page.on('request',request=>{if(request.method()==='POST')posts.push({path:new URL(request.url()).pathname,body:request.postDataJSON()});});
   await page.goto('/#join?token='+token(letter));await provider(page);
   if(letter==='e'){
    await expect(page.locator('#auth-title')).toHaveText('Invitation expired');await page.locator('#auth-issue-signin').click();
   }else{
    await expect(page.locator('#auth-title')).toHaveText('Welcome back');
    await expect(page.locator('#auth-invitation-notice')).toContainText('already been used');
    await expect(page.locator('#auth-create-account')).toBeHidden();
   }
   await expect(page.locator('#auth-existing-account')).toBeHidden();
   if(method==='github')await page.locator('#auth-github').click();
   else{
    await page.locator('#auth-password-summary').click();await expect(page.locator('#auth-github')).toBeHidden();
    await page.locator('#auth-handle').fill('browser_alice');await page.locator('#auth-password').fill(password);await page.locator('#auth-submit').click();
   }
   await expect(page.locator('#account-handle')).toHaveText('browser_alice');await expect(page.locator('#auth-dialog')).toBeHidden();
   expect(new URL(page.url()).hash).toBe('#home');await page.locator('#nav-notebook').click();await expect(page.locator('#nb-note-list')).toContainText('Alice browser-only private note');
   expect(posts.filter(row=>row.path==='/api/auth/register'||row.path==='/api/auth/github/complete'||row.path==='/api/access/invitations/redeem')).toEqual([]);
   if(method==='github')expect(posts.find(row=>row.path==='/api/auth/github/start').body).not.toHaveProperty('invitation_token');
   expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  });
 }
 test('signed-in members leave invitation URLs without inspecting or accepting them at '+width+'px',async({page})=>{
  await page.setViewportSize({width,height:1000});await signIn(page);const destination=new URL(page.url()).hash,posts=[];
  page.on('request',request=>{if(request.method()==='POST')posts.push(new URL(request.url()).pathname);});
  for(const letter of ['r','o','e','v']){await page.goto('/#join?token='+token(letter));await expect(page.locator('#account-handle')).toHaveText('browser_alice');await expect(page.locator('#access-workspace')).toBeHidden();await expect(page).toHaveURL(url=>url.hash===destination);}
  expect(posts).toEqual([]);
 });
 test('an unused invitation creates and resumes one password account at '+width+'px',async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});const invitation=token(width===1440?'p':'q'),origin='http://127.0.0.1:8029';
  await page.goto('/#join?token='+invitation);await expect(page.locator('#auth-title')).toHaveText('Create an account');await page.locator('#auth-password-summary').click();
  await expect(page.locator('#auth-github')).toBeHidden();await page.locator('#auth-handle').fill('new_password_'+width);await page.locator('#auth-password').fill(password);
  const registered=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/auth/register');await page.locator('#auth-submit').click();const saved=await(await registered).json();
  await expect(page.locator('#auth-title')).toHaveText('Verify your email');await expect(page.locator('#access-code')).toBeVisible();await expect(page.locator('#catalogue-workspace')).toBeHidden();
  await page.keyboard.press('Escape');await page.locator('#access-pending-signout').click();await page.locator('#access-sign-in').click();await page.locator('#auth-password-summary').click();
  await page.locator('#auth-handle').fill('new_password_'+width);await page.locator('#auth-password').fill(password);
  const loggedIn=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/auth/login');await page.locator('#auth-submit').click();expect((await(await loggedIn).json()).user.id).toBe(saved.user.id);
  await expect(page.locator('#auth-title')).toHaveText('Finish joining Crucible');
  await page.locator('#auth-invitation-link').fill(new URL(page.url()).origin+'/#join?token='+invitation);await page.locator('#auth-invitation-form button[type=submit]').click();
  await expect(page.locator('#auth-title')).toHaveText('Verify your email');await expect(page.locator('#access-code')).toBeVisible();
 });
 test('an unused invitation creates a GitHub account before mailbox verification at '+width+'px',async({page})=>{
  await page.setViewportSize({width,height:1000});await page.goto('/#join?token='+token(width===1440?'g':'h'));await provider(page,'synthetic-new-'+width);
  await page.locator('#auth-github').click();await expect(page.locator('#github-handle-form')).toBeVisible();await page.locator('#github-handle').fill('new_github_'+width);await page.locator('#github-handle-submit').click();
  await expect(page.locator('#auth-title')).toHaveText('Verify your email');await expect(page.locator('#access-code')).toBeVisible();await expect(page.locator('#catalogue-workspace')).toBeHidden();
 });
}

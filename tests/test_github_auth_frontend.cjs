const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'account-a', handle: 'alice'};
const scheme = {id: 'fixture', version: '1', name: 'Fixture', next_profile_id: 'tier-1',
  profiles: [{id: 'tier-1', instance_size: 1, shared_secret_bytes: 32, parameters: {}, encoding: {shared_secret_bytes: 32}, correctness: {}}],
  size_parameter: {meaning: 'Fixture'}, protocol: {}, profile_metrics: {'tier-1': {public_key_bytes: 10}}};
const access = status => ({mode: 'beta', status, allowed: status === 'active'});
const auth = {github_enabled: true, github_linked: false, github_login: null, password_enabled: true, password_recovery: true};
const session = (signedIn = null, status = signedIn ? 'active' : 'anonymous', config = auth) => ({user: signedIn, access: access(status), csrf_token: 'csrf-a', auth: config});
const state = app => vm.runInContext('state', app.sandbox);
const paths = app => app.requests.map(request => request.url.pathname);
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
async function appWith({hash = '#challenge', signedIn = null, status, config = auth, responder} = {}) {
  return createApp({schemes: [scheme], hash, responder: (url, options, envelope) => {
    if (url.pathname === '/api/session') return envelope(session(signedIn, status, config));
    if (url.pathname.startsWith('/api/people/')) return envelope({person: {handle: decodeURIComponent(url.pathname.split('/').at(-1)), points: 0, accepted_proposal_count: 0, records: [], recent_recoveries: []}});
    return responder?.(url, options, envelope);
  }});
}
(async () => {
  await test('unconfigured provider keeps familiar password sign-in and hides unavailable recovery', async () => {
    const app = await appWith({config: {github_enabled: false, password_recovery: false}});
    assert.equal(app.$('#access-github').hidden, true);
    assert.equal(app.$('#access-sign-in').textContent, 'Sign in');
    app.$('#access-sign-in').click();
    assert.equal(app.$('#auth-password-options').open, true);
    assert.ok(app.$('#auth-password-options').classList.contains('sole-method'));
    assert.equal(app.$('#auth-github').hidden, true);
    assert.equal(app.$('#auth-forgot-password').hidden, true);
  });
  await test('GitHub is primary while password fallback stays one disclosure away', async () => {
    const app = await appWith({hash: '#join?token=invitation_token'});
    assert.equal(app.$('#access-github').hidden, false);
    assert.equal(app.$('#access-register').hidden, true);
    assert.equal(app.$('#access-sign-in').textContent, 'Use a password');
    app.$('#auth-button').click();
    assert.equal(app.$('#auth-github').hidden, false);
    assert.equal(app.$('#auth-password-options').open, false);
    app.$('#auth-dialog').close(); app.$('#access-sign-in').click();
    assert.equal(app.$('#auth-password-options').open, true);
    assert.equal(app.$('#register-mode').hidden, false);
  });
  await test('GitHub start carries invitation and safe deep link server-side with CSRF', async () => {
    const app = await appWith({hash: '#join?token=invitation_token', responder: (url, options) => {
      if (url.pathname === '/api/auth/github/start') {
        assert.equal(options.headers['X-CSRF-Token'], 'csrf-a');
        assert.deepEqual(JSON.parse(options.body), {mode: 'login', return_route: '#connect?code=ABCD-EFGH', invitation_token: 'invitation_token'});
        return {authorization_url: 'https://github.com/login/oauth/authorize?client_id=fixture&state=opaque'};
      }
    }});
    state(app).returnRoute = '#connect?code=ABCD-EFGH';
    const destinations = []; app.location.assign = value => destinations.push(value);
    app.$('#access-github').click(); app.$('#access-github').click(); await flush();
    assert.equal(paths(app).filter(path => path === '/api/auth/github/start').length, 1);
    assert.deepEqual(destinations, ['https://github.com/login/oauth/authorize?client_id=fixture&state=opaque']);
  });
  await test('provider navigation rejects an unexpected authorization host', async () => {
    const app = await appWith({responder: url => url.pathname === '/api/auth/github/start' ? {authorization_url: 'https://example.org/steal'} : undefined});
    app.location.assign = () => { throw new Error('must not navigate'); };
    app.$('#access-github').click(); await flush();
    assert.match(app.$('#access-error').textContent, /could not be started/);
    assert.equal(app.$('#access-github').disabled, false);
  });
  await test('closing dialog cancels a late provider-start navigation', async () => {
    const gate = deferred();
    const app = await appWith({responder: url => url.pathname === '/api/auth/github/start' ? gate.promise : undefined});
    let navigations = 0; app.location.assign = () => { navigations++; };
    app.$('#auth-button').click(); app.$('#auth-github').click(); await flush();
    app.$('#auth-dialog').close();
    gate.resolve({authorization_url: 'https://github.com/login/oauth/authorize'}); await flush();
    assert.equal(navigations, 0);
  });
  await test('GitHub callback resumes the original member route without a second account', async () => {
    const app = await appWith({signedIn: user, hash: '#auth?github=complete', responder: url =>
      url.pathname === '/api/auth/github/pending' ? {pending: false, return_route: '#scheme/fixture/1'} : undefined});
    assert.equal(app.location.hash, '#scheme/fixture/1');
    assert.equal(app.$('#overview-workspace').hidden, false);
    assert.equal(paths(app).includes('/api/auth/register'), false);
    assert.equal(paths(app).includes('/api/auth/github/complete'), false);
  });
  await test('new GitHub participant chooses a handle before protected reads and mailbox redemption', async () => {
    const app = await appWith({hash: '#auth?github=choose-handle', responder: (url, options, envelope) => {
      if (url.pathname === '/api/auth/github/pending') return {pending: true, github_login: 'alice-dev', suggested_handle: 'alice_dev', return_route: '#scheme/fixture/1', invitation_token: 'invitation_token'};
      if (url.pathname === '/api/auth/github/complete') {
        assert.deepEqual(JSON.parse(options.body), {handle: 'alice'});
        return envelope({...session(user, 'pending'), csrf_token: 'csrf-new'});
      }
    }});
    assert.equal(app.$('#auth-dialog').open, true);
    assert.equal(app.$('#github-handle-form').hidden, false);
    assert.equal(app.$('#github-handle').value, 'alice_dev');
    assert.equal(paths(app).includes('/api/schemes'), false);
    app.$('#github-handle').value = 'alice'; app.$('#github-handle-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.location.hash, '#join?token=invitation_token');
    assert.equal(app.$('#access-verification').hidden, false);
    assert.equal(state(app).returnRoute, '#scheme/fixture/1');
    assert.equal(state(app).csrf, 'csrf-new');
    assert.equal(paths(app).includes('/api/schemes'), false);
  });
  await test('existing-account escape explicitly returns to password sign-in without creating an account', async () => {
    const app = await appWith({hash: '#auth?github=choose-handle', responder: url =>
      url.pathname === '/api/auth/github/pending' ? {pending: true, suggested_handle: 'alice', return_route: '#challenge'} : undefined});
    app.$('#github-existing-account').click(); await flush();
    assert.equal(app.$('#auth-password-options').open, true);
    assert.equal(app.$('#github-handle-form').hidden, true);
    assert.equal(app.$('#auth-form').closest('[hidden]'), null);
    assert.equal(paths(app).includes('/api/auth/github/complete'), false);
  });
  await test('linking is offered only on the owning profile and sends explicit link mode', async () => {
    const app = await appWith({signedIn: user, hash: '#person/alice', responder: (url, options) => {
      if (url.pathname === '/api/auth/github/start') {
        assert.deepEqual(JSON.parse(options.body), {mode: 'link', return_route: '#person/alice', password: 'existing long password'});
        return {authorization_url: 'https://github.com/login/oauth/authorize'};
      }
    }});
    assert.equal(app.$('#account-sign-in').hidden, false);
    app.location.assign = () => {};
    app.$('#account-link-github').click();
    assert.equal(app.$('#github-link-form').hidden, false);
    assert.equal(paths(app).includes('/api/auth/github/start'), false);
    app.$('#github-link-password').value = 'existing long password';
    app.$('#github-link-form').dispatchEvent({type: 'submit'}); await flush();
    assert.ok(paths(app).includes('/api/auth/github/start'));
    assert.equal(app.$('#github-link-password').value, '');
    await app.navigate('#person/bob');
    assert.equal(app.$('#account-sign-in').hidden, true);
    app.sandbox.setSession(app.envelope(session(user, 'active', {...auth, github_linked: true, github_login: 'alice-dev'})));
    await app.navigate('#person/alice');
    assert.equal(app.$('#account-link-github').hidden, true);
    assert.equal(app.$('#account-github-status').textContent, 'Linked as alice-dev.');
  });
  await test('account or session changes invalidate the password confirmation panel', async () => {
    const app = await appWith({signedIn: user, hash: '#person/alice'});
    app.$('#account-link-github').click(); app.$('#github-link-password').value = 'existing long password';
    app.sandbox.setSession(app.envelope({...session(user), csrf_token: 'new-session-csrf'}));
    assert.equal(app.$('#auth-dialog').open, false);
    assert.equal(app.$('#github-link-password').value, '');
    assert.equal(state(app).githubLinkContext, null);
    app.$('#github-link-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(paths(app).includes('/api/auth/github/start'), false);
  });
  await test('a rejected GitHub link clears the password and permits an explicit retry', async () => {
    const app = await appWith({signedIn: user, hash: '#person/alice'});
    app.sandbox.fetch = async () => ({ok: false, status: 403, json: async () => ({detail: 'Your password is incorrect.'})});
    app.$('#account-link-github').click(); app.$('#github-link-password').value = 'existing wrong password';
    app.$('#github-link-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.$('#github-link-password').value, '');
    assert.equal(app.$('#github-link-submit').disabled, false);
    assert.match(app.$('#github-link-error').textContent, /incorrect/);
  });
  await test('cancelled provider callback gives retry and password options without protected reads', async () => {
    const app = await appWith({hash: '#auth?github=denied', responder: url =>
      url.pathname === '/api/auth/github/pending' ? {pending: false, return_route: '#notebook'} : undefined});
    assert.equal(app.location.hash, '#notebook');
    assert.equal(app.$('#auth-dialog').open, true);
    assert.match(app.$('#auth-github-error').textContent, /cancelled/);
    assert.equal(app.$('#auth-github').disabled, false);
    assert.equal(app.$('#auth-password-options').open, false);
    assert.equal(paths(app).includes('/api/schemes'), false);
  });
  await test('an unlinked GitHub callback explains existing account linking and exposes password sign-in without creating an account', async () => {
    const app = await appWith({hash:'#auth?github=invite-required', responder:url =>
      url.pathname === '/api/auth/github/pending' ? {pending:false, return_route:'#notebook'} : undefined});
    assert.equal(app.location.hash,'#notebook');
    assert.equal(app.$('#auth-dialog').open,true);
    assert.equal(app.$('#auth-password-options').open,true);
    assert.equal(app.$('#auth-form').closest('[hidden]'),null);
    assert.equal(app.$('#github-handle-form').hidden,true);
    assert.equal(state(app).user,null);assert.equal(state(app).authMode,'login');
    assert.equal(state(app).access.allowed,false);
    const guidance=app.$('#auth-github-error').textContent;
    assert.match(guidance,/not linked to a Crucible account/);
    assert.match(guidance,/sign in with your password/);
    assert.match(guidance,/profile’s Sign-in settings and choose Link GitHub/);
    assert.match(guidance,/New participants should open their invitation link/);
    assert.deepEqual(paths(app),['/api/session','/api/auth/github/pending']);
    assert.ok(app.requests.every(request=>request.options.method==='GET'));
  });
  await test('password rejection replaces stale GitHub guidance with the actual password error', async () => {
    const app = await appWith({hash:'#auth?github=invite-required', responder:url =>
      url.pathname === '/api/auth/github/pending' ? {pending:false, return_route:'#notebook'} : undefined});
    assert.equal(app.$('#auth-github-error').hidden,false);
    const fetch=app.sandbox.fetch, attempts=[];
    app.sandbox.fetch=async(path,options)=>{
      if(path!=='/api/auth/login')return fetch(path,options);
      attempts.push({path,method:options.method,body:JSON.parse(options.body)});
      return {ok:false,status:401,json:async()=>({detail:'Invalid handle or password.'})};
    };
    app.$('#auth-handle').value='alice';app.$('#auth-password').value='incorrect fixture password';
    app.$('#auth-form').dispatchEvent({type:'submit'});
    assert.equal(app.$('#auth-github-error').hidden,true);
    assert.equal(app.$('#auth-github-error').textContent,'');
    await flush();
    assert.equal(app.$('#auth-error').textContent,'Invalid handle or password.');
    assert.equal(app.$('#auth-github-error').hidden,true);
    assert.equal(app.$('#auth-dialog').open,true);assert.equal(state(app).user,null);
    assert.deepEqual(attempts,[{path:'/api/auth/login',method:'POST',body:{handle:'alice',password:'incorrect fixture password'}}]);
    assert.deepEqual(paths(app),['/api/session','/api/auth/github/pending']);
  });
  await test('a suspended signed-in callback stays gated', async () => {
    const app = await appWith({signedIn: user, status: 'suspended', hash: '#auth?github=complete', responder: url =>
      url.pathname === '/api/auth/github/pending' ? {pending: false, return_route: '#notebook'} : undefined});
    assert.equal(app.$('#access-workspace').hidden, false);
    assert.match(app.$('#access-description').textContent, /not active/);
    assert.equal(paths(app).includes('/api/schemes'), false);
  });
  await test('reset link is scrubbed before the initial session response', async () => {
    const gate = deferred(); const token = 'a'.repeat(43);
    const app = await createApp({schemes: [scheme], hash: '#reset-password?token=' + token, responder: url =>
      url.pathname === '/api/session' ? gate.promise : undefined});
    assert.equal(app.location.hash, '#auth?password=reset');
    assert.equal(app.location.href.includes(token), false);
    gate.resolve(app.envelope(session())); await flush();
    assert.equal(app.$('#password-reset-form').hidden, false);
    assert.equal(app.$('#password-reset-submit').disabled, false);
  });
  await test('recovery request displays generic non-enumerating status', async () => {
    const app = await appWith({responder: (url, options) => {
      if (url.pathname === '/api/auth/password/reset/start') { assert.deepEqual(JSON.parse(options.body), {handle: 'alice'}); return {status: 'requested'}; }
    }});
    app.$('#access-sign-in').click(); app.$('#auth-handle').value = 'alice'; app.$('#auth-forgot-password').click();
    assert.equal(app.$('#password-reset-handle').value, 'alice');
    app.$('#password-reset-request-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(app.$('#password-reset-request-status').hidden, false);
    assert.match(app.$('#password-reset-request-status').textContent, /^If this account has a verified email/);
  });
  await test('reset validates confirmation, revokes-session response stays signed out, and erases secret', async () => {
    const token = 'a'.repeat(43);
    const app = await appWith({hash: '#reset-password?token=' + token, responder: (url, options, envelope) => {
      if (url.pathname === '/api/auth/password/reset/complete') {
        assert.deepEqual(JSON.parse(options.body), {token, password: 'a long new password'});
        return envelope({...session(), status: 'password_reset', csrf_token: 'reset-csrf'});
      }
    }});
    app.$('#password-reset-new').value = 'a long new password'; app.$('#password-reset-confirm').value = 'different';
    app.$('#password-reset-form').dispatchEvent({type: 'submit'}); await flush();
    assert.match(app.$('#password-reset-error').textContent, /do not match/);
    assert.equal(paths(app).includes('/api/auth/password/reset/complete'), false);
    app.$('#password-reset-confirm').value = 'a long new password'; app.$('#password-reset-form').dispatchEvent({type: 'submit'}); await flush();
    assert.equal(state(app).passwordResetToken, null);
    assert.equal(state(app).user, null);
    assert.equal(state(app).csrf, 'reset-csrf');
    assert.equal(app.$('#password-reset-new').value, '');
    assert.equal(app.$('#auth-password-options').open, true);
    assert.match(app.$('#auth-description').textContent, /has been reset/);
    assert.equal(paths(app).includes('/api/auth/login'), false);
  });
  await test('closing reset form clears in-memory secrets and malformed links stay disabled', async () => {
    const app = await appWith({hash: '#reset-password?token=' + 'a'.repeat(43)});
    app.$('#password-reset-new').value = 'a long new password'; app.$('#auth-dialog').close();
    assert.equal(state(app).passwordResetToken, null);
    assert.equal(app.$('#password-reset-new').value, '');
    await app.navigate('#reset-password?token=bad');
    assert.equal(app.$('#password-reset-submit').disabled, true);
    assert.match(app.$('#password-reset-error').textContent, /incomplete/);
  });
  await test('authentication does not persist browser credentials or use raw callback codes', async () => {
    const app = await appWith();
    for (const unsafe of ['https://evil.example/', '//evil.example', '#auth?code=secret', '#reset-password?token=secret', '#challenge\\evil']) assert.equal(app.sandbox.safeAuthReturnRoute(unsafe), '#challenge');
    const source = fs.readFileSync(require('node:path').resolve(__dirname, '../web/app.js'), 'utf8');
    const authSource = source.slice(source.indexOf('function safeAuthReturnRoute'), source.indexOf('function setAuthMode'));
    assert.doesNotMatch(authSource, /(?:localStorage|sessionStorage)\s*\./);
    assert.doesNotMatch(authSource, /\.get\(['"](?:code|access_token)['"]\)/);
  });
  console.log(`\n${passed} GitHub and recovery frontend checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

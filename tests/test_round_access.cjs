// Run with `node tests/test_round_access.cjs`. The registered profiles below are fixtures.
const assert = require('node:assert/strict');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 1, handle: 'first'};
const profile = n => ({id: 'tier-' + n, instance_size: n, shared_secret_bytes: 32, parameters: {n}, encoding: {shared_secret_bytes: 32}, correctness: {}});
const release = (id, version = '2') => ({id, version, name: id, educational: true, profiles: [20,21,22,23].map(profile), next_profile_id: 'tier-20', size_parameter: {meaning: 'Fixture parameter'}, protocol: {}});
const previous = release('alpha', '1');
const alpha = {...release('alpha'), previous_releases: [previous]};
const beta = release('beta');
const schemes = [alpha,beta];
const route = (tier, view = 'challenge', scheme = alpha) => `#challenge/${scheme.id}/${scheme.version}?profile=tier-${tier}&view=${view}`;
const deferred = () => {let resolve; const promise = new Promise(r => {resolve=r;}); return {promise,resolve};};
const posts = app => app.requests.filter(r => r.url.pathname === '/api/challenges' && r.options.method === 'POST').map(r => JSON.parse(r.options.body));

function service() {
  const initialRound = Math.floor(Date.now() / 3600000);
  const server = {user, roundId: initialRound, instances: new Map(), active: new Set(), before: null, after: null, fail: null};
  const scope = (who, scheme, version, round) => JSON.stringify([who,scheme,version,round]);
  server.envelope = (data, round = server.roundId) => ({server_time: round * 3600 + 1200, round: {id: round, starts_at: round * 3600, ends_at: (round + 1) * 3600}, ...data});
  server.seed = (tier = 20, scheme = alpha, who = server.user.id, round = server.roundId) => {
    const key = scope(who,scheme.id,scheme.version,round);
    const id = key + ':tier-' + tier;
    const challenge = {id, scheme_id:scheme.id, scheme_version:scheme.version, profile_id:'tier-' + tier,
      round_id:round, status:'open', issued_at:round * 3600 + 1200, ends_at:(round + 1) * 3600,
      public:{encoding:{shared_secret_bytes:32}}, submission:null};
    server.active.add(key); server.instances.set(id,challenge); return challenge;
  };
  server.respond = async (url, options) => {
    if (url.pathname === '/api/session') return server.envelope({user:server.user,csrf_token:'fixture-csrf'});
    if (url.pathname === '/api/schemes') return server.envelope({schemes});
    if (url.pathname === '/api/leaderboard') return server.envelope({entries:[]});
    if (url.pathname !== '/api/challenges') return undefined;
    const payload = options.method === 'POST' ? JSON.parse(options.body) : Object.fromEntries(url.searchParams);
    const req = {method:options.method, ...payload, userId:server.user?.id, roundId:server.roundId};
    if (server.before) await server.before(req);
    const key = scope(req.userId,req.scheme_id,req.scheme_version,req.roundId);
    const id = key + ':' + req.profile_id;
    let result;
    if (req.method === 'POST' && server.fail) result = {fixtureHttpError: server.fail};
    else {
      if (req.method === 'POST') {
        if (req.active_round_id !== undefined && (req.active_round_id !== server.roundId || !server.active.has(key))) {
          result = {fixtureHttpError:409};
        } else if (!server.instances.has(id)) server.seed(Number(req.profile_id.slice(5)), {id:req.scheme_id,version:req.scheme_version}, req.userId, req.roundId);
      }
      if (!result) {
        const active = req.scheme_version === '2' && server.active.has(key);
        result = server.envelope({challenge:server.instances.get(id) || null, round_started:active,
          round_started_round_id:active ? req.roundId : null},req.roundId);
      }
    }
    if (server.after) result = await server.after(req,result);
    return result;
  };
  return server;
}
async function boot({hash = route(20), seed = false} = {}) {
  const server = service();
  if (seed) server.seed();
  const app = await createApp({schemes,user,hash,responder:server.respond});
  // Keep the shared DOM fixture unchanged; only this suite needs HTTP failures.
  const fetch = app.sandbox.fetch;
  app.sandbox.fetch = async (...args) => {
    const response = await fetch(...args);
    const body = await response.json();
    return body.fixtureHttpError ? {ok:false,status:body.fixtureHttpError,json:async()=>({detail:'Fixture issue failure'})} : response;
  };
  return {app,server};
}
let passed = 0;
async function test(name, run) {await run(); console.log('PASS ' + name); passed++;}
(async () => {
  await test('one manual request starts a round, later Challenge tiers load automatically, and revisits reuse instances', async () => {
    const {app,server} = await boot();
    assert.equal(app.$('#empty-title').textContent,'Ready?');
    assert.equal(posts(app).length,0);
    app.$('#issue-button').click(); await flush();
    assert.equal(posts(app).length,1);
    assert.equal('active_round_id' in posts(app)[0],false);
    assert.equal(app.$('#challenge-active').hidden,false);
    await app.navigate(route(21));
    assert.equal(posts(app).length,2);
    assert.equal(posts(app)[1].active_round_id,server.roundId);
    assert.match(app.$('#challenge-round').textContent,/tier-21/);
    await app.navigate(route(20)); await app.navigate(route(21));
    assert.equal(posts(app).length,2);
    assert.equal(server.instances.size,2);
  });
  await test('leaderboards, protocols, About us, and slider previews never issue instances', async () => {
    const {app} = await boot({seed:true,hash:route(21,'leaderboard')});
    await app.navigate(route(22,'protocol')); await app.navigate('#about');
    await app.navigate(route(20));
    app.$('#size-slider').value='3'; app.$('#size-slider').dispatchEvent({type:'input'}); await flush();
    assert.equal(posts(app).length,0);
    assert.match(app.location.hash,/tier-20/);
    app.$('#size-slider').dispatchEvent({type:'change'}); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-23']);
    await app.navigate(route(21,'protocol'));
    app.$('#tab-challenge').click(); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-23','tier-21']);
  });
  await test('another scheme, archived release, another account, and the next hour all require their own start', async () => {
    const {app,server} = await boot({seed:true});
    await app.navigate(route(21,'challenge',beta));
    assert.equal(posts(app).length,0);
    server.seed(20,previous);
    await app.navigate(route(21,'challenge',previous));
    assert.equal(posts(app).length,0);
    assert.equal(app.$('#empty-title').textContent,'Archived release');
    await app.navigate(route(20));
    server.user = {id:2,handle:'second'};
    app.sandbox.setSession(server.envelope({user:server.user,csrf_token:'second-csrf'}));
    await app.sandbox.loadChallenge();
    assert.equal(app.$('#empty-title').textContent,'Ready?');
    assert.equal(posts(app).length,0);
    server.user = user; server.roundId++;
    app.sandbox.setSession(server.envelope({user,csrf_token:'next-round'}));
    await app.sandbox.loadChallenge();
    assert.equal(posts(app).length,0);
    assert.equal(app.$('#empty-title').textContent,'Ready?');
  });
  await test('activation requires a matching response round and cannot be borrowed from stale or archived metadata', async () => {
    const {app,server} = await boot({seed:true});
    server.after = async (req,result) => ({...result,round_started:true,round_started_round_id:req.roundId-1});
    await app.navigate(route(21));
    assert.equal(posts(app).length,0);
    server.after = async (req,result) => ({...result,round_started:true,round_started_round_id:req.roundId});
    await app.navigate(route(21,'challenge',previous));
    assert.equal(posts(app).length,0);
  });
  await test('rapid tier navigation discards late GETs and issues only the final selected tier', async () => {
    const {app,server} = await boot({seed:true});
    const gates = new Map([['tier-21',deferred()],['tier-22',deferred()]]);
    server.after = async (req,result) => {
      if (req.method==='GET' && gates.has(req.profile_id)) await gates.get(req.profile_id).promise;
      return result;
    };
    await app.navigate(route(21)); await app.navigate(route(22)); await app.navigate(route(23));
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-23']);
    gates.get('tier-22').resolve(); gates.get('tier-21').resolve(); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-23']);
    assert.match(app.$('#challenge-round').textContent,/tier-23/);
  });
  await test('navigation during the first manual POST waits, then obtains only the final tier', async () => {
    const {app,server} = await boot();
    const gate = deferred();
    server.before = async req => {if(req.method==='POST' && req.profile_id==='tier-20') await gate.promise;};
    app.$('#issue-button').click(); await flush();
    await app.navigate(route(21)); await app.navigate(route(22));
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-20']);
    gate.resolve(); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-20','tier-22']);
    assert.equal(posts(app)[1].active_round_id,server.roundId);
    assert.match(app.$('#challenge-round').textContent,/tier-22/);
  });
  await test('an in-flight automatic POST cannot overwrite or duplicate later selections', async () => {
    const {app,server} = await boot({seed:true});
    const gate = deferred();
    server.before = async req => {if(req.method==='POST' && req.profile_id==='tier-21') await gate.promise;};
    await app.navigate(route(21)); await app.navigate(route(22)); await app.navigate(route(23));
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-21']);
    gate.resolve(); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-21','tier-23']);
    assert.match(app.$('#challenge-round').textContent,/tier-23/);
    await app.sandbox.loadChallenge();
    assert.equal(posts(app).length,2);
  });
  await test('leaving Challenge or changing accounts during a POST cannot auto-issue or reveal its result', async () => {
    for (const changeAccount of [false,true]) {
      const {app,server} = await boot();
      const gate = deferred();
      server.before = async req => {if(req.method==='POST') await gate.promise;};
      app.$('#issue-button').click(); await flush();
      if (changeAccount) {
        server.user={id:2,handle:'second'};
        app.sandbox.setSession(server.envelope({user:server.user,csrf_token:'second'}));
        await app.sandbox.loadChallenge();
      } else await app.navigate(route(22,'protocol'));
      gate.resolve(); await flush();
      assert.equal(posts(app).length,1);
      assert.equal(app.$('#challenge-active').hidden,true);
    }
  });
  await test('rate-limit and service errors pause automation until an explicit retry, without duplicate POSTs', async () => {
    for (const status of [429,503]) {
      const {app,server} = await boot({seed:true});
      server.fail=status;
      await app.navigate(route(21));
      assert.equal(posts(app).length,1);
      assert.equal(app.$('#challenge-error').hidden,false);
      await app.sandbox.loadChallenge();
      await app.navigate(route(22));
      assert.equal(posts(app).length,1);
      server.fail=null;
      app.$('#retry-challenge').click(); await flush();
      assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-21','tier-22']);
      assert.match(app.$('#challenge-round').textContent,/tier-22/);
    }
  });
  await test('after an account switch, a fresh GET may use only the new account’s independently active round', async () => {
    const {app,server} = await boot();
    server.seed(20,alpha,2);
    const gate=deferred();
    server.before=async req=>{if(req.method==='POST' && req.userId===1) await gate.promise;};
    app.$('#issue-button').click(); await flush();
    server.user={id:2,handle:'second'};
    app.sandbox.setSession(server.envelope({user:server.user,csrf_token:'second'}));
    await app.navigate(route(21));
    assert.equal(posts(app).length,1);
    gate.resolve(); await flush();
    assert.deepEqual(posts(app).map(p=>p.profile_id),['tier-20','tier-21']);
    assert.equal(posts(app)[1].active_round_id,server.roundId);
    assert.match(app.$('#challenge-round').textContent,/tier-21/);
  });
  await test('a delayed prior-hour response cannot move the clock back or start the new hour', async () => {
    const {app,server} = await boot({seed:true});
    const gate=deferred();
    server.after=async(req,result)=>{if(req.method==='GET' && req.profile_id==='tier-21') await gate.promise;return result;};
    await app.navigate(route(21));
    server.roundId++;
    app.sandbox.setSession(server.envelope({user,csrf_token:'next-round'}));
    gate.resolve(); await flush();
    assert.equal(posts(app).length,0);
    await app.sandbox.loadChallenge();
    assert.equal(posts(app).length,0);
    assert.equal(app.$('#empty-title').textContent,'Ready?');
    assert.equal(app.$('#challenge-active').hidden,true);
  });
  console.log(`\n${passed} round-access regressions passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

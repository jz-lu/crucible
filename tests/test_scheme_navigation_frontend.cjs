const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const user = {id: 'alice-id', handle: 'alice'};
const active = (envelope, csrf = 'csrf', account = user) => envelope({user: account, csrf_token: csrf, access: {mode: 'beta', status: 'active', allowed: true}});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const profile = {id: 'native-20', instance_size: 20, stage_number: 19, stage_label: 'Stage 19', shared_secret_bytes: 32,
  encoding: {shared_secret_bytes: 32}, parameters: {exact: '9007199254740993'}, correctness: {kind: 'fixture', failure_bound: '0'}};
const release = (id = 'a', version = '2', name = 'Scheme A') => ({id, version, name, profiles: [{...profile}],
  selection_kind: 'tier', record_inheritance: true, next_profile_id: profile.id, largest_recovered_profile_id: profile.id,
  recovered_profile_ids: [profile.id], participant_count: 2, first_recovery: {profile_id: profile.id, scheme_version: version, handle: 'alice'},
  size_parameter: {meaning: 'Fixture'}, protocol: {construction: name + ' full construction'},
  profile_metrics: {[profile.id]: {public_key_bytes: 17, ciphertext_bytes: 9}}});
const a = release(), old = release('a', '1', 'Archived A'), b = release('b', '1', 'Scheme B');
function compact(full) {
  const copy = {...full}; delete copy.protocol; delete copy.profile_metrics;
  copy.profiles = full.profiles.map(({parameters, encoding, correctness, shared_secret_bytes, ...labels}) => labels);
  return copy;
}
function navigation(revision = 'r1', values = [a, b]) {
  return {projection: 'navigation', catalogue_revision: revision, schemes: values.map(item => ({...compact(item), previous_releases: item.id === 'a' ? [compact(old)] : []}))};
}
const detail = (full, revision = 'r1') => ({projection: 'release', catalogue_revision: revision, scheme: full, current_version: full.id === 'a' ? '2' : '1'});
const queries = app => app.requests.filter(r => r.url.pathname === '/api/schemes').map(r => r.url.searchParams);
const reads = (app, projection) => queries(app).filter(query => query.get('projection') === projection);
const route = (full, view = 'protocol') => `#challenge/${full.id}/${full.version}?profile=${profile.id}&view=${view}`;
function responder(extra) {
  return (url, options, envelope) => {
    const override = extra?.(url, options, envelope); if (override !== undefined) return override;
    if (url.pathname === '/api/session') return active(envelope);
    if (url.pathname !== '/api/schemes') return undefined;
    if (url.searchParams.get('projection') === 'navigation') return envelope(navigation());
    if (url.searchParams.get('projection') === 'release') {
      const full = [a, old, b].find(item => item.id === url.searchParams.get('scheme_id') && item.version === url.searchParams.get('version'));
      assert.ok(full, 'exact release requested'); return envelope(detail(full));
    }
    throw new Error('Unexpected full catalogue request');
  };
}
let passed = 0;
async function test(name, run) { await run(); console.log('PASS ' + name); passed++; }
(async () => {
  await test('Schemes first render needs navigation only and leaves the access screen as soon as authorized', async () => {
    const gate = deferred();
    const app = await createApp({hash: '#challenge', responder: responder(url => url.pathname === '/api/schemes' ? gate.promise : undefined)});
    assert.equal(app.$('#access-workspace').hidden, true);
    assert.equal(app.$('#route-loading').hidden, false);
    assert.match(app.$('#route-loading').textContent, /KEM Schemes/);
    assert.equal(reads(app, 'navigation').length, 1); assert.equal(reads(app, 'release').length, 0);
    gate.resolve(app.envelope(navigation())); await flush();
    assert.equal(app.$('#route-loading').hidden, true);
    assert.match(app.$('#kem-catalogue').textContent, /Scheme AStage 19alice/);
    assert.equal(vm.runInContext('releaseDetails.size', app.sandbox), 0);
    assert.equal(vm.runInContext('state.schemes[0].profiles[0].parameters', app.sandbox), undefined);
  });
  await test('deep links overlap navigation and exact detail reads but never render partial metadata', async () => {
    const nav = deferred(), full = deferred();
    const app = await createApp({hash: route(a), responder: responder(url => {
      if (url.pathname === '/api/schemes') return url.searchParams.get('projection') === 'navigation' ? nav.promise : full.promise;
    })});
    assert.equal(reads(app, 'navigation').length, 1); assert.equal(reads(app, 'release').length, 1);
    assert.equal(app.$('#workspace').hidden, true);
    full.resolve(app.envelope(detail(a))); await flush();
    assert.equal(app.$('#workspace').hidden, true);
    nav.resolve(app.envelope(navigation())); await flush();
    assert.equal(app.$('#workspace').hidden, false);
    assert.match(app.$('#parameter-json').textContent, /9007199254740993/);
    assert.equal(app.requests.some(r => r.url.pathname === '/api/challenges'), false);
  });
  await test('late details cannot reopen a page and revisits use the exact already-loaded release', async () => {
    const pending = deferred();
    const app = await createApp({hash: '#challenge', responder: responder(url => {
      if (url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'release' && url.searchParams.get('scheme_id') === 'a') return pending.promise;
    })});
    await app.navigate(route(a)); await app.navigate('#about'); await app.navigate(route(b));
    assert.equal(app.$('#scheme-title').textContent, 'Scheme B');
    pending.resolve(app.envelope(detail(a))); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Scheme B');
    await app.navigate(route(a));
    assert.equal(app.$('#scheme-title').textContent, 'Scheme A');
    assert.equal(reads(app, 'release').length, 2);
  });
  await test('archived deep links retain their full description and never issue a new archived challenge', async () => {
    const app = await createApp({hash: route(old, 'challenge'), responder: responder()});
    assert.equal(app.$('#scheme-title').textContent, 'Archived A');
    assert.match(app.$('#empty-title').textContent, /Archived/);
    assert.equal(reads(app, 'release')[0].get('version'), '1');
    assert.equal(app.requests.some(r => r.options.method === 'POST'), false);
    app.$('#issue-button').click(); await flush();
    assert.match(app.location.hash, /#challenge\/a\/2/);
    assert.equal(app.requests.some(r => r.options.method === 'POST'), false);
  });
  await test('profile records get human stage labels from navigation without downloading protocols', async () => {
    const record = {scheme_id: 'a', scheme_name: 'Scheme A', scheme_version: '2', profile_id: profile.id, tier: 20,
      place: 1, submission_id: 'receipt', received_at: 100, is_current: true};
    const app = await createApp({hash: '#person/alice', responder: responder((url, options, envelope) => {
      if (url.pathname === '/api/people/alice') return envelope({person: {handle: 'alice', points: 0, first_records: [record], records: []}});
    })});
    assert.equal(app.$('#first-records').querySelector('.milestone-result').textContent, 'Stage 19');
    assert.equal(reads(app, 'release').length, 0);
  });
  await test('session rotation evicts cached and pending details, and late old-session bytes cannot return', async () => {
    const pending = deferred(); let count = 0;
    const app = await createApp({hash: '#challenge', responder: responder((url, options, envelope) => {
      if (url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'release' && ++count === 1) return pending.promise;
    })});
    await app.navigate(route(a));
    app.sandbox.setSession(active(app.envelope, 'new-csrf')); await flush();
    assert.equal(reads(app, 'release').length, 2);
    assert.equal(app.$('#scheme-title').textContent, 'Scheme A');
    pending.resolve(app.envelope(detail({...a, name: 'Old session data'}))); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Scheme A');
    assert.equal(vm.runInContext('selectedScheme().name', app.sandbox), 'Scheme A');
  });
  await test('same-revision background progress preserves DOM and focus without fetching another description', async () => {
    const app = await createApp({hash: route(a), responder: responder((url, options, envelope) => {
      if (url.pathname === '/api/schemes' && url.searchParams.has('progress_only')) return envelope({projection: 'progress', catalogue_revision: 'r1', releases: [a, old, b].map(item => ({id: item.id, version: item.version,
        participant_count: item.participant_count, recovered_profile_ids: item.recovered_profile_ids, largest_recovered_profile_id: item.largest_recovered_profile_id, next_profile_id: item.next_profile_id, first_recovery: item.first_recovery}))});
    })});
    const parameter = app.$('#parameter-json'), input = app.$('#profile-select'); input.value = '23'; input.focus();
    await app.sandbox.refreshProfiles({background: true});
    assert.equal(app.$('#parameter-json'), parameter); assert.equal(app.document.activeElement, input); assert.equal(input.value, '23');
    assert.equal(reads(app, 'release').length, 1);
  });
  await test('metadata revision changes replace only the selected full release and preserve exact stage mapping', async () => {
    let revision = 'r1';
    const app = await createApp({hash: route(a), responder: responder((url, options, envelope) => {
      if (url.pathname !== '/api/schemes') return undefined;
      if (url.searchParams.has('progress_only')) return envelope({projection: 'progress', catalogue_revision: revision, releases: []});
      if (url.searchParams.get('projection') === 'navigation') return envelope(navigation(revision));
      if (url.searchParams.get('projection') === 'release') return envelope(detail({...a, protocol: {construction: revision + ' full description'}}, revision));
    })});
    revision = 'r2'; await app.sandbox.refreshProfiles({background: true}); await flush();
    assert.equal(vm.runInContext('selectedScheme().protocol.construction', app.sandbox), 'r2 full description');
    assert.equal(reads(app, 'release').length, 2);
    assert.equal(reads(app, 'navigation').length, 2);
    assert.match(app.$('#size-slider-value').textContent, /19/);
  });
  await test('wrong release or revision responses fail closed without activating challenge controls', async () => {
    for (const payload of [detail(b), detail(a, 'other-revision'), {...detail(a), projection: 'navigation'}]) {
      const app = await createApp({hash: '#challenge', responder: responder((url, options, envelope) => {
        if (url.pathname === '/api/schemes' && url.searchParams.get('projection') === 'release') return envelope(payload);
      })});
      await app.navigate(route(a, 'challenge'));
      assert.equal(app.$('#workspace').hidden, true);
      assert.match(app.$('#route-loading').textContent, /could not be loaded|changed/);
      assert.equal(app.requests.some(r => r.url.pathname === '/api/challenges'), false);
      assert.equal(vm.runInContext('releaseDetails.size', app.sandbox), 0);
    }
  });
  await test('a new navigation revision starts a fresh detail read without joining an obsolete pending one', async () => {
    const oldRequest = deferred(); let revision = 'r1', detailReads = 0;
    const app = await createApp({hash: '#challenge', responder: responder((url, options, envelope) => {
      if (url.pathname !== '/api/schemes') return undefined;
      if (url.searchParams.get('projection') === 'navigation') return envelope(navigation(revision));
      if (url.searchParams.get('projection') === 'release') return detailReads++ === 0 ? oldRequest.promise : envelope(detail({...a, name: 'Fresh A'}, revision));
    })});
    await app.navigate(route(a));
    revision = 'r2';
    await app.sandbox.loadAuthorizedCatalogue(); await flush();
    assert.equal(detailReads, 2);
    assert.equal(app.$('#scheme-title').textContent, 'Fresh A');
    oldRequest.resolve(app.envelope(detail({...a, name: 'Obsolete A'}, 'r1'))); await flush();
    assert.equal(app.$('#scheme-title').textContent, 'Fresh A');
  });
  await test('explicit Take on survives sign-in and lazy metadata reloading, but navigation away cancels it', async () => {
    for (const leave of [false, true]) {
      let signedIn = false, issued = 0; const login = deferred();
      const app = await createApp({hash: route(a, 'leaderboard'), responder: responder((url, options, envelope) => {
        if (url.pathname === '/api/session') return envelope({user: signedIn ? user : null, csrf_token: signedIn ? 'signed-in' : 'anonymous', access: {mode: 'open', allowed: true}});
        if (url.pathname === '/api/auth/login') return login.promise.then(() => { signedIn = true; return envelope({user, csrf_token: 'signed-in', access: {mode: 'open', allowed: true}}); });
        if (url.pathname === '/api/challenges' && options.method === 'POST') {
          issued++; const clock = envelope({});
          return envelope({challenge: {id: 'issued', scheme_id: 'a', scheme_version: '2', profile_id: profile.id,
            round_id: clock.round.id, issued_at: clock.server_time, ends_at: clock.round.ends_at, status: 'open',
            public: {encoding: {shared_secret_bytes: 32}}}});
        }
      })});
      app.$('.tier-record-challenge').click(); await flush();
      assert.equal(app.$('#auth-dialog').open, true);
      app.$('#auth-handle').value = 'alice'; app.$('#auth-password').value = 'synthetic test password';
      app.$('#auth-form').dispatchEvent({type: 'submit'}); await flush();
      if (leave) await app.navigate('#about');
      login.resolve(); await flush();
      assert.equal(issued, leave ? 0 : 1);
      if (!leave) assert.equal(app.$('#challenge-active').hidden, false);
    }
  });

  await test('scheme pointer or keyboard intent starts one exact description read before clicking', async () => {
    const pending=deferred();
    const app=await createApp({responder:responder(url=>url.searchParams.get('projection')==='release'?pending.promise:undefined)});
    const link=app.$('#kem-catalogue').querySelector('.kem-name');link.dispatchEvent({type:'pointerenter'});link.dispatchEvent({type:'focus'});await flush();
    assert.equal(reads(app,'release').length,1);link.click();await flush();assert.equal(reads(app,'release').length,1);
    pending.resolve(app.envelope(detail(a)));await flush();assert.equal(app.$('#overview-workspace').hidden,false);
    assert.equal(app.requests.some(r=>r.url.pathname==='/api/challenges'),false);
  });
  await test('stage browsing paints its heading without building hidden protocol or formatting math', async () => {
    const app=await createApp({hash:route(a,'leaderboard'),responder:responder()});
    assert.equal(app.$('#scheme-title').textContent,'Scheme A');
    assert.doesNotMatch(app.$('#protocol-construction').textContent,/Scheme A full construction/);
    assert.equal(app.$('#parameter-facts').textContent.trim(),'');
    let mathCalls=0;app.window.CrucibleMath={render(node,value){mathCalls++;node.textContent=value;}};
    await app.navigate(route(b,'leaderboard'));
    assert.equal(app.$('#scheme-title').textContent,'Scheme B');assert.equal(mathCalls,0);
    app.$('#tab-protocol').click();await flush();
    assert.equal(app.$('#protocol-construction').textContent,'Scheme B full construction');
    assert.match(app.$('#parameter-json').textContent,/9007199254740993/);assert.ok(mathCalls>0);
    const before=mathCalls;app.$('#tab-leaderboard').click();await flush();
    await app.navigate(route(a,'leaderboard'));assert.equal(mathCalls,before);
    app.$('#scheme-specifics').open=true;app.$('#scheme-specifics').dispatchEvent({type:'toggle'});await flush();
    assert.equal(app.$('#scheme-construction-short').textContent,'Scheme A full construction');
    assert.equal(app.requests.some(r=>r.options.method==='POST'),false);
  });
  await test('record links prefetch only exact known releases, deduplicate and discard session-stale responses', async () => {
    const pending=deferred();
    const app=await createApp({responder:responder(url=>url.searchParams.get('projection')==='release'?pending.promise:undefined)});
    const link=app.$('#kem-catalogue').querySelector('.kem-progress');
    link.dispatchEvent({type:'focus'});link.dispatchEvent({type:'pointerenter'});await flush();
    assert.equal(reads(app,'release').length,1);assert.equal(reads(app,'release')[0].get('version'),'2');
    app.sandbox.setSession(active(app.envelope,'rotated'));await flush();
    pending.resolve(app.envelope(detail(a)));await flush();
    assert.equal(vm.runInContext('releaseDetails.size',app.sandbox),0);
    assert.equal(app.requests.some(r=>r.url.pathname==='/api/challenges'||r.options.method==='POST'),false);
  });
  console.log(`\n${passed} compact scheme navigation checks passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

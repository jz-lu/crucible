const {test, expect, signIn, gate, holdResponse} = require('./helpers.cjs');

const schemeId = 'teaching-dh-kem';
const cards = ['public', 'cipher', 'computation'];
const schemeRoute = version => '#scheme/' + schemeId + '/' + version;

// Decorate real authorized release reads with small, unmistakably synthetic
// measurements. This never publishes a resource report or touches hosted data.
async function resourceFixture(page) {
  const reads = [], holds = [];
  let publicBytes = 32;
  await page.route('**/api/protocol/resources?*', async route => {
    const query = new URL(route.request().url()).searchParams;
    const version = query.get('version'), bytes = publicBytes;
    if (query.get('scheme_id') !== schemeId || !['3', '4'].includes(version)) return route.fallback();
    const read = {version, projection: query.get('projection')}; reads.push(read);
    const hold = holds.find(item => !item.claimed && item.version === version);
    if (hold) hold.claimed = true;
    try {
      const response = await route.fetch();
      expect(response.status(), 'synthetic data must follow real release authorization').toBe(200);
      if (hold) { hold.arrived.resolve(); await hold.release.promise; }
      const implementation = {id: 'browser-cache-fixture', sha256: 'a'.repeat(64)};
      const method = 'b'.repeat(64);
      const operation = {complete: true, duration_ns: {n: 100, median: 2000, q1: 1000, q3: 3000}};
      const configurations = [20, 21].map(n => ({
        configuration: String(n), sizes_bytes: {public_key: bytes, ciphertext: 64, private_key: 48},
        communication: {fresh_exchange_bytes: bytes + 64, cached_exchange_bytes: 64},
        native_evidence: [{comparison_profile: method, implementation, platform_identified: true,
          computation: {keygen: operation, encapsulate: operation, decapsulate: operation},
          memory_components: {'keygen:rss': {complete: true, values: {n: 5, max: 8192}}}}],
      }));
      await route.fulfill({response, json: {scheme_id: schemeId, version, projection: 'charts', state: 'current',
        selected_historical: false, historical: [], resources: {configurations,
          measurement_profiles: {[method]: {profile: {method: {id: 'serialized-native-honest-v2'},
            environment: {os: 'Linux', cpu: 'Synthetic cache browser fixture'}, sampling: {workers: 5, trials: 20}}}}},
        publication: {id: 'c'.repeat(64), completeness: 'complete', received_at: 1800000000,
          binding: {implementation, profiles: [20, 21].map(n => ({profile_id: 'dh-' + n, configuration: String(n)}))},
          source: {repository: 'synthetic/browser-cache-fixture', commit: 'd'.repeat(40), run_id: '12345'}}}});
    } finally { if (hold) hold.delivered.resolve(); }
  });
  return {
    reads, setPublicBytes(value) { publicBytes = value; },
    count(version = '4') { return reads.filter(item => item.version === version).length; },
    holdNext(version = '4') {
      const hold = {version, claimed: false, arrived: gate(), release: gate(), delivered: gate()};
      holds.push(hold); return hold;
    },
    async close() {
      for (const hold of holds) hold.release.resolve();
      await Promise.all(holds.filter(hold => hold.claimed).map(hold => hold.delivered.promise));
      expect(reads.every(read => read.projection === 'charts'), 'browsing requests use the compact chart projection').toBe(true);
    },
  };
}

async function chartPointsReady(page, publicValue = '32') {
  for (const card of cards) await expect(page.locator('#overview-chart-' + card + ' .chart-point')).toHaveCount(2);
  await expect(page.locator('#overview-chart-public .chart-point').first()).toHaveAttribute('data-value', publicValue);
  await expect(page.locator('#overview-chart-cipher .chart-point').first()).toHaveAttribute('data-value', '64');
  await expect(page.locator('#overview-chart-computation .chart-point').first()).toHaveAttribute('data-value', '2');
}

// Observe the first paint after a real hash navigation, rather than waiting for
// eventual chart data and accidentally accepting a visible request waterfall.
async function firstNavigationFrame(page, version) {
  return page.evaluate(version => new Promise(resolve => {
    window.addEventListener('hashchange', () => requestAnimationFrame(() => resolve({
      screen: state.screen, version: state.schemeVersion,
      points: ['public', 'cipher', 'computation'].map(name => document.querySelectorAll('#overview-chart-' + name + ' .chart-point').length),
    })), {once: true});
    window.location.hash = '#scheme/teaching-dh-kem/' + version;
  }), version);
}

test('idle preparation paints all resource cards immediately and warm navigation keeps them through unrelated writes', async ({page}) => {
  const fixture = await resourceFixture(page);
  const metadata = await holdResponse(page, url => url.pathname === '/api/schemes' &&
    url.searchParams.get('projection') === 'release' && url.searchParams.get('version') === '4');
  let metadataArrived = false;
  metadata.arrived.then(() => { metadataArrived = true; });
  try {
    await page.goto('/#about');
    await signIn(page, 'alice', {navigate: false});
    await metadata.arrived;
    // Release metadata remains blocked: the independent report must already
    // start, rather than waiting until metadata or the visible page is ready.
    await expect.poll(() => fixture.count()).toBe(1);
    metadata.release(); await metadata.delivered;
    await page.waitForFunction(() => browseWarmState?.done.has('release:' + releaseKey('teaching-dh-kem', '4')) &&
      !browseWarmRunning && Boolean(completeRelease('teaching-dh-kem', '4')));
    const first = await firstNavigationFrame(page, '4');
    expect(first).toEqual({screen: 'overview', version: '4', points: [2, 2, 2]});
    await chartPointsReady(page);
    expect(fixture.count()).toBe(1);

    const publicCard = page.locator('#overview-chart-public');
    await publicCard.getByLabel('Measure', {exact: true}).selectOption('fresh');
    await page.evaluate(hash => { location.hash = hash; }, schemeRoute('3'));
    await chartPointsReady(page);
    expect(fixture.count('3')).toBe(1);
    expect(await firstNavigationFrame(page, '4')).toEqual({screen: 'overview', version: '4', points: [2, 2, 2]});
    await chartPointsReady(page, '96');
    await expect(publicCard.getByLabel('Measure', {exact: true})).toHaveValue('fresh');
    expect(fixture.count()).toBe(1);

    // A local private note mutation invalidates notebook reads, not immutable
    // resource evidence belonging to the same authenticated account.
    await page.evaluate(() => api('/api/notebook/notes', {idempotency_key: crypto.randomUUID(),
      content: {title: 'Resource cache browser fixture', claim: 'Synthetic cache check.',
        conditions: 'Disposable local database only.', body: 'Not a research finding.', tags: ['fixture']}}));
    await page.locator('#nav-about').click();
    expect(await firstNavigationFrame(page, '4')).toEqual({screen: 'overview', version: '4', points: [2, 2, 2]});
    await chartPointsReady(page, '96');
    expect(fixture.count()).toBe(1);

    const refresh = fixture.holdNext();
    await publicCard.getByText('Values, method & source', {exact: true}).click();
    const measure = publicCard.getByLabel('Measure', {exact: true});
    await measure.focus();
    await measure.evaluate(element => { element.dataset.cacheContinuity = 'retained'; });
    const before = await publicCard.boundingBox();
    // Invoke the actual refresh handler without moving the user's keyboard
    // focus, as a background refresh would do.
    await publicCard.getByRole('button', {name: 'Refresh resource measurements', exact: true}).evaluate(button => button.click());
    await refresh.arrived.promise;
    await chartPointsReady(page, '96');
    await expect(measure).toBeFocused();
    refresh.release.resolve(); await refresh.delivered.promise;
    await page.evaluate(() => CrucibleResources.load({id: 'teaching-dh-kem', version: '4'}, api));
    await expect(measure).toBeFocused();
    await expect(measure).toHaveAttribute('data-cache-continuity', 'retained');
    const after = await publicCard.boundingBox();
    expect(Math.abs(after.height - before.height)).toBeLessThan(2);
    expect(fixture.count()).toBe(2);
  } finally {
    metadata.release(); if (metadataArrived) await metadata.delivered;
    await metadata.remove(); await fixture.close();
  }
});

test('visible navigation joins an unfinished idle report and fills all three cards from one request', async ({page}) => {
  const fixture = await resourceFixture(page), pending = fixture.holdNext();
  try {
    await page.goto('/#about');
    await signIn(page, 'bob', {navigate: false});
    await pending.arrived.promise;
    await page.waitForFunction(() => Boolean(completeRelease('teaching-dh-kem', '4')));
    expect(await firstNavigationFrame(page, '4')).toEqual({screen: 'overview', version: '4', points: [0, 0, 0]});
    expect(fixture.count()).toBe(1);
    const measure = page.locator('#overview-chart-public').getByLabel('Measure', {exact: true});
    await measure.focus();
    pending.release.resolve(); await pending.delivered.promise;
    await chartPointsReady(page);
    await expect(measure).toBeFocused();
    await expect(page.locator('#overview-chart-computation .resource-method')).toContainText('Synthetic cache browser fixture');
    expect(fixture.count()).toBe(1);
  } finally { await fixture.close(); }
});

test('signout clears resource values and choices, and a late old-account refresh cannot overwrite a new account', async ({page}) => {
  await page.setViewportSize({width: 390, height: 1000});
  const fixture = await resourceFixture(page);
  try {
    await page.goto('/' + schemeRoute('4'));
    await signIn(page, 'alice', {navigate: false});
    await chartPointsReady(page);
    const card = page.locator('#overview-chart-public');
    await card.getByLabel('Measure', {exact: true}).selectOption('fresh');
    await card.getByText('Values, method & source', {exact: true}).click();
    const oldAccount = fixture.holdNext();
    await card.getByRole('button', {name: 'Refresh resource measurements', exact: true}).click();
    await oldAccount.arrived.promise;
    await page.locator('#logout-button').click();
    await expect(page.locator('#access-auth-actions')).toBeVisible();
    await expect(page.locator('#overview-graphs .chart-point')).toHaveCount(0);
    fixture.setPublicBytes(96);
    await signIn(page, 'bob', {navigate: false});
    if (new URL(page.url()).hash !== schemeRoute('4')) await page.evaluate(hash => { location.hash = hash; }, schemeRoute('4'));
    await chartPointsReady(page, '96');
    await expect(card.getByLabel('Measure', {exact: true})).toHaveValue('public_key');
    expect(fixture.count()).toBe(3);
    oldAccount.release.resolve(); await oldAccount.delivered.promise;
    // Drain the old response handler before checking that neither its values
    // nor the prior user's selected view can re-enter the current document.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await chartPointsReady(page, '96');
    await expect(card.getByLabel('Measure', {exact: true})).toHaveValue('public_key');
    await expect(page.locator('#account-handle')).toHaveText('browser_bob');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { await fixture.close(); }
});

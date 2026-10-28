const {test, expect, signIn, gate} = require('./helpers.cjs');

// UI-only measurements decorate an authorized fixture response. This never
// registers or publishes evidence; backend identity/ZIP checks have separate tests.
for (const width of [1440, 390]) {
  test(`resource views retain focus and show scoped RAM at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    await page.route('**/api/schemes?*', async route => {
      const response=await route.fetch(); if(response.status()!==200) return route.fulfill({response});
      const data=await response.json();
      for(const scheme of data.schemes || (data.scheme ? [data.scheme] : [])) if(scheme.id==='teaching-dh-kem' && String(scheme.version)==='4') {
        scheme.record_inheritance=false;scheme.recovered_profile_ids=['dh-20','dh-160'];scheme.largest_recovered_profile_id='dh-160';
      }
      return route.fulfill({response,json:data});
    });
    await page.route('**/api/protocol/resources?*', async route => {
      const response = await route.fetch();
      if (response.status() !== 200) return route.fulfill({response});
      const query = new URL(route.request().url()).searchParams;
      const scheme = query.get('scheme_id'), version = query.get('version');
      if (scheme !== 'teaching-dh-kem' || version !== '4') return route.fulfill({response});
      const impl = {id:'browser-fixture',sha256:'a'.repeat(64)}, method='b'.repeat(64);
      const op = {complete:true,duration_ns:{n:100,median:2000,q1:1000,q3:3000}};
      const rows = [20,21].map(n=>({configuration:String(n),sizes_bytes:{public_key:32,ciphertext:64,private_key:48},
        communication:{fresh_exchange_bytes:96,cached_exchange_bytes:64},
        native_evidence:[{comparison_profile:method,implementation:impl,platform_identified:true,
          computation:{keygen:op,encapsulate:op,decapsulate:op}, profile:{environment:{cpu:'Synthetic browser fixture'}},
          memory_components:{'keygen:rss':{complete:true,values:{n:5,max:8192}}}}],
        stack_evidence:[{implementation:impl,comparison_profile:method,operations:{keygen:{measured:5,planned:5,max_observed_bytes:256}}}],
        memory_evidence:[{implementation:impl,comparison_profile:method,operations:{keygen:{measured:2,planned:2,max_observed_bytes:1024}}}]}));
      await route.fulfill({response,json:{scheme_id:scheme,version,state:'current',historical:[],publication:{completeness:'complete',id:'c'.repeat(64),received_at:1800000000,
        binding:{implementation:impl,profiles:[20,21].map(n=>({profile_id:'dh-'+n,configuration:String(n)}))},
        source:{repository:'countableclouds/crucible-schemes',commit:'d'.repeat(40),run_id:'12345'}},resources:{configurations:rows}}});
    });
    await page.goto('/#scheme/teaching-dh-kem/4');
    await signIn(page,'bob',{navigate:false});
    const card=page.locator('#overview-chart-computation');
    await expect(page.locator('#overview-badge-computation')).toHaveText('Current');
    for(const name of ['public','cipher','computation','security']) {
      const graph=page.locator('#overview-chart-'+name);
      await expect(graph.locator('.chart-recovery-line')).toHaveCount(1);
      await expect(graph.locator('.chart-recovery-line')).toHaveAttribute('data-profile-id','dh-160');
      await expect(graph.locator('.chart-recovery-area')).toBeVisible();
    }
    const measure=card.getByLabel('Measure',{exact:true});
    await measure.focus();
    await measure.selectOption('rss');
    await expect(measure).toBeFocused();
    await expect(card).toContainText('Maximum observed fresh-worker resident RAM');
    await expect(card.locator('.chart-point')).toHaveCount(2);
    await expect(card.locator('.chart-point').first()).toHaveAttribute('data-value','8192');
    await measure.selectOption('heap_stack');
    await expect(card).toContainText('same Massif snapshot');
    await expect(card.locator('.chart-point').first()).toHaveAttribute('data-value','1024');
    await card.getByText('Values, method & source',{exact:true}).click();
    await expect(card.getByRole('link',{name:'Download measurements & evidence'})).toHaveAttribute('href',/\/api\/resource-publications\/[a-f0-9]{64}\/download/);
    await expect(card).toContainText('browser-fixture');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('resource-memory.png'),fullPage:true});
    await page.locator('#nav-challenge').click();
    await page.goto('/#scheme/teaching-dh-kem/4');
    await expect(page.locator('#overview-badge-computation')).toHaveText('Current');
  });

  test(`missing resource state preserves card geometry and requires explicit legacy choice at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    const ready = gate(), release = gate();
    await page.route('**/api/protocol/resources?*', async route => {
      const response = await route.fetch();
      if (response.status() !== 200) return route.fulfill({response});
      const query = new URL(route.request().url()).searchParams;
      ready.resolve(); await release.promise;
      return route.fulfill({response, json: {scheme_id: query.get('scheme_id'), version: query.get('version'),
        state: 'outdated', publication: null, resources: null, historical: []}});
    });
    await page.goto('/#scheme/teaching-dh-kem/4');
    await signIn(page, 'bob', {navigate:false});
    await ready.promise;
    const chart = page.locator('#overview-chart-public');
    await expect(chart.getByLabel('Report', {exact:true})).toBeVisible();
    const card = chart.locator('..'), initial = await card.boundingBox();
    await expect(chart.locator('.chart-point')).toHaveCount(0);
    release.resolve();
    await expect(chart).toContainText('Resources not calculated');
    await expect(page.locator('#overview-badge-public')).toHaveText('Outdated');
    const settled = await card.boundingBox();
    expect(Math.abs(initial.height - settled.height)).toBeLessThan(2);
    await chart.getByLabel('Report', {exact:true}).selectOption('legacy');
    await expect(page.locator('#overview-badge-public')).toHaveText('Earlier site reference');
    await expect(chart.locator('.chart-point').first()).toBeVisible();
    await chart.getByLabel('Report', {exact:true}).selectOption('current');
    await expect(chart).toContainText('Resources not calculated');
    await expect(chart.locator('.chart-point')).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('resource-not-calculated.png'),fullPage:true});
  });
}

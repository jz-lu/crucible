const {test, expect, signIn} = require('./helpers.cjs');

// Read the browser's own screenshot pixels without a second image dependency.
async function railPixels(page, slider, selected, path) {
  const png = await slider.screenshot({path, scale:'css'});
  return page.evaluate(async ({bytes, selected}) => {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type:'image/png'}));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext('2d'); context.drawImage(bitmap, 0, 0); bitmap.close();
    const color = stage => {
      const x = Math.round(7 + (canvas.width - 14) * (stage - 20) / 280);
      return [...context.getImageData(x, Math.floor(canvas.height / 2), 1, 1).data].slice(0, 3);
    };
    const cssColor = value => {
      const sample = new OffscreenCanvas(1, 1).getContext('2d'); sample.fillStyle = value;
      sample.fillRect(0, 0, 1, 1); return [...sample.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const css = getComputedStyle(document.documentElement);
    return {first:color(50), gap:color(180), last:color(250), thumb:color(selected),
      gray:cssColor(css.getPropertyValue('--line')), blue:cssColor(css.getPropertyValue('--primary'))};
  }, {bytes:[...png], selected});
}

function closeColor(actual, expected) {
  actual.forEach((value, index) => expect(Math.abs(value - expected[index])).toBeLessThanOrEqual(3));
}

for (const width of [1440, 390]) {
  test(`an exact-profile rail renders separate recovered segments and the correct thumb at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    const posts = [];
    page.on('request', request => {if (request.method() === 'POST') posts.push(new URL(request.url()).pathname);});
    // Transform only metadata returned by real authorized fixture GETs. This
    // exercises exact-profile CSS with the portable DH ladder, not Frodo KEM
    // generation, receipt acceptance, or a real recovery claim.
    await page.route('**/api/schemes?*', async route => {
      const response = await route.fetch();
      if (response.status() !== 200) return route.fulfill({response});
      const data = await response.json();
      const decorate = release => {
        if (release.id === 'teaching-dh-kem' && release.version === '4') {
          release.record_inheritance = false;
          for (const profile of release.profiles || []) profile.record_inheritance = false;
          release.recovered_profile_ids = [40, 240].flatMap(start => Array.from({length:20}, (_, i) => 'dh-' + (start + i)));
          release.largest_recovered_profile_id = 'dh-259'; release.next_profile_id = 'dh-20';
        }
        (release.previous_releases || []).forEach(decorate);
      };
      (data.schemes || data.releases || []).forEach(decorate); if (data.scheme) decorate(data.scheme);
      await route.fulfill({response, json:data});
    });
    await page.goto('/#challenge/teaching-dh-kem/4?profile=dh-40&view=protocol');
    await signIn(page, 'bob', {navigate:false});
    const slider = page.locator('#size-slider');
    await expect(slider).toHaveValue('20');
    await expect(slider).toHaveAttribute('max', '280');
    await expect(slider).toHaveAttribute('style', /--recovery-track: linear-gradient/);
    await expect(slider).toHaveAttribute('style', /--recovery-thumb: #278451/);
    const recovered = await railPixels(page, slider, 40, testInfo.outputPath('exact-recovered-rail.png'));
    for (const value of [recovered.first, recovered.last, recovered.thumb]) closeColor(value, [39, 132, 81]);
    closeColor(recovered.gap, recovered.gray);
    await page.screenshot({path:testInfo.outputPath('exact-recovered-stage.png'), fullPage:true});
    await page.locator('#profile-select').fill('150'); await page.locator('#jump-size').click();
    await expect(slider).toHaveValue('130');
    await expect(slider).not.toHaveAttribute('style', /--recovery-thumb/);
    const unsolved = await railPixels(page, slider, 150, testInfo.outputPath('exact-unsolved-rail.png'));
    closeColor(unsolved.first, [39, 132, 81]); closeColor(unsolved.last, [39, 132, 81]);
    closeColor(unsolved.gap, unsolved.gray); closeColor(unsolved.thumb, unsolved.blue);
    await page.screenshot({path:testInfo.outputPath('exact-unsolved-stage.png'), fullPage:true});
    expect(posts).toEqual(['/api/auth/login']);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

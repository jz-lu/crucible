const {test, expect, signIn} = require('./helpers.cjs');

// Read the browser's own screenshot pixels without a second image dependency.
async function railPixels(page, slider, selected, path, {minimum=20, count=281, samples={first:50,gap:180,last:250}} = {}) {
  const png = await slider.screenshot({path, scale:'css'});
  return page.evaluate(async ({bytes, selected, minimum, count, samples}) => {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type:'image/png'}));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext('2d'); context.drawImage(bitmap, 0, 0); bitmap.close();
    const color = stage => {
      const x = Math.round(7 + (canvas.width - 14) * (stage - minimum) / (count - 1));
      return [...context.getImageData(x, Math.floor(canvas.height / 2), 1, 1).data].slice(0, 3);
    };
    const cssColor = value => {
      const sample = new OffscreenCanvas(1, 1).getContext('2d'); sample.fillStyle = value;
      sample.fillRect(0, 0, 1, 1); return [...sample.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const css = getComputedStyle(document.documentElement);
    return {...Object.fromEntries(Object.entries(samples).map(([key,stage])=>[key,color(stage)])), thumb:color(selected),
      gray:cssColor(css.getPropertyValue('--line')), blue:cssColor(css.getPropertyValue('--primary'))};
  }, {bytes:[...png], selected, minimum, count, samples});
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

  test(`a six-position exact rail stops at the recovered stage and refreshes while focused at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:1000});
    const posts=[]; let recovered=[20,21,22];
    page.on('request',request=>{if(request.method()==='POST')posts.push(new URL(request.url()).pathname);});
    // Six rendering positions copied from real authorized portable metadata.
    // No ML-KEM generation, receipt acceptance, or production recovery claim.
    await page.route('**/api/schemes?*',async route=>{
      const response=await route.fetch();
      if(response.status()!==200)return route.fulfill({response});
      const data=await response.json();
      const decorate=release=>{
        if(release.id==='teaching-dh-kem'&&release.version==='4'){
          release.record_inheritance=false;
          if(release.profiles)release.profiles=release.profiles.filter(profile=>/^dh-2[0-5]$/.test(profile.id)).map(profile=>{
            const stage=Number(profile.id.slice(3))-19;
            return {...profile,stage_number:stage,instance_size:stage,stage_position:stage,stage_label:'Stage '+stage,record_inheritance:false};
          });
          release.default_progression={...release.default_progression,stage_count:6};
          release.recovered_profile_ids=recovered.map(stage=>'dh-'+stage);
          release.largest_recovered_profile_id='dh-'+Math.max(...recovered);
          release.next_profile_id='dh-'+[20,21,22,23,24,25].find(stage=>!recovered.includes(stage));
        }
        (release.previous_releases||[]).forEach(decorate);
      };
      (data.schemes||data.releases||[]).forEach(decorate);if(data.scheme)decorate(data.scheme);
      await route.fulfill({response,json:data});
    });
    await page.goto('/#challenge/teaching-dh-kem/4?profile=dh-25&view=protocol');
    await signIn(page,'bob',{navigate:false});
    const slider=page.locator('#size-slider');
    await expect(slider).toHaveValue('5');await expect(slider).toHaveAttribute('max','5');
    await slider.focus();
    const geometry={minimum:1,count:6,samples:{before:2.95,after:3.25}};
    const before=await railPixels(page,slider,6,testInfo.outputPath('six-stage-end-at-three.png'),geometry);
    closeColor(before.before,[39,132,81]);closeColor(before.after,before.gray);closeColor(before.thumb,before.blue);
    const hash=await page.evaluate(()=>location.hash);
    recovered=[20,21,22,23];
    await page.evaluate(()=>refreshProfiles({background:true}));
    await expect(slider).toBeFocused();await expect(slider).toHaveValue('5');
    expect(await page.evaluate(()=>location.hash)).toBe(hash);
    const after=await railPixels(page,slider,6,testInfo.outputPath('six-stage-end-at-four.png'),
      {minimum:1,count:6,samples:{before:3.95,after:4.25}});
    closeColor(after.before,[39,132,81]);closeColor(after.after,after.gray);closeColor(after.thumb,after.blue);
    recovered=[20,22];
    await page.evaluate(()=>refreshProfiles({background:true}));
    const isolated=await railPixels(page,slider,6,testInfo.outputPath('six-stage-isolated-three.png'),
      {minimum:1,count:6,samples:{marker:3,left:2.9,right:3.1,gap:2}});
    closeColor(isolated.marker,[39,132,81]);
    for(const value of [isolated.left,isolated.right,isolated.gap])closeColor(value,isolated.gray);
    expect(posts).toEqual(['/api/auth/login']);
  });
}

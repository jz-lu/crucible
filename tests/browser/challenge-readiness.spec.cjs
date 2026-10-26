const {test, expect, signIn, holdResponse} = require('./helpers.cjs');

const scheme = 'teaching-dh-kem';
const version = '4';

async function readyGeometry(page) {
  return page.locator('#challenge-empty').evaluate(element => {
    const rect = element.getBoundingClientRect();
    const heading = element.querySelector('h3');
    const title = heading.getBoundingClientRect();
    const style = getComputedStyle(heading);
    return {x: rect.x + scrollX, y: rect.y + scrollY, width: rect.width, height: rect.height,
      titleX: title.x + scrollX, titleY: title.y + scrollY, titleHeight: title.height,
      font: style.font, letterSpacing: style.letterSpacing};
  });
}

function sameGeometry(actual, expected) {
  for (const field of ['x', 'y', 'width', 'height', 'titleX', 'titleY', 'titleHeight']) {
    expect(Math.abs(actual[field] - expected[field]), `Ready frame ${field}`).toBeLessThan(1);
  }
  expect(actual.font).toBe(expected.font);
  expect(actual.letterSpacing).toBe(expected.letterSpacing);
}

for (const [width, profile] of [[1440, 'dh-30'], [390, 'dh-32']]) {
  test(`Ready stays usable while status is pending, and queues one deliberate request at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    const route = `/#challenge/${scheme}/${version}?profile=${profile}&view=challenge`;
    const summaryPath = url => url.pathname === '/api/challenges' &&
      url.searchParams.get('scheme_id') === scheme && url.searchParams.get('scheme_version') === version &&
      url.searchParams.get('profile_id') === profile && url.searchParams.get('summary') === 'true';
    // The issuance URL carries its exact target in the POST body, while the
    // summary GET carries it in the query. Both real responses are held before
    // the first action that could request either one.
    const summary = await holdResponse(page, summaryPath);
    const issuance = await holdResponse(page, url => url.pathname === '/api/challenges' &&
      url.searchParams.get('summary') === 'true' && !url.searchParams.has('profile_id'));
    let summaryArrived = false, issuanceArrived = false;
    const pendingSummary = summary.arrived.then(response => {summaryArrived = true; return response;});
    const pendingIssuance = issuance.arrived.then(response => {issuanceArrived = true; return response;});
    const reads = [], posts = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (summaryPath(url) && request.method() === 'GET') reads.push(request.url());
      if (url.pathname === '/api/challenges' && request.method() === 'POST') posts.push(request.postDataJSON());
    });
    let challengeId;
    try {
      await signIn(page, 'bob');
      // Stop the setup document without changing real authentication or the
      // normal background preparation preference of the tested document.
      await page.goto('about:blank');
      expect(reads).toHaveLength(0); expect(posts).toHaveLength(0);
      await page.goto(route);
      const summaryResponse = await pendingSummary;
      expect(summaryResponse.status()).toBe(200);
      const data = await summaryResponse.json();
      expect(data.challenge).toBeNull();
      expect(data.generation_admission.admitted).toBe(true);
      await expect(page.locator('#challenge-empty')).toBeVisible();
      await expect(page.locator('#empty-title')).toHaveText('Ready?');
      await expect(page.locator('#issue-button')).toBeEnabled();
      await expect(page.locator('#challenge-loading')).toHaveAttribute('role', 'status');
      expect(await page.locator('#challenge-loading').evaluate(element =>
        getComputedStyle(element).clipPath)).toBe('inset(50%)');
      await expect(page.locator('#challenge-active')).toBeHidden();
      await expect(page.locator('#challenge-details')).toBeHidden();
      await expect(page.locator('#manual-instance-tools')).toBeHidden();
      await expect(page.locator('#open-submission')).toBeDisabled();
      expect(await page.locator('#panel-challenge').innerText()).not.toMatch(/Loading|Your instance is ready|Preparing/);
      expect(reads).toHaveLength(1); expect(posts).toHaveLength(0);
      const ready = await readyGeometry(page);
      await expect(page.locator('#challenge-instance-rule')).toHaveText('One answer per instance.');
      await expect(page.locator('#challenge-empty .section-number, #panel-challenge .workflow')).toHaveCount(0);
      await expect(page.locator('#scheme-clock')).toBeVisible();
      await expect(page.locator('#manual-tools summary')).toBeVisible();
      expect(ready.height, 'compact entry card without the former 190px desktop minimum').toBeLessThanOrEqual(width === 1440 ? 160 : 220);
      const actionFrame = await page.locator('#issue-button').boundingBox();
      expect(actionFrame.x + actionFrame.width).toBeLessThanOrEqual(ready.x + ready.width);
      if (width === 390) {
        expect(Math.abs(actionFrame.x - ready.titleX), 'mobile action aligns with the Ready text').toBeLessThan(1);
      }
      const copy = await page.locator('#empty-description').textContent();
      await page.screenshot({path: testInfo.outputPath('ready-status-pending.png'), fullPage: true});

      await page.locator('#issue-button').click();
      await expect(page.locator('#issue-button')).toBeDisabled();
      await expect(page.locator('#issue-button')).toHaveText('Preparing your instance…');
      await expect(page.locator('#empty-title')).toHaveText('Ready?');
      await expect(page.locator('#empty-description')).toHaveText(copy);
      sameGeometry(await readyGeometry(page), ready);
      // Disabled repeated clicks cannot duplicate or shortcut the queued
      // request while the server's current-instance response remains held.
      await page.locator('#issue-button').evaluate(element => {element.click(); element.click();});
      expect(reads).toHaveLength(1); expect(posts).toHaveLength(0);
      await page.locator('#nav-about').focus();
      summary.release(); await summary.delivered;
      const issuedResponse = await pendingIssuance;
      expect(issuedResponse.status()).toBe(200);
      const issued = await issuedResponse.json();
      challengeId = issued.challenge.id;
      expect(issued.challenge.profile_id).toBe(profile);
      expect(posts).toHaveLength(1);
      expect(posts[0]).toMatchObject({scheme_id: scheme, scheme_version: version, profile_id: profile});
      await expect(page.locator('#challenge-empty')).toBeVisible();
      await expect(page.locator('#issue-button')).toBeDisabled();
      await expect(page.locator('#issue-button')).toHaveText('Preparing your instance…');
      await expect(page.locator('#challenge-active')).toBeHidden();
      await expect(page.locator('#nav-about')).toBeFocused();
      sameGeometry(await readyGeometry(page), ready);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: testInfo.outputPath('generation-response-pending.png'), fullPage: true});
      issuance.release(); await issuance.delivered;
      await expect(page.locator('#challenge-active')).toBeVisible();
      await expect(page.locator('#use-agent')).toBeEnabled();
      await expect(page.locator('#target-key')).toContainText('32-byte key');
      await expect(page.locator('#challenge-error')).toBeHidden();
      await expect(page.locator('#nav-about')).toBeFocused();
      expect(posts).toHaveLength(1); expect(reads).toHaveLength(1);
    } finally {
      summary.release(); issuance.release();
      if (summaryArrived) await summary.delivered;
      if (issuanceArrived) await issuance.delivered;
      await summary.remove(); await issuance.remove();
    }

    // A fresh document has no display cache. The same deliberate click must
    // open the real existing instance after its GET, never issue another one.
    await page.goto('about:blank');
    const existing = await holdResponse(page, summaryPath);
    let existingArrived = false;
    const pendingExisting = existing.arrived.then(response => {existingArrived = true; return response;});
    try {
      await page.goto(route);
      const response = await pendingExisting;
      expect((await response.json()).challenge.id).toBe(challengeId);
      await expect(page.locator('#empty-title')).toHaveText('Ready?');
      await expect(page.locator('#issue-button')).toBeEnabled();
      await expect(page.locator('#challenge-active')).toBeHidden();
      await page.locator('#issue-button').click();
      await expect(page.locator('#issue-button')).toHaveText('Preparing your instance…');
      await expect(page.locator('#issue-button')).toBeDisabled();
      expect(posts).toHaveLength(1);
      await page.locator('#nav-about').focus();
      existing.release(); await existing.delivered;
      await expect(page.locator('#challenge-active')).toBeVisible();
      await expect(page.locator('#download-button')).toHaveAttribute('href', '/api/challenges/' + encodeURIComponent(challengeId) + '/download');
      await expect(page.locator('#nav-about')).toBeFocused();
      expect(posts).toHaveLength(1); expect(reads).toHaveLength(2);
    } finally {
      existing.release();
      if (existingArrived) await existing.delivered;
      await existing.remove();
    }
  });
}

for (const width of [1440, 390]) {
  test(`overview and challenge share the round clock without an issuance action at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height: 1000});
    await signIn(page);
    await page.goto('about:blank');
    const summary = await holdResponse(page, url => url.pathname === '/api/challenges' && url.searchParams.has('profile_id'));
    let arrived = false;
    const pending = summary.arrived.then(response => {arrived = true; return response;});
    const posts = [];
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/challenges') posts.push(request.url());
    });
    try {
      await page.goto('/#scheme/' + scheme + '/' + version);
      const clock = page.locator('#overview-clock'), action = page.locator('#overview-challenges');
      await expect(clock).toBeVisible(); await expect(action).toBeVisible();
      await expect(clock.locator('.round-time')).toHaveText('Round ends in');
      await expect(page.locator('#overview-countdown')).toHaveText(/^\d{2}:\d{2}$/);
      await expect(page.locator('#overview-countdown')).toHaveAttribute('aria-label', /until the shared round ends/);
      const boxes = await page.locator('.overview-entry-actions').evaluate(element => {
        const rect = selector => {
          const r = element.querySelector(selector).getBoundingClientRect();
          return {x:r.x,y:r.y,width:r.width,height:r.height};
        };
        return {clock:rect('#overview-clock'),action:rect('#overview-challenges'),width:innerWidth};
      });
      for (const box of [boxes.clock, boxes.action]) {
        expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      if (width === 1440) {
        expect(boxes.clock.x + boxes.clock.width).toBeLessThanOrEqual(boxes.action.x);
        expect(Math.abs(boxes.clock.y + boxes.clock.height / 2 - boxes.action.y - boxes.action.height / 2)).toBeLessThanOrEqual(1);
      } else {
        const separateRows = boxes.action.y >= boxes.clock.y + boxes.clock.height;
        const beside = boxes.action.x >= boxes.clock.x + boxes.clock.width;
        expect(separateRows || beside, 'mobile clock and action do not overlap').toBe(true);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(posts).toHaveLength(0);
      await page.screenshot({path:testInfo.outputPath('overview-round-clock.png'),fullPage:true});
      await action.click();
      await page.locator('#tab-challenge').click(); await pending;
      await expect(page.locator('#scheme-clock')).toBeVisible();
      await expect(page.locator('#scheme-clock .round-time')).toHaveText('Challenge closes in');
      const seconds = await page.evaluate(() => {
        const parse = selector => {
          const text = document.querySelector(selector).textContent.trim();
          if (!/^\d{2}:\d{2}$/.test(text)) throw Error('Invalid round clock');
          const [minutes, seconds] = text.split(':').map(Number); return minutes * 60 + seconds;
        };
        return [parse('#overview-countdown'), parse('#countdown')];
      });
      expect(Math.abs(seconds[0] - seconds[1])).toBeLessThanOrEqual(1);
      expect(posts).toHaveLength(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path:testInfo.outputPath('challenge-round-clock.png'),fullPage:true});
      // The reusable actor may already have started this round in another
      // journey. Leave before releasing its status so this clock-only check
      // never requests a further instance through normal round continuation.
      await page.locator('#nav-about').click();
      await expect(page.locator('#about-workspace')).toBeVisible();
      summary.release(); await summary.delivered;
      expect(posts).toHaveLength(0);
    } finally {
      summary.release();
      if (arrived) await summary.delivered;
      await summary.remove();
    }
  });
}

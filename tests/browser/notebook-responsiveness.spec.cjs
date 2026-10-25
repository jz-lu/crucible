const {test, expect, signIn, notebookReady, useDataSaver, holdResponse} = require('./helpers.cjs');

// Exercise the paginated server fallback explicitly. Enabled complete-index
// filtering and background Timeline preparation have dedicated warmup tests.
test.beforeEach(async ({page}) => { await useDataSaver(page); });

const list = page => page.locator('#nb-note-list');
const filter = page => page.locator('#notebook-workspace [name="visibility"]');
const timeline = page => page.locator('#notebook-workspace [data-nb="timeline"]');
const timelineTab = page => page.locator('#notebook-workspace [data-nb="timeline-view"]');
const notesTab = page => page.locator('#notebook-workspace [data-nb="notes-view"]');
const listRequest = scope => url => url.pathname === '/api/notebook/notes' && url.searchParams.get('scope') === scope;

test('rapid filters keep admitted rows stable until only the newest real response arrives', async ({page}) => {
  await signIn(page); await notebookReady(page);
  const search = page.locator('#notebook-workspace [data-nb="search"]');
  await expect(search).toHaveAttribute('placeholder', 'Search titles, summaries or tags…');
  const original = await list(page).locator('.nb-entry').first().elementHandle();
  const human = await holdResponse(page, listRequest('human'));
  const mine = await holdResponse(page, listRequest('mine'));
  try {
    await filter(page).focus();
    await filter(page).selectOption('human'); await human.arrived;
    await expect(list(page)).toHaveAttribute('aria-busy', 'true');
    await expect(list(page)).toContainText('Alice browser-only private note');
    expect(await original.evaluate(element => element.isConnected)).toBe(true);
    await expect(list(page).getByRole('button', {name: 'Edit', exact: true})).toBeDisabled();
    await filter(page).selectOption('mine'); await mine.arrived;
    human.release(); await human.delivered;
    await expect(list(page)).toContainText('Alice browser-only private note');
    await expect(list(page)).toHaveAttribute('aria-busy', 'true');
    await expect(filter(page)).toBeFocused();
    mine.release(); await mine.delivered;
    await expect(list(page)).not.toHaveAttribute('aria-busy', 'true');
    await expect(filter(page)).toHaveValue('mine');
    await expect(filter(page)).toBeFocused();
    await expect(list(page).getByRole('button', {name: 'Edit', exact: true})).toBeEnabled();
    await expect(search).toHaveAttribute('placeholder', 'Search titles, summaries or tags…');
  } finally {
    human.release(); mine.release();
    await human.delivered; await mine.delivered;
    await human.remove(); await mine.remove();
  }
});

test('completed filter replacement and browser history keep the requested query and keyboard focus', async ({page}) => {
  await signIn(page); await notebookReady(page);
  await page.evaluate(() => {window.location.hash = '#notebook?filter=mine';});
  await expect(filter(page)).toHaveValue('mine'); await expect(list(page)).not.toHaveAttribute('aria-busy', 'true');
  const pending = await holdResponse(page, listRequest('human'));
  try {
    await page.evaluate(() => {window.location.hash = '#notebook?filter=human';}); await pending.arrived;
    await list(page).locator('.nb-title').filter({hasText: 'Alice browser-only private note'}).focus();
    pending.release(); await pending.delivered;
    await expect(list(page)).toContainText('No matching notes.');
    await expect(list(page)).toBeFocused();
  } finally {pending.release(); await pending.delivered; await pending.remove();}
  await page.goBack();
  await expect(filter(page)).toHaveValue('mine'); await notebookReady(page);
  await expect(list(page)).not.toHaveAttribute('aria-busy', 'true');
  await page.goForward();
  await expect(filter(page)).toHaveValue('human'); await expect(list(page)).toContainText('No matching notes.');
  await expect(list(page)).not.toHaveAttribute('aria-busy', 'true');
});

test('Timeline revisits paint the known aggregate before fresh validation and keep unchanged SVG', async ({page}) => {
  await signIn(page); await notebookReady(page);
  await timelineTab(page).click(); await expect(timeline(page).locator('svg')).toBeVisible();
  const total = await timeline(page).locator('.nb-timeline-total').textContent();
  await notesTab(page).click(); await notebookReady(page);
  const pending = await holdResponse(page, url => url.pathname === '/api/notebook/timeline');
  try {
    await timelineTab(page).click(); await pending.arrived;
    await expect(timeline(page).locator('svg')).toBeVisible();
    await expect(timeline(page).locator('.nb-timeline-total')).toHaveText(total);
    await expect(timeline(page)).toHaveAttribute('aria-busy', 'true');
    const cachedSvg = await timeline(page).locator('svg').elementHandle();
    await notesTab(page).focus(); pending.release(); await pending.delivered;
    await expect(timeline(page)).not.toHaveAttribute('aria-busy', 'true');
    expect(await cachedSvg.evaluate(element => element.isConnected)).toBe(true);
    await expect(notesTab(page)).toBeFocused();
  } finally {pending.release(); await pending.delivered; await pending.remove();}
});

test('a real revoked browser session clears cached notebook and Timeline reads', async ({page, context}) => {
  await signIn(page); await notebookReady(page);
  await timelineTab(page).click(); await expect(timeline(page).locator('svg')).toBeVisible();
  await notesTab(page).click(); await notebookReady(page);
  // This fixed fixture cookie belongs to a deliberately revoked synthetic
  // account. The real API returns403 and the real session refresh clears UI.
  await context.addCookies([{name:'crucible_session', value:'synthetic-revoked-browser-cookie',
    url:'http://127.0.0.1:8029', sameSite:'Strict'}]);
  const rejected = page.waitForResponse(response => new URL(response.url()).pathname === '/api/notebook/timeline' && response.status() === 403);
  await timelineTab(page).click(); await rejected;
  await expect(page.locator('#access-workspace')).toBeVisible();
  await expect(page.locator('#access-description')).toContainText('Your beta access is not active');
  await expect(page.locator('#notebook-workspace')).toBeHidden();
  await expect(page.locator('body')).not.toContainText('Alice browser-only private note');
  await expect(page.locator('#notebook-workspace svg')).toHaveCount(0);
});

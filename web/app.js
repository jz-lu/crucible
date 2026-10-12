'use strict';

const $ = selector => document.querySelector(selector);
const state = {
  user: null, csrf: '', schemes: [], profile: null, challenge: null,
  methods: [], view: 'challenge', authMode: 'login', round: null,
  timeOffset: 0, ready: false, challengeLoading: false, issueBusy: false,
  submitBusy: false, pendingAttempt: null, roundRefreshing: false,
  challengeRequest: 0, leaderboardRequest: 0, submissionChallenge: null
};
let toastTimer;
let roundRetryAt = 0;
const dialogTriggers = new WeakMap();
const authDialog = $('#auth-dialog');
const submissionDialog = $('#submission-dialog');
const historyDialog = $('#history-dialog');
const findingDialog = $('#finding-dialog');
const research = {
  visible: false, targets: [], target: null, kind: 'scheme', findings: [],
  nextBefore: null, request: 0, editing: null, followup: null, saving: false, recoveries: [], recoveriesRequest: 0
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}
function setText(selector, text) { $(selector).textContent = text ?? ''; }
function setError(selector, message) {
  const target = $(selector);
  target.textContent = message || '';
  target.hidden = !message;
}
function toast(message) {
  clearTimeout(toastTimer);
  setText('#toast', message);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5000);
}
function utcTime(seconds, withSeconds = true) {
  if (!Number.isFinite(Number(seconds))) return 'Not recorded';
  return new Date(Number(seconds) * 1000).toISOString().slice(11, withSeconds ? 19 : 16) + ' UTC';
}
function utcDate(seconds) {
  if (!Number.isFinite(Number(seconds))) return 'Not recorded';
  const value = new Date(Number(seconds) * 1000).toISOString();
  return value.slice(0, 10) + ' · ' + value.slice(11, 19) + ' UTC';
}
function duration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 1) return value.toFixed(3) + 's';
  if (value < 60) return value.toFixed(value < 10 ? 2 : 1) + 's';
  return Math.floor(value / 60) + 'm ' + String(Math.floor(value % 60)).padStart(2, '0') + 's';
}
function now() { return (Date.now() + state.timeOffset) / 1000; }
function profileId(profile = state.profile) { return profile?.profile_id || profile?.id || ''; }
function instanceSize(profile) { return profile?.instance_size ?? profile?.parameter_bits; }
function sharedKeyBytes(challenge) {
  const bytes = challenge?.public?.encoding?.shared_secret_bytes;
  return Number.isInteger(bytes) && bytes > 0 && bytes <= 4096 ? bytes : null;
}
function roundRemaining() { return state.round ? Math.max(0, Math.ceil(state.round.ends_at - now())) : null; }
function absorbClock(data) {
  if (Number.isFinite(Number(data?.server_time))) state.timeOffset = Number(data.server_time) * 1000 - Date.now();
  if (data?.round) state.round = data.round;
  updateClock();
}
async function api(path, body) {
  let response;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: body === undefined ? {Accept: 'application/json'} : {
        Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf
      },
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
  } catch {
    const error = new Error(controller.signal.aborted ? 'The server took too long to respond. Please try again.' : 'The server could not be reached. Check your connection and try again.');
    error.status = 0;
    throw error;
  } finally { clearTimeout(timeout); }
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : null;
    const fallback = response.status === 429 ? 'Too many requests. Wait a moment and try again.' :
      response.status === 401 ? 'Your session has ended. Sign in again to continue.' :
      response.status === 403 ? 'This request could not be authorized. Refresh your session and try again.' :
      'The request could not be completed. Please try again.';
    const error = new Error(detail || fallback);
    error.status = response.status;
    throw error;
  }
  absorbClock(data);
  return data;
}
function setSession(data) {
  const previousId = state.user?.id;
  state.user = data.user || null;
  state.csrf = data.csrf_token || '';
  if (previousId !== state.user?.id) {
    state.challenge = null;
    state.challengeRequest++;
    state.methods = [];
    state.pendingAttempt = null;
    state.submissionChallenge = null;
    $('#submission-form').reset();
    if (submissionDialog.open) submissionDialog.close();
    if (historyDialog.open) historyDialog.close();
    if (findingDialog.open) findingDialog.close();
    if (research.visible) renderFindings();
  }
  $('#auth-button').hidden = Boolean(state.user);
  $('#auth-button').disabled = false;
  $('#account-handle').hidden = !state.user;
  setText('#account-handle', state.user?.handle || '');
  $('#logout-button').hidden = !state.user;
  $('#history-button').hidden = !state.user;
  absorbClock(data);
}
function globalError(message) {
  setText('#global-message-text', message);
  $('#global-message').hidden = !message;
}
function setLoading(target, text) {
  const content = node('div', undefined, 'loading-state');
  content.setAttribute('role', 'status');
  content.append(node('span', undefined, 'loader'), node('span', text));
  content.firstChild.setAttribute('aria-hidden', 'true');
  target.replaceChildren(content);
}
function showInlineError(target, message, retry) {
  const messageNode = node('div', undefined, 'notice notice-error');
  messageNode.setAttribute('role', 'alert');
  messageNode.append(node('span', message));
  if (retry) {
    const button = node('button', 'Try again', 'text-button');
    button.type = 'button';
    button.addEventListener('click', retry);
    messageNode.append(button);
  }
  target.replaceChildren(messageNode);
}
function showEmpty(target, message) { target.replaceChildren(node('div', message, 'empty-state')); }

function updateClock() {
  const remaining = roundRemaining();
  if (remaining === null) return;
  const clock = String(Math.floor(remaining / 60)).padStart(2, '0') + ':' + String(remaining % 60).padStart(2, '0');
  setText('#countdown', clock);
  $('#countdown').setAttribute('aria-label', `${Math.floor(remaining / 60)} minutes and ${remaining % 60} seconds until the next round`);
  $('#countdown').classList.toggle('urgent', remaining < 60);
  setText('#round-deadline', utcTime(state.round.ends_at, false) + ' · shared deadline');
  setText('#submission-time', remaining ? clock + ' until the round ends' : 'This round has ended.');
  const formExpired = state.submissionChallenge && now() >= state.submissionChallenge.ends_at;
  $('#submit-answer').disabled = state.submitBusy || Boolean(formExpired && !state.pendingAttempt) || sharedKeyBytes(state.submissionChallenge) === null;
  if (formExpired && submissionDialog.open && !state.submitBusy && !state.pendingAttempt) {
    setError('#submission-error', 'This round has ended. Close this form to receive an instance for the new round.');
  }
  $('#open-submission').disabled = !isChallengeOpen();
  const staleChallenge = state.challenge && state.challenge.round_id !== state.round.id;
  if ((remaining === 0 || staleChallenge) && state.ready && !state.roundRefreshing && !state.submitBusy && Date.now() >= roundRetryAt) refreshRound();
}
function isChallengeOpen() {
  return Boolean(state.challenge && state.challenge.status === 'open' && !state.challenge.submission && now() < state.challenge.ends_at && sharedKeyBytes(state.challenge) !== null);
}
async function refreshRound() {
  if (state.roundRefreshing) return;
  state.roundRefreshing = true;
  const oldRound = state.round?.id;
  try {
    const data = await api('/api/session');
    setSession(data);
    if (state.round?.id !== oldRound || (state.challenge && state.challenge.round_id !== state.round.id)) {
      await loadChallenge();
      if (state.view === 'leaderboard') await loadLeaderboard();
      toast('A new round has begun. Request a fresh instance when you are ready.');
    }
  } catch (error) {
    globalError(error.message + ' The countdown will synchronize when the connection returns.');
  } finally {
    roundRetryAt = Date.now() + 5000;
    state.roundRefreshing = false;
  }
}
async function synchronizeSession() {
  if (!state.ready || state.roundRefreshing) return;
  const oldRound = state.round?.id;
  const oldUser = state.user?.id;
  try {
    const data = await api('/api/session');
    setSession(data);
    await refreshProfiles();
    globalError('');
    if (oldRound !== state.round?.id || oldUser !== state.user?.id) {
      await loadChallenge();
      if (state.view === 'leaderboard') await loadLeaderboard();
    }
  } catch (error) { globalError(error.message); }
}

async function refreshProfiles() {
  const data = await api('/api/schemes');
  state.schemes = data.schemes || [];
  state.profile = state.schemes.flatMap(scheme => scheme.profiles || []).find(profile => profileId(profile) === profileId()) || state.profile;
  renderProfiles();
}

function renderProfiles() {
  const select = $('#profile-select');
  select.replaceChildren();
  for (const scheme of state.schemes) {
    for (const profile of scheme.profiles || []) {
      const option = node('option', `Size ${instanceSize(profile)}${scheme.next_profile_id === profileId(profile) ? " · Next step" : ""}`);
      option.value = profileId(profile);
      select.append(option);
    }
  }
  select.disabled = select.options.length === 0;
  if (state.profile) select.value = profileId();
  if (!select.options.length) {
    select.append(node('option', 'No profiles available'));
    setText('#scheme-summary', 'No challenge profiles have been published yet.');
  }
  renderProtocol();
}
function addFact(list, label, value) {
  const row = node('div');
  row.append(node('dt', label), node('dd', value === null || value === undefined ? 'Not specified' :
    typeof value === 'object' ? JSON.stringify(value) : value));
  list.append(row);
}
function renderProtocol() {
  const profile = state.profile;
  const scheme = state.schemes.find(item => (item.profiles || []).some(p => profileId(p) === profileId(profile)));
  if (scheme) {
    setText('#scheme-title', scheme.name);
    setText('#scheme-summary', scheme.summary);
    setText('#protocol-version', scheme.version ? `${scheme.name} · version ${scheme.version}` : scheme.name);
  }
  const facts = $('#parameter-facts');
  const correctness = $('#correctness-facts');
  facts.replaceChildren();
  correctness.replaceChildren();
  if (!profile) return;
  const descriptor = scheme?.size_parameter;
  const symbol = descriptor?.symbol || 'n';
  const meaning = descriptor?.meaning;
  const explanation = meaning ? `For this KEM, size ${symbol} is the ${meaning.charAt(0).toLowerCase() + meaning.slice(1)}.` :
    `Size ${symbol} follows this scheme's parameter mapping.`;
  setText('#profile-label', `${descriptor?.name || 'Instance size'} ${symbol}`);
  setText('#profile-note', `${explanation} Sizes are comparable only within this scheme and version.`);
  addFact(facts, descriptor?.name || 'Instance size', `${symbol} = ${instanceSize(profile)}`);
  addFact(facts, 'Profile', profile.name || profileId());
  if (descriptor?.mapping) addFact(facts, 'Size mapping', descriptor.mapping);
  if (profile.parameter_label === 'subgroup-order bits') addFact(facts, 'Subgroup order size', `${profile.parameter_bits} bits`);
  if (profile.modulus_bits != null) addFact(facts, 'Modulus size', `${profile.modulus_bits} bits`);
  addFact(facts, 'Shared-key output', profile.shared_secret_bytes == null ? null : `${profile.shared_secret_bytes} bytes`);
  setText('#parameter-json', JSON.stringify({parameters: profile.parameters, encoding: profile.encoding, kdf: profile.kdf}, null, 2));
  const claim = profile.correctness || {};
  addFact(correctness, 'Claim type', claim.kind);
  addFact(correctness, 'Failure bound', claim.failure_bound);
  addFact(correctness, 'Argument', claim.argument);
  addFact(correctness, 'Scope', claim.scope);
  addFact(correctness, 'Evidence status', claim.evidence_status);
}
function renderChallenge() {
  $('#challenge-loading').hidden = !state.challengeLoading;
  $('#challenge-empty').hidden = state.challengeLoading || Boolean(state.challenge);
  $('#challenge-active').hidden = state.challengeLoading || !state.challenge;
  $('#issue-button').disabled = !state.ready || !state.profile || state.issueBusy || roundRemaining() === 0;
  if (!state.challenge) {
    setText('#empty-title', state.user ? 'Ready for this round?' : 'A fresh instance, just for you.');
    setText('#empty-description', state.user ? 'Receive a public key and encapsulation unique to your account and selected profile. Your answer is due at the shared deadline.' :
      'Sign in to receive a public key and encapsulation unique to your account for this profile and round.');
    setText('#issue-button', state.issueBusy ? 'Preparing your instance…' : state.user ? 'Get this round’s instance →' : 'Sign in to begin →');
    return;
  }
  const challenge = state.challenge;
  const result = challenge.submission?.status || (challenge.status === 'solved' ? 'correct' : challenge.status === 'incorrect' ? 'incorrect' : null);
  const expired = challenge.status === 'expired' || now() >= challenge.ends_at;
  const status = result === 'correct' ? 'Recovered' : result === 'incorrect' ? 'Incorrect · locked' : expired ? 'Round ended' : 'Open';
  setText('#challenge-status', status);
  $('#challenge-status').className = 'status-tag' + (result === 'incorrect' ? ' error-tag' : expired && !result ? ' muted-tag' : '');
  setText('#challenge-round', `${challenge.profile_id} / Round ${challenge.round_id}`);
  setText('#challenge-issued', utcTime(challenge.issued_at));
  setText('#challenge-ends', utcTime(challenge.ends_at));
  const keyBytes = sharedKeyBytes(challenge);
  setText('#target-key', keyBytes === null ? 'Output length not specified' : `${keyBytes}-byte key · ${keyBytes * 2} hex characters`);
  setText('#instance-json', JSON.stringify(challenge.public, null, 2));
  $('#download-button').href = '/api/challenges/' + encodeURIComponent(challenge.id) + '/download';
  $('#download-button').download = 'crucible-' + challenge.profile_id + '-' + challenge.round_id + '.json';
  const resultNode = $('#challenge-result');
  resultNode.hidden = !result && !expired && keyBytes !== null;
  resultNode.className = 'notice' + (result === 'incorrect' || (!result && !expired && keyBytes === null) ? ' notice-error' : '');
  resultNode.textContent = result === 'correct' ? 'Key recovered. The server accepted your answer. Your method and compute declaration are attached to this recovery.' :
    result === 'incorrect' ? 'The submitted key did not match. This instance is locked. You can try a different profile or return for the next round.' :
      expired ? 'This round has ended without an accepted recovery for this instance.' :
        keyBytes === null ? 'This instance does not declare a supported shared-key output length. Submission is unavailable; refresh the instance or report the problem.' : '';
  $('#open-submission').hidden = Boolean(result || expired || challenge.status !== 'open');
  $('#open-submission').disabled = !isChallengeOpen();
}
async function loadChallenge() {
  const request = ++state.challengeRequest;
  const selected = profileId();
  $('#challenge-error').hidden = true;
  state.challenge = null;
  if (!state.user || !selected) {
    state.challengeLoading = false;
    renderChallenge();
    return;
  }
  state.challengeLoading = true;
  renderChallenge();
  try {
    const data = await api('/api/challenges?profile_id=' + encodeURIComponent(selected));
    if (request !== state.challengeRequest) return;
    state.challenge = data.challenge;
  } catch (error) {
    if (request !== state.challengeRequest) return;
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally {
    if (request === state.challengeRequest) {
      state.challengeLoading = false;
      renderChallenge();
    }
  }
}
async function issueChallenge() {
  if (!state.user) return openAuth($('#issue-button'));
  if (state.issueBusy || !state.profile) return;
  state.issueBusy = true;
  const selected = profileId();
  const accountId = state.user.id;
  $('#challenge-error').hidden = true;
  renderChallenge();
  try {
    const data = await api('/api/challenges', {profile_id: selected});
    if (state.user?.id !== accountId) return;
    if (profileId() === selected) state.challenge = data.challenge;
    toast('Your instance is ready. The shared round deadline applies.');
  } catch (error) {
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally { state.issueBusy = false; renderChallenge(); }
}

async function loadLeaderboard() {
  const target = $('#leaderboard-content');
  const request = ++state.leaderboardRequest;
  const selected = profileId();
  if (!selected) return showEmpty(target, 'Choose an available profile to see its recoveries.');
  setLoading(target, 'Loading the first recoveries…');
  $('#refresh-leaderboard').disabled = true;
  try {
    const data = await api('/api/leaderboard?profile_id=' + encodeURIComponent(selected));
    if (request !== state.leaderboardRequest) return;
    if (!data.entries?.length) return showEmpty(target, 'No accepted recoveries have been recorded for this profile and version.');
    const wrap = node('div', undefined, 'leaderboard-wrap');
    const table = node('table', undefined, 'leaderboard-table');
    const caption = node('caption', 'First recoveries for ' + selected, 'skip-link');
    table.append(caption);
    const head = node('thead');
    const headings = node('tr');
    for (const title of ['Rank', 'Researcher / method', 'Elapsed', 'Received (UTC)']) {
      const th = node('th', title); th.scope = 'col'; headings.append(th);
    }
    head.append(headings); table.append(head);
    const body = node('tbody');
    for (const entry of data.entries) {
      const row = node('tr');
      row.append(node('td', String(entry.rank).padStart(2, '0')));
      const researcher = node('td');
      researcher.append(node('div', entry.handle, 'leaderboard-handle'), node('div', entry.method?.name || 'Method', 'leaderboard-method'));
      row.append(researcher, node('td', duration(entry.elapsed_seconds), 'mono'), node('td', utcDate(entry.received_at), 'mono receipt-cell'));
      body.append(row);
      const detailsRow = node('tr', undefined, 'result-detail-row');
      const cell = node('td'); cell.colSpan = 4;
      const details = node('details');
      details.append(node('summary', 'Inspect method and compute'));
      details.append(node('pre', entry.method?.pseudocode || 'No pseudocode recorded.', 'code-block'));
      const facts = node('dl', undefined, 'detail-list');
      const compute = entry.compute || {};
      addFact(facts, 'Answer received', utcDate(entry.received_at));
      addFact(facts, 'Hardware', compute.hardware);
      addFact(facts, 'Parallel workers', compute.workers);
      addFact(facts, 'Attack time', compute.wall_seconds == null ? null : duration(compute.wall_seconds));
      addFact(facts, 'Precomputation', compute.precomputation);
      addFact(facts, 'Peak memory', compute.peak_memory);
      if (compute.notes) addFact(facts, 'Notes', compute.notes);
      details.append(facts, node('p', 'Method and resources are contributor-reported. An accepted key does not independently validate this account of the work.', 'panel-footnote'));
      cell.append(details); detailsRow.append(cell); body.append(detailsRow);
    }
    table.append(body); wrap.append(table); target.replaceChildren(wrap);
  } catch (error) {
    if (request === state.leaderboardRequest) showInlineError(target, error.message, loadLeaderboard);
  } finally { if (request === state.leaderboardRequest) $('#refresh-leaderboard').disabled = false; }
}
function activateView(view, focus = false) {
  state.view = view;
  for (const tab of document.querySelectorAll('[data-view]')) {
    const selected = tab.dataset.view === view;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $('#panel-' + tab.dataset.view).hidden = !selected;
    if (selected && focus) tab.focus();
  }
  if (view === 'leaderboard') loadLeaderboard();
}
const tabs = [...document.querySelectorAll('[data-view]')];
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateView(tab.dataset.view));
  tab.addEventListener('keydown', event => {
    const indices = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1};
    if (event.key in indices) { event.preventDefault(); activateView(tabs[indices[event.key]].dataset.view, true); }
  });
});

function openDialog(dialog, trigger) {
  dialogTriggers.set(dialog, trigger || document.activeElement);
  dialog.showModal();
  document.body.style.overflow = 'hidden';
}
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    document.body.style.overflow = '';
    const trigger = dialogTriggers.get(dialog);
    if (trigger?.isConnected && !trigger.hidden) trigger.focus();
    if (dialog === authDialog) $('#auth-password').value = '';
    if (dialog === submissionDialog && !state.pendingAttempt && !state.submitBusy) $('#candidate').value = '';
  });
}
function setAuthMode(mode) {
  state.authMode = mode;
  $('#login-mode').classList.toggle('selected', mode === 'login');
  $('#register-mode').classList.toggle('selected', mode === 'register');
  $('#login-mode').setAttribute('aria-pressed', String(mode === 'login'));
  $('#register-mode').setAttribute('aria-pressed', String(mode === 'register'));
  setText('#auth-title', mode === 'login' ? 'Welcome back.' : 'Join the experiment.');
  setText('#auth-description', mode === 'login' ? 'Sign in to receive your instance for the current round.' : 'Create an account to investigate challenges and share your results.');
  setText('#auth-submit', mode === 'login' ? 'Sign in →' : 'Create account →');
  $('#auth-password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('#auth-password').minLength = 12;
  setError('#auth-error', '');
}
function openAuth(trigger) { setError('#auth-error', ''); openDialog(authDialog, trigger); }
$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#auth-submit');
  button.disabled = true;
  $('#login-mode').disabled = true;
  $('#register-mode').disabled = true;
  setError('#auth-error', '');
  setText('#auth-submit', state.authMode === 'login' ? 'Signing in…' : 'Creating your account…');
  try {
    const data = await api('/api/auth/' + state.authMode, {handle: $('#auth-handle').value.trim(), password: $('#auth-password').value});
    setSession(data);
    $('#auth-form').reset();
    authDialog.close();
    await loadChallenge();
    toast('Signed in as ' + state.user.handle + '.');
  } catch (error) { setError('#auth-error', error.message); }
  finally {
    button.disabled = false;
    $('#login-mode').disabled = false;
    $('#register-mode').disabled = false;
    setText('#auth-submit', state.authMode === 'login' ? 'Sign in →' : 'Create account →');
  }
});
async function logout() {
  $('#logout-button').disabled = true;
  try {
    const data = await api('/api/auth/logout', {});
    setSession(data);
    renderChallenge();
    toast('You are signed out.');
  } catch (error) { globalError(error.message); }
  finally { $('#logout-button').disabled = false; }
}

function renderMethodOptions(selected = 'new') {
  const select = $('#method-select');
  select.replaceChildren();
  const first = node('option', 'Create a new method'); first.value = 'new'; select.append(first);
  for (const method of state.methods) {
    const option = node('option', method.name); option.value = String(method.id); select.append(option);
  }
  select.value = state.methods.some(method => String(method.id) === String(selected)) ? selected : 'new';
  updateMethodFields();
}
function updateMethodFields() {
  const isNew = $('#method-select').value === 'new';
  $('#new-method-fields').hidden = !isNew;
  $('#method-name').required = isNew;
  $('#method-pseudocode').required = isNew;
  $('#selected-method-preview').hidden = isNew;
  const method = state.methods.find(item => String(item.id) === $('#method-select').value);
  setText('#selected-method-code', method?.pseudocode || '');
}
function lockSubmissionFields(lock) {
  for (const field of $('#submission-form').querySelectorAll('input, textarea, select')) field.disabled = lock;
}
async function openSubmission() {
  if (!isChallengeOpen()) return;
  const challenge = state.challenge;
  const keyBytes = sharedKeyBytes(challenge);
  const hexLength = keyBytes * 2;
  const candidate = $('#candidate');
  candidate.minLength = hexLength;
  candidate.maxLength = hexLength;
  candidate.pattern = `[0-9a-fA-F]{${hexLength}}`;
  candidate.title = `Enter exactly ${hexLength} hexadecimal characters, without spaces or a 0x prefix.`;
  candidate.placeholder = `${hexLength} hexadecimal characters`;
  setText('#candidate-hint', `The complete ${keyBytes}-byte key. No spaces or 0x prefix.`);
  const accountId = state.user?.id;
  const sameChallenge = state.submissionChallenge?.id === challenge.id;
  if (!sameChallenge) {
    $('#submission-form').reset();
    state.pendingAttempt = null;
    lockSubmissionFields(false);
  }
  state.submissionChallenge = challenge;
  setText('#submission-context', `${challenge.profile_id} / Round ${challenge.round_id}`);
  setError('#submission-error', state.pendingAttempt ? 'The previous request has no confirmed result. Retry to check the same answer; it will not create a second attempt.' : '');
  setText('#submit-answer', state.pendingAttempt ? 'Retry the same answer →' : 'Submit this answer →');
  openDialog(submissionDialog, $('#open-submission'));
  updateClock();
  try {
    const data = await api('/api/methods');
    if (state.user?.id !== accountId || state.submissionChallenge?.id !== challenge.id) return;
    state.methods = data.methods || [];
    const selected = $('#method-select').value;
    renderMethodOptions(selected);
  } catch (error) { setError('#submission-error', 'Saved methods could not be loaded. ' + error.message); }
}
$('#submission-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.submitBusy || !state.submissionChallenge || (now() >= state.submissionChallenge.ends_at && !state.pendingAttempt)) return;
  state.submitBusy = true;
  updateClock();
  setError('#submission-error', '');
  setText('#submit-answer', 'Submitting…');
  const challenge = state.submissionChallenge;
  try {
    if (!state.pendingAttempt) {
      const keyBytes = sharedKeyBytes(challenge);
      if (keyBytes === null) throw new Error('This instance does not declare a supported shared-key output length.');
      if (!new RegExp(`^[0-9a-fA-F]{${keyBytes * 2}}$`).test($('#candidate').value)) {
        throw new Error(`Enter the complete ${keyBytes}-byte key as exactly ${keyBytes * 2} hexadecimal characters.`);
      }
      let methodId = $('#method-select').value;
      if (methodId === 'new') {
        const name = $('#method-name').value.trim();
        const pseudocode = $('#method-pseudocode').value.trim();
        if (!name || !pseudocode) throw new Error('Enter a method name and pseudocode.');
        const data = await api('/api/methods', {name, pseudocode});
        state.methods.push(data.method);
        methodId = data.method.id;
        renderMethodOptions(String(methodId));
      }
      const compute = {
        hardware: $('#hardware').value.trim(), workers: Number($('#workers').value),
        wall_seconds: Number($('#wall-seconds').value), precomputation: $('#precomputation').value.trim(),
        peak_memory: $('#peak-memory').value.trim(), notes: $('#compute-notes').value.trim()
      };
      if (!compute.hardware || !compute.precomputation || !compute.peak_memory) throw new Error('Complete the hardware, preparation, and memory fields.');
      state.pendingAttempt = {
        challengeId: challenge.id,
        payload: {candidate: $('#candidate').value.toLowerCase(), method_id: methodId, compute, idempotency_key: crypto.randomUUID()}
      };
    }
    lockSubmissionFields(true);
    const data = await api('/api/challenges/' + encodeURIComponent(state.pendingAttempt.challengeId) + '/submit', state.pendingAttempt.payload);
    if (state.challenge?.id === data.challenge.id) state.challenge = data.challenge;
    state.pendingAttempt = null;
    $('#submission-form').reset();
    lockSubmissionFields(false);
    submissionDialog.close();
    renderChallenge();
    toast(data.submission.status === 'correct' ? 'Key recovered. Your recovery has been recorded.' : 'Answer recorded. The key did not match this instance.');
    // A progress hint refresh cannot change the accepted submission receipt.
    if (data.submission.status === 'correct') refreshProfiles().catch(() => {});
    if (state.view === 'leaderboard') loadLeaderboard();
  } catch (error) {
    if (error.status === 409) {
      state.pendingAttempt = null;
      lockSubmissionFields(false);
      $('#candidate').value = '';
      submissionDialog.close();
      await loadChallenge();
      toast(error.message);
    } else if (state.pendingAttempt && (error.status === 0 || error.status >= 500)) {
      setError('#submission-error', 'The answer has no confirmed result. Retry the same answer to safely check its status. ' + error.message);
    } else {
      state.pendingAttempt = null;
      lockSubmissionFields(false);
      setError('#submission-error', error.message);
    }
  } finally {
    state.submitBusy = false;
    setText('#submit-answer', state.pendingAttempt ? 'Retry the same answer →' : 'Submit this answer →');
    updateClock();
  }
});

async function showHistory() {
  openDialog(historyDialog, $('#history-button'));
  const target = $('#history-content');
  setLoading(target, 'Loading your submissions…');
  try {
    const data = await api('/api/me/submissions');
    if (!data.submissions?.length) return showEmpty(target, 'No submissions yet. Your received answers will appear here.');
    const items = [];
    for (const submission of data.submissions) {
      const item = node('article', undefined, 'history-item');
      const copy = node('div');
      copy.append(node('h3', submission.method_name || 'Submitted method'), node('p', `${submission.profile_id} · Round ${submission.round_id}`), node('p', utcDate(submission.received_at)));
      item.append(copy, node('span', submission.status === 'correct' ? 'Recovered' : 'Incorrect', 'status-tag' + (submission.status === 'correct' ? '' : ' error-tag')));
      items.push(item);
    }
    target.replaceChildren(...items);
  } catch (error) { showInlineError(target, error.message, async () => { historyDialog.close(); await showHistory(); }); }
}
async function copyInstance() {
  if (!state.challenge) return;
  const payload = JSON.stringify(state.challenge.public, null, 2);
  try { await navigator.clipboard.writeText(payload); toast('Public instance copied. Download the full challenge contract for exact protocol details.'); }
  catch {
    const details = $('#instance-json').parentElement;
    details.open = true;
    const range = document.createRange(); range.selectNodeContents($('#instance-json'));
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    toast('Public instance selected. Use your usual copy shortcut.');
  }
}

$('#profile-select').addEventListener('change', () => {
  state.profile = state.schemes.flatMap(scheme => scheme.profiles || []).find(profile => profileId(profile) === $('#profile-select').value) || null;
  renderProtocol();
  loadChallenge();
  if (state.view === 'leaderboard') loadLeaderboard();
});
$('#issue-button').addEventListener('click', issueChallenge);
$('#retry-challenge').addEventListener('click', loadChallenge);
$('#auth-button').addEventListener('click', () => openAuth($('#auth-button')));
$('#login-mode').addEventListener('click', () => setAuthMode('login'));
$('#register-mode').addEventListener('click', () => setAuthMode('register'));
$('#logout-button').addEventListener('click', logout);
$('#open-submission').addEventListener('click', openSubmission);
$('#copy-instance').addEventListener('click', copyInstance);
$('#method-select').addEventListener('change', updateMethodFields);
$('#refresh-leaderboard').addEventListener('click', loadLeaderboard);
$('#history-button').addEventListener('click', showHistory);
$('#retry-button').addEventListener('click', () => state.ready ? synchronizeSession() : initialize());
document.addEventListener('visibilitychange', () => { if (!document.hidden) synchronizeSession(); });
window.addEventListener('online', synchronizeSession);


// Research records keep claims and their conditions separate from hourly ranking.
function showWorkspace(name) {
  research.visible = name === 'research';
  $('.intro').hidden = research.visible;
  $('.educational-note').hidden = research.visible;
  $('#workspace').hidden = research.visible;
  $('#research-workspace').hidden = !research.visible;
  for (const kind of ['experiment', 'research']) {
    const button = $('#nav-' + kind);
    const selected = kind === name;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  $('.skip-link').href = research.visible ? '#research-workspace' : '#workspace';
  if (research.visible && !research.targets.length) loadResearch();
  else if (research.visible && research.target) loadFindings();
}
function researchError(message) {
  setText('#research-error-text', message);
  $('#research-error').hidden = !message;
}
async function loadResearch() {
  researchError('');
  $('#retry-research').disabled = true;
  setLoading($('#target-catalogue'), 'Loading research targets…');
  try {
    const data = await api('/api/targets');
    research.targets = data.targets || [];
    const previous = research.target?.id;
    const target = research.targets.find(item => item.id === previous) ||
      research.targets.find(item => item.kind === research.kind) || research.targets[0];
    if (target) selectResearchTarget(target.id);
    else showEmpty($('#target-catalogue'), 'No research targets have been published yet.');
  } catch (error) {
    researchError(error.message);
    $('#target-catalogue').replaceChildren();
  } finally { $('#retry-research').disabled = false; }
}
function updateTargetTabs(focus = false) {
  for (const tab of document.querySelectorAll('[data-target-kind]')) {
    const selected = tab.dataset.targetKind === research.kind;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (focus && selected) tab.focus();
  }
  $('#target-catalogue').setAttribute('aria-labelledby', 'target-tab-' + research.kind);
}
function chooseTargetKind(kind, focus = false) {
  research.kind = kind;
  const target = research.targets.find(item => item.kind === kind);
  updateTargetTabs(focus);
  if (target) selectResearchTarget(target.id);
  else {
    research.target = null;
    $('#research-target').hidden = true;
    $('#findings-section').hidden = true;
    showEmpty($('#target-catalogue'), 'No targets of this kind have been published yet.');
  }
}
function selectResearchTarget(id) {
  const target = research.targets.find(item => item.id === id);
  if (!target) return;
  research.target = target;
  research.kind = target.kind;
  research.findings = [];
  research.nextBefore = null;
  updateTargetTabs();
  const catalogue = $('#target-catalogue');
  const choices = research.targets.filter(item => item.kind === target.kind);
  catalogue.replaceChildren();
  if (choices.length > 1) {
    for (const choice of choices) {
      const button = node('button', choice.name, 'target-choice' + (choice.id === target.id ? ' selected' : ''));
      button.type = 'button';
      button.setAttribute('aria-pressed', String(choice.id === target.id));
      button.addEventListener('click', () => selectResearchTarget(choice.id));
      catalogue.append(button);
    }
  } else catalogue.append(node('p', target.kind === 'scheme' ? '01 / Curated scheme' : '01 / Precisely defined problem', 'eyebrow'));
  $('#research-target').hidden = false;
  $('#findings-section').hidden = false;
  setText('#target-meta', `${target.kind === 'scheme' ? 'Scheme' : 'Assumption / problem'} · version ${target.version}`);
  setText('#target-title', target.name);
  setText('#target-summary', target.summary);
  setText('#target-definition', target.definition);
  const relationships = $('#target-related');
  relationships.replaceChildren();
  for (const relation of target.related_targets || []) {
    const related = research.targets.find(item => item.id === relation.id);
    const row = node('div', undefined, 'related-target');
    const button = node('button', related?.name || relation.id, 'text-button');
    button.type = 'button';
    button.addEventListener('click', () => selectResearchTarget(relation.id));
    row.append(button, node('span', relation.relationship, 'field-hint'));
    relationships.append(row);
  }
  loadFindings();
}
async function loadFindings(append = false) {
  if (!research.target) return;
  const targetId = research.target.id;
  const request = ++research.request;
  const previous = append ? research.findings : [];
  $('#more-findings').disabled = true;
  if (!append) setLoading($('#findings-list'), 'Loading findings and investigations…');
  let path = '/api/contributions?target_id=' + encodeURIComponent(targetId) + '&limit=20';
  if (append && research.nextBefore) path += '&before=' + encodeURIComponent(research.nextBefore);
  try {
    const data = await api(path);
    if (request !== research.request) return;
    const seen = new Set(previous.map(item => item.id));
    research.findings = previous.concat((data.contributions || []).filter(item => !seen.has(item.id)));
    research.nextBefore = data.next_before || null;
    renderFindings();
  } catch (error) {
    if (request !== research.request) return;
    if (append) researchError(error.message);
    else showInlineError($('#findings-list'), error.message, () => loadFindings());
  } finally { if (request === research.request) $('#more-findings').disabled = false; }
}
function friendlyModel(value) {
  return {'classical': 'Classical', 'quantum': 'Quantum', 'mixed': 'Mixed model', 'not-applicable': 'Model not applicable'}[value] || value;
}
function friendlyKind(value) {
  return {'argument': 'Argument', 'heuristic': 'Heuristic', 'experiment': 'Experiment', 'mixed': 'Mixed evidence'}[value] || value;
}
function safeArtifactLink(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || /\s/.test(value)) return null;
    const link = node('a', 'Open supporting artifact ↗', 'text-button artifact-link');
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  } catch { return null; }
}
function appendRevisionContent(container, revision, includeClaim = false) {
  if (includeClaim) {
    container.append(node('h4', revision.title), node('p', revision.claim, 'finding-claim preserve-lines'));
    const target = research.targets.find(item => item.id === revision.target_id);
    container.append(node('p', `${target?.name || revision.target_id} · target version ${revision.target_version}`, 'finding-meta'));
  }
  const conditions = node('div', undefined, 'finding-section');
  conditions.append(node('h4', 'Conditions and scope'), node('p', revision.conditions, 'preserve-lines'));
  const writeup = node('div', undefined, 'finding-section');
  writeup.append(node('h4', 'Write-up'), node('p', revision.body, 'preserve-lines'));
  container.append(conditions, writeup);
  if (revision.time_cost || revision.memory_cost || revision.success_probability || revision.profile_ids?.length) {
    const facts = node('dl', undefined, 'detail-list finding-quantities');
    if (revision.profile_ids?.length) addFact(facts, 'Applicable profiles', revision.profile_ids.join(', '));
    if (revision.time_cost) addFact(facts, 'Time cost', revision.time_cost);
    if (revision.memory_cost) addFact(facts, 'Memory cost', revision.memory_cost);
    if (revision.success_probability) addFact(facts, 'Success probability', revision.success_probability);
    container.append(facts);
  }
  const artifact = safeArtifactLink(revision.artifact_url);
  if (artifact) container.append(artifact);
  if (revision.verification) {
    const verification = node('div', undefined, 'verification-scope');
    verification.append(node('strong', 'Supporting recovery · key verified for one instance'), node('p', revision.verification.scope || 'The server accepted the key for this one instance before its deadline.'));
    if (revision.verification.received_at) verification.append(node('p', 'Received ' + utcDate(revision.verification.received_at), 'field-hint'));
    container.append(verification);
  }
}
async function loadRelatedFinding(id, container, button) {
  button.disabled = true;
  try {
    const data = await api('/api/contributions/' + encodeURIComponent(id));
    const record = data.contribution;
    const panel = node('div', undefined, 'referenced-finding');
    panel.append(node('p', `Referenced finding · ${record.author.handle} · revision ${record.latest.revision}`, 'eyebrow'));
    appendRevisionContent(panel, record.latest, true);
    container.replaceChildren(panel);
  } catch (error) {
    container.replaceChildren(node('p', error.message, 'form-error'));
    button.disabled = false;
  }
}
function createFindingCard(record) {
  const revision = record.latest;
  const card = node('article', undefined, 'finding-card');
  const header = node('div', undefined, 'finding-header');
  const identity = node('p', undefined, 'finding-author');
  identity.append(node('strong', record.author.handle), node('span', `Revision ${revision.revision} · ${utcDate(revision.created_at || record.updated_at)}`));
  header.append(identity, node('span', 'Unreviewed', 'status-tag muted-tag'));
  const title = node('h3', revision.title);
  const meta = node('p', `Target v${revision.target_version} · ${friendlyKind(revision.evidence_kind)} · ${friendlyModel(revision.model)}`, 'finding-meta');
  card.append(header, title, meta, node('p', revision.claim, 'finding-claim preserve-lines'));
  const details = node('details', undefined, 'finding-details');
  details.append(node('summary', 'Read conditions and write-up'));
  appendRevisionContent(details, revision);
  if (revision.related_contribution_id) {
    const relationship = node('div', undefined, 'finding-relationship');
    const original = node('button', 'View the finding this follows up →', 'text-button');
    const originalContent = node('div');
    original.type = 'button';
    original.addEventListener('click', () => loadRelatedFinding(revision.related_contribution_id, originalContent, original));
    relationship.append(original, originalContent);
    details.append(relationship);
  }
  const actions = node('div', undefined, 'finding-actions');
  const followup = node('button', 'Add a follow-up', 'text-button');
  followup.type = 'button';
  followup.addEventListener('click', () => openFinding({followup: record, trigger: followup}));
  actions.append(followup);
  if (record.editable && record.author.id === state.user?.id) {
    const revise = node('button', 'Revise finding', 'text-button');
    revise.type = 'button';
    revise.addEventListener('click', () => openFinding({editing: record, trigger: revise}));
    actions.append(revise);
  }
  details.append(actions);
  if (Number(revision.revision) > 1) {
    const history = node('details', undefined, 'revision-history');
    const historyContent = node('div');
    const historyStatus = node('div');
    const earlierButton = node('button', 'Load earlier revisions ↓', 'text-button');
    earlierButton.type = 'button';
    earlierButton.hidden = true;
    historyContent.append(historyStatus, earlierButton);
    history.append(node('summary', 'Earlier revisions (' + (Number(revision.revision) - 1) + ')'), historyContent);
    let historyLoaded = false;
    let historyLoading = false;
    let nextBeforeRevision = null;
    const seenRevisions = new Set();
    async function loadHistoryPage() {
      if (historyLoading) return;
      historyLoading = true;
      earlierButton.disabled = true;
      setLoading(historyStatus, 'Loading revision history…');
      try {
        let path = '/api/contributions/' + encodeURIComponent(record.id) + '?limit=20';
        if (nextBeforeRevision !== null) path += '&before_revision=' + encodeURIComponent(nextBeforeRevision);
        const data = await api(path);
        const versions = (data.contribution.revisions || []).filter(item =>
          Number(item.revision) < Number(revision.revision) && !seenRevisions.has(String(item.id || item.revision)));
        for (const version of versions) {
          seenRevisions.add(String(version.id || version.revision));
          const item = node('details', undefined, 'previous-revision');
          item.append(node('summary', `Revision ${version.revision} · ${utcDate(version.created_at)}`));
          appendRevisionContent(item, version, true);
          historyContent.insertBefore(item, historyStatus);
        }
        historyLoaded = true;
        nextBeforeRevision = data.contribution.next_before_revision ?? null;
        historyStatus.replaceChildren();
        if (!seenRevisions.size && nextBeforeRevision === null) showEmpty(historyStatus, 'No earlier revisions were returned.');
        earlierButton.hidden = nextBeforeRevision === null;
        earlierButton.textContent = 'Load earlier revisions ↓';
      } catch (error) {
        const message = node('p', error.message, 'form-error');
        message.setAttribute('role', 'alert');
        historyStatus.replaceChildren(message);
        earlierButton.hidden = false;
        earlierButton.textContent = 'Retry loading revisions';
      } finally {
        historyLoading = false;
        earlierButton.disabled = false;
      }
    }
    earlierButton.addEventListener('click', loadHistoryPage);
    history.addEventListener('toggle', () => {
      if (history.open && !historyLoaded) loadHistoryPage();
    });
    details.append(history);
  }
  card.append(details);
  return card;
}
function renderFindings() {
  const list = $('#findings-list');
  $('#more-findings').hidden = !research.nextBefore;
  if (!research.findings.length) return showEmpty(list, 'No findings have been recorded for this target yet. A useful first contribution can be a clear argument, a reproducible experiment, or a carefully documented question.');
  list.replaceChildren(...research.findings.map(createFindingCard));
}
function findingVersion(target) {
  return research.editing?.latest.target_version || research.followup?.latest.target_version || target?.version;
}
function renderFindingProfiles(selected = []) {
  const target = research.targets.find(item => item.id === $('#finding-target').value);
  const options = $('#finding-profiles');
  options.replaceChildren();
  const version = target?.versions?.find(item => item.version === findingVersion(target));
  for (const profile of version?.profiles || target?.profiles || []) {
    const label = node('label');
    const input = node('input');
    input.type = 'checkbox';
    input.value = profile.id;
    input.checked = selected.includes(profile.id);
    label.append(input, node('span', instanceSize(profile) == null ? profile.name || profile.id : `Size ${instanceSize(profile)}`));
    options.append(label);
  }
  if (!options.childElementCount) options.append(node('p', 'No registered profiles for this target. Describe the parameter regime in Conditions and scope.', 'field-hint'));
}
async function loadSupportingRecoveries(selectedId = '') {
  const select = $('#finding-recovery');
  const enabled = $('#finding-target').value === 'teaching-dh-kem';
  $('#finding-recovery-field').hidden = !enabled;
  select.replaceChildren();
  const none = node('option', 'No recovery attached'); none.value = ''; select.append(none);
  research.recoveries = [];
  const request = ++research.recoveriesRequest;
  if (!enabled) return;
  select.disabled = true;
  setText('#finding-recovery-hint', 'Loading your correct submissions…');
  try {
    const data = await api('/api/me/submissions');
    if (request !== research.recoveriesRequest) return;
    const target = research.targets.find(item => item.id === $('#finding-target').value);
    research.recoveries = (data.submissions || []).filter(item => item.status === 'correct' && item.scheme_version === findingVersion(target));
    for (const recovery of research.recoveries) {
      const option = node('option', `${recovery.profile_id} · ${utcDate(recovery.received_at)} · ${recovery.method_name}`);
      option.value = recovery.id;
      select.append(option);
    }
    if (selectedId && !research.recoveries.some(item => item.id === selectedId)) {
      const previous = node('option', 'Previously attached recovery'); previous.value = selectedId; select.append(previous);
    }
    select.value = selectedId;
    setText('#finding-recovery-hint', research.recoveries.length || selectedId ?
      'The recovery verifies one instance only. Its exact profile must be selected below; the finding remains unreviewed.' :
      'No correct submissions are available to attach. You can publish a finding without one.');
  } catch (error) {
    if (request !== research.recoveriesRequest) return;
    if (selectedId) {
      const previous = node('option', 'Previously attached recovery'); previous.value = selectedId; select.append(previous); select.value = selectedId;
    }
    setText('#finding-recovery-hint', 'Recovery history could not be loaded. ' + error.message);
  } finally { if (request === research.recoveriesRequest) select.disabled = false; }
}
function openFinding({editing = null, followup = null, trigger = $('#add-finding')} = {}) {
  if (!state.user) return openAuth(trigger);
  if (research.saving) return toast('A contribution is being saved. Please wait for its result.');
  research.editing = editing;
  research.followup = followup;
  const form = $('#finding-form');
  form.reset();
  setError('#finding-error', '');
  $('#finding-target').replaceChildren();
  for (const target of research.targets) {
    const option = node('option', target.name + ' · v' + (target.id === (editing?.latest.target_id || followup?.latest.target_id) ? findingVersion(target) : target.version));
    option.value = target.id;
    $('#finding-target').append(option);
  }
  const content = editing?.latest;
  const currentTarget = content?.target_id || followup?.latest.target_id || research.target?.id;
  $('#finding-target').value = currentTarget;
  $('#finding-target').disabled = Boolean(editing || followup);
  const labels = {
    '#finding-name': content?.title, '#finding-claim': content?.claim,
    '#finding-conditions': content?.conditions, '#finding-body': content?.body,
    '#finding-artifact': content?.artifact_url, '#finding-time-cost': content?.time_cost,
    '#finding-memory-cost': content?.memory_cost, '#finding-success': content?.success_probability
  };
  for (const [selector, value] of Object.entries(labels)) $(selector).value = value || '';
  $('#finding-model').value = content?.model || 'not-applicable';
  $('#finding-kind').value = content?.evidence_kind || 'argument';
  renderFindingProfiles(content?.profile_ids || []);
  loadSupportingRecoveries(content?.submission_id || '');
  $('.finding-advanced').open = Boolean(content?.profile_ids?.length || content?.time_cost || content?.memory_cost || content?.success_probability || content?.submission_id);
  setText('#finding-title', editing ? 'Revise the finding.' : followup ? 'Follow the work.' : 'Record a finding.');
  setText('#finding-description', editing ? 'Publish an updated account of the claim. The previous version remains available.' : 'State the claim, where it applies, and enough of the work to inspect it.');
  setText('#finding-context', editing ? `Revision ${Number(content.revision) + 1}` : 'Research contribution');
  setText('#save-finding', editing ? 'Publish revision →' : 'Publish finding →');
  $('#finding-relationship').hidden = !followup;
  setText('#finding-relationship', followup ? 'Follow-up to: ' + followup.latest.title : '');
  openDialog(findingDialog, trigger);
}
$('#finding-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (research.saving) return;
  const target = research.targets.find(item => item.id === $('#finding-target').value);
  if (!target) return setError('#finding-error', 'Choose a research target.');
  const payload = {
    target_id: target.id, target_version: findingVersion(target),
    profile_ids: [...$('#finding-profiles').querySelectorAll('input:checked')].map(item => item.value),
    title: $('#finding-name').value.trim(), claim: $('#finding-claim').value.trim(),
    conditions: $('#finding-conditions').value.trim(), body: $('#finding-body').value.trim(),
    model: $('#finding-model').value, evidence_kind: $('#finding-kind').value,
    submission_id: $('#finding-recovery-field').hidden ? null : ($('#finding-recovery').value || null)
  };
  if (!payload.title || !payload.claim || !payload.conditions || !payload.body) return setError('#finding-error', 'Complete the title, claim, conditions, and write-up.');
  const supporting = research.recoveries.find(item => item.id === payload.submission_id);
  if (supporting && !payload.profile_ids.includes(supporting.profile_id)) {
    return setError('#finding-error', `Select profile ${supporting.profile_id} to attach this recovery, or choose No recovery attached.`);
  }
  for (const [key, selector] of Object.entries({artifact_url: '#finding-artifact', time_cost: '#finding-time-cost', memory_cost: '#finding-memory-cost', success_probability: '#finding-success'})) {
    const value = $(selector).value.trim();
    if (value) payload[key] = value;
  }
  const editing = research.editing;
  const relation = research.followup?.id || editing?.latest.related_contribution_id;
  if (relation) payload.related_contribution_id = relation;
  if (editing) payload.expected_revision = Number(editing.latest.revision);
  research.saving = true;
  $('#save-finding').disabled = true;
  setText('#save-finding', 'Publishing…');
  setError('#finding-error', '');
  try {
    const path = editing ? '/api/contributions/' + encodeURIComponent(editing.id) + '/revisions' : '/api/contributions';
    await api(path, payload);
    findingDialog.close();
    selectResearchTarget(target.id);
    toast(editing ? 'Revision published. Earlier versions remain in the record.' : 'Finding published as unreviewed.');
  } catch (error) {
    setError('#finding-error', (error.status === 0 || error.status >= 500) ? 'The save could not be confirmed. Check the research list before publishing again. ' + error.message : error.message);
    if (error.status === 403) synchronizeSession();
  } finally {
    research.saving = false;
    $('#save-finding').disabled = false;
    setText('#save-finding', editing ? 'Publish revision →' : 'Publish finding →');
  }
});
$('#nav-experiment').addEventListener('click', () => showWorkspace('experiment'));
$('#nav-research').addEventListener('click', () => showWorkspace('research'));
$('#retry-research').addEventListener('click', loadResearch);
$('#add-finding').addEventListener('click', () => openFinding());
$('#more-findings').addEventListener('click', () => loadFindings(true));
$('#finding-target').addEventListener('change', () => { renderFindingProfiles(); loadSupportingRecoveries(); });
$('#finding-recovery').addEventListener('change', () => {
  const selected = research.recoveries.find(item => item.id === $('#finding-recovery').value);
  if (selected) {
    for (const checkbox of $('#finding-profiles').querySelectorAll('input')) {
      if (checkbox.value === selected.profile_id) checkbox.checked = true;
    }
  }
});
const targetTabs = [...document.querySelectorAll('[data-target-kind]')];
for (const [index, tab] of targetTabs.entries()) {
  tab.addEventListener('click', () => chooseTargetKind(tab.dataset.targetKind));
  tab.addEventListener('keydown', event => {
    const indices = {ArrowRight: (index + 1) % targetTabs.length, ArrowLeft: (index - 1 + targetTabs.length) % targetTabs.length, Home: 0, End: targetTabs.length - 1};
    if (event.key in indices) {
      event.preventDefault();
      chooseTargetKind(targetTabs[indices[event.key]].dataset.targetKind, true);
    }
  });
}

async function initialize() {
  globalError('');
  $('#retry-button').disabled = true;
  $('#challenge-loading').hidden = false;
  try {
    const [session, catalogue] = await Promise.all([api('/api/session'), api('/api/schemes')]);
    setSession(session);
    state.schemes = catalogue.schemes || [];
    state.profile = state.schemes.flatMap(scheme => scheme.profiles || [])[0] || null;
    state.ready = true;
    renderProfiles();
    await loadChallenge();
  } catch (error) {
    globalError(error.message);
    $('#challenge-loading').hidden = true;
    setText('#scheme-summary', 'The workspace could not be loaded. Use Try again above to reconnect.');
  } finally { $('#retry-button').disabled = false; }
}
setInterval(updateClock, 1000);
setInterval(() => { if (!document.hidden) synchronizeSession(); }, 60000);
initialize();

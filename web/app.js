'use strict';

const $ = selector => document.querySelector(selector);
// Keep the research interface dormant while the first MVP focuses on numerical challenges.
const RESEARCH_ENABLED = false;
const state = {
  user: null, csrf: '', canReviewProposals: false, schemes: [], profile: null, challenge: null,
  methods: [], view: 'leaderboard', authMode: 'login', round: null,
  timeOffset: 0, ready: false, challengeLoading: false, issueBusy: false,
  submitBusy: false, pendingAttempt: null, roundRefreshing: false,
  challengeRequest: 0, leaderboardRequest: 0, submissionChallenge: null,
  historyRequest: 0, recoveryRequest: 0,
  screen: 'catalogue', schemeId: null, schemeVersion: null,
  overviewMetrics: {public: 'public_key_bytes', cipher: 'ciphertext_bytes', computation: 'keygen', security: 'classical'},
  importBusy: false, recoveryImportRequest: 0, submissionFormRevision: 0,
  autoIssueAttempts: new Set(), autoIssuePaused: new Map()
};
let toastTimer;
let roundRetryAt = 0;
const dialogTriggers = new WeakMap();
const authDialog = $('#auth-dialog');
const submissionDialog = $('#submission-dialog');
const historyDialog = $('#history-dialog');
const recoveryDialog = $('#recovery-dialog');
const findingDialog = $('#finding-dialog');
const research = {
  visible: false, targets: [], target: null, kind: 'scheme', findings: [],
  nextBefore: null, request: 0, editing: null, followup: null, saving: false, recoveries: [], recoveriesRequest: 0
};
const RECOVERY_FILE_MAX_BYTES = 128 * 1024;
const PROPOSAL_FILE_MAX_BYTES = 48 * 1024;
const proposals = {
  specification: null, digest: null, pending: null, receipt: null,
  validating: false, submitting: false, validationRequest: 0, submitRequest: 0,
  listRequest: 0, detailRequest: 0, listUser: null, detailId: null,
  reviewMode: false, revisingId: null, revisionBase: null, detailRecord: null,
  reviewRequest: 0, reviewBusy: false, reviewPending: null
};

// Versioned data contract: imports never execute code or submit an answer.
function readRecoveryDocument(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > RECOVERY_FILE_MAX_BYTES) {
    throw new Error('Choose a JSON file no larger than 128 KB.');
  }
  let value;
  try { value = JSON.parse(text.replace(/^\uFEFF/, '')); }
  catch { throw new Error('This file is not valid JSON. Use the downloadable template, without Markdown code fences.'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The result must be a JSON object.');
  const allowed = ['format', 'challenge_id', 'candidate', 'method', 'compute', '_instructions'];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new Error('The result contains an unrecognized field. Use the template’s field names.');
  if (value.format !== 'crucible-recovery-v1') throw new Error('Set format to "crucible-recovery-v1", as in the template.');
  if (typeof value.challenge_id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(value.challenge_id)) throw new Error('challenge_id must be the exact identifier from a downloaded challenge or result template.');
  if (Object.hasOwn(value, '_instructions') && (!value._instructions || typeof value._instructions !== 'object' ||
      Array.isArray(value._instructions) || Object.values(value._instructions).some(item => typeof item !== 'string'))) {
    throw new Error('_instructions must be an object containing text, or it can be removed.');
  }
  return value;
}
function parseRecoveryFile(text, challenge) {
  const value = readRecoveryDocument(text);
  function object(input, name, allowed) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error(`${name} must be a JSON object.`);
    if (Object.keys(input).some(key => !allowed.includes(key))) throw new Error(`${name} contains an unrecognized field. Use the template's field names.`);
  }
  function string(input, name, max, required = true) {
    if (typeof input !== 'string' || [...input].length > max || (required && !input.trim())) {
      throw new Error(`${name} must be ${required ? 'nonblank text' : 'text'} of at most ${max} characters.`);
    }
    return input.trim();
  }
  if (!challenge || value.challenge_id !== challenge.id) throw new Error('This file belongs to a different challenge. Use the template for this exact instance.');
  const keyBytes = sharedKeyBytes(challenge);
  if (keyBytes === null) throw new Error('This challenge does not declare a supported shared-key length.');
  if (typeof value.candidate !== 'string' || !new RegExp(`^[0-9a-fA-F]{${keyBytes * 2}}$`).test(value.candidate)) {
    throw new Error(`candidate must contain exactly ${keyBytes * 2} hexadecimal characters for this ${keyBytes}-byte key.`);
  }
  object(value.method, 'method', ['name', 'pseudocode']);
  const method = {
    name: string(value.method.name, 'method.name', 100),
    pseudocode: string(value.method.pseudocode, 'method.pseudocode', 15000)
  };
  if (new TextEncoder().encode(JSON.stringify(method)).length > 64 * 1024) {
    throw new Error('The method’s JSON encoding must fit within 64 KiB. Shorten the pseudocode or remove escaped control characters.');
  }
  object(value.compute, 'compute', ['hardware', 'workers', 'wall_seconds', 'precomputation', 'peak_memory', 'notes']);
  const compute = value.compute;
  if (!Number.isInteger(compute.workers) || compute.workers < 1 || compute.workers > 1000000) {
    throw new Error('compute.workers must be an integer from 1 to 1000000.');
  }
  if (typeof compute.wall_seconds !== 'number' || !Number.isFinite(compute.wall_seconds) || compute.wall_seconds < 0 || compute.wall_seconds > 315576000) {
    throw new Error('compute.wall_seconds must be a number from 0 to 315576000, in seconds.');
  }
  return {
    format: value.format, challenge_id: value.challenge_id, candidate: value.candidate.toLowerCase(), method,
    compute: {
      hardware: string(compute.hardware, 'compute.hardware', 500),
      workers: compute.workers, wall_seconds: compute.wall_seconds,
      precomputation: string(compute.precomputation, 'compute.precomputation', 2000),
      peak_memory: string(compute.peak_memory, 'compute.peak_memory', 300),
      notes: string(compute.notes === undefined ? '' : compute.notes, 'compute.notes', 2000, false)
    }
  };
}

function recoveryTemplate(challenge) {
  const keyBytes = sharedKeyBytes(challenge);
  return {
    format: 'crucible-recovery-v1', challenge_id: challenge.id, candidate: '',
    method: {name: '', pseudocode: ''},
    compute: {hardware: '', workers: 1, wall_seconds: null, precomputation: '', peak_memory: '', notes: ''},
    _instructions: {
      file: 'Save one UTF-8 JSON object as a .json file, at most 131072 bytes (128 KiB), including these instructions. No Markdown fences or JSON comments. Escape line breaks inside strings as \\n.',
      fields: 'Required: format, challenge_id, candidate, method.name, method.pseudocode, compute.hardware, compute.workers, compute.wall_seconds, compute.precomputation, compute.peak_memory. Optional: compute.notes and this _instructions object. No other fields are accepted.',
      instructions: 'You may keep or remove _instructions. If kept, it must be an object of text values. It is guidance only, ignored when submitting; editing it cannot change validation rules.',
      format: 'Keep format exactly "crucible-recovery-v1". Empty strings and the null attack-time placeholder must be filled in before import.',
      challenge_id: 'Use the exact challenge.id from the downloaded challenge contract or this template. The website looks up that instance and selects its scheme, release, and tier automatically, even if another tier is displayed. It must belong to the signed-in account, still be open, and have no final answer. Do not replace this ID with a tier number.',
      candidate: `The complete ${keyBytes}-byte shared secret: exactly ${keyBytes * 2} hexadecimal characters. Uppercase or lowercase accepted; no spaces, line breaks, or 0x prefix.`,
      'method.name': 'Required nonblank text, at most 100 Unicode characters; a short descriptive name for the attack.',
      'method.pseudocode': 'Required nonblank text, at most 15000 Unicode characters. Describe the algorithm, key derivation, and preparation. The method object must also fit within a 65536-byte JSON request, including escapes. Pseudocode is stored as text, never executed.',
      'compute.hardware': 'Required nonblank text, at most 500 Unicode characters. Describe the CPU/GPU model and configuration used.',
      'compute.workers': 'Required JSON number: an integer from 1 through 1000000. State the number of parallel workers; do not quote the number or use a boolean.',
      'compute.wall_seconds': 'Required finite JSON number from 0 through 315576000; fractions are allowed. Report attack wall-clock time in seconds, not the time since the challenge was downloaded. Replace null; do not quote the number.',
      'compute.precomputation': 'Required nonblank text, at most 2000 Unicode characters. Describe preparation, its cost, and reused data, or write "None".',
      'compute.peak_memory': 'Required nonblank text, at most 300 Unicode characters. Give an estimate with units, or write "Not measured".',
      'compute.notes': 'Optional text, at most 2000 Unicode characters. Include measurement uncertainty or other relevant details. An omitted or empty string is allowed; null is not.',
      text: 'Text limits apply before trimming outer whitespace. Required text cannot consist only of whitespace. Keep method and compute declarations accurate; they are contributor-reported.',
      deadline: `This instance closes at ${utcDate(challenge.ends_at)}. The complete final answer must reach the server strictly before that deadline. Importing a file does not extend the deadline or submit an answer.`,
      submission: 'Review the imported fields, then explicitly submit. Each instance accepts one well-formed final answer, whether correct or incorrect. If a submission outcome is uncertain, retry the same answer; do not replace it with another file.',
      publication: 'A correct recovery may publish your handle, method, pseudocode, and compute declaration on the leaderboard. Do not include passwords, API tokens, or other credentials.'
    }
  };
}

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
function initialSecurityEstimate(scheme, profile) {
  const model = scheme?.initial_security_model;
  const estimate = profile?.initial_security_estimate;
  return typeof model?.id === 'string' && model.id && estimate?.model_id === model.id &&
    Number.isFinite(estimate.bits) && estimate.bits >= 0 ? estimate.bits : null;
}
function estimateBits(value) { return value.toLocaleString('en-US', {maximumFractionDigits: 1}); }
function tierNote(scheme, profile) {
  const estimate = initialSecurityEstimate(scheme, profile);
  return estimate === null ? 'Each scheme maps its tiers to its own parameters.' :
    `Initial classical estimate: ≈${estimateBits(estimate)} bits.`;
}
function availableReleases() { return state.schemes.flatMap(scheme => [scheme, ...(scheme.previous_releases || [])]); }
function selectedScheme() {
  return availableReleases().find(scheme => scheme.id === state.schemeId && String(scheme.version) === state.schemeVersion);
}
function selectionKey() { return JSON.stringify([state.schemeId, state.schemeVersion, profileId()]); }
function schemeRoute(scheme, profile, view = 'leaderboard') {
  const query = new URLSearchParams({profile: profileId(profile), view});
  return '#challenge/' + encodeURIComponent(scheme.id) + '/' + encodeURIComponent(scheme.version) + '?' + query;
}
function overviewRoute(scheme, profile = null) {
  return '#scheme/' + encodeURIComponent(scheme.id) + '/' + encodeURIComponent(scheme.version) +
    (profile ? '?' + new URLSearchParams({profile: profileId(profile)}) : '');
}
function navigate(hash) {
  if (window.location.hash === hash) applyRoute();
  else window.location.hash = hash;
}
function workspaceSelector(screen) {
  return {scheme: '#workspace', overview: '#overview-workspace', research: '#research-workspace', about: '#about-workspace', propose: '#proposal-workspace'}[screen] || '#catalogue-workspace';
}
function applyRoute(focus = false) {
  if (!state.ready) return;
  const previous = selectionKey();
  const previousScreen = state.screen;
  const previousView = state.view;
  let hash = window.location.hash || '#challenge';
  if (!RESEARCH_ENABLED && hash.split('?')[0] === '#research') {
    hash = '#challenge';
    window.history.replaceState(null, '', hash);
  }
  const [path, query = ''] = hash.split('?');
  let scheme, profile, view = 'leaderboard';
  let screen = path === '#propose' ? 'propose' : path === '#about' ? 'about' : path === '#research' ? 'research' : 'catalogue';
  let invalid = !['#challenge', '#research', '#about', '#propose'].includes(path);
  const match = path.match(/^#challenge\/([^/]+)\/([^/]+)$/);
  if (match) {
    try {
      const id = decodeURIComponent(match[1]);
      const version = decodeURIComponent(match[2]);
      scheme = availableReleases().find(item => item.id === id && String(item.version) === version);
      const params = new URLSearchParams(query);
      profile = scheme?.profiles?.find(item => profileId(item) === params.get('profile'));
      if (!params.has('profile')) profile = scheme?.profiles?.find(item => profileId(item) === scheme.next_profile_id) || scheme?.profiles?.[0];
      if (scheme && profile) {
        screen = 'scheme';
        invalid = false;
        view = ['challenge', 'leaderboard', 'protocol'].includes(params.get('view')) ? params.get('view') : 'leaderboard';
        if (!params.has('profile')) window.history.replaceState(null, '', schemeRoute(scheme, profile, view));
      }
    } catch { /* A malformed link must never select a different protocol. */ }
  }
  const overviewMatch = path.match(/^#scheme\/([^/]+)\/([^/]+)$/);
  if (overviewMatch) {
    try {
      const id = decodeURIComponent(overviewMatch[1]);
      const version = decodeURIComponent(overviewMatch[2]);
      scheme = availableReleases().find(item => item.id === id && String(item.version) === version);
      const params = new URLSearchParams(query);
      profile = scheme?.profiles?.find(item => profileId(item) === params.get('profile'));
      if (scheme && (!params.has('profile') || profile)) { screen = 'overview'; invalid = false; }
    } catch { /* Unknown or malformed releases fail closed to the catalogue. */ }
  }
  state.screen = screen;
  state.schemeId = ['scheme', 'overview'].includes(screen) ? scheme.id : null;
  state.schemeVersion = ['scheme', 'overview'].includes(screen) ? String(scheme.version) : null;
  state.profile = screen === 'scheme' ? profile : null;
  const changed = previous !== selectionKey();
  if (changed) {
    state.challengeRequest++;
    state.leaderboardRequest++;
    state.challenge = null;
    state.challengeLoading = false;
    $('#challenge-error').hidden = true;
    $('#leaderboard-content').replaceChildren();
  }
  showWorkspace(screen);
  if (previousScreen === 'propose' && screen !== 'propose') leaveProposalPage();
  if (screen === 'propose') enterProposalPage(new URLSearchParams(query), previousScreen !== 'propose');
  const routeError = 'That KEM, version, or profile is unavailable. Choose a published KEM below.';
  if (invalid) globalError(routeError);
  else if ($('#global-message-text').textContent === routeError) globalError('');
  if (screen === 'scheme') {
    $('#scheme-back').href = overviewRoute(scheme, profile);
    renderProfiles();
    renderView(view);
    if (changed || previousScreen !== 'scheme' || (view === 'challenge' && previousView !== view)) loadChallenge();
    if (view === 'leaderboard' && (changed || previousView !== view || previousScreen !== 'scheme')) loadLeaderboard();
  }
  if (screen === 'overview') renderOverview(scheme, profile);
  document.title = ['scheme', 'overview'].includes(screen) ? scheme.name + (screen === 'overview' ? ' overview' : '') + ' — Crucible' : screen === 'research' ? 'Research — Crucible' : screen === 'about' ? 'About us — Crucible' : screen === 'propose' ? 'Propose a scheme — Crucible' : 'Schemes — Crucible';
  if (focus && previousScreen !== screen) $(workspaceSelector(screen)).focus();
}
function sharedKeyBytes(challenge) {
  const bytes = challenge?.public?.encoding?.shared_secret_bytes;
  return Number.isInteger(bytes) && bytes > 0 && bytes <= 4096 ? bytes : null;
}
function roundRemaining() { return state.round ? Math.max(0, Math.ceil(state.round.ends_at - now())) : null; }
function absorbClock(data) {
  // A delayed response from the previous hour must not reactivate its round.
  if (data?.round && state.round && data.round.id < state.round.id) return;
  if (Number.isFinite(Number(data?.server_time))) state.timeOffset = Number(data.server_time) * 1000 - Date.now();
  if (data?.round) {
    if (state.round?.id !== data.round.id) {
      state.autoIssueAttempts.clear();
      state.autoIssuePaused.clear();
    }
    state.round = data.round;
  }
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
    if (Array.isArray(data.errors)) error.fieldErrors = data.errors.filter(item => item && typeof item.path === 'string' && typeof item.message === 'string');
    throw error;
  }
  absorbClock(data);
  return data;
}
function setSession(data) {
  const previousId = state.user?.id;
  const previousReview = state.canReviewProposals;
  state.user = data.user || null;
  state.csrf = data.csrf_token || '';
  state.canReviewProposals = Boolean(state.user && data.capabilities?.review_protocol_proposals);
  if (previousId !== state.user?.id) {
    resetProposalAccount(previousId);
    state.historyRequest++;
    state.recoveryRequest++;
    state.autoIssueAttempts.clear();
    state.autoIssuePaused.clear();
    state.challenge = null;
    state.challengeRequest++;
    state.leaderboardRequest++;
    state.methods = [];
    state.pendingAttempt = null;
    state.submissionChallenge = null;
    state.recoveryImportRequest++;
    state.importBusy = false;
    state.submissionFormRevision++;
    $('#submission-form').reset();
    lockSubmissionFields(false);
    setError('#recovery-file-error', '');
    $('#recovery-file-status').hidden = true;
    if (submissionDialog.open) submissionDialog.close();
    if (recoveryDialog.open) recoveryDialog.close();
    if (historyDialog.open) historyDialog.close();
    $('#history-content').replaceChildren();
    $('#recovery-content').replaceChildren();
    setText('#recovery-context', '');
    setText('#recovery-title', 'Submission details');
    if (findingDialog.open) findingDialog.close();
    if (research.visible) renderFindings();
  }
  if (previousId === state.user?.id && previousReview !== state.canReviewProposals) resetProposalAccount(previousId);
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
  $('#countdown').setAttribute('aria-label', `${Math.floor(remaining / 60)} minutes and ${remaining % 60} seconds until this challenge closes`);
  $('#countdown').classList.toggle('urgent', remaining < 60);
  setText('#submission-time', remaining ? clock + ' until the round ends' : 'This round has ended.');
  const formExpired = state.submissionChallenge && now() >= state.submissionChallenge.ends_at;
  $('#submit-answer').disabled = state.submitBusy || state.importBusy || Boolean(formExpired && !state.pendingAttempt) || sharedKeyBytes(state.submissionChallenge) === null;
  updateRecoveryControls();
  if (formExpired && submissionDialog.open && !state.submitBusy && !state.pendingAttempt) {
    setError('#submission-error', 'This round has ended. Close this form to receive an instance for the new round.');
  }
  $('#open-submission').disabled = !state.ready || state.submitBusy || state.importBusy;
  const staleChallenge = state.challenge && state.challenge.round_id !== state.round.id;
  if ((remaining === 0 || staleChallenge) && state.ready && !state.roundRefreshing && !state.submitBusy && Date.now() >= roundRetryAt) refreshRound();
}
function isChallengeOpen() {
  return canSubmitChallenge(state.challenge);
}
function canSubmitChallenge(challenge) {
  return Boolean(challenge && challenge.status === 'open' && !challenge.submission &&
    Number.isFinite(challenge.ends_at) && now() < challenge.ends_at && sharedKeyBytes(challenge) !== null);
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
  renderCatalogue();
  applyRoute();
}

function profileBytes(scheme, profile, metric) {
  const value = scheme?.profile_metrics?.[profileId(profile)]?.[metric];
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function participantCount(scheme) {
  const value = scheme?.participant_count;
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function renderCatalogue() {
  const target = $('#kem-catalogue');
  const commonReference = state.schemes.length && state.schemes.every(scheme => {
    const profile = scheme.profiles?.find(item => instanceSize(item) === 50);
    return scheme.initial_security_model?.id === 'classical-baseline-v1' && initialSecurityEstimate(scheme, profile) !== null && profile.initial_security_estimate.target_bits === 128;
  });
  setText('#tier-help', commonReference ? 'Tiers share an initial classical reference: Tier 50 ≈128 bits. Actual security and attack costs may differ.' :
    'Tiers are specific to each scheme. Equal tiers across schemes do not imply equal security or attack cost.');
  if (!state.schemes.length) return showEmpty(target, 'No KEMs have been published yet.');
  const wrap = node('div', undefined, 'kem-table-wrap');
  const table = node('table', undefined, 'kem-table');
  const head = node('thead');
  const headings = node('tr');
  for (const label of ['KEM', 'Largest recovery', 'Recovered by', 'Participants']) {
    const th = node('th', label);
    th.scope = 'col';
    if (label === 'Largest recovery') {
      th.className = 'recovery-heading';
      const help = node('button'); help.id = 'tier-info'; help.type = 'button';
      help.setAttribute('aria-label', 'About challenge tiers');
      help.setAttribute('aria-controls', 'tier-help');
      help.setAttribute('aria-expanded', String(!$('#tier-help').hidden));
      help.addEventListener('click', () => {
        const expanded = $('#tier-help').hidden;
        $('#tier-help').hidden = !expanded;
        help.setAttribute('aria-expanded', String(expanded));
      });
      const heading = node('span', undefined, 'recovery-heading-label');
      heading.append(node('span', label), help);
      th.replaceChildren(heading);
    }
    if (label === 'Participants') th.setAttribute('aria-sort', 'descending');
    if (label === 'Participants') th.title = 'Distinct accounts with at least one submitted recovery attempt, successful or unsuccessful, including compatible releases.';
    headings.append(th);
  }
  head.append(headings);
  const body = node('tbody');
  const catalogue = [...state.schemes].sort((a, b) =>
    (participantCount(b) ?? -1) - (participantCount(a) ?? -1) ||
    a.name.localeCompare(b.name, 'en', {sensitivity: 'base'}) || a.id.localeCompare(b.id));
  for (const scheme of catalogue) {
    const profiles = scheme.profiles || [];
    const largest = profiles.find(profile => profileId(profile) === scheme.largest_recovered_profile_id);
    const row = node('tr');
    const name = node('td');
    const link = node('a', scheme.name, 'kem-name');
    link.href = overviewRoute(scheme);
    name.append(link);
    const progress = node('td');
    progress.dataset.label = 'Largest recovery';
    if (largest) {
      const record = node('a', 'Tier ' + instanceSize(largest), 'kem-progress');
      record.href = schemeRoute(scheme, largest, 'leaderboard');
      record.setAttribute('aria-label', `View recoveries at tier ${instanceSize(largest)}`);
      progress.append(record);
    } else progress.append(node('span', 'No recoveries yet', 'kem-record-empty'));
    const firstCell = node('td');
    firstCell.dataset.label = 'Recovered by';
    if (largest && scheme.first_recovery?.profile_id === profileId(largest)) {
      const first = node('button', scheme.first_recovery.handle, 'kem-first-handle');
      first.type = 'button';
      first.disabled = !scheme.first_recovery.submission_id;
      first.setAttribute('aria-haspopup', 'dialog');
      first.setAttribute('aria-label', `View first recovery by ${scheme.first_recovery.handle} for ${scheme.name}, Tier ${instanceSize(largest)}`);
      first.title = 'View recovery details · ' + utcDate(scheme.first_recovery.received_at);
      first.addEventListener('click', () => openSubmissionDetails(scheme.first_recovery.submission_id, first));
      firstCell.append(first);
    } else firstCell.append(node('span', '—', 'kem-meta'));
    const participants = node('td');
    participants.dataset.label = 'Participants';
    const count = participantCount(scheme);
    participants.append(node('span', count === null ? '—' : count.toLocaleString('en-US'), 'kem-participants'));
    row.append(name, progress, firstCell, participants);
    body.append(row);
  }
  table.append(head, body);
  wrap.append(table);
  target.replaceChildren(wrap);
}
const OVERVIEW_METRICS = {
  public_key_bytes: {label: 'Public key', title: 'Public-key size', unit: 'Bytes', kind: 'encoding'},
  ciphertext_bytes: {label: 'Ciphertext', title: 'Ciphertext size', unit: 'Bytes', kind: 'encoding'},
  shared_secret_bytes: {label: 'Shared secret', title: 'Shared-secret size', unit: 'Bytes', kind: 'encoding'},
  keygen: {label: 'Key generation', title: 'Key generation', unit: 'Seconds', kind: 'timing'},
  encapsulate: {label: 'Encapsulation', title: 'Encapsulation', unit: 'Seconds', kind: 'timing'},
  decapsulate: {label: 'Decapsulation', title: 'Decapsulation', unit: 'Seconds', kind: 'timing'},
  classical: {label: 'Initial estimate', title: 'Initial classical estimate', unit: 'Reference work (bits)', kind: 'baseline'},
  quantum: {label: 'Quantum', title: 'Quantum security estimate', unit: 'Estimated security (bits)', kind: 'security'}
};
const OVERVIEW_CARDS = {
  public: ['public_key_bytes', 'ciphertext_bytes', 'shared_secret_bytes'],
  cipher: ['public_key_bytes', 'ciphertext_bytes', 'shared_secret_bytes'],
  computation: ['keygen', 'encapsulate', 'decapsulate'],
  security: ['classical', 'quantum']
};
function overviewProfiles(scheme) {
  return (scheme.profiles || []).filter(profile => Number.isSafeInteger(instanceSize(profile)) && instanceSize(profile) > 0)
    .slice().sort((a, b) => instanceSize(a) - instanceSize(b));
}
function overviewValue(scheme, profile, metric) {
  if (metric === 'classical') return initialSecurityEstimate(scheme, profile);
  if (metric === 'shared_secret_bytes') {
    const value = profile.shared_secret_bytes;
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  return ['public_key_bytes', 'ciphertext_bytes'].includes(metric) ? profileBytes(scheme, profile, metric) : null;
}
function svgNode(tag, attributes = {}, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = String(text);
  return element;
}
function renderOverviewChart(scheme, cardId) {
  const metric = state.overviewMetrics[cardId];
  const descriptor = OVERVIEW_METRICS[metric];
  const target = $('#overview-chart-' + cardId);
  const card = target.closest('.overview-card');
  const profiles = overviewProfiles(scheme);
  const values = profiles.map(profile => ({tier: instanceSize(profile), value: overviewValue(scheme, profile, metric)}));
  const measured = values.filter(point => point.value !== null);
  const minTier = values[0]?.tier, maxTier = values.at(-1)?.tier;
  const recovered = profiles.find(profile => profileId(profile) === scheme.largest_recovered_profile_id);
  const recoveredTier = recovered ? instanceSize(recovered) : null;
  const recoveredLabel = recovered ? `Largest recovered · Tier ${recoveredTier}` : '';
  setText('#overview-title-' + cardId, descriptor.title);
  for (const button of card.querySelectorAll('[data-overview-metric]')) button.setAttribute('aria-pressed', String(button.dataset.overviewMetric === metric));
  const svg = svgNode('svg', {viewBox: '0 0 480 240', role: 'img', 'aria-labelledby': 'graph-title-' + cardId + ' graph-description-' + cardId, class: 'overview-svg'});
  svg.append(svgNode('title', {id: 'graph-title-' + cardId}, descriptor.title + ' by challenge tier'));
  const summary = measured.length ? `${measured.length} published values; ${descriptor.unit.toLowerCase()} on the vertical axis.` : 'No values have been published. This chart shows axes only.';
  svg.append(svgNode('desc', {id: 'graph-description-' + cardId}, `${summary} ${values.length ? `Challenge tiers ${minTier} to ${maxTier} on the horizontal axis.` : 'No challenge tiers published.'}${recovered ? ` ${recoveredLabel}. The red line marks a recorded recovery, not a security guarantee or individual recoveries at every lower tier.` : ''}`));
  const left = 48, right = 465, top = 43, bottom = 190;
  const x = tier => minTier === maxTier ? (left + right) / 2 : left + (tier - minTier) / (maxTier - minTier) * (right - left);
  const maximum = measured.length ? Math.max(...measured.map(point => point.value)) : 0;
  const ceiling = maximum > 0 ? maximum : 1;
  const y = value => bottom - value / ceiling * (bottom - top);
  svg.append(svgNode('text', {x: left, y: 19, class: 'chart-axis-title'}, descriptor.unit));
  if (recovered && x(recoveredTier) > left) svg.append(svgNode('rect', {x: left, y: top, width: x(recoveredTier) - left, height: bottom - top, class: 'chart-recovery-area', 'aria-hidden': 'true'}));
  for (let index = 0; index <= 3; index++) {
    const gridY = top + index * (bottom - top) / 3;
    svg.append(svgNode('line', {x1: left, y1: gridY, x2: right, y2: gridY, class: 'chart-gridline'}));
  }
  svg.append(svgNode('path', {d: `M${left} ${top}V${bottom}H${right}`, class: 'chart-axis'}));
  if (measured.length) {
    for (const value of [0, ceiling]) svg.append(svgNode('text', {x: left - 9, y: y(value) + 4, 'text-anchor': 'end', class: 'chart-tick'}, value.toLocaleString('en-US')));
  }
  if (values.length) {
    const tickTiers = [...new Set([minTier, values[Math.floor((values.length - 1) / 2)].tier, maxTier])];
    for (const tier of tickTiers) {
      svg.append(svgNode('line', {x1: x(tier), y1: bottom, x2: x(tier), y2: bottom + 5, class: 'chart-axis'}));
      svg.append(svgNode('text', {x: x(tier), y: bottom + 19, 'text-anchor': 'middle', class: 'chart-tick'}, tier));
    }
  }
  svg.append(svgNode('text', {x: (left + right) / 2, y: 235, 'text-anchor': 'middle', class: 'chart-axis-title'}, 'Challenge tier'));
  if (measured.length) {
    let path = '', connected = false;
    for (const point of values) {
      if (point.value === null) { connected = false; continue; }
      path += `${connected ? 'L' : 'M'}${x(point.tier).toFixed(2)} ${y(point.value).toFixed(2)} `;
      connected = true;
    }
    svg.append(svgNode('path', {d: path.trim(), class: 'chart-series'}));
    for (const point of measured) {
      const dot = svgNode('circle', {cx: x(point.tier), cy: y(point.value), r: values.length > 60 ? 1.6 : 2.8, class: 'chart-point', 'data-tier': point.tier, 'data-value': point.value});
      dot.append(svgNode('title', {}, `Tier ${point.tier}: ${point.value.toLocaleString('en-US')} ${descriptor.unit.toLowerCase()}`));
      svg.append(dot);
    }
  } else {
    svg.append(svgNode('text', {x: (left + right) / 2, y: 106, 'text-anchor': 'middle', class: 'chart-empty-label'}, ['security', 'baseline'].includes(descriptor.kind) ? 'No estimates published' : descriptor.kind === 'timing' ? 'No timings published' : 'No values published'));
    svg.append(svgNode('text', {x: (left + right) / 2, y: 128, 'text-anchor': 'middle', class: 'chart-empty-detail'}, 'Awaiting evidence'));
  }
  if (recovered) {
    const marker = svgNode('line', {x1: x(recoveredTier), y1: top, x2: x(recoveredTier), y2: bottom, class: 'chart-recovery-line', 'data-tier': recoveredTier});
    marker.append(svgNode('title', {}, recoveredLabel));
    svg.append(marker);
  }
  target.replaceChildren(svg);
  if (recovered) {
    const label = node('p', recoveredLabel, 'overview-recovery-note');
    target.append(label);
  }
  const note = descriptor.kind === 'timing' ? 'A timing series needs a reproducible benchmark, hardware, and measurement method.' :
    descriptor.kind === 'baseline' ? (measured.length ? 'Frozen classical reference; not a measured security guarantee.' : 'No initial classical estimate is published for this release.') :
    descriptor.kind === 'security' ? 'An estimate needs a stated security game, attack model, and source. Recovery records are not estimates.' :
    metric === 'shared_secret_bytes' ? 'Declared output length at each tier. Key length is not a security estimate.' :
    'Canonical encoded bytes. Excludes JSON and shared domain parameters. Dots mark published tiers.';
  target.append(node('p', note, 'overview-chart-note'));
  const badge = $('#overview-badge-' + cardId);
  badge.textContent = measured.length ? (descriptor.kind === 'baseline' ? 'Initial model' : 'Published values') : 'Not reported';
  badge.classList.toggle('is-empty', !measured.length);
  if ((descriptor.kind === 'encoding' || descriptor.kind === 'baseline') && values.length) {
    const details = node('details', undefined, 'overview-values');
    details.append(node('summary', 'View values'));
    const table = node('table');
    const caption = node('caption', descriptor.title + ' · version ' + scheme.version);
    const head = node('thead'), row = node('tr');
    for (const label of ['Challenge tier', descriptor.unit]) { const th = node('th', label); th.scope = 'col'; row.append(th); }
    head.append(row);
    const body = node('tbody');
    for (const point of values) {
      const row = node('tr');
      row.append(node('td', point.tier), node('td', point.value === null ? 'Not reported' : point.value.toLocaleString('en-US')));
      body.append(row);
    }
    table.append(caption, head, body);
    const scroll = node('div', undefined, 'overview-values-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', descriptor.title + ' values by challenge tier');
    scroll.append(table); details.append(scroll); target.append(details);
  }
}
function renderOverview(scheme, contextProfile = null) {
  setText('#overview-title', scheme.name);
  setText('#overview-summary', scheme.summary || '');
  const profiles = overviewProfiles(scheme);
  const current = state.schemes.find(item => item.id === scheme.id);
  const archived = String(current?.version) !== String(scheme.version);
  setText('#overview-release', `${archived ? 'Archived release ' + scheme.version + ' · ' : ''}${profiles.length} challenge tiers`);
  const entry = contextProfile || profiles.find(profile => profileId(profile) === scheme.next_profile_id) || profiles[0];
  const challengeLink = $('#overview-challenges');
  challengeLink.hidden = !entry;
  challengeLink.href = entry ? schemeRoute(scheme, entry) : '#challenge';
  const record = profiles.find(profile => profileId(profile) === scheme.largest_recovered_profile_id);
  setText('#overview-progress', record ? `Largest recovery · Tier ${instanceSize(record)}` : 'No recoveries yet');
  const grid = $('#overview-graphs');
  if (!grid.childElementCount) {
    for (const [id, metrics] of Object.entries(OVERVIEW_CARDS)) {
      const card = node('article', undefined, 'overview-card');
      card.setAttribute('aria-labelledby', 'overview-title-' + id);
      const heading = node('div', undefined, 'overview-card-heading');
      const title = node('h2'); title.id = 'overview-title-' + id;
      const badge = node('span', undefined, 'overview-badge'); badge.id = 'overview-badge-' + id;
      heading.append(title, badge);
      const options = node('div', undefined, 'overview-metric-options');
      options.setAttribute('role', 'group'); options.setAttribute('aria-label', 'Choose metric for ' + (id === 'public' ? 'first encoding chart' : id === 'cipher' ? 'second encoding chart' : id + ' chart'));
      for (const metric of metrics) {
        const button = node('button', OVERVIEW_METRICS[metric].label);
        button.type = 'button'; button.dataset.overviewMetric = metric;
        button.addEventListener('click', () => {
          if (state.screen !== 'overview' || !selectedScheme()) return;
          state.overviewMetrics[id] = metric;
          renderOverviewChart(selectedScheme(), id);
        });
        options.append(button);
      }
      const chart = node('div'); chart.id = 'overview-chart-' + id;
      card.append(heading, options, chart); grid.append(card);
    }
  }
  for (const id of Object.keys(OVERVIEW_CARDS)) renderOverviewChart(scheme, id);
  const overview = scheme.overview || {};
  for (const field of ['strengths', 'limitations']) {
    const list = $('#overview-' + field);
    const values = Array.isArray(overview[field]) ? overview[field].filter(value => typeof value === 'string' && value.trim()) : [];
    list.replaceChildren(...values.map(value => node('li', value)));
    list.closest('section').hidden = !values.length;
  }
  for (const field of ['correctness', 'security']) {
    const value = typeof overview[field] === 'string' ? overview[field] : '';
    setText('#overview-' + field, value);
    $('#overview-' + field).closest('section').hidden = !value;
  }
}
function renderProfiles(previewProfile = null) {
  const input = $('#profile-select');
  const scheme = selectedScheme();
  const profiles = [...(scheme?.profiles || [])].sort((a, b) => instanceSize(a) - instanceSize(b));
  const current = previewProfile || state.profile;
  const index = profiles.findIndex(profile => profileId(profile) === profileId(current));
  const options = $('#size-options');
  const restoreFocus = [...options.querySelectorAll('button')].includes(document.activeElement);
  const largest = profiles.find(profile => profileId(profile) === scheme?.largest_recovered_profile_id);
  const frontier = profiles.find(profile => profileId(profile) === scheme?.next_profile_id);
  const recovered = new Set(scheme?.recovered_profile_ids || (largest ? [profileId(largest)] : []));
  input.disabled = !profiles.length;
  input.min = String(instanceSize(profiles[0]) || 1);
  input.max = String(instanceSize(profiles.at(-1)) || 1);
  const jumpContext = [scheme?.id, scheme?.version, profileId(state.profile)].join('/');
  if (input.dataset.context !== jumpContext || previewProfile) input.value = '';
  input.dataset.context = jumpContext;
  const slider = $('#size-slider');
  slider.min = '0';
  slider.max = String(Math.max(0, profiles.length - 1));
  slider.value = String(Math.max(0, index));
  slider.disabled = profiles.length < 2;
  slider.setAttribute('aria-valuetext', current ? `Challenge tier ${instanceSize(current)}` : 'No tiers available');
  setText('#size-slider-value', current ? String(instanceSize(current)) : '—');
  $('#jump-size').disabled = !profiles.length;
  setText('#size-range', profiles.length ? `${input.min}–${input.max}` : '');
  $('#previous-size').disabled = index <= 0;
  $('#next-size').disabled = index < 0 || index >= profiles.length - 1;
  const advanceLabel = 'Go to next unsolved tier';
  const advanceNumber = frontier ? instanceSize(frontier) : '—';
  const concealAdvance = !frontier || profileId(frontier) === profileId(current);
  $('#advance-size').disabled = concealAdvance;
  $('#advance-size').classList.toggle('is-concealed', concealAdvance);
  $('#advance-size').setAttribute('aria-hidden', String(concealAdvance));
  setText('#advance-size-label', advanceLabel);
  setText('#advance-size-value', String(advanceNumber));
  $('#advance-size').setAttribute('aria-label', `${advanceLabel} ${advanceNumber}`);
  const start = Math.max(0, Math.min(index - 2, profiles.length - 5));
  const choices = profiles.slice(start, start + 5).map(profile => {
    const selected = profileId(profile) === profileId(current);
    const record = Boolean(largest && profileId(profile) === profileId(largest));
    const solved = recovered.has(profileId(profile));
    const nextUnsolved = Boolean(frontier && profileId(profile) === profileId(frontier));
    const prior = Boolean(largest && instanceSize(profile) <= instanceSize(largest));
    const button = node('button', String(instanceSize(profile)), 'size-option');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(selected));
    const status = nextUnsolved ? ', next unsolved tier' : record ? ', largest recovered' : solved ? ', recovered' : prior ? ', earlier tier' : '';
    button.setAttribute('aria-label', `Tier ${instanceSize(profile)}` + status);
    button.title = `Tier ${instanceSize(profile)}` + status;
    button.classList.toggle('is-next-unsolved', nextUnsolved);
    button.classList.toggle('is-prior', prior);
    button.tabIndex = selected ? 0 : -1;
    button.addEventListener('click', () => navigate(schemeRoute(scheme, profile, state.view)));
    button.addEventListener('keydown', event => {
      let target;
      if (event.key === 'ArrowLeft') target = profiles[Math.max(0, index - 1)];
      else if (event.key === 'ArrowRight') target = profiles[Math.min(profiles.length - 1, index + 1)];
      else if (event.key === 'Home') target = profiles[0];
      else if (event.key === 'End') target = profiles.at(-1);
      else return;
      event.preventDefault();
      if (target) navigate(schemeRoute(scheme, target, state.view));
    });
    return button;
  });
  options.replaceChildren(...choices);
  if (restoreFocus) choices.find(button => button.getAttribute('aria-pressed') === 'true')?.focus();
  setError('#profile-error', '');
  setText('#profile-note', tierNote(scheme, current));
  if (!previewProfile) renderProtocol();
}
function mathText(element, value) {
  if (window.CrucibleMath) window.CrucibleMath.render(element, value);
  else element.textContent = value;
}
function addFact(list, label, value, typeset = false) {
  const row = node('div');
  const description = node('dd');
  const text = value === null || value === undefined ? 'Not specified' :
    typeof value === 'object' ? JSON.stringify(value) : value;
  if (typeset) mathText(description, text);
  else description.textContent = text;
  row.append(node('dt', label), description);
  list.append(row);
}
function renderProtocol() {
  const profile = state.profile;
  const scheme = selectedScheme();
  if (scheme) {
    setText('#scheme-title', scheme.name);
    setText('#scheme-summary', scheme.summary);
    setText('#protocol-version', scheme.version ? `${scheme.name} · version ${scheme.version}` : scheme.name);
    const protocol = scheme.protocol || {};
    mathText($('#protocol-construction'), protocol.construction_math || protocol.construction || 'The parameter contract below defines this KEM. Consult its registered specification for the full construction.');
    mathText($('#scheme-construction-short'), protocol.construction_math || protocol.construction || scheme.summary);
    const specifics = $('#scheme-specific-facts');
    specifics.replaceChildren();
    addFact(specifics, 'Underlying problem', protocol.assumption || 'See the registered protocol.');
    addFact(specifics, 'Mapped parameter', scheme.size_parameter?.meaning);
    addFact(specifics, 'Public inputs', protocol.public_input_math || protocol.public_input, true);
    addFact(specifics, 'Required answer', protocol.shared_secret || 'The complete shared key declared by the instance.');
    addFact(specifics, 'Correctness', protocol.correctness || profile?.correctness?.argument);
    const downloads = $('#protocol-downloads');
    downloads.replaceChildren();
    const specUrl = '/api/protocol/spec?' + new URLSearchParams({scheme_id: scheme.id, version: scheme.version});
    for (const [url, label] of [[specUrl, 'Download the scheme specification ↓'], [protocol.source_url, 'Download the reference implementation ↓'], [protocol.example_url, 'Download the example solver ↓']]) {
      if (!url) continue;
      let parsed;
      try { parsed = new URL(url, window.location.href); } catch { continue; }
      if (!['http:', 'https:'].includes(parsed.protocol)) continue;
      const link = node('a', label, 'text-button');
      link.href = url;
      if (downloads.childElementCount) downloads.append(' · ');
      downloads.append(link);
    }
    downloads.hidden = !downloads.childElementCount;
    $('#protocol-solver-note').hidden = !scheme.educational || !protocol.example_url;
  }
  const facts = $('#parameter-facts');
  const correctness = $('#correctness-facts');
  facts.replaceChildren();
  correctness.replaceChildren();
  $('#parameter-measurement-basis').hidden = true;
  if (!profile) return;
  const descriptor = scheme?.size_parameter;
  setText('#profile-label', 'Challenge tier');
  setText('#profile-note', tierNote(scheme, profile));
  addFact(facts, 'Challenge tier', `Tier ${instanceSize(profile)}`);
  const estimate = initialSecurityEstimate(scheme, profile);
  if (estimate !== null) {
    addFact(facts, 'Initial classical estimate', `≈${estimateBits(estimate)} bits`);
    const model = scheme.initial_security_model;
    addFact(facts, 'Reference model', typeof model.name === 'string' ? model.name : typeof model.label === 'string' ? model.label : model.id);
    const scope = [model.summary, model.scope].filter(value => typeof value === 'string' && value.trim()).join(' ');
    if (scope) addFact(facts, 'Estimate scope', scope);
  }
  setText('#protocol-size-caveat', 'An incorrect answer locks that instance. An expired instance without an accepted recovery is simply unsolved here; it is not evidence of a security bound. ' +
    (estimate === null ? 'A challenge tier selects a scheme-specific parameter set, not an estimate of security bits.' :
      'This tier selects parameters using a frozen initial classical reference. The estimate is not a measured security guarantee; actual attack costs may differ.'));
  addFact(facts, 'Profile', profile.name || profileId());
  if (descriptor?.mapping) addFact(facts, 'Parameter mapping', descriptor.mapping_math || descriptor.mapping, true);
  if (profile.parameter_label === 'subgroup-order bits') addFact(facts, 'Subgroup order size', `${profile.parameter_bits} bits`);
  if (profile.modulus_bits != null) addFact(facts, 'Modulus size', `${profile.modulus_bits} bits`);
  for (const [metric, label] of [['public_key_bytes', 'Public key'], ['ciphertext_bytes', 'Ciphertext']]) {
    const bytes = profileBytes(scheme, profile, metric);
    addFact(facts, label, bytes === null ? 'Not reported' : `${bytes.toLocaleString('en-US')} bytes`);
  }
  const basis = scheme?.profile_metrics_basis;
  const basisText = basis && typeof basis === 'object' ?
    [basis.encoding, typeof basis.excludes === 'string' && basis.excludes ? 'Excludes ' + basis.excludes.charAt(0).toLowerCase() + basis.excludes.slice(1) : null, basis.notes].filter(value => typeof value === 'string' && value).join(' ') :
    'Sizes count the canonical encoded bytes, excluding JSON and shared domain parameters.';
  setText('#parameter-measurement-basis', basisText);
  $('#parameter-measurement-basis').hidden = false;
  addFact(facts, 'Shared-key output', profile.shared_secret_bytes == null ? null : `${profile.shared_secret_bytes} bytes`);
  setText('#parameter-json', JSON.stringify({algorithms: profile.algorithms, parameters: profile.parameters, encoding: profile.encoding, kdf: profile.kdf}, null, 2));
  const claim = profile.correctness || {};
  addFact(correctness, 'Claim type', claim.kind);
  addFact(correctness, 'Failure bound', claim.failure_bound);
  addFact(correctness, 'Argument', scheme?.protocol?.correctness_argument_math || claim.argument, true);
  addFact(correctness, 'Scope', claim.scope);
  addFact(correctness, 'Evidence status', claim.evidence_status);
}
function renderChallenge() {
  $('#open-submission').hidden = false;
  $('#open-submission').disabled = !state.ready || state.submitBusy || state.importBusy;
  $('#challenge-loading').hidden = !state.challengeLoading;
  $('#challenge-empty').hidden = state.challengeLoading || Boolean(state.challenge);
  $('#challenge-active').hidden = state.challengeLoading || !state.challenge;
  $('#issue-button').disabled = !state.ready || !state.profile || state.issueBusy || roundRemaining() === 0;
  if (!state.challenge) {
    const current = state.schemes.find(scheme => scheme.id === state.schemeId);
    if (current && String(current.version) !== state.schemeVersion) {
      setText('#empty-title', 'Archived release');
      setText('#empty-description', 'Existing recoveries remain available here. New instances use the current release.');
      setText('#issue-button', 'Open current challenges →');
      return;
    }
    setText('#empty-title', state.user ? 'Ready?' : 'A fresh instance, just for you.');
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
  $('#download-button').href = '/api/challenges/' + encodeURIComponent(challenge.id) + '/download';
  $('#download-button').download = 'crucible-' + challenge.profile_id + '-' + challenge.round_id + '.json';
  const resultNode = $('#challenge-result');
  resultNode.hidden = !result && !expired && keyBytes !== null;
  resultNode.className = 'notice' + (result === 'incorrect' || (!result && !expired && keyBytes === null) ? ' notice-error' : '');
  resultNode.textContent = result === 'correct' ? 'Key recovered. The server accepted your answer. Your method and compute declaration are attached to this recovery.' :
    result === 'incorrect' ? 'The submitted key did not match. This instance is locked. You can try a different profile or return for the next round.' :
      expired ? 'This round has ended without an accepted recovery for this instance.' :
        keyBytes === null ? 'This instance does not declare a supported shared-key output length. Submission is unavailable; refresh the instance or report the problem.' : '';
}
function currentChallengeRelease() {
  return state.screen === 'scheme' && state.schemes.some(scheme =>
    scheme.id === state.schemeId && String(scheme.version) === state.schemeVersion);
}
function challengeAccessScope() {
  return JSON.stringify([state.user?.id, state.schemeId, state.schemeVersion, state.round?.id]);
}
function challengeAccessAttempt() { return JSON.stringify([challengeAccessScope(), profileId()]); }
function confirmedRoundAccess(data) {
  return data?.round_started === true && Number.isSafeInteger(data.round_started_round_id) &&
    data.round_started_round_id === data.round?.id && data.round_started_round_id === state.round?.id &&
    roundRemaining() > 0;
}
function showAutomaticPause() {
  const message = state.autoIssuePaused.get(challengeAccessScope());
  if (!message) return;
  setText('#challenge-error-text', message);
  $('#challenge-error').hidden = false;
}
async function loadChallenge({retry = false} = {}) {
  const request = ++state.challengeRequest;
  const selected = profileId();
  const selection = selectionKey();
  const accountId = state.user?.id;
  const stillSelected = () => request === state.challengeRequest &&
    state.user?.id === accountId && selectionKey() === selection;
  if (retry) {
    state.autoIssueAttempts.delete(challengeAccessAttempt());
    state.autoIssuePaused.delete(challengeAccessScope());
  }
  $('#challenge-error').hidden = true;
  state.challenge = null;
  if (state.screen !== 'scheme' || !state.user || !selected) {
    state.challengeLoading = false;
    renderChallenge();
    return;
  }
  state.challengeLoading = true;
  renderChallenge();
  let access;
  try {
    const data = await api('/api/challenges?' + new URLSearchParams({profile_id: selected, scheme_id: state.schemeId, scheme_version: state.schemeVersion}));
    if (!stillSelected() || data.round?.id !== state.round?.id) return;
    state.challenge = data.challenge || null;
    access = data;
  } catch (error) {
    if (!stillSelected()) return;
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally {
    if (stillSelected()) {
      state.challengeLoading = false;
      renderChallenge();
    }
  }
  if (stillSelected() && !state.challenge && currentChallengeRelease() &&
      state.view === 'challenge' && confirmedRoundAccess(access)) {
    if (state.autoIssuePaused.has(challengeAccessScope())) showAutomaticPause();
    else if (!state.issueBusy) await issueChallenge({activeRoundId: access.round_started_round_id});
  }
}
async function issueChallenge({activeRoundId} = {}) {
  const automatic = activeRoundId !== undefined;
  const current = state.schemes.find(scheme => scheme.id === state.schemeId);
  if (current && String(current.version) !== state.schemeVersion) {
    if (automatic) return;
    const profile = current.profiles.find(item => profileId(item) === current.next_profile_id) || current.profiles[0];
    return navigate(schemeRoute(current, profile, 'challenge'));
  }
  if (!state.user) return automatic ? undefined : openAuth($('#issue-button'));
  if (state.issueBusy || !state.profile || state.screen !== 'scheme') return;
  const scope = challengeAccessScope();
  const attempt = challengeAccessAttempt();
  if (automatic && (state.view !== 'challenge' || !currentChallengeRelease() ||
      activeRoundId !== state.round?.id || roundRemaining() <= 0 || state.challenge ||
      state.autoIssueAttempts.has(attempt) || state.autoIssuePaused.has(scope))) return;
  if (!automatic) state.autoIssuePaused.delete(scope);
  // Retain attempted tiers even after an uncertain response; only an explicit
  // retry can issue that tier again. The server also reuses existing instances.
  state.autoIssueAttempts.add(attempt);
  state.issueBusy = true;
  const selected = profileId();
  const selection = selectionKey();
  const accountId = state.user.id;
  const roundId = state.round?.id;
  const request = ++state.challengeRequest;
  const stillSelected = () => request === state.challengeRequest &&
    state.user?.id === accountId && selectionKey() === selection;
  state.challengeLoading = automatic;
  $('#challenge-error').hidden = true;
  renderChallenge();
  let completed = false;
  try {
    const data = await api('/api/challenges', {profile_id: selected, scheme_id: state.schemeId, scheme_version: state.schemeVersion,
      ...(automatic ? {active_round_id: activeRoundId} : {})});
    completed = confirmedRoundAccess(data);
    if (!stillSelected()) return;
    state.challenge = data.challenge?.round_id === state.round?.id ? data.challenge : null;
    if (state.challenge && !automatic) toast('Your instance is ready. The shared round deadline applies.');
  } catch (error) {
    if (state.user?.id === accountId) state.autoIssuePaused.set(scope, error.message);
    if (!stillSelected()) return;
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally {
    state.issueBusy = false;
    if (stillSelected()) state.challengeLoading = false;
    renderChallenge();
    // Navigation can finish a GET while the previous POST is still pending.
    // Re-read only the final selection, with its own account/scheme authority.
    if (state.user && state.round?.id === roundId &&
        state.view === 'challenge' && currentChallengeRelease() && !state.challenge) {
      if ((completed || state.user.id !== accountId) && request !== state.challengeRequest) await loadChallenge();
      else showAutomaticPause();
    }
  }
}

async function loadLeaderboard() {
  const target = $('#leaderboard-content');
  const scope = 'The first ten distinct accounts to recover this tier or a higher one, across all rounds. Ordered by server receipt.';
  setText('#leaderboard-title', 'Recoveries');
  $('#leaderboard-scope').hidden = true;
  $('#leaderboard-footnote').hidden = true;
  setText('#leaderboard-scope', scope);
  const request = ++state.leaderboardRequest;
  const selected = profileId();
  if (state.screen !== 'scheme' || !selected) return showEmpty(target, 'Choose an available tier to see its recoveries.');
  setLoading(target, 'Loading the first recoveries…');
  $('#refresh-leaderboard').disabled = true;
  try {
    const data = await api('/api/leaderboard?' + new URLSearchParams({profile_id: selected, scheme_id: state.schemeId, scheme_version: state.schemeVersion}));
    if (request !== state.leaderboardRequest) return;
    if (data.includes_compatible_versions?.length) setText('#leaderboard-scope', scope + ' Includes identical parameters from version ' + data.includes_compatible_versions.join(', ') + '; each record keeps its original release.');
    if (!data.entries?.length) {
      setText('#leaderboard-title', 'Unsolved');
      return showEmpty(target, 'No accepted recoveries have been recorded for this tier and version.');
    }
    setText('#leaderboard-title', 'Broken by:');
    $('#leaderboard-scope').hidden = false;
    $('#leaderboard-footnote').hidden = false;
    const wrap = node('div', undefined, 'leaderboard-wrap');
    const table = node('table', undefined, 'leaderboard-table');
    const caption = node('caption', 'First recoveries for Tier ' + instanceSize(state.profile), 'skip-link');
    table.append(caption);
    const head = node('thead');
    const headings = node('tr');
    for (const title of ['Rank', 'Researcher / method', 'Time to submit', 'Received (UTC)']) {
      const th = node('th', title); th.scope = 'col'; headings.append(th);
    }
    head.append(headings); table.append(head);
    const body = node('tbody');
    for (const entry of data.entries) {
      const row = node('tr', undefined, 'recovery-entry-row');
      row.append(node('td', String(entry.rank).padStart(2, '0')));
      const researcher = node('td');
      const button = node('button', undefined, 'recovery-entry-button');
      button.type = 'button';
      button.setAttribute('aria-label', 'View recovery by ' + entry.handle);
      button.setAttribute('aria-haspopup', 'dialog');
      button.append(node('span', entry.handle, 'leaderboard-handle'));
      if (entry.is_inherited) button.append(node('span', 'via tier ' + entry.instance_size, 'leaderboard-credit'));
      button.append(node('span', entry.method?.name || 'Method', 'leaderboard-method'));
      const recoveredSize = entry.instance_size ?? instanceSize(state.profile);
      const context = `${selectedScheme().name} · Tier ${recoveredSize} · version ${entry.scheme_version || state.schemeVersion}`;
      button.addEventListener('click', event => { event.stopPropagation?.(); openRecovery(entry, context, button); });
      row.addEventListener('click', () => openRecovery(entry, context, button));
      researcher.append(button);
      row.append(researcher, node('td', duration(entry.elapsed_seconds), 'mono'), node('td', utcDate(entry.received_at), 'mono receipt-cell'));
      body.append(row);
    }
    table.append(body); wrap.append(table); target.replaceChildren(wrap);
  } catch (error) {
    if (request === state.leaderboardRequest) showInlineError(target, error.message, loadLeaderboard);
  } finally { if (request === state.leaderboardRequest) $('#refresh-leaderboard').disabled = false; }
}
function renderRecovery(entry, context) {
  const incorrect = entry.status === 'incorrect';
  setText('#recovery-eyebrow', incorrect ? 'Recorded submission' : 'Recorded recovery');
  setText('#recovery-title', entry.method?.name || 'Recovery details');
  setText('#recovery-context', entry.handle + ' · ' + context);
  const content = $('#recovery-content');
  const facts = node('dl', undefined, 'detail-list');
  const compute = entry.compute || {};
  addFact(facts, 'Result', incorrect ? 'Incorrect' : 'Recovered');
  addFact(facts, 'Answer received', utcDate(entry.received_at));
  addFact(facts, 'Time to submit', duration(entry.elapsed_seconds) + ' from issue to receipt, including delays');
  addFact(facts, 'Reported attack time', compute.wall_seconds == null ? null : duration(compute.wall_seconds));
  addFact(facts, 'Hardware', compute.hardware);
  addFact(facts, 'Parallel workers', compute.workers);
  addFact(facts, 'Precomputation', compute.precomputation);
  addFact(facts, 'Peak memory', compute.peak_memory);
  if (compute.notes) addFact(facts, 'Notes', compute.notes);
  content.replaceChildren(node('pre', entry.method?.pseudocode || 'No pseudocode recorded.', 'code-block'), facts,
    node('p', 'Method and resources are contributor-reported. The server checks the submitted key, not the accuracy of these claims.', 'panel-footnote'));
}
function openRecovery(entry, context, trigger) {
  state.recoveryRequest++;
  renderRecovery(entry, context);
  if (!recoveryDialog.open) openDialog(recoveryDialog, trigger);
}
async function openSubmissionDetails(id, trigger) {
  if (!id) return;
  const request = ++state.recoveryRequest;
  const accountId = state.user?.id;
  const current = () => request === state.recoveryRequest && recoveryDialog.open && state.user?.id === accountId;
  setText('#recovery-eyebrow', 'Recorded submission');
  setText('#recovery-title', 'Submission details');
  setText('#recovery-context', '');
  setLoading($('#recovery-content'), 'Loading the recorded method and compute…');
  if (!recoveryDialog.open) openDialog(recoveryDialog, trigger);
  try {
    const data = await api('/api/submissions/' + encodeURIComponent(id));
    if (!current()) return;
    const entry = data.submission;
    if (!entry || entry.id !== id || !['correct', 'incorrect'].includes(entry.status) ||
        typeof entry.handle !== 'string' || !entry.method || typeof entry.method !== 'object' || Array.isArray(entry.method) ||
        !entry.compute || typeof entry.compute !== 'object' || Array.isArray(entry.compute) ||
        ![entry.scheme_id, entry.scheme_version, entry.profile_id].every(value => typeof value === 'string' && value)) {
      throw new Error('The submission details could not be loaded.');
    }
    const scheme = availableReleases().find(release => release.id === entry.scheme_id && String(release.version) === String(entry.scheme_version));
    const name = scheme?.name || entry.scheme_id;
    const tier = Number.isSafeInteger(entry.instance_size) ? 'Tier ' + entry.instance_size : entry.profile_id;
    const context = `${name} · ${tier} · version ${entry.scheme_version}`;
    renderRecovery(entry, context);
  } catch (error) {
    if (current()) showInlineError($('#recovery-content'), error.message, () => openSubmissionDetails(id, trigger));
  }
}
function renderView(view, focus = false) {
  state.view = view;
  for (const tab of document.querySelectorAll('[data-view]')) {
    const selected = tab.dataset.view === view;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $('#panel-' + tab.dataset.view).hidden = !selected;
    if (selected && focus) tab.focus();
  }
}
function activateView(view, focus = false) {
  const scheme = selectedScheme();
  if (!scheme || !state.profile) return;
  navigate(schemeRoute(scheme, state.profile, view));
  if (focus) $('#tab-' + view).focus();
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
    document.body.style.overflow = [...document.querySelectorAll('dialog')].some(item => item.open) ? 'hidden' : '';
    const trigger = dialogTriggers.get(dialog);
    const parentDialog = trigger?.closest('dialog');
    if (trigger?.isConnected && !trigger.hidden && !trigger.disabled &&
        !trigger.closest('[hidden]') && (!parentDialog || parentDialog.open)) trigger.focus();
    if (dialog === historyDialog) {
      state.historyRequest++;
      $('#history-content').replaceChildren();
    }
    if (dialog === recoveryDialog) {
      state.recoveryRequest++;
      $('#recovery-content').replaceChildren();
      setText('#recovery-context', '');
      setText('#recovery-title', 'Submission details');
    }
    if (dialog === authDialog) $('#auth-password').value = '';
    if (dialog === submissionDialog && !state.pendingAttempt && !state.submitBusy) $('#candidate').value = '';
    if (dialog === submissionDialog) {
      state.recoveryImportRequest++;
      state.importBusy = false;
      lockSubmissionFields(Boolean(state.pendingAttempt || state.submitBusy));
      updateRecoveryControls();
    }
  });
}
function setAuthMode(mode) {
  state.authMode = mode;
  $('#login-mode').classList.toggle('selected', mode === 'login');
  $('#register-mode').classList.toggle('selected', mode === 'register');
  $('#login-mode').setAttribute('aria-pressed', String(mode === 'login'));
  $('#register-mode').setAttribute('aria-pressed', String(mode === 'register'));
  setText('#auth-title', mode === 'login' ? 'Welcome back.' : 'Join the challenge.');
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
    if (state.screen === 'scheme' && state.view === 'leaderboard') await loadLeaderboard();
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
    if (state.screen === 'scheme' && state.view === 'leaderboard') loadLeaderboard();
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
function updateRecoveryControls() {
  const disabled = !submissionDialog.open || !state.user || state.importBusy || state.submitBusy || Boolean(state.pendingAttempt);
  $('#upload-recovery').disabled = disabled;
  $('#recovery-file').disabled = disabled;
  $('#download-recovery-template').disabled = disabled || !canSubmitChallenge(state.submissionChallenge);
  setText('#upload-recovery', state.importBusy ? 'Reading file…' : 'Upload result file');
}
async function importRecoveryFile(file) {
  if (!submissionDialog.open || !state.user) return;
  if (state.submitBusy || state.importBusy || state.pendingAttempt) {
    return setError('#recovery-file-error', 'Finish the pending submission before importing another result.');
  }
  setError('#recovery-file-error', '');
  if (!file || file.size > RECOVERY_FILE_MAX_BYTES) return setError('#recovery-file-error', 'Choose a JSON file no larger than 128 KB.');
  const previousChallengeId = state.submissionChallenge?.id;
  const selection = selectionKey();
  const screen = state.screen;
  const accountId = state.user?.id;
  const request = ++state.recoveryImportRequest;
  const current = () => request === state.recoveryImportRequest && submissionDialog.open && state.user?.id === accountId &&
    state.submissionChallenge?.id === previousChallengeId && selectionKey() === selection && state.screen === screen;
  state.importBusy = true;
  lockSubmissionFields(true);
  updateClock();
  try {
    const text = await file.text();
    if (!current()) return;
    const document = readRecoveryDocument(text);
    // Resolve the exact owned instance, even when it matches the displayed one:
    // another tab may already have submitted it. The final POST rechecks all rules.
    const downloaded = await api('/api/challenges/' + encodeURIComponent(document.challenge_id) + '/download');
    if (!current()) return;
    const challenge = downloaded.challenge;
    if (!challenge || challenge.id !== document.challenge_id) throw new Error('The server did not return the instance named in this file.');
    if (!canSubmitChallenge(challenge)) throw new Error('This file’s instance has expired, already has a final answer, or cannot accept a key.');
    if (state.pendingAttempt || state.submitBusy) throw new Error('Finish the pending submission before importing another result.');
    const target = submissionTarget(challenge);
    if (!target.scheme || !target.profile) throw new Error('This instance’s scheme, release, or tier is unavailable. Refresh the page and try again.');
    const result = parseRecoveryFile(text, challenge);
    // Commit target and draft together, only after the whole file is valid.
    state.submissionChallenge = challenge;
    renderSubmissionTarget();
    renderMethodOptions('new');
    const fields = {
      '#candidate': result.candidate, '#method-name': result.method.name,
      '#method-pseudocode': result.method.pseudocode, '#hardware': result.compute.hardware,
      '#workers': result.compute.workers, '#wall-seconds': result.compute.wall_seconds,
      '#precomputation': result.compute.precomputation, '#peak-memory': result.compute.peak_memory,
      '#compute-notes': result.compute.notes
    };
    for (const [selector, value] of Object.entries(fields)) $(selector).value = String(value);
    state.submissionFormRevision++;
    setError('#submission-error', '');
    setText('#recovery-file-status', `Imported ${file.name || 'result file'} for ${submissionTargetLabel(challenge)}. Review the filled fields, then submit your answer.`);
    $('#recovery-file-status').hidden = false;
    navigate(schemeRoute(target.scheme, target.profile, state.view));
  } catch (error) {
    if (current()) setError('#recovery-file-error', error.message || 'The file could not be read. Try downloading and completing the template.');
  } finally {
    if (request === state.recoveryImportRequest) {
      state.importBusy = false;
      lockSubmissionFields(Boolean(state.pendingAttempt || state.submitBusy || !state.submissionChallenge));
      updateClock();
    }
  }
}
function downloadRecoveryTemplate() {
  if (!state.submissionChallenge) return;
  const blob = new Blob([JSON.stringify(recoveryTemplate(state.submissionChallenge), null, 2) + '\n'], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = node('a');
  link.href = url;
  link.download = 'crucible-result-' + state.submissionChallenge.id + '.json';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function lockSubmissionFields(lock) {
  for (const field of $('#submission-form').querySelectorAll('input, textarea, select')) field.disabled = lock;
}
function submissionTarget(challenge) {
  const scheme = availableReleases().find(release => release.id === challenge?.scheme_id &&
    String(release.version) === String(challenge?.scheme_version));
  return {scheme, profile: scheme?.profiles?.find(profile => profileId(profile) === challenge?.profile_id)};
}
function submissionTargetLabel(challenge) {
  const {scheme, profile} = submissionTarget(challenge);
  return scheme && profile ? `${scheme.name} (Tier ${instanceSize(profile)})` : challenge?.profile_id || 'Choose an instance';
}
function renderSubmissionTarget() {
  const challenge = state.submissionChallenge;
  $('#submission-manual-fields').hidden = !challenge;
  $('#submission-target-hint').hidden = Boolean(challenge);
  setText('#submission-context', submissionTargetLabel(challenge));
  if (!challenge) return;
  const keyBytes = sharedKeyBytes(challenge);
  if (keyBytes === null) return;
  const hexLength = keyBytes * 2;
  const candidate = $('#candidate');
  candidate.minLength = hexLength;
  candidate.maxLength = hexLength;
  candidate.pattern = `[0-9a-fA-F]{${hexLength}}`;
  candidate.title = `Enter exactly ${hexLength} hexadecimal characters, without spaces or a 0x prefix.`;
  candidate.placeholder = `${hexLength} hexadecimal characters`;
  setText('#candidate-hint', `The complete ${keyBytes}-byte key. No spaces or 0x prefix.`);
}
async function openSubmission() {
  if (!state.user) return openAuth($('#open-submission'));
  if (state.submitBusy || state.importBusy) return;
  // An uncertain answer must keep its original target and retry identifier.
  const challenge = state.pendingAttempt ? state.submissionChallenge : isChallengeOpen() ? state.challenge : null;
  const accountId = state.user?.id;
  const sameChallenge = state.submissionChallenge?.id === challenge?.id;
  if (!sameChallenge) {
    $('#submission-form').reset();
    state.pendingAttempt = null;
    lockSubmissionFields(false);
    state.submissionFormRevision++;
    setError('#recovery-file-error', '');
    $('#recovery-file-status').hidden = true;
  }
  state.submissionChallenge = challenge;
  renderSubmissionTarget();
  lockSubmissionFields(Boolean(state.pendingAttempt || !challenge));
  setError('#submission-error', state.pendingAttempt ? 'The previous request has no confirmed result. Retry to check the same answer; it will not create a second attempt.' : '');
  setText('#submit-answer', state.pendingAttempt ? 'Retry the same answer →' : 'Submit this answer →');
  openDialog(submissionDialog, $('#open-submission'));
  updateClock();
  const formRevision = state.submissionFormRevision;
  try {
    const data = await api('/api/methods');
    if (state.user?.id !== accountId || state.submissionChallenge?.id !== challenge?.id) return;
    state.methods = data.methods || [];
    if (state.submissionFormRevision !== formRevision) return;
    const selected = $('#method-select').value;
    renderMethodOptions(selected);
  } catch (error) {
    if (state.user?.id === accountId && state.submissionChallenge?.id === challenge?.id && state.submissionFormRevision === formRevision) {
      setError('#submission-error', 'Saved methods could not be loaded. ' + error.message);
    }
  }
}
$('#submission-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.submitBusy || state.importBusy || !state.submissionChallenge || (now() >= state.submissionChallenge.ends_at && !state.pendingAttempt)) return;
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
  const request = ++state.historyRequest;
  const accountId = state.user?.id;
  const current = () => request === state.historyRequest && historyDialog.open && state.user?.id === accountId;
  openDialog(historyDialog, $('#history-button'));
  const target = $('#history-content');
  setLoading(target, 'Loading your submissions…');
  try {
    const data = await api('/api/me/submissions');
    if (!current()) return;
    if (!data.submissions?.length) return showEmpty(target, 'No submissions yet. Your received answers will appear here.');
    const items = [];
    for (const submission of data.submissions) {
      const item = node('button', undefined, 'history-item');
      item.type = 'button';
      item.setAttribute('aria-haspopup', 'dialog');
      const copy = node('span', undefined, 'history-item-copy');
      const scheme = availableReleases().find(release => release.id === submission.scheme_id &&
        String(release.version) === String(submission.scheme_version));
      const profile = scheme?.profiles?.find(profile => profileId(profile) === submission.profile_id);
      const name = scheme?.name || state.schemes.find(release => release.id === submission.scheme_id)?.name || submission.scheme_id;
      const tier = instanceSize(profile);
      const title = `${name || 'Scheme'} (${tier == null ? submission.profile_id : 'Tier ' + tier})`;
      copy.append(node('span', title, 'history-item-title'), node('span', submission.method_name || 'Submitted method', 'history-item-meta'), node('span', utcDate(submission.received_at), 'history-item-meta'));
      item.setAttribute('aria-label', 'View submission for ' + title + ' received ' + utcDate(submission.received_at));
      item.addEventListener('click', () => openSubmissionDetails(submission.id, item));
      item.append(copy, node('span', submission.status === 'correct' ? 'Recovered' : 'Incorrect', 'status-tag' + (submission.status === 'correct' ? '' : ' error-tag')));
      items.push(item);
    }
    target.replaceChildren(...items);
  } catch (error) {
    if (current()) showInlineError(target, error.message, async () => { historyDialog.close(); await showHistory(); });
  }
}
async function copyInstance() {
  if (!state.challenge) return;
  const id = state.challenge.id;
  const accountId = state.user?.id;
  $('#copy-instance').disabled = true;
  try {
    // Copy and Download must use the same authoritative challenge contract.
    const contract = await api('/api/challenges/' + encodeURIComponent(id) + '/download');
    if (state.challenge?.id !== id || state.user?.id !== accountId) return;
    const payload = JSON.stringify(contract, null, 2);
    try { await navigator.clipboard.writeText(payload); toast('Challenge JSON copied. It contains the same information as the download.'); }
    catch {
      toast('Clipboard access is unavailable. Use Download challenge JSON to save the same information.');
    }
  } catch (error) { toast('The challenge could not be copied. ' + error.message); }
  finally { $('#copy-instance').disabled = false; }
}

function chooseInstanceSize() {
  const scheme = selectedScheme();
  if (!$('#profile-select').value.trim()) {
    setError('#profile-error', '');
    return;
  }
  const size = Number($('#profile-select').value);
  const profile = Number.isInteger(size) && scheme?.profiles?.find(item => instanceSize(item) === size);
  if (profile) {
    setError('#profile-error', '');
    // A blur after Enter can emit change again. Keep the current buttons in
    // place so that a pointer or keyboard focus moving to the rail is retained.
    if (profileId(profile) === profileId()) {
      $('#profile-select').value = '';
      return;
    }
    navigate(schemeRoute(scheme, profile, state.view));
  }
  else setError('#profile-error', `Choose a published integer tier from ${$('#profile-select').min} to ${$('#profile-select').max}.`);
}
function stepInstanceSize(direction) {
  const scheme = selectedScheme();
  const profiles = [...(scheme?.profiles || [])].sort((a, b) => instanceSize(a) - instanceSize(b));
  const index = profiles.findIndex(profile => profileId(profile) === profileId());
  const profile = profiles[index + direction];
  if (profile) navigate(schemeRoute(scheme, profile, state.view));
}
function sliderProfile() {
  const profiles = [...(selectedScheme()?.profiles || [])].sort((a, b) => instanceSize(a) - instanceSize(b));
  const index = Number($('#size-slider').value);
  return Number.isInteger(index) ? profiles[index] : null;
}
$('#size-slider').addEventListener('input', () => {
  const profile = sliderProfile();
  if (!profile) return;
  renderProfiles(profile);
});
$('#size-slider').addEventListener('pointercancel', () => renderProfiles());
$('#size-slider').addEventListener('change', () => {
  const profile = sliderProfile();
  if (!profile) return renderProfiles();
  navigate(schemeRoute(selectedScheme(), profile, state.view));
});
$('#profile-select').addEventListener('change', chooseInstanceSize);
$('#profile-select').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); chooseInstanceSize(); }
});
$('#jump-size').addEventListener('click', chooseInstanceSize);
$('#previous-size').addEventListener('click', () => stepInstanceSize(-1));
$('#next-size').addEventListener('click', () => stepInstanceSize(1));
$('#advance-size').addEventListener('click', () => {
  const scheme = selectedScheme();
  const frontier = scheme?.profiles?.find(profile => profileId(profile) === scheme.next_profile_id);
  if (frontier) navigate(schemeRoute(scheme, frontier, state.view));
});
$('#issue-button').addEventListener('click', () => issueChallenge());
$('#retry-challenge').addEventListener('click', () => loadChallenge({retry: true}));
$('#auth-button').addEventListener('click', () => openAuth($('#auth-button')));
$('#login-mode').addEventListener('click', () => setAuthMode('login'));
$('#register-mode').addEventListener('click', () => setAuthMode('register'));
$('#logout-button').addEventListener('click', logout);
$('#open-submission').addEventListener('click', openSubmission);
$('#upload-recovery').addEventListener('click', () => $('#recovery-file').click());
$('#download-recovery-template').addEventListener('click', downloadRecoveryTemplate);
$('#recovery-file').addEventListener('change', async () => {
  const file = $('#recovery-file').files?.[0];
  $('#recovery-file').value = '';
  if (file) await importRecoveryFile(file);
});
$('#copy-instance').addEventListener('click', copyInstance);
$('#method-select').addEventListener('change', updateMethodFields);
$('#refresh-leaderboard').addEventListener('click', loadLeaderboard);
$('#history-button').addEventListener('click', showHistory);
$('#retry-button').addEventListener('click', () => state.ready ? synchronizeSession() : initialize());
$('#refresh-catalogue').addEventListener('click', async () => {
  $('#refresh-catalogue').disabled = true;
  try { await refreshProfiles(); globalError(''); }
  catch (error) { globalError(error.message); }
  finally { $('#refresh-catalogue').disabled = false; }
});
$('.skip-link').addEventListener('click', event => {
  event.preventDefault();
  const target = $(workspaceSelector(state.screen));
  target.focus();
  target.scrollIntoView();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) synchronizeSession(); });
window.addEventListener('online', synchronizeSession);
window.addEventListener('hashchange', () => applyRoute(true));


// A proposal is a description for review. No uploaded instructions or links run.
const SPECIFICATION_LABELS = {
  setup: 'Setup', keygen: 'Key generation', encapsulate: 'Encapsulation', decapsulate: 'Decapsulation',
  tier_meaning: 'Meaning of a tier', supported_tiers: 'Supported tiers', mapping: 'Parameter mapping',
  distribution: 'Sampling distribution', reuse: 'Parameter reuse', public_key: 'Public key',
  ciphertext: 'Ciphertext', shared_secret: 'Shared secret', shared_secret_bytes: 'Shared-secret bytes',
  kind: 'Claim type', failure_bound: 'Failure bound', argument: 'Argument', scope: 'Scope', evidence: 'Evidence',
  notion: 'Security notion', model: 'Attack model', claim: 'Claim', assumptions: 'Assumptions',
  url: 'URL', sha256: 'SHA-256', runtime: 'Runtime', entrypoint: 'Entry point'
};
function proposalError(error) {
  setError('#proposal-error', error?.message || '');
  const list = $('#proposal-field-errors');
  list.replaceChildren();
  for (const item of (error?.fieldErrors || []).slice(0, 30)) list.append(node('li', item.path + ': ' + item.message));
  list.hidden = !list.childElementCount;
}
function proposalStatus(message) {
  setText('#proposal-status', message);
  $('#proposal-status').hidden = !message;
}
function validSpecification(spec) {
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  return object(spec) && spec.format === 'crucible-kem-spec-v1' &&
    typeof spec.name === 'string' && typeof spec.version === 'string' && typeof spec.summary === 'string' &&
    ['algorithms', 'parameters', 'encoding', 'correctness', 'security'].every(key => object(spec[key])) &&
    Array.isArray(spec.references);
}
function validProposal(record, id = null) {
  return record && typeof record.id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(record.id) &&
    (!id || record.id === id) && ['submitted', 'changes_requested', 'approved'].includes(record.status) &&
    Number.isSafeInteger(record.revision) && record.revision > 0 && Number.isFinite(record.created_at) &&
    typeof record.content_sha256 === 'string' && /^[a-f0-9]{64}$/.test(record.content_sha256) && validSpecification(record.specification);
}
function proposalStatusLabel(status) {
  return {submitted: 'Review pending', changes_requested: 'Changes requested', approved: 'Approved for trusted integration'}[status] || status;
}
function previewValue(key, value) {
  if (value === null || value === undefined) return key === 'shared_secret_bytes' ? 'See shared-secret encoding' : 'Not provided';
  if (key === 'supported_tiers' && value && typeof value === 'object') {
    if (value.kind === 'range') return `${value.min}–${value.max} · step ${value.step}`;
    if (value.kind === 'list' && Array.isArray(value.values)) return value.values.join(', ');
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function renderSpecification(target, spec) {
  target.replaceChildren(node('h2', spec.name), node('p', 'Version ' + spec.version, 'proposal-version'), node('p', spec.summary, 'proposal-summary'));
  for (const [key, label] of [['parameters', 'Parameters'], ['algorithms', 'Algorithms'], ['encoding', 'Encoding'],
    ['correctness', 'Correctness claim'], ['security', 'Security claim'], ['implementation', 'Implementation']]) {
    const section = node('details', undefined, 'proposal-section');
    section.open = key === 'parameters';
    section.append(node('summary', label));
    const value = spec[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const facts = node('dl', undefined, 'detail-list proposal-facts');
      for (const [field, content] of Object.entries(value)) addFact(facts, SPECIFICATION_LABELS[field] || field, previewValue(field, content));
      section.append(facts);
    } else section.append(node('p', 'Not provided. Implementation review is required before activation.', 'field-hint'));
    target.append(section);
  }
  if (spec.references.length) {
    const section = node('details', undefined, 'proposal-section');
    section.append(node('summary', 'References'));
    const list = node('ul');
    for (const reference of spec.references) {
      if (reference && typeof reference.title === 'string' && typeof reference.url === 'string') list.append(node('li', reference.title + ' · ' + reference.url));
    }
    section.append(list);
    target.append(section);
  }
}
function renderProposalDraft() {
  const busy = proposals.validating || proposals.submitting;
  $('#upload-proposal').disabled = busy || Boolean(proposals.pending) || Boolean(proposals.revisingId && !proposals.revisionBase);
  $('#proposal-file').disabled = $('#upload-proposal').disabled;
  $('#proposal-review').hidden = !proposals.specification || Boolean(proposals.receipt);
  $('#submit-proposal').disabled = busy || !proposals.specification;
  if (proposals.revisingId && !proposals.revisionBase) $('#submit-proposal').disabled = true;
  setText('#submit-proposal', proposals.submitting ? 'Submitting…' : !state.user ? 'Sign in to submit' : proposals.pending ? 'Retry submission' : proposals.revisingId ? 'Submit revised specification' : 'Submit proposal');
  $('#proposal-sign-in').hidden = Boolean(state.user);
  $('#refresh-proposals').hidden = !state.user || (proposals.reviewMode && !state.canReviewProposals);
  $('#proposal-review-link').hidden = !state.canReviewProposals;
  $('#proposal-review-link').href = proposals.reviewMode ? '#propose' : '#propose?review=1';
  setText('#proposal-review-link', proposals.reviewMode ? 'My proposals →' : 'Review proposals →');
  if (proposals.receipt) {
    proposalStatus('Submitted for review. ');
    const link = node('a', 'View your proposal', 'text-button');
    link.href = '#propose?proposal=' + encodeURIComponent(proposals.receipt.id);
    $('#proposal-status').append(link);
  }
}
function clearProposalDraft() {
  proposals.specification = null;
  proposals.digest = null;
  proposals.pending = null;
  proposals.receipt = null;
  $('#proposal-preview').replaceChildren();
  $('#proposal-file').value = '';
  proposalError(null);
  proposalStatus('');
}
function resetProposalAccount(previousId) {
  proposals.validationRequest++;
  proposals.submitRequest++;
  proposals.listRequest++;
  proposals.detailRequest++;
  proposals.reviewRequest++;
  proposals.validating = false;
  proposals.submitting = false;
  proposals.listUser = null;
  proposals.detailId = null;
  proposals.revisingId = null;
  proposals.revisionBase = null;
  proposals.reviewPending = null;
  proposals.reviewBusy = false;
  clearProposalReview();
  $('#proposal-list').replaceChildren();
  $('#proposal-detail-content').replaceChildren();
  // A completed anonymous preview can survive sign-in; another account's work cannot.
  if (previousId != null) clearProposalDraft();
  else if (!proposals.specification) proposalStatus('');
  renderProposalDraft();
  if (state.screen === 'propose' && state.ready) enterProposalPage(new URLSearchParams(window.location.hash.split('?')[1] || ''), true);
}
function leaveProposalPage() {
  proposals.validationRequest++;
  proposals.listRequest++;
  proposals.detailRequest++;
  if (proposals.validating) proposalStatus('Choose the file again to finish checking it.');
  proposals.validating = false;
  proposals.listUser = null;
  proposals.detailId = null;
  $('#proposal-detail-content').replaceChildren();
  $('#proposal-list').replaceChildren();
  clearProposalReview();
  renderProposalDraft();
}
function enterProposalPage(params, entering) {
  const id = params.get('proposal');
  const revise = id ? null : params.get('revise');
  const reviewMode = params.get('review') === '1';
  const modeChanged = proposals.reviewMode !== reviewMode;
  proposals.reviewMode = reviewMode;
  if (!id && !reviewMode && proposals.pending && proposals.pending.revisionId !== revise) {
    navigate(proposals.pending.revisionId ? '#propose?revise=' + encodeURIComponent(proposals.pending.revisionId) : '#propose');
    proposalError(new Error('Retry the pending submission before starting another proposal or revision.'));
    return;
  }
  if (!id && !reviewMode && proposals.revisingId !== revise) {
    clearProposalDraft();
    proposals.revisingId = revise;
    proposals.revisionBase = null;
  }
  if ((id || reviewMode || revise !== proposals.revisingId) && proposals.validating) {
    proposals.validationRequest++;
    proposals.validating = false;
    proposalStatus('Choose the file again to finish checking it.');
  }
  $('#proposal-draft').hidden = Boolean(id) || reviewMode;
  $('#proposal-detail').hidden = !id;
  $('#proposal-revision-context').hidden = !revise;
  setText('#proposal-title', revise ? 'Revise your proposal' : reviewMode ? 'Review proposals' : 'Propose a scheme');
  setText('#proposal-history-title', reviewMode ? 'Review queue' : 'My proposals');
  renderProposalDraft();
  const listKey = JSON.stringify([state.user?.id, reviewMode]);
  if (entering || modeChanged || proposals.listUser !== listKey) loadProposals();
  if (id && (entering || proposals.detailId !== id)) loadProposalDetail(id);
  else if (revise && (entering || !proposals.revisionBase)) loadProposalRevision(revise);
  else if (!id && proposals.detailId) {
    proposals.detailRequest++;
    proposals.detailId = null;
    $('#proposal-detail-content').replaceChildren();
    clearProposalReview();
  }
}
async function importProposal(file) {
  if (!file || proposals.submitting || proposals.pending || state.screen !== 'propose') return;
  const request = ++proposals.validationRequest;
  const account = state.user?.id;
  clearProposalDraft();
  proposals.validating = true;
  proposalStatus('Checking the specification format…');
  renderProposalDraft();
  const current = () => request === proposals.validationRequest && account === state.user?.id && state.screen === 'propose' && !proposals.detailId && !proposals.reviewMode;
  try {
    if (!Number.isFinite(file.size) || file.size > PROPOSAL_FILE_MAX_BYTES) throw new Error('Choose a JSON specification no larger than 48 KiB.');
    const text = await file.text();
    if (!current()) return;
    if (new TextEncoder().encode(text).length > PROPOSAL_FILE_MAX_BYTES) throw new Error('Choose a JSON specification no larger than 48 KiB.');
    let specification;
    try { specification = JSON.parse(text.replace(/^\uFEFF/, '')); }
    catch { throw new Error('This file is not valid JSON. Fill in the downloadable template without Markdown code fences.'); }
    if (!specification || typeof specification !== 'object' || Array.isArray(specification)) throw new Error('The specification must be a JSON object.');
    const payload = {specification};
    if (new TextEncoder().encode(JSON.stringify(payload)).length > 64 * 1024) throw new Error('The specification’s JSON request is too large. Shorten it and upload again.');
    const result = await api('/api/protocol/proposals/validate', payload);
    if (!current()) return;
    if (result.validation?.status !== 'format-valid' || !validSpecification(result.specification) ||
        typeof result.content_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(result.content_sha256)) throw new Error('The format check returned an incomplete result. Upload the file again.');
    proposals.specification = result.specification;
    proposals.digest = result.content_sha256;
    renderSpecification($('#proposal-preview'), result.specification);
    proposalStatus('Format checked. Review the specification below before submitting.');
  } catch (error) {
    if (!current()) return;
    proposalError(error);
    proposalStatus('');
  } finally {
    if (request === proposals.validationRequest) {
      proposals.validating = false;
      renderProposalDraft();
    }
  }
}
async function submitProposal() {
  if (!proposals.specification || proposals.validating || proposals.submitting || state.screen !== 'propose' || proposals.detailId) return;
  if (!state.user) return openAuth($('#submit-proposal'));
  const account = state.user.id;
  const request = ++proposals.submitRequest;
  if (proposals.revisingId && !proposals.revisionBase) return;
  proposals.pending ||= {
    revisionId: proposals.revisingId,
    path: proposals.revisingId ? '/api/protocol/proposals/' + encodeURIComponent(proposals.revisingId) + '/revisions' : '/api/protocol/proposals',
    payload: {specification: proposals.specification, idempotency_key: crypto.randomUUID(),
      ...(proposals.revisingId ? {expected_revision: proposals.revisionBase.revision, content_sha256: proposals.revisionBase.content_sha256} : {})}
  };
  const pending = proposals.pending;
  proposals.submitting = true;
  proposalError(null);
  proposalStatus('Submitting the reviewed specification…');
  renderProposalDraft();
  try {
    const result = await api(pending.path, pending.payload);
    if (request !== proposals.submitRequest || account !== state.user?.id) return;
    if (!validProposal(result.proposal, pending.revisionId) || result.proposal.content_sha256 !== proposals.digest ||
        (pending.revisionId && result.proposal.revision !== pending.payload.expected_revision + 1)) throw new Error('The submission receipt could not be confirmed.');
    proposals.receipt = result.proposal;
    if (pending.revisionId) {
      proposals.revisionBase = result.proposal;
      setText('#proposal-revision-context', `Revision ${result.proposal.revision} submitted. The earlier revision and reviews remain recorded.`);
    }
    proposals.pending = null;
    if (state.screen === 'propose') loadProposals();
  } catch (error) {
    if (request !== proposals.submitRequest || account !== state.user?.id) return;
    const uncertain = !error.status || error.status >= 500;
    if (!uncertain) proposals.pending = null;
    proposalError(error);
    proposalStatus(uncertain ? 'The submission could not be confirmed. Retry to recover the same receipt; a retry will not create a second proposal.' : 'The proposal was not submitted. Review the error, then try again.');
  } finally {
    if (request === proposals.submitRequest) {
      proposals.submitting = false;
      renderProposalDraft();
    }
  }
}
async function loadProposals(cursor = null) {
  const request = ++proposals.listRequest;
  const account = state.user?.id;
  const reviewing = proposals.reviewMode;
  const after = reviewing && typeof cursor === 'string' ? cursor : null;
  proposals.listUser = JSON.stringify([account, reviewing]);
  const target = $('#proposal-list');
  if (!account) { target.replaceChildren(); return; }
  if (reviewing && !state.canReviewProposals) return showEmpty(target, 'The review queue is available to configured reviewers.');
  if (!after) setLoading(target, reviewing ? 'Loading the review queue…' : 'Loading your proposals…');
  $('#refresh-proposals').disabled = true;
  const current = () => request === proposals.listRequest && account === state.user?.id && state.screen === 'propose' && proposals.reviewMode === reviewing;
  try {
    const result = await api(reviewing ? '/api/protocol/review-queue' + (after ? '?' + new URLSearchParams({after}) : '') : '/api/me/protocol-proposals');
    if (!current()) return;
    if (!Array.isArray(result.proposals) || result.proposals.some(record => !validProposal(record))) throw new Error('Your proposals could not be loaded.');
    if (!after) target.replaceChildren();
    if (!result.proposals.length && !after) return showEmpty(target, reviewing ? 'No proposals awaiting your review.' : 'No submitted proposals yet.');
    for (const record of result.proposals) {
      const link = node('a', undefined, 'proposal-list-entry');
      link.href = '#propose?' + (reviewing ? 'review=1&' : '') + 'proposal=' + encodeURIComponent(record.id);
      link.append(node('span', record.specification.name, 'proposal-list-name'), node('span', 'Revision ' + record.revision + ' · ' + proposalStatusLabel(record.status) + (reviewing && record.author?.handle ? ' · ' + record.author.handle : '') + ' · ' + utcDate(record.created_at), 'proposal-list-meta'));
      target.append(link);
    }
    if (reviewing && typeof result.next_after === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(result.next_after)) {
      const more = node('button', 'Load more proposals', 'text-button');
      more.type = 'button';
      more.addEventListener('click', async () => { more.hidden = true; await loadProposals(result.next_after); });
      target.append(more);
    }
  } catch (error) { if (current()) showInlineError(target, error.message, loadProposals); }
  finally { if (request === proposals.listRequest) $('#refresh-proposals').disabled = false; }
}
async function loadProposalDetail(id) {
  const request = ++proposals.detailRequest;
  const account = state.user?.id;
  proposals.detailId = id;
  const target = $('#proposal-detail-content');
  target.replaceChildren();
  clearProposalReview();
  if (!account) return showEmpty(target, 'Sign in to view your submitted proposal.');
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) return showEmpty(target, 'This proposal link is invalid.');
  setLoading(target, 'Loading your submitted specification…');
  const current = () => request === proposals.detailRequest && account === state.user?.id && state.screen === 'propose' && proposals.detailId === id;
  try {
    const result = await api('/api/protocol/proposals/' + encodeURIComponent(id));
    if (!current()) return;
    if (!validProposal(result.proposal, id)) throw new Error('The proposal details could not be loaded.');
    const record = result.proposal;
    proposals.detailRecord = record;
    renderSpecification(target, record.specification);
    target.prepend(node('p', 'Revision ' + record.revision + ' · ' + proposalStatusLabel(record.status) + ' · ' + utcDate(record.created_at), 'proposal-status'));
    target.append(node('p', record.status === 'approved' ? 'A manual reviewer approved this revision for trusted integration. Approval does not activate a live challenge or certify security.' : 'Format checked only. This submitted specification has not been approved or activated as a live challenge.', 'panel-footnote'),
      node('p', 'Content SHA-256 · ' + record.content_sha256, 'proposal-digest'));
    renderProposalAudit(target, result);
    const own = result.author?.id === account;
    $('#proposal-detail-actions').hidden = !own && !(state.canReviewProposals && result.author?.id);
    $('#revise-proposal').hidden = !own;
    $('#revise-proposal').href = '#propose?revise=' + encodeURIComponent(record.id);
    if (!own && result.author?.id && state.canReviewProposals && (record.status === 'submitted' || proposals.reviewPending?.id === record.id)) showProposalReview(record, true);
  } catch (error) { if (current()) showInlineError(target, error.message, () => loadProposalDetail(id)); }
}
async function loadProposalRevision(id) {
  const request = ++proposals.detailRequest;
  const account = state.user?.id;
  proposals.detailId = null;
  proposals.revisionBase = null;
  clearProposalReview();
  setText('#proposal-revision-context', 'Loading the revision to replace…');
  renderProposalDraft();
  const current = () => request === proposals.detailRequest && account === state.user?.id && state.screen === 'propose' && proposals.revisingId === id && !proposals.detailId;
  if (!account) { setText('#proposal-revision-context', 'Sign in to upload a revised specification.'); return; }
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) { setText('#proposal-revision-context', 'This proposal link is invalid.'); return; }
  try {
    const result = await api('/api/protocol/proposals/' + encodeURIComponent(id));
    if (!current()) return;
    if (!validProposal(result.proposal, id) || result.author?.id !== account) throw new Error('Only the proposer can upload a revised specification.');
    proposals.revisionBase = result.proposal;
    setText('#proposal-revision-context', `Replacing revision ${result.proposal.revision} of ${result.proposal.specification.name}. The earlier revision and reviews remain recorded. A new revision returns to review pending.`);
  } catch (error) { if (current()) { proposalError(error); setText('#proposal-revision-context', 'The revision could not be loaded. Reopen this proposal to retry.'); } }
  finally { if (current()) renderProposalDraft(); }
}
function renderProposalAudit(target, result) {
  const revisions = Array.isArray(result.revisions) ? result.revisions : [];
  const reviews = Array.isArray(result.reviews) ? result.reviews : [];
  if (!revisions.length && !reviews.length) return;
  const history = node('details', undefined, 'proposal-section');
  history.append(node('summary', 'Revision and review history'));
  history.open = result.proposal?.status === 'changes_requested';
  for (const revision of revisions) {
    if (!Number.isSafeInteger(revision.revision) || !validSpecification(revision.specification)) continue;
    const item = node('details', undefined, 'proposal-audit-entry');
    item.append(node('summary', 'Revision ' + revision.revision + ' · ' + utcDate(revision.created_at)));
    const content = node('div');
    renderSpecification(content, revision.specification);
    content.append(node('p', 'Content SHA-256 · ' + revision.content_sha256, 'proposal-digest'));
    item.append(content);
    history.append(item);
    for (const review of reviews.filter(review => review.revision === revision.revision)) {
      const note = node('div', undefined, 'proposal-audit-note');
      note.append(node('h4', proposalStatusLabel(review.decision) + (review.reviewer?.handle ? ' · ' + review.reviewer.handle : '')),
        node('p', typeof review.notes === 'string' ? review.notes : '', 'proposal-summary'),
        node('p', 'Evidence', 'eyebrow'), node('p', typeof review.evidence === 'string' ? review.evidence : '', 'proposal-summary'));
      history.append(note);
    }
  }
  if (Number.isSafeInteger(result.next_before_revision)) {
    const more = node('button', 'Load earlier revisions', 'text-button');
    more.type = 'button';
    more.addEventListener('click', async () => {
      const request = proposals.detailRequest;
      const account = state.user?.id;
      const id = proposals.detailId;
      more.disabled = true;
      try {
        const earlier = await api('/api/protocol/proposals/' + encodeURIComponent(id) + '/revisions?' + new URLSearchParams({before_revision: result.next_before_revision}));
        if (request !== proposals.detailRequest || account !== state.user?.id || state.screen !== 'propose' || proposals.detailId !== id) return;
        renderProposalAudit(history, earlier);
        more.hidden = true;
      } catch (error) { if (request === proposals.detailRequest && account === state.user?.id) { more.textContent = 'Try loading earlier revisions again'; more.title = error.message; } }
      finally { if (request === proposals.detailRequest) more.disabled = false; }
    });
    history.append(more);
  }
  target.append(history);
}
function clearProposalReview() {
  proposals.detailRecord = null;
  $('#proposal-detail-actions').hidden = true;
  $('#revise-proposal').hidden = true;
  $('#proposal-review-form').hidden = true;
  $('#proposal-review-form').reset();
  for (const input of document.querySelectorAll('[data-proposal-check]')) input.checked = false;
  setError('#proposal-review-error', '');
  setText('#proposal-review-artifact', '');
  setText('#proposal-review-status', '');
  $('#proposal-review-status').hidden = true;
}
function showProposalReview(record, initialize = false) {
  $('#proposal-review-form').hidden = false;
  const artifact = record.specification.implementation?.sha256;
  setText('#proposal-review-artifact', artifact ? 'Implementation SHA-256 · ' + artifact : 'Approval requires an implementation artifact with a SHA-256 identifier.');
  if (initialize) $('#proposal-decision').value = 'changes_requested';
  const pending = proposals.reviewPending;
  if (pending && pending.id === record.id) {
    $('#proposal-decision').value = pending.payload.decision;
    $('#proposal-review-notes').value = pending.payload.notes;
    $('#proposal-review-evidence').value = pending.payload.evidence;
    for (const input of document.querySelectorAll('[data-proposal-check]')) input.checked = pending.payload.checks[input.dataset.proposalCheck] === true;
    if (initialize) {
      setText('#proposal-review-status', `Retry the pending decision for revision ${pending.payload.expected_revision} to confirm its receipt. The displayed specification is revision ${record.revision}.`);
      $('#proposal-review-status').hidden = false;
    }
  }
  $('#proposal-review-fields').disabled = Boolean(pending) || proposals.reviewBusy;
  $('#submit-proposal-review').disabled = proposals.reviewBusy || Boolean(pending && pending.id !== record.id);
  setText('#submit-proposal-review', proposals.reviewBusy ? 'Recording review…' : pending?.id === record.id ? 'Retry review' : 'Record review');
  if (pending && pending.id !== record.id) {
    setText('#proposal-review-status', 'A previous review is awaiting confirmation. ');
    const link = node('a', 'Return to that proposal and retry.', 'text-button');
    link.href = '#propose?review=1&proposal=' + encodeURIComponent(pending.id);
    $('#proposal-review-status').append(link);
    $('#proposal-review-status').hidden = false;
  }
}
async function submitProposalReview(event) {
  event.preventDefault();
  const record = proposals.detailRecord;
  if (!record || proposals.reviewBusy || !state.canReviewProposals || $('#proposal-review-form').hidden || (proposals.reviewPending && proposals.reviewPending.id !== record.id)) return;
  const account = state.user?.id;
  const request = ++proposals.reviewRequest;
  const checks = Object.fromEntries([...document.querySelectorAll('[data-proposal-check]')].map(input => [input.dataset.proposalCheck, input.checked === true]));
  const decision = $('#proposal-decision').value;
  const notes = $('#proposal-review-notes').value.trim();
  const evidence = $('#proposal-review-evidence').value.trim();
  const implementation = record.specification.implementation?.sha256 || null;
  setError('#proposal-review-error', '');
  if (!proposals.reviewPending) {
    if (!['approved', 'changes_requested'].includes(decision) || !notes || !evidence) return setError('#proposal-review-error', 'Choose a decision and include notes and supporting evidence.');
    if (decision === 'approved' && (!Object.values(checks).every(Boolean) || !/^[a-f0-9]{64}$/.test(implementation || '') || [...evidence].length < 20 || record.specification.correctness.kind === 'unknown')) return setError('#proposal-review-error', 'Approval requires all five checks, an explicit correctness claim, an identified implementation artifact, and a documented reproduction record.');
    proposals.reviewPending = {id: record.id, payload: {
      expected_revision: record.revision, content_sha256: record.content_sha256, decision,
      implementation_sha256: implementation, checks, notes, evidence, idempotency_key: crypto.randomUUID()
    }};
  }
  const pending = proposals.reviewPending;
  proposals.reviewBusy = true;
  showProposalReview(record);
  setText('#proposal-review-status', 'Recording your manual review…');
  $('#proposal-review-status').hidden = false;
  try {
    const result = await api('/api/protocol/proposals/' + encodeURIComponent(pending.id) + '/reviews', pending.payload);
    if (request !== proposals.reviewRequest || account !== state.user?.id) return;
    if (!validProposal(result.proposal, pending.id) || result.review?.revision !== pending.payload.expected_revision ||
        result.review?.content_sha256 !== pending.payload.content_sha256 || result.review?.decision !== pending.payload.decision) throw new Error('The review receipt could not be confirmed.');
    proposals.reviewPending = null;
    if (state.screen === 'propose') {
      if (proposals.detailId === pending.id) loadProposalDetail(pending.id);
      loadProposals();
    }
  } catch (error) {
    if (request !== proposals.reviewRequest || account !== state.user?.id) return;
    const uncertain = !error.status || error.status >= 500;
    if (!uncertain) proposals.reviewPending = null;
    if (state.screen === 'propose' && proposals.detailId === pending.id) {
      setError('#proposal-review-error', error.message + (error.fieldErrors?.length ? ' ' + error.fieldErrors.map(item => item.path + ': ' + item.message).join(' ') : ''));
      setText('#proposal-review-status', uncertain ? 'The review could not be confirmed. Retry to recover the same decision receipt.' : 'The review was not recorded. Reopen the proposal if its revision has changed.');
      $('#proposal-review-status').hidden = false;
    }
  } finally {
    if (request === proposals.reviewRequest) {
      proposals.reviewBusy = false;
      if (state.screen === 'propose' && proposals.detailId === pending.id && proposals.detailRecord) showProposalReview(proposals.detailRecord);
    }
  }
}
$('#upload-proposal').addEventListener('click', () => $('#proposal-file').click());
$('#proposal-file').addEventListener('change', event => { const file = event.target.files?.[0]; event.target.value = ''; importProposal(file); });
$('#submit-proposal').addEventListener('click', submitProposal);
$('#refresh-proposals').addEventListener('click', loadProposals);
$('#proposal-sign-in').addEventListener('click', () => openAuth($('#proposal-sign-in')));
$('#proposal-review-form').addEventListener('submit', submitProposalReview);
$('#download-proposal').addEventListener('click', () => {
  const record = proposals.detailRecord;
  if (!record) return;
  const blob = new Blob([JSON.stringify(record.specification, null, 2) + '\n'], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = node('a');
  link.href = url;
  link.download = 'crucible-proposal-' + record.id + '-r' + record.revision + '.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// Research records keep claims and their conditions separate from hourly ranking.
function showWorkspace(name) {
  if (!RESEARCH_ENABLED && name === 'research') name = 'catalogue';
  const enteringResearch = !research.visible && name === 'research';
  research.visible = name === 'research';
  $('#catalogue-workspace').hidden = name !== 'catalogue';
  $('#workspace').hidden = name !== 'scheme';
  $('#overview-workspace').hidden = name !== 'overview';
  $('#research-workspace').hidden = !research.visible;
  $('#about-workspace').hidden = name !== 'about';
  $('#proposal-workspace').hidden = name !== 'propose';
  for (const kind of ['challenge', 'research', 'about']) {
    const button = $('#nav-' + kind);
    const selected = kind === 'challenge' ? ['catalogue', 'scheme', 'overview', 'propose'].includes(name) : kind === name;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  $('.skip-link').href = workspaceSelector(name);
  if (enteringResearch && !research.targets.length) loadResearch();
  else if (enteringResearch && research.target) loadFindings();
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
  return {'argument': 'Argument', 'heuristic': 'Heuristic', 'experiment': 'Empirical result', 'mixed': 'Mixed evidence'}[value] || value;
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
    label.append(input, node('span', instanceSize(profile) == null ? profile.name || profile.id : `Tier ${instanceSize(profile)}`));
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
$('#nav-challenge').addEventListener('click', () => navigate('#challenge'));
$('#nav-about').addEventListener('click', () => navigate('#about'));
$('#nav-research').hidden = !RESEARCH_ENABLED;
$('#nav-research').addEventListener('click', () => { if (RESEARCH_ENABLED) navigate('#research'); });
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
  const [session, catalogue] = await Promise.allSettled([api('/api/session'), api('/api/schemes')]);
  if (session.status === 'fulfilled') setSession(session.value);
  if (catalogue.status === 'fulfilled') {
    state.schemes = catalogue.value.schemes || [];
    state.ready = true;
    renderCatalogue();
    applyRoute();
  } else {
    showInlineError($('#kem-catalogue'), catalogue.reason.message, initialize);
    $('#challenge-loading').hidden = true;
  }
  if (session.status === 'rejected') globalError('Sign-in could not be loaded. ' + session.reason.message);
  $('#retry-button').disabled = false;
}
setInterval(updateClock, 1000);
setInterval(() => { if (!document.hidden) synchronizeSession(); }, 60000);
initialize();

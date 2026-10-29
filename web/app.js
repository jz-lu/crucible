'use strict';

const $ = selector => document.querySelector(selector);
// Keep the research interface dormant while the first MVP focuses on numerical challenges.
const RESEARCH_ENABLED = false;
const state = {
  auth: {}, authPanel: 'signin', githubBusy: false, githubPending: false, githubCompleteBusy: false, authCallbackBusy: false, githubLinkContext: null,
  passwordResetToken: null, passwordResetBusy: false, passwordRecoveryBusy: false,
  access: null, sessionResolved: false, invitationToken: null, invitationBusy: false, invitationDelivery: null,
  returnRoute: null, protectedLoaded: false, canManageBetaAccess: false,
  user: null, csrf: '', canReviewProposals: false, canSelfReviewProposals: false, schemes: [], profile: null, challenge: null,
  generationAdmission: null, methods: [], view: 'leaderboard', authMode: 'login', round: null,
  timeOffset: 0, ready: false, catalogueLoaded: false, challengeLoading: false, issueBusy: false,
  submitBusy: false, pendingAttempt: null, roundRefreshing: false,
  challengeRequest: 0, leaderboardRequest: 0, submissionChallenge: null,
  leaderboardDataKey: null, leaderboardPendingKey: null,
  historyRequest: 0, recoveryRequest: 0, catalogueRequest: 0, catalogueRevision: null, sessionRevision: 0,
  peopleRequest: 0, personRequest: 0, personHandle: null, personDataKey: null,
  people: [], peoplePagination: null,
  connectCode: null, connectRequest: 0, connection: null, connectBusy: false,
  agentTokenRequest: 0,
  agentTokens: [], agentTokenPagination: null,
  agentInstructionsKey: null, challengeStartIntent: null, profileMetadataPending: null,
  screen: 'catalogue', reviewSection: 'publication', schemeId: null, schemeVersion: null,
  overviewMetrics: {computation: 'keygen', security: 'classical'},
  importBusy: false, recoveryImportRequest: 0, submissionFormRevision: 0, submissionDraftBaseline: null,
};
let toastTimer;
let roundRetryAt = 0;
let sessionSynchronization = null;
let documentEpoch = 0, documentRetired = false, initializationRequest = 0;
let initializationPending = false, resumeInitialization = false;
const researcherEditor = {account: null, request: 0, base: null, pending: null, open: false, busy: false, loading: false, conflict: false, contextRequired: false, guarded: false};
function ownsDocument(epoch) { return !documentRetired && documentEpoch === epoch; }
window.addEventListener('pagehide', () => {
  resumeInitialization = initializationPending || !state.sessionResolved || browsingRequests.size > 0;
  documentRetired = true; documentEpoch++;
  // A restored document must not attach to requests owned by its retired visit.
  // Keep completed snapshots; detach only in-flight handoffs and read owners.
  catalogueLoad = null; mapCatalogueLoad = null; releaseLoads.clear();
  pendingReads.clear(); intendedReads.clear(); warmedReads.clear(); notebookEarlyReads.clear(); readViewEpoch++;
  sessionSynchronization = null; state.roundRefreshing = false;
  state.challengeStartIntent = null;
  notebookWorkspace?.invalidateReads?.(); reviewNotebookWorkspace?.invalidateReads?.();
  window.CrucibleResources?.invalidateReads?.();
  suspendResearcherEditor();
});
window.addEventListener('pageshow', event => {
  documentRetired = false;
  const resume = event.persisted && resumeInitialization;
  resumeInitialization = false;
  if (resume) void initialize();
});
let notebookWorkspace = null;
let reviewNotebookWorkspace = null;
let notebookAssets = null;
let notebookAssetsReady = false;
const notebookAssetLoads = new Map();
const notebookAssetAttempts = new Map(); // Four fixed asset paths; no account data.
let notebookShowRequest = 0;
let catalogueLoad = null;
let mapCatalogueLoad = null;
let mapReleases = null;
// Navigation contains labels/progress only. Charts and challenge controls must
// resolve an exact complete release through this separate, page-memory cache.
const releaseDetails = new Map();
const releaseLoads = new Map();
let releaseGeneration = 0;
let navigationProjection = false;
let workspaceLoadRequest = 0;
const routeLoadingPanel = $('#route-loading');
// Only previously authorized read results live here, never credentials or rights.
// Revalidate reads on entry, sharing in-flight or recent user-intent requests.
// Bound both lifetime and size; nothing is persisted in browser storage.
const readViews = new Map();
const pendingReads = new Map();
const intendedReads = new Map();
const warmedReads = new Map();
const notebookEarlyReads = new Map();
let browseWarmScheduled = null, browseWarmRunning = false, browseWarmState = null;
let browseWarmIntent = null;
const BROWSE_WARM_BYTES = 4 * 1024 * 1024, BROWSE_WARM_JOBS = 24;
let readViewEpoch = 0;
let renderedReads = new WeakMap();
function invalidateReadViews({resourceReports = true} = {}) {
  readViews.clear(); pendingReads.clear(); intendedReads.clear(); renderedReads = new WeakMap(); readViewEpoch++;
  warmedReads.clear(); notebookEarlyReads.clear(); referenceTimings.clear();
  if (resourceReports) window.CrucibleResources?.clear();
  browseWarmScheduled = null; browseWarmIntent = null;
}
function primeRead(key, path) {
  for (const [oldKey, entry] of intendedReads) if (entry.done && Date.now() - entry.at >= 10000) intendedReads.delete(oldKey);
  if (!hasAccess() || navigator.connection?.saveData || cachedRead(key) || pendingReads.has(key) || intendedReads.has(key) || intendedReads.size + pendingReads.size >= 2) return;
  const task = api(path, undefined, {readRecovery: true}), entry = {task, at: Date.now(), done: false};
  task.then(() => { entry.done = true; }, () => { if (intendedReads.get(key) === entry) intendedReads.delete(key); });
  intendedReads.set(key, entry);
}
function readAgain(key, path) {
  const intended = intendedReads.get(key); intendedReads.delete(key);
  const warmed = warmedReads.get(key); warmedReads.delete(key);
  if (pendingReads.has(key)) return pendingReads.get(key);
  const response = intended && Date.now() - intended.at < 10000 ? intended.task :
    warmed && Date.now() - warmed.at < 10000 ? Promise.resolve(warmed.data) : api(path, undefined, {readRecovery: true});
  const task = response.finally(() => { if (pendingReads.get(key) === task) pendingReads.delete(key); });
  pendingReads.set(key, task); return task;
}
function cachedRead(key) {
  const entry = readViews.get(key);
  if (!entry || Date.now() - entry.at > 300000) { readViews.delete(key); return null; }
  readViews.delete(key); readViews.set(key, entry);
  return entry.data;
}
function rememberRead(key, data) {
  const text = JSON.stringify(data);
  readViews.delete(key);
  if (text.length > 500000) return;
  readViews.set(key, {data, at: Date.now(), size: text.length});
  let size = [...readViews.values()].reduce((sum, entry) => sum + entry.size, 0);
  while (readViews.size > 32 || size > 2000000) {
    const first = readViews.keys().next().value;
    size -= readViews.get(first).size; readViews.delete(first);
  }
}
function paintRead(key, target, data, render) {
  const signature = JSON.stringify(data), old = renderedReads.get(target);
  if (old?.key === key && old.signature === signature && old.child === target.firstChild) return;
  render(data);
  renderedReads.set(target, {key, signature, child: target.firstChild});
}
function forbiddenRead(error) { return [401, 403, 404, 410].includes(error.status); }
function readRefreshError(key, target, error, retry, cached) {
  readViews.delete(key);
  if (cached && !forbiddenRead(error)) toast('Refresh failed. ' + error.message);
  else showInlineError(target, error.message, retry);
}
function browseWarmAllowed() {
  return !documentRetired && hasAccess() && state.user && state.ready && !document.hidden && !navigator.connection?.saveData;
}
function scheduleBrowseWarmup() {
  if (!browseWarmAllowed() || browseWarmScheduled || browseWarmRunning) return;
  const token = {}; browseWarmScheduled = token;
  // Give the selected view a paint before requesting any optional metadata.
  setTimeout(() => {
    if (browseWarmScheduled !== token) return;
    const run = () => {
      if (browseWarmScheduled !== token) return;
      browseWarmScheduled = null; void runBrowseWarmup();
    };
    const idle = () => window.requestIdleCallback ? window.requestIdleCallback(run, {timeout: 1000}) : run();
    if (window.requestAnimationFrame) window.requestAnimationFrame(idle); else idle();
  }, 200);
}
function warmBytes(value) { return new TextEncoder().encode(JSON.stringify(value)).length; }
function prepareResourceReport(scheme, speculative = true) {
  if (!hasAccess() || !scheme) return Promise.resolve({bytes: 0});
  return window.CrucibleResources?.load(scheme, api, () => redrawResourceCharts(scheme), {speculative}) || Promise.resolve({bytes: 0});
}
function primeRouteResources(scheme) {
  // A selected overview can fetch its independent report alongside the release
  // description. Neither request creates a challenge or grants access.
  if (/^#scheme\/[^/]+\/[^/?]+(?:\?|$)/.test(window.location.hash)) void prepareResourceReport(scheme, false);
}
async function warmRead(key, path, project) {
  if (cachedRead(key)) return 0;
  const epoch = readViewEpoch;
  const data = await readAgain(key, path);
  if (!hasAccess() || epoch !== readViewEpoch) return 0;
  const value = project(data), bytes = warmBytes(data);
  if (value && bytes <= 500000) {
    rememberRead(key, value);
    // Consume a just-warmed response once, as with a user-intent prefetch;
    // later visits still revalidate normally while painting the bounded cache.
    warmedReads.set(key, {data, at: Date.now()});
  }
  return bytes;
}
function browseWarmJobs() {
  const jobs = [], add = (key, run) => jobs.push({key, run});
  add('notebook:setup', async () => { await ensureNotebookAssets(); if (browseWarmAllowed()) getNotebookWorkspace(); return 0; });
  for (const kind of ['timeline', 'list']) add('notebook:' + kind, () => notebookWorkspace?.warm?.(kind));
  if (state.canReviewProposals) for (const kind of ['list', 'next']) {
    add('publication:' + kind, () => window.CrucibleNotebook && getNotebookWorkspace(true).warm(kind));
  }
  add('navigation', async () => {
    if (state.catalogueLoaded) return 0;
    await loadAuthorizedCatalogue(); return state.catalogueLoaded ? warmBytes(state.schemes) : 0;
  });
  const current = state.schemes;
  const preferred = [routeRelease(), browseWarmIntent].filter(Boolean);
  const ordered = [...preferred.map(value => current.find(item => item.id === value.id && String(item.version) === String(value.version))), ...current]
    .filter((value, index, values) => value && values.indexOf(value) === index).slice(0, 8);
  const releaseJobs = scheme => {
    const key = releaseKey(scheme.id, scheme.version);
    add('release:' + key, async () => {
      // One bounded scheme job, at most two independent reads. Charge both
      // responses without adding eight jobs beyond the reviewer's queue limit.
      const hadRelease = Boolean(completeRelease(scheme.id, scheme.version));
      const results = await Promise.allSettled([loadRelease(scheme.id, scheme.version), prepareResourceReport(scheme)]);
      const resourceBytes = results[1].status === 'fulfilled' ? results[1].value?.bytes || 0 : 0;
      return resourceBytes + (hadRelease ? 0 : warmBytes(completeRelease(scheme.id, scheme.version) || null));
    });
  };
  // The viewed or most recently intended release precedes other graph data.
  if (ordered[0]) releaseJobs(ordered[0]);
  if (state.user) {
    const handle = state.user.handle;
    add('person:' + handle, () => warmRead('person:' + handle.toLowerCase(), '/api/people/' + encodeURIComponent(handle) + '?include_milestones=false',
      data => data.person?.handle?.toLowerCase() === handle.toLowerCase() ? data.person : null));
  }
  add('notebook:map', () => notebookWorkspace?.warm?.('map'));
  add('people', () => warmRead('people', '/api/people?limit=50&offset=0&include_records=false', data =>
    Array.isArray(data.people) && data.people.every(person => typeof person.handle === 'string' && person.handle) ?
      {people: data.people, pagination: data.pagination, scoring: data.scoring} : null));
  add('history', () => warmRead('history:' + (state.catalogueRevision || ''), '/api/me/submissions', data =>
    Array.isArray(data.submissions) && data.submissions.every(item => typeof item?.id === 'string' && item.id) ? {submissions: data.submissions} : null));
  for (const scheme of ordered.slice(1)) releaseJobs(scheme);
  return jobs;
}
async function runBrowseWarmup() {
  if (!browseWarmAllowed() || browseWarmRunning) return;
  if (protectedRequests.size || [...document.querySelectorAll('dialog')].some(dialog => dialog.open) || state.issueBusy || state.submitBusy) {
    scheduleBrowseWarmup(); return;
  }
  if (browseWarmState?.epoch !== readViewEpoch) browseWarmState = {epoch: readViewEpoch, done: new Set(), bytes: 0};
  const batch = browseWarmState;
  if (batch.done.size >= BROWSE_WARM_JOBS || batch.bytes >= BROWSE_WARM_BYTES) return;
  const job = browseWarmJobs().find(item => !batch.done.has(item.key));
  if (!job) return;
  batch.done.add(job.key); browseWarmRunning = true;
  try {
    const result = await job.run();
    const bytes = typeof result === 'number' ? result : result?.bytes;
    if (batch.epoch === readViewEpoch) batch.bytes += typeof bytes === 'number' && Number.isFinite(bytes) ? Math.max(0, bytes) : 0;
  } catch { /* Optional reads never replace the selected page with an error. */ }
  finally { browseWarmRunning = false; scheduleBrowseWarmup(); }
}
let assumptionsWorkspace = null;
const referenceTimings = new Map();
const REFERENCE_TIMING_MAX_AGE_MS = 60000;
const protectedRequests = new Set();
const browsingRequests = new Set();
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
  reviewRequest: 0, reviewBusy: false, reviewPending: null,
  agentRevision: null, agentRouteKey: null, agentKeys: {draft: null, revision: null}
};

function normalizeAIModel(value = '', label = 'AI model(s)') {
  if (typeof value !== 'string' || [...value].length > 300) {
    throw new Error(`${label} must be text of at most 300 characters.`);
  }
  // With the Unicode flag, the surrogate range matches only unpaired surrogates.
  if (/[\u0000\uD800-\uDFFF]/u.test(value)) {
    throw new Error(`${label} must be valid Unicode text without null characters.`);
  }
  return value.trim();
}

// Versioned data contract: imports never execute code or submit an answer.
function readRecoveryDocument(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > RECOVERY_FILE_MAX_BYTES) {
    throw new Error('Choose a JSON file no larger than 128 KB.');
  }
  let value;
  try { value = JSON.parse(text.replace(/^\uFEFF/, '')); }
  catch { throw new Error('This file is not valid JSON. Use the downloadable template, without Markdown code fences.'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The result must be a JSON object.');
  const allowed = ['format', 'challenge_id', 'candidate', 'method', 'compute', 'ai_model', '_instructions'];
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
    ai_model: normalizeAIModel(value.ai_model, 'ai_model'),
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
    ai_model: '',
    compute: {hardware: '', workers: 1, wall_seconds: null, precomputation: '', peak_memory: '', notes: ''},
    _instructions: {
      file: 'Save one UTF-8 JSON object as a .json file, at most 131072 bytes (128 KiB), including these instructions. No Markdown fences or JSON comments. Escape line breaks inside strings as \\n.',
      fields: 'Required: format, challenge_id, candidate, method.name, method.pseudocode, compute.hardware, compute.workers, compute.wall_seconds, compute.precomputation, compute.peak_memory. Optional: ai_model, compute.notes and this _instructions object. No other fields are accepted.',
      instructions: 'You may keep or remove _instructions. If kept, it must be an object of text values. It is guidance only, ignored when submitting; editing it cannot change validation rules.',
      format: 'Keep format exactly "crucible-recovery-v1". Required text fields and the null attack-time placeholder must be filled in before import.',
      challenge_id: 'Use the exact challenge.id from the downloaded challenge contract or this template. The website looks up that instance and selects its scheme, release, and parameter profile automatically, even if another profile is displayed. It must belong to the signed-in account, still be open, and have no final answer. Do not replace this ID with a stage number or parameter-set name.',
      candidate: `The complete ${keyBytes}-byte shared secret: exactly ${keyBytes * 2} hexadecimal characters. Uppercase or lowercase accepted; no spaces, line breaks, or 0x prefix.`,
      'method.name': 'Required nonblank text, at most 100 Unicode characters; a short descriptive name for the attack.',
      'method.pseudocode': 'Required nonblank text, at most 15000 Unicode characters. Describe the algorithm, key derivation, and preparation. The method object must also fit within a 65536-byte JSON request, including escapes. Pseudocode is stored as text, never executed.',
      ai_model: 'Optional participant-reported text, at most 300 Unicode characters before trimming outer whitespace. Name the AI model(s) that implemented this result, including exact versions when known. Do not guess a model or version. Omit or leave empty when not reported; blank does not establish that no AI was used. Null and null characters are not accepted; text must be valid Unicode.',
      'compute.hardware': 'Required nonblank text, at most 500 Unicode characters. Describe the CPU/GPU model and configuration used.',
      'compute.workers': 'Required JSON number: an integer from 1 through 1000000. State the number of parallel workers; do not quote the number or use a boolean.',
      'compute.wall_seconds': 'Required finite JSON number from 0 through 315576000; fractions are allowed. Report attack wall-clock time in seconds, not the time since the challenge was downloaded. Replace null; do not quote the number.',
      'compute.precomputation': 'Required nonblank text, at most 2000 Unicode characters. Describe preparation, its cost, and reused data, or write "None".',
      'compute.peak_memory': 'Required nonblank text, at most 300 Unicode characters. Give an estimate with units, or write "Not measured".',
      'compute.notes': 'Optional text, at most 2000 Unicode characters. Include measurement uncertainty or other relevant details. An omitted or empty string is allowed; null is not.',
      text: 'Text limits apply before trimming outer whitespace. Required text cannot consist only of whitespace. Keep method, AI model and compute declarations accurate; they are participant-reported.',
      deadline: `This instance closes at ${utcDate(challenge.ends_at)}. The complete final answer must reach the server strictly before that deadline. Importing a file does not extend the deadline or submit an answer.`,
      submission: 'Review the imported fields, then explicitly submit. Each instance accepts one well-formed final answer, whether correct or incorrect. If a submission outcome is uncertain, retry the same answer; do not replace it with another file.',
      publication: 'A correct recovery may publish your handle, method, pseudocode, AI model annotation, and compute declaration with the result. Do not include passwords, API tokens, or other credentials.'
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
function instanceSize(profile) { return profile && Object.hasOwn(profile, 'stage_number') ? profile.stage_number : profile?.instance_size ?? profile?.parameter_bits; }
function stagePosition(profile) { return profile?.stage_position ?? instanceSize(profile); }
function intermediateConfiguration(profile) { return profile != null && Object.hasOwn(profile, 'stage_number') && profile.stage_number === null; }
function namedParameters(scheme, profile = null) {
  return scheme?.selection_kind === 'parameter-set' || profile?.profile_kind === 'standard-parameter-set';
}
function researchTiers(scheme, profile = null) {
  return profile?.profile_kind === 'research-tier' || scheme?.profile_kind === 'research-tier' ||
    scheme?.profiles?.some(item => item.profile_kind === 'research-tier');
}
function exactProfileRecords(scheme, profile = null) {
  return scheme?.record_inheritance === false || profile?.record_inheritance === false || namedParameters(scheme, profile);
}
function profileLabel(profile, scheme = null) {
  return namedParameters(scheme, profile) ? profile?.profile_label || profile?.name || profileId(profile) :
    profile?.stage_label || (instanceSize(profile) == null ? profile?.native_parameter_label || profileId(profile) : 'Stage ' + instanceSize(profile));
}
function orderedProfiles(scheme) {
  return (scheme?.profiles || []).filter(profile => !scheme?.default_progression || Number.isSafeInteger(instanceSize(profile))).slice().sort((a, b) => namedParameters(scheme) ?
    (a.profile_order ?? 0) - (b.profile_order ?? 0) : instanceSize(a) - instanceSize(b));
}
function recoveredProfileIds(scheme) {
  if (Array.isArray(scheme?.recovered_profile_ids)) return scheme.recovered_profile_ids;
  if (Array.isArray(scheme?.profile_recoveries)) return scheme.profile_recoveries.map(record => record.profile_id);
  return scheme?.largest_recovered_profile_id ? [scheme.largest_recovered_profile_id] : [];
}
function recordProfileLabel(record) {
  const scheme = availableReleases().find(item => item.id === record.scheme_id && String(item.version) === String(record.scheme_version));
  const profile = scheme?.profiles?.find(item => profileId(item) === record.profile_id);
  if (profile) return profileLabel(profile, scheme);
  if (namedParameters(scheme, record)) return record.profile_label || record.profile_id;
  return Number.isSafeInteger(record.tier ?? record.instance_size) ? 'Stage ' + (record.tier ?? record.instance_size) : record.profile_id;
}
function initialSecurityEstimate(scheme, profile) {
  const model = scheme?.initial_security_model;
  const estimate = profile?.initial_security_estimate;
  return typeof model?.id === 'string' && model.id && estimate?.model_id === model.id &&
    Number.isFinite(estimate.bits) && estimate.bits >= 0 ? estimate.bits : null;
}
function securityReferences(scheme, metric) {
  const evidence = scheme?.security_references;
  const value = evidence?.metrics?.[metric];
  return evidence?.format === 'crucible-security-references-v1' && evidence.scheme_id === scheme.id &&
    String(evidence.version) === String(scheme.version) && Array.isArray(value?.models) && Array.isArray(value?.estimates) ? value : null;
}
function securityReference(scheme, profile, metric) {
  // Frozen release references remain intact. Later literature evidence is
  // separately identified, and applies only to the explicitly named profile.
  const frozen = metric === 'classical' ? initialSecurityEstimate(scheme, profile) : null;
  if (frozen !== null) return {bits: frozen, model: scheme.initial_security_model};
  const evidence = securityReferences(scheme, metric);
  const estimates = (evidence?.estimates || []).filter(item => item?.profile_id === profileId(profile));
  if (estimates.length !== 1) return null;
  const estimate = estimates[0];
  const models = evidence.models.filter(model => model?.id === estimate.model_id);
  return models.length === 1 && ['id', 'label', 'unit'].every(key => typeof models[0][key] === 'string' && models[0][key].trim()) && typeof estimate.bits === 'number' && Number.isFinite(estimate.bits) && estimate.bits >= 0 ?
    {bits: estimate.bits, model: models[0]} : null;
}
function supportingNoteLink(note) {
  if (typeof note?.id !== 'string' || !note.id.startsWith('curated:') || !Number.isSafeInteger(note.revision) ||
      note.revision < 1 || !/^[a-f0-9]{64}$/.test(note.content_sha256 || '')) return null;
  const link = node('a', 'Supporting research note →', 'text-button');
  link.href = '#notebook?' + new URLSearchParams({note: note.id, revision: note.revision, sha256: note.content_sha256});
  return link;
}
function estimateBits(value) { return value.toLocaleString('en-US', {maximumFractionDigits: 1}); }
function attackReferenceModel(scheme) {
  const model = scheme?.initial_security_model;
  return researchTiers(scheme) || model?.kind === 'attack-reference' || model?.status === 'limited-reference' || model?.estimate_kind === 'attack-upper-bound';
}
function tierNote(scheme, profile) {
  if (namedParameters(scheme, profile)) return 'Named parameter sets are distinct challenge targets, not a stage progression. Recoveries do not transfer between them.';
  const context = profile?.stage_context;
  if (intermediateConfiguration(profile)) return context?.before_stage != null && context?.after_stage != null ? `Between Stages ${context.before_stage} and ${context.after_stage}. ${profile.native_parameter_label || 'A reviewed intermediate configuration.'}` : 'A reviewed configuration outside the default progression.';
  const estimate = initialSecurityEstimate(scheme, profile);
  if (estimate !== null) return `${attackReferenceModel(scheme) ? 'Initial classical attack reference' : 'Initial classical estimate'}: ≈${estimateBits(estimate)} bits.`;
  const published = securityReference(scheme, profile, 'classical');
  if (published) return `Published classical attack reference: ${estimateBits(published.bits)} (${published.model.unit}). See Protocol for its scope.`;
  return researchTiers(scheme, profile) ? 'Stages follow this scheme’s default progression through reviewed configurations. Security estimates are not yet available.' : 'Stages follow this scheme’s default progression through reviewed configurations.';
}
function availableReleases() { return state.schemes.flatMap(scheme => [scheme, ...(scheme.previous_releases || [])]); }
function releaseKey(id, version) { return JSON.stringify([id, String(version)]); }
function completeRelease(id, version) {
  const cached = releaseDetails.get(releaseKey(id, version));
  if (cached?.revision === state.catalogueRevision) return cached.scheme;
  // Older servers ignore the new projection argument and return full metadata.
  // Keep that deployment/rollback path without treating navigation as complete.
  if (!navigationProjection) return availableReleases().find(scheme => scheme.id === id && String(scheme.version) === String(version));
  return null;
}
function selectedScheme() {
  return completeRelease(state.schemeId, state.schemeVersion);
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
function assumptionsRoute(scheme, profile = null, claim = null) {
  const query = new URLSearchParams();
  if (profile) query.set('profile', profileId(profile));
  if (claim) query.set('claim', claim);
  return '#scheme/' + encodeURIComponent(scheme.id) + '/' + encodeURIComponent(scheme.version) + '/assumptions' + (query.size ? '?' + query : '');
}
function renderAssumptions(scheme, profile, query) {
  $('#assumptions-back').href = overviewRoute(scheme, profile);
  setText('#assumptions-title', scheme.name);
  setText('#assumptions-context', `Release ${scheme.version}${profile ? ' · ' + profileLabel(profile, scheme) : ''}`);
  if (!assumptionsWorkspace) assumptionsWorkspace = window.CrucibleAssumptions.create({
    root: $('#assumptions-content'), api, route: assumptionsRoute,
    isVisible: () => hasAccess() && state.screen === 'assumptions',
    account: () => state.user?.id || null
  });
  assumptionsWorkspace.show(scheme, profile, query.get('claim'));
}
function navigate(hash) {
  if (window.location.hash === hash) applyRoute();
  else window.location.hash = hash;
}
function personRoute(handle) { return '#person/' + encodeURIComponent(handle); }
function siteOrigin() { return new URL(window.location.href).origin; }
function connectionCode(value) {
  const code = String(value || '').trim().toUpperCase().replace(/-/g, '');
  return /^[A-Z0-9]{8}$/.test(code) ? code.slice(0, 4) + '-' + code.slice(4) : null;
}
function hasAccess() { return state.sessionResolved && state.access?.allowed === true; }
function captureInvitation() {
  const [path, query = ''] = window.location.hash.split('?');
  if (path !== '#join') return;
  const token = new URLSearchParams(query).get('token');
  if (!token || token.length > 512 || !/^[A-Za-z0-9_-]+$/.test(token)) {
    state.invitationToken = null; state.invitationDelivery = null;
    setError('#access-error', 'This invitation link is incomplete. Open the full link you received.');
    return;
  }
  if (token && token.length <= 512 && /^[A-Za-z0-9_-]+$/.test(token) && token !== state.invitationToken) {
    state.invitationToken = token;
    state.invitationDelivery = null;
    $('#access-code').value = '';
    setError('#access-error', '');
  }
}
function renderAccessGate() {
  $('#access-workspace').removeAttribute('data-catalogue-pending');
  $('#access-loading-preview').hidden = true;
  $('#access-workspace').setAttribute('aria-busy', 'false');
  if (state.screen !== 'access' && window.location.hash && !window.location.hash.startsWith('#join') && !authenticationRoute()) state.returnRoute = window.location.hash;
  state.screen = 'access';
  showWorkspace('access');
  const status = state.access?.status;
  const blocked = ['revoked', 'suspended'].includes(status);
  const joining = Boolean(state.invitationToken);
  setText('#access-title', 'Private beta');
  setText('#access-description', hasAccess() ? 'Your access is ready.' : blocked ?
    'Your beta access is not active. Contact the person who invited you.' : joining ?
    'Join Crucible with your invitation.' : state.user ?
    'Open your invitation link to activate beta access.' : 'Sign in to continue, or open your invitation link to join.');
  $('#access-auth-actions').hidden = Boolean(state.user);
  $('#access-register').hidden = !joining;
  $('#access-verification').hidden = !state.user || !joining || blocked || hasAccess();
  $('#access-continue').hidden = !hasAccess();
  setText('#access-account', state.user ? 'Joining as ' + state.user.handle : '');
  $('#access-code-form').hidden = !state.invitationDelivery;
  $('#access-send-code').disabled = state.invitationBusy;
  $('#access-redeem').disabled = state.invitationBusy;
  setText('#access-send-code', state.invitationDelivery ? 'Send a new code' : 'Email a verification code');
  setText('#access-delivery', state.invitationDelivery ? 'Code sent to ' + state.invitationDelivery.masked_recipient + '. Check that inbox to continue.' : '');
  $('#register-mode').hidden = state.access?.mode === 'beta' && !joining;
  renderAuthMethods();
  document.title = 'Private beta — Crucible';
}
function clearProtectedState() {
  resetResearcherEditor();
  invalidateReadViews();
  referenceTimings.clear(); window.CrucibleResources?.clear();
  assumptionsWorkspace?.clear();
  notebookWorkspace?.setAccount(null);
  reviewNotebookWorkspace?.setAccount(null);
  const reloadShell = state.protectedLoaded;
  state.protectedLoaded = false;
  state.ready = false;
  state.catalogueLoaded = false;
  catalogueLoad = null;
  releaseDetails.clear(); releaseLoads.clear(); releaseGeneration++;
  navigationProjection = false; workspaceLoadRequest++;
  mapCatalogueLoad = null; mapReleases = null;
  for (const controller of protectedRequests) controller.abort();
  for (const key of ['catalogueRequest', 'challengeRequest', 'leaderboardRequest', 'historyRequest', 'recoveryRequest', 'peopleRequest', 'personRequest', 'connectRequest']) state[key]++;
  state.schemes = []; state.catalogueRevision = null; state.generationAdmission = null; state.people = []; state.profile = null; state.challenge = null; state.methods = [];
  state.schemeId = null; state.schemeVersion = null; state.personHandle = null; state.personDataKey = null;
  state.pendingAttempt = null; state.submissionChallenge = null; state.connection = null; state.round = null;
  state.challengeStartIntent = null; state.challengeLoading = false;
  state.canReviewProposals = false; state.canSelfReviewProposals = false; state.canManageBetaAccess = false;
  resetAgentSettings(); resetAgentInstructions(); resetProposalAccount(state.user?.id);
  research.request++; research.recoveriesRequest++; research.targets = []; research.target = null; research.findings = []; research.recoveries = [];
  for (const selector of ['#kem-catalogue', '#people-content', '#person-content', '#leaderboard-content', '#history-content', '#recovery-content', '#connect-content', '#overview-graphs', '#target-catalogue']) $(selector).replaceChildren();
  for (const dialog of document.querySelectorAll('dialog')) if (dialog.open && dialog !== authDialog) dialog.close();
  resetSubmissionForm();
  // A fresh shell also releases notebook closures and private data held by the
  // previous page. The next bootstrap stays on the access screen, without a loop.
  if (reloadShell && typeof window.location.reload === 'function') window.location.reload();
}
async function loadAuthorizedCatalogue() {
  if (!hasAccess()) return false;
  if (catalogueLoad) return catalogueLoad;
  const account = state.user?.id, csrf = state.csrf, request = ++state.catalogueRequest, lifecycle = documentEpoch;
  const current = () => ownsDocument(lifecycle) && hasAccess() && account === state.user?.id && csrf === state.csrf && request === state.catalogueRequest;
  const task = (async () => {
    let data;
    try { data = await api('/api/schemes?projection=navigation', undefined, {readRecovery: true}); }
    catch (error) {
      if (!current() || !routeNeedsCatalogue()) return false;
      throw error;
    }
    if (!current()) return false;
    installNavigation(data);
    state.catalogueLoaded = true;
    state.ready = true;
    state.protectedLoaded = true;
    // A slow catalogue request must not reload an independent page entered
    // meanwhile. Its eventual data is available when a scheme route needs it.
    if (routeNeedsCatalogue()) applyRoute();
    return true;
  })();
  catalogueLoad = task;
  try { return await task; }
  finally { if (catalogueLoad === task) catalogueLoad = null; }
}
const RELEASE_PROGRESS_FIELDS = ['generation_admission', 'participant_count', 'recovered_profile_ids', 'largest_recovered_profile_id', 'next_profile_id', 'profile_recoveries', 'first_recovery', 'recovery_summary'];
function installNavigation(data) {
  if (!Array.isArray(data.schemes) || (data.projection && data.projection !== 'navigation')) throw new Error('Scheme navigation could not be loaded.');
  const revision = data.catalogue_revision || null;
  const releases = data.schemes.flatMap(scheme => [scheme, ...(scheme.previous_releases || [])]);
  const keys = new Set();
  for (const scheme of releases) {
    if (!scheme || typeof scheme.id !== 'string' || !scheme.id || scheme.version == null ||
        !Array.isArray(scheme.profiles) || keys.has(releaseKey(scheme.id, scheme.version))) throw new Error('Scheme navigation could not be loaded.');
    keys.add(releaseKey(scheme.id, scheme.version));
  }
  if (state.catalogueRevision !== null && state.catalogueRevision !== revision) {
    invalidateReadViews();
    releaseLoads.clear(); releaseGeneration++;
  }
  state.schemes = data.schemes;
  state.catalogueRevision = revision;
  navigationProjection = data.projection === 'navigation';
  for (const [key, entry] of releaseDetails) {
    const current = data.schemes.find(item => item.id === entry.scheme.id);
    if (entry.revision !== revision || !keys.has(key) || (entry.currentVersion != null && String(entry.currentVersion) !== String(current?.version))) releaseDetails.delete(key);
  }
  if (!navigationProjection) {
    for (const scheme of releases) releaseDetails.set(releaseKey(scheme.id, scheme.version), {revision, scheme});
  } else {
    for (const scheme of releases) {
      const entry = releaseDetails.get(releaseKey(scheme.id, scheme.version));
      if (!entry) continue;
      const updated = {...entry.scheme};
      for (const field of RELEASE_PROGRESS_FIELDS) {
        if (Object.hasOwn(scheme, field)) updated[field] = scheme[field]; else delete updated[field];
      }
      entry.scheme = updated;
    }
  }
}
function routeRelease() {
  const match = window.location.hash.split('?')[0].match(/^#(?:scheme|challenge)\/([^/]+)\/([^/]+)(?:\/assumptions)?$/);
  if (!match) return null;
  try {
    const id = decodeURIComponent(match[1]), version = decodeURIComponent(match[2]);
    return id && version && id.length <= 100 && version.length <= 100 ? {id, version} : null;
  } catch { return null; }
}
function challengeReadKey(id, version, profile) {
  return 'challenge:' + JSON.stringify([state.round?.id, id, String(version), profile]);
}
function challengeReadPath(id, version, profile) {
  return '/api/challenges?' + new URLSearchParams({profile_id: profile, scheme_id: id, scheme_version: version, summary: 'true'});
}
function primeRouteChallenge(target) {
  if (!target) return;
  const params = new URLSearchParams(window.location.hash.split('?')[1] || '');
  const profile = params.get('profile');
  if (params.get('view') !== 'challenge' || !profile || profile.length > 200) return;
  // Read only the explicitly visited selection while its description loads.
  // Generation still requires the normal confirmed round or deliberate action.
  primeRead(challengeReadKey(target.id, target.version, profile), challengeReadPath(target.id, target.version, profile));
}
async function loadMapCatalogue() {
  if (!hasAccess()) return;
  if (state.catalogueLoaded || mapReleases) return;
  if (mapCatalogueLoad) return mapCatalogueLoad;
  const account = state.user?.id, csrf = state.csrf, lifecycle = documentEpoch;
  const task = (async () => {
    const data = await api('/api/schemes?projection=map', undefined, {readRecovery: true});
    if (!ownsDocument(lifecycle) || !hasAccess() || account !== state.user?.id || csrf !== state.csrf) return;
    if (data.projection !== 'map' || !Array.isArray(data.schemes) || data.schemes.some(item =>
      !item || typeof item.id !== 'string' || !item.id || typeof item.version !== 'string' || !item.version)) {
      throw new Error('Scheme links could not be loaded. Please try again.');
    }
    mapReleases = data.schemes;
  })();
  mapCatalogueLoad = task;
  try { await task; }
  finally { if (mapCatalogueLoad === task) mapCatalogueLoad = null; }
}
async function loadRelease(id, version) {
  if (!hasAccess()) return false;
  const key = releaseKey(id, version);
  if (state.catalogueLoaded && completeRelease(id, version)) return true;
  if (releaseLoads.has(key)) return releaseLoads.get(key);
  const account = state.user?.id, csrf = state.csrf, generation = releaseGeneration, lifecycle = documentEpoch;
  const current = () => ownsDocument(lifecycle) && hasAccess() && account === state.user?.id && csrf === state.csrf && generation === releaseGeneration;
  const task = (async () => {
    const data = await api('/api/schemes?' + new URLSearchParams({projection: 'release', scheme_id: id, version}), undefined, {readRecovery: true});
    if (!current()) return false;
    let scheme = data.scheme;
    if (!data.projection && Array.isArray(data.schemes)) scheme = data.schemes.flatMap(item => [item, ...(item.previous_releases || [])]).find(item => item.id === id && String(item.version) === String(version));
    if ((data.projection && data.projection !== 'release') || !scheme || scheme.id !== id || String(scheme.version) !== String(version) || !Array.isArray(scheme.profiles) || !scheme.protocol) throw new Error('This scheme release could not be loaded.');
    const revision = data.catalogue_revision || null;
    if (state.catalogueLoaded && revision !== state.catalogueRevision) throw new Error('Scheme information changed. Refresh the page to load the current description.');
    const latest = state.schemes.find(item => item.id === id);
    if (state.catalogueLoaded && data.projection === 'release' && String(data.current_version) !== String(latest?.version)) throw new Error('The current scheme release changed. Refresh the page to continue.');
    releaseDetails.set(key, {revision, scheme, currentVersion: data.current_version});
    return true;
  })();
  releaseLoads.set(key, task);
  try { return await task; }
  finally { if (releaseLoads.get(key) === task) releaseLoads.delete(key); }
}
function cataloguePreview() {
  const wrap = node('div', undefined, 'kem-table-wrap catalogue-preview loading-preview');
  wrap.setAttribute('aria-hidden', 'true');
  const table = node('table', undefined, 'kem-table'), head = node('thead'), headings = node('tr'), body = node('tbody');
  const labels = ['KEM', 'Largest recovery', 'Recovered by', 'Participants'];
  for (const label of labels) {
    const heading = node('th', label, label === 'Largest recovery' ? 'recovery-heading' : '');
    if (label === 'Largest recovery') {
      const line = node('span', undefined, 'recovery-heading-label');
      line.append(node('span', label), node('span', undefined, 'catalogue-help-space'));
      heading.replaceChildren(line);
    }
    headings.append(heading);
  }
  head.append(headings);
  const groupRow = node('tr', undefined, 'kem-group-row'), groupHeading = node('th');
  groupHeading.colSpan = 4;
  groupHeading.append(node('span', undefined, 'catalogue-placeholder catalogue-group-placeholder'));
  groupRow.append(groupHeading); body.append(groupRow);
  for (let index = 0; index < 6; index++) {
    const row = node('tr');
    for (const [column, label] of labels.entries()) {
      const cell = node('td');
      if (column) cell.dataset.label = label;
      cell.append(node('span', undefined, 'catalogue-placeholder')); row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body); wrap.append(table);
  return wrap;
}
function routeLoadingTarget() {
  return state.screen === 'catalogue' ? $('#kem-catalogue') : routeLoadingPanel;
}
function showRouteLoading() {
  const path = window.location.hash.split('?')[0], release = routeRelease();
  const known = release && availableReleases().find(item => item.id === release.id && String(item.version) === release.version);
  let profileName = 'Profile';
  try {
    const handle = path.match(/^#person\/([^/]+)$/)?.[1];
    const decoded = handle && decodeURIComponent(handle);
    if (decoded && decoded.length <= 100 && !/[\u0000-\u001f\u007f]/.test(decoded)) profileName = decoded;
  } catch { /* Malformed profile URLs never become display markup. */ }
  const title = known?.name || (release ? 'Scheme' : path.startsWith('#person/') ? profileName : path === '#notebook' ? 'Research notebook' : path === '#reviews' ? 'Review center' : path === '#propose' ? 'Propose a scheme' : 'KEM Schemes');
  const profileRoute = path.startsWith('#person/');
  routeLoadingPanel.classList.toggle('profile-route-preview', profileRoute);
  state.screen = 'loading';
  state.challengeRequest++; state.leaderboardRequest++; state.peopleRequest++; state.personRequest++;
  const params = new URLSearchParams(window.location.hash.split('?')[1] || '');
  const intent = state.challengeStartIntent;
  if (intent && (!release || release.id !== intent.schemeId || release.version !== intent.schemeVersion ||
      params.get('profile') !== intent.profileId || params.get('view') !== 'challenge')) state.challengeStartIntent = null;
  if (!release && ['', '#challenge'].includes(path)) {
    const target = $('#kem-catalogue');
    state.screen = 'catalogue';
    if (!target.querySelector('.catalogue-preview')) {
      const status = node('span', 'Loading schemes', 'sr-only'); status.setAttribute('role', 'status');
      target.replaceChildren(status, cataloguePreview());
    }
    target.setAttribute('aria-busy', 'true');
    $('#refresh-catalogue').disabled = true;
    showWorkspace('catalogue');
    document.title = title + ' — Crucible';
    return;
  }
  if (routeLoadingPanel.dataset.route === window.location.hash && routeLoadingPanel.getAttribute('aria-busy') === 'true') {
    showWorkspace('loading'); return;
  }
  routeLoadingPanel.dataset.route = window.location.hash;
  if (profileRoute) {
    const back = node('span', '← Leaderboard', 'text-button scheme-back'); back.setAttribute('aria-hidden', 'true');
    const backline = node('div', undefined, 'profile-backline'); backline.append(back);
    const heading = node('div', undefined, 'catalogue-heading'); heading.append(node('h1', title));
    quietReadStatus(routeLoadingPanel, 'Loading profile…');
    routeLoadingPanel.append(backline, heading, personPreview());
    routeLoadingPanel.setAttribute('aria-busy', 'true');
    showWorkspace('loading'); document.title = title + ' — Crucible';
    return;
  }
  routeLoadingPanel.replaceChildren(node('h1', title));
  routeLoadingPanel.setAttribute('aria-busy', 'true');
  const status = node('p', 'Loading…', 'sr-only'); status.setAttribute('role', 'status');
  const preview = node('div', undefined, 'loading-preview'); preview.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 4; index++) {
    const row = node('div', undefined, 'loading-preview-row'); row.append(node('span'), node('span')); preview.append(row);
  }
  routeLoadingPanel.append(status, preview);
  showWorkspace('loading');
  document.title = title + ' — Crucible';
}
function routeNeedsCatalogue() {
  const path = window.location.hash.split('?')[0];
  if (['#about', '#people', '#connect'].includes(path)) return false;
  // Profile records still need the catalogue's mapping from native parameters
  // to human stage labels. Keep that dependency until the record API supplies it.
  // Notebook visualizations load their small release-link index independently.
  if (path === '#notebook') return false;
  return true;
}
async function loadAuthorizedWorkspace() {
  if (!hasAccess()) return false;
  state.ready = true;
  state.protectedLoaded = true;
  if (routeNeedsCatalogue() && !state.catalogueLoaded) {
    showRouteLoading();
    const catalogue = loadAuthorizedCatalogue();
    // Deep links can overlap the two independent reads. The response revision
    // must match navigation before a complete release can reach a renderer.
    const target = routeRelease();
    primeRouteChallenge(target);
    primeRouteResources(target);
    if (target) void loadRelease(target.id, target.version).catch(() => {});
    const person = window.location.hash.split('?')[0].match(/^#person\/([^/]+)$/);
    if (person) {
      try {
        const handle = decodeURIComponent(person[1]);
        if (handle && handle.length <= 100) primeRead('person:' + handle.toLowerCase(), '/api/people/' + encodeURIComponent(handle) + '?include_milestones=false');
      } catch { /* Invalid routes do not prefetch another account. */ }
    }
    return catalogue;
  }
  applyRoute();
  return true;
}
function reviewerRoute(section = 'publication', values = {}) {
  return '#reviews?' + new URLSearchParams({section, ...values});
}
function proposalPageActive() {
  return state.screen === 'propose' || (state.screen === 'reviews' && state.reviewSection === 'schemes' && state.canReviewProposals);
}
function renderReviewAccountLink() {
  $('#profile-review-link').hidden = !ownsViewedProfile() || !state.canReviewProposals || !hasAccess();
  renderAccountSignIn();
}
function renderReviewPage(query) {
  const params = new URLSearchParams(query);
  const allowed = hasAccess() && Boolean(state.user) && state.canReviewProposals;
  $('#review-account-back').href = state.user ? personRoute(state.user.handle) : '#people';
  $('#review-navigation').hidden = !allowed;
  $('#review-access-message').hidden = allowed;
  for (const section of ['publication', 'schemes', 'research']) {
    const link = $('#review-nav-' + section), selected = state.reviewSection === section;
    if (selected) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  $('#review-notebook-workspace').hidden = !allowed || state.reviewSection === 'schemes';
  $('#proposal-workspace').hidden = !allowed || state.reviewSection !== 'schemes';
  if (!allowed) {
    reviewNotebookWorkspace?.setAccount(null);
    reviewNotebookWorkspace?.hide();
    return;
  }
  if (state.reviewSection === 'schemes') {
    params.set('review', '1');
    enterProposalPage(params, true);
  } else {
    if (state.reviewSection === 'publication') {
      params.set('review', '1');
      for (const key of ['research', 'research_review', 'research_request']) params.delete(key);
    } else {
      params.set('research', '1'); params.set('research_review', '1');
      for (const key of ['review', 'note', 'revision']) params.delete(key);
    }
    showNotebook(params, true);
  }
}
function loadNotebookAsset(path, stylesheet = false) {
  if (notebookAssetLoads.has(path)) return notebookAssetLoads.get(path);
  const attempt = (notebookAssetAttempts.get(path) || 0) + 1;
  notebookAssetAttempts.set(path, attempt);
  // A removed element may leave its request stalled. A retry needs a distinct
  // URL to avoid sharing that browser request; admission still uses this path.
  const url = path + (attempt > 1 ? '?asset_attempt=' + attempt : '');
  const element = node(stylesheet ? 'link' : 'script');
  if (stylesheet) { element.rel = 'stylesheet'; element.href = url; }
  else { element.async = true; element.src = url; }
  const task = new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(new Error('The notebook took too long to load. Please try again.')), 15000);
    function finish(error) {
      if (settled) return;
      settled = true; clearTimeout(timer); element.onload = element.onerror = null;
      if (error) {
        element.remove();
        if (notebookAssetLoads.get(path) === task) notebookAssetLoads.delete(path);
        reject(error);
      } else resolve();
    }
    element.onload = () => finish();
    element.onerror = () => finish(new Error('The notebook could not be loaded. Please try again.'));
  });
  notebookAssetLoads.set(path, task);
  (stylesheet ? document.querySelector('head') : document.body).append(element);
  return task;
}
function ensureNotebookAssets() {
  if (!hasAccess()) return Promise.reject(new Error('Sign in to open the notebook.'));
  if (window.CrucibleNotebook && (!notebookAssetLoads.size || notebookAssetsReady)) return Promise.resolve();
  return notebookAssets ||= (async () => {
      // These scripts define independent globals; dependencies are read only
      // after this readiness barrier. Independent execution also lets a retry
      // succeed while a removed, timed-out script request is still stalled.
      const results = await Promise.allSettled([
        loadNotebookAsset('/notebook/notebook.css', true),
        ...['/notebook/seed.js', '/notebook/store.js', '/notebook/notebook.js'].map(src => loadNotebookAsset(src))
      ]);
      const failed = results.find(result => result.status === 'rejected');
      if (failed) throw failed.reason;
      if (!hasAccess()) throw new Error('Sign in to open the notebook.');
      notebookAssetsReady = true;
    })().catch(error => { notebookAssets = null; throw error; });
}
function notebookAPI(path, body, options) {
  const early = notebookEarlyReads.get(path);
  if (early && body === undefined && (!options?.method || options.method === 'GET')) {
    notebookEarlyReads.delete(path);
    if (hasAccess() && early.epoch === readViewEpoch && Date.now() - early.at < 10000) return early.task;
  }
  return api(path, body, {...options, readRecovery: true});
}
function getNotebookWorkspace(reviewing = false) {
  const target = $(reviewing ? '#review-notebook-workspace' : '#notebook-workspace');
  const options = {navigate, api: reviewing ? api : notebookAPI,
    account: () => hasAccess() && (!reviewing || state.canReviewProposals) ? state.user : null,
    prepareMapCatalogue: loadMapCatalogue,
    currentReleases: () => hasAccess() ? (state.catalogueLoaded ? state.schemes : mapReleases || []) : [],
    canWarm: () => browseWarmAllowed() && !protectedRequests.size &&
      ![...document.querySelectorAll('dialog')].some(dialog => dialog.open) && !state.issueBusy && !state.submitBusy,
    session: () => state.csrf, reviewOnly: reviewing};
  // mount creates static controls only. Background jobs never call .show(),
  // render private lists, draw a hidden graph or load individual note bodies.
  if (reviewing) return reviewNotebookWorkspace ||= window.CrucibleNotebook.mount(target, options);
  return notebookWorkspace ||= window.CrucibleNotebook.mount(target, options);
}
function notebookPendingFrame(params) {
  const frame = $('#notebook-frame-template').cloneNode(true);
  frame.removeAttribute('id'); frame.removeAttribute('data-pending-layout');
  frame.classList.remove('pending-page-preview'); frame.classList.add('notebook-shell-preview');
  const view = ['map', 'timeline'].includes(params.get('view')) ? params.get('view') : 'notes';
  frame.setAttribute('data-notebook-view', view);
  for (const tab of frame.querySelectorAll('[data-notebook-tab]')) {
    if (tab.getAttribute('data-notebook-tab') === view) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }
  return frame;
}
async function showNotebook(query, reviewing = false) {
  const account = state.user?.id, csrf = state.csrf, section = state.reviewSection, request = ++notebookShowRequest, lifecycle = documentEpoch;
  const params = new URLSearchParams(query || window.location.hash.split('?')[1] || '');
  const target = $(reviewing ? '#review-notebook-workspace' : '#notebook-workspace');
  const current = () => ownsDocument(lifecycle) && request === notebookShowRequest && hasAccess() && account === state.user?.id && csrf === state.csrf &&
    (reviewing ? state.screen === 'reviews' && state.canReviewProposals && state.reviewSection === section && section !== 'schemes' : state.screen === 'notebook');
  if (!current()) return;
  // Resolve the tiny action-link index alongside notebook assets, rather than
  // competing with the map's six assets after its controller becomes available.
  // The graph can still render if this independent, session-bound read fails.
  if (!reviewing && params.get('view') === 'map') void loadMapCatalogue().catch(() => {});
  // These two cold views have a read known before notebook code arrives.
  // Fetch it alongside the scripts, then hand the same authorized response to
  // the controller. Other filters/readers still choose their own queries.
  const firstPath = !params.size ? '/api/notebook/notes?limit=25&include_curated=true&scope=all' :
    params.size === 1 && params.get('view') === 'timeline' ? '/api/notebook/timeline' : null;
  if (!reviewing && !notebookWorkspace && account != null && firstPath && !notebookEarlyReads.has(firstPath)) {
    const entry = {epoch: readViewEpoch, at: Date.now(), task: readAgain('notebook:first:' + firstPath, firstPath)};
    notebookEarlyReads.set(firstPath, entry);
    // Do not hand a completed failure to a later visible reader.
    entry.task.catch(() => { if (notebookEarlyReads.get(firstPath) === entry) notebookEarlyReads.delete(firstPath); });
  }
  if (!window.CrucibleNotebook || (!notebookAssetsReady && notebookAssetLoads.size)) {
    if (reviewing) setLoading(target, 'Loading review workspace…');
    else if (params.has('note') || params.has('research_request') || params.has('research')) {
      // A reader's private title and body remain unknown until its own read.
      const shell = node('div', undefined, 'notebook-shell-preview');
      shell.append(node('h1', 'Research notebook'));
      const frame = node('div', undefined, 'loading-preview');
      frame.setAttribute('aria-hidden', 'true');
      for (let index = 0; index < 4; index++) {
        const row = node('div', undefined, 'loading-preview-row'); row.append(node('span'), node('span')); frame.append(row);
      }
      shell.append(frame);
      quietReadStatus(target, 'Loading notebook…'); target.append(shell);
    } else {
      // The same public frame is used before access resolves and while the
      // admitted controller arrives. It contains no results or enabled actions.
      quietReadStatus(target, 'Loading notebook…'); target.append(notebookPendingFrame(params));
    }
    try { await ensureNotebookAssets(); }
    catch (error) { if (current()) showInlineError(target, error.message, () => showNotebook(query, reviewing)); return; }
  }
  if (!current()) return;
  await getNotebookWorkspace(reviewing).show(params);
  if (current()) scheduleBrowseWarmup();
}
$('#access-sign-in').addEventListener('click', () => { setAuthMode('login'); openAuth($('#access-sign-in')); $('#auth-password-options').open = true; });
$('#access-register').addEventListener('click', () => { setAuthMode('register'); openAuth($('#access-register')); });
$('#access-send-code').addEventListener('click', async () => {
  if (!state.user || !state.invitationToken || state.invitationBusy) return;
  const account = state.user.id, token = state.invitationToken;
  state.invitationBusy = true; setError('#access-error', ''); renderAccessGate();
  try {
    const data = await api('/api/access/invitations/verify/start', {token});
    if (state.user?.id !== account || state.invitationToken !== token) return;
    state.invitationDelivery = data;
    $('#access-code').value = '';
  } catch (error) { if (state.user?.id === account) setError('#access-error', error.message); }
  finally {
    state.invitationBusy = false;
    if (!hasAccess() || state.screen === 'access') { renderAccessGate(); if (state.invitationDelivery) $('#access-code').focus(); }
  }
});
$('#access-code-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.user || !state.invitationToken || state.invitationBusy) return;
  const account = state.user.id, token = state.invitationToken;
  state.invitationBusy = true; setError('#access-error', ''); renderAccessGate();
  try {
    await api('/api/access/invitations/redeem', {token, code: $('#access-code').value.trim()});
    if (state.user?.id !== account || state.invitationToken !== token) return;
    const session = await api('/api/session');
    if (state.user?.id !== account) return;
    setSession(session);
    if (!hasAccess()) throw new Error('Your access is not active yet. Please try again.');
    state.invitationToken = null; state.invitationDelivery = null; $('#access-code').value = '';
    window.history.replaceState(null, '', state.returnRoute || '#challenge');
    await loadAuthorizedWorkspace();
  } catch (error) { if (state.user?.id === account) setError('#access-error', error.message); }
  finally { state.invitationBusy = false; if (!hasAccess() || state.screen === 'access') renderAccessGate(); }
});
$('#access-continue').addEventListener('click', async () => {
  state.invitationToken = null; state.invitationDelivery = null;
  window.history.replaceState(null, '', state.returnRoute || '#challenge');
  try { await loadAuthorizedWorkspace(); }
  catch (error) { globalError(error.message); }
});
function workspaceSelector(screen) {
  return {loading: '#route-loading', access: '#access-workspace', scheme: '#workspace', overview: '#overview-workspace', assumptions: '#assumptions-workspace', notebook: '#notebook-workspace', reviews: '#review-workspace', research: '#research-workspace', about: '#about-workspace', propose: '#proposal-workspace', people: '#people-workspace', person: '#person-workspace', connect: '#connect-workspace'}[screen] || '#catalogue-workspace';
}
function applyRoute(focus = false) {
  if (documentRetired) return;
  const routeRequest = ++workspaceLoadRequest, lifecycle = documentEpoch;
  if (authenticationRoute() && state.sessionResolved) { void handleAuthenticationRoute().catch(error => globalError(error.message)); return; }
  captureInvitation();
  if (state.sessionResolved && (!hasAccess() || window.location.hash.split('?')[0] === '#join')) {
    renderAccessGate();
    if (focus) $('#access-workspace').focus();
    return;
  }
  if (!state.ready) return;
  if (routeNeedsCatalogue() && !state.catalogueLoaded) {
    void loadAuthorizedWorkspace().catch(error => {
      if (ownsDocument(lifecycle) && hasAccess() && routeRequest === workspaceLoadRequest) showInlineError(routeLoadingTarget(), error.message, initialize);
    });
    return;
  }
  const targetRelease = routeRelease();
  const knownRelease = targetRelease && availableReleases().some(scheme => scheme.id === targetRelease.id && String(scheme.version) === targetRelease.version);
  if (knownRelease && !completeRelease(targetRelease.id, targetRelease.version)) {
    const route = window.location.hash, account = state.user?.id, csrf = state.csrf;
    const current = () => ownsDocument(lifecycle) && hasAccess() && routeRequest === workspaceLoadRequest && window.location.hash === route && account === state.user?.id && csrf === state.csrf;
    showRouteLoading();
    primeRouteChallenge(targetRelease);
    primeRouteResources(targetRelease);
    void loadRelease(targetRelease.id, targetRelease.version).then(loaded => {
      if (loaded && current()) applyRoute(focus);
    }).catch(error => {
      if (current()) showInlineError(routeLoadingPanel, error.message, () => { state.catalogueLoaded = false; void loadAuthorizedWorkspace().catch(error => globalError(error.message)); });
    });
    return;
  }
  const previous = selectionKey();
  const previousScreen = state.screen;
  const previousView = state.view;
  let hash = window.location.hash || '#challenge';
  if (!RESEARCH_ENABLED && hash.split('?')[0] === '#research') {
    hash = '#challenge';
    window.history.replaceState(null, '', hash);
  }
  let [path, query = ''] = hash.split('?');
  const legacyParams = new URLSearchParams(query);
  if ((path === '#propose' && legacyParams.get('review') === '1') ||
      (path === '#notebook' && (legacyParams.get('review') === '1' || legacyParams.get('research_review') === '1'))) {
    const section = path === '#propose' ? 'schemes' : legacyParams.get('research_review') === '1' ? 'research' : 'publication';
    legacyParams.delete('review'); legacyParams.delete('research'); legacyParams.delete('research_review');
    legacyParams.set('section', section); path = '#reviews'; query = legacyParams.toString();
    window.history.replaceState(null, '', path + '?' + query);
  }
  const reviewParams = new URLSearchParams(query);
  if (path === '#reviews') {
    const section = reviewParams.get('section');
    state.reviewSection = ['publication', 'schemes', 'research'].includes(section) ? section : 'publication';
    if (section !== state.reviewSection) { reviewParams.set('section', state.reviewSection); query = reviewParams.toString(); window.history.replaceState(null, '', '#reviews?' + query); }
  }
  let scheme, profile, handle, view = 'leaderboard';
  let screen = path === '#connect' ? 'connect' : path === '#people' ? 'people' : path === '#propose' ? 'propose' : path === '#reviews' ? 'reviews' : path === '#about' ? 'about' : path === '#notebook' ? 'notebook' : path === '#research' ? 'research' : 'catalogue';
  let invalid = !['#challenge', '#notebook', '#research', '#about', '#propose', '#reviews', '#people', '#connect'].includes(path);
  const personMatch = path.match(/^#person\/([^/]+)$/);
  if (personMatch) {
    try {
      handle = decodeURIComponent(personMatch[1]);
      if (handle && handle.length <= 100) { screen = 'person'; invalid = false; }
    } catch { /* Malformed handles do not select another profile. */ }
  }
  const match = path.match(/^#challenge\/([^/]+)\/([^/]+)$/);
  if (match) {
    try {
      const id = decodeURIComponent(match[1]);
      const version = decodeURIComponent(match[2]);
      scheme = completeRelease(id, version);
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
  const overviewMatch = path.match(/^#scheme\/([^/]+)\/([^/]+)(\/assumptions)?$/);
  if (overviewMatch) {
    try {
      const id = decodeURIComponent(overviewMatch[1]);
      const version = decodeURIComponent(overviewMatch[2]);
      scheme = completeRelease(id, version);
      const params = new URLSearchParams(query);
      profile = scheme?.profiles?.find(item => profileId(item) === params.get('profile'));
      if (scheme && (!params.has('profile') || profile)) { screen = overviewMatch[3] ? 'assumptions' : 'overview'; invalid = false; }
    } catch { /* Unknown or malformed releases fail closed to the catalogue. */ }
  }
  state.screen = screen;
  state.personHandle = screen === 'person' ? handle : null;
  state.peopleRequest++;
  state.personRequest++;
  state.connectRequest++;
  state.connection = null;
  state.connectBusy = false;
  state.connectCode = screen === 'connect' ? connectionCode(new URLSearchParams(query).get('code')) : null;
  resetAgentSettings();
  state.schemeId = ['scheme', 'overview', 'assumptions'].includes(screen) ? scheme.id : null;
  state.schemeVersion = ['scheme', 'overview', 'assumptions'].includes(screen) ? String(scheme.version) : null;
  state.profile = screen === 'scheme' ? profile : null;
  if (state.challengeStartIntent && !challengeStartSelectionMatches(state.challengeStartIntent, view)) state.challengeStartIntent = null;
  const changed = previous !== selectionKey();
  if (screen !== 'scheme' || changed) resetAgentInstructions();
  if (changed) {
    state.challengeRequest++;
    state.leaderboardRequest++;
    state.challenge = null;
    state.generationAdmission = null;
    state.challengeLoading = false;
    $('#challenge-error').hidden = true;
    $('#leaderboard-content').replaceChildren();
  }
  showWorkspace(screen);
  if (['propose', 'reviews'].includes(previousScreen) && (screen !== previousScreen || screen === 'reviews')) leaveProposalPage();
  if (screen === 'reviews') renderReviewPage(query);
  if (screen === 'propose') enterProposalPage(new URLSearchParams(query), previousScreen !== 'propose');
  const routeError = 'That KEM, version, or profile is unavailable. Choose a published KEM below.';
  if (invalid) globalError(routeError);
  else if ($('#global-message-text').textContent === routeError) globalError('');
  if (screen === 'scheme') {
    $('#scheme-back').href = overviewRoute(scheme, profile);
    renderView(view);
    renderProfiles(null, {details: changed || previousScreen !== 'scheme' || (view === 'protocol' && previousView !== view)});
    if (view === 'challenge' && (changed || previousScreen !== 'scheme' || previousView !== view)) loadChallenge();
    if (view === 'leaderboard' && (changed || previousView !== view || previousScreen !== 'scheme')) loadLeaderboard();
  }
  if (screen === 'catalogue') renderCatalogue();
  if (screen === 'overview') renderOverview(scheme, profile);
  if (screen === 'assumptions') renderAssumptions(scheme, profile, new URLSearchParams(query));
  if (screen === 'people') loadPeople();
  if (screen === 'person') loadPerson(handle);
  if (screen === 'connect') loadConnection();
  if (screen === 'notebook') {
    showNotebook(query);
  }
  document.title = ['scheme', 'overview'].includes(screen) ? scheme.name + (screen === 'overview' ? ' overview' : '') + ' — Crucible' : screen === 'person' ? handle + ' — Crucible' : screen === 'people' ? 'Leaderboard — Crucible' : screen === 'research' ? 'Research — Crucible' : screen === 'about' ? 'About us — Crucible' : screen === 'propose' ? 'Propose a scheme — Crucible' : 'Schemes — Crucible';
  if (screen === 'assumptions') document.title = `Assumptions · ${scheme.name} — Crucible`;
  if (screen === 'connect') document.title = 'Connect an agent — Crucible';
  if (screen === 'notebook') document.title = 'Research notebook — Crucible';
  if (screen === 'reviews') document.title = 'Review center — Crucible';
  renderReviewAccountLink();
  if (focus && previousScreen !== screen) $(workspaceSelector(screen)).focus({preventScroll: ['catalogue', 'people'].includes(screen)});
  scheduleBrowseWarmup();
}
function sharedKeyBytes(challenge) {
  const bytes = challenge?.projection === 'summary' ? challenge.shared_secret_bytes : challenge?.public?.encoding?.shared_secret_bytes;
  return Number.isInteger(bytes) && bytes > 0 && bytes <= 4096 ? bytes : null;
}
function roundRemaining() { return state.round ? Math.max(0, Math.ceil(state.round.ends_at - now())) : null; }
function absorbClock(data) {
  // A delayed response from the previous hour must not reactivate its round.
  if (data?.round && state.round && data.round.id < state.round.id) return;
  if (Number.isFinite(Number(data?.server_time))) state.timeOffset = Number(data.server_time) * 1000 - Date.now();
  if (data?.round) {
    if (state.round?.id !== data.round.id) {
      state.challengeStartIntent = null;
    }
    state.round = data.round;
  }
  updateClock();
}
async function api(path, body, {raw = false, method = body === undefined ? 'GET' : 'POST', initialSession = false, readRecovery = false} = {}) {
  const lifecycle = documentEpoch, sessionOwner = state.sessionRevision, accountOwner = state.user?.id, csrfOwner = state.csrf;
  const protectedRequest = !/^\/api\/(?:session(?:\?|$)|auth\/|access\/)/.test(path);
  const recoverRead = !raw && body === undefined && method === 'GET' &&
    (initialSession && path === '/api/session' || readRecovery && protectedRequest && !path.startsWith('/api/agent/'));
  const ownsRead = () => ownsDocument(lifecycle) && (!protectedRequest || hasAccess()) && (initialSession ? sessionOwner === state.sessionRevision :
    accountOwner === state.user?.id && csrfOwner === state.csrf);
  if (protectedRequest && !hasAccess()) {
    const error = new Error('Sign in with an active beta membership to continue.');
    error.status = 403;
    throw error;
  }
  let bootstrap;
  if (initialSession && path === '/api/session' && body === undefined && method === 'GET') {
    bootstrap = window.CrucibleSessionBootstrap?.response ? window.CrucibleSessionBootstrap : null;
    // Mark this before fetching if app.js wins the race. A late bootstrap
    // download must not launch another session request with an older lifecycle.
    window.CrucibleSessionBootstrap = {consumed: true};
  }
  const controller = bootstrap?.controller || new AbortController();
  if (protectedRequest) protectedRequests.add(controller);
  if (recoverRead) browsingRequests.add(controller);
  const timeout = bootstrap ? null : setTimeout(() => controller.abort(), body !== undefined && path.split('?')[0] === '/api/challenges' ? 30000 : 20000);
  try {
    let response;
    const fetchResponse = () => fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: body === undefined ? {Accept: 'application/json'} : {
        Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf
      },
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    try {
      try { response = await (bootstrap?.response || fetchResponse()); }
      catch (failure) {
        // Stop-loading and transient network loss have the same browser error.
        // Only explicitly selected browsing reads recover once, under their original deadline;
        // neither a server response nor a write is ever retried here.
        if (!recoverRead || failure?.name !== 'TypeError' || controller.signal.aborted || !ownsRead()) throw failure;
        response = await fetchResponse();
      }
    } catch {
      const error = new Error(controller.signal.aborted ? 'The server took too long to respond. Please try again.' : 'The server could not be reached. Check your connection and try again.');
      error.status = 0;
      error.code = controller.signal.aborted ? 'response_timeout' : 'network_unreachable';
      throw error;
    }
    let data;
    const bodyInUse = response.bodyUsed === true || response.body?.locked === true;
    try { data = raw && response.ok ? await response.text() : await response.json(); } catch (failure) {
      if (controller.signal.aborted || response.ok) {
        // Fetch can resolve at the headers and fail later while reading its
        // body. Distinguish that interruption from bad JSON or a service page;
        // never log the body or replay a request whose outcome may be unknown.
        const interrupted = !bodyInUse && ['TypeError', 'AbortError'].includes(failure?.name);
        const html = /^text\/html(?:\s*;|\s*$)/i.test(response.headers?.get?.('Content-Type') || '');
        const error = new Error(controller.signal.aborted ? 'The server took too long to respond. Please try again.' :
          interrupted ? 'The connection was interrupted before the response finished. Please try again.' :
          html && !bodyInUse ? 'The server returned an unexpected page. Please try again.' :
          'The server returned an unreadable response. Please try again.');
        error.status = 0;
        error.code = controller.signal.aborted ? 'response_timeout' : interrupted ? 'response_interrupted' :
          bodyInUse ? 'response_already_read' : html ? (response.redirected ? 'unexpected_response_redirect' : 'unexpected_response_page') :
          failure?.name === 'SyntaxError' ? 'invalid_json_response' : 'response_unreadable';
        throw error;
      }
      data = {};
    }
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : null;
      const fallback = response.status === 429 ? 'Too many requests. Wait a moment and try again.' :
        response.status === 401 ? 'Your session has ended. Sign in again to continue.' :
        response.status === 403 ? 'This request could not be authorized. Refresh your session and try again.' :
        'The request could not be completed. Please try again.';
      const error = new Error(detail || fallback);
      error.status = response.status;
      if (path === '/api/me/profile' && ['profile_revision_conflict', 'profile_idempotency_conflict'].includes(data?.code)) error.code = data.code;
      if ((protectedRequest || path.startsWith('/api/access/')) && state.access?.mode === 'beta' && [401, 403].includes(response.status)) {
        // Recheck membership without making a rejected child request await its parent refresh.
        void synchronizeSession();
      }
      if (Array.isArray(data?.errors)) error.fieldErrors = data.errors.filter(item => item && typeof item.path === 'string' && typeof item.message === 'string');
      throw error;
    }
    if (protectedRequest && !hasAccess()) {
      const error = new Error('Beta access is no longer active.'); error.status = 403; throw error;
    }
    // This exact format check creates no proposal or note; other writes retain
    // conservative invalidation, including unknown paths and methods.
    const readOnlyValidation = method === 'POST' && path === '/api/protocol/proposals/validate';
    if (protectedRequest && !['GET', 'HEAD'].includes(method) && !readOnlyValidation) {
      // These exact actions change participant content/progress, never the
      // separately published measurement corpus. Unknown writes still clear it.
      const unrelatedToResources = method === 'PUT' && path === '/api/me/profile' || method === 'POST' && (
        ['/api/notebook/notes', '/api/notebook/import', '/api/challenges', '/api/methods'].includes(path) ||
        /^\/api\/notebook\/notes\/[^/?]+\/(?:revisions|publication-requests|publication-reviews)$/.test(path) ||
        /^\/api\/challenges\/[^/?]+\/submit$/.test(path));
      invalidateReadViews({resourceReports: !unrelatedToResources}); notebookWorkspace?.invalidateReads?.(); reviewNotebookWorkspace?.invalidateReads?.();
    }
    if (!raw && ownsDocument(lifecycle) && (!initialSession || sessionOwner === state.sessionRevision) && (!recoverRead || ownsRead())) absorbClock(data);
    return data;
  } finally {
    if (bootstrap) bootstrap.finish(); else clearTimeout(timeout);
    protectedRequests.delete(controller);
    browsingRequests.delete(controller);
  }
}
function setSession(data) {
  document.body.removeAttribute('data-access-pending');
  $('.primary-nav').removeAttribute('inert');
  state.sessionRevision++;
  const previousId = state.user?.id;
  const previousCsrf = state.csrf;
  const previousReview = state.canReviewProposals;
  const previousSelfReview = state.canSelfReviewProposals;
  state.user = data.user || null;
  state.auth = data.auth || {};
  state.csrf = data.csrf_token || '';
  if (state.authPanel === 'github-link' && state.githubLinkContext && (state.githubLinkContext.accountId !== state.user?.id || state.githubLinkContext.csrf !== state.csrf)) {
    state.githubLinkContext = null; $('#github-link-password').value = '';
    state.authPanel = 'signin'; if (authDialog.open) authDialog.close();
  }
  state.sessionResolved = true;
  state.access = data.access || (state.access?.mode === 'beta' ?
    {mode: 'beta', allowed: false, status: state.user ? 'pending' : 'anonymous'} :
    {mode: 'open', allowed: true, status: state.user ? 'active' : 'anonymous'});
  // Presentation only: the next reload can keep the gate or route frame in
  // place, but every reload still waits for its fresh session before any data.
  document.body.setAttribute('data-access-view', hasAccess() ? 'workspace' : 'gate');
  window.CruciblePendingPage?.remember(hasAccess());
  state.canManageBetaAccess = Boolean(state.user && data.capabilities?.manage_beta_access);
  state.canReviewProposals = Boolean(state.user && data.capabilities?.review_protocol_proposals);
  state.canSelfReviewProposals = Boolean(state.canReviewProposals && data.capabilities?.self_review_protocol_proposals);
  if (previousId !== state.user?.id || !hasAccess()) resetResearcherEditor();
  else if (previousCsrf !== state.csrf) suspendResearcherEditor();
  notebookWorkspace?.setAccount(hasAccess() ? state.user : null);
  reviewNotebookWorkspace?.setAccount(hasAccess() && state.canReviewProposals ? state.user : null);
  if (previousId !== state.user?.id || previousCsrf !== state.csrf) {
    state.challengeStartIntent = null;
    invalidateReadViews();
    notebookWorkspace?.invalidateReads?.(); reviewNotebookWorkspace?.invalidateReads?.();
    // An in-flight catalogue belongs to this browser session. A changed account
    // or rotated session must be able to start a fresh read immediately.
    catalogueLoad = null;
    mapCatalogueLoad = null; mapReleases = null;
    state.catalogueRequest++;
    releaseDetails.clear(); releaseLoads.clear(); releaseGeneration++;
    if (navigationProjection) state.catalogueLoaded = false;
  }
  if (previousId !== state.user?.id) {
    referenceTimings.clear(); window.CrucibleResources?.clear();
    assumptionsWorkspace?.clear();
    state.invitationDelivery = null;
    $('#access-code').value = '';
    state.challengeStartIntent = null;
    state.connectRequest++;
    state.connection = null;
    state.connectBusy = false;
    resetAgentSettings();
    resetAgentInstructions();
    resetProposalAccount(previousId);
    state.historyRequest++;
    state.recoveryRequest++;
    state.challenge = null; state.generationAdmission = null;
    state.challengeRequest++;
    state.leaderboardRequest++;
    state.methods = [];
    state.pendingAttempt = null;
    state.submissionChallenge = null;
    state.recoveryImportRequest++;
    state.importBusy = false;
    state.submissionFormRevision++;
    resetSubmissionForm();
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
  if (previousId === state.user?.id && (previousReview !== state.canReviewProposals || previousSelfReview !== state.canSelfReviewProposals)) {
    invalidateReadViews(); notebookWorkspace?.invalidateReads?.(); reviewNotebookWorkspace?.invalidateReads?.();
    resetProposalAccount(previousId);
  }
  $('#auth-button').hidden = Boolean(state.user);
  $('#auth-button').disabled = false;
  $('#account-handle').hidden = !state.user || !hasAccess();
  setText('#account-handle', state.user?.handle || '');
  $('#account-handle').href = state.user ? personRoute(state.user.handle) : '#people';
  $('#logout-button').hidden = !state.user;
  $('#history-button').hidden = !state.user || !hasAccess();
  $('#register-mode').hidden = state.access.mode === 'beta' && !state.invitationToken;
  renderAuthMethods();
  if (!hasAccess()) {
    clearProtectedState();
    renderAccessGate();
    return;
  }
  absorbClock(data);
  renderReviewAccountLink();
  if (state.screen === 'reviews' && state.ready && (previousId !== state.user?.id || previousCsrf !== state.csrf || previousReview !== state.canReviewProposals || previousSelfReview !== state.canSelfReviewProposals)) renderReviewPage(window.location.hash.split('?')[1] || '');
  if (previousId !== state.user?.id && state.ready) {
    if (state.screen === 'connect') loadConnection();
    if (state.screen === 'person') renderAgentSettings();
    if (state.screen === 'notebook') showNotebook();
  }
  if (previousId === state.user?.id && previousCsrf !== state.csrf && state.ready && state.screen === 'notebook') showNotebook();
  // Session changes invalidate the old reader's epoch. An independent People
  // page must start its replacement itself; catalogue routing will not do it.
  if (state.ready && state.screen === 'people' && (previousId !== state.user?.id || previousCsrf !== state.csrf ||
      previousReview !== state.canReviewProposals || previousSelfReview !== state.canSelfReviewProposals)) loadPeople();
  if (state.ready && navigationProjection && !state.catalogueLoaded && routeNeedsCatalogue()) applyRoute();
  scheduleBrowseWarmup();
}
function globalError(message) {
  setText('#global-message-text', message);
  $('#global-message').hidden = !message;
}
function setLoading(target, text) {
  const content = node('div', undefined, 'loading-state');
  content.setAttribute('role', 'status');
  content.append(node('span', text, 'sr-only'));
  target.replaceChildren(content);
}
function quietReadStatus(target, text) {
  const status = node('span', text, 'sr-only'); status.setAttribute('role', 'status');
  target.replaceChildren(status);
}
function showInlineError(target, message, retry) {
  target.setAttribute('aria-busy', 'false');
  if (target === $('#kem-catalogue')) $('#refresh-catalogue').disabled = false;
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

function renderRoundCountdowns(remaining) {
  const known = Number.isFinite(remaining) && remaining >= 0;
  const clock = known ? String(Math.floor(remaining / 60)).padStart(2, '0') + ':' + String(remaining % 60).padStart(2, '0') : '—:—';
  for (const [selector, deadline] of [['#countdown', 'this challenge closes'], ['#overview-countdown', 'the shared round ends']]) {
    const target = $(selector);
    target.textContent = clock;
    target.setAttribute('aria-label', known ? `${Math.floor(remaining / 60)} minutes and ${remaining % 60} seconds until ${deadline}` : 'Round time unavailable');
    target.classList.toggle('urgent', known && remaining < 60);
  }
  return clock;
}
function renderOverviewClock(scheme = selectedScheme(), remaining = roundRemaining()) {
  const current = state.schemes.find(item => item.id === scheme?.id);
  $('#overview-clock').hidden = !hasAccess() || !Number.isFinite(remaining) || state.screen !== 'overview' ||
    !current || String(current.version) !== String(scheme.version) ||
    $('#overview-challenges').hidden || generationUnavailable(scheme);
}
function updateClock() {
  const remaining = hasAccess() ? roundRemaining() : null;
  renderRoundCountdowns(remaining);
  renderOverviewClock(undefined, remaining);
  if (!hasAccess()) return;
  updateConnectionExpiry();
  const deadline = state.submissionChallenge?.ends_at;
  const formRemaining = Number.isFinite(deadline) ? Math.max(0, Math.ceil(deadline - now())) : null;
  const formExpired = formRemaining === 0;
  const formClock = formRemaining === null ? '' : String(Math.floor(formRemaining / 60)).padStart(2, '0') + ':' + String(formRemaining % 60).padStart(2, '0');
  setText('#submission-time', formRemaining === null ? '' : formExpired
    ? state.pendingAttempt ? 'This instance has closed. You can still check the original answer’s receipt.' : 'This instance has closed.'
    : formClock + ' until this instance closes');
  updateAgentAction();
  $('#submit-answer').disabled = state.submitBusy || state.importBusy || Boolean(formExpired && !state.pendingAttempt) || sharedKeyBytes(state.submissionChallenge) === null;
  updateRecoveryControls();
  if (formExpired && submissionDialog.open && !state.submitBusy && !state.pendingAttempt) {
    setError('#submission-error', 'This instance has closed. Your draft is kept in this tab; close the form to choose a new instance.');
  }
  $('#open-submission').disabled = !state.ready || state.submitBusy || state.importBusy || state.challengeLoading;
  if (remaining === null) return;
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
  if (documentRetired || state.roundRefreshing || !hasAccess()) return;
  state.roundRefreshing = true;
  const oldRound = state.round?.id;
  let sessionRevision = state.sessionRevision;
  const lifecycle = documentEpoch;
  const current = () => ownsDocument(lifecycle) && state.sessionRevision === sessionRevision;
  try {
    const data = await api('/api/session');
    if (!current()) return;
    setSession(data);
    sessionRevision = state.sessionRevision;
    if (!hasAccess()) return;
    if (state.round?.id !== oldRound || (state.challenge && state.challenge.round_id !== state.round.id)) {
      await loadChallenge();
      if (state.view === 'leaderboard') await loadLeaderboard();
      if (current()) toast('A new round has begun. Request a fresh instance when you are ready.');
    }
  } catch (error) {
    if (current()) globalError(error.message + ' The countdown will synchronize when the connection returns.');
  } finally {
    if (ownsDocument(lifecycle)) {
      roundRetryAt = Date.now() + 5000;
      state.roundRefreshing = false;
    }
  }
}
async function synchronizeSession() {
  if (documentRetired || !state.sessionResolved || state.roundRefreshing) return;
  if (sessionSynchronization) return sessionSynchronization;
  const lifecycle = documentEpoch;
  const task = (async () => {
    const oldRound = state.round?.id;
    const oldUser = state.user?.id;
    let revision = state.sessionRevision;
    const current = () => ownsDocument(lifecycle) && revision === state.sessionRevision;
    try {
      const data = await api('/api/session');
      // A login/logout completed after this read began. Its response owns the
      // account state; the older read must not restore the previous account.
      if (!current()) return;
      setSession(data);
      revision = state.sessionRevision;
      if (!hasAccess()) return;
      if (!state.ready || (routeNeedsCatalogue() && !state.catalogueLoaded)) { await loadAuthorizedWorkspace(); return; }
      const changed = oldRound !== state.round?.id || oldUser !== state.user?.id;
      if (['catalogue', 'overview', 'scheme'].includes(state.screen) && await refreshProfiles({background: true}) === false) return;
      if (state.screen === 'person') await loadPerson(state.personHandle, {preserve: true});
      if (!current()) return;
      globalError('');
      if (state.screen === 'scheme') {
        // Do not await a child that may itself request a session refresh on 401.
        if (changed && state.view === 'challenge') loadChallenge();
        else if (state.view === 'leaderboard' && !document.hidden) {
          const key = leaderboardRecoveryKey();
          if (changed || (key !== state.leaderboardDataKey && key !== state.leaderboardPendingKey)) loadLeaderboard({preserve: true});
        }
      }
    } catch (error) { if (current()) globalError(error.message); }
  })();
  sessionSynchronization = task;
  try { await task; }
  finally { if (sessionSynchronization === task) sessionSynchronization = null; }
}

function catalogueDisplayKey(scheme) {
  return JSON.stringify(scheme && [scheme.id, scheme.version, scheme.name, scheme.display_group,
    scheme.participant_count, scheme.largest_recovered_profile_id, scheme.next_profile_id,
    scheme.first_recovery, recoveredProfileIds(scheme), scheme.generation_admission, scheme.recovery_summary]);
}
function mergeCatalogueProgress(data) {
  if (data.projection !== 'progress' || !state.catalogueRevision || data.catalogue_revision !== state.catalogueRevision || !Array.isArray(data.releases)) return null;
  if (data.releases.some(row => !row || typeof row.id !== 'string' || typeof row.version !== 'string')) return null;
  const key = release => JSON.stringify([release.id, release.version]);
  const known = state.schemes.flatMap(scheme => [scheme, ...(scheme.previous_releases || [])]);
  const rows = new Map(data.releases.map(row => [key(row), row]));
  if (rows.size !== data.releases.length || rows.size !== known.length || known.some(release => !rows.has(key(release)))) return null;
  const fields = RELEASE_PROGRESS_FIELDS;
  if (data.releases.some(row => fields.some(field => !['profile_recoveries', 'generation_admission', 'recovery_summary'].includes(field) && !Object.hasOwn(row, field)))) return null;
  const update = release => {
    const result = {...release}, row = rows.get(key(release));
    // Only dynamic fields can replace metadata. In particular, a partial reply
    // cannot supply parameter sets, protocol links or a new release identity.
    for (const field of fields) { if (Object.hasOwn(row, field)) result[field] = row[field]; else if (field !== 'generation_admission') delete result[field]; }
    return result;
  };
  return state.schemes.map(scheme => ({...update(scheme), previous_releases: (scheme.previous_releases || []).map(update)}));
}
async function refreshProfiles({background = false} = {}) {
  if (documentRetired || !hasAccess()) return false;
  const lifecycle = documentEpoch;
  const request = ++state.catalogueRequest;
  const oldRevision = state.catalogueRevision;
  const oldCatalogue = JSON.stringify(state.schemes.map(catalogueDisplayKey));
  const oldSelection = catalogueDisplayKey(selectedScheme());
  const oldAdmission = JSON.stringify(selectedScheme()?.generation_admission);
  const route = window.location.hash;
  let data;
  let merged;
  try {
    data = await api('/api/schemes' + (background && state.catalogueRevision ? '?progress_only=true' : '?projection=navigation'));
    if (!ownsDocument(lifecycle) || request !== state.catalogueRequest) return false;
    if (data.projection === 'progress') {
      merged = mergeCatalogueProgress(data);
      if (!merged) data = await api('/api/schemes?projection=navigation');
    }
  }
  catch (error) {
    if (!ownsDocument(lifecycle) || request !== state.catalogueRequest) return false;
    throw error;
  }
  if (!ownsDocument(lifecycle) || request !== state.catalogueRequest) return false;
  installNavigation(merged ? {schemes: merged, catalogue_revision: data.catalogue_revision, ...(navigationProjection ? {projection: 'navigation'} : {})} : data);
  state.catalogueLoaded = true;
  const metadataChanged = oldRevision !== state.catalogueRevision;
  let scheme = selectedScheme();
  if (['scheme', 'overview', 'assumptions'].includes(state.screen) && !scheme) {
    const target = routeRelease();
    if (target && availableReleases().some(item => item.id === target.id && String(item.version) === target.version)) {
      await loadRelease(target.id, target.version);
      if (!ownsDocument(lifecycle) || request !== state.catalogueRequest || route !== window.location.hash) return false;
      scheme = selectedScheme();
    }
  }
  const selectionChanged = oldSelection !== catalogueDisplayKey(scheme);
  if (oldAdmission !== JSON.stringify(scheme?.generation_admission)) state.generationAdmission = null;
  if (state.screen === 'catalogue' && (!background || metadataChanged || oldCatalogue !== JSON.stringify(state.schemes.map(catalogueDisplayKey)))) renderCatalogue();
  if (['scheme', 'overview', 'assumptions'].includes(state.screen) && !scheme) return applyRoute();
  if (state.screen === 'overview' && (!background || metadataChanged || selectionChanged)) {
    const openCards = Object.keys(OVERVIEW_CARDS).filter(id => $('#overview-chart-' + id)?.querySelector('details')?.open);
    const context = new URLSearchParams(window.location.hash.split('?')[1] || '').get('profile');
    renderOverview(scheme, scheme.profiles.find(profile => profileId(profile) === context));
    for (const id of openCards) { const details = $('#overview-chart-' + id).querySelector('details'); if (details) details.open = true; }
  }
  if (state.screen === 'scheme') {
    const profile = scheme.profiles.find(item => profileId(item) === profileId());
    if (!profile) return applyRoute();
    state.profile = profile;
    renderChallenge();
    renderRecordChallengeAction();
    // Metadata refresh is not navigation. Preserve editors, disclosure state,
    // in-flight connections, and a slider/jump input currently being edited.
    const editing = [$('#size-slider'), $('#profile-select')].includes(document.activeElement);
    if (!background || metadataChanged || selectionChanged || state.profileMetadataPending === selectionKey()) {
      if (editing) {
        state.profileMetadataPending = selectionKey();
        refreshProfileRecoveryStyles(scheme);
      }
      else renderProfiles(null, {details: (!background || metadataChanged || state.profileMetadataPending === selectionKey()) && (state.view === 'protocol' || $('#scheme-specifics').open)});
    }
  }
  return true;
}

function profileBytes(scheme, profile, metric) {
  const value = scheme?.profile_metrics?.[profileId(profile)]?.[metric];
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function participantCount(scheme) {
  const value = scheme?.participant_count;
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}
function catalogueGroup(scheme) {
  if (['number-theory', 'lattices', 'codes'].includes(scheme.display_group)) return scheme.display_group;
  // These established entries predate catalogue groups. Keep this display-only
  // classification separate from their immutable protocol manifests.
  return ['teaching-dh-kem', 'rsa-kem', 'dhkem-x25519'].includes(scheme.id) ? 'number-theory' : null;
}
function prepareSchemeLink(link, scheme) {
  const prepare = () => {
    if (!hasAccess() || !state.catalogueLoaded || navigator.connection?.saveData || releaseLoads.size >= 2 ||
        !availableReleases().some(item => item.id === scheme.id && String(item.version) === String(scheme.version))) return;
    browseWarmIntent = {id: scheme.id, version: String(scheme.version)};
    void prepareResourceReport(scheme);
    void loadRelease(scheme.id, String(scheme.version)).then(scheduleBrowseWarmup, () => {});
  };
  link.addEventListener('pointerenter', prepare); link.addEventListener('focus', prepare);
}
function renderCatalogue() {
  const target = $('#kem-catalogue');
  if (target.getAttribute('aria-busy') === 'true') $('#refresh-catalogue').disabled = false;
  target.setAttribute('aria-busy', 'false');
  setText('#tier-help', 'Stages mark positions in each scheme’s default progression through reviewed configurations. Schemes can have different numbers of stages; equal stage numbers do not imply equal security or attack cost. Named parameter sets are distinct targets; their order does not imply security or transfer recovery results.');
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
      help.setAttribute('aria-label', 'About challenge stages');
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
    if (label === 'Participants' && new Set(state.schemes.map(catalogueGroup)).size === 1) th.setAttribute('aria-sort', 'descending');
    if (label === 'Participants') th.title = 'Distinct accounts with at least one submitted recovery attempt, successful or unsuccessful, including compatible releases.';
    headings.append(th);
  }
  head.append(headings);
  const groups = ['number-theory', null, 'lattices', 'codes'];
  const groupLabels = {'number-theory': 'Number-theoretic', lattices: 'Lattice-based', codes: 'Code-based'};
  const catalogue = [...state.schemes].sort((a, b) =>
    groups.indexOf(catalogueGroup(a)) - groups.indexOf(catalogueGroup(b)) ||
    (participantCount(b) ?? -1) - (participantCount(a) ?? -1) ||
    a.name.localeCompare(b.name, 'en', {sensitivity: 'base'}) || a.id.localeCompare(b.id));
  table.append(head);
  let body, currentGroup;
  for (const scheme of catalogue) {
    const group = catalogueGroup(scheme);
    if (!body || group !== currentGroup) {
      body = node('tbody'); currentGroup = group;
      const row = node('tr', undefined, 'kem-group-row');
      const title = node('th', groupLabels[group] || 'Miscellaneous');
      title.scope = 'rowgroup'; title.colSpan = 4;
      title.append(node('span', String(catalogue.filter(item => catalogueGroup(item) === group).length), 'kem-group-count'));
      row.append(title); body.append(row);
      table.append(body);
    }
    const profiles = scheme.profiles || [];
    const largest = profiles.find(profile => profileId(profile) === scheme.largest_recovered_profile_id);
    const row = node('tr');
    const name = node('td');
    const link = node('a', scheme.name, 'kem-name');
    link.href = overviewRoute(scheme);
    prepareSchemeLink(link, scheme);
    name.append(link);
    const progress = node('td');
    progress.dataset.label = 'Largest recovery';
    if (largest) {
      const record = node('a', profileLabel(largest, scheme), 'kem-progress');
      record.href = schemeRoute(scheme, largest, 'leaderboard');
      prepareSchemeLink(record, scheme);
      record.setAttribute('aria-label', `View recoveries for ${profileLabel(largest, scheme)}`);
      progress.append(record);
    }
    const firstCell = node('td');
    firstCell.dataset.label = 'Recovered by';
    if (largest && scheme.first_recovery?.profile_id === profileId(largest)) {
      const first = node('a', scheme.first_recovery.handle, 'kem-first-handle');
      first.href = personRoute(scheme.first_recovery.handle);
      first.setAttribute('aria-label', `View ${scheme.first_recovery.handle}’s profile`);
      firstCell.append(first);
    } else firstCell.append(node('span', '—', 'kem-meta'));
    const participants = node('td');
    participants.dataset.label = 'Participants';
    const count = participantCount(scheme);
    participants.append(node('span', count === null ? '—' : count.toLocaleString('en-US'), 'kem-participants'));
    row.append(name, progress, firstCell, participants);
    body.append(row);
  }
  wrap.append(table);
  target.replaceChildren(wrap);
}
const AGENT_SCOPE_LABELS = {
  'challenges:read': 'Read your challenge instances',
  'challenges:write': 'Request challenge instances for your account',
  'methods:read': 'Read your saved attack methods and source',
  'methods:write': 'Create attack methods and upload source files',
  'submissions:read': 'Read your submission receipts',
  'submissions:write': 'Submit recovered keys on your behalf',
  'proposals:read': 'Read your private proposals and reviewer feedback',
  'proposals:write': 'Submit your proposals and revisions for human review',
  'notes:read': 'Read published and curated research notes and their reading priorities',
  'notes:rate': 'Rate other authors’ notes on your behalf, influencing reading priorities and author points',
  'notebook:read': 'Read and export your own notebook, including private drafts and feedback',
  'notebook:write': 'Create, import and revise your own private notes; delete and restore unsubmitted drafts',
  'notebook:publish': 'Request publication review of your exact saved note revisions'
};
function agentScopeList(scopes) {
  const list = node('ul', undefined, 'agent-scope-list');
  for (const scope of scopes || []) list.append(node('li', AGENT_SCOPE_LABELS[scope] || scope));
  return list;
}
function renderConnection() {
  const target = $('#connect-content');
  setText('#connect-origin', siteOrigin());
  $('#connect-code-form').hidden = Boolean(state.connectCode);
  if (!state.connectCode) return showEmpty(target, 'Enter the code shown by the agent you want to connect.');
  const code = node('p', state.connectCode, 'connect-code');
  code.setAttribute('aria-label', 'Connection code ' + state.connectCode);
  target.replaceChildren(code);
  if (!state.user) {
    target.append(node('p', 'Sign in to review this connection. Signing in will not connect the agent.', 'connect-copy'));
    const signIn = node('button', 'Sign in', 'button button-primary'); signIn.type = 'button';
    signIn.addEventListener('click', () => openAuth(signIn)); target.append(signIn);
    return;
  }
  const connection = state.connection;
  if (!connection) return;
  const status = connection.status === 'pending' && connection.expires_at <= now() ? 'expired' : connection.status;
  if (status !== 'pending') {
    const messages = {approved: 'Connection approved. Return to your agent to finish connecting.', denied: 'Connection cancelled. This agent has not been connected.', expired: 'This code has expired. Ask your agent to start a new connection.'};
    target.append(node('p', messages[status] || 'This connection is unavailable.', 'connect-result'));
    if (status === 'approved') {
      const link = node('a', 'Manage connected agents →', 'text-button'); link.href = personRoute(state.user.handle); target.append(link);
    }
    return;
  }
  target.append(node('h2', connection.label, 'connect-agent-name'));
  target.append(node('p', 'Check that this code matches your agent. Connect only an agent you started.', 'connect-copy'));
  const account = node('p', 'Connect to ', 'connect-copy'); account.append(node('strong', state.user.handle), '.'); target.append(account);
  target.append(agentScopeList(connection.scopes));
  target.append(node('p', 'This code expires at ' + utcTime(connection.expires_at) + '.', 'field-hint'));
  const actions = node('div', undefined, 'connect-actions');
  for (const [action, label, className] of [['approve', 'Connect agent', 'button button-primary'], ['deny', 'Cancel connection', 'text-button']]) {
    const button = node('button', state.connectBusy && action === 'approve' ? 'Connecting…' : label, className);
    button.type = 'button'; button.disabled = state.connectBusy; button.dataset.agentDecision = action;
    button.addEventListener('click', () => decideConnection(action)); actions.append(button);
  }
  target.append(actions);
}
function updateConnectionExpiry() {
  if (state.screen === 'connect' && state.connection?.status === 'pending' && !state.connectBusy && state.connection.expires_at <= now()) {
    state.connection = {...state.connection, status: 'expired'};
    renderConnection();
  }
}
async function loadConnection() {
  if (state.screen !== 'connect') return;
  const request = ++state.connectRequest;
  const code = state.connectCode, account = state.user?.id;
  const current = () => state.screen === 'connect' && state.connectCode === code && state.user?.id === account && request === state.connectRequest;
  state.connection = null;
  renderConnection();
  if (!code || !state.user) return;
  setLoading($('#connect-content'), 'Loading connection details…');
  try {
    const data = await api('/api/agent/connect/' + encodeURIComponent(code));
    if (!current()) return;
    const connection = data.connection;
    if (!connection || connectionCode(connection.user_code) !== code || typeof connection.label !== 'string' ||
        !Array.isArray(connection.scopes) || connection.scopes.some(scope => typeof scope !== 'string') ||
        !Number.isFinite(connection.expires_at) || !['pending', 'approved', 'expired', 'denied'].includes(connection.status)) throw new Error('The agent connection details could not be verified.');
    state.connection = connection;
    renderConnection();
  } catch (error) {
    if (current()) showInlineError($('#connect-content'), error.message, loadConnection);
  }
}
async function decideConnection(action) {
  if (!['approve', 'deny'].includes(action) || state.screen !== 'connect' || !state.user || state.connectBusy ||
      state.connection?.status !== 'pending' || state.connection.expires_at <= now()) return updateConnectionExpiry();
  const code = state.connectCode, account = state.user.id;
  const request = ++state.connectRequest;
  const current = () => state.screen === 'connect' && state.connectCode === code && state.user?.id === account && request === state.connectRequest;
  state.connectBusy = true; renderConnection();
  try {
    const data = await api('/api/agent/connect/' + encodeURIComponent(code) + '/' + action, {});
    if (!current()) return;
    if (connectionCode(data.connection?.user_code) !== code || !['approved', 'denied', 'expired'].includes(data.connection?.status)) throw new Error('The connection outcome could not be confirmed. Refresh its status before trying again.');
    state.connection = data.connection;
    state.connectBusy = false; renderConnection();
  } catch (error) {
    if (current()) {
      state.connection = null;
      showInlineError($('#connect-content'), error.message, loadConnection);
    }
  } finally { if (current()) state.connectBusy = false; }
}
function resetAgentSettings() {
  state.agentTokenRequest++;
  state.agentTokens = []; state.agentTokenPagination = null;
  $('#profile-account').hidden = true;
  $('#agent-connections').hidden = true;
  $('#agent-connections').open = false;
  $('#agent-token-list').replaceChildren();
}
function ownsViewedProfile() {
  return state.screen === 'person' && state.user && state.personHandle?.toLowerCase() === state.user.handle.toLowerCase();
}
function renderAgentSettings() {
  if (!ownsViewedProfile() || !hasAccess()) return resetAgentSettings();
  $('#profile-account').hidden = false;
  $('#agent-connections').hidden = false;
}
async function loadAgentTokens(more = false) {
  if (!ownsViewedProfile() || !$('#agent-connections').open) return;
  const request = ++state.agentTokenRequest, account = state.user.id;
  const current = () => request === state.agentTokenRequest && state.user?.id === account && ownsViewedProfile() && $('#agent-connections').open;
  const target = $('#agent-token-list');
  const offset = more === true && state.agentTokenPagination ? state.agentTokenPagination.offset + state.agentTokenPagination.limit : 0;
  if (!offset) { state.agentTokens = []; state.agentTokenPagination = null; }
  setLoading(target, 'Loading connected agents…');
  $('#refresh-agent-tokens').disabled = true;
  try {
    const data = await api('/api/agent/tokens?' + new URLSearchParams({limit: '100', offset: String(offset)}));
    if (!current()) return;
    if (!Array.isArray(data.tokens)) throw new Error('Connected agents could not be loaded.');
    state.agentTokens = offset ? [...state.agentTokens, ...data.tokens.filter(token => !state.agentTokens.some(previous => previous.id === token.id))] : data.tokens;
    state.agentTokenPagination = data.pagination || null;
    if (!state.agentTokens.length) return showEmpty(target, 'No agents connected.');
    const rows = state.agentTokens.map(token => {
      const row = node('div', undefined, 'agent-token-row');
      const copy = node('div', undefined, 'agent-token-copy');
      copy.append(node('strong', token.label));
      copy.append(node('p', token.active ? 'Expires ' + utcDate(token.expires_at) : token.revoked_at ? 'Disconnected' : 'Expired', 'field-hint'));
      const permissions = node('details'); permissions.append(node('summary', 'Permissions'), agentScopeList(token.scopes)); copy.append(permissions);
      row.append(copy);
      if (token.active) {
        const revoke = node('button', 'Disconnect', 'text-button'); revoke.type = 'button';
        revoke.setAttribute('aria-label', 'Disconnect ' + token.label);
        revoke.addEventListener('click', async () => {
          if (!current()) return;
          revoke.disabled = true;
          try {
            await api('/api/agent/tokens/' + encodeURIComponent(token.id) + '/revoke', {});
            if (current()) await loadAgentTokens();
          } catch (error) {
            if (current()) { row.append(node('p', error.message, 'field-error')); revoke.disabled = false; }
          }
        });
        row.append(revoke);
      }
      return row;
    });
    target.replaceChildren(...rows);
    if (data.pagination?.has_more) {
      const next = node('button', 'Show more agents', 'text-button'); next.type = 'button';
      next.addEventListener('click', () => loadAgentTokens(true)); target.append(next);
    }
  } catch (error) {
    if (current()) showInlineError(target, error.message, loadAgentTokens);
  } finally { if (current()) $('#refresh-agent-tokens').disabled = false; }
}
function agentFollowupRequest() {
  return `Retrieve the newest ${selectedScheme()?.name || state.schemeId} challenge ${namedParameters(selectedScheme(), state.profile) ? 'for' : 'at'} ${profileLabel(state.profile, selectedScheme())}.`;
}
function agentInstructions(challenge) {
  const origin = siteOrigin();
  const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  const client = 'python3 ./crucible_cli.py --server ' + quote(origin);
  const folder = './crucible-attack-' + challenge.id.slice(0, 8);
  const schemeId = challenge.scheme_id || challenge.public?.scheme_id || state.schemeId;
  const selection = namedParameters(selectedScheme(), state.profile) || intermediateConfiguration(state.profile) ? '--profile-id ' + quote(profileId()) : '--stage ' + instanceSize(state.profile);
  const terminal = !canSubmitChallenge(challenge);
  const purpose = terminal ? 'Inspect my completed or closed' : 'Retrieve my selected';
  const workflow = terminal
    ? 'This instance cannot accept a new answer. Inspect its public inputs and any existing submission or receipt; do not attempt a new submission. Ask before starting a different instance.'
    : `Deadline: ${utcDate(challenge.ends_at)}. Follow the workspace README for the attack and submission workflow. Record the AI model(s) that implemented the result in ai_model, with exact versions when known; never guess. Leave it blank when not reported.`;
  return `${purpose} ${selectedScheme()?.name || schemeId} challenge ${namedParameters(selectedScheme(), state.profile) ? 'for' : 'at'} ${profileLabel(state.profile, selectedScheme())}.\n\nClient: ${origin}/api/agent/client — save as crucible_cli.py and inspect before running. If login is needed, run ${client} login and let me approve in my browser.\n\nBefore work, read and follow the model pipeline contract at ${origin}/api/agent/pipeline: run ${client} guide. It identifies the supported workflows, exact source contracts and permission boundaries. The workspace saves a copy as pipeline.json; never put credentials in prompts or research files.\n\nFetch this exact instance into a fresh folder:\n${client} get --challenge-id ${quote(challenge.id)} --path ${quote(folder)}\n${workflow}\n\nNext time I can just ask: “${agentFollowupRequest()}”\nFor those future requests, use ${client} init ${quote(schemeId)} ${selection} --path NEW_EMPTY_FOLDER. No website visit needed while connected.`;
}
function agentInstructionsKey() {
  return state.user && state.challenge ? JSON.stringify([state.user.id, state.challenge.id, canSubmitChallenge(state.challenge)]) : null;
}
function updateAgentAction() {
  setText('#use-agent', state.challenge && !canSubmitChallenge(state.challenge) ? 'Inspect with an agent →' : 'Use with an agent →');
  if (!$('#agent-instructions').hidden && state.agentInstructionsKey !== agentInstructionsKey()) showAgentInstructions();
}
function resetAgentInstructions() {
  state.agentInstructionsKey = null;
  $('#agent-instructions').hidden = true;
  $('#use-agent').setAttribute('aria-expanded', 'false');
  setText('#agent-instructions-text', '');
  setText('#agent-followup-request', '');
}
function showAgentInstructions() {
  const key = agentInstructionsKey();
  if (!key) return;
  state.agentInstructionsKey = key;
  setText('#agent-instructions-text', agentInstructions(state.challenge));
  setText('#agent-followup-request', '“' + agentFollowupRequest() + '”');
  $('#agent-instructions').hidden = false;
  $('#use-agent').setAttribute('aria-expanded', 'true');
}
function toggleAgentInstructions() {
  if (!$('#agent-instructions').hidden) resetAgentInstructions();
  else showAgentInstructions();
}
async function copyAgentInstructions() {
  if (!state.challenge || !state.user) return;
  const challenge = state.challenge, account = state.user.id;
  const instructions = agentInstructions(challenge);
  $('#copy-agent-instructions').disabled = true;
  try {
    await navigator.clipboard.writeText(instructions);
    if (state.challenge?.id === challenge.id && state.user?.id === account) toast('Agent instructions copied. Give them to your agent.');
  } catch {
    if (state.challenge?.id === challenge.id && state.user?.id === account) {
      showAgentInstructions();
      toast('Copy the agent instructions shown below.');
    }
  } finally { $('#copy-agent-instructions').disabled = false; }
}
$('#connect-code-form').addEventListener('submit', event => {
  event.preventDefault();
  const code = connectionCode($('#connect-code-input').value);
  if (!code) return showInlineError($('#connect-content'), 'Enter the eight-character code from your agent.');
  navigate('#connect?' + new URLSearchParams({code}));
});
$('#agent-connections').addEventListener('toggle', () => {
  if ($('#agent-connections').open) loadAgentTokens();
  else { state.agentTokenRequest++; $('#agent-token-list').replaceChildren(); }
});
$('#refresh-agent-tokens').addEventListener('click', () => loadAgentTokens());
function peopleNumber(value, fallback = '—') {
  return Number.isFinite(value) && value >= 0 ? value.toLocaleString('en-US', {maximumFractionDigits: 2}) : fallback;
}
function peopleTable(headings, rows) {
  const wrap = node('div', undefined, 'people-table-wrap');
  const table = node('table', undefined, 'people-table');
  const head = node('thead');
  const titles = node('tr');
  for (const title of headings) { const cell = node('th', title); cell.scope = 'col'; titles.append(cell); }
  head.append(titles);
  const body = node('tbody');
  for (const cells of rows) {
    const row = node('tr');
    cells.forEach((content, index) => {
      const cell = node('td'); cell.dataset.label = headings[index]; cell.append(content); row.append(cell);
    });
    body.append(row);
  }
  table.append(head, body); wrap.append(table); return wrap;
}
function personLink(handle) {
  const link = node('a', handle, 'person-link');
  link.href = personRoute(handle);
  return link;
}
function peoplePreview() {
  const rows = Array.from({length: 6}, () => ['person-rank', 'person-link', 'person-points', ''].map(className => {
    const cell = node('span', undefined, className);
    cell.append(node('span', undefined, 'catalogue-placeholder'));
    return cell;
  }));
  const preview = peopleTable(['Rank', 'Participant', 'Points', 'Accepted proposals'], rows);
  preview.classList.add('people-ranking', 'is-ranked', 'people-preview');
  preview.setAttribute('aria-hidden', 'true');
  return preview;
}
function renderPeople(scoring) {
  const target = $('#people-content');
  const scored = state.people.some(person => Number.isFinite(person.points));
  setText('#people-score-note', scored ? '' : 'Points are being finalized. Participants are listed alphabetically.');
  $('#people-score-note').hidden = scored || !state.people.length;
  if (!state.people.length) return showEmpty(target, 'No participants yet.');
  const ranked = scored && state.people.every(person => Number.isInteger(person.rank) && person.rank > 0);
  const headings = [...(ranked ? ['Rank'] : []), 'Participant', 'Points', 'Accepted proposals'];
  const rows = state.people.map(person => [
    ...(ranked ? [node('span', peopleNumber(person.rank), 'person-rank')] : []),
    personLink(person.handle), node('span', peopleNumber(person.points, 'Pending'), 'person-points'),
    node('span', peopleNumber(person.accepted_proposal_count))
  ]);
  const table = peopleTable(headings, rows);
  table.classList.add('people-ranking');
  table.classList.toggle('is-ranked', ranked);
  target.replaceChildren(table);
}
async function loadPeople(more = false) {
  if (state.screen !== 'people') return;
  const request = ++state.peopleRequest, epoch = readViewEpoch;
  const current = () => hasAccess() && epoch === readViewEpoch && state.screen === 'people' && request === state.peopleRequest;
  const target = $('#people-content');
  const appendError = target.querySelector('.people-page-error');
  if (appendError) {
    const hadFocus = document.activeElement?.closest('.people-page-error') === appendError;
    appendError.remove();
    if (hadFocus) $('#people-workspace').focus({preventScroll: true});
  }
  const offset = more && state.peoplePagination ? state.peoplePagination.offset + state.peoplePagination.limit : 0;
  $('#refresh-people').disabled = true;
  $('#more-people').disabled = true;
  const key = 'people', cached = !more && cachedRead(key);
  const paint = data => {
    const focused = document.activeElement?.closest('#people-content') === target ? document.activeElement : null;
    const href = focused?.tagName === 'A' ? focused.getAttribute('href') : null;
    state.people = data.people; state.peoplePagination = data.pagination || null;
    paintRead(key, target, data, value => renderPeople(value.scoring));
    if (focused && !focused.isConnected && ![...document.querySelectorAll('dialog')].some(dialog => dialog.open)) {
      const replacement = href && [...target.querySelectorAll('a')].find(link => link.getAttribute('href') === href);
      (replacement || $('#people-workspace')).focus({preventScroll: true});
    }
    $('#more-people').hidden = !data.pagination?.has_more;
  };
  if (cached) paint(cached);
  if (!more && !cached) {
    state.people = [];
    state.peoplePagination = null;
    $('#more-people').hidden = true;
    $('#people-score-note').hidden = true;
    quietReadStatus(target, 'Loading participants…');
    target.append(peoplePreview());
  }
  target.setAttribute('aria-busy', 'true');
  try {
    const data = await readAgain(offset ? key + ':' + offset : key, '/api/people?' + new URLSearchParams({limit: '50', offset: String(offset), include_records: 'false'}));
    if (!current()) return;
    if (!Array.isArray(data.people) || data.people.some(person => typeof person.handle !== 'string' || !person.handle)) throw new Error('The participant list could not be loaded.');
    if (!more) rememberRead(key, {people: data.people, pagination: data.pagination, scoring: data.scoring});
    paint({people: more ? [...state.people, ...data.people.filter(person => !state.people.some(existing => existing.handle === person.handle))] : data.people, pagination: data.pagination, scoring: data.scoring});
  } catch (error) {
    if (current()) {
      if (more && state.people.length && !forbiddenRead(error)) {
        // A failed next page does not invalidate the rows already received.
        // Preserve their pagination position so retry requests this same page.
        const message = node('div', undefined, 'people-page-error');
        showInlineError(message, error.message, () => loadPeople(true));
        target.append(message);
      } else readRefreshError(key, target, error, () => loadPeople(), cached);
    }
  } finally {
    if (current()) { target.setAttribute('aria-busy', 'false'); $('#refresh-people').disabled = false; $('#more-people').disabled = false; }
  }
}
function recoveryRecordLabel(record) {
  return `${record.scheme_name || record.scheme_id} · ${recordProfileLabel(record)}`;
}
function exactRecord(record) {
  return exactProfileRecords(availableReleases().find(scheme => scheme.id === record.scheme_id && String(scheme.version) === String(record.scheme_version)), record);
}
function personalRecordTable(records) {
  const includesNamed = records.some(record => namedParameters(availableReleases().find(scheme =>
    scheme.id === record.scheme_id && String(scheme.version) === String(record.scheme_version)), record));
  return peopleTable(['Scheme', includesNamed ? 'Stage / parameter set' : 'Highest stage', 'Points', 'Solved'], records.map(record => {
    const name = node('span', undefined, 'person-record-scheme');
    const link = node('a', record.scheme_name || record.scheme_id, 'person-scheme-link');
    link.href = overviewRoute({id: record.scheme_id, version: record.scheme_version});
    prepareSchemeLink(link, {id: record.scheme_id, version: record.scheme_version});
    name.append(link);
    if (!record.is_current) name.append(node('span', 'Archived · release ' + record.scheme_version, 'person-record-release'));
    const result = node('button', recordProfileLabel(record), 'person-record-result');
    result.type = 'button'; result.disabled = !record.submission_id;
    if (record.submission_id) result.dataset.submissionId = record.submission_id;
    result.setAttribute('aria-label', 'View result for ' + recoveryRecordLabel(record));
    result.setAttribute('aria-haspopup', 'dialog');
    result.addEventListener('click', () => openSubmissionDetails(record.submission_id, result));
    const points = node('span', record.scoring_eligible ? peopleNumber(record.score?.total) : 'Not scored', 'person-record-points');
    return [name, result, points, node('span', utcDate(record.received_at), 'person-record-date')];
  }));
}
function validFirstRecord(record) {
  return record && Number.isInteger(record.tier) && record.tier > 0 && record.place === 1 &&
    Number.isFinite(record.received_at) &&
    ['scheme_id', 'scheme_version', 'profile_id', 'submission_id'].every(key => typeof record[key] === 'string' && record[key]);
}
function personDisclosure(title, count) {
  const section = node('details', undefined, 'person-section person-disclosure');
  const summary = node('summary', undefined, 'person-section-summary');
  summary.append(node('h2', title), node('span', count, 'person-section-count'));
  const content = node('div', undefined, 'person-section-content');
  section.append(summary, content);
  return {section, content};
}
function renderFirstRecords(person) {
  const firsts = (person.first_records || []).filter(validFirstRecord);
  const {section, content} = personDisclosure('First to reach', `${firsts.length} scheme${firsts.length === 1 ? '' : 's'}`);
  section.id = 'first-records';
  if (!firsts.length) {
    content.append(node('div', 'No first-place records yet.', 'empty-state'));
    return section;
  }
  const table = peopleTable(['Scheme', 'Stage', 'Reached'], firsts.map(record => {
    const name = node('span', undefined, 'person-record-scheme');
    name.append(node('span', record.scheme_name || record.scheme_id, 'milestone-scheme'));
    if (!record.is_current) name.append(node('span', 'Archived · release ' + record.scheme_version, 'person-record-release'));
    const result = node('button', recordProfileLabel(record), 'milestone-result milestone-achievement');
    result.type = 'button';
    result.dataset.submissionId = record.submission_id;
    result.setAttribute('aria-haspopup', 'dialog');
    result.setAttribute('aria-label', `${record.scheme_name || record.scheme_id}: first to reach ${recordProfileLabel(record)}. View result.`);
    result.addEventListener('click', () => openSubmissionDetails(record.submission_id, result));
    return [name, result, node('span', utcDate(record.received_at), 'person-record-date')];
  }));
  table.classList.add('first-records-table');
  table.querySelector('table').classList.add('milestone-table');
  table.querySelector('table').setAttribute('aria-label', 'Highest first-place stage per scheme');
  content.append(table);
  return section;
}
function personStats(person) {
  const stats = node('dl', undefined, 'person-stats');
  for (const [label, key] of [['Points', 'points'], ['Accepted proposals', 'accepted_proposal_count']]) {
    const value = node('dd', person ? peopleNumber(person[key], key === 'points' ? 'Pending' : '—') : undefined);
    if (!person) value.append(node('span', undefined, 'catalogue-placeholder'));
    const term = node('dt', label);
    if (key === 'points' && person?.score_breakdown) term.append(personScoreBreakdown(person));
    const stat = node('div'); stat.append(term, value); stats.append(stat);
  }
  return stats;
}
function personScoreBreakdown(person) {
  const breakdown = node('details', undefined, 'person-score-breakdown');
  const summary = node('summary', '?');
  summary.setAttribute('aria-label', 'Points breakdown');
  const panel = node('div', undefined, 'person-score-help-panel');
  const values = node('dl', undefined, 'person-score-parts');
  for (const [label, key] of [['Personal progress', 'progress'], ['First-record bonus', 'early_recovery'], ['Accepted proposals', 'scheme_contributions'], ['Research notes', 'note_contributions']]) {
    const part = node('div'); part.append(node('dt', label), node('dd', peopleNumber(person.score_breakdown[key]))); values.append(part);
  }
  panel.append(values);
  panel.append(node('p', 'Each eligible scheme offers up to 500 personal-progress points and 2,000 points for your best first record. Configuration weights are fixed by the points policy; equal points do not imply equal attack difficulty. Each explicitly admitted new scheme earns 1,000 points.'));
  panel.append(node('p', 'Intermediate stages and repeated solves do not accumulate extra points. Archived releases keep their records and are labelled when they are not scored. This is the provisional points policy.'));
  panel.append(node('p', 'Research-note contributions earn 1 or 2 points per supporting account through agent ratings. Changed or removed ratings update that credit. These points do not certify a claim or admit a scheme.'));
  breakdown.append(summary, panel);
  breakdown.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !breakdown.open) return;
    event.preventDefault(); event.stopPropagation();
    breakdown.open = false; summary.focus({preventScroll: true});
  });
  return breakdown;
}
document.addEventListener('pointerdown', event => {
  const help = $('#person-content').querySelector('.person-score-breakdown[open]');
  if (help && !help.contains(event.target)) help.open = false;
});
function personPreview() {
  // Only reserve quiet space: optional member metadata has unknown height.
  // Show metadata and scores together rather than moving provisional cards.
  // Match the initial profile frame before access is resolved.
  const preview = node('div', undefined, 'person-preview'); preview.setAttribute('aria-hidden', 'true');
  return preview;
}
const researcherFields = {affiliation: ['#researcher-affiliation', 200], position: ['#researcher-position', 150], display_name: ['#researcher-display-name', 100], website: ['#researcher-website', 2048]};
function researcherWebsite(value) {
  if (!value || /[\s\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && url.hostname && !url.username && !url.password ? url : null;
  } catch { return null; }
}
function validResearcherProfile(value) {
  return value && Number.isSafeInteger(value.revision) && value.revision >= 0 &&
    Object.entries(researcherFields).every(([key, [, max]]) => typeof value[key] === 'string' && [...value[key]].length <= max && !/[\u0000-\u001f\u007f-\u009f]/u.test(value[key])) &&
    (!value.website || researcherWebsite(value.website)) ? value : null;
}
function ownResearcherProfile() {
  return hasAccess() && state.user && state.screen === 'person' && state.personHandle?.toLowerCase() === state.user.handle.toLowerCase();
}
function researcherValues() {
  return Object.fromEntries(Object.entries(researcherFields).map(([key, [selector]]) => [key, $(selector).value.trim()]));
}
function researcherDirty() {
  return Boolean(researcherEditor.pending || researcherEditor.base && Object.entries(researcherValues()).some(([key, value]) => value !== researcherEditor.base[key]));
}
function warnResearcherDraft(event) { event.preventDefault(); event.returnValue = ''; }
function researcherDraftGuard() {
  const dirty = researcherDirty();
  if (dirty === researcherEditor.guarded) return;
  researcherEditor.guarded = dirty;
  window[dirty ? 'addEventListener' : 'removeEventListener']('beforeunload', warnResearcherDraft);
}
function researcherEditorControls() {
  const locked = researcherEditor.loading || researcherEditor.busy || Boolean(researcherEditor.pending);
  for (const [selector] of Object.values(researcherFields)) $(selector).disabled = locked;
  $('#researcher-profile-form').setAttribute('aria-busy', String(researcherEditor.loading || researcherEditor.busy));
  $('#save-researcher-profile').disabled = !ownResearcherProfile() || !researcherEditor.base || researcherEditor.contextRequired || researcherEditor.loading || researcherEditor.busy;
  setText('#save-researcher-profile', researcherEditor.busy ? 'Saving…' : researcherEditor.pending ? 'Retry save' : researcherEditor.conflict ? 'Save my changes' : 'Save profile');
  $('#reload-researcher-profile').hidden = !researcherEditor.base && !$('#researcher-profile-error').textContent;
  $('#reload-researcher-profile').disabled = researcherEditor.loading || researcherEditor.busy;
  setText('#reload-researcher-profile', researcherEditor.contextRequired ? 'Retry refresh' : 'Reload saved details');
  $('#edit-researcher-profile').hidden = !ownResearcherProfile();
  $('#edit-researcher-profile').setAttribute('aria-expanded', String(researcherEditor.open));
  setText('#edit-researcher-profile', researcherDirty() ? 'Continue editing' : 'Edit profile');
  researcherDraftGuard();
}
function suspendResearcherEditor() {
  researcherEditor.request++; researcherEditor.open = false; researcherEditor.busy = false; researcherEditor.loading = false;
  $('#researcher-profile-editor').hidden = true;
  researcherEditorControls();
}
function resetResearcherEditor() {
  Object.assign(researcherEditor, {account: null, base: null, pending: null, conflict: false, contextRequired: false});
  for (const [selector] of Object.values(researcherFields)) $(selector).value = '';
  $('#researcher-profile-summary').replaceChildren(); $('#researcher-profile-summary').hidden = true;
  $('#researcher-profile-conflict').replaceChildren(); $('#researcher-profile-conflict').hidden = true;
  setError('#researcher-profile-error', ''); setText('#researcher-profile-status', '');
  suspendResearcherEditor();
}
function renderResearcherProfile(value) {
  const target = $('#researcher-profile-summary'), profile = validResearcherProfile(value);
  const signature = JSON.stringify(profile), previous = renderedReads.get(target);
  if (previous?.key === 'researcher-profile' && previous.signature === signature && previous.child === target.firstChild) return;
  target.replaceChildren();
  target.hidden = !profile || !Object.keys(researcherFields).some(key => profile[key]);
  if (target.hidden) { renderedReads.set(target, {key: 'researcher-profile', signature, child: target.firstChild}); return; }
  if (profile.display_name) target.append(node('p', profile.display_name, 'researcher-profile-name'));
  const affiliation = [profile.affiliation, profile.position].filter(Boolean).join(' · ');
  if (affiliation) target.append(node('p', affiliation));
  const url = researcherWebsite(profile.website);
  if (url) {
    const link = node('a', url.hostname.replace(/^www\./, ''));
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer nofollow';
    target.append(link);
  }
  target.append(node('p', 'Self-reported', 'researcher-profile-provenance'));
  renderedReads.set(target, {key: 'researcher-profile', signature, child: target.firstChild});
}
function researcherConflict(profile) {
  const target = $('#researcher-profile-conflict'); target.replaceChildren(); target.hidden = false;
  target.append(node('p', 'Saved details changed. Your draft is kept. Review the current details before saving your changes.'));
  const values = node('dl');
  for (const [label, key] of [['Institution or affiliation', 'affiliation'], ['Position', 'position'], ['Display name', 'display_name'], ['Website', 'website']]) {
    values.append(node('dt', label), node('dd', profile[key] || 'Not provided'));
  }
  target.append(values);
}
async function readResearcherProfile({replace = false, conflict = false} = {}) {
  const editor = researcherEditor, request = ++editor.request, account = state.user?.id, csrf = state.csrf, lifecycle = documentEpoch;
  const current = () => ownsDocument(lifecycle) && ownResearcherProfile() && editor.open && editor.request === request && account === state.user?.id && csrf === state.csrf;
  editor.loading = true; setError('#researcher-profile-error', ''); researcherEditorControls();
  try {
    const data = await api('/api/me/profile', undefined, {readRecovery: true});
    if (!current()) return;
    const profile = validResearcherProfile(data.profile);
    if (!profile) throw new Error('Your profile could not be read. Try again.');
    editor.contextRequired = false;
    renderResearcherProfile(profile);
    if (replace || !editor.base) {
      for (const [key, [selector]] of Object.entries(researcherFields)) $(selector).value = profile[key];
      editor.base = profile; editor.pending = null; editor.conflict = false;
      $('#researcher-profile-conflict').hidden = true;
    } else if (conflict || profile.revision !== editor.base.revision) {
      // Rebase only after showing current saved values. The next write still
      // requires the member's explicit Save, and never overwrites their draft.
      editor.base = profile; editor.pending = null; editor.conflict = true;
      researcherConflict(profile);
    }
  } catch (error) {
    if (current()) setError('#researcher-profile-error', error.message);
  } finally { if (current()) { editor.loading = false; researcherEditorControls(); } }
}
async function openResearcherEditor() {
  if (!ownResearcherProfile() || researcherEditor.open) return;
  if (researcherEditor.account !== state.user.id) resetResearcherEditor();
  researcherEditor.account = state.user.id; researcherEditor.open = true;
  $('#researcher-profile-editor').hidden = false;
  setText('#researcher-profile-status', '');
  // Uncertain writes must keep their original revision/key/payload until an
  // explicit retry or reload; a normal reopen must not silently rebase them.
  if (researcherEditor.pending) researcherEditorControls();
  else await readResearcherProfile({conflict: researcherEditor.contextRequired});
}
async function saveResearcherProfile(event) {
  event.preventDefault();
  const editor = researcherEditor;
  if (!ownResearcherProfile() || !editor.open || !editor.base || editor.contextRequired || editor.busy || editor.loading) return;
  const values = researcherValues();
  if (!validResearcherProfile({...values, revision: editor.base.revision})) {
    setError('#researcher-profile-error', 'Check the field lengths and use a complete http:// or https:// website without a username or password.'); return;
  }
  if (!editor.pending) editor.pending = {...values, expected_revision: editor.base.revision, idempotency_key: crypto.randomUUID()};
  const payload = editor.pending, request = ++editor.request, account = state.user.id, csrf = state.csrf, lifecycle = documentEpoch;
  const current = () => ownsDocument(lifecycle) && ownResearcherProfile() && editor.open && editor.request === request && account === state.user?.id && csrf === state.csrf;
  editor.busy = true; setError('#researcher-profile-error', ''); setText('#researcher-profile-status', ''); researcherEditorControls();
  try {
    const data = await api('/api/me/profile', payload, {method: 'PUT'});
    if (!current()) return;
    const profile = validResearcherProfile(data.profile);
    if (!profile || profile.revision !== payload.expected_revision + 1 || Object.keys(researcherFields).some(key => profile[key] !== payload[key])) {
      throw new Error('The saved profile could not be confirmed. Retry the same save or reload saved details.');
    }
    editor.base = profile; editor.pending = null; editor.conflict = false;
    for (const [key, [selector]] of Object.entries(researcherFields)) $(selector).value = profile[key];
    $('#researcher-profile-conflict').hidden = true;
    renderResearcherProfile(profile); setText('#researcher-profile-status', 'Profile saved.');
  } catch (error) {
    if (!current()) return;
    if (error.status === 409) {
      editor.pending = null; editor.busy = false; editor.contextRequired = true;
      // Disable Save until a fresh revision is available; keep the typed draft.
      await readResearcherProfile({conflict: true});
    } else {
      // A received rejection (except server errors) is definitive. Transport
      // uncertainty and unreadable success retain the exact idempotent write.
      if (error.status >= 400 && error.status < 500) editor.pending = null;
      setError('#researcher-profile-error', error.message + (editor.pending ? ' Retry save sends the same changes once.' : ''));
    }
  } finally {
    if (current()) {
      editor.busy = false; researcherEditorControls();
    }
  }
}
$('#edit-researcher-profile').addEventListener('click', openResearcherEditor);
$('#close-researcher-profile').addEventListener('click', () => { suspendResearcherEditor(); $('#edit-researcher-profile').focus({preventScroll: true}); });
$('#researcher-profile-form').addEventListener('submit', saveResearcherProfile);
$('#researcher-profile-form').addEventListener('input', researcherEditorControls);
$('#reload-researcher-profile').addEventListener('click', () => {
  if (!ownResearcherProfile() || researcherEditor.busy || researcherEditor.loading) return;
  if (researcherEditor.contextRequired) { void readResearcherProfile({conflict: true}); return; }
  if (researcherDirty() && !window.confirm('Discard this draft and load your saved profile? A previous save may already have succeeded.')) return;
  void readResearcherProfile({replace: true});
});

function renderPerson(person) {
  const target = $('#person-content');
  setText('#person-title', person.handle);
  renderResearcherProfile(person.researcher_profile);
  target.replaceChildren(personStats(person));
  if (!Number.isFinite(person.points)) target.append(node('p', 'Scoring is being finalized. Verified results are recorded below.', 'people-score-note'));
  target.append(renderFirstRecords(person));
  const {section: records, content: recordContent} = personDisclosure('Personal records',
    `${peopleNumber(person.records?.length || 0)} scheme${person.records?.length === 1 ? '' : 's'}`);
  records.id = 'personal-records';
  if (person.records?.length) recordContent.append(personalRecordTable(person.records));
  else recordContent.append(node('div', 'No solved challenges yet.', 'empty-state'));
  target.append(records);
  const {section: recent, content: recentContent} = personDisclosure('Recent solves',
    peopleNumber(person.recent_recoveries?.length || 0) + (person.recent_recoveries_has_more ? ' shown' : ''));
  recent.id = 'recent-solves';
  if (person.recent_recoveries?.length) {
    const list = node('div', undefined, 'person-recoveries');
    for (const record of person.recent_recoveries) {
      const item = node('button', undefined, 'history-item');
      item.type = 'button'; item.disabled = !record.submission_id;
      if (record.submission_id) item.dataset.submissionId = record.submission_id;
      item.setAttribute('aria-haspopup', 'dialog');
      item.setAttribute('aria-label', 'View result for ' + recoveryRecordLabel(record));
      const copy = node('span', undefined, 'history-item-copy');
      copy.append(node('span', recoveryRecordLabel(record), 'history-item-title'));
      const meta = (!record.is_current ? 'Archived release ' + record.scheme_version + ' · ' : '') + utcDate(record.received_at);
      copy.append(node('span', meta, 'history-item-meta'));
      item.append(copy, node('span', 'View result →', 'person-result-action'));
      item.addEventListener('click', () => openSubmissionDetails(record.submission_id, item));
      list.append(item);
    }
    recentContent.append(list);
  } else recentContent.append(node('div', 'No solved challenges yet.', 'empty-state'));
  target.append(recent);
  state.personDataKey = JSON.stringify(person);
  renderAgentSettings();
  renderReviewAccountLink();
}
async function loadPerson(handle, {preserve = false} = {}) {
  const request = ++state.personRequest;
  const revision = state.sessionRevision, epoch = readViewEpoch;
  const current = () => hasAccess() && epoch === readViewEpoch && state.screen === 'person' && state.personHandle === handle && request === state.personRequest &&
    (!preserve || revision === state.sessionRevision);
  const target = $('#person-content'), key = 'person:' + handle.toLowerCase();
  // Ownership is established by the session and route, independently of the
  // public score response. Do not make account controls wait for that read.
  renderAgentSettings();
  researcherEditorControls();
  const cached = cachedRead(key);
  const paint = person => {
    // Metadata has its own stable DOM outside the score reader. Restore it on
    // cached entry even when the unchanged score DOM does not need repainting.
    renderResearcherProfile(person.researcher_profile);
    paintRead(key, target, person, renderPerson);
  };
  if (cached) { paint(cached); preserve = true; }
  if (!preserve) {
    renderResearcherProfile(null);
    state.personDataKey = null;
    setText('#person-title', handle);
    quietReadStatus(target, 'Loading profile…'); target.append(personPreview());
  }
  target.setAttribute('aria-busy', 'true');
  try {
    const data = await readAgain(key, '/api/people/' + encodeURIComponent(handle) + '?include_milestones=false');
    if (!current()) return;
    if (!data.person || typeof data.person.handle !== 'string' || data.person.handle.toLowerCase() !== handle.toLowerCase()) throw new Error('That profile could not be loaded.');
    // The server envelope changes on each poll; only actual public profile
    // changes should replace these sections and their focused controls.
    rememberRead(key, data.person);
    if (preserve && state.personDataKey === JSON.stringify(data.person) && renderedReads.get(target)?.child === target.firstChild) return;
    const disclosureKey = element => element.id || element.className;
    const expanded = preserve ? new Set([...target.querySelectorAll('details')].filter(element => element.open).map(disclosureKey)) : new Set();
    // Read focus now, after the request: a user may have opened a dialog or
    // moved elsewhere while it was pending. Match records by identity, not row.
    const focused = preserve && document.activeElement?.closest('#person-content') === target &&
      ![...document.querySelectorAll('dialog')].some(dialog => dialog.open) ? document.activeElement : null;
    const focusedSection = focused?.closest('details');
    const focusKey = focusedSection ? disclosureKey(focusedSection) : null;
    const detailsTrigger = dialogTriggers.get(recoveryDialog);
    const detailsId = detailsTrigger?.closest('#person-content') === target ? detailsTrigger.dataset.submissionId : null;
    const detailsSection = detailsId && detailsTrigger.closest('details');
    const detailsKey = detailsSection ? disclosureKey(detailsSection) : null;
    paint(data.person);
    if (preserve) for (const element of target.querySelectorAll('details')) element.open = expanded.has(disclosureKey(element));
    if (detailsId && !detailsTrigger.isConnected) {
      const section = [...target.querySelectorAll('details')].find(element => disclosureKey(element) === detailsKey);
      const replacement = section?.open && [...section.querySelectorAll('[data-submission-id]')]
        .find(element => element.dataset.submissionId === detailsId && !element.disabled);
      dialogTriggers.set(recoveryDialog, replacement || section?.querySelector('summary') || $('#person-workspace'));
    }
    if (focusKey) {
      const section = [...target.querySelectorAll('details')].find(element => disclosureKey(element) === focusKey);
      const receipt = focused.dataset.submissionId;
      const href = focused.tagName === 'A' ? focused.getAttribute('href') : null;
      const replacement = section?.open && (receipt ?
        [...section.querySelectorAll('[data-submission-id]')].find(element => element.dataset.submissionId === receipt && !element.disabled) :
        href ? [...section.querySelectorAll('a')].find(element => element.getAttribute('href') === href) : null);
      (replacement || section?.querySelector('summary') || $('#person-workspace')).focus({preventScroll: true});
    }
  } catch (error) {
    if (current()) {
      if (forbiddenRead(error)) renderResearcherProfile(null);
      readRefreshError(key, target, error, () => loadPerson(handle), cached || preserve);
    }
  } finally {
    if (current()) target.setAttribute('aria-busy', 'false');
  }
}
async function refreshRecoveryViews() {
  const refreshes = [refreshProfiles()];
  if (state.screen === 'people') refreshes.push(loadPeople());
  if (state.screen === 'person') refreshes.push(loadPerson(state.personHandle, {preserve: true}));
  await Promise.allSettled(refreshes);
  if (hasAccess() && state.screen === 'scheme' && state.view === 'leaderboard' && recoverySnapshot(selectedScheme()) &&
      leaderboardRecoveryKey() !== state.leaderboardDataKey) loadLeaderboard({preserve: true});
}
function referenceTimingKey(scheme) { return JSON.stringify([scheme.id, String(scheme.version)]); }
function referenceTimingState(scheme) { return referenceTimings.get(referenceTimingKey(scheme)); }
function measuredTiming(result) {
  return result?.status === 'measured' && Number.isSafeInteger(result.samples) && result.samples > 0 &&
    result.failure_count === 0 && [result.median_seconds, result.q1_seconds, result.q3_seconds].every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0) &&
    result.q1_seconds <= result.median_seconds && result.median_seconds <= result.q3_seconds;
}
function conditionalTiming(result) {
  return result?.status === 'partial' && result.sample_scope === 'successful-key-generation' &&
    Number.isSafeInteger(result.samples) && result.samples > 0 &&
    [result.outcomes?.warmup?.abort, result.outcomes?.measured?.abort].every(value => Number.isSafeInteger(value) && value >= 0) &&
    [result.median_seconds, result.q1_seconds, result.q3_seconds].every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0) &&
    result.q1_seconds <= result.median_seconds && result.median_seconds <= result.q3_seconds;
}
function plottedTiming(result) { return measuredTiming(result) || conditionalTiming(result); }
function timingAbortLabel(result) {
  return `${result.outcomes.measured.abort} measured-trial key-generation aborts; ${result.outcomes.warmup.abort} warmup aborts`;
}
function timingResult(scheme, profile, operation) {
  return referenceTimingState(scheme)?.report?.results.find(result => result.profile_id === profileId(profile) && result.operation === operation);
}
function timingNumber(value) { return value.toLocaleString('en-US', {maximumSignificantDigits: 4}); }
function timingDuration(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—';
  return timingNumber(seconds < 1 ? seconds * 1000 : seconds) + (seconds < 1 ? ' ms' : ' s');
}
function validTimingReport(report, method, scheme) {
  if (report === null) return true;
  if (!report || report.scheme_id !== scheme.id || String(report.scheme_version) !== String(scheme.version) ||
      report.method_id !== method.id || report.status !== 'local-pilot' || !Array.isArray(report.results) ||
      !(/^[a-f0-9]{64}$/.test(report.implementation?.artifact_sha256 || '') ||
        (report.implementation?.artifact_sha256 === null && /^[a-f0-9]{64}$/.test(report.implementation?.source_sha256 || '')))) return false;
  const keys = new Set();
  for (const result of report.results) {
    if (!result || typeof result.profile_id !== 'string' || !['keygen', 'encapsulate', 'decapsulate'].includes(result.operation) ||
        !['measured', 'failed', 'timeout', 'partial'].includes(result.status) ||
        !Number.isSafeInteger(result.samples) || result.samples < 0 || !Number.isSafeInteger(result.failure_count) || result.failure_count < 0 ||
        (result.status === 'measured' && !measuredTiming(result))) return false;
    const key = JSON.stringify([result.profile_id, result.operation]);
    if (keys.has(key)) return false;
    keys.add(key);
  }
  return true;
}
async function loadReferenceTimings(scheme, {refresh = false} = {}) {
  if (!hasAccess()) return;
  const key = referenceTimingKey(scheme), existing = referenceTimings.get(key);
  if (existing && (existing.status === 'loading' || (!refresh && Date.now() - existing.checkedAt < REFERENCE_TIMING_MAX_AGE_MS))) return;
  // Reports may be published after the catalogue release. Recheck on a revisit
  // or the visible page's session poll, while keeping its current graph steady.
  const entry = {status: 'loading', method: existing?.method || null, report: existing?.report || null, supportingNote: existing?.supportingNote || null, error: null, checkedAt: null};
  referenceTimings.set(key, entry);
  const epoch = readViewEpoch;
  const current = () => hasAccess() && epoch === readViewEpoch && referenceTimings.get(key) === entry;
  const visible = () => current() && state.screen === 'overview' && selectedScheme() && referenceTimingKey(selectedScheme()) === key;
  if (refresh && visible()) renderOverviewChart(selectedScheme(), 'computation');
  try {
    const data = await api('/api/protocol/benchmarks?' + new URLSearchParams({scheme_id: scheme.id, version: scheme.version}));
    if (!current()) return;
    if (!data.method || typeof data.method.id !== 'string' || !Number.isSafeInteger(data.method.version) ||
        !validTimingReport(data.report, data.method, scheme)) throw new Error('The reference timing report could not be loaded.');
    entry.method = data.method; entry.report = data.report; entry.supportingNote = data.supporting_note || null; entry.status = 'loaded';
  } catch (error) { if (current()) { entry.status = 'error'; entry.report = null; entry.error = error.message; } }
  if (!current()) return;
  entry.checkedAt = Date.now();
  const unchanged = existing?.status === 'loaded' && entry.status === 'loaded' &&
    JSON.stringify([existing.method, existing.report, existing.supportingNote]) === JSON.stringify([entry.method, entry.report, entry.supportingNote]);
  if (visible() && (refresh || !unchanged)) renderOverviewChart(selectedScheme(), 'computation');
}
function renderReferenceMethod(target, method) {
  if (!method) return;
  if (typeof method.summary === 'string') target.append(node('p', method.summary));
  const facts = node('dl', undefined, 'detail-list reference-method-facts');
  for (const [label, value] of [
    ['Method', method.id + ' · version ' + method.version], ['Workload', method.workload],
    ['Included', Array.isArray(method.includes) ? method.includes.join('; ') : null],
    ['Excluded', Array.isArray(method.excludes) ? method.excludes.join('; ') : null],
    ['Statistics', method.statistics], ['Correctness checks', method.correctness], ['Limits', method.limits],
    ['Interpretation', method.interpretation], ['TLS context', method.tls_context]
  ]) if (typeof value === 'string' && value) addFact(facts, label, value);
  const baseline = method.baseline;
  if (baseline && typeof baseline.name === 'string') {
    const sizes = [['public key', baseline.public_key_bytes], ['ciphertext', baseline.ciphertext_bytes], ['shared secret', baseline.shared_secret_bytes]]
      .filter(([, bytes]) => Number.isSafeInteger(bytes) && bytes > 0).map(([label, bytes]) => `${label} ${bytes.toLocaleString('en-US')} bytes`);
    addFact(facts, baseline.name, sizes.join(' · ') + (baseline.note ? '. ' + baseline.note : ''));
  }
  target.append(facts);
  const sources = node('ul', undefined, 'reference-sources');
  for (const source of Array.isArray(method.sources) ? method.sources : []) {
    const url = safeArtifactLink(source?.url);
    if (!url || typeof source.title !== 'string') continue;
    const link = node('a', source.title); link.href = url.href;
    const item = node('li'); item.append(link); sources.append(item);
  }
  if (sources.childElementCount) target.append(sources);
}
function renderTimingDetails(target, scheme, metric) {
  const entry = referenceTimingState(scheme), report = entry?.report;
  const details = node('details', undefined, 'overview-values reference-timing-details');
  details.append(node('summary', 'Method and results'));
  const content = node('div', undefined, 'reference-timing-content');
  const metadata = node('div');
  const supportingNote = supportingNoteLink(entry?.supportingNote);
  if (supportingNote) content.append(supportingNote);
  content.append(node('p', 'NIST/TLS-informed methodology. These are implementation operation timings, not a TLS handshake, a security estimate, or NIST certification.'));
  if (entry?.status === 'error') content.append(node('p', 'Timings unavailable. ' + entry.error, 'form-error'));
  if (report) {
    content.append(node('p', 'Local pilot · not independently reproduced.', 'reference-pilot-note'));
    const facts = node('dl', undefined, 'detail-list reference-method-facts');
    const implementation = report.implementation || {}, environment = report.environment || {}, sampling = report.sampling || {};
    for (const [label, value] of [
      ['Implementation', implementation.label], ['Language', implementation.language], ['Performance level', implementation.performance_level],
      ['CPU', environment.cpu], ['OS', environment.os], ['Runtime', environment.runtime], ['Compiler', environment.compiler],
      ['Threads', environment.threads], ['CPU affinity', environment.cpu_affinity],
      ['Warmups per configuration', sampling.warmups], ['Requested measured trials', sampling.trials],
      ['Configuration time budget', typeof sampling.profile_timeout_seconds === 'number' ? timingDuration(sampling.profile_timeout_seconds) : null],
      ['Measured', report.measured_at], ['Report', report.id],
      ['Implementation closure SHA-256', implementation.artifact_sha256], ['Source SHA-256', implementation.source_sha256]
    ]) if (typeof value === 'string' || typeof value === 'number') addFact(facts, label, String(value));
    metadata.append(facts);
    for (const note of Array.isArray(report.notes) ? report.notes : []) if (typeof note === 'string') metadata.append(node('p', note));
  }
  const table = node('table'), head = node('thead'), headings = node('tr');
  table.append(node('caption', OVERVIEW_METRICS[metric].label + ' · ' + scheme.name + ' · release ' + scheme.version));
  for (const label of [namedParameters(scheme) ? 'Parameter set' : 'Configuration', 'Result', 'Samples', 'Median', 'Middle 50%']) {
    const th = node('th', label); th.scope = 'col'; headings.append(th);
  }
  head.append(headings); table.append(head);
  const body = node('tbody');
  const profiles = overviewProfiles(scheme), reportedProfiles = profiles.filter(profile => timingResult(scheme, profile, metric));
  if (report) {
    const complete = reportedProfiles.filter(profile => measuredTiming(timingResult(scheme, profile, metric))).length;
    const incomplete = reportedProfiles.length - complete, unattempted = profiles.length - reportedProfiles.length;
    const counts = [`${complete} complete`];
    if (incomplete) counts.push(`${incomplete} incomplete`);
    if (unattempted) counts.push(`${unattempted} not attempted`);
    content.append(node('p', `${reportedProfiles.length} of ${profiles.length} configurations attempted. ${counts.join(' · ')}.`, 'reference-timing-coverage'));
  }
  for (const profile of reportedProfiles) {
    const result = timingResult(scheme, profile, metric), measured = plottedTiming(result), conditional = conditionalTiming(result), row = node('tr');
    const status = result ? {measured: 'Measured', failed: 'Run failed', timeout: 'Timed out', partial: 'Partial run'}[result.status] : entry?.status === 'loading' ? '—' : entry?.status === 'error' ? 'Unavailable' : 'Not measured';
    const label = node('th', profileLabel(profile, scheme)); label.scope = 'row'; label.title = profileId(profile);
    row.append(label, node('td', conditional ? 'Successful exchanges only · ' + timingAbortLabel(result) : status + (result?.failure_count ? ` · ${result.failure_count} failed` : '')),
      node('td', result ? result.samples : '—'), node('td', measured ? timingDuration(result.median_seconds) : '—'),
      node('td', measured ? timingDuration(result.q1_seconds) + '–' + timingDuration(result.q3_seconds) : '—'));
    body.append(row);
  }
  if (!reportedProfiles.length) {
    const row = node('tr'), cell = node('td', entry?.status === 'loading' ? '' : entry?.status === 'error' ? 'Measurements unavailable.' : 'Not measured.');
    if (entry?.status === 'loading') quietReadStatus(cell, 'Loading measurements');
    cell.colSpan = 5; row.append(cell); body.append(row);
  }
  table.append(body);
  const scroll = node('div', undefined, 'overview-values-scroll');
  scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Reference timing results');
  scroll.append(table); content.append(scroll);
  const actions = node('div', undefined, 'reference-timing-actions');
  const refresh = node('button', entry?.status === 'error' ? 'Try again' : 'Refresh results', 'text-button');
  refresh.type = 'button'; refresh.disabled = entry?.status === 'loading';
  refresh.addEventListener('click', () => loadReferenceTimings(scheme, {refresh: true}));
  const download = node('a', 'Download method and results', 'text-button');
  download.href = '/api/protocol/benchmarks?' + new URLSearchParams({scheme_id: scheme.id, version: scheme.version});
  download.download = 'crucible-reference-timings.json';
  actions.append(refresh, download); content.append(actions, metadata);
  renderReferenceMethod(content, entry?.method);
  details.append(content); target.append(details);
}
function renderSecurityModels(target, scheme, metric, plottedModels) {
  const evidence = securityReferences(scheme, metric);
  const content = node('div', undefined, 'reference-timing-content');
  const supportingNote = supportingNoteLink(scheme.evidence_note);
  if (supportingNote) content.append(supportingNote);
  if (typeof evidence?.explanation === 'string') content.append(node('p', evidence.explanation));
  const models = new Map(plottedModels);
  for (const model of evidence?.models || []) if (typeof model?.id === 'string' && model.id && !models.has(model.id)) models.set(model.id, model);
  if (!models.size && metric === 'classical' && scheme.initial_security_model?.id) models.set(scheme.initial_security_model.id, scheme.initial_security_model);
  for (const model of models.values()) {
    const section = node('section', undefined, 'security-model-details');
    section.append(node('h3', model.label || model.name || model.id));
    for (const key of ['scope', 'description', 'summary', 'formula', 'assumptions', 'limitations']) {
      const value = model[key];
      if (typeof value === 'string' && value) section.append(node('p', value));
      else if (Array.isArray(value)) {
        const list = node('ul');
        for (const item of value) if (typeof item === 'string') list.append(node('li', item));
        if (list.childElementCount) section.append(list);
      }
    }
    const sources = node('ul', undefined, 'reference-sources');
    for (const source of model.sources || model.references || []) {
      const url = safeArtifactLink(source?.url);
      if (!url || typeof source.title !== 'string') continue;
      const link = node('a', source.title); link.href = url.href;
      const item = node('li'); item.append(link); sources.append(item);
    }
    if (sources.childElementCount) section.append(sources);
    content.append(section);
  }
  const assumptions = node('a', 'View the scheme’s assumptions →', 'text-button');
  assumptions.href = assumptionsRoute(scheme);
  content.append(assumptions); target.append(content);
}
const OVERVIEW_METRICS = {
  public_key_bytes: {label: 'Public key', title: 'Public-key size', unit: 'Bytes', kind: 'encoding'},
  ciphertext_bytes: {label: 'Ciphertext', title: 'Ciphertext size', unit: 'Bytes', kind: 'encoding'},
  keygen: {label: 'Key generation', title: 'Key generation', unit: 'Seconds', kind: 'timing'},
  encapsulate: {label: 'Encapsulation', title: 'Encapsulation', unit: 'Seconds', kind: 'timing'},
  decapsulate: {label: 'Decapsulation', title: 'Decapsulation', unit: 'Seconds', kind: 'timing'},
  classical: {label: 'Classical reference', title: 'Initial classical reference', unit: 'Reference work (bits)', kind: 'baseline'},
  quantum: {label: 'Quantum', title: 'Quantum security estimate', unit: 'Estimated security (bits)', kind: 'security'}
};
const OVERVIEW_CARDS = {
  public: ['public_key_bytes'],
  cipher: ['ciphertext_bytes'],
  computation: ['keygen', 'encapsulate', 'decapsulate'],
  security: ['classical', 'quantum']
};
function overviewProfiles(scheme) {
  if (namedParameters(scheme)) return orderedProfiles(scheme).filter(profile => profileId(profile));
  return (scheme.profiles || []).filter(profile => Number.isFinite(stagePosition(profile)) && stagePosition(profile) > 0)
    .slice().sort((a, b) => stagePosition(a) - stagePosition(b));
}
function overviewValue(scheme, profile, metric) {
  if (['classical', 'quantum'].includes(metric)) return securityReference(scheme, profile, metric)?.bits ?? null;
  if (OVERVIEW_METRICS[metric]?.kind === 'timing') {
    const result = timingResult(scheme, profile, metric);
    return plottedTiming(result) ? result.median_seconds : null;
  }
  return ['public_key_bytes', 'ciphertext_bytes'].includes(metric) ? profileBytes(scheme, profile, metric) : null;
}
function svgNode(tag, attributes = {}, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = String(text);
  return element;
}
// Stored resource reports and security-reference charts annotate the same
// release-scoped recovery records. Named sets never inherit a numeric frontier.
function overviewRecovery(scheme, values, named) {
  const exact = exactProfileRecords(scheme), recoveredIds = new Set(recoveredProfileIds(scheme));
  const recorded = exact ? values.filter(point => recoveredIds.has(point.profileId)) :
    values.filter(point => point.profileId === scheme.largest_recovered_profile_id);
  const points = named ? recorded : recorded.slice(-1);
  const tier = !named ? points[0]?.tier : null;
  const label = !points.length ? '' : !named ? 'Largest recovered · ' + points[0].label :
    'Recovered · ' + (points.length > 3 ? `${points.length} parameter sets` : points.map(point => point.label).join(', '));
  const meaning = named ? 'Red markers identify recorded recoveries, not a security guarantee or recoveries at other profiles.' :
    exact ? 'The red line marks the highest recorded stage, not a security guarantee or recoveries at lower stages. The tinted area shows the range up to that stage.' :
      'The red line marks a recorded recovery, not a security guarantee or individual recoveries at every lower stage.';
  return {points, tier, label, meaning};
}
function redrawResourceCharts(scheme) {
  const current = selectedScheme();
  if (!hasAccess() || state.screen !== 'overview' || current?.id !== scheme.id || String(current.version) !== String(scheme.version)) return;
  // One in-flight report can be selected by several cards. Refresh all readers;
  // the resource renderer leaves unchanged controls and focus in place.
  for (const id of ['public', 'cipher', 'computation']) renderOverviewChart(current, id);
}
function renderOverviewChart(scheme, cardId) {
  const target = $('#overview-chart-' + cardId);
  if (cardId !== 'security') {
    window.CrucibleResources?.render({scheme, cardId, target, profiles: overviewProfiles(scheme),
        profileId, profileLabel, stagePosition, named: namedParameters(scheme), api,
        recoveryFor: points => overviewRecovery(scheme, points, namedParameters(scheme)),
        redraw: () => redrawResourceCharts(scheme)});
    return;
  }
  const metric = state.overviewMetrics[cardId] || OVERVIEW_CARDS[cardId][0];
  const descriptor = {...OVERVIEW_METRICS[metric]};
  const detailsOpen = target.querySelector('details')?.open === true;
  const timing = descriptor.kind === 'timing' ? referenceTimingState(scheme) : null;
  const card = target.closest('.overview-card');
  const profiles = overviewProfiles(scheme);
  const named = namedParameters(scheme);
  const axisLabel = named ? 'Parameter set' : 'Challenge stage';
  const security = ['classical', 'quantum'].includes(metric);
  const evidence = security ? securityReferences(scheme, metric) : null;
  const polynomialQuantum = metric === 'quantum' && evidence?.models?.some(model => model?.id === 'shor-polynomial-quantum-v1');
  const values = profiles.map((profile, index) => ({tier: named ? index : stagePosition(profile), intermediate: intermediateConfiguration(profile), label: profileLabel(profile, scheme), profileId: profileId(profile), value: overviewValue(scheme, profile, metric), model: security ? securityReference(scheme, profile, metric)?.model : null, conditional: descriptor.kind === 'timing' && conditionalTiming(timingResult(scheme, profile, metric)), timingResult: descriptor.kind === 'timing' ? timingResult(scheme, profile, metric) : null}));
  const referenceModels = new Map(values.filter(point => point.model).map(point => [point.model.id, point.model]));
  const singleModel = referenceModels.size === 1 ? [...referenceModels.values()][0] : null;
  const literatureOnly = security && singleModel && (metric !== 'classical' || !profiles.some(profile => initialSecurityEstimate(scheme, profile) !== null));
  if (literatureOnly) {
    descriptor.title = metric === 'quantum' ? 'Quantum attack reference' : 'Classical attack reference';
    descriptor.unit = singleModel.unit;
  } else if (polynomialQuantum) {
    descriptor.title = 'Quantum recovery';
    descriptor.unit = 'Resource estimate pending';
  }
  if (descriptor.kind === 'timing' && values.some(point => point.value !== null) && Math.max(...values.map(point => point.value || 0)) < 1) {
    descriptor.unit = 'Milliseconds';
    for (const point of values) if (point.value !== null) point.value *= 1000;
  }
  const indexedLabels = named && (profiles.length > 3 || values.some(point => point.label.length > 16));
  const measured = values.filter(point => point.value !== null);
  const minTier = values[0]?.tier, maxTier = values.at(-1)?.tier;
  const {points: recoveredPoints, tier: recoveredTier, label: recoveredLabel, meaning: recoveryMeaning} = overviewRecovery(scheme, values, named);
  setText('#overview-title-' + cardId, cardId === 'computation' ? 'Operation time' : descriptor.title);
  for (const button of card.querySelectorAll('[data-overview-metric]')) button.setAttribute('aria-pressed', String(button.dataset.overviewMetric === metric));
  const svg = svgNode('svg', {viewBox: '0 0 480 240', role: 'img', 'aria-labelledby': 'graph-title-' + cardId + ' graph-description-' + cardId, class: 'overview-svg'});
  svg.append(svgNode('title', {id: 'graph-title-' + cardId}, descriptor.title + ' by ' + axisLabel.toLowerCase()));
  const summary = measured.length ? `${measured.length} published values; ${descriptor.unit.toLowerCase()} on the vertical axis.` : 'No values have been published. This chart shows axes only.';
  svg.append(svgNode('desc', {id: 'graph-description-' + cardId}, `${summary} ${named ? 'Distinct named parameter sets on the horizontal axis; points are not interpolated.' : values.length ? `Challenge stages ${minTier} to ${maxTier} on the horizontal axis.` : 'No challenge stages published.'}${recoveredLabel ? ` ${recoveredLabel}. ${recoveryMeaning}` : ''}`));
  const left = 48, right = 465, top = 43, bottom = 190;
  const x = tier => minTier === maxTier ? (left + right) / 2 : left + (tier - minTier) / (maxTier - minTier) * (right - left);
  const maximum = measured.length ? Math.max(...measured.map(point => point.value)) : 0;
  const ceiling = maximum > 0 ? maximum : 1;
  const y = value => bottom - value / ceiling * (bottom - top);
  svg.append(svgNode('text', {x: left, y: 19, class: 'chart-axis-title'}, descriptor.unit));
  if (Number.isFinite(recoveredTier) && x(recoveredTier) > left) svg.append(svgNode('rect', {x: left, y: top, width: x(recoveredTier) - left, height: bottom - top, class: 'chart-recovery-area', 'aria-hidden': 'true'}));
  for (let index = 0; index <= 3; index++) {
    const gridY = top + index * (bottom - top) / 3;
    svg.append(svgNode('line', {x1: left, y1: gridY, x2: right, y2: gridY, class: 'chart-gridline'}));
  }
  svg.append(svgNode('path', {d: `M${left} ${top}V${bottom}H${right}`, class: 'chart-axis'}));
  if (measured.length) {
    for (const value of [0, ceiling]) svg.append(svgNode('text', {x: left - 9, y: y(value) + 4, 'text-anchor': 'end', class: 'chart-tick'}, descriptor.kind === 'timing' ? timingNumber(value) : value.toLocaleString('en-US')));
  }
  if (values.length) {
    const defaultValues = values.filter(point => !point.intermediate);
    const tickValues = defaultValues.length ? defaultValues : values;
    const tickTiers = named ? values.map(point => point.tier) : [...new Set([minTier, tickValues[Math.floor((tickValues.length - 1) / 2)].tier, maxTier])];
    for (const tier of tickTiers) {
      svg.append(svgNode('line', {x1: x(tier), y1: bottom, x2: x(tier), y2: bottom + 5, class: 'chart-axis'}));
      svg.append(svgNode('text', {x: x(tier), y: bottom + 19, 'text-anchor': 'middle', class: 'chart-tick'}, named ? indexedLabels ? tier + 1 : values.find(point => point.tier === tier).label : tier));
    }
  }
  svg.append(svgNode('text', {x: (left + right) / 2, y: 235, 'text-anchor': 'middle', class: 'chart-axis-title'}, axisLabel));
  if (measured.length) {
    let path = '', connected = false, previousModel = null;
    for (const point of values) {
      if (point.value === null || point.conditional) { connected = false; continue; }
      path += `${connected && (!security || previousModel === point.model?.id) ? 'L' : 'M'}${x(point.tier).toFixed(2)} ${y(point.value).toFixed(2)} `;
      connected = true;
      previousModel = point.model?.id;
    }
    if (!named) svg.append(svgNode('path', {d: path.trim(), class: 'chart-series'}));
    for (const point of measured) {
      const dot = svgNode('circle', {cx: x(point.tier), cy: y(point.value), r: named || measured.length === 1 ? 4 : values.length > 60 ? 1.6 : 2.8, class: 'chart-point' + (point.conditional ? ' chart-point-conditional' : ''), 'data-tier': point.tier, 'data-profile-id': point.profileId, 'data-value': point.value});
      dot.append(svgNode('title', {}, `${point.label}: ${descriptor.kind === 'timing' ? timingNumber(point.value) : point.value.toLocaleString('en-US')} ${descriptor.unit.toLowerCase()}${point.conditional ? '; median of ' + point.timingResult.samples + ' successful exchanges only; ' + timingAbortLabel(point.timingResult) : ''}`));
      svg.append(dot);
    }
  } else {
    const timingLabel = timing?.status === 'loading' ? '' : timing?.status === 'error' ? 'Timings unavailable' : 'No timings published';
    svg.append(svgNode('text', {x: (left + right) / 2, y: 106, 'text-anchor': 'middle', class: 'chart-empty-label'}, polynomialQuantum ? 'Polynomial-time quantum attack' : ['security', 'baseline'].includes(descriptor.kind) ? 'No estimates published' : descriptor.kind === 'timing' ? timingLabel : 'No values published'));
    svg.append(svgNode('text', {x: (left + right) / 2, y: 128, 'text-anchor': 'middle', class: 'chart-empty-detail'}, polynomialQuantum ? 'Shor’s algorithm · not a zero-cost estimate' : descriptor.kind === 'timing' ? timing?.status === 'loading' ? '' : timing?.status === 'error' ? 'Retry in Method and results' : 'No completed measurements for this operation' : evidence?.explanation ? 'See model and values below' : 'Awaiting evidence'));
  }
  for (const point of recoveredPoints) {
    const marker = svgNode('line', {x1: x(point.tier), y1: top, x2: x(point.tier), y2: bottom, class: 'chart-recovery-line', 'data-tier': point.tier, 'data-profile-id': point.profileId});
    marker.append(svgNode('title', {}, named ? 'Recovered · ' + point.label : recoveredLabel));
    svg.append(marker);
  }
  target.replaceChildren(svg);
  if (recoveredLabel) {
    const label = node('p', recoveredLabel, 'overview-recovery-note');
    target.append(label);
  }
  const note = literatureOnly ? `${singleModel.label}. ${measured.length} of ${values.length} configurations. Published attack-model reference, not full-KEM security bits; see model and values.` : descriptor.kind === 'timing' ? timing?.report ? 'Median wall time · ' + [timing.report.implementation?.language, timing.report.implementation?.performance_level].filter(Boolean).join(' · ') + '. ' + (named ? '' : 'Lines join adjacent measured configurations. ') + 'See method and results for the environment and spread.' : 'Reference implementation timings use a shared measurement method. Missing measurements are left empty.' :
    descriptor.kind === 'baseline' ? (measured.length ? attackReferenceModel(scheme) ? 'A stated classical attack reference, not a security lower bound. Other attacks may be faster.' : 'Frozen classical reference; not a measured security guarantee.' : 'No initial classical estimate is published for this release.') :
    descriptor.kind === 'security' ? 'An estimate needs a stated security game, attack model, and source. Recovery records are not estimates.' :
    'Canonical encoded bytes. Excludes JSON and shared domain parameters. ' + (named ? 'Dots mark distinct parameter sets; no interpolation is implied.' : 'Dots mark reviewed configurations; intermediate configurations sit between the surrounding stages.');
  if (indexedLabels) {
    const legend = node('ol', undefined, 'chart-parameter-legend');
    for (const point of values) legend.append(node('li', point.label));
    target.append(legend);
  }
  target.append(node('p', note, 'overview-chart-note'));
  if (descriptor.kind === 'timing' && timing?.report) {
    const incomplete = profiles.filter(profile => { const result = timingResult(scheme, profile, metric); return result && !plottedTiming(result); });
    if (values.some(point => point.conditional)) target.append(node('p', 'Hollow points: medians conditional on successful key generation. Aborted attempts are retained in Method and results; these points are not joined by lines.', 'overview-chart-note chart-conditional-note'));
    if (incomplete.length) target.append(node('p', `${incomplete.length} incomplete ${incomplete.length === 1 ? 'run' : 'runs'} left empty: ${incomplete.map(profile => profileLabel(profile, scheme)).join(', ')}. Recorded aborts or incomplete trials are listed in Method and results.`, 'overview-chart-note chart-coverage-note'));
  } else if (security && measured.length && measured.length < values.length && !literatureOnly) {
    target.append(node('p', `${measured.length} of ${values.length} configurations have a reference. Gaps are not interpolated; see model and values.`, 'overview-chart-note'));
  }
  const badge = $('#overview-badge-' + cardId);
  badge.textContent = literatureOnly ? 'Published reference' : descriptor.kind === 'timing' ? timing?.report ? 'Local pilot' : timing?.status === 'loading' ? 'Loading' : timing?.status === 'error' ? 'Unavailable' : 'Not measured' : measured.length ? (descriptor.kind === 'baseline' ? attackReferenceModel(scheme) ? 'Attack reference' : 'Initial model' : 'Published values') : 'Not reported';
  badge.classList.toggle('is-empty', !measured.length);
  if (descriptor.kind === 'timing' && timing?.status === 'loading') quietReadStatus(badge, 'Loading timings');
  if ((descriptor.kind === 'encoding' || security) && values.length) {
    const details = node('details', undefined, 'overview-values');
    details.append(node('summary', security ? 'Model and values' : 'View values'));
    if (security) renderSecurityModels(details, scheme, metric, referenceModels);
    else {
      const supportingNote = supportingNoteLink(scheme.evidence_note);
      if (supportingNote) { const source = node('p', undefined, 'overview-chart-note'); source.append(supportingNote); details.append(source); }
    }
    const table = node('table');
    const caption = node('caption', descriptor.title + ' · version ' + scheme.version);
    const head = node('thead'), row = node('tr');
    for (const label of [axisLabel, descriptor.unit]) { const th = node('th', label); th.scope = 'col'; row.append(th); }
    head.append(row);
    const body = node('tbody');
    for (const point of values) {
      const row = node('tr');
      row.append(node('td', named || point.intermediate ? point.label : point.tier), node('td', point.value === null ? 'Not reported' : point.value.toLocaleString('en-US')));
      body.append(row);
    }
    table.append(caption, head, body);
    const scroll = node('div', undefined, 'overview-values-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', descriptor.title + ' values by ' + axisLabel.toLowerCase());
    scroll.append(table); details.append(scroll); target.append(details);
  }
  if (descriptor.kind === 'timing') renderTimingDetails(target, scheme, metric);
  if (detailsOpen && target.querySelector('details')) target.querySelector('details').open = true;
}
function renderOverview(scheme, contextProfile = null) {
  setText('#overview-title', scheme.name);
  setText('#overview-summary', scheme.summary || '');
  $('#overview-beta').hidden = scheme.review_status !== 'local-beta';
  setText('#overview-beta', (researchTiers(scheme) ? 'Research stages · ' : '') + 'Local beta. Independent admission review pending.' + (scheme.scoring_eligible === false ? ' These profiles are not scored.' : ''));
  const profiles = overviewProfiles(scheme);
  const current = state.schemes.find(item => item.id === scheme.id);
  const archived = String(current?.version) !== String(scheme.version);
  setText('#overview-release', `${archived ? 'Archived release ' + scheme.version + ' · ' : ''}${namedParameters(scheme) ? profiles.length : scheme.default_progression?.stage_count ?? profiles.length} ${namedParameters(scheme) ? 'parameter set' + (profiles.length === 1 ? '' : 's') : 'challenge stages'}`);
  const entry = contextProfile || profiles.find(profile => profileId(profile) === scheme.next_profile_id) || profiles[0];
  $('#overview-assumptions').href = assumptionsRoute(scheme, contextProfile);
  const challengeLink = $('#overview-challenges');
  challengeLink.hidden = !entry;
  challengeLink.href = entry ? schemeRoute(scheme, entry) : '#challenge';
  renderOverviewClock(scheme);
  $('#overview-generation-status').hidden = archived || !generationUnavailable(scheme);
  setText('#overview-generation-status', generationUnavailable(scheme) ? generationMessage(scheme) : '');
  const record = profiles.find(profile => profileId(profile) === scheme.largest_recovered_profile_id);
  setText('#overview-progress', record ? `${namedParameters(scheme) ? 'Recovered parameter set' : 'Largest recovery'} · ${profileLabel(record, scheme)}` : '');
  $('#overview-progress').hidden = !record;
  const grid = $('#overview-graphs');
  if (!grid.childElementCount) {
    for (const id of Object.keys(OVERVIEW_CARDS)) {
      const card = node('article', undefined, 'overview-card');
      if (id === 'computation') card.classList.add('overview-computation');
      card.setAttribute('aria-labelledby', 'overview-title-' + id);
      const heading = node('div', undefined, 'overview-card-heading');
      const title = node('h2'); title.id = 'overview-title-' + id;
      const badge = node('span', undefined, 'overview-badge'); badge.id = 'overview-badge-' + id;
      heading.append(title, badge);
      card.append(heading);
      const metrics = OVERVIEW_CARDS[id];
      if (id === 'security' && metrics.length > 1) {
        const options = node('div', undefined, 'overview-metric-options');
        options.setAttribute('role', 'group'); options.setAttribute('aria-label', id === 'computation' ? 'Choose operation to time' : 'Choose metric for ' + id + ' chart');
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
        card.append(options);
      }
      const chart = node('div'); chart.id = 'overview-chart-' + id;
      card.append(chart); grid.append(card);
    }
  }
  window.CrucibleResources?.load(scheme, api, () => redrawResourceCharts(scheme));
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
function renderIntermediateConfigurations(scheme, current) {
  const section = $('#intermediate-configurations');
  const choices = (scheme?.profiles || []).filter(intermediateConfiguration);
  section.hidden = !choices.length;
  const context = `${scheme?.id}/${scheme?.version}`;
  if (section.dataset.context !== context) section.open = false;
  section.dataset.context = context;
  setText('#intermediate-configurations-label', `Intermediate configurations (${choices.length})`);
  const target = $('#intermediate-configuration-options');
  const restoreFocus = [...target.querySelectorAll('button')].includes(document.activeElement);
  const recovered = new Set(recoveredProfileIds(scheme));
  const buttons = choices.map(profile => {
    const button = node('button', profileLabel(profile, scheme), 'parameter-set-option');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(profileId(profile) === profileId(current)));
    button.classList.toggle('is-recovered', recovered.has(profileId(profile)));
    button.title = tierNote(scheme, profile);
    button.addEventListener('click', () => navigate(schemeRoute(scheme, profile, state.view)));
    return button;
  });
  target.replaceChildren(...buttons);
  if (restoreFocus) buttons.find(button => button.getAttribute('aria-pressed') === 'true')?.focus();
}
function exactRecoveryTrack(profiles, recovered) {
  // Runs stop at recovered thumb centers, never halfway into an unsolved stage.
  // Isolated records get small centered ticks, capped to half the stage gap.
  // A single disabled position is represented by its thumb alone. Bound CSS by
  // registered positions, never by receipt IDs or their text.
  if (profiles.length < 2 || profiles.length > 1024) return null;
  const stops = ['var(--line) 0%'];
  const boundary = position => `calc(7px + (100% - 14px) * ${position / (profiles.length - 1)})`;
  const halfTick = `min(2px, calc((100% - 14px) * ${1 / (4 * (profiles.length - 1))}))`;
  let found = false;
  for (let index = 0; index < profiles.length; index++) {
    if (!recovered.has(profileId(profiles[index]))) continue;
    const first = index;
    while (index + 1 < profiles.length && recovered.has(profileId(profiles[index + 1]))) index++;
    const isolated = first === index;
    const start = isolated ? `calc(${boundary(first)} - ${halfTick})` : boundary(first);
    const end = isolated ? `calc(${boundary(index)} + ${halfTick})` : boundary(index);
    stops.push(`var(--line) ${start}`, `#278451 ${start}`, `#278451 ${end}`, `var(--line) ${end}`);
    found = true;
  }
  if (!found) return null;
  stops.push('var(--line) 100%');
  return `linear-gradient(to right, ${stops.join(', ')})`;
}
// This describes only the current control geometry/preview, never recoveries
// or access. It prevents a progress repaint from moving an active slider or
// treating the display position of an intermediate configuration as a record.
let profileRailView = null;
function paintProfileRecoveryStyles(scheme, profiles, current) {
  const exact = exactProfileRecords(scheme, current);
  const recovered = new Set(recoveredProfileIds(scheme));
  const largest = profiles.find(profile => profileId(profile) === scheme?.largest_recovered_profile_id);
  const recoveredIndex = !exact && largest ? profiles.indexOf(largest) : -1;
  const recoveredThrough = recoveredIndex < 0 ? '0%' : profiles.length < 2 || recoveredIndex === profiles.length - 1
    ? '100%' : `calc(7px + (100% - 14px) * ${recoveredIndex / (profiles.length - 1)})`;
  const exactTrack = exact ? exactRecoveryTrack(profiles, recovered) : null;
  const registered = profiles.some(profile => profileId(profile) === profileId(current));
  const selectedRecovered = registered && (recovered.has(profileId(current)) ||
    (!exact && largest && instanceSize(current) <= instanceSize(largest)));
  $('#size-slider').style.cssText = `--recovered-through: ${recoveredThrough}` +
    (exactTrack ? `; --recovery-track: ${exactTrack}` : '') + (selectedRecovered ? '; --recovery-thumb: #278451' : '');
}
function refreshProfileRecoveryStyles(scheme) {
  if (!hasAccess() || scheme !== selectedScheme() || profileRailView?.selection !== selectionKey()) return;
  const profiles = orderedProfiles(scheme);
  if (profiles.length !== profileRailView.order.length ||
      profiles.some((profile, index) => profileId(profile) !== profileRailView.order[index])) return;
  const current = scheme.profiles.find(profile => profileId(profile) === profileRailView.profile);
  if (current) paintProfileRecoveryStyles(scheme, profiles, current);
}
function renderProfiles(previewProfile = null, {details = true} = {}) {
  if (!previewProfile) renderSchemeHeading();
  if (!previewProfile) state.profileMetadataPending = null;
  const input = $('#profile-select');
  const scheme = selectedScheme();
  const profiles = orderedProfiles(scheme);
  const current = previewProfile || state.profile;
  const named = namedParameters(scheme, current);
  renderIntermediateConfigurations(scheme, current);
  $('#advance-size').classList.toggle('is-parameter-set', named);
  for (const id of ['#tier-size-rail', '#tier-size-slider', '#tier-size-jump']) $(id).hidden = named;
  $('#parameter-set-options').hidden = !named;
  if (named) {
    profileRailView = null;
    const target = $('#parameter-set-options');
    const restoreFocus = [...target.querySelectorAll('button')].includes(document.activeElement);
    const recovered = new Set(recoveredProfileIds(scheme));
    const frontier = profiles.find(profile => profileId(profile) === scheme.next_profile_id);
    const choices = profiles.map(profile => {
      const selected = profileId(profile) === profileId(current);
      const solved = recovered.has(profileId(profile));
      const button = node('button', profileLabel(profile, scheme), 'parameter-set-option');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(selected));
      button.classList.toggle('is-recovered', solved);
      button.setAttribute('aria-label', profileLabel(profile, scheme) + (solved ? ', recovered' : ', unsolved'));
      button.addEventListener('click', () => navigate(schemeRoute(scheme, profile, state.view)));
      return button;
    });
    target.replaceChildren(...choices);
    if (restoreFocus) choices.find(button => button.getAttribute('aria-pressed') === 'true')?.focus();
    setText('#profile-label', 'Parameter set');
    setText('#size-range', profiles.length > 1 ? `${profiles.length} sets` : '');
    const conceal = !frontier || recovered.has(profileId(frontier)) || profileId(frontier) === profileId(current);
    $('#advance-size').disabled = conceal;
    $('#advance-size').classList.toggle('is-concealed', conceal);
    $('#advance-size').setAttribute('aria-hidden', String(conceal));
    setText('#advance-size-label', 'Go to next unsolved parameter set');
    setText('#advance-size-value', frontier ? profileLabel(frontier, scheme) : '');
    $('#advance-size').setAttribute('aria-label', `Go to next unsolved parameter set ${frontier ? profileLabel(frontier, scheme) : ''}`);
    setError('#profile-error', '');
    setText('#profile-note', tierNote(scheme, current));
    if (!previewProfile && details && (state.view === 'protocol' || $('#scheme-specifics').open)) renderProtocol();
    return;
  }
  const intermediate = intermediateConfiguration(current);
  setText('#profile-label', intermediate ? 'Configuration' : 'Stage');
  const selectedIndex = profiles.findIndex(profile => profileId(profile) === profileId(current));
  const index = selectedIndex >= 0 ? selectedIndex : Math.max(0, profiles.findLastIndex(profile => stagePosition(profile) < stagePosition(current)));
  const options = $('#size-options');
  const restoreFocus = [...options.querySelectorAll('button')].includes(document.activeElement);
  const largest = profiles.find(profile => profileId(profile) === scheme?.largest_recovered_profile_id);
  const frontier = profiles.find(profile => profileId(profile) === scheme?.next_profile_id);
  const exact = exactProfileRecords(scheme, current);
  const recovered = new Set(recoveredProfileIds(scheme));
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
  profileRailView = {selection: selectionKey(), profile: profileId(current), order: profiles.map(profileId)};
  paintProfileRecoveryStyles(scheme, profiles, current);
  slider.setAttribute('aria-valuetext', current ? intermediate ? profileLabel(current, scheme) : `Challenge stage ${instanceSize(current)}` : 'No stages available');
  setText('#size-slider-value', current && !intermediate ? String(instanceSize(current)) : '—');
  $('#jump-size').disabled = !profiles.length;
  setText('#size-range', profiles.length && current ? intermediate ? `Between ${current.stage_context?.before_stage ?? '—'} and ${current.stage_context?.after_stage ?? '—'}` : `${instanceSize(current)} of ${scheme?.default_progression?.stage_count ?? input.max}` : '');
  $('#previous-size').disabled = !profiles.some(profile => stagePosition(profile) < stagePosition(current));
  $('#next-size').disabled = !profiles.some(profile => stagePosition(profile) > stagePosition(current));
  const advanceLabel = 'Go to next unsolved stage';
  const advanceNumber = frontier ? instanceSize(frontier) : '—';
  const concealAdvance = !frontier || (exact && recovered.has(profileId(frontier))) || profileId(frontier) === profileId(current);
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
    const prior = exact ? solved : Boolean(largest && instanceSize(profile) <= instanceSize(largest));
    const button = node('button', String(instanceSize(profile)), 'size-option');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(selected));
    const status = nextUnsolved ? ', next unsolved stage' : record ? ', largest recovered' : solved ? ', recovered' : prior ? ', earlier stage' : '';
    button.setAttribute('aria-label', `Stage ${instanceSize(profile)}` + status);
    button.title = `Stage ${instanceSize(profile)}` + status;
    button.classList.toggle('is-next-unsolved', nextUnsolved);
    button.classList.toggle('is-prior', prior);
    button.tabIndex = selected || (intermediate && profiles.indexOf(profile) === index) ? 0 : -1;
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
  if (!previewProfile && details && (state.view === 'protocol' || $('#scheme-specifics').open)) renderProtocol();
}
function configurationProse(value) {
  // Legacy descriptions refer to immutable configurations, not today's stage order.
  // Keep math, URLs and identifiers intact; raw downloaded contracts are untouched.
  if (typeof value !== 'string' || /[$]|\\[([]/.test(value)) return value;
  return value.split(/(https?:\/\/\S+)/g).map((part, index) => index % 2 ? part :
    part.replace(/(?<![\w/.-])([Tt])ier(s?)(?![\w/.-])/g, (_, initial, plural) =>
      (initial === 'T' ? 'Configuration' : 'configuration') + plural)).join('');
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
function renderSchemeHeading() {
  const scheme = selectedScheme();
  if (!scheme) return;
  setText('#scheme-title', scheme.name);
  setText('#scheme-summary', scheme.summary);
  $('#scheme-beta').hidden = scheme.review_status !== 'local-beta';
  setText('#scheme-beta', (researchTiers(scheme, state.profile) ? 'Research stages · ' : '') + 'Local beta. Independent admission review pending.' + (scheme.scoring_eligible === false ? ' These profiles are not scored.' : ''));
}
$('#scheme-specifics').addEventListener('toggle', () => {
  if ($('#scheme-specifics').open && hasAccess() && state.screen === 'scheme') renderProtocol();
});
function renderProtocol() {
  const profile = state.profile;
  const scheme = selectedScheme();
  if (scheme) {
    setText('#protocol-version', scheme.version ? `${scheme.name} · version ${scheme.version}` : scheme.name);
    const protocol = scheme.protocol || {};
    mathText($('#protocol-construction'), protocol.construction_math || configurationProse(protocol.construction) || 'The parameter contract below defines this KEM. Consult its registered specification for the full construction.');
    mathText($('#scheme-construction-short'), protocol.construction_math || configurationProse(protocol.construction) || scheme.summary);
    const specifics = $('#scheme-specific-facts');
    specifics.replaceChildren();
    addFact(specifics, 'Underlying problem', protocol.assumption || 'See the registered protocol.');
    addFact(specifics, namedParameters(scheme) ? 'Parameter sets' : 'Mapped parameter', configurationProse(scheme.size_parameter?.meaning));
    if (scheme.ladder_design) {
      addFact(specifics, 'Stage progression', scheme.ladder_design.progression);
      addFact(specifics, 'Cost as stages rise', scheme.ladder_design.cost);
      addFact(specifics, 'Parameter choices', scheme.ladder_design.qualification);
      addFact(specifics, 'Upper endpoint', scheme.ladder_design.endpoint);
      const selection = scheme.ladder_design.selection;
      if (selection) {
        addFact(specifics, 'Parameter selection', selection.status + '. ' + selection.objective);
        addFact(specifics, 'Search scope', selection.domain);
        addFact(specifics, 'Selection limits', selection.limitations);
      }
    }
    if (researchTiers(scheme, profile)) addFact(specifics, 'Protocol relationship', configurationProse(protocol.research_relationship) || 'A scheme-specific research parameter family. Published parameter-set names apply only where explicitly declared; consult the exact parameter mapping and correctness domain below.');
    if (scheme.review_status === 'local-beta') addFact(specifics, 'Review status', 'Local beta. Independent correctness and implementation admission review is pending.' + (scheme.scoring_eligible === false ? ' These profiles do not award points.' : ''));
    addFact(specifics, 'Public inputs', protocol.public_input_math || configurationProse(protocol.public_input), true);
    addFact(specifics, 'Required answer', protocol.shared_secret || 'The complete shared key declared by the instance.');
    addFact(specifics, 'Correctness', configurationProse(protocol.correctness || profile?.correctness?.argument));
    const downloads = $('#protocol-downloads');
    downloads.replaceChildren();
    const specUrl = '/api/protocol/spec?' + new URLSearchParams({scheme_id: scheme.id, version: scheme.version});
    for (const [url, label] of [[specUrl, 'Download the scheme specification ↓'], [protocol.source_url, 'Download the reference implementation ↓'], [protocol.artifacts_url, 'Download implementation manifest ↓'], [protocol.correctness_url, 'View correctness checks →'], [protocol.example_url, 'Download the example solver ↓']]) {
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
  setText('#profile-label', namedParameters(scheme, profile) ? 'Parameter set' : intermediateConfiguration(profile) ? 'Configuration' : 'Stage');
  setText('#profile-note', tierNote(scheme, profile));
  addFact(facts, namedParameters(scheme, profile) ? 'Parameter set' : intermediateConfiguration(profile) ? 'Configuration' : 'Challenge stage', profileLabel(profile, scheme));
  setText('#protocol-record-rules', 'Method versions are immutable. Correct recoveries can appear publicly with the account handle, method, pseudocode, and compute declaration. ' + (exactProfileRecords(scheme, profile) ? `Records apply only to the exact ${namedParameters(scheme, profile) ? 'parameter set' : intermediateConfiguration(profile) ? 'configuration' : 'stage'} recovered, across all rounds. Recoveries do not transfer to other profiles.${scheme?.scoring_eligible === false ? ' These profiles do not award points.' : ''}` : 'Each stage highlights the earliest accepted recovery at that stage or higher, across all rounds.') + ' Other results remain on participant profiles. Different profiles are not ranked against one another.');
  const estimate = initialSecurityEstimate(scheme, profile);
  if (estimate !== null) {
    addFact(facts, attackReferenceModel(scheme) ? 'Initial classical attack reference' : 'Initial classical estimate', `≈${estimateBits(estimate)} bits`);
    const model = scheme.initial_security_model;
    addFact(facts, 'Reference model', typeof model.name === 'string' ? model.name : typeof model.label === 'string' ? model.label : model.id);
    const scope = [model.summary, model.scope].filter(value => typeof value === 'string' && value.trim()).join(' ');
    if (scope) addFact(facts, attackReferenceModel(scheme) ? 'Reference scope' : 'Estimate scope', configurationProse(scope));
  }
  for (const metric of ['classical', 'quantum']) {
    if (metric === 'classical' && estimate !== null) continue;
    const reference = securityReference(scheme, profile, metric);
    if (!reference) continue;
    addFact(facts, `Published ${metric} attack reference`, `${estimateBits(reference.bits)} (${reference.model.unit})`);
    addFact(facts, `${metric === 'classical' ? 'Classical' : 'Quantum'} reference model`, reference.model.label);
    addFact(facts, 'Reference scope', reference.model.scope);
  }
  setText('#protocol-size-caveat', 'An incorrect answer locks that instance. An expired instance without an accepted recovery is simply unsolved here; it is not evidence of a security bound. ' +
    (namedParameters(scheme, profile) ? 'This named parameter set is a distinct target. A recovery is not inherited by other parameter sets, and no stage or security estimate is implied.' : estimate === null ? 'A configuration selects exact scheme parameters, not an estimate of security bits.' :
      attackReferenceModel(scheme) ? 'This configuration has a stated classical attack reference, not a security lower bound; other attacks may be faster.' : 'This configuration selects parameters using a frozen initial classical reference. The estimate is not a measured security guarantee; actual attack costs may differ.'));
  addFact(facts, 'Configuration', profile.native_parameter_label || profileLabel(profile, scheme));
  if (descriptor?.mapping) addFact(facts, 'Parameter mapping', descriptor.mapping_math || configurationProse(descriptor.mapping), true);
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
  // Exact public integers may exceed JavaScript Number's precision. The server
  // supplies display-only JSON text for those profiles; frozen manifests stay intact.
  setText('#parameter-json', typeof profile.exact_parameter_json === 'string' ? profile.exact_parameter_json : JSON.stringify({algorithms: profile.algorithms, parameters: profile.parameters, encoding: profile.encoding, kdf: profile.kdf}, null, 2));
  const claim = profile.correctness || {};
  addFact(correctness, 'Claim type', claim.kind);
  addFact(correctness, 'Failure bound', claim.failure_bound);
  addFact(correctness, 'Configuration bound', claim.per_profile_failure_bound);
  addFact(correctness, 'Evidence class', {'deterministic-support-proof': 'Deterministic support proof', 'conditional-analytical-bound': 'Conditional analytical bound', 'heuristic-model-analytical-bound': 'Bound in a heuristic model'}[claim.evidence_class] || configurationProse(claim.evidence_class));
  addFact(correctness, 'Argument', scheme?.protocol?.correctness_argument_math || configurationProse(claim.argument), true);
  addFact(correctness, 'Scope', configurationProse(claim.scope));
  addFact(correctness, 'Assumptions', configurationProse(claim.assumptions));
  addFact(correctness, 'Evidence status', configurationProse(claim.evidence_status));
}
function generationAdmission(scheme = selectedScheme()) {
  const observed = state.generationAdmission;
  if (scheme?.id === state.schemeId && String(scheme.version) === state.schemeVersion && observed?.selection === selectionKey()) return observed.value;
  return scheme?.generation_admission || null;
}
function generationUnavailable(scheme = selectedScheme()) {
  const admission = generationAdmission(scheme);
  return admission != null && admission.admitted !== true &&
    !(admission.admitted === null && admission.reason === 'profile_specific');
}
function generationMessage(scheme = selectedScheme()) {
  return generationAdmission(scheme)?.reason === 'archived_release' ?
    'New instances use the current release. Existing challenges and recovery records remain available.' :
    'New instances for this release are not enabled in this beta. Existing challenges and recovery records remain available.';
}
function renderRecordChallengeAction() {
  const target = document.querySelector('#record-challenge-action');
  if (!target || state.screen !== 'scheme') return;
  const scheme = selectedScheme(), profile = state.profile;
  if (!scheme || !profile) return;
  const unavailable = generationUnavailable(scheme);
  const label = unavailable ? 'View existing challenges →' : namedParameters(scheme, profile) ? 'Take on this parameter set →' :
    intermediateConfiguration(profile) ? 'Take on this configuration →' : 'Take on this stage →';
  const challenge = node('a', label, 'button ' + (unavailable ? 'button-secondary' : 'button-primary') + ' tier-record-challenge');
  challenge.href = schemeRoute(scheme, profile, 'challenge');
  challenge.addEventListener('click', event => takeOnChallenge(scheme, profile, event));
  const key = JSON.stringify([selectionKey(), generationAdmission(scheme), label]);
  if (target.dataset.generation === key) return;
  target.dataset.generation = key; target.replaceChildren();
  const invitation = target.parentElement?.querySelector('.tier-record-invitation');
  if (invitation) invitation.textContent = 'No accepted recoveries yet.' + (unavailable ? '' : ' Set the first record for ' + profileLabel(profile, scheme) + '.');
  if (unavailable) target.append(node('p', generationMessage(scheme), 'field-hint'));
  target.append(challenge);
}
function renderChallenge() {
  if (state.agentInstructionsKey !== agentInstructionsKey()) resetAgentInstructions();
  $('#use-agent').disabled = !state.user || !state.challenge || state.challengeLoading;
  updateAgentAction();
  $('#manual-instance-tools').hidden = !state.challenge || state.challengeLoading;
  $('#open-submission').hidden = false;
  $('#open-submission').disabled = !state.ready || state.submitBusy || state.importBusy || state.challengeLoading;
  $('#challenge-loading').hidden = !state.challengeLoading || Boolean(state.challenge);
  // This card offers an action; it does not assert that no instance exists.
  // Its frame is available while the account's exact instance is checked.
  $('#challenge-empty').hidden = Boolean(state.challenge);
  $('#challenge-active').hidden = !state.challenge;
  $('#challenge-active').setAttribute('aria-busy', String(state.challengeLoading));
  $('#challenge-details').hidden = !state.challenge;
  const unavailable = generationUnavailable();
  $('#scheme-clock').hidden = unavailable && !state.challenge;
  $('#challenge-instance-rule').hidden = unavailable && !state.challenge;
  const requesting = state.issueBusy || challengeStartPending();
  $('#issue-button').setAttribute('aria-busy', String(requesting));
  $('#issue-button').disabled = !state.ready || !state.profile || requesting || roundRemaining() === 0 ||
    (unavailable && generationAdmission()?.reason !== 'archived_release');
  if (!state.challenge) {
    const current = state.schemes.find(scheme => scheme.id === state.schemeId);
    if (current && String(current.version) !== state.schemeVersion) {
      setText('#empty-title', 'Archived release');
      setText('#empty-description', 'Existing recoveries remain available here. New instances use the current release.');
      setText('#issue-button', 'Open current challenges →');
      return;
    }
    if (unavailable) {
      setText('#empty-title', 'Challenges not enabled');
      setText('#empty-description', generationMessage());
      setText('#issue-button', 'Not enabled in this beta');
      return;
    }
    setText('#empty-title', state.user ? 'Ready?' : 'Start this challenge');
    setText('#empty-description', state.user ? 'Recover the shared secret from your instance.' :
      'Sign in to get your instance.');
    setText('#issue-button', requesting ? 'Preparing your instance…' : state.user ? 'Get instance →' : 'Sign in to begin →');
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
function challengeStartSelectionMatches(intent, view = state.view) {
  return intent && state.screen === 'scheme' && view === 'challenge' &&
    intent.schemeId === state.schemeId && intent.schemeVersion === state.schemeVersion &&
    intent.profileId === profileId() && currentChallengeRelease();
}
function selectedChallengeStartIntent() {
  if (!state.profile || !currentChallengeRelease() || generationUnavailable()) return null;
  return {schemeId: state.schemeId, schemeVersion: state.schemeVersion, profileId: profileId(),
    accountId: state.user?.id || null, csrf: state.csrf, roundId: state.round?.id,
    awaitingSignIn: !state.user};
}
function challengeStartPending() {
  const intent = state.challengeStartIntent;
  return Boolean(challengeStartSelectionMatches(intent) && !intent.awaitingSignIn && hasAccess() &&
    intent.accountId === state.user?.id && intent.csrf === state.csrf &&
    intent.roundId === state.round?.id && roundRemaining() > 0);
}
function takeOnChallenge(scheme, profile, event) {
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
      (event.button != null && event.button !== 0)) return;
  event.preventDefault();
  if (state.screen !== 'scheme' || state.schemeId !== scheme.id || state.schemeVersion !== String(scheme.version) ||
      profileId() !== profileId(profile)) return;
  const route = schemeRoute(scheme, profile, 'challenge');
  // Only this deliberate action grants one request; a copied URL carries no authority.
  state.challengeStartIntent = selectedChallengeStartIntent();
  navigate(route);
}
async function loadChallenge({retry = false} = {}) {
  const request = ++state.challengeRequest;
  const selected = profileId();
  const selection = selectionKey();
  const accountId = state.user?.id;
  const csrf = state.csrf, lifecycle = documentEpoch;
  const key = challengeReadKey(state.schemeId, state.schemeVersion, selected);
  const stillSelected = () => ownsDocument(lifecycle) && request === state.challengeRequest &&
    hasAccess() && state.user?.id === accountId && state.csrf === csrf && selectionKey() === selection;
  $('#challenge-error').hidden = true;
  state.challenge = retry ? null : cachedRead(key)?.challenge || null; state.generationAdmission = null;
  if (state.screen !== 'scheme' || !state.user || !selected) {
    state.challengeLoading = false;
    renderChallenge();
    if (!state.user && state.challengeStartIntent?.awaitingSignIn &&
        challengeStartSelectionMatches(state.challengeStartIntent) && !authDialog.open) openAuth($('#issue-button'));
    return;
  }
  state.challengeLoading = true;
  renderChallenge();
  let access;
  try {
    const data = await readAgain(challengeReadKey(state.schemeId, state.schemeVersion, selected), challengeReadPath(state.schemeId, state.schemeVersion, selected));
    if (!stillSelected()) return;
    if (data.round?.id !== state.round?.id) { state.challengeStartIntent = null; return; }
    state.challenge = data.challenge || null;
    if (Object.hasOwn(data, 'generation_admission')) {
      state.generationAdmission = {selection, value: {admitted: data.generation_admission?.admitted === true, reason: data.generation_admission?.reason}};
    }
    access = data;
    if (state.challenge) rememberRead(key, {challenge: state.challenge});
    else readViews.delete(key);
  } catch (error) {
    if (!stillSelected()) return;
    state.challenge = null; readViews.delete(key);
    state.challengeStartIntent = null;
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally {
    if (stillSelected()) {
      state.challengeLoading = false;
      renderChallenge();
    } else if (ownsDocument(lifecycle) && request === state.challengeRequest && hasAccess() && state.user?.id === accountId &&
        state.csrf !== csrf && selectionKey() === selection && state.screen === 'scheme' && state.view === 'challenge') {
      // Rotation invalidates both cached and pending reads. A fresh read may
      // recover the current display, but cannot carry an old request to start.
      state.challenge = null; state.challengeLoading = false; state.challengeStartIntent = null;
      void loadChallenge();
    }
  }
  if (stillSelected() && generationUnavailable()) { state.challengeStartIntent = null; return; }
  if (stillSelected() && access && challengeStartPending()) {
    if (state.challenge || roundRemaining() <= 0) state.challengeStartIntent = null;
    else if (!state.issueBusy) {
      state.challengeStartIntent = null;
      await issueChallenge();
    }
    return;
  }
}
async function issueChallenge() {
  const current = state.schemes.find(scheme => scheme.id === state.schemeId);
  if (current && String(current.version) !== state.schemeVersion) {
    const profile = current.profiles.find(item => profileId(item) === current.next_profile_id) || current.profiles[0];
    return navigate(schemeRoute(current, profile, 'challenge'));
  }
  if (generationUnavailable()) { state.challengeStartIntent = null; renderChallenge(); return; }
  if (!state.user) return openAuth($('#issue-button'));
  if (state.issueBusy || !state.profile || state.screen !== 'scheme' || roundRemaining() === 0) return;
  if (state.challengeLoading) {
    // Both entry points share one explicit intent. A pending read decides
    // whether to open an existing instance or issue once after exact admission.
    if (!challengeStartPending()) {
      state.challengeStartIntent = selectedChallengeStartIntent();
      renderChallenge();
    }
    return;
  }
  state.issueBusy = true;
  const selected = profileId();
  const selection = selectionKey();
  const accountId = state.user.id;
  const csrf = state.csrf;
  const request = ++state.challengeRequest;
  const stillSelected = () => request === state.challengeRequest &&
    hasAccess() && state.user?.id === accountId && state.csrf === csrf && selectionKey() === selection;
  state.challengeLoading = false;
  $('#challenge-error').hidden = true;
  renderChallenge();
  try {
    const data = await api('/api/challenges?summary=true', {profile_id: selected, scheme_id: state.schemeId, scheme_version: state.schemeVersion});
    if (!stillSelected()) return;
    state.challenge = data.challenge?.round_id === state.round?.id ? data.challenge : null;
    if (state.challenge) rememberRead(challengeReadKey(state.schemeId, state.schemeVersion, selected), {challenge: state.challenge});
    if (state.challenge) toast('Your instance is ready. The shared round deadline applies.');
  } catch (error) {
    if (!stillSelected()) return;
    setText('#challenge-error-text', error.message);
    $('#challenge-error').hidden = false;
    if (error.status === 401) await synchronizeSession();
  } finally {
    state.issueBusy = false;
    if (stillSelected()) state.challengeLoading = false;
    renderChallenge();
    // A deliberate Take on action can arrive while another request finishes.
    // Ordinary stage browsing never grants permission to generate an instance.
    if (challengeStartPending() && !state.challenge && request !== state.challengeRequest) await loadChallenge();
  }
}

const recoverySummaries = new WeakMap();
function recoverySnapshot(scheme) {
  const summary = scheme?.recovery_summary;
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) return null;
  const cached = recoverySummaries.get(summary);
  if (cached?.profiles === scheme.profiles && cached.id === scheme.id && cached.version === scheme.version) return cached.value;
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const text = value => typeof value === 'string' && value.length > 0;
  let valid = summary.format === 'crucible-recovery-summary-v1' && summary.complete === true &&
    summary.scheme_id === scheme.id && summary.scheme_version === String(scheme.version) &&
    object(summary.profiles) && Array.isArray(scheme.profiles);
  const profiles = scheme.profiles || [], ids = new Set(profiles.map(profileId));
  valid = valid && ids.size === profiles.length && Object.keys(summary.profiles).length === ids.size;
  // Completeness is essential: an absent/malformed entry is never "Unsolved".
  // Validate once per snapshot/metadata object, not once for each stage click.
  if (valid) for (const profile of profiles) {
    const id = profileId(profile), row = summary.profiles[id];
    if (!Object.hasOwn(summary.profiles, id) || !object(row) || !Object.hasOwn(row, 'winner') ||
        !Array.isArray(row.includes_compatible_versions) || !row.includes_compatible_versions.every(text) ||
        new Set(row.includes_compatible_versions).size !== row.includes_compatible_versions.length) { valid = false; break; }
    const entry = row.winner;
    if (entry === null) continue;
    const size = profile.instance_size ?? profile.parameter_bits;
    if (!object(entry) || entry.rank !== 1 || ![entry.submission_id, entry.handle, entry.profile_id, entry.scheme_version].every(text) ||
        entry.scheme_id !== scheme.id || !ids.has(entry.profile_id) || !Number.isFinite(entry.received_at) ||
        !Number.isFinite(entry.instance_size) || entry.instance_size < 0 || (Number.isFinite(size) && entry.instance_size < size) ||
        typeof entry.is_inherited !== 'boolean' || entry.is_inherited !== (entry.profile_id !== id) ||
        (exactProfileRecords(scheme, profile) && entry.is_inherited) ||
        (entry.scheme_version !== String(scheme.version) && !row.includes_compatible_versions.includes(entry.scheme_version)) ||
        !object(entry.method) || !text(entry.method.name)) { valid = false; break; }
  }
  const value = valid ? summary.profiles : null;
  recoverySummaries.set(summary, {profiles: scheme.profiles, id: scheme.id, version: scheme.version, value});
  return value;
}
function leaderboardRecoveryKey() {
  // Catalogue counts and recoveries outside this record's scope cannot change
  // its winner. Include the exact release/selection in both completed and pending keys.
  const scheme = selectedScheme(), exact = exactProfileRecords(scheme, state.profile);
  const snapshot = recoverySnapshot(scheme);
  if (snapshot) return JSON.stringify([selectionKey(), exact, snapshot[profileId()]]);
  const relevant = new Set((scheme?.profiles || []).filter(profile => exact ? profileId(profile) === profileId() :
    stagePosition(profile) >= stagePosition(state.profile)).map(profile => profileId(profile)));
  const recovered = recoveredProfileIds(scheme).filter(id => relevant.has(id)).sort();
  const records = (scheme?.profile_recoveries || []).filter(record => relevant.has(record.profile_id))
    .sort((a, b) => a.profile_id.localeCompare(b.profile_id));
  const first = !exact && relevant.has(scheme?.first_recovery?.profile_id) ? scheme.first_recovery : null;
  return JSON.stringify([selectionKey(), exact, recovered, records, first]);
}

async function refreshLeaderboard() {
  if (!hasAccess()) return;
  if (!recoverySnapshot(selectedScheme())) return loadLeaderboard({preserve: true});
  const epoch = readViewEpoch, selection = selectionKey(), request = ++state.leaderboardRequest;
  const current = () => hasAccess() && epoch === readViewEpoch && request === state.leaderboardRequest &&
    selection === selectionKey() && state.screen === 'scheme' && state.view === 'leaderboard';
  $('#refresh-leaderboard').disabled = true;
  try {
    if (await refreshProfiles({background: true}) !== false && current()) await loadLeaderboard({preserve: true});
  } catch (error) {
    if (current()) toast('Recovery record refresh failed. ' + error.message);
  } finally {
    if (current()) $('#refresh-leaderboard').disabled = false;
  }
}
async function loadLeaderboard({preserve = false} = {}) {
  if (!hasAccess()) return;
  const target = $('#leaderboard-content');
  const card = $('#tier-record');
  const named = namedParameters(selectedScheme(), state.profile);
  const exact = exactProfileRecords(selectedScheme(), state.profile);
  const scope = exact ? `The earliest accepted recovery of this exact ${named ? 'parameter set' : intermediateConfiguration(state.profile) ? 'configuration' : 'stage'}, across all rounds. Ties use the server receipt identifier.` : 'The earliest accepted recovery at this stage or higher, across all rounds. Ties use the server receipt identifier.';
  const footnote = (exact ? 'Recoveries do not transfer between profiles. ' : '“Via Stage” credits a recovery at a higher stage; the result describes that actual instance. ') + 'A recovery verifies one challenge answer. It does not establish a new attack method or a general break of the scheme. Method and compute details are contributor-reported.';
  const request = ++state.leaderboardRequest, epoch = readViewEpoch;
  const selected = profileId();
  const schemeId = state.schemeId, schemeVersion = state.schemeVersion;
  const selection = selectionKey(), dataKey = leaderboardRecoveryKey();
  const current = () => hasAccess() && epoch === readViewEpoch && request === state.leaderboardRequest && state.screen === 'scheme' &&
    state.view === 'leaderboard' && selection === selectionKey();
  if (state.screen !== 'scheme' || !selected) {
    setText('#leaderboard-title', ''); card.setAttribute('data-state', 'empty');
    $('#tier-record-notes').hidden = true; $('#leaderboard-scope').hidden = true; $('#leaderboard-footnote').hidden = true;
    return showEmpty(target, named ? 'Choose an available parameter set to see its record.' : 'Choose an available stage to see its record.');
  }
  state.leaderboardPendingKey = dataKey;
  const key = 'record:' + dataKey, cached = cachedRead(key);
  const paint = data => {
    const focus = document.activeElement?.closest('#leaderboard-content') === target ? document.activeElement : null;
    const trigger = dialogTriggers.get(recoveryDialog);
    const ownsTrigger = trigger?.closest('#leaderboard-content') === target;
    paintRead(key, target, {...data, generation_admission: generationAdmission()}, data => {
    if (!data.entries.length) {
      setText('#leaderboard-title', 'Unsolved');
      card.setAttribute('data-state', 'unsolved');
      $('#tier-record-notes').hidden = true;
      $('#tier-record-notes').open = false;
      $('#leaderboard-scope').hidden = true;
      $('#leaderboard-footnote').hidden = true;
      const empty = node('div', undefined, 'tier-record-open');
      empty.append(node('p', 'No accepted recoveries yet.' + (generationUnavailable() ? '' : ' Set the first record for ' + profileLabel(state.profile, selectedScheme()) + '.'), 'tier-record-invitation'));
      const action = node('div'); action.id = 'record-challenge-action'; empty.append(action);
      target.replaceChildren(empty);
      renderRecordChallengeAction();
      state.leaderboardDataKey = dataKey;
      return;
    }
    const entry = data.entries[0];
    setText('#leaderboard-title', 'Broken by');
    card.setAttribute('data-state', 'solved');
    $('#tier-record-notes').hidden = false;
    $('#leaderboard-scope').hidden = false;
    $('#leaderboard-footnote').hidden = false;
    setText('#leaderboard-scope', scope);
    setText('#leaderboard-footnote', footnote);
    if (data.includes_compatible_versions?.length) setText('#leaderboard-scope', scope + ' Includes identical parameters from version ' + data.includes_compatible_versions.join(', ') + '; each record keeps its original release.');
    const winner = node('div', undefined, 'tier-winner');
    const name = node('a', entry.handle, 'tier-winner-name');
    name.href = '#person/' + encodeURIComponent(entry.handle);
    name.setAttribute('aria-label', 'View ' + entry.handle + '’s profile');
    winner.append(name);
    const meta = node('div', undefined, 'tier-record-meta');
    meta.append(node('span', 'First recovery · ' + utcDate(entry.received_at)));
    const entryLabel = recordProfileLabel({...entry, scheme_id: entry.scheme_id || state.schemeId, scheme_version: entry.scheme_version || state.schemeVersion, profile_id: entry.profile_id || selected});
    if (entry.is_inherited && !exact) meta.append(node('span', 'Via ' + entryLabel, 'leaderboard-credit'));
    if (entry.scheme_version && entry.scheme_version !== state.schemeVersion) meta.append(node('span', 'Release ' + entry.scheme_version, 'tier-record-release'));
    winner.append(meta);
    const evidence = node('div', undefined, 'tier-record-evidence');
    evidence.append(node('span', entry.method?.name || 'Recovery method', 'tier-record-method'));
    const result = node('button', 'View result →', 'text-button tier-record-result');
    result.type = 'button';
    result.dataset.submissionId = entry.submission_id || '';
    result.setAttribute('aria-label', 'View recovery by ' + entry.handle);
    result.setAttribute('aria-haspopup', 'dialog');
    const context = `${selectedScheme().name} · ${entryLabel} · version ${entry.scheme_version || state.schemeVersion}`;
    result.addEventListener('click', () => entry.submission_id
      ? openSubmissionDetails(entry.submission_id, result)
      : openRecovery(entry, context, result));
    evidence.append(result);
    winner.append(evidence);
    target.replaceChildren(winner);
    state.leaderboardDataKey = dataKey;
    });
    const replacement = old => [...target.querySelectorAll('button,a')].find(element =>
      old.dataset.submissionId ? element.dataset.submissionId === old.dataset.submissionId : element.href && element.href === old.href) || $('#tab-leaderboard');
    if (focus && !focus.isConnected) replacement(focus).focus({preventScroll: true});
    if (ownsTrigger && !trigger.isConnected) dialogTriggers.set(recoveryDialog, replacement(trigger));
  };
  const snapshot = recoverySnapshot(selectedScheme())?.[selected];
  if (snapshot && current()) {
    paint({entries: snapshot.winner === null ? [] : [snapshot.winner], includes_compatible_versions: snapshot.includes_compatible_versions});
    state.leaderboardPendingKey = null;
    $('#refresh-leaderboard').disabled = false;
    return;
  }
  if (cached) { paint(cached); preserve = true; }

  if (!preserve) {
    state.leaderboardDataKey = null;
    setText('#leaderboard-title', '');
    card.setAttribute('data-state', 'loading');
    $('#tier-record-notes').hidden = true;
    $('#tier-record-notes').open = false;
    $('#leaderboard-scope').hidden = true;
    $('#leaderboard-footnote').hidden = true;
    setText('#leaderboard-scope', scope);
    setText('#leaderboard-footnote', footnote);
    setLoading(target, 'Loading the recovery record…');
  }
  $('#refresh-leaderboard').disabled = true;
  try {
    const data = await readAgain(key, '/api/leaderboard?' + new URLSearchParams({profile_id: selected, scheme_id: schemeId, scheme_version: schemeVersion}));
    if (!current()) return;
    if (!Array.isArray(data.entries) || (data.profile_id != null && data.profile_id !== selected) ||
        (data.scheme_id != null && data.scheme_id !== schemeId) ||
        (data.scheme_version != null && data.scheme_version !== schemeVersion)) {
      throw new Error('The recovery record could not be loaded. Please try again.');
    }
    if (data.entries.length) {
      const entry = data.entries[0];
      if (entry.rank !== 1 || typeof entry.handle !== 'string' || !entry.handle || !Number.isFinite(entry.received_at)) {
      throw new Error('The recovery record could not be loaded. Please try again.');
      }
      if (exact && (entry.is_inherited || (entry.profile_id != null && entry.profile_id !== selected))) {
      throw new Error('The recovery record does not belong to this exact profile. Please refresh.');
      }
    }
    const record = {entries: data.entries, includes_compatible_versions: data.includes_compatible_versions};
    rememberRead(key, record);
    paint(record);
  } catch (error) {
    if (current()) state.leaderboardDataKey = null;
    readViews.delete(key);
    if (current() && preserve && !forbiddenRead(error)) toast('Recovery record refresh failed. ' + error.message);
    else if (current()) {
      setText('#leaderboard-title', '');
      card.setAttribute('data-state', 'error');
      $('#tier-record-notes').hidden = true;
      $('#leaderboard-scope').hidden = true;
      $('#leaderboard-footnote').hidden = true;
      showInlineError(target, error.message, loadLeaderboard);
    }
  } finally {
    if (request === state.leaderboardRequest) {
      state.leaderboardPendingKey = null;
      $('#refresh-leaderboard').disabled = false;
    }
  }
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
  addFact(facts, 'AI model(s)', typeof entry.ai_model === 'string' && entry.ai_model.trim() ? entry.ai_model.trim() : 'Not reported');
  addFact(facts, 'Reported attack time', compute.wall_seconds == null ? null : duration(compute.wall_seconds));
  addFact(facts, 'Hardware', compute.hardware);
  addFact(facts, 'Parallel workers', compute.workers);
  addFact(facts, 'Precomputation', compute.precomputation);
  addFact(facts, 'Peak memory', compute.peak_memory);
  if (compute.notes) addFact(facts, 'Notes', compute.notes);
  content.replaceChildren(node('pre', entry.method?.pseudocode || 'No pseudocode recorded.', 'code-block'), facts,
    node('p', 'Method, AI model attribution and resources are participant-reported. The server checks the submitted key, not the accuracy of these claims. An unreported model does not establish that no AI was used.', 'panel-footnote'));
  if (typeof entry.method?.source?.download_url === 'string') {
    try {
      const source = new URL(entry.method.source.download_url, siteOrigin());
      if (source.origin === siteOrigin() && !source.username && !source.password && source.pathname.startsWith('/api/')) {
        const link = node('a', 'Download code bundle ↓', 'button button-quiet');
        link.href = source.href; link.setAttribute('download', ''); content.append(link);
      }
    } catch { /* Invalid artifact locations are not links. */ }
  }
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
    const tier = recordProfileLabel(entry);
    const context = `${name} · ${tier} · version ${entry.scheme_version}`;
    renderRecovery(entry, context);
  } catch (error) {
    if (current()) showInlineError($('#recovery-content'), error.message, () => openSubmissionDetails(id, trigger));
  }
}
function renderView(view, focus = false) {
  const departingRecordFocus = view === 'challenge' && document.activeElement?.closest('#panel-leaderboard');
  const previousFocus = departingRecordFocus ? document.activeElement : null;
  state.view = view;
  for (const tab of document.querySelectorAll('[data-view]')) {
    const selected = tab.dataset.view === view;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $('#panel-' + tab.dataset.view).hidden = !selected;
    if (selected && focus) tab.focus();
  }
  // A Record action can change tabs without changing the outer workspace.
  // Hand off only the focus hidden by this transition, never header/dialog focus.
  if (previousFocus && (document.activeElement === previousFocus || document.activeElement === document.body) &&
      (!previousFocus.isConnected || previousFocus.closest('[hidden]'))) $('#tab-challenge').focus();
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
    // A queued close from the previous opening must not clear a reopened list.
    if (dialog === historyDialog && dialog.open) return;
    document.body.style.overflow = [...document.querySelectorAll('dialog')].some(item => item.open) ? 'hidden' : '';
    const trigger = dialogTriggers.get(dialog);
    const parentDialog = trigger?.closest('dialog');
    if (trigger?.isConnected && !trigger.hidden && !trigger.disabled &&
        !trigger.closest('[hidden]') && (!parentDialog || parentDialog.open)) trigger.focus();
    if (dialog === historyDialog) {
      state.historyRequest++;
      $('#history-content').replaceChildren();
      $('#history-content').setAttribute('aria-busy', 'false');
    }
    if (dialog === recoveryDialog) {
      state.recoveryRequest++;
      $('#recovery-content').replaceChildren();
      setText('#recovery-context', '');
      setText('#recovery-title', 'Submission details');
    }
    if (dialog === authDialog) {
      $('#auth-password').value = '';
      $('#password-reset-form').reset();
      $('#github-link-password').value = ''; state.githubLinkContext = null;
      state.passwordResetToken = null;
      if (authenticationRoute() && !state.authCallbackBusy) window.history.replaceState(null, '', safeAuthReturnRoute(state.returnRoute));
      if (hasAccess() && !state.ready && !state.authCallbackBusy && !state.githubCompleteBusy) void loadAuthorizedWorkspace().catch(error => globalError(error.message));
      if (state.challengeStartIntent?.awaitingSignIn) state.challengeStartIntent = null;
    }
    if (dialog === submissionDialog) {
      state.recoveryImportRequest++;
      state.importBusy = false;
      lockSubmissionFields(Boolean(state.pendingAttempt || state.submitBusy));
      updateRecoveryControls();
    }
  });
}
function safeAuthReturnRoute(value) {
  return typeof value === 'string' && value.length <= 2048 && !/[\u0000-\u0020\\]/.test(value) &&
    /^#(?:challenge|scheme|assumptions|person|people|notebook|propose|reviews|about|connect)(?:[/?]|$)/.test(value) ? value : '#challenge';
}
function authenticationRoute() { return /^#(?:auth|reset-password)(?:\?|$)/.test(window.location.hash); }
function renderAuthMethods() {
  const github = state.auth?.github_enabled === true;
  $('#access-github').hidden = !github;
  $('#access-github').disabled = state.githubBusy;
  $('#access-sign-in').classList.toggle('button-primary', !github);
  $('#access-sign-in').classList.toggle('button-quiet', github);
  setText('#access-sign-in', github ? 'Use a password' : 'Sign in');
  $('#access-register').hidden = github || !state.invitationToken;
  $('#auth-github').hidden = !github;
  $('#auth-github').disabled = state.githubBusy;
  setText('#auth-github', state.githubBusy ? 'Connecting to GitHub…' : 'Continue with GitHub');
  setText('#access-github', state.githubBusy ? 'Connecting to GitHub…' : 'Continue with GitHub');
  $('#auth-password-options').classList.toggle('sole-method', !github);
  if (!github) $('#auth-password-options').open = true;
  $('#auth-forgot-password').hidden = !state.auth?.password_recovery || state.authMode !== 'login';
  $('#auth-sign-in-methods').hidden = state.authPanel !== 'signin';
  $('#github-handle-form').hidden = state.authPanel !== 'github-handle';
  $('#github-link-form').hidden = state.authPanel !== 'github-link';
  $('#github-link-submit').disabled = state.githubBusy;
  $('#password-reset-request-form').hidden = state.authPanel !== 'reset-request';
  $('#password-reset-form').hidden = state.authPanel !== 'reset-password';
  renderAccountSignIn();
}
function renderAccountSignIn() {
  const visible = Boolean(ownsViewedProfile() && hasAccess() && state.auth?.github_enabled);
  $('#account-sign-in').hidden = !visible;
  if (!visible) { $('#account-sign-in').open = false; setError('#account-sign-in-error', ''); }
  const linked = state.auth?.github_linked === true;
  setText('#account-github-status', linked ? 'Linked' + (state.auth.github_login ? ' as ' + state.auth.github_login : '') + '.' : 'Use GitHub to sign in to this same account.');
  $('#account-link-github').hidden = linked;
  $('#account-link-github').disabled = state.githubBusy;
  setText('#account-link-github', state.githubBusy ? 'Connecting…' : 'Link GitHub');
}
async function startGitHub(mode = 'login', trigger = $('#auth-github'), password) {
  if (!state.auth?.github_enabled || state.githubBusy || (mode === 'link' ? !state.user : Boolean(state.user))) return;
  const account = state.user?.id, csrf = state.csrf, dialogStart = trigger === $('#auth-github') || trigger === $('#github-link-submit');
  const destination = mode === 'link' ? personRoute(state.user.handle) : safeAuthReturnRoute(state.returnRoute || window.location.hash);
  const errorTarget = mode === 'link' ? (dialogStart ? '#github-link-error' : '#account-sign-in-error') : dialogStart ? '#auth-github-error' : '#access-error';
  state.githubBusy = true; renderAuthMethods(); setError(errorTarget, '');
  try {
    const data = await api('/api/auth/github/start', {mode, return_route: destination,
      ...(mode === 'link' && typeof password === 'string' ? {password} : {}),
      ...(mode === 'login' && state.invitationToken ? {invitation_token: state.invitationToken} : {})});
    if (state.user?.id !== account || state.csrf !== csrf || (dialogStart && !authDialog.open) || (mode === 'link' && !ownsViewedProfile())) return;
    const destinationURL = new URL(data.authorization_url);
    if (destinationURL.origin !== 'https://github.com' || destinationURL.pathname !== '/login/oauth/authorize' || destinationURL.username || destinationURL.password) throw new Error('GitHub sign-in could not be started. Please try again.');
    window.location.assign(destinationURL.href);
  } catch (error) {
    if (state.user?.id === account && (!dialogStart || authDialog.open)) setError(errorTarget, error.message);
  } finally { $('#github-link-password').value = ''; state.githubBusy = false; renderAuthMethods(); }
}
function showAuthPanel(panel, trigger) {
  state.authPanel = panel;
  renderAuthMethods();
  if (!authDialog.open) openDialog(authDialog, trigger);
}
async function finishAuthentication(returnRoute = state.returnRoute) {
  const destination = safeAuthReturnRoute(returnRoute);
  state.returnRoute = destination;
  const route = !hasAccess() && state.invitationToken ? '#join?' + new URLSearchParams({token: state.invitationToken}) : destination;
  window.history.replaceState(null, '', route);
  if (!hasAccess()) { renderAccessGate(); return; }
  state.invitationToken = null; state.invitationDelivery = null;
  try { await loadAuthorizedWorkspace(); }
  catch (error) { globalError(error.message); }
}
function capturePasswordResetLink() {
  const [path, query = ''] = window.location.hash.split('?');
  if (path !== '#reset-password') return;
  const token = new URLSearchParams(query).get('token');
  // Scrub before even requesting a session; never put reset secrets in storage.
  window.history.replaceState(null, '', '#auth?password=reset');
  state.passwordResetToken = typeof token === 'string' && /^[A-Za-z0-9_-]{43}$/.test(token) ? token : null;
}
async function handleAuthenticationRoute() {
  capturePasswordResetLink();
  const [path, query = ''] = window.location.hash.split('?');
  if (path === '#auth' && new URLSearchParams(query).get('password') === 'reset') {
    $('#password-reset-form').reset();
    setText('#auth-title', 'Reset your password');
    setText('#auth-description', 'Choose a new password for your Crucible account.');
    setError('#password-reset-error', state.passwordResetToken ? '' : 'This reset link is incomplete. Request a new link.');
    $('#password-reset-submit').disabled = !state.passwordResetToken;
    showAuthPanel('reset-password');
    if (state.passwordResetToken) $('#password-reset-new').focus();
    return true;
  }
  if (path !== '#auth') return false;
  if (state.authCallbackBusy) return true;
  const outcome = new URLSearchParams(query).get('github');
  if (!outcome) { openAuth(); return true; }
  state.authCallbackBusy = true;
  const callbackHash = window.location.hash, account = state.user?.id;
  let returnRoute = '#challenge';
  try {
    const result = await api('/api/auth/github/pending');
    if (window.location.hash !== callbackHash || state.user?.id !== account) return true;
    returnRoute = safeAuthReturnRoute(result.return_route);
    state.returnRoute = returnRoute;
    if (typeof result.invitation_token === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(result.invitation_token)) state.invitationToken = result.invitation_token;
    if (outcome === 'choose-handle' && result.pending === true && !state.user) {
      state.githubPending = true;
      $('#github-handle-form').reset();
      const suggested = result.suggested_handle;
      $('#github-handle').value = typeof suggested === 'string' && /^[A-Za-z0-9_]{3,32}$/.test(suggested) ? suggested : '';
      setText('#github-handle-context', result.github_login ? 'Connected to GitHub as ' + result.github_login + '.' : 'Your GitHub account is connected.');
      setText('#auth-title', 'Choose your handle');
      setText('#auth-description', state.access?.mode === 'beta' ? 'Create your Crucible account, then verify your invitation email.' : 'Create your Crucible account.');
      setError('#github-handle-error', '');
      showAuthPanel('github-handle');
      $('#github-handle').focus();
      return true;
    }
    if (['complete', 'linked'].includes(outcome) && state.user) {
      await finishAuthentication(returnRoute);
      toast(outcome === 'linked' ? 'GitHub is linked to your account.' : 'Signed in as ' + state.user.handle + '.');
      return true;
    }
    const messages = {
      denied: 'GitHub sign-in was cancelled. You can try again or use your password.',
      expired: 'This GitHub sign-in has expired. Please try again.',
      'invite-required': 'This GitHub account is not linked to a Crucible account. If you already have one, sign in with your password, then open your profile’s Sign-in settings and choose Link GitHub. New participants should open their invitation link.',
      'identity-in-use': 'This GitHub account is already linked to another Crucible account. Sign in to that account or contact the operator.',
      'session-changed': 'Your session changed during GitHub sign-in. Please try again from your current account.'
    };
    throw new Error(messages[outcome] || 'GitHub sign-in could not be completed. Please try again.');
  } catch (error) {
    if (window.location.hash !== callbackHash || state.user?.id !== account) return true;
    await finishAuthentication(returnRoute);
    if (state.user) { globalError(error.message); }
    else {
      openAuth();
      if (outcome === 'invite-required') $('#auth-password-options').open = true;
      setError('#auth-github-error', error.message);
    }
  } finally { state.authCallbackBusy = false; }
  return true;
}
$('#access-github').addEventListener('click', () => startGitHub('login', $('#access-github')));
$('#auth-github').addEventListener('click', () => startGitHub('login', $('#auth-github')));
$('#account-link-github').addEventListener('click', () => {
  if (!ownsViewedProfile() || !hasAccess() || !state.auth?.github_enabled || state.auth.github_linked || state.githubBusy) return;
  if (!state.auth.password_enabled) return startGitHub('link', $('#account-link-github'));
  state.githubLinkContext = {accountId: state.user.id, csrf: state.csrf};
  $('#github-link-form').reset(); setError('#github-link-error', '');
  setText('#auth-title', 'Link GitHub');
  setText('#auth-description', 'Confirm your password to add GitHub sign-in to this account.');
  setText('#github-link-context', 'Your Crucible account: ' + state.user.handle);
  showAuthPanel('github-link', $('#account-link-github')); $('#github-link-password').focus();
});
$('#github-link-form').addEventListener('submit', event => {
  event.preventDefault();
  const context = state.githubLinkContext;
  if (!context || context.accountId !== state.user?.id || context.csrf !== state.csrf || !ownsViewedProfile() || state.authPanel !== 'github-link' || state.githubBusy) return;
  const password = $('#github-link-password').value;
  $('#github-link-password').value = '';
  void startGitHub('link', $('#github-link-submit'), password);
});
$('#github-existing-account').addEventListener('click', () => { state.githubPending = false; window.history.replaceState(null, '', safeAuthReturnRoute(state.returnRoute)); setAuthMode('login'); $('#auth-password-options').open = true; $('#auth-handle').focus(); });
$('#github-handle-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.githubPending || state.githubCompleteBusy || state.user) return;
  const csrf = state.csrf;
  state.githubCompleteBusy = true;
  $('#github-handle-submit').disabled = true; setError('#github-handle-error', '');
  try {
    const data = await api('/api/auth/github/complete', {handle: $('#github-handle').value.trim()});
    if (state.user || state.csrf !== csrf) return;
    state.githubPending = false;
    setSession(data); authDialog.close();
    await finishAuthentication();
  } catch (error) {
    if (authDialog.open && state.authPanel === 'github-handle') setError('#github-handle-error', error.message);
  } finally { state.githubCompleteBusy = false; $('#github-handle-submit').disabled = false; }
});
function openPasswordRecovery() {
  if (!state.auth?.password_recovery) return;
  $('#password-reset-request-form').reset();
  $('#password-reset-handle').value = $('#auth-handle').value.trim();
  setError('#password-reset-request-error', '');
  $('#password-reset-request-status').hidden = true;
  setText('#auth-title', 'Reset your password');
  setText('#auth-description', 'We’ll send a reset link to your verified email address.');
  showAuthPanel('reset-request', $('#auth-forgot-password'));
  $('#password-reset-handle').focus();
}
$('#auth-forgot-password').addEventListener('click', openPasswordRecovery);
for (const id of ['#password-reset-request-back', '#password-reset-back']) $(id).addEventListener('click', () => {
  state.passwordResetToken = null; $('#password-reset-form').reset();
  setAuthMode('login'); $('#auth-password-options').open = true; $('#auth-handle').focus();
});
$('#password-reset-request-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.passwordRecoveryBusy || !state.auth?.password_recovery) return;
  const csrf = state.csrf;
  state.passwordRecoveryBusy = true; $('#password-reset-request-submit').disabled = true;
  setError('#password-reset-request-error', '');
  try {
    await api('/api/auth/password/reset/start', {handle: $('#password-reset-handle').value.trim()});
    if (state.csrf !== csrf || !authDialog.open || state.authPanel !== 'reset-request') return;
    setText('#password-reset-request-status', 'If this account has a verified email address, a reset link will arrive shortly.');
    $('#password-reset-request-status').hidden = false;
  } catch (error) {
    if (authDialog.open && state.authPanel === 'reset-request' && state.csrf === csrf) setError('#password-reset-request-error', error.message);
  } finally { state.passwordRecoveryBusy = false; $('#password-reset-request-submit').disabled = false; }
});
$('#password-reset-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.passwordResetToken || state.passwordResetBusy) return;
  const password = $('#password-reset-new').value;
  if (password !== $('#password-reset-confirm').value) { setError('#password-reset-error', 'The passwords do not match.'); $('#password-reset-confirm').focus(); return; }
  if (password.length < 12 || password.length > 128) { setError('#password-reset-error', 'Use a password of 12–128 characters.'); return; }
  const token = state.passwordResetToken, csrf = state.csrf;
  state.passwordResetBusy = true; $('#password-reset-submit').disabled = true; setError('#password-reset-error', '');
  try {
    const data = await api('/api/auth/password/reset/complete', {token, password});
    if (state.csrf !== csrf) return;
    state.passwordResetToken = null; $('#password-reset-form').reset();
    window.history.replaceState(null, '', safeAuthReturnRoute(state.returnRoute));
    setSession(data);
    setAuthMode('login'); $('#auth-password-options').open = true;
    setText('#auth-description', 'Your password has been reset. Sign in with your new password.');
    $('#auth-handle').focus();
  } catch (error) {
    if (authDialog.open && state.authPanel === 'reset-password' && state.csrf === csrf) setError('#password-reset-error', error.message);
  } finally { state.passwordResetBusy = false; $('#password-reset-submit').disabled = !state.passwordResetToken; }
});

function setAuthMode(mode) {
  if (mode === 'register' && state.access?.mode === 'beta' && !state.invitationToken) mode = 'login';
  state.authMode = mode;
  state.authPanel = 'signin';
  $('#login-mode').classList.toggle('selected', mode === 'login');
  $('#register-mode').classList.toggle('selected', mode === 'register');
  $('#login-mode').setAttribute('aria-pressed', String(mode === 'login'));
  $('#register-mode').setAttribute('aria-pressed', String(mode === 'register'));
  setText('#auth-title', mode === 'login' ? 'Welcome back.' : 'Join the challenge.');
  setText('#auth-description', state.access?.mode === 'beta' ? (mode === 'login' ? 'Sign in to your Crucible account.' : 'Create your account, then verify your invitation email.') : mode === 'login' ? 'Sign in to receive your instance for the current round.' : 'Create an account to investigate challenges and share your results.');
  setText('#auth-submit', mode === 'login' ? 'Sign in →' : 'Create account →');
  $('#auth-password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('#auth-password').minLength = 12;
  setError('#auth-error', '');
  setError('#auth-github-error', '');
  renderAuthMethods();
}
function openAuth(trigger) { setAuthMode(state.authMode); $('#auth-password-options').open = !state.auth?.github_enabled; if (!authDialog.open) openDialog(authDialog, trigger); }
$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const startIntent = state.challengeStartIntent;
  const button = $('#auth-submit');
  button.disabled = true;
  $('#login-mode').disabled = true;
  $('#register-mode').disabled = true;
  setError('#auth-error', '');
  setError('#auth-github-error', '');
  setText('#auth-submit', state.authMode === 'login' ? 'Signing in…' : 'Creating your account…');
  try {
    const data = await api('/api/auth/' + state.authMode, {handle: $('#auth-handle').value.trim(), password: $('#auth-password').value,
      ...(state.authMode === 'register' && state.invitationToken ? {invitation_token: state.invitationToken} : {})});
    const dialogStillOpen = authDialog.open;
    const resumeStart = startIntent?.awaitingSignIn && state.challengeStartIntent === startIntent &&
      !state.user && dialogStillOpen && challengeStartSelectionMatches(startIntent) && startIntent.roundId === data.round?.id;
    setSession(data);
    if (resumeStart && state.user) state.challengeStartIntent = {...startIntent, accountId: state.user.id,
      csrf: state.csrf, awaitingSignIn: false};
    $('#auth-form').reset();
    authDialog.close();
    if (!hasAccess()) { renderAccessGate(); return; }
    if (!state.ready) { await loadAuthorizedWorkspace(); return; }
    await loadChallenge();
    if (state.screen === 'scheme' && state.view === 'leaderboard') await loadLeaderboard();
    toast('Signed in as ' + state.user.handle + '.');
  } catch (error) {
    if (authDialog.open) setError('#auth-error', error.message);
    else globalError(error.message);
  }
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
    if (!hasAccess()) { toast('You are signed out.'); return; }
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
  $('#download-recovery-template').title = !state.user ? 'Sign in to download a result template.' :
    state.importBusy || state.submitBusy ? 'Wait for the current operation to finish.' :
      state.pendingAttempt ? 'Finish the pending submission before downloading another template.' :
        !canSubmitChallenge(state.submissionChallenge) ? 'Choose an open, unanswered challenge to download its template.' :
          'Download a JSON template for this exact challenge.';
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
    if (!target.scheme || !target.profile) throw new Error('This instance’s scheme, release, or parameter profile is unavailable. Refresh the page and try again.');
    const result = parseRecoveryFile(text, challenge);
    if (!confirmSubmissionReplacement('import this result')) return;
    // Commit target and draft together, only after the whole file is valid.
    state.submissionChallenge = challenge;
    renderSubmissionTarget();
    renderMethodOptions('new');
    const fields = {
      '#candidate': result.candidate, '#method-name': result.method.name,
      '#method-pseudocode': result.method.pseudocode, '#hardware': result.compute.hardware,
      '#workers': result.compute.workers, '#wall-seconds': result.compute.wall_seconds,
      '#precomputation': result.compute.precomputation, '#peak-memory': result.compute.peak_memory,
      '#compute-notes': result.compute.notes, '#ai-model': result.ai_model
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
function submissionDraftSnapshot() {
  // Only user-authored values: file controls and display state are not the draft.
  return JSON.stringify(['candidate', 'method-select', 'method-name', 'method-pseudocode', 'ai-model',
    'hardware', 'workers', 'wall-seconds', 'precomputation', 'peak-memory', 'compute-notes'].map(id => {
    const value = $('#' + id).value;
    return id === 'method-select' ? value || 'new' : id === 'workers' ? value || '1' : value;
  }));
}
function resetSubmissionForm() {
  $('#submission-form').reset();
  renderMethodOptions('new');
  state.submissionDraftBaseline = submissionDraftSnapshot();
}
function confirmSubmissionReplacement(action) {
  return state.submissionDraftBaseline === null || submissionDraftSnapshot() === state.submissionDraftBaseline ||
    window.confirm(`Discard your unsent submission draft and ${action}? This cannot be undone.`);
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
  return scheme && profile ? `${scheme.name} (${profileLabel(profile, scheme)})` : challenge?.profile_id || 'Choose an instance';
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
  if (state.submitBusy || state.importBusy || (state.challengeLoading && !state.pendingAttempt)) return;
  // An uncertain answer must keep its original target and retry identifier.
  let challenge = state.pendingAttempt ? state.submissionChallenge : isChallengeOpen() ? state.challenge : null;
  if (state.submissionChallenge?.id !== challenge?.id && !confirmSubmissionReplacement('switch to another instance')) {
    challenge = state.submissionChallenge;
  }
  const accountId = state.user?.id;
  const sameChallenge = state.submissionChallenge?.id === challenge?.id;
  if (!sameChallenge) {
    resetSubmissionForm();
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
      const aiModel = normalizeAIModel($('#ai-model').value);
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
        payload: {candidate: $('#candidate').value.toLowerCase(), method_id: methodId, compute, ai_model: aiModel, idempotency_key: crypto.randomUUID()}
      };
    }
    lockSubmissionFields(true);
    const data = await api('/api/challenges/' + encodeURIComponent(state.pendingAttempt.challengeId) + '/submit', state.pendingAttempt.payload);
    if (state.challenge?.id === data.challenge.id) state.challenge = data.challenge;
    state.pendingAttempt = null;
    resetSubmissionForm();
    lockSubmissionFields(false);
    submissionDialog.close();
    renderChallenge();
    toast(data.submission.status === 'correct' ? 'Key recovered. Your recovery has been recorded.' : 'Answer recorded. The key did not match this instance.');
    // A progress hint refresh cannot change the accepted submission receipt.
    if (data.submission.status === 'correct') refreshRecoveryViews();
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

function historyPreview() {
  const preview = node('div', undefined, 'history-preview');
  preview.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 4; index++) {
    const item = node('div', undefined, 'history-item');
    const copy = node('span', undefined, 'history-item-copy');
    for (const className of ['history-item-title', 'history-item-meta', 'history-item-meta']) {
      const line = node('span', undefined, className);
      line.append(node('span', undefined, 'catalogue-placeholder'));
      copy.append(line);
    }
    item.append(copy); preview.append(item);
  }
  return preview;
}
async function showHistory() {
  if (!hasAccess() || !state.user) return;
  const request = ++state.historyRequest;
  const accountId = state.user?.id, epoch = readViewEpoch;
  const current = () => hasAccess() && epoch === readViewEpoch && request === state.historyRequest && historyDialog.open && state.user?.id === accountId;
  const target = $('#history-content');
  const key = 'history:' + (state.catalogueRevision || ''), cached = cachedRead(key);
  const paint = data => {
    const focused = document.activeElement?.closest('#history-content') === target ? document.activeElement : null;
    const detailsTrigger = dialogTriggers.get(recoveryDialog);
    const detailsId = detailsTrigger?.closest('#history-content') === target ? detailsTrigger.dataset.submissionId : null;
    paintRead(key, target, data, data => {
      if (!data.submissions?.length) return showEmpty(target, 'No submissions yet. Your received answers will appear here.');
      const items = [];
      for (const submission of data.submissions) {
        const item = node('button', undefined, 'history-item');
        item.type = 'button';
        item.dataset.submissionId = submission.id;
        item.setAttribute('aria-haspopup', 'dialog');
        const copy = node('span', undefined, 'history-item-copy');
        const scheme = availableReleases().find(release => release.id === submission.scheme_id &&
          String(release.version) === String(submission.scheme_version));
        const profile = scheme?.profiles?.find(profile => profileId(profile) === submission.profile_id);
        const name = scheme?.name || state.schemes.find(release => release.id === submission.scheme_id)?.name || submission.scheme_id;
        const title = `${name || 'Scheme'} (${profile ? profileLabel(profile, scheme) : recordProfileLabel(submission)})`;
        copy.append(node('span', title, 'history-item-title'), node('span', submission.method_name || 'Submitted method', 'history-item-meta'), node('span', utcDate(submission.received_at), 'history-item-meta'));
        item.setAttribute('aria-label', 'View submission for ' + title + ' received ' + utcDate(submission.received_at));
        item.addEventListener('click', () => openSubmissionDetails(submission.id, item));
        item.append(copy, node('span', submission.status === 'correct' ? 'Recovered' : 'Incorrect', 'status-tag' + (submission.status === 'correct' ? '' : ' error-tag')));
        items.push(item);
      }
      target.replaceChildren(...items);
    });
    const replacement = id => [...target.querySelectorAll('[data-submission-id]')].find(item => item.dataset.submissionId === id) || historyDialog.querySelector('[data-close]');
    if (focused && !focused.isConnected && !recoveryDialog.open) replacement(focused.dataset.submissionId).focus({preventScroll: true});
    if (detailsId && !detailsTrigger.isConnected) dialogTriggers.set(recoveryDialog, replacement(detailsId));
  };
  // Populate the first frame before opening the dialog. Its scroll viewport is
  // fixed independently of the number of results, including empty/error states.
  if (cached) paint(cached);
  else {
    const focused = document.activeElement?.closest('#history-content') === target ? document.activeElement : null;
    quietReadStatus(target, 'Loading your submissions…'); target.append(historyPreview());
    if (focused && historyDialog.open && !recoveryDialog.open) historyDialog.querySelector('[data-close]').focus({preventScroll: true});
  }
  target.setAttribute('aria-busy', 'true');
  if (!historyDialog.open) openDialog(historyDialog, $('#history-button'));
  try {
    const data = await readAgain(key, '/api/me/submissions');
    if (!current()) return;
    if (!Array.isArray(data.submissions) || data.submissions.some(item => !item || typeof item.id !== 'string' || !item.id)) throw new Error('Your submissions could not be loaded. Please try again.');
    rememberRead(key, {submissions: data.submissions});
    paint({submissions: data.submissions});
  } catch (error) {
    if (current()) readRefreshError(key, target, error, () => showHistory(), cached);
  } finally {
    if (current()) target.setAttribute('aria-busy', 'false');
  }
}
async function copyInstance() {
  if (!state.challenge) return;
  const id = state.challenge.id;
  const accountId = state.user?.id;
  $('#copy-instance').disabled = true;
  try {
    // Copy and Download must use the same authoritative challenge contract.
    const payload = await api('/api/challenges/' + encodeURIComponent(id) + '/download', undefined, {raw: true});
    if (state.challenge?.id !== id || state.user?.id !== accountId) return;
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
  else setError('#profile-error', `Choose a published integer stage from ${$('#profile-select').min} to ${$('#profile-select').max}.`);
}
function stepInstanceSize(direction) {
  const scheme = selectedScheme();
  const profiles = orderedProfiles(scheme);
  const position = stagePosition(state.profile);
  const profile = direction > 0 ? profiles.find(profile => stagePosition(profile) > position) : profiles.findLast(profile => stagePosition(profile) < position);
  if (profile) navigate(schemeRoute(scheme, profile, state.view));
}
function sliderProfile() {
  const profiles = orderedProfiles(selectedScheme());
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
$('#copy-agent-instructions').addEventListener('click', copyAgentInstructions);
$('#use-agent').addEventListener('click', toggleAgentInstructions);
$('#method-select').addEventListener('change', updateMethodFields);
$('#refresh-leaderboard').addEventListener('click', refreshLeaderboard);
$('#history-button').addEventListener('click', showHistory);
$('#retry-button').addEventListener('click', () => state.ready ? synchronizeSession() : initialize());
$('#refresh-catalogue').addEventListener('click', async () => {
  $('#refresh-catalogue').disabled = true;
  try { if (await refreshProfiles() !== false) globalError(''); }
  catch (error) { globalError(error.message); }
  finally { $('#refresh-catalogue').disabled = false; }
});
$('.skip-link').addEventListener('click', event => {
  event.preventDefault();
  const target = $(workspaceSelector(state.screen));
  target.focus();
  target.scrollIntoView();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) void synchronizeSession().finally(scheduleBrowseWarmup); });
window.addEventListener('online', synchronizeSession);
window.addEventListener('hashchange', () => applyRoute(true));


// A proposal is a description for review. No uploaded instructions or links run.
const PROPOSAL_AGENT_UI = {
  draft: {button: '#proposal-use-agent', panel: '#proposal-agent-instructions', text: '#proposal-agent-text', followup: '#proposal-agent-followup', copy: '#copy-proposal-agent'},
  revision: {button: '#proposal-revise-agent', panel: '#proposal-revise-agent-instructions', text: '#proposal-revise-agent-text', followup: '#proposal-revise-agent-followup', copy: '#copy-proposal-revise-agent'}
};
function proposalAgentTarget(kind) {
  if (state.screen !== 'propose') return null;
  let record = null;
  if (kind === 'revision') {
    record = proposals.agentRevision;
    if (!state.user || !record || proposals.detailId !== record.id) return null;
  } else {
    if (proposals.detailId || proposals.reviewMode || proposals.pending || proposals.submitting) return null;
    if (proposals.revisingId) {
      record = proposals.revisionBase;
      if (!state.user || !record || record.id !== proposals.revisingId) return null;
    }
  }
  return {record, key: JSON.stringify([state.user?.id || null, record?.id || 'new', record?.revision || null, record?.content_sha256 || null])};
}
function proposalAgentPrompt(record) {
  const origin = siteOrigin();
  const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  const client = 'python3 ./crucible_cli.py --server ' + quote(origin);
  const folder = record ? './crucible-proposal-' + record.id.slice(0, 8) + '-r' + (record.revision + 1) : './crucible-proposal';
  const initialize = record ? `proposal revise ${quote(record.id)}` : 'proposal init';
  const base = record ? `\nExpected base: revision ${record.revision}, content SHA-256 ${record.content_sha256}. If the fetched revision or hash differs, show me the change before editing.\n` : '\n';
  const login = `${client} login`;
  return `${record ? 'Help me revise my KEM proposal' : 'Help me prepare a new KEM proposal'} for Crucible.\n\nClient: ${origin}/api/agent/client — save as crucible_cli.py and inspect before running.\nBefore private access or submission, run ${login} if needed and let me approve the proposal permissions in my browser. Existing connections need login once to add those permissions.\n\nBefore work, read and follow the model pipeline contract at ${origin}/api/agent/pipeline: run ${client} guide. It identifies the supported workflows, exact source contracts and permission boundaries. The workspace saves a copy as pipeline.json; never put credentials in prompts or research files.\n\nPrepare a fresh workspace:\n${client} ${initialize} --path ${quote(folder)}\n${base}\nRead the proposal guide saved in that workspace (also at ${origin}/api/protocol/proposal-guide), then follow its README, template and schema. Explain what it adds over the closest existing scheme. Describe complete reproducible algorithms and sampling distributions, public and private encodings, correctness bound and scope, security assumptions, and identified implementation/evidence. Complete correctness.justification with a precise statement, conditions, configuration coverage and supporting reasoning; distinguish proved, modeled and conjectural steps. Empirical tests are supplemental. Formal verification is optional and conditional guarantees must state their conditions. Reference implementation measurement policy: ${origin}/api/protocol/reference-method. Timings describe the identified implementation, not a full TLS handshake or a security guarantee. Complete the template’s tier_design plan: describe the supported configuration space or reviewed generation rule, with stable configuration identities, and a default progression through natural parameter settings. Its positions are stages; their count is scheme-specific and equal stage numbers do not imply equal security. Preserve the construction and sampling distributions, explaining any coordinated parameter changes. For approval, state a precise correctness guarantee: zero honest disagreement or failure probability at most 2^-128 for every admitted configuration, with probability scope and assumptions. Also state the security notion, claimed guarantee and applicable parameters, with all underlying assumptions. If security depends on a correctness bound, identify that dependency and its required threshold. A claim may depend on conjectures or a heuristic model; distinguish the conditional argument from the evidence for each premise, independently motivate those premises and identify unproved steps for stress testing. Use justification.kind conjectural-claim where appropriate. Unknown correctness is acceptable in an early draft but needs a stated guarantee before approval. The common correctness target remains at most 2^-128; the claim may be conditional, but the target is not optional. For a statistical claim, declare correctness.failure_bound_exponent as an integer at least 128, consistent with the bound and assumptions. Perfect correctness states failure_bound "0" and omits the exponent; correctness.policy_id is optional. A proof gap undermines justification; a rigorous demonstration that actual failure probability exceeds the target refutes the guarantee. A looser upper bound alone is not a refutation. Report generation aborts separately. State the endpoint target, evidence and status (supported-estimate, provisional or unestimated), retaining meaningful published or justified endpoints; justify useful intermediate stages. Prefer a default stage count in multiples of ten, with multiples of five as a fallback when the reviewed configurations support it; preserve endpoints and useful intermediate configurations, and never invent settings or compromise correctness or fidelity to meet a count. Complete parameter_selection: justify efficient admissible configurations under a stated cost objective, constraints, pinned security model and candidate domain; document comparison evidence, tradeoffs and uncertainty without claiming global optimality. Never add padding or restrict entropy just to manufacture stages. Keep unknowns explicit. Ask me for missing information; do not invent proofs, benchmarks or security claims. Spend a separate, deliberate pass on citations: read the primary sources, check exact versions and claim scope, and identify the supporting section, theorem or page in the relevant argument. Cite existing Crucible notes by their exact revision link and content hash, with permission for any private material. Separate mathematical ingredients from a published complete KEM and from inherited security claims. Mark inaccessible or unverified sources and originality uncertainty explicitly; never invent citations or add unrelated ones for appearance.\n\nCheck the specification:\n${client} proposal validate --path ${quote(folder)}\nShow me the specification and validation result. Only when I ask you to submit, run:\n${client} proposal submit --path ${quote(folder)}\nSubmission creates a private research note containing the exact specification and its readable explanation; revisions preserve the earlier notes and reviews. Return the note link and review link from the receipt. Submission requests human review; it does not approve or activate the scheme.`;
}
function resetProposalAgentInstructions(kind = null) {
  for (const name of kind ? [kind] : Object.keys(PROPOSAL_AGENT_UI)) {
    const ui = PROPOSAL_AGENT_UI[name];
    proposals.agentKeys[name] = null;
    $(ui.panel).hidden = true;
    $(ui.button).setAttribute('aria-expanded', 'false');
    setText(ui.text, ''); setText(ui.followup, '');
  }
}
function renderProposalAgentControls() {
  for (const [kind, ui] of Object.entries(PROPOSAL_AGENT_UI)) {
    const target = proposalAgentTarget(kind);
    $(ui.button).disabled = !target;
    if (kind === 'revision') $(ui.button).hidden = !target;
    if (proposals.agentKeys[kind] && proposals.agentKeys[kind] !== target?.key) resetProposalAgentInstructions(kind);
  }
  setText('#proposal-use-agent', proposals.revisingId ? 'Revise with an agent →' : 'Use with an agent →');
}
function showProposalAgentInstructions(kind) {
  const target = proposalAgentTarget(kind);
  if (!target) return;
  const ui = PROPOSAL_AGENT_UI[kind];
  proposals.agentKeys[kind] = target.key;
  setText(ui.text, proposalAgentPrompt(target.record));
  setText(ui.followup, target.record ? `“Update my Crucible proposal ${target.record.id} using the reviewer feedback.”` : '“Help me prepare a new KEM proposal for Crucible.”');
  $(ui.panel).hidden = false;
  $(ui.button).setAttribute('aria-expanded', 'true');
}
async function copyProposalAgentInstructions(kind) {
  const target = proposalAgentTarget(kind);
  if (!target) return;
  const ui = PROPOSAL_AGENT_UI[kind];
  $(ui.copy).disabled = true;
  const current = () => proposalAgentTarget(kind)?.key === target.key && proposals.agentKeys[kind] === target.key;
  try {
    await navigator.clipboard.writeText(proposalAgentPrompt(target.record));
    if (current()) toast('Proposal prompt copied. Give it to your agent.');
  } catch {
    if (current()) { showProposalAgentInstructions(kind); toast('Copy the proposal instructions shown below.'); }
  } finally { $(ui.copy).disabled = false; }
}
const SPECIFICATION_LABELS = {
  setup: 'Setup', keygen: 'Key generation', encapsulate: 'Encapsulation', decapsulate: 'Decapsulation',
  tier_meaning: 'Meaning of a stage', supported_tiers: 'Supported stages', mapping: 'Parameter mapping',
  distribution: 'Sampling distribution', reuse: 'Parameter reuse', public_key: 'Public key', private_key: 'Private key',
  ciphertext: 'Ciphertext', shared_secret: 'Shared secret', shared_secret_bytes: 'Shared-secret bytes',
  kind: 'Claim type', failure_bound: 'Failure bound', argument: 'Argument', scope: 'Scope', evidence: 'Evidence',
  policy_id: 'Common correctness policy', failure_bound_exponent: 'Failure probability at most 2 to the negative exponent',
  notion: 'Security notion', model: 'Attack model', claim: 'Claim', assumptions: 'Assumptions',
  url: 'URL', sha256: 'SHA-256', runtime: 'Runtime', entrypoint: 'Entry point',
  parameter_progression: 'Parameter progression', cost_tradeoff: 'Resource costs', fixed_correctness: 'Common correctness requirement',
  endpoint_status: 'Endpoint status', endpoint_target: 'Endpoint target', endpoint_evidence: 'Endpoint evidence', intermediate_steps: 'Intermediate steps', parameter_selection: 'Parameter selection',
  objective: 'Cost objective', constraints: 'Fixed constraints', security_model: 'Security and cost model',
  statement: 'Mathematical statement', conditions: 'Conditions', configuration_coverage: 'Configuration coverage', derivation: 'Derivation',
  search_domain: 'Candidate search domain', comparison_evidence: 'Candidate comparison evidence', tradeoffs_and_uncertainty: 'Tradeoffs and uncertainty'
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
function hasProposalTierDesign(spec) {
  const plan = spec.tier_design;
  return plan && typeof plan === 'object' && !Array.isArray(plan) &&
    ['supported-estimate', 'provisional', 'unestimated'].includes(plan.endpoint_status) &&
    ['parameter_progression', 'cost_tradeoff', 'fixed_correctness', 'endpoint_target', 'endpoint_evidence', 'intermediate_steps']
      .every(key => typeof plan[key] === 'string' && plan[key].trim());
}
function hasProposalParameterSelection(spec) {
  const plan = spec.tier_design?.parameter_selection;
  return plan && typeof plan === 'object' && !Array.isArray(plan) &&
    ['objective', 'constraints', 'security_model', 'search_domain', 'comparison_evidence', 'tradeoffs_and_uncertainty']
      .every(key => typeof plan[key] === 'string' && plan[key].trim());
}
function proposalStatusLabel(status) {
  return {submitted: 'Review pending', changes_requested: 'Changes requested', approved: 'Approved'}[status] || status;
}
function previewValue(key, value) {
  if (value === null || value === undefined) return key === 'shared_secret_bytes' ? 'See shared-secret encoding' : 'Not provided';
  if (key === 'endpoint_status') return {'supported-estimate': 'Supported estimate', provisional: 'Provisional', unestimated: 'Unestimated'}[value] || String(value);
  if (key === 'kind' && ['mathematical-proof', 'analytical-bound', 'model-based-analysis', 'conjectural-claim'].includes(value)) return {'mathematical-proof': 'Mathematical proof', 'analytical-bound': 'Analytical bound', 'model-based-analysis': 'Model-based analysis', 'conjectural-claim': 'Conjectural claim'}[value];
  if (key === 'supported_tiers' && value && typeof value === 'object') {
    if (value.kind === 'range') return `${value.min}–${value.max} · step ${value.step}`;
    if (value.kind === 'list' && Array.isArray(value.values)) return value.values.join(', ');
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function renderSpecification(target, spec) {
  target.replaceChildren(node('h2', spec.name), node('p', 'Version ' + spec.version, 'proposal-version'), node('p', spec.summary, 'proposal-summary'));
  for (const [key, label] of [['parameters', 'Parameters'], ['tier_design', 'Default progression'], ['algorithms', 'Algorithms'], ['encoding', 'Encoding'],
    ['correctness', 'Correctness claim'], ['security', 'Security claim'], ['implementation', 'Implementation']]) {
    const section = node('details', undefined, 'proposal-section');
    section.open = key === 'parameters';
    section.append(node('summary', label));
    const value = spec[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const facts = node('dl', undefined, 'detail-list proposal-facts');
      for (const [field, content] of Object.entries(value)) {
        if (['parameter_selection', 'justification'].includes(field) && content && typeof content === 'object' && !Array.isArray(content)) {
          for (const [choice, description] of Object.entries(content)) addFact(facts,
            field === 'justification' && choice === 'kind' ? 'Justification type' : SPECIFICATION_LABELS[choice] || choice,
            previewValue(choice, description));
        } else addFact(facts, SPECIFICATION_LABELS[field] || field, previewValue(field, content));
      }
      section.append(facts);
    } else section.append(node('p', key === 'tier_design'
      ? 'Not provided in this revision. A default-progression plan is required before approval: reviewed configurations, faithful parameter scaling, resource costs, a common correctness bound, endpoint target and evidence or provisional status, and useful intermediate stages.'
      : 'Not provided. Implementation review is required before activation.', 'field-hint'));
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
  renderProposalAgentControls();
  const busy = proposals.validating || proposals.submitting;
  $('#upload-proposal').disabled = busy || Boolean(proposals.pending) || Boolean(proposals.revisingId && !proposals.revisionBase);
  $('#proposal-file').disabled = $('#upload-proposal').disabled;
  $('#proposal-review').hidden = !proposals.specification || Boolean(proposals.receipt);
  $('#submit-proposal').disabled = busy || !proposals.specification;
  if (proposals.revisingId && !proposals.revisionBase) $('#submit-proposal').disabled = true;
  setText('#submit-proposal', proposals.submitting ? 'Submitting…' : !state.user ? 'Sign in to submit' : proposals.pending ? 'Retry submission' : proposals.revisingId ? 'Submit revised specification' : 'Submit proposal');
  $('#proposal-sign-in').hidden = Boolean(state.user);
  $('#refresh-proposals').hidden = !state.user || (proposals.reviewMode && !state.canReviewProposals);
  if (proposals.receipt) {
    proposalStatus('Submitted for review. ');
    const link = node('a', 'View your proposal', 'text-button');
    link.href = proposals.receipt.source_note?.url || '#propose?proposal=' + encodeURIComponent(proposals.receipt.id);
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
  resetProposalAgentInstructions();
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
  resetProposalAgentInstructions();
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
  const agentRouteKey = JSON.stringify([id, revise, reviewMode]);
  if (proposals.agentRouteKey !== agentRouteKey) resetProposalAgentInstructions();
  proposals.agentRouteKey = agentRouteKey;
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
  $('#proposal-page-back').hidden = reviewMode;
  $('#proposal-context').hidden = reviewMode;
  $('#proposal-detail-back').href = reviewMode ? reviewerRoute('schemes') : '#notebook?compose=proposal';
  setText('#proposal-detail-back', reviewMode ? '← Scheme proposals' : '← New proposal note');
  $('#proposal-workspace').classList.toggle('proposal-review-workspace', reviewMode);
  $('#proposal-draft').hidden = Boolean(id) || reviewMode;
  $('#proposal-detail').hidden = !id;
  $('#proposal-revision-context').hidden = !revise;
  setText('#proposal-title', revise ? 'Revise your proposal' : reviewMode ? 'Scheme proposals' : 'Propose a scheme');
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
  renderProposalAgentControls();
  if (params.get("agent") === "1" && !id && !reviewMode) showProposalAgentInstructions("draft");
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
  const current = () => request === proposals.listRequest && account === state.user?.id && proposalPageActive() && proposals.reviewMode === reviewing;
  try {
    const result = await api(reviewing ? '/api/protocol/review-queue' + (after ? '?' + new URLSearchParams({after}) : '') : '/api/me/protocol-proposals');
    if (!current()) return;
    if (!Array.isArray(result.proposals) || result.proposals.some(record => !validProposal(record))) throw new Error('Your proposals could not be loaded.');
    if (!after) target.replaceChildren();
    if (!result.proposals.length && !after) return showEmpty(target, reviewing ? 'No proposals awaiting your review.' : 'No submitted proposals yet.');
    for (const record of result.proposals) {
      const link = node('a', undefined, 'proposal-list-entry');
      link.href = reviewing ? reviewerRoute('schemes', {proposal: record.id}) : '#propose?proposal=' + encodeURIComponent(record.id);
      link.append(node('span', record.specification.name, 'proposal-list-name'), node('span', 'Revision ' + record.revision + ' · ' + (record.retracted ? 'Retracted' : proposalStatusLabel(record.status)) + (reviewing && record.author?.handle ? ' · ' + record.author.handle : '') + ' · ' + utcDate(record.created_at), 'proposal-list-meta'));
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
  const current = () => request === proposals.detailRequest && account === state.user?.id && proposalPageActive() && proposals.detailId === id;
  try {
    const result = await api('/api/protocol/proposals/' + encodeURIComponent(id));
    if (!current()) return;
    if (!validProposal(result.proposal, id)) throw new Error('The proposal details could not be loaded.');
    const record = result.proposal;
    proposals.detailRecord = record;
    proposals.detailOwn = result.author?.id === account;
    renderSpecification(target, record.specification);
    if (record.source_note?.id) {
      const source = node('a', 'Read proposal note →', 'text-button');
      source.href = '#notebook?' + new URLSearchParams({note:record.source_note.id, revision:String(record.source_note.revision)});
      target.prepend(source);
    }
    target.prepend(node('p', 'Revision ' + record.revision + ' · ' + (record.retracted ? 'Retracted' : proposalStatusLabel(record.status)) + ' · ' + utcDate(record.created_at), 'proposal-status'));
    window.CrucibleRetractions.mount(target, {record, kind:'scheme', api, current, onSuccess:async () => { await loadProposalDetail(id); if (proposalPageActive()) loadProposals(); }});
    const testOnlyReview = (result.reviews || []).some(review => review.revision === record.revision && review.checks?.test_only_self_review === true);
    target.append(node('p', record.retracted ? 'The proposer withdrew this request. Earlier evidence and review decisions remain in the history.' : testOnlyReview ? 'Development self-review only. This decision does not satisfy independent admission review or earn scheme contribution points.' : record.status === 'approved' ? 'A manual reviewer approved this revision for trusted integration. Approval does not activate a live challenge or certify security.' : 'Format checked only. This submitted specification has not been approved or activated as a live challenge.', 'panel-footnote'),
      node('p', 'Content SHA-256 · ' + record.content_sha256, 'proposal-digest'));
    renderProposalAudit(target, result);
    const own = result.author?.id === account;
    proposals.agentRevision = own && !record.retracted ? record : null;
    renderProposalAgentControls();
    $('#proposal-detail-actions').hidden = !own && !(state.canReviewProposals && result.author?.id);
    $('#revise-proposal').hidden = !own || record.retracted;
    $('#revise-proposal').href = record.source_note?.id ? '#notebook?note=' + encodeURIComponent(record.source_note.id) : '#propose?revise=' + encodeURIComponent(record.id);
    $('#revise-proposal').textContent = record.source_note ? 'Revise proposal note →' : 'Upload a revised specification →';
    if (proposals.reviewMode && !record.retracted && (!own || state.canSelfReviewProposals) && result.author?.id && state.canReviewProposals && (record.status === 'submitted' || proposals.reviewPending?.id === record.id)) showProposalReview(record, true);
  } catch (error) { if (current()) showInlineError(target, error.message, () => loadProposalDetail(id)); }
}
async function loadProposalRevision(id) {
  const request = ++proposals.detailRequest;
  const account = state.user?.id;
  proposals.detailId = null;
  proposals.revisionBase = null;
  clearProposalReview();
  quietReadStatus($('#proposal-revision-context'), 'Loading the revision to replace');
  renderProposalDraft();
  const current = () => request === proposals.detailRequest && account === state.user?.id && proposalPageActive() && proposals.revisingId === id && !proposals.detailId;
  if (!account) { setText('#proposal-revision-context', 'Sign in to upload a revised specification.'); return; }
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) { setText('#proposal-revision-context', 'This proposal link is invalid.'); return; }
  try {
    const result = await api('/api/protocol/proposals/' + encodeURIComponent(id));
    if (!current()) return;
    if (!validProposal(result.proposal, id) || result.author?.id !== account) throw new Error('Only the proposer can upload a revised specification.');
    if (result.proposal.retracted) throw new Error('This proposal was retracted. Start a new proposal to pursue a revised idea.');
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
    if (revision.source_note?.id) {
      const source = node('a', 'Read the note submitted with this revision →', 'text-button');
      source.href = '#notebook?' + new URLSearchParams({note:revision.source_note.id, revision:String(revision.source_note.revision)});
      content.append(source);
    }
    item.append(content);
    history.append(item);
    for (const review of reviews.filter(review => review.revision === revision.revision)) {
      const note = node('div', undefined, 'proposal-audit-note');
      note.append(node('h4', (review.checks?.test_only_self_review === true ? 'Development self-review · ' : '') + proposalStatusLabel(review.decision) + (review.reviewer?.handle ? ' · ' + review.reviewer.handle : '')),
        node('p', typeof review.notes === 'string' ? review.notes : '', 'proposal-summary'),
        node('p', 'Evidence', 'eyebrow'), node('p', typeof review.evidence === 'string' ? review.evidence : '', 'proposal-summary'),
        node('p', review.checks?.tier_design === true ? 'Default progression: reviewed.' : 'Default progression: not reviewed in this decision.', 'field-hint'),
        node('p', review.checks?.parameter_selection === true ? 'Parameter selection: reviewed.' : 'Parameter selection: not reviewed in this decision.', 'field-hint'));
      if (review.checks?.test_only_self_review === true) note.append(node('p', 'Test only; independent admission and scheme contribution credit still require another reviewer.', 'field-hint'));
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
        if (request !== proposals.detailRequest || account !== state.user?.id || !proposalPageActive() || proposals.detailId !== id) return;
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
  proposals.agentRevision = null;
  resetProposalAgentInstructions('revision');
  $('#proposal-revise-agent').hidden = true;
  proposals.detailRecord = null;
  proposals.detailOwn = false;
  $('#proposal-self-review-notice').hidden = true;
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
  if (state.screen !== 'reviews' || state.reviewSection !== 'schemes' || !proposals.reviewMode || !state.canReviewProposals) return;
  $('#proposal-review-form').hidden = false;
  $('#proposal-self-review-notice').hidden = !proposals.detailOwn;
  const approvalOption = $('#proposal-decision').querySelector('[value="approved"]');
  if (approvalOption) approvalOption.textContent = proposals.detailOwn ? 'Approve for testing only' : 'Approve for trusted integration';
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
    link.href = reviewerRoute('schemes', {proposal: pending.id});
    $('#proposal-review-status').append(link);
    $('#proposal-review-status').hidden = false;
  }
}
async function submitProposalReview(event) {
  event.preventDefault();
  const record = proposals.detailRecord;
  if (!proposalPageActive() || !proposals.reviewMode || !record || proposals.reviewBusy || !state.canReviewProposals || $('#proposal-review-form').hidden || (proposals.reviewPending && proposals.reviewPending.id !== record.id)) return;
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
    if (decision === 'approved' && !hasProposalTierDesign(record.specification)) return setError('#proposal-review-error', 'Approval requires a default-progression plan in this specification. Ask the proposer to submit a new revision with the completed plan.');
    if (decision === 'approved' && !hasProposalParameterSelection(record.specification)) return setError('#proposal-review-error', 'Approval requires a parameter-selection plan: objective, constraints, pinned model, search domain, candidate comparisons and uncertainty. Submit a completed revision first.');
    const correctness = record.specification.correctness;
    const meetsCorrectnessTarget = correctness?.kind === 'perfect' && correctness.failure_bound === '0' ||
      correctness?.kind === 'statistical' && Number.isInteger(correctness.failure_bound_exponent) &&
      correctness.failure_bound_exponent >= 128 && correctness.failure_bound_exponent <= 1_000_000;
    if (decision === 'approved' && !meetsCorrectnessTarget) return setError('#proposal-review-error', 'Approval requires an explicit correctness claim of perfect agreement or failure probability at most 2^-128. For a statistical claim, declare failure_bound_exponent as an integer at least 128. Assumptions may remain conjectural; weaker or unknown claims remain drafts.');
    const justification = correctness?.justification;
    if (decision === 'approved' && (!['mathematical-proof', 'analytical-bound', 'model-based-analysis', 'conjectural-claim'].includes(justification?.kind) || !['statement', 'conditions', 'configuration_coverage', 'derivation'].every(key => typeof justification?.[key] === 'string' && justification[key].trim()) || typeof record.specification.encoding?.private_key !== 'string' || !record.specification.encoding.private_key.trim())) return setError('#proposal-review-error', 'Approval requires an explicit correctness claim, supporting reasoning and assumptions covering every admitted configuration, plus the private-key representation. Conjectural dependencies must be identified; proof of every assumption is not required. Ask for a completed revision first.');
    if (decision === 'approved' && (!checks.parameter_selection || !checks.tier_design || !Object.values(checks).every(Boolean) || !/^[a-f0-9]{64}$/.test(implementation || '') || [...evidence].length < 20 || record.specification.correctness.kind === 'unknown')) return setError('#proposal-review-error', 'Approval requires all review checks, an explicit correctness claim, an identified implementation artifact, and a documented reproduction record.');
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
    if (proposalPageActive()) {
      if (proposals.detailId === pending.id) loadProposalDetail(pending.id);
      loadProposals();
    }
  } catch (error) {
    if (request !== proposals.reviewRequest || account !== state.user?.id) return;
    const uncertain = !error.status || error.status >= 500;
    if (!uncertain) proposals.reviewPending = null;
    if (proposalPageActive() && proposals.detailId === pending.id) {
      setError('#proposal-review-error', error.message + (error.fieldErrors?.length ? ' ' + error.fieldErrors.map(item => item.path + ': ' + item.message).join(' ') : ''));
      setText('#proposal-review-status', uncertain ? 'The review could not be confirmed. Retry to recover the same decision receipt.' : 'The review was not recorded. Reopen the proposal if its revision has changed.');
      $('#proposal-review-status').hidden = false;
    }
  } finally {
    if (request === proposals.reviewRequest) {
      proposals.reviewBusy = false;
      if (proposalPageActive() && proposals.detailId === pending.id && proposals.detailRecord) showProposalReview(proposals.detailRecord);
    }
  }
}
$('#upload-proposal').addEventListener('click', () => $('#proposal-file').click());
$('#proposal-file').addEventListener('change', event => { const file = event.target.files?.[0]; event.target.value = ''; importProposal(file); });
$('#submit-proposal').addEventListener('click', submitProposal);
$('#refresh-proposals').addEventListener('click', loadProposals);
$('#proposal-sign-in').addEventListener('click', () => openAuth($('#proposal-sign-in')));
$('#proposal-review-form').addEventListener('submit', submitProposalReview);
for (const [kind, ui] of Object.entries(PROPOSAL_AGENT_UI)) {
  $(ui.button).addEventListener('click', () => {
    if ($(ui.panel).hidden) showProposalAgentInstructions(kind);
    else resetProposalAgentInstructions(kind);
  });
  $(ui.copy).addEventListener('click', () => copyProposalAgentInstructions(kind));
}
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
  if (!hasAccess()) name = 'access';
  if (!ownResearcherProfile() && researcherEditor.open) suspendResearcherEditor();
  if (name !== 'person') renderResearcherProfile(null);
  researcherEditorControls();
  const transferLoadingFocus = name !== 'loading' && document.activeElement?.closest?.('#route-loading') === routeLoadingPanel;
  if (!RESEARCH_ENABLED && name === 'research') name = 'catalogue';
  const [pendingPath, pendingQuery = ''] = window.location.hash.split('?');
  const preparingMap = name === 'loading' && hasAccess() && pendingPath === '#notebook' && new URLSearchParams(pendingQuery).get('view') === 'map';
  // Catalogue arrival must not detach the connected map being prepared for
  // this same destination. Other routes and access loss still cancel it.
  if (name !== 'notebook' && !preparingMap) notebookWorkspace?.hide();
  if (name !== 'reviews' || state.reviewSection === 'schemes') reviewNotebookWorkspace?.hide();
  if (name !== 'assumptions') assumptionsWorkspace?.clear();
  const enteringResearch = !research.visible && name === 'research';
  research.visible = name === 'research';
  $('#access-workspace').hidden = name !== 'access';
  if (routeLoadingPanel) {
    routeLoadingPanel.hidden = name !== 'loading';
    if (name !== 'loading') routeLoadingPanel.setAttribute('aria-busy', 'false');
  }
  $('.primary-nav').hidden = name === 'access';
  $('#nav-people').hidden = name === 'access';
  $('#catalogue-workspace').hidden = name !== 'catalogue';
  $('#workspace').hidden = name !== 'scheme';
  $('#overview-workspace').hidden = name !== 'overview';
  $('#assumptions-workspace').hidden = name !== 'assumptions';
  $('#notebook-workspace').hidden = name !== 'notebook';
  $('#review-workspace').hidden = name !== 'reviews';
  $('#review-notebook-workspace').hidden = name !== 'reviews' || state.reviewSection === 'schemes' || !state.canReviewProposals;
  $('#research-workspace').hidden = !research.visible;
  $('#about-workspace').hidden = name !== 'about';
  $('#proposal-workspace').hidden = name !== 'propose' && !(name === 'reviews' && state.reviewSection === 'schemes' && state.canReviewProposals);
  $('#people-workspace').hidden = name !== 'people';
  $('#person-workspace').hidden = name !== 'person';
  $('#connect-workspace').hidden = name !== 'connect';
  const navigationSection = name === 'loading' ? window.CruciblePendingPage?.section?.() :
    ['catalogue', 'scheme', 'overview', 'assumptions', 'propose'].includes(name) ? 'challenge' : name;
  for (const kind of ['challenge', 'people', 'notebook', 'research', 'about']) {
    const button = $('#nav-' + kind);
    const selected = kind === navigationSection;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  let ownProfileSelected = name === 'person' && ownResearcherProfile();
  if (name === 'loading' && hasAccess() && state.user && pendingPath.startsWith('#person/')) {
    try { ownProfileSelected = decodeURIComponent(pendingPath.slice(8)).toLowerCase() === state.user.handle.toLowerCase(); } catch {}
  }
  $('#account-handle').classList.toggle('selected', Boolean(ownProfileSelected));
  if (ownProfileSelected) $('#account-handle').setAttribute('aria-current', 'page');
  else $('#account-handle').removeAttribute('aria-current');
  $('.skip-link').href = workspaceSelector(name);
  if (transferLoadingFocus) $(workspaceSelector(name)).focus({preventScroll: true});
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
    label.append(input, node('span', profileLabel(profile, target)));
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
  quietReadStatus($('#finding-recovery-hint'), 'Loading your correct submissions');
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
$('#nav-people').addEventListener('click', () => navigate('#people'));
$('#nav-notebook').addEventListener('click', () => navigate('#notebook'));
$('#refresh-people').addEventListener('click', () => loadPeople());
$('#more-people').addEventListener('click', () => loadPeople(true));
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

for (const event of ['pointerenter', 'focus']) {
  $('#nav-people').addEventListener(event, () => primeRead('people', '/api/people?limit=50&offset=0&include_records=false'));
  $('#account-handle').addEventListener(event, () => {
    if (state.user) primeRead('person:' + state.user.handle.toLowerCase(), '/api/people/' + encodeURIComponent(state.user.handle) + '?include_milestones=false');
  });
  $('#nav-notebook').addEventListener(event, () => {
    if (!hasAccess() || navigator.connection?.saveData || notebookAssetsReady) return;
    void loadNotebookAsset('/notebook/notebook.css', true).catch(() => {});
    for (const path of ['/notebook/seed.js', '/notebook/store.js', '/notebook/notebook.js']) void loadNotebookAsset(path).catch(() => {});
  });
}
async function initialize() {
  if (documentRetired) return;
  const token = ++initializationRequest, lifecycle = documentEpoch;
  initializationPending = true;
  let revision = state.sessionRevision;
  const current = () => !documentRetired && lifecycle === documentEpoch && token === initializationRequest && revision === state.sessionRevision;
  globalError('');
  if (!state.sessionResolved) {
    $('#access-loading-preview').hidden = false;
    $('#access-workspace').setAttribute('aria-busy', 'true');
    $('#access-workspace').removeAttribute('data-catalogue-pending');
    const path = window.location.hash.split('?')[0];
    setText('#access-title', path === '#notebook' ? 'Research notebook' : path === '#about' ? 'About us' : path === '#people' ? 'Leaderboard' : 'Crucible');
    $('#access-title').removeAttribute('aria-hidden');
    window.CruciblePendingPage?.apply();
    setText('#access-description', 'Checking access…');
  }
  $('#retry-button').disabled = true;
  $('#challenge-loading').hidden = false;
  capturePasswordResetLink();
  captureInvitation();
  try {
    const session = await api('/api/session', undefined, {initialSession: true});
    if (!current()) return;
    setSession(session);
    revision = state.sessionRevision;
    if (await handleAuthenticationRoute()) return;
    if (!current()) return;
    if (!hasAccess()) return;
    await loadAuthorizedWorkspace();
  } catch (error) {
    if (!current()) return;
    if (hasAccess()) { showRouteLoading(); showInlineError(routeLoadingTarget(), error.message, initialize); }
    else {
      document.body.removeAttribute('data-access-pending');
      $('#access-loading-preview').hidden = true;
      $('#access-workspace').setAttribute('aria-busy', 'false');
      if (!state.sessionResolved && error.code === 'network_unreachable') {
        const retry = node('button', 'Try again', 'text-button'); retry.type = 'button';
        retry.id = 'access-retry'; retry.addEventListener('click', initialize);
        $('#access-description').replaceChildren(node('span', 'Access could not be checked. Check your connection, then '), retry);
      } else {
        globalError(error.message);
        if (!state.sessionResolved) setText('#access-description', 'Access could not be checked. Try again.');
      }
    }
    $('#challenge-loading').hidden = true;
  } finally {
    if (token === initializationRequest) initializationPending = false;
    if (current()) $('#retry-button').disabled = false;
  }
}
setInterval(updateClock, 1000);
setInterval(() => { if (!document.hidden) synchronizeSession(); }, 60000);
initialize();

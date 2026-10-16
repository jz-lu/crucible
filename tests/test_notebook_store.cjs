'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Store = require('../web/notebook/store.js');
const Seed = require('../web/notebook/seed.js');

function memoryStorage() {
  const data = new Map();
  return { data, getItem(key) { return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { data.set(key, value); } };
}
function fixture(seed = Seed) {
  const storage = memoryStorage();
  return { storage, store: Store.create(seed, { storage }) };
}
// Earlier exported workspaces could contain generic views. Keep explicit test
// fixtures for that data contract without restoring views to the notebook seed.
function legacySnapshot() {
  const snapshot = Store.clone(Seed);
  const binding = (id, noteIndex, extra) => {
    const note = snapshot.notes[noteIndex];
    return { id, noteId: note.id, noteRevision: 1, excerpt: note.revisions[0].claim,
      scope: 'An explicitly scoped legacy test application.',
      assessment: 'unreviewed', assessmentReason: '', ...extra };
  };
  snapshot.views = [
    { id: 'legacy-argument', title: 'Legacy argument view', kind: 'argument',
      question: 'What does this exact claim support?', scope: 'A compatibility fixture, not current notebook navigation.',
      bindings: [binding('legacy-edge', 0, { from: 'Premise', to: 'Conditional conclusion', relation: 'supports conditionally' })] },
    { id: 'legacy-tradeoff', title: 'Legacy resource view', kind: 'tradeoff',
      question: 'How do these toy strategies compare?', scope: 'Declared toy online work and allocated-byte model.',
      bindings: [
        binding('legacy-search', 3, { label: 'Exhaustive search', timeLog2: 32, memoryLog2: 5, evidence: 'illustrative' }),
        binding('legacy-table', 4, { label: 'Inverse table', timeLog2: 0, memoryLog2: Math.log2(2 ** 34 + 32), evidence: 'illustrative' })
      ] }
  ];
  return snapshot;
}
function input(overrides = {}) {
  return { title: 'A precise motivated conjecture', claim: 'The stated algorithm satisfies bound B under hypothesis H.',
    conditions: 'Hypothesis H in the declared model.', body: 'Motivation and limitations. This is unproved.',
    tags: ['custom free tag'], kind: 'conjecture', ...overrides };
}

test('notebook seed has six claim-bearing notes and no generic views', () => {
  const valid = Store.validateSnapshot(Seed);
  assert.equal(valid.notes.length, 6);
  assert.deepEqual(valid.views, []);
  for (const note of valid.notes) {
    for (const field of ['title', 'claim', 'conditions', 'body']) assert.ok(note.revisions[0][field].trim());
  }
});

test('legacy view exports still import, retain exact pins, and survive restart without changing the seed', () => {
  const { store, storage } = fixture();
  const legacy = legacySnapshot();
  const imported = store.importJSON(JSON.stringify(legacy));
  assert.deepEqual(imported.views.map(view => view.kind), ['argument', 'tradeoff']);
  assert.equal(imported.views[1].bindings[0].timeLog2, 32);
  assert.equal(imported.views[1].bindings[1].timeLog2, 0);
  assert.ok(imported.views[1].bindings[1].memoryLog2 > 34);
  for (const view of imported.views) {
    for (const binding of view.bindings) {
      const source = imported.notes.find(note => note.id === binding.noteId).revisions[0];
      assert.equal(binding.excerpt, source.claim, 'legacy fixture excerpts are exact source text');
      assert.equal(binding.assessment, 'unreviewed');
      assert.equal(store.bindingState(binding).stale, false);
    }
  }
  assert.deepEqual(Store.create(Seed, { storage }).load().views, imported.views);
  assert.deepEqual(Seed.views, []);
});

test('minimal prose content needs no theorem taxonomy, structured game, or proof artifact', () => {
  const { store } = fixture();
  const record = store.createNote({ title: 'Minimal', claim: 'A claim.', conditions: 'Stated conditions.', body: 'Supporting mathematics.' });
  assert.match(record.id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual(record.revisions[0].tags, []);
  assert.equal(record.revisions[0].kind, undefined);
});

test('simple editor retains authored support metadata without inheriting review or check status', () => {
  const { store } = fixture();
  const original = store.createNote(input({
    references: [{ title: 'Supporting source', url: 'https://example.org/paper' }],
    artifacts: [{ sha256: 'a'.repeat(64), role: 'author-supplied proof draft' }],
    extensions: { domain: ['finite groups'] },
    assessment: 'reviewed', review_status: 'accepted', verification: { checked: true },
    assessmentReason: 'A judgment about the first revision only.'
  }));
  const first = original.revisions[0];
  const retained = Store.authoredMetadata(first);
  assert.deepEqual(Object.keys(retained).sort(), ['artifacts', 'extensions', 'references']);
  const revised = store.reviseNote(original.id, 1, { ...retained, ...input({ title: 'Revised content' }) });
  const second = revised.revisions[1];
  assert.deepEqual(second.references, first.references);
  assert.deepEqual(second.artifacts, first.artifacts);
  assert.deepEqual(second.extensions, first.extensions);
  for (const field of ['assessment', 'review_status', 'verification', 'assessmentReason']) assert.equal(second[field], undefined);
  retained.references[0].title = 'Mutated local copy';
  assert.equal(store.snapshot().notes.at(-1).revisions[1].references[0].title, 'Supporting source');
  assert.deepEqual(Store.authoredMetadata(null), {});
});

test('note creation persists and snapshots cannot mutate stored content', () => {
  const { store, storage } = fixture();
  const note = store.createNote(input());
  note.revisions[0].claim = 'mutated returned value';
  const external = store.snapshot(); external.notes[0].revisions[0].claim = 'mutated snapshot';
  const latest = store.snapshot().notes.at(-1);
  assert.notEqual(latest.revisions[0].claim, note.revisions[0].claim);
  assert.equal(store.snapshot().notes[0].revisions[0].claim, Seed.notes[0].revisions[0].claim);
  const restarted = Store.create(Seed, { storage });
  assert.equal(restarted.load().notes.at(-1).id, latest.id);
});

test('revisions append immutably, detect conflicts, and do not update pinned bindings', () => {
  const { store } = fixture(legacySnapshot());
  const original = store.snapshot().notes[0];
  const binding = store.snapshot().views[0].bindings[0];
  const revised = store.reviseNote(original.id, 1, { ...original.revisions[0], title: 'Revised precise claim' });
  assert.deepEqual(revised.revisions[0], original.revisions[0]);
  assert.equal(revised.revisions[1].revision, 2);
  assert.deepEqual(store.bindingState(binding), { missing: false, stale: true, latestRevision: 2, pinnedRevision: 1 });
  assert.equal(store.snapshot().views[0].bindings[0].noteRevision, 1);
  assert.throws(() => store.reviseNote(original.id, 1, input()), error => error.code === 'revision_conflict');
  assert.equal(store.snapshot().notes[0].revisions.length, 2);
});

test('view edits preserve kind and imported optional metadata', () => {
  const { store } = fixture();
  const view = store.createView({ title: 'Custom view', question: 'What follows?', scope: 'This model only.', kind: 'argument', custom: { colorHint: 'ember' } });
  const changed = store.updateView(view.id, { title: 'Updated title' });
  assert.equal(changed.title, 'Updated title');
  assert.equal(changed.custom.colorHint, 'ember');
  assert.throws(() => store.updateView(view.id, { kind: 'tradeoff' }), error => error.code === 'invalid_update');
  assert.throws(() => store.updateView(view.id, { bindings: [] }), error => error.code === 'invalid_update');
});

test('bindings require exact existing source revisions and can be repinned explicitly', () => {
  const { store } = fixture(legacySnapshot());
  const view = store.snapshot().views[0];
  const binding = store.addBinding(view.id, { ...view.bindings[0], scope: 'A different explicitly declared application.' });
  assert.notEqual(binding.id, view.bindings[0].id);
  assert.throws(() => store.updateBinding(view.id, binding.id, { noteRevision: 99 }), /missing note revision/);
  assert.throws(() => store.updateBinding(view.id, binding.id, { noteId: 'missing' }), /missing note revision/);
  store.reviseNote(binding.noteId, 1, input());
  const repinned = store.updateBinding(view.id, binding.id, { noteRevision: 2 });
  assert.equal(store.bindingState(repinned).stale, false);
  const removed = store.removeBinding(view.id, binding.id);
  assert.equal(removed.id, binding.id);
  assert.ok(!store.snapshot().views[0].bindings.some(item => item.id === binding.id));
});

test('local assessments need reasons and are ordinary editable demo data', () => {
  const { store } = fixture(legacySnapshot());
  const view = store.snapshot().views[0], binding = view.bindings[0];
  assert.throws(() => store.updateBinding(view.id, binding.id, { assessment: 'reviewed', assessmentReason: '' }), /reason/);
  const changed = store.updateBinding(view.id, binding.id, { assessment: 'disputed', assessmentReason: 'Local test: the application omits its correctness hypothesis.' });
  assert.equal(changed.assessment, 'disputed');
  assert.throws(() => store.updateBinding(view.id, binding.id, { assessment: 'proved' }), /assessment/);
});

test('failed import never changes the in-memory or saved snapshot', () => {
  const { store, storage } = fixture(legacySnapshot()); store.save();
  const before = store.exportJSON(), saved = storage.getItem(Store.DEFAULT_NAMESPACE);
  assert.throws(() => store.importJSON('{ broken'), error => error.code === 'invalid_json');
  const duplicate = JSON.parse(before); duplicate.notes.push(duplicate.notes[0]);
  assert.throws(() => store.importJSON(JSON.stringify(duplicate)), /Duplicate note/);
  const invalidPin = JSON.parse(before); invalidPin.views[0].bindings[0].noteRevision = 0;
  assert.throws(() => store.importJSON(JSON.stringify(invalidPin)), /positive note revision/);
  assert.equal(store.exportJSON(), before);
  assert.equal(storage.getItem(Store.DEFAULT_NAMESPACE), saved);
});

test('prototype mutation keys, nonfinite numbers, accessors, cycles and oversized inputs are rejected', () => {
  const { store } = fixture();
  const poisoned = JSON.parse(store.exportJSON());
  Object.defineProperty(poisoned, '__proto__', { value: { polluted: true }, enumerable: true });
  assert.throws(() => store.importJSON(JSON.stringify(poisoned)), /Unsupported object field/);
  assert.equal({}.polluted, undefined);
  const badNumber = legacySnapshot(); badNumber.views[1].bindings[0].timeLog2 = Infinity;
  assert.throws(() => Store.validateSnapshot(badNumber), /finite/);
  const accessor = { ...Seed }; Object.defineProperty(accessor, 'surprise', { get() { throw new Error('getter executed'); }, enumerable: true });
  assert.throws(() => Store.validateSnapshot(accessor), /Unsupported object field/);
  const cycle = { ...Seed }; cycle.cycle = cycle;
  assert.throws(() => Store.validateSnapshot(cycle), /deeply/);
  assert.throws(() => store.importJSON(' '.repeat(Store.MAX_BYTES + 1)), error => error.code === 'too_large');
});

test('extension JSON and harmless mathematical text survive export without being executed', () => {
  const { store } = fixture();
  const note = store.createNote(input({ body: '<script>globalThis.notExecuted = true</script> is source text.',
    extensions: { arbitraryFutureAnnotation: ['claim', { hypothesis: 'H' }] } }));
  const copy = Store.create(Seed, { persist: false }); copy.importJSON(store.exportJSON());
  const imported = copy.snapshot().notes.find(item => item.id === note.id).revisions[0];
  assert.deepEqual(imported.extensions, note.revisions[0].extensions);
  assert.equal(imported.body, note.revisions[0].body);
  assert.equal(globalThis.notExecuted, undefined);
});

test('quota and storage access failures are surfaced and failed writes are atomic', () => {
  const { store, storage } = fixture(); store.save();
  const before = store.exportJSON();
  storage.setItem = () => { throw new Error('quota'); };
  assert.throws(() => store.createNote(input()), error => error.code === 'storage_write_failed');
  assert.equal(store.exportJSON(), before);
  assert.match(store.lastError, /could not save/);
  const inaccessible = Store.create(Seed, { storage: null });
  assert.equal(inaccessible.load().notes.length, 6);
  assert.match(inaccessible.lastError, /unavailable/);
  assert.throws(() => inaccessible.createNote(input()), error => error.code === 'storage_unavailable');
});

test('corrupt saved data is not overwritten by loading and missing-source status is explicit', () => {
  const { store, storage } = fixture();
  storage.setItem(Store.DEFAULT_NAMESPACE, '{bad');
  const loaded = store.load();
  assert.equal(loaded.notes.length, 6);
  assert.equal(storage.getItem(Store.DEFAULT_NAMESPACE), '{bad');
  assert.match(store.lastError, /not been overwritten/);
  assert.throws(() => store.createNote(input()), error => error.code === 'storage_invalid');
  assert.equal(storage.getItem(Store.DEFAULT_NAMESPACE), '{bad');
  store.importJSON(JSON.stringify(Seed));
  assert.equal(store.lastError, null);
  assert.doesNotThrow(() => store.createNote(input()));
  assert.deepEqual(store.bindingState({ noteId: 'missing', noteRevision: 1 }),
    { missing: true, stale: false, latestRevision: null, pinnedRevision: null });
});

test('imports preserve all historical revisions while allowing new notes, appended revisions, and replaced views', () => {
  const { store } = fixture(legacySnapshot());
  store.createNote(input());
  const before = store.exportJSON();
  const removed = JSON.parse(before); removed.notes.pop();
  assert.throws(() => store.importJSON(JSON.stringify(removed)), error => error.code === 'history_conflict');
  const rewritten = JSON.parse(before); rewritten.notes[0].revisions[0].claim = 'Rewritten history';
  assert.throws(() => store.importJSON(JSON.stringify(rewritten)), error => error.code === 'history_conflict');
  assert.equal(store.exportJSON(), before);
  const appended = JSON.parse(before);
  appended.notes[0].revisions.push({ ...appended.notes[0].revisions[0], revision: 2, title: 'New revision' });
  appended.views = [];
  const imported = store.importJSON(JSON.stringify(appended));
  assert.equal(imported.notes[0].revisions.length, 2);
  assert.equal(imported.views.length, 0);
  assert.throws(() => store.importJSON(before), error => error.code === 'history_conflict');
});

test('best-effort cross-tab checks reject stale saves and allow reload then retry', () => {
  const storage = memoryStorage();
  const first = Store.create(Seed, { storage }), second = Store.create(Seed, { storage });
  first.load(); second.load();
  first.createNote(input({ title: 'First tab note' }));
  const before = second.exportJSON();
  assert.throws(() => second.createNote(input({ title: 'Stale second tab note' })), error => error.code === 'storage_conflict');
  assert.equal(second.exportJSON(), before);
  assert.match(second.lastError, /another tab/);
  second.load();
  assert.equal(second.snapshot().notes.at(-1).revisions[0].title, 'First tab note');
  second.createNote(input({ title: 'Second tab after reload' }));
  assert.throws(() => first.save(), error => error.code === 'storage_conflict');
  const third = Store.create(Seed, { storage });
  assert.throws(() => third.save(), error => error.code === 'storage_conflict');
});

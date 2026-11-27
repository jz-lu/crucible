(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TheoryStore = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const FORMAT = 'crucible-theory-local-v1';
  const DEFAULT_NAMESPACE = 'crucible-theory-prototype-v1';
  const MAX_BYTES = 2 * 1024 * 1024;
  const forbidden = new Set(['__proto__', 'prototype', 'constructor']);
  const assessments = new Set(['unreviewed', 'reviewed', 'disputed']);

  class StoreError extends Error {
    constructor(code, message) { super(message); this.name = 'TheoryStoreError'; this.code = code; }
  }
  function fail(code, message) { throw new StoreError(code, message); }
  function bytes(text) {
    return typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(text).length : Buffer.byteLength(text, 'utf8');
  }
  function inspect(value, depth = 0) {
    if (depth > 16) fail('invalid_snapshot', 'The document is nested too deeply.');
    if (value === null || typeof value === 'boolean') return;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) fail('invalid_snapshot', 'Numbers must be finite.');
      return;
    }
    if (typeof value === 'string') {
      if (value.length > 50000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) {
        fail('invalid_snapshot', 'Text is too long or contains unsupported control characters.');
      }
      return;
    }
    if (Array.isArray(value)) {
      if (value.length > 1000) fail('invalid_snapshot', 'An array has too many items.');
      value.forEach(item => inspect(item, depth + 1));
      return;
    }
    if (!value || typeof value !== 'object' ||
        ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      fail('invalid_snapshot', 'Only plain JSON data is accepted.');
    }
    if (Object.keys(value).length > 100) fail('invalid_snapshot', 'An object has too many fields.');
    for (const [key, descriptor] of Object.entries(Object.getOwnPropertyDescriptors(value))) {
      if (forbidden.has(key) || key.length > 100 || !Object.hasOwn(descriptor, 'value')) {
        fail('invalid_snapshot', 'Unsupported object field.');
      }
      inspect(descriptor.value, depth + 1);
    }
  }
  function clone(value) {
    inspect(value);
    return JSON.parse(JSON.stringify(value));
  }
  function canonical(value) {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
    }
    return JSON.stringify(value);
  }
  function authoredMetadata(revision) {
    // Only authored supporting material transfers through the simple editor.
    // Assessment/check status belongs to an exact revision, never its successor.
    const retained = {};
    if (revision) for (const key of ['references', 'artifacts', 'extensions', 'sections', 'statements', 'arguments']) {
      if (Object.hasOwn(revision, key)) retained[key] = clone(revision[key]);
    }
    return retained;
  }
  function object(value, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail('invalid_snapshot', label + ' must be an object.');
  }
  function text(value, label, max = 20000, allowEmpty = false) {
    if (typeof value !== 'string' || value.length > max || (!allowEmpty && !value.trim())) {
      fail('invalid_snapshot', label + ' needs ' + (allowEmpty ? 'bounded text.' : 'nonempty bounded text.'));
    }
  }
  function id(value, label = 'ID') { text(value, label, 100); }
  function time(value) {
    if (!Number.isSafeInteger(value) || value < 0) fail('invalid_snapshot', 'Dates must be nonnegative integer milliseconds.');
  }
  function tags(value) {
    if (!Array.isArray(value) || value.length > 40) fail('invalid_snapshot', 'Use at most 40 text tags.');
    value.forEach(item => text(item, 'Tag', 100));
  }
  function readableStatementBlocks(body, headings = null, structure = null) {
    text(body, 'Section text', 40000);
    const blocks = [], buffer = [];
    let fence = null, ticks = 0, math = null, lineNumber = 0, firstLine = 1;
    const flush = () => { if (buffer.length) { blocks.push({kind:'text', text:buffer.join('\n'), line:firstLine}); buffer.length = 0; } };
    for (const line of body.replace(/\r\n?/g, '\n').split('\n')) {
      lineNumber++; if (!buffer.length) firstLine = lineNumber;
      if (fence) {
        const close = /^ {0,3}(`+|~+)[ \t]*$/.exec(line);
        if (close && close[1][0] === fence[0] && close[1].length >= fence[1]) fence = null;
        buffer.push(line); continue;
      }
      if (!ticks && !math) {
        const opened = /^ {0,3}(`{3,}|~{3,})/.exec(line);
        if (opened) { structure?.fence_lines.push(lineNumber); fence = [opened[1][0],opened[1].length]; buffer.push(line); continue; }
        if (['\\[','$$'].includes(line.trim())) structure?.math_lines.push(lineNumber);
        const heading = /^(#{1,6})[ \t]+(.+)$/.exec(line);
        if (heading && headings) {
          const title = heading[2].replace(/[ \t]+#+[ \t]*$/, '').trim();
          if (title) headings.push({line:lineNumber,level:heading[1].length,title});
        }
        const marker = /^ {0,3}\[\[statement:([a-z][a-z0-9-]{0,79})\]\][ \t]*$/.exec(line);
        if (marker) { flush(); blocks.push({kind:'statement',id:marker[1],line:lineNumber}); continue; }
        if (/^ {0,3}\\\[\[statement:[a-z][a-z0-9-]{0,79}\]\][ \t]*$/.test(line)) { buffer.push(line); continue; }
        if (/^(?: {4}|\t)/.test(line)) { buffer.push(line); continue; }
      }
      for (let i = 0, slashes = 0; i < line.length;) {
        if (ticks) {
          if (line[i] === '`') {
            let end = i + 1; while (line[end] === '`') end++;
            if (end - i === ticks) ticks = 0;
            slashes = 0;
            i = end; continue;
          }
        } else {
          const escaped = slashes % 2 === 1;
          if (math) {
            if (!escaped && line.startsWith(math, i)) { i += math.length; math = null; slashes = 0; continue; }
          } else if (!escaped) {
            if (line[i] === '`') {
              let end = i + 1; while (line[end] === '`') end++;
              ticks = end - i; slashes = 0; i = end; continue;
            }
            const opening = ['\\[','\\(','$$'].find(token => line.startsWith(token, i));
            if (opening) { math = {'\\[':'\\]','\\(':'\\)','$$':'$$'}[opening]; i += opening.length; slashes = 0; continue; }
          }
        }
        slashes = line[i] === '\\' ? slashes + 1 : 0; i++;
      }
      buffer.push(line);
    }
    flush(); return blocks;
  }
  function markdownHeadings(body) {
    const headings = []; readableStatementBlocks(body, headings); return headings;
  }
  function markdownStructure(body) {
    const structure = {headings:[],fence_lines:[],math_lines:[]};
    readableStatementBlocks(body, structure.headings, structure); return structure;
  }
  function headingPassage(content, location) {
    object(location, 'Heading location'); text(location.heading, 'Referenced heading', 240);
    if (Object.keys(location).some(key => !['heading','section_id'].includes(key))) fail('invalid_snapshot', 'Unsupported heading location field.');
    let source = content.body;
    if (location.section_id != null) {
      if (!/^[a-z][a-z0-9-]{0,79}$/.test(location.section_id) || forbidden.has(location.section_id)) fail('invalid_snapshot', 'Use a stable source section ID.');
      const sections = (content.sections || []).filter(item => item.id === location.section_id);
      if (sections.length !== 1) fail('invalid_snapshot', "The heading's source section is missing or ambiguous.");
      source = sections[0].body;
    }
    const headings = markdownHeadings(source), matches = headings.filter(item => item.title === location.heading);
    if (matches.length !== 1) fail('invalid_snapshot', 'The heading reference “' + location.heading + '” must match one unique heading in its source. Update the reference if you renamed or removed that heading.');
    const selected = matches[0], lines = source.replace(/\r\n?/g, '\n').split('\n');
    const end = (headings.find(item => item.line > selected.line && item.level <= selected.level)?.line || lines.length + 1) - 1;
    const passage = lines.slice(selected.line - 1, end).join('\n');
    if (readableStatementBlocks(passage).some(item => item.kind === 'statement')) fail('invalid_snapshot', 'A referenced heading needs its Markdown text in place, not statement insertion markers.');
    return {heading:selected.title,start_line:selected.line,end_line:end,text:passage,...(location.section_id != null ? {section_id:location.section_id} : {})};
  }
  const validPassage = id => typeof id === 'string' && /^p1-(?:b|s-[a-z][a-z0-9-]{0,63})-(?:0|[1-9][0-9]{0,4})-[1-9][0-9]{0,4}$/.test(id);
  function passages(content) {
    const result = [];
    const containers = [...(content.sections || []).map(part => ({id:part.id,body:part.body,title:part.title})), {body:content.body}];
    for (const part of containers) {
      const lines = part.body.replace(/\r\n?/g, '\n').split('\n');
      const headings = [...(part.id ? [{line:0,level:0,title:part.title}] : []), ...markdownHeadings(part.body)];
      const stack = [], ends = [];
      for (let i = headings.length - 1; i >= 0; i--) {
        while (stack.length && headings[stack.at(-1)].level > headings[i].level) stack.pop();
        ends[i] = stack.length ? headings[stack.at(-1)].line - 1 : lines.length; stack.push(i);
      }
      for (let i = 0; i < headings.length; i++) {
        const heading = headings[i];
        result.push({id:'p1-' + (part.id ? 's-' + part.id : 'b') + '-' + heading.line + '-' + ends[i],
          heading:heading.title,start_line:heading.line,end_line:ends[i],...(part.id ? {section_id:part.id} : {})});
      }
    }
    return result;
  }
  function resolvePassage(content, id) {
    if (!validPassage(id)) fail('invalid_snapshot', 'Use an exact version-one passage selector.');
    const passage = passages(content).find(item => item.id === id);
    if (!passage) fail('invalid_snapshot', 'This passage is absent from the exact saved note; no replacement was selected.');
    const source = passage.section_id ? content.sections.find(item => item.id === passage.section_id).body : content.body;
    let definition = source.replace(/\r\n?/g, '\n').split('\n').slice(Math.max(0, passage.start_line - 1), passage.end_line).join('\n');
    if (!passage.start_line) definition = passage.heading + '\n\n' + definition;
    if (readableStatementBlocks(definition).some(item => item.kind === 'statement')) fail('invalid_snapshot', 'This legacy passage inserts other statement text. Use its existing statement citation instead.');
    return {id, label:'',title:passage.heading,definition,definitions:[],source_passage:passage};
  }
  function materializeStatement(content, item) {
    if (!item.location) return item;
    if (item.definition != null) fail('invalid_snapshot', 'Use either a Markdown heading location or a definition, not both.');
    const {text:definition,...source_passage} = headingPassage(content, item.location);
    const {location,...record} = item;
    return {...record,label:item.label || '',title:item.title || source_passage.heading,definition,source_passage};
  }
  function statementPlacements(content) {
    const ids = new Set((content.statements || []).filter(item => !item.location).map(item => item.id)), placements = new Map();
    const sections = content.sections?.length ? content.sections : ids.size && content.body ? [{id:'$body',body:content.body}] : [];
    for (const section of sections) for (const block of readableStatementBlocks(section.body)) {
      if (block.kind !== 'statement') continue;
      if (!ids.has(block.id)) fail('invalid_snapshot', 'A statement placement must name a canonical statement in this note.');
      if (placements.has(block.id)) fail('invalid_snapshot', 'Place each statement only once in the readable note; use exact links for later references.');
      placements.set(block.id, section.id);
    }
    return placements;
  }
  function validateRevision(revision, expected) {
    object(revision, 'Revision');
    if (revision.revision !== expected) fail('invalid_snapshot', 'Note revisions must be consecutive, starting at one.');
    text(revision.title, 'Note title', 240);
    text(revision.claim, 'Summary', 20000, true);
    text(revision.conditions, 'Conditions', 20000, true);
    text(revision.body, 'Supporting body', 40000);
    if (revision.sections !== undefined) {
      if (!Array.isArray(revision.sections) || revision.sections.length > 32) fail('invalid_snapshot', 'Use at most 32 readable sections.');
      const ids = new Set();
      for (const section of revision.sections) {
        object(section, 'Readable section');
        if (typeof section.id !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(section.id) || forbidden.has(section.id) || ids.has(section.id)) fail('invalid_snapshot', 'Readable sections need unique stable IDs.');
        if (Object.keys(section).some(key => !['id', 'title', 'body'].includes(key))) fail('invalid_snapshot', 'Unsupported readable section field.');
        ids.add(section.id); text(section.title, 'Section heading', 240); text(section.body, 'Section text', 40000);
      }
    }
    for (const key of ['statements', 'arguments']) {
      if (revision[key] === undefined) continue;
      const values = revision[key], ids = new Set();
      if (!Array.isArray(values) || values.length > 32) fail('invalid_snapshot', 'Use at most 32 ' + key + '.');
      for (const item of values) {
        object(item, key); text(item.id, 'Stable identifier', 80);
        if (!/^[a-z][a-z0-9-]{0,79}$/.test(item.id) || forbidden.has(item.id) || ids.has(item.id)) fail('invalid_snapshot', 'Use unique stable identifiers.');
        ids.add(item.id); if (key !== 'statements' || !item.location || item.title != null) text(item.title, 'Title', 240);
        if (key === 'statements') {
          if (item.location) materializeStatement(revision, item);
          else text(item.definition, 'Precise statement', 12000);
        }
        else {
          if (item.conclusion_statement != null) {
            if (item.conclusion != null) fail('invalid_snapshot', 'Use one conclusion statement, not a second copy of its text.');
            const ref = item.conclusion_statement; object(ref, 'Conclusion statement');
            if ((ref.statement_id != null) === (ref.passage != null) || (ref.passage != null ? !validPassage(ref.passage) : typeof ref.statement_id !== 'string' || !/^[a-z][a-z0-9-]{0,79}$/.test(ref.statement_id) || forbidden.has(ref.statement_id))) fail('invalid_snapshot', 'Identify the exact conclusion statement.');
            const external = ['note_id','revision','content_sha256'].filter(key => ref[key] != null);
            if (external.length && (external.length !== 3 || typeof ref.note_id !== 'string' || !ref.note_id.trim() || !Number.isSafeInteger(ref.revision) || ref.revision < 1 || !/^[a-f0-9]{64}$/.test(ref.content_sha256))) fail('invalid_snapshot', 'An external conclusion needs its exact note revision and hash.');
            if (!external.length && ref.passage) resolvePassage(revision, ref.passage);
            if (!external.length && !ref.passage && !revision.statements?.some(statement => statement.id === ref.statement_id)) fail('invalid_snapshot', 'The local conclusion statement is missing.');
          } else text(item.conclusion, 'Conclusion', 6000);
          text(item.scope, 'Scope', 6000); if (!Array.isArray(item.dependencies) || item.dependencies.length > 32) fail('invalid_snapshot', 'Bound the exact dependency list.');
        }
      }
    }
    statementPlacements(revision);
    tags(revision.tags);
    if (revision.kind !== undefined) text(revision.kind, 'Author label', 100, true);
    time(revision.createdAt);
  }
  function validateBinding(binding, view, notes) {
    object(binding, 'Binding');
    id(binding.id, 'Binding ID');
    id(binding.noteId, 'Referenced note ID');
    if (!Number.isInteger(binding.noteRevision) || binding.noteRevision < 1) fail('invalid_snapshot', 'Pin a positive note revision.');
    const note = notes.get(binding.noteId);
    if (!note || !note.revisions.some(item => item.revision === binding.noteRevision)) {
      fail('invalid_snapshot', 'A binding references a missing note revision.');
    }
    text(binding.excerpt, 'Excerpt', 10000);
    text(binding.scope, 'Application scope', 10000);
    if (!assessments.has(binding.assessment)) fail('invalid_snapshot', 'Unknown local assessment state.');
    text(binding.assessmentReason, 'Assessment reason', 10000, binding.assessment === 'unreviewed');
    if (view.kind === 'argument') {
      text(binding.from, 'From label', 240);
      text(binding.to, 'To label', 240);
      text(binding.relation, 'Relation', 100);
    } else {
      text(binding.label, 'Point label', 240);
      for (const field of ['timeLog2', 'memoryLog2']) {
        if (typeof binding[field] !== 'number' || !Number.isFinite(binding[field]) || binding[field] < -1024 || binding[field] > 1024) {
          fail('invalid_snapshot', 'Plot coordinates need finite log2 values between -1024 and 1024.');
        }
      }
      if (binding.evidence !== 'illustrative') fail('invalid_snapshot', 'This local prototype only labels plot points as illustrative.');
    }
  }
  function validateSnapshot(value) {
    inspect(value);
    object(value, 'Snapshot');
    if (bytes(JSON.stringify(value)) > MAX_BYTES) fail('too_large', 'Snapshots must fit within 2 MiB.');
    if (value.format !== FORMAT) fail('invalid_snapshot', 'Unknown snapshot format.');
    if (!Array.isArray(value.notes) || value.notes.length > 200) fail('invalid_snapshot', 'Use at most 200 notes.');
    if (!Array.isArray(value.views) || value.views.length > 50) fail('invalid_snapshot', 'Use at most 50 views.');
    const notes = new Map();
    for (const note of value.notes) {
      object(note, 'Note'); id(note.id, 'Note ID');
      if (notes.has(note.id)) fail('invalid_snapshot', 'Duplicate note ID.');
      if (!Array.isArray(note.revisions) || note.revisions.length < 1 || note.revisions.length > 100) {
        fail('invalid_snapshot', 'Each note needs 1–100 immutable revisions.');
      }
      note.revisions.forEach((revision, index) => validateRevision(revision, index + 1));
      notes.set(note.id, note);
    }
    const views = new Set();
    for (const view of value.views) {
      object(view, 'View'); id(view.id, 'View ID');
      if (views.has(view.id)) fail('invalid_snapshot', 'Duplicate view ID.');
      views.add(view.id);
      text(view.title, 'View title', 240);
      text(view.question, 'View question', 4000);
      text(view.scope, 'View scope', 10000);
      if (!['argument', 'tradeoff'].includes(view.kind)) fail('invalid_snapshot', 'Choose an argument or tradeoff view.');
      if (!Array.isArray(view.bindings) || view.bindings.length > 200) fail('invalid_snapshot', 'Use at most 200 bindings per view.');
      const bindingIds = new Set();
      view.bindings.forEach(binding => {
        validateBinding(binding, view, notes);
        if (bindingIds.has(binding.id)) fail('invalid_snapshot', 'Duplicate binding ID within a view.');
        bindingIds.add(binding.id);
      });
    }
    return clone(value);
  }
  function uuid() {
    let cryptoAPI = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
    if (!cryptoAPI && typeof require === 'function') cryptoAPI = require('node:crypto').webcrypto;
    if (!cryptoAPI || typeof cryptoAPI.getRandomValues !== 'function') fail('random_unavailable', 'Secure random IDs are unavailable in this browser.');
    if (typeof cryptoAPI.randomUUID === 'function') return cryptoAPI.randomUUID();
    const parts = cryptoAPI.getRandomValues(new Uint8Array(16));
    parts[6] = (parts[6] & 15) | 64; parts[8] = (parts[8] & 63) | 128;
    const value = Array.from(parts, part => part.toString(16).padStart(2, '0')).join('');
    return value.slice(0, 8) + '-' + value.slice(8, 12) + '-' + value.slice(12, 16) + '-' + value.slice(16, 20) + '-' + value.slice(20);
  }
  function create(seed, options = {}) {
    const initial = validateSnapshot(seed);
    let state = clone(initial);
    let storage = null;
    let storageAccessError = null;
    try {
      storage = Object.hasOwn(options, 'storage') ? options.storage :
        (typeof window !== 'undefined' ? window.localStorage : null);
    } catch (error) { storageAccessError = error; }
    const namespace = options.namespace || DEFAULT_NAMESPACE;
    let lastError = null;
    let baseline;
    let invalidStoredData = false;
    const persist = options.persist !== false;
    function persistState(next, explicitImport = false) {
      if (!persist) { lastError = null; return; }
      if (!storage || storageAccessError) {
        lastError = 'Browser storage is unavailable. Changes were not saved; enable storage or use explicit memory-only mode.';
        throw new StoreError('storage_unavailable', lastError);
      }
      if (invalidStoredData && !explicitImport) {
        lastError = 'The saved snapshot is unreadable. It was preserved. Import an explicitly chosen valid snapshot before saving changes.';
        throw new StoreError('storage_invalid', lastError);
      }
      let current;
      try { current = storage.getItem(namespace); }
      catch (error) {
        lastError = 'Browser storage could not be read. No changes were saved.';
        throw new StoreError('storage_read_failed', lastError);
      }
      if (baseline === undefined && current === null) baseline = null;
      if (current !== baseline) {
        lastError = 'Saved data changed in another tab or has not been loaded. Reload the latest snapshot before saving; your current changes were not written.';
        throw new StoreError('storage_conflict', lastError);
      }
      try {
        const encoded = JSON.stringify(next);
        storage.setItem(namespace, encoded);
        baseline = encoded;
        invalidStoredData = false;
        lastError = null;
      }
      catch (error) {
        lastError = 'Browser storage could not save this change. Your previous saved state is unchanged.';
        throw new StoreError('storage_write_failed', lastError);
      }
    }
    function commit(next, explicitImport = false) {
      const validated = validateSnapshot(next);
      persistState(validated, explicitImport);
      state = validated;
    }
    function noteIn(next, identifier) {
      const note = next.notes.find(item => item.id === identifier);
      if (!note) fail('not_found', 'Note not found.');
      return note;
    }
    function viewIn(next, identifier) {
      const view = next.views.find(item => item.id === identifier);
      if (!view) fail('not_found', 'View not found.');
      return view;
    }
    function revision(input, number) {
      object(input, 'Note input');
      return { ...clone(input), tags: input.tags === undefined ? [] : clone(input.tags), revision: number, createdAt: Date.now() };
    }
    const api = {
      get lastError() { return lastError; },
      load() {
        if (!persist) return clone(state);
        if (!storage || storageAccessError) {
          lastError = 'Browser storage is unavailable. Showing the example without saving changes.';
          return clone(state);
        }
        try {
          const raw = storage.getItem(namespace);
          baseline = raw;
          if (raw !== null) {
            if (typeof raw !== 'string' || bytes(raw) > MAX_BYTES) fail('too_large', 'The saved snapshot is oversized.');
            state = validateSnapshot(JSON.parse(raw));
          }
          invalidStoredData = false;
          lastError = null;
        } catch (error) {
          invalidStoredData = true;
          lastError = 'The saved snapshot could not be loaded. It has not been overwritten; showing the current example. Export or inspect your saved data before replacing it.';
        }
        return clone(state);
      },
      snapshot() { return clone(state); },
      save() { persistState(state); return clone(state); },
      createNote(input) {
        const next = clone(state);
        const note = { id: uuid(), revisions: [revision(input, 1)] };
        next.notes.push(note); commit(next); return clone(note);
      },
      reviseNote(identifier, expectedRevision, input) {
        const next = clone(state), note = noteIn(next, identifier);
        const latest = note.revisions[note.revisions.length - 1].revision;
        if (expectedRevision !== latest) fail('revision_conflict', 'A newer note revision exists. Reload it before saving.');
        note.revisions.push(revision(input, latest + 1)); commit(next); return clone(note);
      },
      createView(input) {
        object(input, 'View input');
        const next = clone(state);
        const view = { ...clone(input), id: uuid(), bindings: [] };
        next.views.push(view); commit(next); return clone(view);
      },
      updateView(identifier, patch) {
        object(patch, 'View changes'); inspect(patch);
        if (Object.keys(patch).some(key => !['title', 'question', 'scope'].includes(key))) {
          fail('invalid_update', 'Only the view title, question, and scope can be edited.');
        }
        const next = clone(state), view = viewIn(next, identifier);
        Object.assign(view, clone(patch)); commit(next); return clone(view);
      },
      addBinding(viewId, input) {
        object(input, 'Binding input');
        const next = clone(state), view = viewIn(next, viewId);
        const binding = { assessment: 'unreviewed', assessmentReason: '', ...clone(input), id: uuid() };
        if (view.kind === 'tradeoff' && binding.evidence === undefined) binding.evidence = 'illustrative';
        view.bindings.push(binding); commit(next); return clone(binding);
      },
      updateBinding(viewId, bindingId, patch) {
        object(patch, 'Binding changes'); inspect(patch);
        if (Object.hasOwn(patch, 'id')) fail('invalid_update', 'A binding ID cannot change.');
        const next = clone(state), view = viewIn(next, viewId);
        const binding = view.bindings.find(item => item.id === bindingId);
        if (!binding) fail('not_found', 'Binding not found.');
        Object.assign(binding, clone(patch)); commit(next); return clone(binding);
      },
      removeBinding(viewId, bindingId) {
        const next = clone(state), view = viewIn(next, viewId);
        const index = view.bindings.findIndex(item => item.id === bindingId);
        if (index < 0) fail('not_found', 'Binding not found.');
        const removed = view.bindings.splice(index, 1)[0]; commit(next); return clone(removed);
      },
      bindingState(binding) {
        const note = state.notes.find(item => item.id === binding.noteId);
        const pinned = note && note.revisions.find(item => item.revision === binding.noteRevision);
        const latest = note && note.revisions[note.revisions.length - 1];
        return { missing: !pinned, stale: Boolean(pinned && latest.revision !== binding.noteRevision),
          latestRevision: latest ? latest.revision : null, pinnedRevision: pinned ? pinned.revision : null };
      },
      exportJSON() { return JSON.stringify(state, null, 2) + '\n'; },
      importJSON(raw) {
        if (typeof raw !== 'string' || bytes(raw) > MAX_BYTES) fail('too_large', 'Import a JSON snapshot no larger than 2 MiB.');
        let value;
        try { value = JSON.parse(raw); } catch (error) { fail('invalid_json', 'This file is not valid JSON.'); }
        const incoming = validateSnapshot(value);
        const incomingNotes = new Map(incoming.notes.map(note => [note.id, note]));
        for (const note of state.notes) {
          const replacement = incomingNotes.get(note.id);
          if (!replacement || replacement.revisions.length < note.revisions.length ||
              note.revisions.some((item, index) => canonical(item) !== canonical(replacement.revisions[index]))) {
            fail('history_conflict', 'An import must retain every existing note and its exact revision history. Append a new revision or create a new note instead of rewriting or deleting history.');
          }
        }
        commit(incoming, true); return clone(state);
      }
    };
    return api;
  }
  return { FORMAT, DEFAULT_NAMESPACE, MAX_BYTES, StoreError, validateSnapshot, clone, authoredMetadata, readableStatementBlocks, markdownHeadings, markdownStructure, headingPassage, passages, resolvePassage, validPassage, materializeStatement, statementPlacements, create };
});

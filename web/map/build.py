"""Validate the curated catalogue and emit its offline browser payload. No dependencies."""
import json
import hashlib
import copy
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
CONTENT_FIELDS = {'title', 'claim', 'conditions', 'body', 'tags', 'kind', 'references', 'artifacts', 'extensions'}


def content_hash(revision):
    content = {key: value for key, value in revision.items() if key in CONTENT_FIELDS}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_notes(workspace, data):
    if workspace.get('format') != 'crucible-theory-local-v1' or workspace.get('views') != []:
        raise ValueError('Use the global notebook format; this map does not fabricate legacy views.')
    notes = workspace['notes']
    if not 1 <= len(notes) <= 200 or len({n['id'] for n in notes}) != len(notes):
        raise ValueError('Each note needs a unique bounded identity.')
    ids = {s['id'] for s in data['schemes']}
    sources = {s['id']: s for s in data['sources']}
    revisions = {}
    for note in notes:
        if not 1 <= len(note['id']) <= 100 or not 1 <= len(note['revisions']) <= 100:
            raise ValueError('Invalid note identity or revision count.')
        for number, revision in enumerate(note['revisions'], 1):
            if revision['revision'] != number or type(revision['createdAt']) is not int or revision['createdAt'] < 0:
                raise ValueError('Notes need consecutive revisions with integer millisecond dates.')
            for field, limit in [('title', 240), ('claim', 20000), ('conditions', 20000), ('body', 40000)]:
                if not isinstance(revision[field], str) or not revision[field].strip() or len(revision[field]) > limit:
                    raise ValueError('Invalid global note content.')
            if not isinstance(revision['tags'], list) or len(revision['tags']) > 40 or len(set(revision['tags'])) != len(revision['tags']):
                raise ValueError('Use a small, deduplicated tag set.')
            if any(not isinstance(tag, str) or not tag.strip() or len(tag) > 100 for tag in revision['tags']):
                raise ValueError('Invalid tag.')
            if not set(revision) <= CONTENT_FIELDS | {'revision', 'createdAt'}:
                raise ValueError('Do not invent review or publication fields in note revisions.')
            metadata = revision['extensions']['research_note']
            if not set(metadata['scheme_ids']) <= ids:
                raise ValueError('A note links to an unknown scheme.')
            for reference in revision['references']:
                if sources.get(reference['id']) != reference:
                    raise ValueError('Note source metadata must match the source registry.')
            revisions[note['id'], number] = revision
    if len((json.dumps(workspace, ensure_ascii=False, indent=2) + '\n').encode()) > 2 * 1024 * 1024:
        raise ValueError('Notes must remain portable within the global workspace bound.')
    for scheme in data['schemes']:
        binding = scheme['note_ref']
        revision = revisions.get((binding['note_id'], binding['revision']))
        if not revision or content_hash(revision) != binding['content_sha256']:
            raise ValueError('Map binding references a missing or changed note revision.')
        metadata = revision['extensions']['research_note']
        if metadata['role'] != 'scheme-description' or scheme['id'] not in metadata['scheme_ids']:
            raise ValueError('A map entry needs a descriptive note about that scheme.')
    return revisions


def validate(data, workspace=None):
    if data.get('schema_version') != 2 or data.get('mode') != 'design_draft':
        raise ValueError('This builder accepts only the version-2 notes-backed draft.')
    if data.get('entry_scope') != 'kem':
        raise ValueError('The active research map is KEM-only.')
    if workspace is None:
        workspace = json.loads((ROOT / 'notes.json').read_text())
    validate_notes(workspace, data)
    indexes = {}
    for kind in ('domains', 'families', 'assumptions', 'schemes', 'sources'):
        rows = data[kind]
        ids = [row['id'] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate {kind} identifier')
        if not all(isinstance(id, str) and id and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in id) for id in ids):
            raise ValueError(f'Invalid {kind} identifier')
        indexes[kind] = set(ids)

    def references(values, kind):
        if not values or not set(values) <= indexes[kind]:
            raise ValueError(f'Missing or unknown {kind} reference: {values}')

    for family in data['families']:
        references([family['domain']], 'domains')
        if not family['boundary']:
            raise ValueError('A family needs a stated boundary.')
    for source in data['sources']:
        url = urlparse(source['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError(f'Unsafe source URL: {source["id"]}')
    for assumption in data['assumptions']:
        references(assumption['sources'], 'sources')
        if assumption['scope'] != 'overview':
            raise ValueError('Do not turn overview labels into unreviewed formal assumptions.')
    for scheme in data['schemes']:
        references([scheme['family']], 'families')
        references(scheme['assumptions'], 'assumptions')
        references(scheme['sources'], 'sources')
        if scheme['status'] not in {'reference', 'research'}:
            raise ValueError('Unknown map status')
        if scheme['kind'] != 'kem' or scheme['form'] != 'specified-kem':
            raise ValueError('Only specified KEM entries belong in the active map.')
        if scheme['scoring_admission'] is not None:
            raise ValueError('This standalone seed cannot assert live scoring authority.')
        if any(field in scheme for field in ('name', 'summary', 'rationale', 'review', 'body', 'tags')):
            raise ValueError('Descriptive content belongs in notes, not duplicated map records.')
        if scheme['origin'] not in {'literature', 'local_baseline'}:
            raise ValueError('Do not invent Crucible discoveries in the seed catalogue.')
    highlighted = [row['scheme'] for row in data['highlights']]
    references(highlighted, 'schemes')
    if len(highlighted) != len(set(highlighted)):
        raise ValueError('Duplicate highlight')
    for scheme in data['schemes']:
        if scheme['id'] in highlighted and (scheme['kind'] != 'kem' or scheme['status'] != 'reference'):
            raise ValueError('Literature entries cannot silently become highlighted.')
    snapshot = data['scoring_snapshot']
    if not snapshot['policy_id'] or not snapshot['captured_at'] or len(snapshot['source_sha256']) != 64:
        raise ValueError('Scoring membership needs dated, traceable policy evidence.')
    scored = [row['scheme'] for row in snapshot['releases']]
    references(scored, 'schemes')
    if len(scored) != len(set(scored)) or not set(highlighted) <= set(scored):
        raise ValueError('Highlights must be included in scored membership without duplicates.')
    kem_ids = {row['id'] for row in data['schemes'] if row['kind'] == 'kem'}
    if not set(scored) <= kem_ids:
        raise ValueError('A research direction cannot have a scored KEM release.')
    for release in snapshot['releases']:
        if not release['version'] or not release['profile_prefix'] or type(release['max_stage']) is not int or release['max_stage'] < 1:
            raise ValueError('A scored entry must identify its exact enrolled release.')
    seen = set()
    for relation in data['relationships']:
        references([relation['from'], relation['to']], 'schemes')
        references(relation['sources'], 'sources')
        key = tuple(sorted((relation['from'], relation['to']))) + (relation['type'],)
        if relation['from'] == relation['to'] or key in seen:
            raise ValueError('Self-link or duplicate relationship')
        seen.add(key)
        if relation['type'] not in {'comparison', 'shared_project', 'contrast'}:
            raise ValueError('This draft makes no formal reduction or independence claims.')
        if not relation['explanation']:
            raise ValueError('A relationship needs its actual meaning.')


def project(data, workspace):
    validate(data, workspace)
    revisions = validate_notes(workspace, data)
    result = copy.deepcopy(data)
    for scheme in result['schemes']:
        binding = scheme['note_ref']
        revision = revisions[binding['note_id'], binding['revision']]
        scheme.update(name=revision['title'], summary=revision['claim'], tags=revision['tags'], conditions=revision['conditions'])
    result['note_index'] = []
    for note in workspace['notes']:
        revision = note['revisions'][-1]
        result['note_index'].append({'id': note['id'], 'revision': revision['revision'], 'title': revision['title'], 'tags': revision['tags'], **revision['extensions']['research_note']})
    return result


if __name__ == '__main__':
    data = json.loads((ROOT / 'catalogue.json').read_text())
    workspace = json.loads((ROOT / 'notes.json').read_text())
    payload = json.dumps(project(data, workspace), ensure_ascii=True, separators=(',', ':'))
    (ROOT / 'catalogue.js').write_text('// Generated projection; edit notes.json and catalogue.json.\nwindow.CRUCIBLE_LANDSCAPE = ' + payload + ';\n')
    note_payload = {**workspace, 'revision_hashes': {n['id']: {str(r['revision']): content_hash(r) for r in n['revisions']} for n in workspace['notes']}}
    (ROOT / 'notes-data.js').write_text('// Generated by build.py; edit notes.json.\nwindow.CRUCIBLE_NOTES = ' + json.dumps(note_payload, ensure_ascii=True, separators=(',', ':')) + ';\n')
    print(f'Validated {len(workspace["notes"])} notes, {len(data["schemes"])} map bindings and {len(data["highlights"])} draft highlights.')

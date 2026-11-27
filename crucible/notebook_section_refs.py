"""Derived section labels over immutable note revisions; reading never allocates.

Human label: 56.2.1. Resolver identifier: 56.2.1@3 (revision 3).
Historical P.N aliases continue to resolve through notebook_locs.
"""
import re
from urllib.parse import urlencode

from sqlalchemy import select, tuple_

from . import note_sections

PATTERN = r"^[1-9][0-9]{0,9}(?:\.[1-9][0-9]{0,4}){1,8}@(?:[1-9][0-9]?|100)$"
KINDS = r"Assumption|Lemma|Theorem|Definition|Proposition|Corollary|Conjecture|Claim"


def parse(value):
    if not isinstance(value, str) or not re.fullmatch(PATTERN, value):
        return None
    label, revision = value.split('@')
    number, *parts = label.split('.')
    if int(number) > 2147483647:
        return None
    return int(number), '.'.join(parts), int(revision)


def outline(content):
    """Number actual headings, including explicitly placed historical blocks.

    Heading depth determines nesting, not a required scientific outline. Skipped
    Markdown heading levels do not introduce phantom sections or zero ordinals.
    Aliases keep old statement/section links at their exact original source.
    """
    records, root_count, placed = [], 0, set()
    statements = {row['id']: row for row in content.get('statements', [])}
    passages = note_sections.passages(content)
    containers = [(part['id'], part['body'], part['title']) for part in content.get('sections', [])]
    containers.append((None, content.get('body', ''), None))

    def item(kind, identifier, title, path, aliases=()):
        match = re.match(r'^(?:' + KINDS + r')\b', title, re.IGNORECASE)
        row = {'key': kind + ':' + identifier, 'anchor': {kind: identifier}, 'title': title,
               'number': '.'.join(map(str, path)), 'kind': match[0].capitalize() if match else 'Section',
               'aliases': list(aliases)}
        records.append(row)
        return row

    for section_id, body, title in containers:
        stack = []
        if section_id:
            root_count += 1
            root = next(p for p in passages if p.get('section_id') == section_id and p['start_line'] == 0)
            item('note_section', section_id, title, [root_count], ['passage:' + root['id']])
            stack.append({'level': 0, 'path': [root_count], 'children': 0})
        headings, placements = [], []
        list(note_sections.statement_markers(body, headings, placements))
        tokens = [(h['line'], h['level'], 'heading', h) for h in headings]
        tokens += [(p['line'], 7, 'statement', p) for p in placements]
        for line, level, kind, token in sorted(tokens, key=lambda value: value[0]):
            while stack and stack[-1]['level'] >= level:
                stack.pop()
            if stack:
                stack[-1]['children'] += 1
                path = [*stack[-1]['path'], stack[-1]['children']]
            else:
                root_count += 1
                path = [root_count]
            if kind == 'heading':
                passage = next(p for p in passages if p.get('section_id') == section_id and p['start_line'] == line)
                item('passage', passage['id'], token['title'], path)
                stack.append({'level': level, 'path': path, 'children': 0})
            elif token['id'] in statements:
                value = statements[token['id']]
                item('statement', value['id'], value.get('title', value['id']), path)
                placed.add(value['id'])
    by_key = {row['key']: row for row in records}
    for value in statements.values():
        if value.get('location'):
            try:
                target = note_sections.heading_passage(content, value['location'])
                passage = next(p for p in passages if p.get('section_id') == target.get('section_id') and p['start_line'] == target['start_line'])
                by_key['passage:' + passage['id']]['aliases'].append('statement:' + value['id'])
            except (ValueError, KeyError, StopIteration):
                continue  # Invalid historical source is not given a substitute.
        elif value['id'] not in placed:
            root_count += 1
            item('statement', value['id'], value.get('title', value['id']), [root_count])
    # Honor explicit historical designations without inferring a mathematical
    # type from the statement's substance. Headings remain authoritative.
    for row in records:
        ids = [key[10:] for key in [row['key'], *row['aliases']] if key.startswith('statement:')]
        labels = [statements[identifier].get('label', '') for identifier in ids]
        if row['kind'] == 'Section':
            if any(re.match(r'^(?:Assumption\b|A[-.]\d)', label, re.I) for label in labels):
                row['kind'] = 'Assumption'
            elif any(re.search(r'\bclaim\b', label, re.I) for label in labels):
                row['kind'] = 'Claim'
    return records


def prose_links(content):
    """The editor's conservative inline-link subset; literal examples aren't uses."""
    for source in [content.get('body', ''), *(part['body'] for part in content.get('sections', []))]:
        fence, comment = None, False
        for line in source.split('\n'):
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
            if fence:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                    fence = None
                continue
            if marker:
                fence = marker[1]
                continue
            if comment or '<!--' in line:
                comment = '-->' not in line
                continue
            if line.startswith(('    ', '\t')) or '`' in line:
                continue
            yield from re.findall(r'\]\(<?([^\s()<>]+)>?(?=\s*(?:["\'][^\n]*["\']\s*)?\))', line)


def source_references(content, base=None):
    """Uniform inputs for the existing exact-revision reference index."""
    explicit = content.get('references', [])
    if not isinstance(explicit, list):
        explicit = [explicit]
    yield from explicit
    yield from ({'url': url} for url in prose_links(content))
    for argument in content.get('arguments', []):
        refs = [argument.get('conclusion_statement'), *(use.get('statement') for use in argument.get('dependencies', []))]
        for ref in refs:
            if not ref:
                continue
            source = ref if ref.get('note_id') else base
            if not source:
                continue
            anchor = {'passage': ref['passage']} if ref.get('passage') else {'statement': ref.get('statement_id')}
            if not all(anchor.values()):
                continue
            yield {'url': '/#notebook?' + urlencode({'note': source['note_id'], 'revision': source['revision'],
                'sha256': source['content_sha256'], **anchor})}


def describe(number, revision, row, used=False):
    label = str(number) + '.' + row['number']
    return {'number': row['number'], 'label': label, 'kind': row['kind'], 'title': row['title'],
            'loc': label + '@' + str(revision), 'anchor': row['anchor'], 'used': used}


def display_label(session, source, content, anchor):
    """Presentation only; caller has authorized this exact source."""
    from .notebook_numbers import NotebookNumber
    identifier = source.get('note_id', source.get('id'))
    numbers = session.info.setdefault('section_note_numbers', {})
    if identifier not in numbers:
        numbers[identifier] = session.scalar(select(NotebookNumber.number).where(NotebookNumber.note_id == identifier))
    row = next((row for row in outline(content) if anchor in [row['key'], *row['aliases']]), None)
    if not numbers[identifier] or row is None:
        return {}
    value = describe(numbers[identifier], source['revision'], row)
    return {'label': value['kind'] + ' ' + value['label'], 'section_label': value['label'], 'loc': value['loc']}


def metadata(session, records, visible_ids=None):
    """Read-only, viewer-scoped citation markers; no private incoming-use leak."""
    from . import notebook_graph, notebook_numbers, notebook
    pins = {(note['id'], rev['revision'], rev['content_sha256']) for note in records for rev in note['revisions']}
    if not pins:
        return {}
    numbers = dict(session.execute(select(notebook_numbers.NotebookNumber.note_id, notebook_numbers.NotebookNumber.number)
        .where(notebook_numbers.NotebookNumber.note_id.in_({p[0] for p in pins}))).all())
    uses = set()
    if visible_ids is not None:
        edge = notebook_graph.NotebookReference
        uses.update(session.execute(select(edge.target_note_id, edge.target_revision, edge.target_sha256, edge.target_anchor)
            .where(tuple_(edge.target_note_id, edge.target_revision, edge.target_sha256).in_(pins),
                   edge.target_anchor.is_not(None), edge.source_revision_id.in_(visible_ids))).all())
    # Bundled research is readable to every eligible catalogue reader. It has
    # no ORM rows, so retain its finite, explicit citations as derived metadata.
    if 'curated_section_uses' not in session.info:
        bundled_uses = set()
        for source in notebook.curated_collection(session):
            for rev in source['revisions']:
                base = {'note_id': source['id'], 'revision': rev['revision'], 'content_sha256': rev['content_sha256']}
                for ref in notebook_graph.references(rev['content'], session, base=base):
                    if ref.get('target_anchor'):
                        bundled_uses.add((ref['target_note_id'], ref['target_revision'], ref['target_sha256'], ref['target_anchor']))
        session.info['curated_section_uses'] = bundled_uses
    uses.update(session.info['curated_section_uses'])
    result = {}
    for note in records:
        if note['id'] not in numbers:
            continue
        for rev in note['revisions']:
            pin = (note['id'], rev['revision'], rev['content_sha256'])
            mapping = {}
            for row in outline(rev['content']):
                keys = [row['key'], *row['aliases']]
                value = describe(numbers[note['id']], rev['revision'], row, any((*pin, key) in uses for key in keys))
                mapping.update((key, value) for key in keys)
            result.setdefault(note['id'], {})[str(rev['revision'])] = mapping
    return result


def lookup(connection, identifier):
    """Internal resolution only; the caller must authorize the resulting source."""
    from . import notebook, notebook_numbers
    parsed = parse(identifier)
    if parsed is None:
        return None
    number, section, revision = parsed
    note_id = connection.scalar(select(notebook_numbers.NotebookNumber.note_id).where(notebook_numbers.NotebookNumber.number == number))
    if note_id is None:
        return None
    if note_id.startswith('curated:'):
        notes = notebook.curated_collection(connection) if hasattr(connection, 'info') else notebook.curated_collection()
        note = next((row for row in notes if row['id'] == note_id), None)
        saved = next((row for row in note['revisions'] if row['revision'] == revision), None) if note else None
        content, digest = (saved['content'], saved['content_sha256']) if saved else (None, None)
    else:
        saved = connection.execute(select(notebook.NotebookRevision.content, notebook.NotebookRevision.content_sha256)
            .where(notebook.NotebookRevision.note_id == note_id, notebook.NotebookRevision.revision == revision)).first()
        content, digest = saved if saved else (None, None)
    if not isinstance(content, dict) or 'body' not in content or notebook._hash(content) != digest:
        return None
    row = next((row for row in outline(content) if row['number'] == section), None)
    if row is None:
        return None
    kind, target = next(iter(row['anchor'].items()))
    if kind == 'passage':
        try:
            note_sections.resolve_passage(content, target)
        except ValueError:
            return None  # Composite historical blocks retain their own exact anchors.
    key = {'statement': 'statement_id', 'note_section': 'section_id'}.get(kind, kind)
    return {'note_id': note_id, 'revision': revision, 'content_sha256': digest, key: target}

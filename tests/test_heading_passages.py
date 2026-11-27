"""Source-backed statements retain exact identity without duplicated prose."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from crucible import notebook, note_sections, statement_workflow as workflow
from test_statement_workflow import trial, create, revise, content, publish, context, block, pin, argument

CASES = json.loads((Path(__file__).parent / 'fixtures/heading-passages.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_heading_passage_grammar(case):
    value = {'body': case['body']}
    if case.get('invalid'):
        with pytest.raises(ValueError):
            note_sections.heading_passage(value, {'heading': case['heading']})
    else:
        passage = note_sections.heading_passage(value, {'heading': case['heading']})
        assert (passage['start_line'], passage['end_line']) == (case['start'], case['end'])
        lines = case['body'].replace('\r\n', '\n').replace('\r', '\n').split('\n')
        assert passage['text'] == '\n'.join(lines[case['start'] - 1:case['end']])
        assert note_sections.resolve_passage(value, f"p1-b-{case['start']}-{case['end']}")['definition'] == passage['text']


def headed():
    return content(body='## Premise\nBound at most one half.\n### Scope\nOnly this experiment.\n## Later\nSeparate prose.',
                   statements=[{'id': 'rare-failure', 'location': {'heading': 'Premise'}}])


def test_container_is_explicit_and_authored_record_never_materializes():
    value = headed()
    value['sections'] = [{'id': 'explanation', 'title': 'Human explanation', 'body': '## Premise\nA different text.'}]
    statement = value['statements'][0]
    assert notebook._content(value) == value
    resolved = workflow.materialize_statement(statement, value)
    assert 'one half' in resolved['definition'] and 'Different' not in resolved['definition']
    assert 'location' not in resolved and resolved['title'] == 'Premise'
    section_statement = {**statement, 'location': {'heading': 'Premise', 'section_id': 'explanation'}}
    assert workflow.materialize_statement(section_statement, value)['definition'] == '## Premise\nA different text.'
    with pytest.raises(ValueError, match='source section'):
        workflow.materialize_statement({**statement, 'location': {'heading': 'Premise', 'section_id': 'missing'}}, value)
    assert 'definition' not in statement and 'title' not in statement


def test_definition_hash_changes_with_passage_not_unrelated_prose_and_legacy_is_unchanged():
    value = headed(); statement = value['statements'][0]
    before = workflow.definition_hash(statement, value)
    changed = deepcopy(value); changed['body'] = value['body'].replace('one half', 'one quarter')
    assert workflow.definition_hash(statement, changed) != before
    changed['body'] = value['body'].replace('Separate prose.', 'Other unrelated prose.')
    assert workflow.definition_hash(statement, changed) == before
    legacy = block()
    assert workflow.definition_hash(legacy) == notebook._hash({'definition': legacy['definition'], 'definitions': []})
    old = content(statements=[legacy])
    assert notebook._content(old) == old
    with pytest.raises(ValueError, match='either'):
        workflow.Statement.model_validate({**statement, 'definition': 'Duplicate copy'})


def test_reading_examples_validate_and_the_counterexample_count_is_reproducible():
    raw = (Path(__file__).parents[1] / 'docs/examples/note-reading-demo.json').read_text()
    target = '#notebook?note=fixture&revision=2&sha256=' + 'a' * 64
    raw = raw.replace('{{repetition.premise}}', target + '&statement=fresh-trials')
    raw = raw.replace('{{repetition.bound}}', target + '&statement=repetition-bound')
    assert '{{repetition.' not in raw
    notes = json.loads(raw)['notes']
    for value in notes:
        assert notebook._content(value) == value
        assert value['extensions']['illustrative'] is True
        assert 'human-written' not in value['tags']
    from itertools import product
    from fractions import Fraction
    outcomes = list(product((0, 1), repeat=6))
    assert Fraction(sum(shared == 1 for shared, *fresh in outcomes), len(outcomes)) == Fraction(1, 2)


def test_api_pins_access_and_resolved_dependencies(trial):
    _, owner, reviewer, reader = trial
    value = headed(); first = create(owner, value)
    exact = f"/api/notebook/notes/{first['id']}?revision=1&sha256={first['content_sha256']}&statement=rare-failure"
    assert reader.client.get(exact).status_code == 404
    publish(owner, reviewer, first)
    shown = reader.client.get(exact).json()['note']
    assert shown['revisions'][0]['content'] == value
    resolved = reader.client.get(context(first)).json()['statement']
    assert resolved['definition'] == '## Premise\nBound at most one half.\n### Scope\nOnly this experiment.'
    assert resolved['source'] == pin(first)
    assert resolved['definition_sha256'] == workflow.definition_hash(value['statements'][0], value)
    downstream = create(owner, content(arguments=[argument(first)]))
    publish(owner, reviewer, downstream)
    result = reader.client.get(context(downstream, 'argument', 'attack-success')).json()['argument']
    assert result['dependencies'][0]['resolved_statement'] == resolved
    value['body'] = value['body'].replace('one half', 'one quarter')
    later = revise(owner, first, value)
    assert later['content_sha256'] != first['content_sha256']
    assert reader.client.get(exact).json()['note']['revisions'][0]['content']['body'] != value['body']
    assert reader.client.get(exact.replace(first['content_sha256'], '0' * 64)).status_code == 409


def test_unregistered_passages_are_exact_and_do_not_require_unique_names():
    value = content(body='## Repeat\nFirst.\n### Detail\nNested.\n## Repeat\nSecond.',
                    sections=[{'id': 'explanation', 'title': 'Overview', 'body': '## Repeat\nReadable.'}])
    before = deepcopy(value)
    found = note_sections.passages(value)
    assert [x['id'] for x in found] == [
        'p1-s-explanation-0-2', 'p1-s-explanation-1-2', 'p1-b-1-4', 'p1-b-3-4', 'p1-b-5-6']
    assert all('label' not in item for item in found)  # Locs are assigned, not ordinal labels.
    assert note_sections.resolve_passage(value, 'p1-b-1-4')['definition'] == '## Repeat\nFirst.\n### Detail\nNested.'
    assert note_sections.resolve_passage(value, 'p1-b-5-6')['definition'] == '## Repeat\nSecond.'
    assert note_sections.resolve_passage(value, 'p1-s-explanation-0-2')['definition'] == 'Overview\n\n## Repeat\nReadable.'
    assert value == before == notebook._content(value)
    for selector in ('p1-b-1-3', 'p1-b-2-4', 'p1-b-0-6', 'p2-b-1-4', 'p1-b-01-4'):
        with pytest.raises(ValueError):
            note_sections.resolve_passage(value, selector)
    with pytest.raises(ValueError, match='legacy'):
        note_sections.resolve_passage({'body': '## A\n[[statement:example]]'}, 'p1-b-1-2')


def test_passage_refs_preserve_raw_content_and_reject_mixed_selectors():
    value = content(body='## Claim\nA precise proposition.', arguments=[argument(
        conclusion=None, conclusion_statement={'passage': 'p1-b-1-2'}, status='unconditional', dependencies=[])])
    value['arguments'][0].pop('conclusion')
    assert notebook._content(value) == value
    with pytest.raises(ValueError):
        workflow.StatementReference(passage='p1-b-1-2', statement_id='claim')
    with pytest.raises(ValueError):
        workflow.StatementReference(passage='p1-b-1-2', note_id='incomplete-pin')
    value['arguments'][0]['conclusion_statement']['passage'] = 'p1-b-1-1'
    with pytest.raises(ValueError):
        notebook._content(value)


def test_reader_can_cite_an_ordinary_note_without_modifying_it(trial):
    app, owner, reviewer, reader = trial
    value = content(body='## Premise\nAt most one half.\n### Scope\nThis experiment.\n## Other\nNot included.')
    first = create(owner, value)
    url = f"/api/notebook/notes/{first['id']}?revision=1&sha256={first['content_sha256']}&passage=p1-b-1-4"
    assert reader.client.get(url).status_code == 404
    publish(owner, reviewer, first)
    response = reader.client.get(url)
    assert response.status_code == 200, response.text
    selected = response.json()['note']['selected_passage']
    assert selected['label'] == '' and 'Not included.' not in selected['definition']
    assert response.json()['note']['revisions'][0]['content'] == value
    # A different participant can use this exact ordinary paragraph as a premise.
    consumer = content(arguments=[argument()])
    consumer['arguments'][0]['dependencies'][0]['statement'] = {**pin(first), 'passage': 'p1-b-1-4'}
    second = create(reader, consumer)
    resolved = reader.client.get(context(second, 'argument', 'attack-success'))
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['argument']['dependencies'][0]['resolved_statement']['definition'] == selected['definition']
    publish(reader, reviewer, second)
    connections = owner.client.get(context(first, 'passage', 'p1-b-1-4')).json()
    assert connections['related_uses'][0]['source'] == pin(second)
    assert owner.client.get(f"/api/notebook/notes/{first['id']}").json()['note']['revision'] == 1
    # Citation reads and downstream use did not revise/register the original note.
    changed = deepcopy(value); changed['body'] = '## New intro\nAdditional context.\n' + value['body']
    later = revise(owner, first, changed); publish(owner, reviewer, later)
    assert reader.client.get(url).json()['note']['selected_passage'] == selected
    assert reader.client.get(url.replace(first['content_sha256'], later['content_sha256'])).status_code == 409
    assert reader.client.get(url + '&statement=anything').status_code == 422
    assert reader.client.get(url.replace('p1-b-1-4', 'p1-b-1-3')).status_code == 422
    assert reader.client.get(url.replace('&sha256=' + first['content_sha256'], '')).status_code == 422
    assert reader.client.get(url + '&passage=p1-b-1-4').status_code == 422
    assert reader.client.get(context(first, 'passage', 'p1-b-1-3')).status_code == 422
    # Withdrawn source authorization is rechecked for the downstream argument.
    result = owner.post(f"/api/notebook/notes/{first['id']}/publication-retractions", {'revision':later['revision'],'content_sha256':later['content_sha256'],'idempotency_key':str(uuid4())})
    assert result.status_code == 200, result.text
    assert reader.client.get(url).status_code == 404
    assert reader.client.get(context(second, 'argument', 'attack-success')).status_code == 404


def test_agent_discovers_and_reads_exact_passages_under_existing_scope(trial):
    from test_note_ratings import agent
    _, owner, reviewer, reader = trial
    saved = create(owner, content(body='## Claim\nExact text.'))
    client = agent(reader, scopes=('notes:read',))
    url = f"/api/agent/notes/{saved['id']}"
    params = {'revision':1,'content_sha256':saved['content_sha256'],'passage':'p1-b-1-2', 'include_passages':'true'}
    assert client.get(url, params=params).status_code == 404
    publish(owner, reviewer, saved)
    result = client.get(url, params=params)
    assert result.status_code == 200, result.text
    record = result.json()['note']
    assert record['passages'][0]['id'] == params['passage']
    assert record['selected_passage']['definition'] == '## Claim\nExact text.'
    assert client.get(url, params={'passage':'p1-b-1-2'}).status_code == 422
    assert client.get(url, params={**params,'passage':'p1-b-1-1'}).status_code == 422
    assert agent(reader, scopes=('notebook:read',)).get(url, params=params).status_code == 403
    client.close()


def test_a_passage_and_a_registered_statement_with_the_same_spelling_are_distinct(trial):
    from test_statement_workflow import propose, proposal, review_body, ROOT
    _, owner, reviewer, reader = trial
    value = content(body='## Passage\nThe prose has a different meaning.',
        statements=[{**block(), 'id':'p1-b-1-2'}], arguments=[argument()])
    value['arguments'][0]['dependencies'][0]['statement'] = {'statement_id':'p1-b-1-2'}
    saved = create(owner, value); publish(owner, reviewer, saved)
    proposed = propose(owner, proposal(saved))
    result = reviewer.post(ROOT + '/updates/' + proposed['id'] + '/review', review_body(proposed))
    assert result.status_code == 200, result.text
    assert reader.client.get(context(saved, 'statement', 'p1-b-1-2')).json()['targets']
    assert not reader.client.get(context(saved, 'passage', 'p1-b-1-2')).json()['targets']


def test_identical_passage_text_under_different_context_is_not_the_same_claim():
    a = content(body='Let p be one half.\n## Claim\nFailure probability is at most p.')
    b = content(body='Let p be one third.\n## Claim\nFailure probability is at most p.')
    first = note_sections.resolve_passage(a, 'p1-b-2-3')
    other = note_sections.resolve_passage(b, 'p1-b-2-3')
    assert first['definition'] == other['definition']
    assert first['definition_sha256'] != other['definition_sha256']


def test_passage_equivalence_requires_original_source_and_legacy_serialization_stays_stable():
    original = {**note_sections.resolve_passage({'body':'## Claim\nText.'}, 'p1-b-1-2'),
                'source':{'note_id':'original','revision':1,'content_sha256':'a'*64}}
    copied = {**original, 'source':{**original['source'], 'note_id':'copied'}}
    assert workflow.same_conclusion(original, deepcopy(original))
    assert not workflow.same_conclusion(original, copied)
    assert not workflow.same_conclusion(original, {**original, 'source_passage':{}})
    legacy = workflow.StatementReference(statement_id='claim')
    assert legacy.model_dump(exclude_none=False) == {'statement_id':'claim','note_id':None,'revision':None,'content_sha256':None}


@pytest.mark.parametrize('different_context', [False, True])
def test_proposed_argument_cannot_replace_original_passage_by_copy(trial, different_context):
    from test_statement_workflow import proposal, ROOT
    _, owner, _, _ = trial
    first = content(body='Let p be one half.\n## Claim\nFailure is at most p.')
    original = create(owner, first)
    duplicate = deepcopy(first)
    if different_context:
        duplicate['body'] = duplicate['body'].replace('one half', 'one third')
    copied = create(owner, duplicate)
    for selected, status in [(copied, 409), (original, 200)]:
        record = argument(conclusion_statement={**pin(selected), 'passage':'p1-b-2-3'},
                          status='unconditional', dependencies=[])
        record.pop('conclusion')
        candidate = create(owner, content(arguments=[record]))
        packet = proposal(candidate)
        packet['target']['conclusion_statement'] = {**pin(original), 'passage':'p1-b-2-3'}
        result = owner.post(ROOT + '/updates', packet)
        assert result.status_code == status, result.text

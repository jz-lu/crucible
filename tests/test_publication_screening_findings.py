"""Selected-revision reviewer findings; synthetic workers, no provider transport."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import event, select

from crucible.publication_screening_jobs import digest
from crucible.publication_screening_models import ScreeningAttempt, ScreeningJob
from test_notebook import approve, content, create, revise
from test_notebook_agents import agent
from test_publication_screening_integration import screened, configure, request, run, SyntheticProvider


def detail(actor, saved, revision=None):
    response = actor.client.get(f'/api/notebook/notes/{saved["id"]}',
                                params={'revision': revision or saved['revision']})
    assert response.status_code == 200, response.text
    return response.json()['note']


def screening(actor, saved, revision=None):
    number = revision or saved['revision']
    row = next(row for row in detail(actor, saved, revision)['revisions'] if row['revision'] == number)
    return row.get('publication', {}).get('screening')


def flagged(system, actor=None):
    actor = actor or system.owner
    configure(system)
    saved = create(actor, content(body='Synthetic evidence line.\nAnother line.'))
    assert request(actor, saved).status_code == 200
    assert run(system, SyntheticProvider(flagged=True))['status'] == 'human_review'
    return saved


def test_review_findings_are_exact_private_and_not_scientific_authority(screened):
    saved = flagged(screened)
    newer = revise(screened.owner, saved, content(title='Later private revision'))
    review = screening(screened.reviewer, saved)
    assert review['revision'] == 1 and review['content_sha256'] == saved['content_sha256']
    assert review['status'] == 'human_review'
    found = review['reviewer']
    assert found['outcome'] == 'human_review_needed'
    assert found['concerns'][0]['field_label'] == 'Body'
    assert found['concerns'][0]['line_start'] == 1
    assert 'Synthetic private material' in found['concerns'][0]['explanation']
    assert screening(screened.owner, newer) is None
    owner = screening(screened.owner, saved)
    assert set(owner) == {'revision', 'content_sha256', 'status', 'message'}
    assert 'Awaiting human publication review' in owner['message']
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    approve(screened.reviewer, saved)
    # Community publication does not publish the internal screening evidence.
    public = detail(screened.reader, saved)
    assert 'screening' not in json.dumps(public)
    assert 'Synthetic private material' not in json.dumps(public)
    assert screening(screened.reviewer, saved)['reviewer'] == found
    encoded = json.dumps(detail(screened.reviewer, saved))
    for private_accounting in ('provider_response_id', 'synthetic-response', 'usage',
                               'charged_micro_usd', 'lease_token'):
        assert private_accounting not in encoded


def test_owner_status_and_lists_never_load_attempt_findings(screened):
    saved = flagged(screened)
    publication_id = detail(screened.owner, saved)['revisions'][0]['publication']['id']
    queries = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)
    event.listen(screened.app.state.db.engine, 'before_cursor_execute', capture)
    try:
        paths = [f'/api/notebook/notes/{saved["id"]}', '/api/notebook/notes?scope=mine',
                 '/api/notebook/notes?scope=mine&projection=index',
                 '/api/notebook/publication-status?publication_id=' + publication_id]
        for path in paths:
            response = screened.owner.client.get(path)
            assert response.status_code == 200
            assert 'Synthetic private material' not in response.text
            assert 'usage' not in response.text
        queue = screened.reviewer.client.get('/api/notebook/review-queue')
        assert queue.status_code == 200 and 'Synthetic private material' not in queue.text
    finally:
        event.remove(screened.app.state.db.engine, 'before_cursor_execute', capture)
    assert not any('publication_screening_attempts' in query for query in queries)


def test_reviewer_agent_does_not_inherit_private_findings(screened):
    saved = flagged(screened, screened.reviewer)
    assert screening(screened.reviewer, saved)['reviewer']['concerns']
    client = agent(screened.app, screened.reviewer, ['notebook:read'])
    try:
        response = client.get(f'/api/notebook/notes/{saved["id"]}')
        assert response.status_code == 200
        value = response.json()['note']['revisions'][0]['publication']['screening']
        assert 'reviewer' not in value
        assert 'Synthetic private material' not in response.text
    finally:
        client.close()


@pytest.mark.parametrize('damage', ['digest', 'input', 'lines', 'field', 'excess', 'outcome', 'admission'])
def test_unverified_findings_fail_closed_without_hiding_the_note(screened, damage):
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt))
        result = deepcopy(attempt.result)
        if damage == 'input': result['input_sha256'] = '0' * 64
        if damage == 'lines': result['concerns'][0]['line_end'] = 999999
        if damage == 'field': result['concerns'][0]['field_id'] = 'f99999'
        if damage == 'excess': result['concerns'] *= 5
        if damage == 'outcome': attempt.outcome = 'no_concern_found'
        if damage == 'admission': result['input_admission']['input_tokens'] = True
        attempt.result = result
        attempt.result_sha256 = '0' * 64 if damage == 'digest' else digest(result)
    result = screening(screened.reviewer, saved)['reviewer']
    assert result['concerns'] == [] and result['limitations'] == []
    assert 'could not be verified' in result['unavailable']
    assert 'Synthetic private material' not in json.dumps(result)


@pytest.mark.parametrize('state,code,expected', [
    ('queued', None, 'Awaiting automated screening'),
    ('running', None, 'screening is in progress'),
    ('human_review', 'provider_authentication_failed', 'credentials need operator attention'),
    ('human_review', 'provider_access_denied', 'access needs operator attention'),
    ('human_review', 'provider_quota_exhausted', 'quota is exhausted'),
    ('human_review', 'provider_configuration_error', 'configuration needs operator attention'),
    ('human_review', 'provider_timeout', 'timed out'),
    ('human_review', 'input_token_limit', 'token limit'),
    ('human_review', 'input_content_size_limit', 'content-size limit'),
    ('human_review', 'input_structure_limit', 'structure limit'),
    ('human_review', 'provider_count_unavailable', 'could not determine the input size'),
    ('human_review', 'untrusted arbitrary failure text', 'could not finish'),
])
def test_pending_and_failure_copy_is_fixed_and_does_not_accuse_the_author(screened, state, code, expected):
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    with screened.app.state.db.transaction(write=True) as session:
        job = session.scalar(select(ScreeningJob))
        job.status, job.error_code = state, code
    value = screening(screened.reviewer, saved)
    assert expected in (value['reviewer']['reason'] or value['message'])
    assert value['reviewer']['outcome'] is None and not value['reviewer']['concerns']
    assert 'untrusted arbitrary failure text' not in json.dumps(value)
    assert 'reviewer' not in screening(screened.owner, saved)


def test_historical_findings_survive_later_prompt_size_changes(screened, monkeypatch):
    from crucible import publication_screening_policy as policy
    saved = flagged(screened)
    original = screening(screened.reviewer, saved)['reviewer']
    monkeypatch.setattr(policy, 'POLICY_PROMPT', 'future prompt' * 20000)
    assert screening(screened.reviewer, saved)['reviewer'] == original


def test_historical_findings_without_admission_receipts_remain_readable(screened):
    saved = flagged(screened)
    original = screening(screened.reviewer, saved)['reviewer']
    with screened.app.state.db.transaction(write=True) as session:
        job, attempt = session.scalar(select(ScreeningJob)), session.scalar(select(ScreeningAttempt))
        policy, result = deepcopy(job.policy), deepcopy(attempt.result)
        policy['model_configuration'].pop('input_admission')
        result.pop('input_admission')
        job.policy, job.policy_id = policy, digest(policy)
        attempt.result, attempt.result_sha256 = result, digest(result)
    assert screening(screened.reviewer, saved)['reviewer'] == original

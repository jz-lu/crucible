"""Real notebook transitions with a synthetic injected screener, never an API call.

Screening may grant community visibility to an exact requested revision. It is
not a scientific review, scheme admission, bounty decision, or points award.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import beta_access, bounty_allowance, notebook, registry
from crucible.app import create_app
from crucible.bounty_models import BountyAward, BountyPolicy, BountyTask
from crucible.config import Settings
from crucible.db import User
from crucible.note_ratings import NoteRating
from crucible.protocol_proposals import ProtocolProposal
from crucible.publication_screening_models import AutomaticPublication, ScreeningAttempt, ScreeningJob
from crucible.research_reviews import ResearchRequest
from crucible.scoring_awards import SchemeAdmissionCredit
from test_notebook import Actor, approve, content, create, publication_body, revise
from test_notebook_moderation import withdraw


@pytest.fixture
def screened(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, 'ADAPTERS', {key: value for key, value in registry.ADAPTERS.items()
        if key[0] in ('teaching-dh-kem', 'rsa-kem')})
    monkeypatch.setattr(registry, 'CURRENT', {'teaching-dh-kem': '7', 'rsa-kem': '4'})
    now = [datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()]
    identifiers = [str(uuid4()) for _ in range(5)]
    settings = Settings(f"sqlite:///{tmp_path / 'screening.db'}", b's' * 32,
        origins=('http://testserver',), reviewer_ids=(identifiers[1], identifiers[4]),
        rate_limits=False, access_mode='beta', publication_screening_mode='automatic',
        publication_screening_model='gpt-6-luna')
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in zip(identifiers, ('author', 'reviewer', 'solver', 'reader', 'reviewer2')):
            session.add(User(id=identifier, handle=handle, created_at=now[0]))
        session.flush()
        for identifier in identifiers:
            beta_access.set_membership(session, identifier, 'active', now[0])
        session.add(BountyPolicy(id=bounty_allowance.INITIAL_ID,
            effective_month=bounty_allowance.INITIAL_MONTH,
            configuration=deepcopy(bounty_allowance.INITIAL_CONFIGURATION), created_at=now[0]))
    actors = [Actor(app, identifier) for identifier in identifiers]
    try:
        yield SimpleNamespace(app=app, owner=actors[0], reviewer=actors[1], solver=actors[2],
                              reader=actors[3], reviewer2=actors[4], now=now)
    finally:
        for actor in actors:
            actor.client.close()
        app.state.db.engine.dispose()


def request(actor, saved, *, consent=True, key=None):
    notice = actor.client.get('/api/notebook/publication-policy')
    assert notice.status_code == 200, notice.text
    return actor.post(f'/api/notebook/notes/{saved["id"]}/publication-requests?summary=true',
        publication_body(saved, screening_consent=consent, screening_policy_id=notice.json().get('policy_id'),
                         **({'idempotency_key': key} if key else {})))


def counts(system, models):
    with system.app.state.db.transaction() as session:
        return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in models}


def publication(system, saved):
    with system.app.state.db.transaction() as session:
        row = session.scalar(select(notebook.NotebookPublication).join(notebook.NotebookRevision)
            .where(notebook.NotebookRevision.note_id == saved['id'],
                   notebook.NotebookRevision.revision == saved['revision']))
        return {column.name: getattr(row, column.name) for column in row.__table__.columns} if row else None


def configure(system, *, mode='automatic'):
    from crucible import publication_screening_jobs as jobs, publication_screening_worker as worker
    settings = system.app.state.settings
    jobs.configure(system.app.state.db, policy=worker.policy_for(settings), mode=mode,
        monthly_budget_micro_usd=settings.publication_screening_monthly_budget_micro_usd, now=system.now[0])


def run(system, provider):
    from crucible.publication_screening_worker import run_once
    return run_once(system.app.state.db, system.app.state.settings, provider, clock=lambda: system.now[0])


class SyntheticProvider:
    """The real worker receives only a test response; there is no transport."""
    def __init__(self, *, flagged=False, callback=None):
        self.flagged, self.callback, self.inputs = flagged, callback, []

    def screen(self, prepared):
        from crucible.publication_screening_provider import ProviderResult
        from crucible.publication_screening_policy import validate_verdict
        self.inputs.append(prepared)
        if self.callback:
            self.callback()
        verdict = {'outcome': 'no_concern_found', 'concerns': [], 'limitations': []}
        if self.flagged:
            body_id = next(key for key, value in prepared.fields.items()
                           if value.location == ('body',) and not value.is_key)
            verdict = {'outcome': 'human_review_needed', 'limitations': [], 'concerns': [{
                'category': 'privacy_or_credentials', 'field_id': body_id,
                'line_start': 1, 'line_end': 1,
                'explanation': 'Synthetic private material needs an authorized check.',
                'next_step': 'Ask the author to check disclosure consent.'}]}
        return ProviderResult(verdict=validate_verdict(verdict, prepared), model='gpt-6-luna',
            response_id='synthetic-response', usage={'input_tokens': 100, 'output_tokens': 30,
                                                    'cached_input_tokens': 0, 'reasoning_tokens': 0})


def test_save_read_and_retry_enqueue_only_one_consented_exact_revision(screened):
    saved = create(screened.owner)
    revise(screened.owner, saved)
    for path in ('/api/notebook/notes', f'/api/notebook/notes/{saved["id"]}', '/api/notebook/timeline'):
        assert screened.owner.client.get(path).status_code == 200
    assert counts(screened, (ScreeningJob, ScreeningAttempt)) == {
        'publication_screening_jobs': 0, 'publication_screening_attempts': 0}
    latest = screened.owner.client.get(f'/api/notebook/notes/{saved["id"]}').json()['note']
    key = str(uuid4())
    first, replay = request(screened.owner, latest, key=key), request(screened.owner, latest, key=key)
    assert first.status_code == replay.status_code == 200
    assert first.json()['publication_request'] == replay.json()['publication_request']
    assert counts(screened, (ScreeningJob, ScreeningAttempt)) == {
        'publication_screening_jobs': 1, 'publication_screening_attempts': 0}


def test_pass_publishes_only_the_requested_revision_and_keeps_provenance(screened):
    configure(screened)
    first = create(screened.owner, content(title='Earlier private draft', body='EARLIER_PRIVATE_BODY'))
    submitted = revise(screened.owner, first, content(title='Submitted exact version', body='SUBMITTED_BODY',
        kind='Scheme proposal', claim='This text proposes a scheme and a bounty; it authorizes neither.',
        extensions={'synthetic-extra': 'FULL_INLINE_EXTENSION',
                    'proposed_actions': ['admit scheme', 'change parameters', 'create bounty', 'award points']}))
    assert request(screened.owner, submitted).status_code == 200
    later = revise(screened.owner, submitted, content(title='Future private draft', body='FUTURE_PRIVATE_BODY'))
    before = counts(screened, (ProtocolProposal, ResearchRequest, BountyTask, BountyAward,
                               SchemeAdmissionCredit, NoteRating))
    provider = SyntheticProvider()
    run(screened, provider)
    assert len(provider.inputs) == 1
    prepared = provider.inputs[0]
    assert prepared.content_sha256 == submitted['content_sha256']
    assert 'SUBMITTED_BODY' in prepared.input_json and 'FULL_INLINE_EXTENSION' in prepared.input_json
    assert 'EARLIER_PRIVATE_BODY' not in prepared.input_json and 'FUTURE_PRIVATE_BODY' not in prepared.input_json
    row = publication(screened, submitted)
    assert row['decision'] == 'approved' and row['decision_source'] == 'automated'
    assert row['reviewer_id'] is row['review_key'] is row['review_sha256'] is None
    assert row['test_only_self_review'] is False
    with screened.app.state.db.transaction() as session:
        receipt = session.get(AutomaticPublication, row['id'])
        assert receipt.revision_id == row['revision_id']
        assert receipt.content_sha256 == submitted['content_sha256']
        assert receipt.model_id == 'gpt-6-luna'
        assert session.get(ScreeningAttempt, receipt.attempt_id).result_sha256 == receipt.result_sha256
    public = screened.reader.client.get(f'/api/notebook/notes/{submitted["id"]}').json()['note']
    assert public['revision'] == submitted['revision']
    assert [item['revision'] for item in public['revisions']] == [submitted['revision']]
    for private in (first, later):
        assert screened.reader.client.get(f'/api/notebook/notes/{private["id"]}?revision={private["revision"]}').status_code == 404
    assert counts(screened, (ProtocolProposal, ResearchRequest, BountyTask,
        BountyAward, SchemeAdmissionCredit, NoteRating)) == before
    assert all(person['points'] == 0 for person in screened.reader.client.get('/api/people').json()['people'])
    run(screened, provider)
    assert len(provider.inputs) == 1
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 1}


def test_flagged_revision_stays_private_and_can_receive_a_human_decision(screened):
    configure(screened)
    saved = create(screened.owner, content(body='SYNTHETIC_PRIVATE_MATERIAL'))
    assert request(screened.owner, saved).status_code == 200
    provider = SyntheticProvider(flagged=True)
    run(screened, provider)
    assert publication(screened, saved)['decision'] is None
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    queue = screened.reviewer.client.get('/api/notebook/review-queue').json()['notes']
    assert [item['id'] for item in queue] == [saved['id']]
    approve(screened.reviewer, saved)
    row = publication(screened, saved)
    assert row['decision_source'] == 'human' and row['reviewer_id'] == screened.reviewer.identifier
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 200


@pytest.mark.parametrize('failure', ['provider_timeout', 'invalid_provider_verdict'])
def test_provider_failure_remains_private_with_a_bounded_human_fallback(screened, failure):
    from crucible.publication_screening_provider import ScreeningProviderError
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    class FailedProvider:
        calls = 0
        def screen(self, prepared):
            self.calls += 1
            raise ScreeningProviderError(failure, retryable=failure == 'provider_timeout')
    provider = FailedProvider()
    run(screened, provider)
    with screened.app.state.db.transaction() as session:
        job = session.scalar(select(ScreeningJob))
        retry = job.status == 'queued'
        screened.now[0] = max(screened.now[0], job.available_at + 1)
    if retry:
        run(screened, provider)
    assert provider.calls == (2 if failure == 'provider_timeout' else 1)
    with screened.app.state.db.transaction() as session:
        assert session.scalar(select(ScreeningJob.status)) == 'human_review'
    assert publication(screened, saved)['decision'] is None
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    queue = screened.reviewer.client.get('/api/notebook/review-queue').json()['notes']
    assert [item['id'] for item in queue] == [saved['id']]
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}
    approve(screened.reviewer, saved)
    assert publication(screened, saved)['decision_source'] == 'human'


def test_measured_oversized_input_is_not_truncated_or_sent_for_generation(screened):
    import httpx
    from crucible.publication_screening_provider import COUNT_ENDPOINT, OpenAIResponsesProvider
    configure(screened)
    extensions = {f'part-{number}': 'Y' * 50_000 for number in range(3)}
    saved = create(screened.owner, content(body='X' * 40_000, extensions=extensions))
    assert request(screened.owner, saved).status_code == 200
    requests = []
    def count_only(incoming):
        import json
        assert str(incoming.url) == COUNT_ENDPOINT
        payload = json.loads(incoming.content)
        graph = json.loads(payload['input'][0]['content'][0]['text'])
        strings = [node.get('text') for node in graph['fields']]
        assert 'X' * 40_000 in strings and 'Y' * 50_000 in strings
        requests.append(incoming)
        return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 128001})
    provider = OpenAIResponsesProvider(model='gpt-6-luna', api_key='synthetic-test',
        reasoning_effort='none', transport=httpx.MockTransport(count_only))
    assert run(screened, provider)['error_code'] == 'input_token_limit'
    assert len(requests) == 1
    assert publication(screened, saved)['decision'] is None
    with screened.app.state.db.transaction() as session:
        assert session.scalar(select(ScreeningJob.status)) == 'human_review'
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.result is None and attempt.charged_micro_usd == 0 and attempt.usage_known
    original = screened.owner.client.get(f'/api/notebook/notes/{saved["id"]}').json()['note']
    assert original['revisions'][0]['content']['body'] == 'X' * 40_000
    assert original['revisions'][0]['content']['extensions'] == extensions


@pytest.mark.parametrize('human_decision', ['approved', 'changes_requested'])
def test_human_decision_during_model_call_wins_over_late_pass(screened, human_decision):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    def human_review():
        response = screened.reviewer.post(f'/api/notebook/notes/{saved["id"]}/publication-reviews',
            publication_body(saved, decision=human_decision, reason='Human decision made while the synthetic call is pending.'))
        assert response.status_code == 200, response.text
    run(screened, SyntheticProvider(callback=human_review))
    row = publication(screened, saved)
    assert row['decision'] == human_decision and row['decision_source'] == 'human'
    assert row['reviewer_id'] == screened.reviewer.identifier
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


def test_human_publication_then_withdrawal_cannot_be_republished_by_late_pass(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    def human_withdrawal():
        approve(screened.reviewer, saved)
        response = withdraw(screened.reviewer, saved)
        assert response.status_code == 200, response.text
    run(screened, SyntheticProvider(callback=human_withdrawal))
    assert publication(screened, saved)['decision_source'] == 'human'
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 410
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


def test_operator_disable_during_call_stops_automatic_publication(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    run(screened, SyntheticProvider(callback=lambda: configure(screened, mode='disabled')))
    assert publication(screened, saved)['decision'] is None
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


def test_setting_alone_does_not_activate_the_durable_operator_control(screened):
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    provider = SyntheticProvider()
    run(screened, provider)
    assert provider.inputs == [] and publication(screened, saved)['decision'] is None
    configure(screened)
    run(screened, provider)
    assert len(provider.inputs) == 1 and publication(screened, saved)['decision'] == 'approved'


def test_revoked_author_cannot_be_auto_published_after_an_inflight_call(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    def revoke():
        with screened.app.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, screened.owner.identifier, 'revoked', screened.now[0])
    run(screened, SyntheticProvider(callback=revoke))
    assert publication(screened, saved)['decision'] is None
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


def bounty_system(system):
    return (system.app, system.owner, system.reviewer, system.solver,
            system.reader, system.reviewer2, system.now)


def pin(saved):
    return {'note_id': saved['id'], 'revision': saved['revision'], 'content_sha256': saved['content_sha256']}


def test_automated_publication_is_valid_evidence_but_does_not_award_a_bounty(screened):
    from test_bounties import assess, opened, submit
    configure(screened)
    task = opened(bounty_system(screened))
    saved = create(screened.solver)
    assert request(screened.solver, saved).status_code == 200
    run(screened, SyntheticProvider())
    value = submit(screened.solver, task, pin(saved))
    assert value['bounty']['award'] is None and value['bounty']['status'] == 'open'
    assert counts(screened, (BountyAward,)) == {'bounty_awards': 0}
    value = assess(screened.reviewer, value, 'pipeline')
    assert value['bounty']['status'] == 'completed' and value['bounty']['award']['points'] == 100


def test_automated_label_without_a_receipt_cannot_supply_bounty_evidence(screened):
    from test_bounties import body, opened
    task = opened(bounty_system(screened))
    saved = create(screened.solver)
    assert request(screened.solver, saved).status_code == 200
    row = publication(screened, saved)
    with screened.app.state.db.transaction(write=True) as session:
        record = session.get(notebook.NotebookPublication, row['id'])
        record.decision, record.decision_source = 'approved', 'automated'
        record.reviewed_at = screened.now[0]
    response = screened.solver.post(f'/api/bounties/{task["bounty"]["id"]}/submissions', body(task,
        terms_sha256=task['bounty']['terms_sha256'], contribution_note=pin(saved), claim='solution',
        explanation='A bare label is not authenticated publication provenance.'))
    assert response.status_code == 409, response.text
    assert counts(screened, (BountyAward,)) == {'bounty_awards': 0}


def test_development_self_review_is_preserved_and_excluded_from_bounty_evidence(screened):
    from test_bounties import body, opened
    task = opened(bounty_system(screened))
    app = create_app(replace(screened.app.state.settings, allow_self_review=True), clock=lambda: screened.now[0])
    author = Actor(app, screened.reviewer.identifier)
    try:
        saved = create(author)
        assert request(author, saved).status_code == 200
        approve(author, saved)
        row = publication(screened, saved)
        assert row['test_only_self_review'] is True and row['decision_source'] == 'human'
        configure(screened)
        provider = SyntheticProvider()
        run(screened, provider)
        assert provider.inputs == []
        assert publication(screened, saved) == row
        response = author.post(f'/api/bounties/{task["bounty"]["id"]}/submissions', body(task,
            terms_sha256=task['bounty']['terms_sha256'], contribution_note=pin(saved), claim='solution',
            explanation='Development self review cannot become independently published evidence.'))
        assert response.status_code == 409, response.text
        assert counts(screened, (AutomaticPublication, BountyAward)) == {
            'notebook_automatic_publications': 0, 'bounty_awards': 0}
    finally:
        author.client.close()
        app.state.db.engine.dispose()


def test_enabled_request_requires_consent_and_preserves_private_draft(screened):
    saved = create(screened.owner)
    response = request(screened.owner, saved, consent=False)
    assert response.status_code == 422, response.text
    assert publication(screened, saved) is None
    assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 404
    accepted = request(screened.owner, saved)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['publication_request']['status'] == 'submitted'
    assert publication(screened, saved)['decision'] is None


def test_disabled_mode_retains_existing_human_publication_without_consent(screened):
    app = create_app(replace(screened.app.state.settings, publication_screening_mode='disabled'),
                     clock=lambda: screened.now[0])
    author = Actor(app, screened.owner.identifier)
    reviewer = Actor(app, screened.reviewer.identifier)
    try:
        saved = create(author)
        response = author.post(f'/api/notebook/notes/{saved["id"]}/publication-requests', publication_body(saved))
        assert response.status_code == 200, response.text
        approve(reviewer, saved)
        row = publication(screened, saved)
        assert row['decision_source'] == 'human'
        assert row['reviewer_id'] == screened.reviewer.identifier
        assert screened.reader.client.get(f'/api/notebook/notes/{saved["id"]}').status_code == 200
        pending = create(author, content(title='Historical pending request'))
        response = author.post(f'/api/notebook/notes/{pending["id"]}/publication-requests', publication_body(pending))
        assert response.status_code == 200, response.text
        configure(screened)
        provider = SyntheticProvider()
        run(screened, provider)
        assert provider.inputs == []
        assert publication(screened, saved) == row
        assert publication(screened, pending)['decision'] is None
        assert counts(screened, (ScreeningJob,)) == {'publication_screening_jobs': 0}
    finally:
        author.client.close()
        reviewer.client.close()
        app.state.db.engine.dispose()

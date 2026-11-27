"""Author intent may change without changing content or scientific approval."""
from sqlalchemy import func, select

from crucible import notebook, notebook_visibility
from crucible.publication_screening_models import ScreeningAttempt, ScreeningJob
from test_notebook import system, approve, create, publication_body, request_publication
from test_notebook_moderation import endpoint, shared, withdraw
from test_research_reviews import submit
from test_publication_screening_integration import screened, configure, request, run, SyntheticProvider, counts


def retract(owner, saved, body=None):
    return owner.post(endpoint(saved) + '/publication-retractions', body or publication_body(saved))


def test_approved_visibility_toggles_keep_review_content_and_old_retry_intents(system):
    app, owner, reviewer, reader, _ = system
    saved = shared(owner, reviewer)
    before = owner.client.get(endpoint(saved)).json()['note']['revisions'][0]
    hide, show = publication_body(saved), publication_body(saved)
    assert retract(owner, saved, hide).status_code == 200
    assert reader.client.get(endpoint(saved)).status_code == 404
    response = owner.post(endpoint(saved) + '/publication-requests', show)
    assert response.status_code == 200, response.text
    current = response.json()['note']
    assert current['revision_count'] == 1
    assert current['revisions'][0]['content'] == before['content']
    assert current['revisions'][0]['publication'] == before['publication']
    assert current['publication_status'] == 'approved'
    assert reader.client.get(endpoint(saved)).status_code == 200
    assert retract(owner, saved, hide).status_code == 200  # Lost old response, not a new action.
    assert reader.client.get(endpoint(saved)).status_code == 200
    assert retract(owner, saved).status_code == 200
    assert owner.post(endpoint(saved) + '/publication-requests', show).status_code == 200
    assert reader.client.get(endpoint(saved)).status_code == 404  # Old restore cannot override a newer hide.
    assert owner.post(endpoint(saved) + '/publication-requests', {**show,'content_sha256':'e'*64}).status_code == 409
    assert withdraw(reviewer, saved).status_code == 200
    assert owner.post(endpoint(saved) + '/publication-requests', publication_body(saved)).status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(notebook.NotebookRevision)) == 1
        assert session.scalar(select(func.count()).select_from(notebook_visibility.VisibilityChange)) == 3


def test_retraction_withholds_frozen_third_party_packet_and_search(system):
    _, owner, reviewer, reader, _ = system
    saved = shared(owner, reviewer)
    packet = submit(reader, saved)
    assert retract(owner, saved).status_code == 200
    assert reader.client.get('/api/notebook/notes?q=Sensitive').json()['notes'] == []
    assert reader.client.get('/api/notebook/notes?tag=sensitive-fixture').json()['notes'] == []
    result = reader.client.get('/api/notebook/research-requests/' + packet['id'])
    assert result.status_code == 200 and result.json()['request']['withdrawn_source'] is True
    assert 'Sensitive fixture' not in result.text


def test_queued_retracted_request_waits_and_resumes_same_revision(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    assert retract(screened.owner, saved).status_code == 200
    provider = SyntheticProvider()
    run(screened, provider)
    assert provider.inputs == []
    assert screened.reader.client.get(endpoint(saved)).status_code == 404
    assert screened.reviewer.client.get('/api/notebook/review-queue').json()['notes'] == []
    assert request(screened.owner, saved).status_code == 200
    run(screened, provider)
    assert len(provider.inputs) == 1
    assert screened.reader.client.get(endpoint(saved)).status_code == 200
    assert counts(screened, (ScreeningJob, ScreeningAttempt)) == {'publication_screening_jobs':1,'publication_screening_attempts':1}


def test_inflight_approval_stays_private_then_can_be_restored_without_another_call(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    def hide():
        assert retract(screened.owner, saved).status_code == 200
    provider = SyntheticProvider(callback=hide)
    run(screened, provider)
    assert len(provider.inputs) == 1
    own = screened.owner.client.get(endpoint(saved)).json()['note']
    assert own['publication_status'] == 'private'
    assert own['revisions'][0]['publication']['decision'] == 'approved'
    assert screened.reader.client.get(endpoint(saved)).status_code == 404
    assert request(screened.owner, saved).status_code == 200
    assert screened.reader.client.get(endpoint(saved)).status_code == 200
    assert screened.owner.client.get(endpoint(saved)).json()['note']['revision_count'] == 1
    assert counts(screened, (ScreeningJob, ScreeningAttempt)) == {'publication_screening_jobs':1,'publication_screening_attempts':1}


def test_legacy_cancelled_request_can_resume_and_old_cancel_retry_cannot_cancel_it(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    cancel = publication_body(saved)
    route = endpoint(saved) + '/publication-cancellations'
    first = screened.owner.post(route, cancel)
    assert first.status_code == 200
    assert request(screened.owner, saved).status_code == 200
    assert screened.owner.post(route, cancel).json() == first.json()
    provider = SyntheticProvider(); run(screened, provider)
    assert len(provider.inputs) == 1
    assert screened.reader.client.get(endpoint(saved)).status_code == 200
    assert screened.owner.client.get(endpoint(saved)).json()['note']['revision_count'] == 1

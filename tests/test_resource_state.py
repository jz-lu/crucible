"""Desired-state lifecycle is evidence, not an inference from a missing report."""
from copy import deepcopy
import hashlib
import io
import zipfile

from fastapi import HTTPException
import pytest
from sqlalchemy import event as sql_event, select
from crucible import resource_publications as rp, resource_state as rs
from test_resource_publications import (system, signing_key, token, auth, archive, fixture_files,
    announce, desired_document, activation_for, IDENTITY, NOW)

URL = '/api/protocol/resources?scheme_id=dhkem-x25519&version=1'


def source(run, commit='a', attempt=1):
    return {**IDENTITY, 'commit': commit * 40, 'run_id': str(run), 'run_attempt': str(attempt)}


def state(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def event(db, identity=IDENTITY, *, expected=None, phase='queued', reason='scheduled'):
    document = {'format': 'crucible-resource-event-v1', 'source': identity, 'scheme_id': 'dhkem-x25519', 'version': '1',
                'expected': expected or fixture_files()[1]['expected'], 'phase': phase, 'reason': reason}
    return rs.ingest_control(db, document, identity, NOW, rp.validate_binding)


def report(db, identity=IDENTITY, expected=None):
    files, publication = fixture_files(identity)
    if expected:
        publication['expected'] = expected
    return rp.ingest(db, archive(files, publication), identity, NOW)


def test_docs_only_commit_preserves_activation_and_exact_current_report(system):
    app, client = system; db = app.state.db
    before = state(client)['expected']
    first = report(db)
    receipt = announce(db, source(101, 'b'))
    assert receipt['current'] and receipt['bindings'][0]['activation_id'] == before['activation_id']
    current = state(client)
    assert current['publication']['id'] == first['publication_id'] and current['state'] == 'current'
    assert current['publication']['source']['commit'] != source(101, 'b')['commit']


def test_changes_removal_readd_and_rollback_get_distinct_activations(system):
    app, client = system; db = app.state.db
    original = report(db); ids = [state(client)['expected']['activation_id']]
    changed = announce(db, source(101), producer='e' * 64)
    ids.append(changed['bindings'][0]['activation_id'])
    assert state(client)['state'] == 'outdated' and state(client)['publication'] is None
    removed = {'format': 'crucible-resource-desired-v1', 'source': source(102), 'bindings': []}
    rs.ingest_control(db, removed, source(102), NOW, rp.validate_binding)
    assert state(client)['state'] == 'retired' and state(client)['expected'] is None
    restored = announce(db, source(103), producer='e' * 64)
    ids.append(restored['bindings'][0]['activation_id'])
    rollback = announce(db, source(104))
    ids.append(rollback['bindings'][0]['activation_id'])
    assert len(set(ids)) == 4
    assert state(client)['publication'] is None
    assert state(client)['historical'][0]['id'] == original['publication_id']
    historical = client.get(URL + '&publication_id=' + original['publication_id']).json()
    assert historical['selected_historical'] and historical['resources']['configurations']
    expected = {k: rollback['bindings'][0][k] for k in ('activation_id', 'fingerprint', 'producer_sha256')}
    latest = report(db, source(104), expected)
    assert state(client)['publication']['id'] == latest['publication_id']


def test_late_old_evidence_is_retained_without_becoming_current(system):
    app, client = system; db = app.state.db
    announce(db, source(102), producer='e' * 64)
    late = report(db, source(101))
    assert not late['current'] and state(client)['publication'] is None
    event(db, source(101), phase='failed', reason='measurement_failed')
    assert state(client)['lifecycle'] is None
    assert state(client)['state'] == 'outdated'
    assert client.get('/api/resource-publications/' + late['publication_id'] + '/download').status_code == 200


def test_snapshot_order_idempotency_and_conflicts(system):
    app, _ = system; db = app.state.db
    current = announce(db, source(102))
    assert current == announce(db, source(102))
    assert not announce(db)['current']  # exact earlier retry is harmless
    with pytest.raises(HTTPException) as exc:
        announce(db, source(101))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        announce(db, source(102), producer='e' * 64)
    assert exc.value.status_code == 409
    with db.transaction() as session:
        assert len(session.scalars(select(rs.ResourceActivation)).all()) == 1


def test_evidenced_queue_terminal_and_complete_precedence(system):
    app, client = system; db = app.state.db
    assert state(client)['state'] == 'not_calculated'
    queued = event(db)
    assert queued == event(db) and state(client)['state'] == 'queued'
    failed = event(db, phase='failed', reason='measurement_failed')
    assert state(client)['state'] == 'failed'
    assert failed == event(db, phase='failed', reason='measurement_failed')
    with pytest.raises(HTTPException) as exc:
        event(db, phase='timed_out', reason='measurement_timeout')
    assert exc.value.status_code == 409
    report(db)
    assert state(client)['state'] == 'current' and state(client)['lifecycle'] is None
    event(db, source(101))
    assert state(client)['state'] == 'current' and state(client)['lifecycle']['phase'] == 'queued'
    event(db, source(101), phase='failed', reason='measurement_failed')
    assert state(client)['state'] == 'current' and state(client)['lifecycle']['phase'] == 'failed'


def test_terminal_first_cannot_regress_to_queued(system):
    app, client = system
    event(app.state.db, phase='timed_out', reason='measurement_timeout')
    with pytest.raises(HTTPException) as exc:
        event(app.state.db)
    assert exc.value.status_code == 409 and state(client)['state'] == 'timed_out'


@pytest.mark.parametrize('mutation', ['unknown_activation', 'producer', 'fingerprint', 'early_source', 'binding'])
def test_report_requires_exact_known_activation(system, mutation):
    app, _ = system; files, pub = fixture_files(); identity = IDENTITY
    if mutation == 'unknown_activation': pub['expected']['activation_id'] = '0' * 64
    if mutation == 'producer': pub['expected']['producer_sha256'] = '0' * 64
    if mutation == 'fingerprint': pub['expected']['fingerprint'] = '0' * 64
    if mutation == 'early_source': identity = source(99); pub['source'] = identity
    if mutation == 'binding': pub['binding']['implementation']['id'] = 'other'
    with pytest.raises(HTTPException) as exc:
        rp.ingest(app.state.db, archive(files, pub), identity, NOW)
    assert exc.value.status_code == 422


def failure_archive(identity=IDENTITY, outcome='incomplete', reason='measurement_incomplete',
                    partial_status='retained', conclusion='failure', mutate=None):
    _, publication = fixture_files(identity)
    publication['outcome'] = outcome
    partial = {'workflow.json': rp.canonical({'status': 'incomplete'})} if partial_status == 'retained' else {}
    failure = {'format': 'crucible-resource-failure-v1', 'reason': reason,
        'measurement_job': {'id': '123', 'name': 'Measure dhkem-x25519@1', 'run_id': identity['run_id'],
            'run_attempt': identity['run_attempt'], 'status': 'completed', 'conclusion': conclusion},
        'partial_status': partial_status, 'files': [{'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in partial.items()]}
    if mutate: mutate(failure)
    raw_failure = rp.canonical(failure)
    publication['failure_sha256'] = hashlib.sha256(raw_failure).hexdigest()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('publication.json', rp.canonical(publication)); z.writestr('failure.json', raw_failure)
        for name, raw in partial.items(): z.writestr('partial/' + name, raw)
    return output.getvalue()


@pytest.mark.parametrize('outcome,conclusion,reason', [('incomplete','failure','measurement_incomplete'),
    ('failed','failure','measurement_failed'),('cancelled','cancelled','measurement_cancelled'),
    ('timed_out','timed_out','measurement_timeout'),('failed','success','archive_capacity')])
def test_partial_evidence_is_durable_explicit_and_not_a_graph(system, outcome, conclusion, reason):
    app, client = system
    raw = failure_archive(outcome=outcome, conclusion=conclusion, reason=reason)
    receipt = rp.ingest(app.state.db, raw, IDENTITY, NOW)
    result = state(client)
    assert result['publication'] is None and result['resources'] is None
    assert result['state'] == ('failed' if outcome == 'incomplete' else outcome)
    history = result['historical'][0]
    assert history['completeness'] == 'incomplete' and history['outcome'] == outcome
    assert client.get(history['evidence_url']).content == raw
    detail = client.get(URL + '&publication_id=' + receipt['publication_id']).json()
    assert detail['selected_historical'] and detail['resources']['configurations'] == []


@pytest.mark.parametrize('mutation', ['inventory', 'job', 'conclusion', 'path', 'status'])
def test_failure_archive_still_checks_evidence_inventory(system, mutation):
    app, _ = system
    def mutate(f):
        if mutation == 'inventory': f['files'][0]['sha256'] = '0' * 64
        if mutation == 'job': f['measurement_job']['run_attempt'] = '2'
        if mutation == 'conclusion': f['measurement_job']['conclusion'] = 'success'
        if mutation == 'path': f['files'][0]['path'] = '../escape'
        if mutation == 'status': f['partial_status'] = 'unavailable'
    with pytest.raises(HTTPException) as exc:
        rp.ingest(app.state.db, failure_archive(mutate=mutate), IDENTITY, NOW)
    assert exc.value.status_code == 422


def test_legacy_retained_evidence_never_becomes_implicitly_current(system):
    app, client = system; files, pub = fixture_files()
    pub['format'] = 'crucible-website-publication-v1'; del pub['expected']; del pub['outcome']
    raw = archive(files, pub)
    with pytest.raises(HTTPException) as exc: rp.ingest(app.state.db, raw, IDENTITY, NOW)
    assert exc.value.status_code == 422
    identifier = hashlib.sha256(raw).hexdigest()
    with app.state.db.transaction(write=True) as session:
        session.add(rp.ResourcePublication(id=identifier, scheme_id='dhkem-x25519', version='1',
            repository_id=IDENTITY['repository_id'], run_id=100, run_attempt=1, created_at=NOW,
            publication=pub, resources=files['resources.json'], bundle=raw))
    assert state(client)['publication'] is None
    assert state(client)['historical'][0]['id'] == identifier
    assert client.get(URL + '&publication_id=' + identifier).json()['selected_historical']


def test_signed_json_control_shares_only_the_pinned_workflow_boundary(system, signing_key, monkeypatch):
    app, client = system; private, keys = signing_key
    monkeypatch.setattr(auth, 'signing_keys', lambda _: keys)
    doc = desired_document(fixture_files()[1]['binding'])
    headers = {'Content-Type': 'application/json', 'Authorization': token(private)}
    assert client.post(auth.UPLOAD_PATH, content=rp.canonical(doc), headers=headers).status_code == 200
    assert client.post(auth.UPLOAD_PATH, content=rp.canonical(doc), headers={**headers, 'Cookie': 'test=1'}).status_code == 401
    with monkeypatch.context() as scoped:
        scoped.setattr(rs, 'MAX_CONTROL', 1)
        assert client.post(auth.UPLOAD_PATH, content=rp.canonical(doc), headers=headers).status_code == 413
    duplicate = b'{"format":1,"format":2}'
    assert client.post(auth.UPLOAD_PATH, content=duplicate, headers=headers).status_code == 400


def resource_read_queries(app, client, publication_id=None):
    queries = []
    def capture(_, __, statement, parameters, ___, ____):
        sql = ' '.join(statement.lower().split())
        if sql.startswith('select ') and ' from resource_publications ' in sql:
            queries.append((sql, parameters))
    sql_event.listen(app.state.db.engine, 'before_cursor_execute', capture)
    try:
        response = client.get(URL + ('&publication_id=' + publication_id if publication_id else ''))
    finally:
        sql_event.remove(app.state.db.engine, 'before_cursor_execute', capture)
    assert response.status_code == 200, response.text
    # ZIP bytes are never needed for metadata or graph reads.
    assert all('resource_publications.bundle' not in sql.split(' from ', 1)[0] for sql, _ in queries)
    projections = [(sql, parameters) for sql, parameters in queries
                   if 'resource_publications.resources' in sql.split(' from ', 1)[0]]
    return response.json(), queries, projections


def resource_history(db, count):
    identifiers = []
    for index in range(count):
        identity = source(100 + index)
        files, publication = fixture_files(identity)
        files['resources.json']['fixture_label'] = 'Synthetic resource report ' + str(index)
        files['resources.json']['fixture_padding'] = 'Plumbing fixture, not measurements. ' * 100
        receipt = rp.ingest(db, archive(files, publication), identity, NOW + index)
        identifiers.append(receipt['publication_id'])
    return identifiers


@pytest.mark.parametrize('count', [2, 24])
def test_resource_history_loads_only_the_selected_projection(system, count):
    app, client = system
    ids = resource_history(app.state.db, count)
    result, queries, projections = resource_read_queries(app, client)
    assert result['publication']['id'] == ids[-1] and result['state'] == 'current'
    assert result['resources']['fixture_label'] == 'Synthetic resource report ' + str(count - 1)
    assert [row['id'] for row in result['historical']] == list(reversed(ids[-20:-1]))
    assert len(queries) == 2 and len(projections) == 1
    assert all('resources' not in row for row in result['historical'])

    # A recent history row is in the identity map; an older one is outside the
    # twenty-row history window. Neither should fetch the unselected current
    # projection, and neither may cause one load per history label.
    for selected in (ids[-2], ids[0], ids[-1]):
        result, queries, projections = resource_read_queries(app, client, selected)
        assert result['publication']['id'] == selected
        assert result['selected_historical'] is (selected != ids[-1])
        assert result['resources']['fixture_label'] == 'Synthetic resource report ' + str(ids.index(selected))
        assert len(queries) == 3 and len(projections) == 1
        assert projections[0][1] == (selected,)
        assert 'security' not in result['resources']['configurations'][0]
        assert 'correctness' not in result['resources']['configurations'][0]


@pytest.mark.parametrize('count', [2, 24])
def test_resource_history_loads_only_one_active_failure_document(system, count):
    app, client = system
    ids = resource_history(app.state.db, count)
    failures = []
    for index in range(3):
        identity = source(100 + count + index)
        raw = failure_archive(identity=identity)
        failures.append(rp.ingest(app.state.db, raw, identity, NOW + count + index)['publication_id'])

    result, queries, projections = resource_read_queries(app, client)
    assert result['publication']['id'] == ids[-1] and result['state'] == 'current'
    assert result['lifecycle']['phase'] == 'failed'
    assert result['lifecycle']['reason'] == 'measurement_incomplete'
    assert len(queries) == 3 and len(projections) == 2
    assert projections[-1][1] == (failures[-1],)

    result, queries, projections = resource_read_queries(app, client, failures[-1])
    assert result['publication']['id'] == failures[-1] and result['selected_historical']
    assert result['publication']['completeness'] == 'incomplete'
    assert result['resources']['configurations'] == []
    assert result['resources']['failure']['reason'] == 'measurement_incomplete'
    assert len(queries) == 3 and len(projections) == 1
    assert projections[0][1] == (failures[-1],)

    result, queries, projections = resource_read_queries(app, client, ids[-2])
    assert result['publication']['id'] == ids[-2] and result['selected_historical']
    assert result['resources']['configurations']
    assert len(queries) == 4 and len(projections) == 2
    assert {parameters[0] for _, parameters in projections} == {failures[-1], ids[-2]}

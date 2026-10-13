"""An approval must bind the source that will actually serve challenges."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from crucible import manage, registry
from crucible.db import User
from crucible.manage import configure_reviewer
from crucible.protocol_admission import CHECKS, check_admission
from crucible.protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview
from crucible.protocol_specs import builtin_spec
from test_api import system


def digest(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


@pytest.fixture
def approved(system):
    app, _, settings = system
    spec = builtin_spec('teaching-dh-kem', '4')
    author, reviewer, identifier = (str(uuid4()) for _ in range(3))
    with app.state.db.transaction(write=True) as session:
        session.add_all([User(id=author, handle='author', password_hash='unused', created_at=1),
                         User(id=reviewer, handle='reviewer', password_hash='unused', created_at=1)])
        session.flush()
        session.add(ProtocolProposal(id=identifier, author_id=author, idempotency_key=str(uuid4()),
            created_at=1, status='approved', revision=1, content_sha256=digest(spec), specification=spec))
        session.flush()
        session.add(ProtocolProposalRevision(id=str(uuid4()), proposal_id=identifier, revision=1,
            created_at=1, idempotency_key=str(uuid4()), request_sha256='0'*64,
            content_sha256=digest(spec), specification=spec))
        session.add(ProtocolProposalReview(id=str(uuid4()), proposal_id=identifier, revision=1,
            reviewer_id=reviewer, created_at=2, decision='approved', content_sha256=digest(spec),
            implementation_sha256=spec['implementation']['sha256'], checks={key:True for key in CHECKS},
            notes='Reviewed exact source and specification.', evidence='Pinned test run and independent source review.',
            idempotency_key=str(uuid4()), request_sha256='0'*64))
    return app, identifier, author, reviewer, settings


def test_admission_binding_is_read_only_and_checks_exact_source_without_generation(approved, monkeypatch):
    app, identifier, _, _, _ = approved
    adapter = registry.ADAPTERS[('teaching-dh-kem','4')]
    monkeypatch.setattr(adapter, 'generate', lambda _: pytest.fail('Admission check must not generate instances'))
    before = dict(registry.CURRENT)
    with app.state.db.transaction() as session:
        report = check_admission(session, identifier, 'teaching-dh-kem', '4')
    assert report['status'] == 'binding-check-passed'
    assert report['profiles_checked'] == 281
    assert report['implementation_sha256'] == adapter.IMPLEMENTATION_SHA256
    assert registry.CURRENT == before


@pytest.mark.parametrize('failure', ['not-approved','self-review','incomplete-check','no-evidence',
                                    'wrong-reviewed-hash','changed-root','changed-revision'])
def test_incomplete_or_stale_approval_cannot_pass(approved, failure):
    app, identifier, author, _, _ = approved
    with app.state.db.transaction(write=True) as session:
        root = session.get(ProtocolProposal, identifier)
        revision = session.scalar(select(ProtocolProposalRevision))
        review = session.scalar(select(ProtocolProposalReview))
        if failure == 'not-approved': root.status = 'submitted'
        elif failure == 'self-review': review.reviewer_id = author
        elif failure == 'incomplete-check': review.checks = {key:key != 'correctness' for key in CHECKS}
        elif failure == 'no-evidence': review.evidence = ''
        elif failure == 'wrong-reviewed-hash': review.implementation_sha256 = '0'*64
        elif failure == 'changed-root': root.specification = {**root.specification, 'name':'Changed'}
        elif failure == 'changed-revision': revision.content_sha256 = '0'*64
    with app.state.db.transaction() as session, pytest.raises(ValueError):
        check_admission(session, identifier, 'teaching-dh-kem', '4')


@pytest.mark.parametrize('field', ['version','tiers','secret-length','unknown-correctness'])
def test_reviewed_document_must_match_registered_adapter_metadata(approved, field):
    app, identifier, _, _, _ = approved
    with app.state.db.transaction(write=True) as session:
        root = session.get(ProtocolProposal, identifier)
        spec = deepcopy(root.specification)
        if field == 'version': spec['version'] = '99'
        elif field == 'tiers': spec['parameters']['supported_tiers']['min'] = 21
        elif field == 'secret-length': spec['encoding']['shared_secret_bytes'] = 64
        else: spec['correctness'].update(kind='unknown', failure_bound=None)
        root.specification, root.content_sha256 = spec, digest(spec)
        revision = session.scalar(select(ProtocolProposalRevision))
        revision.specification, revision.content_sha256 = spec, digest(spec)
        session.scalar(select(ProtocolProposalReview)).content_sha256 = digest(spec)
    with app.state.db.transaction() as session, pytest.raises(ValueError):
        check_admission(session, identifier, 'teaching-dh-kem', '4')


def test_registered_source_fingerprint_cannot_change_after_review(approved, monkeypatch):
    app, identifier, _, _, _ = approved
    monkeypatch.setattr(registry.ADAPTERS[('teaching-dh-kem','4')], 'IMPLEMENTATION_SHA256', '0'*64)
    with app.state.db.transaction() as session, pytest.raises(ValueError, match='exact reviewed implementation'):
        check_admission(session, identifier, 'teaching-dh-kem', '4')


def set_reviewed_variable_output(app, identifier):
    """Keep all approval hashes consistent while exercising metadata checks."""
    with app.state.db.transaction(write=True) as session:
        record = session.get(ProtocolProposal, identifier)
        spec = deepcopy(record.specification)
        spec['encoding']['shared_secret_bytes'] = None
        spec['encoding']['shared_secret'] = 'Output length is explicitly declared by each registered profile.'
        record.specification, record.content_sha256 = spec, digest(spec)
        revision = session.scalar(select(ProtocolProposalRevision))
        revision.specification, revision.content_sha256 = spec, digest(spec)
        session.scalar(select(ProtocolProposalReview)).content_sha256 = digest(spec)


@pytest.mark.parametrize('variable', [False, True])
@pytest.mark.parametrize('length', ['missing', None, True, 32.0, '32', 0, -1, 4097])
def test_every_profile_requires_a_strict_bounded_secret_length(approved, monkeypatch, variable, length):
    app, identifier, _, _, _ = approved
    if variable:
        set_reviewed_variable_output(app, identifier)
    adapter = registry.ADAPTERS[('teaching-dh-kem', '4')]
    profiles = deepcopy(adapter.manifests())
    if length == 'missing':
        profiles[-1].pop('shared_secret_bytes')
    else:
        profiles[-1]['shared_secret_bytes'] = length
    monkeypatch.setattr(adapter, 'manifests', lambda: profiles)
    with app.state.db.transaction() as session, pytest.raises(ValueError, match='Every profile must declare'):
        check_admission(session, identifier, 'teaching-dh-kem', '4')


def test_variable_output_spec_can_bind_distinct_valid_per_profile_lengths(approved, monkeypatch):
    app, identifier, _, _, _ = approved
    set_reviewed_variable_output(app, identifier)
    adapter = registry.ADAPTERS[('teaching-dh-kem', '4')]
    profiles = deepcopy(adapter.manifests())
    profiles[0]['shared_secret_bytes'], profiles[-1]['shared_secret_bytes'] = 1, 4096
    monkeypatch.setattr(adapter, 'manifests', lambda: profiles)
    with app.state.db.transaction() as session:
        report = check_admission(session, identifier, 'teaching-dh-kem', '4')
    assert report['status'] == 'binding-check-passed'
    assert 'No execution, security certification, or activation' in report['scope']


@pytest.mark.parametrize('failure', ['duplicate', 'mismatch', 'missing-id', 'missing-profile-id',
                                   'empty', 'blank', 'padding', 'too-long', 'wrong-type'])
def test_profile_identities_are_unique_nonblank_and_unambiguous(approved, monkeypatch, failure):
    app, identifier, _, _, _ = approved
    adapter = registry.ADAPTERS[('teaching-dh-kem', '4')]
    profiles = deepcopy(adapter.manifests())
    if failure == 'duplicate':
        profiles[-1]['id'] = profiles[-1]['profile_id'] = profiles[0]['id']
    elif failure == 'mismatch':
        profiles[-1]['profile_id'] = 'different-id'
    elif failure == 'missing-id':
        profiles[-1].pop('id')
    elif failure == 'missing-profile-id':
        profiles[-1].pop('profile_id')
    else:
        value = {'empty': '', 'blank': '  ', 'padding': ' padded ',
                 'too-long': 'a' * 101, 'wrong-type': ['id']}[failure]
        profiles[-1]['id'] = profiles[-1]['profile_id'] = value
    monkeypatch.setattr(adapter, 'manifests', lambda: profiles)
    with app.state.db.transaction() as session, pytest.raises(ValueError, match='unique nonblank matching'):
        check_admission(session, identifier, 'teaching-dh-kem', '4')


def test_local_reviewer_assignment_resolves_existing_account_id_and_preserves_others(approved, monkeypatch, tmp_path):
    _, _, author, reviewer, settings = approved
    monkeypatch.delenv('CRUCIBLE_REVIEWER_IDS', raising=False)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    settings = replace(settings, reviewer_ids=(author,))
    assert configure_reviewer(settings, 'Reviewer', grant=True) == 'reviewer'
    target = tmp_path / 'reviewers.json'
    assert set(json.loads(target.read_text())['reviewer_ids']) == {author,reviewer}
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='existing account'):
        configure_reviewer(settings, 'unregistered', grant=True)
    assert set(json.loads(target.read_text())['reviewer_ids']) == {author,reviewer}
    configure_reviewer(replace(settings, reviewer_ids=(author,reviewer)), 'reviewer', grant=False)
    assert json.loads(target.read_text())['reviewer_ids'] == [author]


def test_local_grant_cannot_override_production_or_environment_policy(approved, monkeypatch):
    _, _, _, _, settings = approved
    with pytest.raises(ValueError, match='environment-managed'):
        configure_reviewer(replace(settings, production=True), 'reviewer', grant=True)
    monkeypatch.setenv('CRUCIBLE_REVIEWER_IDS', '')
    with pytest.raises(ValueError, match='environment-managed'):
        configure_reviewer(settings, 'reviewer', grant=True)


def test_reviewer_commands_reload_file_instead_of_restoring_stale_permissions(approved, monkeypatch, tmp_path):
    _, _, author, reviewer, settings = approved
    monkeypatch.delenv('CRUCIBLE_REVIEWER_IDS', raising=False)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    stale = replace(settings, reviewer_ids=(author,))
    configure_reviewer(stale, 'author', grant=False)
    configure_reviewer(stale, 'reviewer', grant=True)
    assert json.loads((tmp_path / 'reviewers.json').read_text()) == {'reviewer_ids': [reviewer]}


@pytest.mark.parametrize('contents', ['not json', '{"reviewer_ids":["not-an-account-id"]}',
                                     '{"reviewer_ids":[] , "extra":true}', '{"reviewer_ids":[42]}'])
def test_reviewer_command_does_not_overwrite_malformed_latest_policy(approved, monkeypatch, tmp_path, contents):
    _, _, _, _, settings = approved
    monkeypatch.delenv('CRUCIBLE_REVIEWER_IDS', raising=False)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    target = tmp_path / 'reviewers.json'
    target.write_text(contents)
    with pytest.raises(ValueError, match='no changes were made'):
        configure_reviewer(settings, 'reviewer', grant=True)
    assert target.read_text() == contents
    assert not list(tmp_path.glob('.reviewers-*'))


def test_concurrent_revoke_and_grant_lock_the_entire_policy_update(approved, monkeypatch, tmp_path):
    _, _, author, reviewer, settings = approved
    monkeypatch.delenv('CRUCIBLE_REVIEWER_IDS', raising=False)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    stale = replace(settings, reviewer_ids=(author,))
    target = tmp_path / 'reviewers.json'
    target.write_text(json.dumps({'reviewer_ids': [author]}))
    replacing, allow_replace, second_lock = Event(), Event(), Event()
    actual_replace, actual_flock, actual_read = os.replace, fcntl.flock, manage._latest_reviewers
    reads = []

    def controlled_replace(source, destination):
        if Path(destination) == target and not replacing.is_set():
            replacing.set()
            assert allow_replace.wait(5), 'The test must release the first update.'
        return actual_replace(source, destination)

    def observed_lock(descriptor, operation):
        if operation == fcntl.LOCK_EX and replacing.is_set():
            second_lock.set()
        return actual_flock(descriptor, operation)

    def observed_read(*args):
        reads.append(True)
        return actual_read(*args)

    monkeypatch.setattr(manage.os, 'replace', controlled_replace)
    monkeypatch.setattr(manage.fcntl, 'flock', observed_lock)
    monkeypatch.setattr(manage, '_latest_reviewers', observed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(configure_reviewer, stale, 'author', grant=False)
        try:
            assert replacing.wait(5)
            second = pool.submit(configure_reviewer, stale, 'reviewer', grant=True)
            assert second_lock.wait(5)
            # The second process must not read the old file while the first
            # owns the lock but has not yet atomically replaced it.
            assert len(reads) == 1
        finally:
            allow_replace.set()
        assert first.result(timeout=5) == 'author'
        assert second.result(timeout=5) == 'reviewer'
    assert len(reads) == 2
    assert json.loads(target.read_text()) == {'reviewer_ids': [reviewer]}
    assert target.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / '.reviewers.lock').stat().st_mode & 0o777 == 0o600

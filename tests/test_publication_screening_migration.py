"""Schema0031 adds screening provenance without rewriting research history."""
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import MetaData, inspect, text

from crucible.db import Database
from crucible import notebook, publication_screening_jobs as jobs
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


SCREENING_TABLES = {'publication_screening_jobs', 'publication_screening_attempts',
    'publication_screening_budgets', 'publication_screening_control', 'notebook_automatic_publications'}


def seed_publications(connection, metadata, original):
    """Legacy rows retain their exact human/no-human attribution and decisions."""
    rows = {}
    for label, decision, self_review, reviewed in (
        ('approved', 'approved', False, True),
        ('changes', 'changes_requested', False, True),
        ('self_review', 'approved', True, True),
        ('pending', None, False, False),
        ('unattributed', 'approved', False, False),
    ):
        note_id, revision_id, publication_id = (str(uuid4()) for _ in range(3))
        connection.execute(metadata.tables['notebook_notes'].insert().values(
            id=note_id, author_id=original['user'], created_at=10, latest_revision=1,
            idempotency_key=str(uuid4()), request_sha256='a' * 64))
        connection.execute(metadata.tables['notebook_revisions'].insert().values(
            id=revision_id, note_id=note_id, revision=1, created_at=10,
            content={'title': label, 'body': 'Preserve exact original α\ncontent.'},
            content_sha256='b' * 64, idempotency_key=str(uuid4()), request_sha256='c' * 64))
        connection.execute(metadata.tables['notebook_publications'].insert().values(
            id=publication_id, revision_id=revision_id, author_id=original['user'],
            requested_at=11, idempotency_key=str(uuid4()), request_sha256='d' * 64,
            decision=decision, reviewer_id=original['user'] if reviewed else None,
            reviewed_at=12 if reviewed else None, reason='Original human explanation' if reviewed else None,
            review_key=str(uuid4()) if reviewed else None, review_sha256='e' * 64 if reviewed else None,
            test_only_self_review=self_review))
        rows[label] = publication_id
    return rows


def test_upgrade_preserves_all_original_values_and_backfills_only_human_provenance(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'screening-migration.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0030')
    db = Database(url)
    try:
        metadata = MetaData()
        metadata.reflect(bind=db.engine)
        metadata.remove(metadata.tables['alembic_version'])
        names = set(metadata.tables)
        with db.engine.begin() as connection:
            original, _ = seed_original(connection, metadata)
            publications = seed_publications(connection, metadata, original)
        with db.engine.connect() as connection:
            before = snapshot(connection, metadata)
        command.upgrade(config, '0031')
        assert set(inspect(db.engine).get_table_names()) - {'alembic_version'} == names | SCREENING_TABLES
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            rows = dict(connection.execute(text('SELECT id, decision_source FROM notebook_publications')).all())
            assert rows == {identifier: 'human' if label in ('approved', 'changes', 'self_review') else None
                            for label, identifier in publications.items()}
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0031'
            assert connection.scalar(text('SELECT count(*) FROM publication_screening_jobs')) == 0
            assert connection.scalar(text('SELECT count(*) FROM publication_screening_attempts')) == 0
            assert connection.scalar(text('SELECT count(*) FROM notebook_automatic_publications')) == 0
        command.check(config)
        # With no automated work or receipts, rollback preserves all old data.
        command.downgrade(config, '0030')
        assert not SCREENING_TABLES & set(inspect(db.engine).get_table_names())
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0030'
    finally:
        db.engine.dispose()


def test_pending_screening_audit_prevents_destructive_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'durable-screening.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0030')
    db = Database(url)
    try:
        metadata = MetaData()
        metadata.reflect(bind=db.engine)
        with db.engine.begin() as connection:
            original, _ = seed_original(connection, metadata)
            publications = seed_publications(connection, metadata, original)
        command.upgrade(config, '0031')
        policy = jobs.ScreeningPolicy(policy_version='synthetic-v1', prompt_sha256='f' * 64,
            input_format_version='synthetic-v1', model='gpt-6-luna', max_input_tokens=1000,
            max_output_tokens=100, reserve_micro_usd=100)
        with db.transaction(write=True) as session:
            pending = session.get(notebook.NotebookPublication, publications['pending'])
            job = jobs.enqueue(session, pending, policy=policy, now=20)
            job_id = job.id
        metadata = MetaData()
        metadata.reflect(bind=db.engine)
        with db.engine.connect() as connection:
            before = snapshot(connection, metadata)
        with pytest.raises(RuntimeError):
            command.downgrade(config, '0030')
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0031'
        db.engine.dispose()
        # Reopening the database retains durable queued work and its exact pins.
        reopened = Database(url)
        try:
            with reopened.engine.connect() as connection:
                assert snapshot(connection, metadata) == before
                assert connection.execute(text('SELECT publication_id, content_sha256 FROM publication_screening_jobs WHERE id=:id'),
                    {'id': job_id}).one() == (publications['pending'], 'b' * 64)
        finally:
            reopened.engine.dispose()
    finally:
        db.engine.dispose()

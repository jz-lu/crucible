"""Publication races and exact evidence recovery on the fixed disposable PG17.

Uses only generated fixture roles and invented, explicitly test-only receipts.
The shared fixture rejects ambient endpoints and removes its own schema/roles.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Barrier, Lock

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import event, inspect, select, text

from crucible import resource_publications as rp
from crucible.db import Database
from test_notebook_numbers_postgresql import pg_tool, preserve_service_identifier
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged
from test_resource_publications import archive, fixture_files, IDENTITY, NOW


@pytest.fixture
def publications(preserve_service_identifier, postgres):
    postgres.service_container = preserve_service_identifier
    command.upgrade(postgres.config, 'head')
    command.check(postgres.config)
    db = Database(postgres.runtime_url)
    try:
        yield postgres, db
    finally:
        db.engine.dispose()


@pytest.mark.parametrize('conflict', [False, True])
def test_competing_uploads_retain_one_exact_receipt(publications, conflict):
    fixture, db = publications
    first = archive(*fixture_files())
    files, publication = fixture_files()
    files['resources.json']['fixture_variant'] = 'conflicting invented measurement'
    second = archive(files, publication) if conflict else first
    barrier, lock = Barrier(2, timeout=5), Lock()
    reads = 0

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        nonlocal reads
        if statement.startswith('SELECT ') and 'WHERE resource_publications.repository_id' in statement:
            with lock:
                first_read = reads < 2
                reads += 1
            if first_read:
                barrier.wait()

    def upload(raw):
        try:
            return 200, rp.ingest(db, raw, IDENTITY, NOW)
        except HTTPException as error:
            return error.status_code, None

    event.listen(db.engine, 'after_cursor_execute', synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.submit(upload, first), pool.submit(upload, second)
            results = [left.result(timeout=15), right.result(timeout=15)]
    finally:
        event.remove(db.engine, 'after_cursor_execute', synchronize)
    assert sorted(x[0] for x in results) == ([200, 409] if conflict else [200, 200])
    if not conflict:
        assert results[0] == results[1]
    with db.transaction() as session:
        rows = session.scalars(select(rp.ResourcePublication)).all()
        assert len(rows) == 1 and 'bundle' in inspect(rows[0]).unloaded
        assert rows[0].bundle in (first, second)
        assert hashlib.sha256(rows[0].bundle).hexdigest() == rows[0].id
    assert_original_unchanged(fixture)


def test_backup_restore_retains_exact_bundle_and_current_projection(publications):
    fixture, db = publications
    raw = archive(*fixture_files())
    receipt = rp.ingest(db, raw, IDENTITY, NOW)
    with fixture.backup_engine.connect() as connection:
        assert connection.scalar(text('SELECT bundle FROM resource_publications')) == raw
        for privilege in ('INSERT', 'UPDATE', 'DELETE'):
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, 'resource_publications', :p)"), {'p': privilege})
    with pytest.raises(RuntimeError, match='Preserve resource evidence'):
        command.downgrade(fixture.config, '0025')
    dumped = pg_tool(fixture.service_container, 'pg_dump', fixture.backup,
        '--format=custom', '--no-owner', '--no-privileges',
        '--table=' + fixture.schema + '.resource_publications', 'crucible_metadata_test')
    assert dumped.returncode == 0, dumped.stderr.decode()
    assert dumped.stdout.startswith(b'PGDMP') and len(dumped.stdout) < 4 * 1024 * 1024
    with fixture.owner_engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE resource_publications')
    restored = pg_tool(fixture.service_container, 'pg_restore', fixture.owner,
        '--no-owner', '--no-privileges', '--single-transaction', '--exit-on-error',
        '--dbname=crucible_metadata_test', payload=dumped.stdout)
    assert restored.returncode == 0, restored.stderr.decode()
    with db.transaction() as session:
        row = rp.latest(session, 'dhkem-x25519', '1')
        assert row.id == receipt['publication_id'] and 'bundle' in inspect(row).unloaded
        assert row.bundle == raw and row.publication['source'] == IDENTITY
        assert row.resources['configurations'][0]['sizes_bytes']['public_key'] == 32
    assert rp.ingest(db, raw, IDENTITY, NOW + 1) == receipt
    assert_original_unchanged(fixture)
    command.check(fixture.config)

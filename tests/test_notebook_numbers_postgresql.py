"""Identity allocation and real dump/restore on the fixed disposable PG17 service.

No ambient DSN, credentials or production roles are used. The existing profile
fixture provisions unique synthetic owner/runtime/backup roles and an isolated
schema. CI exposes only its own pinned service container for PG17 client tools.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import re
import subprocess
from threading import Barrier, Lock

from alembic import command
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import DBAPIError

from crucible import notebook_numbers
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged


@pytest.fixture
def numbers(postgres, preserve_service_identifier):
    postgres.service_container = preserve_service_identifier
    command.upgrade(postgres.config, '0025')
    assert_original_unchanged(postgres)
    with postgres.owner_engine.connect() as connection:
        sequence = connection.scalar(text("SELECT pg_get_serial_sequence('notebook_numbers', 'number')"))
        assert sequence == postgres.schema + '.notebook_numbers_number_seq'
        assert inspect(connection).get_sequence_names() == ['notebook_numbers_number_seq']
        assert connection.scalar(text("SELECT identity_generation FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='notebook_numbers' AND column_name='number'")) == 'ALWAYS'
    postgres.sequence = sequence
    yield postgres


def aliases(connection):
    return dict(connection.execute(select(notebook_numbers.NotebookNumber.note_id,
        notebook_numbers.NotebookNumber.number)).all())


def test_runtime_identity_allocation_needs_no_sequence_grant_and_backup_needs_select(numbers):
    with numbers.runtime_engine.begin() as connection:
        for privilege in ('SELECT', 'USAGE', 'UPDATE'):
            assert not connection.scalar(text('SELECT has_sequence_privilege(current_user, :sequence, :privilege)'),
                {'sequence': numbers.sequence, 'privilege': privilege})
        before = aliases(connection)
        assert before == {numbers.ids['note']: 1}  # Backfilled, including Trash.
        allocated = notebook_numbers.ensure(connection, ['curated:' + 'x' * 100, 'synthetic-saved-note'])
        assert set(allocated.values()) == {2, 3}
        assert notebook_numbers.ensure(connection, allocated) == allocated
    with pytest.raises(DBAPIError) as explicit:
        with numbers.runtime_engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO notebook_numbers(number,note_id) VALUES(900,'forbidden-explicit')")
    assert explicit.value.orig.sqlstate == '428C9'
    with pytest.raises(DBAPIError) as denied:
        with numbers.backup_engine.begin() as connection:
            connection.exec_driver_sql(f'SELECT last_value, is_called FROM {numbers.sequence}')
    assert denied.value.orig.sqlstate == '42501'
    with numbers.owner_engine.begin() as connection:
        connection.exec_driver_sql(f'GRANT SELECT ON SEQUENCE {numbers.sequence} TO {numbers.backup}')
    with numbers.backup_engine.connect() as connection:
        assert connection.exec_driver_sql(f'SELECT last_value, is_called FROM {numbers.sequence}').one() == (3, True)
        assert not connection.scalar(text("SELECT has_sequence_privilege(current_user, :sequence, 'USAGE')"), {'sequence': numbers.sequence})
        assert not connection.scalar(text("SELECT has_sequence_privilege(current_user, :sequence, 'UPDATE')"), {'sequence': numbers.sequence})
        assert len(aliases(connection)) == 3
    assert_original_unchanged(numbers)
    command.check(numbers.config)


def test_competing_runtime_writers_reserve_one_number_per_identity(numbers):
    barrier, lock, first_reads = Barrier(2, timeout=5), Lock(), []
    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('SELECT ') and 'FROM notebook_numbers' in statement:
            with lock:
                first = all(connection is not prior for prior in first_reads)
                if first:
                    first_reads.append(connection)
            if first:
                barrier.wait()
    def allocate(identifiers):
        with numbers.runtime_engine.begin() as connection:
            assert connection.get_isolation_level() == 'READ COMMITTED'
            return notebook_numbers.ensure(connection, identifiers)
    event.listen(numbers.runtime_engine, 'after_cursor_execute', synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(allocate, ['curated:shared', 'saved:left'])
            right = pool.submit(allocate, ['curated:shared', 'saved:right'])
            a, b = left.result(timeout=15), right.result(timeout=15)
    finally:
        event.remove(numbers.runtime_engine, 'after_cursor_execute', synchronize)
    assert len(first_reads) == 2 and a['curated:shared'] == b['curated:shared']
    assert len({a['curated:shared'], a['saved:left'], b['saved:right']}) == 3
    with numbers.runtime_engine.connect() as connection:
        assert aliases(connection) == {numbers.ids['note']: 1, **a, **b}
    assert_original_unchanged(numbers)


def pg_tool(container, tool, role, *arguments, payload=None):
    assert tool in {'pg_dump', 'pg_restore'}
    # This is the current workflow's explicit service ID, never a hostname or
    # arbitrary command from caller input. Commands run only inside that service.
    assert re.fullmatch(r'[0-9a-f]{64}', container)
    assert re.fullmatch(r'profile_(owner|backup)_[0-9a-f]{32}', role)
    environment = {'PATH': os.defpath, 'LC_ALL': 'C'}
    commandline = ['docker', 'exec', '-i', '-e', 'PGPASSWORD=profile-synthetic-only', container, tool,
        '--host=127.0.0.1', '--port=5432', '--username=' + role, *arguments]
    return subprocess.run(commandline, input=payload, capture_output=True, timeout=45, env=environment)


def test_pg17_backup_restore_preserves_identity_state_and_next_number(numbers):
    container = numbers.service_container
    assert re.fullmatch(r'[0-9a-f]{64}', container), 'PG17 gate requires its fixed service container for real dump/restore'
    with numbers.runtime_engine.begin() as connection:
        notebook_numbers.ensure(connection, ['curated:kept'])
    with numbers.runtime_engine.connect() as connection:
        transaction = connection.begin()
        abandoned = notebook_numbers.ensure(connection, ['curated:rolled-back'])['curated:rolled-back']
        transaction.rollback()
    with numbers.owner_engine.connect() as connection:
        before = aliases(connection)
        state = connection.exec_driver_sql(f'SELECT last_value, is_called FROM {numbers.sequence}').one()
    assert state == (abandoned, True) and abandoned > max(before.values())
    arguments = ['--format=custom', '--no-owner', '--no-privileges', '--table=' + numbers.schema + '.notebook_numbers',
                 '--table=' + numbers.sequence, 'crucible_metadata_test']
    denied = pg_tool(container, 'pg_dump', numbers.backup, *arguments)
    assert denied.returncode != 0 and b'permission denied for sequence' in denied.stderr
    with numbers.owner_engine.begin() as connection:
        connection.exec_driver_sql(f'GRANT SELECT ON SEQUENCE {numbers.sequence} TO {numbers.backup}')
    dumped = pg_tool(container, 'pg_dump', numbers.backup, *arguments)
    assert dumped.returncode == 0, dumped.stderr.decode()
    assert dumped.stdout.startswith(b'PGDMP') and len(dumped.stdout) < 4 * 1024 * 1024
    with numbers.owner_engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE notebook_numbers')
    restored = pg_tool(container, 'pg_restore', numbers.owner, '--no-owner', '--no-privileges',
        '--single-transaction', '--exit-on-error', '--dbname=crucible_metadata_test', payload=dumped.stdout)
    assert restored.returncode == 0, restored.stderr.decode()
    with numbers.owner_engine.connect() as connection:
        assert aliases(connection) == before
        assert connection.exec_driver_sql(f'SELECT last_value, is_called FROM {numbers.sequence}').one() == state
        assert inspect(connection).get_sequence_names() == ['notebook_numbers_number_seq']
    with numbers.runtime_engine.begin() as connection:
        new = notebook_numbers.ensure(connection, ['curated:after-restoration'])
        assert new['curated:after-restoration'] == abandoned + 1
        assert all(aliases(connection)[key] == value for key, value in before.items())
    assert_original_unchanged(numbers)


@pytest.fixture(autouse=True)
def preserve_service_identifier():
    # Capture before the existing PG fixture scrubs ambient config, without
    # preserving credentials or permitting an alternative connection endpoint.
    return os.environ.get('CRUCIBLE_NOTEBOOK_PG_CONTAINER', '')

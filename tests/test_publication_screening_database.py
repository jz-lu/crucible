"""Worker-only database bounds, without a network connection or provider call."""
from sqlalchemy import text
from sqlalchemy.engine import URL
import pytest

from crucible.db import Database
from crucible import publication_screening_worker as worker


def capture_connection(db):
    observed = []
    class Captured(Exception): pass
    def connect(*args, **kwargs):
        observed.append(dict(kwargs))
        raise Captured()
    db.engine.dialect.connect = connect
    try:
        for _ in range(2):
            with pytest.raises(Captured):
                db.engine.connect()
    finally:
        db.engine.dispose()
    assert observed[0] == observed[1]  # Reconnects never accumulate startup options.
    return observed[0]


def test_worker_connection_enforces_bounds_preserves_tls_and_overrides_weaker_url_options():
    url = URL.create('postgresql+psycopg', username='synthetic', password='synthetic-secret',
        host='invalid.example', database='synthetic', query={
            'sslmode': 'verify-full', 'sslrootcert': '/synthetic/ca.crt', 'connect_timeout': '0',
            'options': '-c application_name=synthetic_worker -c statement_timeout=0 -c lock_timeout=0'})
    observed = capture_connection(worker.worker_database(url.render_as_string(hide_password=False)))
    assert observed['connect_timeout'] == 5
    assert observed['sslmode'] == 'verify-full'
    assert observed['sslrootcert'] == '/synthetic/ca.crt'
    assert observed['options'] == ('-c application_name=synthetic_worker -c statement_timeout=0 -c lock_timeout=0 '
        '-c statement_timeout=10000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000')
    assert worker.DATABASE_LOCK_TIMEOUT_MS < worker.DATABASE_STATEMENT_TIMEOUT_MS
    assert worker.PROVIDER_TIMEOUT_SECONDS + worker.DATABASE_STATEMENT_TIMEOUT_MS / 1000 < worker.LEASE_SECONDS


def test_worker_bounds_do_not_change_general_http_database_configuration():
    url = 'postgresql+psycopg://synthetic:synthetic-secret@invalid.example/synthetic?sslmode=verify-full'
    worker_parameters = capture_connection(worker.worker_database(url))
    ordinary_parameters = capture_connection(Database(url))
    assert worker_parameters['connect_timeout'] == 5
    assert 'connect_timeout' not in ordinary_parameters and 'options' not in ordinary_parameters


def test_worker_sqlite_preserves_existing_transaction_and_foreign_key_behavior(tmp_path):
    db = worker.worker_database('sqlite:///' + str(tmp_path / 'worker.db'))
    try:
        with db.transaction() as session:
            assert session.scalar(text('PRAGMA foreign_keys')) == 1
            assert session.scalar(text('PRAGMA busy_timeout')) == 15000
        with db.transaction(write=True) as session:
            session.execute(text('CREATE TABLE synthetic_work (id integer PRIMARY KEY)'))
            session.execute(text('INSERT INTO synthetic_work VALUES (1)'))
        with pytest.raises(RuntimeError):
            with db.transaction(write=True) as session:
                session.execute(text('INSERT INTO synthetic_work VALUES (2)'))
                raise RuntimeError('synthetic rollback')
        with db.transaction() as session:
            assert session.scalar(text('SELECT count(*) FROM synthetic_work')) == 1
    finally:
        db.engine.dispose()

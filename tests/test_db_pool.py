"""Exercise the real SQLAlchemy pool with disposable SQLite connections.

Only the PostgreSQL driver/URL is substituted. Database's production options
reach an actual engine, connection pool and transactions, without consulting
DATABASE_URL or opening any network/database service. This is not a test of
PostgreSQL's dialect, TLS or provider limits.
"""
from contextlib import ExitStack
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, TimeoutError

from crucible import db as database


@pytest.fixture
def bounded_database(tmp_path, monkeypatch):
    engines = []
    connections = []

    def local_driver(url, **options):
        assert url == "postgresql+psycopg://synthetic.invalid/unused"
        engine = sa.create_engine("sqlite:///" + str(tmp_path / "pool.db"), **options)
        engines.append(engine)

        @sa.event.listens_for(engine, "connect")
        def track_connection(connection, record):
            connections.append(connection)

        return engine

    monkeypatch.setattr(database, "create_engine", local_driver)
    db = database.Database("postgresql+psycopg://synthetic.invalid/unused")
    try:
        yield db, connections
    finally:
        for engine in engines:
            engine.dispose()


def test_five_connections_exhaust_pool_then_release_restores_service(bounded_database):
    db, connections = bounded_database
    with ExitStack() as leases:
        held = [leases.enter_context(db.engine.connect()) for _ in range(5)]
        assert all(connection.exec_driver_sql("SELECT 1").scalar_one() == 1 for connection in held)
        assert len(connections) == 5

        started = monotonic()
        with pytest.raises(TimeoutError):
            with db.transaction() as session:
                session.execute(sa.text("SELECT 1"))
        # Exercise the deployed timeout rather than shortening a private pool
        # attribute. No upper wall-time assertion that flakes on a busy runner.
        assert monotonic() - started >= 4.5
        assert len(connections) == 5  # No overflow connection was opened.

        held.pop().close()
        with db.transaction() as session:
            assert session.scalar(sa.text("SELECT 42")) == 42
        assert len(connections) == 5  # Returned connection is reusable.


def test_failed_transactions_return_capacity_and_roll_back(bounded_database):
    db, connections = bounded_database
    with db.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE evidence (value INTEGER NOT NULL)")

    for _ in range(10):
        with pytest.raises(ValueError, match="synthetic operation failed"):
            with db.transaction(write=True) as session:
                session.execute(sa.text("INSERT INTO evidence VALUES (1)"))
                raise ValueError("synthetic operation failed")
    with db.transaction() as session:
        assert session.scalar(sa.text("SELECT COUNT(*) FROM evidence")) == 0
    assert len(connections) == 1


def test_sqlite_retains_its_own_pool_and_transaction_rules(tmp_path):
    db = database.Database("sqlite:///" + str(tmp_path / "sqlite.db"))
    try:
        with ExitStack() as leases:
            # PostgreSQL's five-connection cap must not change SQLite defaults.
            held = [leases.enter_context(db.engine.connect()) for _ in range(6)]
            for connection in held:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 15000
        with db.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        with pytest.raises(IntegrityError):
            with db.transaction(write=True) as session:
                session.execute(sa.text("INSERT INTO parent VALUES (1)"))
                session.execute(sa.text("INSERT INTO child VALUES (2)"))
        with db.transaction() as session:
            assert session.scalar(sa.text("SELECT COUNT(*) FROM parent")) == 0
        with db.transaction(write=True) as session:
            session.execute(sa.text("INSERT INTO parent VALUES (1)"))
            session.execute(sa.text("INSERT INTO child VALUES (1)"))
        with db.transaction() as session:
            assert session.scalar(sa.text("SELECT COUNT(*) FROM child")) == 1
    finally:
        db.engine.dispose()

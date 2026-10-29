"""Actual SQLite/PostgreSQL parity for trusted metadata, without native workers.

PostgreSQL is opt-in only for the fixed disposable CI service below. No caller
DSN, application settings, environment credentials or participant data are used.
"""
import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Boolean, Integer, String, column, create_engine, select, text, values
from sqlalchemy.orm import Session

from crucible import app as application  # Register all foreign-key targets only.
from crucible import milestones, scoring
from crucible.db import Base, Challenge, Method, Submission, User

POSTGRES_URL = 'postgresql+psycopg://crucible_metadata:synthetic-only@127.0.0.1:5433/crucible_metadata_test'


@pytest.fixture(params=['sqlite', 'postgresql'])
def connection(request):
    dialect = request.param
    if dialect == 'postgresql' and os.environ.get('CRUCIBLE_METADATA_POSTGRES') != '1':
        pytest.skip('Fixed disposable PostgreSQL service is not enabled')
    if dialect == 'postgresql':
        # Remove libpq overrides before the exact loopback-only connection.
        with pytest.MonkeyPatch.context() as patch:
            for name in tuple(os.environ):
                if name.startswith('PG'):
                    patch.delenv(name)
            engine = create_engine(POSTGRES_URL, connect_args={'connect_timeout': 5,
                'options': '-c statement_timeout=10000 -c lock_timeout=2000'})
            with engine.connect() as conn:
                assert conn.execute(text("SELECT current_database(), current_user, current_setting('server_version_num')::integer / 10000")).one() == (
                    'crucible_metadata_test', 'crucible_metadata', 17)
                conn.rollback()
                transaction = conn.begin()
                schema = 'metadata_' + uuid4().hex
                try:
                    conn.execute(text('CREATE SCHEMA ' + schema))
                    conn.execute(text('SET LOCAL search_path TO ' + schema))
                    yield conn
                finally:
                    transaction.rollback()  # Removes only this test's new schema.
            engine.dispose()
    else:
        engine = create_engine('sqlite://')
        with engine.connect() as conn, conn.begin():
            yield conn
        engine.dispose()


def old_values(name, columns, rows, dialect):
    """The original relation, retained only as an independent parity oracle."""
    # Compiling VALUES binds its ColumnClause objects to that relation. Keep
    # them independent of the PostgreSQL table-valued relation being checked.
    return values(*(column(field.name, field.type) for field in columns)).data(rows).cte(name)


def normalized(rows):
    return sorted((tuple(row) for row in rows), key=lambda row: json.dumps(row, sort_keys=True))


def test_metadata_values_preserve_types_null_unicode_and_bound_strings(connection):
    fields = (column('label', String), column('ordinal', Integer), column('inherits', Boolean))
    rows = (("McEliece's α \\ quoted \"label\"", 1, True), ('not SQL: ); DROP TABLE users;', 2, False), (None, -3, None))
    actual = scoring.metadata_relation('typed_metadata', fields, rows, connection.dialect.name)
    baseline = old_values('typed_metadata', fields, rows, connection.dialect.name)
    assert normalized(connection.execute(select(actual))) == normalized(connection.execute(select(baseline))) == normalized(rows)
    assert select(actual)._generate_cache_key() is not None
    empty = scoring.metadata_relation('empty_metadata', fields, (), connection.dialect.name)
    assert connection.execute(select(empty)).all() == []


def test_metadata_bindings_refresh_without_reusing_stale_rows(connection):
    fields = (column('id', String), column('tier', Integer), column('inherits', Boolean))
    for rows in ((('first', 1, True),), (('changed', 2, False),), ()):
        query = select(scoring.metadata_relation('dynamic_static_metadata', fields, rows, connection.dialect.name))
        assert normalized(connection.execute(query)) == normalized(rows)


def test_large_profile_relation_has_bounded_sql_shape(connection):
    fields = (column('scheme', String), column('profile', String), column('tier', Integer))
    rows = tuple(('scheme', f'profile-{n}', n) for n in range(1466))
    query = select(scoring.metadata_relation('large_metadata', fields, rows, connection.dialect.name))
    compiled = query.compile(dialect=connection.dialect)
    assert len(str(compiled)) < 1500 and len(compiled.params) <= 4
    assert query._generate_cache_key() is not None
    assert normalized(connection.execute(query)) == normalized(rows)


def seed(session, monkeypatch):
    owner, second = str(UUID(int=1)), str(UUID(int=2))
    for identifier, handle in ((owner, 'synthetic_one'), (second, 'synthetic_two')):
        session.add(User(id=identifier, handle=handle, password_hash=None, created_at=1.0))
    session.flush()
    profiles = (
        ('teaching-dh-kem', '7', 'dh-v7-tier-1', 1, 'research-tier', 'First', True),
        ('teaching-dh-kem', '7', 'dh-v7-tier-2', 2, 'research-tier', 'Second', True),
        ('hqc', 'modeled-research-v3', 'hqc-modeled-research-v3-tier-1', 1, 'research-tier', 'HQC first', False),
        ('hqc', 'modeled-research-v3', 'hqc-modeled-research-v3-tier-2', 2, 'research-tier', 'HQC second', False),
        ('fixture', 'published', 'fixture-standard', 3, 'standard-parameter-set', 'Named parameter set', False),
        ('teaching-dh-kem', '7', 'dh-v7-tier-3', 3, 'research-tier', 'Third', True),
    )
    monkeypatch.setattr(milestones, '_profile_rows', lambda identity, include_standard: tuple(
        row for row in profiles if include_standard or row[4] != 'standard-parameter-set'))
    def add(number, user, index, size, *, correct=True, late=False):
        scheme, version, profile, _, kind, label, inherits = profiles[index]
        challenge_id, method_id, submission_id = (str(UUID(int=number*10+n)) for n in (100, 101, 102))
        received = 3600600.0 + number * 3600
        round_id = 1000 + number
        ends_at = (round_id + 1) * 3600.0
        session.add(Method(id=method_id, user_id=user, name='Synthetic method', pseudocode='Not an attack.', created_at=received-20))
        session.add(Challenge(id=challenge_id, user_id=user, scheme_id=scheme, scheme_version=version,
            profile_id=profile, round_id=round_id, issued_at=received-10, starts_at=round_id * 3600.0,
            ends_at=ends_at, public_data={}, manifest={'instance_size': size, 'profile_kind': kind,
                'profile_label': label, 'record_inheritance': inherits}, target_mac='synthetic', correctness_passed=True))
        session.flush()
        session.add(Submission(id=submission_id, challenge_id=challenge_id, user_id=user, method_id=method_id,
            profile_id=profile, scheme_version=version, round_id=round_id, idempotency_key=str(UUID(int=number+5000)),
            payload_mac='synthetic', correct=correct, received_at=ends_at if late else received,
            elapsed_seconds=10, compute={}))
        session.flush()
        return submission_id
    valid = [add(1, owner, 1, 2), add(2, second, 0, 1), add(3, owner, 0, 1),
             add(4, second, 3, 2), add(5, owner, 2, 1), add(6, owner, 4, 3)]
    # These actual JSON scalar types must remain rejected by the SQL guards.
    for number, size in enumerate(('1', True, 1.0, None, 999), 7):
        add(number, second, 0, size)
    add(12, second, 0, 1, correct=False)
    add(13, second, 0, 1, late=True)
    return owner, second, valid, add


def queries(session, owner):
    dialect = session.get_bind().dialect.name
    participants, families = scoring.score_relations(dialect)
    return {
        'registered': normalized(session.execute(milestones.registered_receipts(dialect, include_standard=True))),
        'participants': normalized(session.execute(select(participants))),
        'families': normalized(session.execute(select(families))),
        'places': normalized(session.execute(select(milestones._places(owner, dialect)))),
    }


def test_registered_receipts_scores_and_milestones_match_original_values(connection, monkeypatch):
    # All tables are created in memory or the unique rolled-back CI schema.
    Base.metadata.create_all(connection)
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        owner, second, valid, add = seed(session, monkeypatch)
        actual = queries(session, owner)
        with monkeypatch.context() as patch:
            patch.setattr(scoring, 'metadata_relation', old_values)
            baseline = queries(session, owner)
        assert actual == baseline
        assert {row[1] for row in actual['registered']} == set(valid)
        # No response or live score is cached: a new valid result changes totals.
        add(14, second, 5, 3)
        fresh = queries(session, second)
        assert fresh['participants'] != actual['participants']
        with monkeypatch.context() as patch:
            patch.setattr(scoring, 'metadata_relation', old_values)
            assert fresh == queries(session, second)

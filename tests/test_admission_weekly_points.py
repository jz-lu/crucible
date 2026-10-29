"""Admission recognition uses immutable active-time events, never registry age."""
from dataclasses import replace
import json
from math import inf, nan
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite

from crucible import proposal_retractions, registry, scoring
from crucible.db import User
from crucible.protocol_proposals import ProtocolProposal
from crucible.scoring_awards import (ADMISSION_POINTS, ACTIVE_WEEK_POINTS, WEEK_SECONDS,
    AdmissionCreditError, SchemeAdmissionCredit, SchemeAdmissionRetirement,
    admission_points_relation, award_scheme_credit, retire_scheme_credit)
from test_scoring_awards import admission


def totals(db, as_of):
    with db.transaction() as session:
        row = session.execute(admission_points_relation(as_of)).mappings().first()
        return dict(row) if row else None


def retire(db, settings, args, timestamp, **overrides):
    return retire_scheme_credit(db, settings, family_id=args['family_id'],
        reviewer_id=args['reviewer_id'], reason='Family retired after explicit scientific and operational review.',
        clock=lambda: timestamp, **overrides)


def withdraw(db, args, timestamp, kind=proposal_retractions.SCHEME):
    with db.transaction(write=True) as session:
        proposal = session.get(ProtocolProposal, args['proposal_id'])
        author = session.get(User, proposal.author_id)
        body = proposal_retractions.SchemeRetractionInput(expected_revision=proposal.revision,
            content_sha256=proposal.content_sha256, reason='The author withdraws this proposal.', idempotency_key=uuid4())
        return proposal_retractions.withdraw(session, kind, proposal, author, timestamp, body,
            revision=proposal.revision, digest=proposal.content_sha256)


@pytest.mark.parametrize('elapsed,weeks', [(0,0), (WEEK_SECONDS-0.01,0), (WEEK_SECONDS,1),
    (WEEK_SECONDS+0.01,1), (2*WEEK_SECONDS-0.01,1), (2*WEEK_SECONDS,2), (5*WEEK_SECONDS+100,5)])
def test_only_complete_active_weeks_accrue_from_actual_admission(admission, elapsed, weeks):
    db, settings, args, _ = admission
    assert totals(db, 10 + elapsed) is None, 'Approval without admission has no credit.'
    award_scheme_credit(db, settings, **args)
    actual = totals(db, 10 + elapsed)
    assert actual['admission_points'] == ADMISSION_POINTS == 2000
    assert actual['active_weeks'] == weeks
    assert actual['weekly_points'] == ACTIVE_WEEK_POINTS * weeks == 100 * weeks
    assert actual['scheme_contributions'] == 2000 + 100 * weeks


def test_as_of_is_fixed_for_query_and_future_admissions_are_not_counted(admission):
    db, settings, args, author = admission
    award_scheme_credit(db, settings, **args)
    assert totals(db, 9.99) is None
    participants, _ = scoring.score_relations(as_of=10 + 3 * WEEK_SECONDS)
    with db.transaction() as session:
        row = session.execute(select(participants).where(participants.c.id == author)).one()
        assert row.points == row.scheme_contributions == 2300
        assert row.admission_active_weeks == 3
        assert row.score_as_of == 10 + 3 * WEEK_SECONDS
    for value in [-1, nan, inf, -inf, True, '100']:
        with pytest.raises(ValueError, match='timestamp'):
            admission_points_relation(value)


def test_retirement_preserves_earned_points_and_registry_restoration_does_not_resume(admission, monkeypatch):
    db, settings, args, _ = admission
    initial = award_scheme_credit(db, settings, **args)
    cutoff = 10 + 2 * WEEK_SECONDS + 123
    result = retire(db, settings, args, cutoff)
    assert result['created'] and result['retirement']['created_at'] == cutoff
    for as_of in [cutoff, cutoff + WEEK_SECONDS, cutoff + 100 * WEEK_SECONDS]:
        assert totals(db, as_of)['scheme_contributions'] == 2200
    monkeypatch.delitem(registry.CURRENT, args['scheme_id'])
    assert totals(db, cutoff + WEEK_SECONDS)['scheme_contributions'] == 2200
    monkeypatch.setitem(registry.CURRENT, args['scheme_id'], args['scheme_version'])
    assert award_scheme_credit(db, settings, **args)['credit'] == initial['credit']
    assert totals(db, cutoff + 100 * WEEK_SECONDS)['scheme_contributions'] == 2200
    with db.transaction() as session:
        assert session.get(SchemeAdmissionCredit, args['family_id']).created_at == 10


@pytest.mark.parametrize('retired_week,withdrawn_week,expected', [(None,1,1),(1,3,1),(3,1,1)])
def test_author_withdrawal_is_an_automatic_terminal_cutoff(admission, retired_week, withdrawn_week, expected):
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    if retired_week is not None:
        retire(db, settings, args, 10 + retired_week * WEEK_SECONDS + 100)
    withdraw(db, args, 10 + withdrawn_week * WEEK_SECONDS + 100)
    assert totals(db, 10 + 200 * WEEK_SECONDS)['scheme_contributions'] == 2000 + 100 * expected


def test_retirement_retries_dry_run_authority_and_conflicts(admission):
    db, settings, args, _ = admission
    with pytest.raises(AdmissionCreditError, match='existing admission'):
        retire(db, settings, args, 20)
    award_scheme_credit(db, settings, **args)
    assert retire(db, settings, args, 20, dry_run=True)['created'] is False
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionRetirement)) == 0
    with pytest.raises(AdmissionCreditError, match='configured reviewer'):
        retire(db, replace(settings, reviewer_ids=()), args, 20)
    for invalid in [9, nan, inf, -1, True]:
        with pytest.raises(AdmissionCreditError, match='Retirement time'):
            retire(db, settings, args, invalid)
    first = retire(db, settings, args, 20)
    again = retire(db, settings, args, 200)
    assert not again['created'] and again['retirement'] == first['retirement']
    with pytest.raises(AdmissionCreditError, match='immutable retirement'):
        retire_scheme_credit(db, settings, family_id=args['family_id'], reviewer_id=args['reviewer_id'],
            reason='A conflicting attempt must not rewrite the historical cutoff.', clock=lambda:300)
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionRetirement)) == 1


def test_concurrent_exact_retirements_record_one_immutable_cutoff(admission):
    from concurrent.futures import ThreadPoolExecutor
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retire(db, settings, args, 20), range(2)))
    assert sorted(result['created'] for result in results) == [False, True]
    assert results[0]['retirement'] == results[1]['retirement']


def test_points_do_not_infer_a_retirement_date_from_current_registry(admission, monkeypatch):
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    monkeypatch.delitem(registry.CURRENT, args['scheme_id'])
    assert totals(db, 10 + 2 * WEEK_SECONDS)['scheme_contributions'] == 2200
    # Registry removal requires an explicit operator ledger action; inventing a
    # cutoff at read time would produce unstable and non-reproducible history.


def test_people_expose_only_numeric_breakdown_not_private_lifecycle_evidence(admission, monkeypatch):
    from fastapi.testclient import TestClient
    from crucible.app import create_app
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    retirement = retire(db, settings, args, 10 + 2 * WEEK_SECONDS)
    monkeypatch.setattr(scoring, 'time', SimpleNamespace(time=lambda:10 + 9 * WEEK_SECONDS))
    app = create_app(settings)
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        profile = client.get('/api/people/author').json()['person']
        assert profile['points'] == profile['score_breakdown']['scheme_contributions'] == 2200
        assert profile['scheme_admission_breakdown'] == {
            'policy_id':'crucible-scheme-admission-points-v2',
            'admission_points':2000, 'completed_active_weeks':2, 'weekly_points':200}
        board = client.get('/api/people?include_records=false').json()
        encoded = json.dumps(profile) + json.dumps(board)
        for private in [args['proposal_id'], args['review_id'], args['reviewer_id'], args['evidence'], retirement['retirement']['reason']]:
            assert private not in encoded
    app.state.db.engine.dispose()


def test_fixed_as_of_sql_compiles_for_both_supported_databases():
    for dialect in [sqlite.dialect(), postgresql.dialect()]:
        compiled = str(admission_points_relation(1000000).compile(dialect=dialect, compile_kwargs={'literal_binds':True}))
        assert 'floor(' in compiled and 'scheme_admission_retirements' in compiled
        assert 'proposal_retractions' in compiled and 'scheme_admission' in compiled
        assert 'CURRENT_TIMESTAMP' not in compiled and 'now()' not in compiled


def test_0029_migration_preserves_admission_history_and_does_not_invent_retirements(admission, tmp_path, monkeypatch):
    import base64
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from crucible.config import ROOT
    from crucible.db import Database
    from crucible.protocol_proposals import ProtocolProposalRevision, ProtocolProposalReview
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    old_url = f"sqlite:///{tmp_path / 'before-lifecycle.db'}"
    for key, value in {'DATABASE_URL':old_url, 'CRUCIBLE_DATA_DIR':str(tmp_path),
                       'CRUCIBLE_SECRET':base64.b64encode(b'm' * 32).decode(), 'CRUCIBLE_ENV':'development'}.items():
        monkeypatch.setenv(key, value)
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'migrations'))
    command.upgrade(config, '0028')
    migrated = Database(old_url)
    tables = [User, ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview, SchemeAdmissionCredit]
    before = {}
    with db.transaction() as source, migrated.transaction(write=True) as target:
        for model in tables:
            rows = [dict(row) for row in source.execute(select(model.__table__)).mappings()]
            before[model.__tablename__] = rows
            target.execute(model.__table__.insert(), rows)
    command.upgrade(config, '0029')
    with migrated.transaction() as session:
        for model in tables:
            assert [dict(row) for row in session.execute(select(model.__table__)).mappings()] == before[model.__tablename__]
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionRetirement)) == 0
    assert {column['name'] for column in inspect(migrated.engine).get_columns('scheme_admission_retirements')} == set(SchemeAdmissionRetirement.__table__.columns.keys())
    command.downgrade(config, '0028')
    with migrated.transaction() as session:
        assert session.get(SchemeAdmissionCredit, args['family_id']).created_at == 10
    assert 'scheme_admission_retirements' not in inspect(migrated.engine).get_table_names()
    migrated.engine.dispose()

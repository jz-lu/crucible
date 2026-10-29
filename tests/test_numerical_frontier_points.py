"""Flat numerical recognition follows the real within-release frontier only."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from crucible import app as application  # Register all foreign-key metadata.
from crucible import scoring
from crucible.db import Base, Challenge, Database, Method, Submission, User


@pytest.fixture
def ledger(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'frontier.db'}")
    Base.metadata.create_all(db.engine)
    with db.transaction(write=True) as session:
        session.add_all(User(id=name, handle=name, created_at=1) for name in ['alice','bob','carol'])
    yield db
    db.engine.dispose()


def add(db, user, scheme, ordinal, tick, *, identifier=None, value=None, correct=True, version=None):
    enrolled = scoring.FAMILY_RELEASES[scheme]
    release = version or enrolled['version']
    profile = enrolled['profile_prefix'] + str(ordinal)
    received = 3600600 + tick * 3600
    round_id = int(received) // 3600
    key, method = str(uuid4()), str(uuid4())
    identifier = identifier or str(uuid4())
    with db.transaction(write=True) as session:
        session.add(Method(id=method,user_id=user,name='Synthetic proof',pseudocode='Fixture, not an attack.',created_at=received-10))
        session.add(Challenge(id=key,user_id=user,scheme_id=scheme,scheme_version=release,profile_id=profile,
            round_id=round_id,issued_at=received-5,starts_at=round_id*3600,ends_at=(round_id+1)*3600,
            public_data={},manifest={'instance_size':ordinal if value is None else value},target_mac='fixture',correctness_passed=True))
        session.flush()
        session.add(Submission(id=identifier,challenge_id=key,user_id=user,method_id=method,profile_id=profile,
            scheme_version=release,round_id=round_id,idempotency_key=str(uuid4()),payload_mac='fixture',correct=correct,
            received_at=received,elapsed_seconds=5,compute={}))
    return identifier


def scores(db):
    participants, families = scoring.score_relations(as_of=4000000)
    with db.transaction() as session:
        return ({row.id:row.points for row in session.execute(select(participants))},
                {(row.user_id,row.scheme_id):row for row in session.execute(select(families))})


@pytest.mark.parametrize('scheme,endpoint',[('ml-kem',6),('rsa-kem',50)])
def test_six_or_fifty_configurations_offer_the_same_single_flat_award(ledger,scheme,endpoint):
    first=add(ledger,'alice',scheme,endpoint,0)
    add(ledger,'bob',scheme,endpoint,1)
    add(ledger,'bob',scheme,1,2)  # An untouched lower hole is not a frontier advance.
    points,families=scores(ledger)
    assert points == {'alice':100,'bob':0,'carol':0}
    assert families[('alice',scheme)].early_recovery_submission_id == first
    assert families[('alice',scheme)].progress == families[('bob',scheme)].progress == 0


def test_steps_repeats_and_inherited_lower_records_never_stack(ledger):
    first=add(ledger,'alice','teaching-dh-kem',1,0)
    highest=None
    for ordinal in range(2,21):
        highest=add(ledger,'alice','teaching-dh-kem',ordinal,ordinal)
    for tick in range(21,25):
        add(ledger,'alice','teaching-dh-kem',20,tick)
    add(ledger,'bob','rsa-kem',20,0)  # A direct leap in a separate family earns the same100.
    points,families=scores(ledger)
    assert points == {'alice':100,'bob':100,'carol':0}
    family=families[('alice','teaching-dh-kem')]
    assert family.early_recovery_submission_id == first
    assert family.early_recovery_tier == 1
    assert family.highest_frontier_submission_id == highest


def test_copied_frontier_and_lower_holes_get_zero_but_another_person_advancing_gets100(ledger):
    add(ledger,'alice','rsa-kem',10,0)
    add(ledger,'bob','rsa-kem',10,1)
    add(ledger,'bob','rsa-kem',5,2)
    assert scores(ledger)[0]['bob'] == 0
    first=add(ledger,'bob','rsa-kem',11,3)
    high=add(ledger,'bob','rsa-kem',12,4)
    add(ledger,'alice','rsa-kem',13,5)
    add(ledger,'bob','ml-kem',1,6)
    points,families=scores(ledger)
    assert points == {'alice':100,'bob':200,'carol':0}
    assert families[('bob','rsa-kem')].early_recovery_submission_id == first
    assert families[('bob','rsa-kem')].highest_frontier_submission_id == high


def test_server_time_then_receipt_id_breaks_ties_deterministically(ledger):
    # Insert the losing tie first to ensure database insertion order is irrelevant.
    add(ledger,'bob','rsa-kem',10,0,identifier=str(UUID(int=2)))
    winner=add(ledger,'alice','rsa-kem',10,0,identifier=str(UUID(int=1)))
    points,families=scores(ledger)
    assert points == {'alice':100,'bob':0,'carol':0}
    assert families[('alice','rsa-kem')].early_recovery_submission_id == winner


def test_invalid_or_unenrolled_receipts_never_occupy_the_frontier(ledger):
    add(ledger,'alice','rsa-kem',50,0,correct=False)
    add(ledger,'alice','rsa-kem',50,1,value='50')
    add(ledger,'alice','rsa-kem',50,2,value=50.0)
    add(ledger,'alice','rsa-kem',1,3,value=True)
    add(ledger,'alice','rsa-kem',50,4,version='unreviewed-release')
    add(ledger,'alice','rsa-kem',51,5)
    add(ledger,'bob','rsa-kem',1,6)
    assert scores(ledger)[0] == {'alice':0,'bob':100,'carol':0}


def test_scoring_reads_preserve_every_receipt_and_ignore_registry_navigation(ledger,monkeypatch):
    from crucible import registry
    add(ledger,'alice','rsa-kem',10,0)
    add(ledger,'bob','rsa-kem',9,1)
    with ledger.transaction() as session:
        before=[dict(row) for row in session.execute(select(Submission.__table__)).mappings()]
    monkeypatch.setattr(registry,'CURRENT',{})
    for _ in range(3):assert scores(ledger)[0] == {'alice':100,'bob':0,'carol':0}
    with ledger.transaction() as session:
        assert [dict(row) for row in session.execute(select(Submission.__table__)).mappings()] == before


def test_v11_sql_has_one_prior_frontier_window_without_threshold_expansion():
    assert scoring.POLICY_ID == 'crucible-points-v11'
    participants,_ = scoring.score_relations('postgresql',as_of=4000000)
    sql=str(select(participants).compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert 'ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING' in sql
    assert 'score_thresholds' not in sql
    assert 'score_frontiers.tier > coalesce(score_frontiers.previous_frontier, 0)' in sql
    assert 'json_type(' not in sql

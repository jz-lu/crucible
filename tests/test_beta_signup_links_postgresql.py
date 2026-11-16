"""Admission-link serialization and grants on the fixed disposable CI PG17 service."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import func, select, text

from crucible import beta_access as beta
from crucible.db import Database
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged


@pytest.fixture
def signup_pg(postgres):
    command.upgrade(postgres.config, 'head')
    command.check(postgres.config)
    assert_original_unchanged(postgres)
    db = Database(postgres.runtime_url)
    settings = SimpleNamespace(secret=b'g' * 32, origins=('http://testserver',))
    with db.transaction(write=True) as session:
        link = beta.create_signup_link(session, settings, 'PG fixture', 4_000_000, actor_id=postgres.ids['user'])
        session.flush()
        token = beta.signup_link_data(settings, link, 4_000_000)['url'].split('token=')[1]
        identifier = link.id
    try:
        yield SimpleNamespace(db=db, settings=settings, token=token, identifier=identifier, fixture=postgres)
    finally:
        db.engine.dispose()


def test_pg17_same_email_retries_create_one_invitation_and_runtime_grants_apply(signup_pg):
    s = signup_pg
    def claim(_):
        with s.db.transaction(write=True) as session:
            invitation, url = beta.signup_invitation(session, s.settings, s.token, 'same@example.org', 4_000_001)
            return invitation.id, url
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(4)))
    assert len(set(results)) == 1
    with s.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(beta.BetaSignupInvitation)) == 1
    for table in ('beta_signup_links', 'beta_signup_invitations'):
        with s.fixture.runtime_engine.connect() as connection:
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                assert connection.scalar(text('SELECT has_table_privilege(current_user, :table, :privilege)'), {'table': table, 'privilege': privilege})
            assert not connection.scalar(text('SELECT has_table_privilege(current_user, :table, :privilege)'), {'table': table, 'privilege': 'TRUNCATE'})
        with s.fixture.backup_engine.connect() as connection:
            assert connection.scalar(text('SELECT has_table_privilege(current_user, :table, :privilege)'), {'table': table, 'privilege': 'SELECT'})
            assert not connection.scalar(text('SELECT has_table_privilege(current_user, :table, :privilege)'), {'table': table, 'privilege': 'UPDATE'})


@pytest.mark.parametrize('operation', ['claim', 'validate'])
def test_pg17_revocation_serializes_with_recipient_issuance_and_registration(signup_pg, operation):
    s = signup_pg
    with s.db.transaction(write=True) as session:
        _, url = beta.signup_invitation(session, s.settings, s.token, 'pending@example.org', 4_000_001)
        child = url.split('token=')[1]
    started = Event()
    def attempt():
        with s.db.transaction(write=True) as session:
            started.set()
            try:
                if operation == 'claim':
                    beta.signup_invitation(session, s.settings, s.token, 'later@example.org', 4_000_003)
                else:
                    beta.validate_registration_invitation(session, child, 4_000_003, lock=True)
            except HTTPException as error:
                return error.status_code
            return 200
    with ThreadPoolExecutor(max_workers=1) as pool:
        with s.db.transaction(write=True) as session:
            beta.revoke_signup_link(session, s.identifier, 4_000_002)
            session.flush()  # Parent lock stays held until this transaction commits.
            result = pool.submit(attempt)
            assert started.wait(5)
        assert result.result(timeout=10) == 403
    with s.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(beta.BetaSignupInvitation)) == 1

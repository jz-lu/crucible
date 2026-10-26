"""Real migrations/grants and competing profile writes on the fixed CI PG17 service.

Only unique synthetic roles/schema are created. Never use DATABASE_URL or caller
credentials. Locally this suite is collected/skipped unless the dedicated service
flag is enabled; CI enables it and a missing/wrong service fails before mutation.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from crucible import dh_v4, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.security import mac, verify_password
from test_metadata_relations import POSTGRES_URL
from test_researcher_profiles_migration import migration_config, seed_original, snapshot, user_columns


@pytest.fixture
def postgres(tmp_path, monkeypatch):
    if os.environ.get('CRUCIBLE_METADATA_POSTGRES') != '1':
        pytest.skip('Fixed disposable PostgreSQL service is not enabled')
    for name in tuple(os.environ):
        if name.startswith('PG') or name == 'DATABASE_URL' or name.startswith('CRUCIBLE_'):
            monkeypatch.delenv(name)
    admin = create_engine(POSTGRES_URL, poolclass=NullPool, connect_args={
        'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'})
    suffix = uuid4().hex
    schema, owner, runtime, backup = ('profile_' + suffix, 'profile_owner_' + suffix,
                                      'profile_runtime_' + suffix, 'profile_backup_' + suffix)
    engines, roles = [], []
    provisioned = False
    try:
        with admin.begin() as connection:
            assert connection.execute(text(
                "SELECT current_database(), current_user, current_setting('server_version_num')::integer / 10000"
            )).one() == ('crucible_metadata_test', 'crucible_metadata', 17)
            for role in (owner, runtime, backup):
                # Identifiers contain only these fixed prefixes and generated hex.
                connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD 'profile-synthetic-only' "
                                           "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION")
                roles.append(role)
            connection.exec_driver_sql(f'CREATE SCHEMA {schema} AUTHORIZATION {owner}')
            connection.exec_driver_sql(f'REVOKE ALL ON SCHEMA {schema} FROM PUBLIC')
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA {schema} TO {runtime}')
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA {schema} TO {backup}')
            connection.exec_driver_sql(f'ALTER ROLE {backup} SET default_transaction_read_only=on')
            connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} '
                                      f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {runtime}')
            connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA {schema} '
                                      f'GRANT SELECT ON TABLES TO {backup}')
        provisioned = True
        options = f'-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=5000'
        def url(role):
            return make_url(POSTGRES_URL).set(username=role, password='profile-synthetic-only',
                query={'connect_timeout': '5', 'options': options}).render_as_string(hide_password=False)
        owner_engine, runtime_engine, backup_engine = [create_engine(url(role), poolclass=NullPool)
                                                       for role in (owner, runtime, backup)]
        engines.extend((owner_engine, runtime_engine, backup_engine))
        config = migration_config(url(owner), tmp_path, monkeypatch)
        command.upgrade(config, '0023')
        old_names = set(inspect(owner_engine).get_table_names()) - {'alembic_version'}
        assert len(old_names) == 45 and 'researcher_profiles' not in old_names
        metadata = MetaData()
        metadata.reflect(bind=owner_engine, only=sorted(old_names))
        with owner_engine.begin() as connection:
            ids, password = seed_original(connection, metadata)
        with owner_engine.connect() as connection:
            before = snapshot(connection, metadata)
        columns = user_columns(owner_engine)
        command.upgrade(config, '0024')
        yield SimpleNamespace(admin=admin, owner=owner, runtime=runtime, schema=schema,
            owner_engine=owner_engine, runtime_engine=runtime_engine, runtime_url=url(runtime),
            backup=backup, backup_engine=backup_engine,
            config=config, metadata=metadata, before=before, old_names=old_names,
            user_columns=columns, ids=ids, password=password)
    finally:
        for engine in engines:
            engine.dispose()
        # Each generated role belongs only to this fixture. No ambient schema,
        # service role, existing connection, or other test's objects are touched.
        if provisioned:
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
                for role in roles:
                    if connection.scalar(text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role': role}):
                        connection.exec_driver_sql(f'DROP OWNED BY {role}')
                        connection.exec_driver_sql(f'DROP ROLE {role}')
        admin.dispose()


def assert_original_unchanged(fixture):
    with fixture.owner_engine.connect() as connection:
        assert snapshot(connection, fixture.metadata) == fixture.before
        assert verify_password(connection.scalar(text('SELECT password_hash FROM users')), fixture.password)
    assert user_columns(fixture.owner_engine) == fixture.user_columns


def test_pg17_migration_preserves_0023_data_and_applies_owner_runtime_grants(postgres):
    assert set(inspect(postgres.owner_engine).get_table_names()) - {'alembic_version'} == postgres.old_names | {'researcher_profiles'}
    assert_original_unchanged(postgres)
    with postgres.runtime_engine.connect() as connection:
        assert connection.execute(text('SELECT current_user, current_schema()')).one() == (postgres.runtime, postgres.schema)
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0024'
        assert connection.scalar(text('SELECT count(*) FROM researcher_profiles')) == 0
        assert connection.scalar(text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='researcher_profiles'::regclass")) == postgres.owner
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
            assert connection.scalar(text("SELECT has_table_privilege(current_user, 'researcher_profiles', :privilege)"), {'privilege': privilege})
        for privilege in ('TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, 'researcher_profiles', :privilege)"), {'privilege': privilege})
        assert not connection.scalar(text("SELECT has_schema_privilege(current_user, current_schema(), 'CREATE')"))
        assert connection.execute(text('SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user')).one() == (False, False, False)
    # Test permission denial, not just the privilege-reporting query.
    with pytest.raises(DBAPIError) as denied:
        with postgres.runtime_engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE researcher_profiles ADD COLUMN forbidden integer')
    assert denied.value.orig.sqlstate == '42501'
    with postgres.backup_engine.connect() as connection:
        assert connection.execute(text('SELECT current_user, current_schema()')).one() == (postgres.backup, postgres.schema)
        assert connection.scalar(text("SHOW transaction_read_only")) == 'on'
        assert connection.scalar(text('SELECT count(*) FROM researcher_profiles')) == 0
        assert connection.scalar(text("SELECT has_table_privilege(current_user, 'researcher_profiles', 'SELECT')"))
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, 'researcher_profiles', :privilege)"), {'privilege': privilege})
        assert not connection.scalar(text("SELECT has_schema_privilege(current_user, current_schema(), 'CREATE')"))
    with pytest.raises(DBAPIError) as readonly:
        with postgres.backup_engine.begin() as connection:
            connection.exec_driver_sql('UPDATE researcher_profiles SET revision=revision WHERE false')
    assert readonly.value.orig.sqlstate in {'25006', '42501'}
    command.check(postgres.config)
    # Empty new table only: this does not approve dropping populated profiles.
    command.downgrade(postgres.config, '0023')
    assert_original_unchanged(postgres)
    assert 'researcher_profiles' not in inspect(postgres.owner_engine).get_table_names()


@pytest.mark.parametrize('same_request', [False, True], ids=['competing-revisions', 'identical-retry'])
def test_pg17_runtime_profile_writes_lock_account_and_preserve_original_data(postgres, monkeypatch, same_request):
    # Existing real portable adapter, with its normal artifact checks; no native
    # checks are bypassed or claimed by this database-specific fixture.
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})
    settings = Settings(postgres.runtime_url, b'p' * 32, origins=('http://testserver',),
                        access_mode='beta', rate_limits=False)
    app = create_app(settings, clock=lambda: 3_600_600.125, initialize=False)
    token = 'synthetic-preserved-session'
    headers = {'Origin': 'http://testserver', 'X-CSRF-Token': mac(settings.secret, 'csrf-v1', token.encode())}
    first = {'display_name': 'Synthetic researcher', 'affiliation': 'First draft', 'position': '',
             'website': '', 'expected_revision': 0, 'idempotency_key': str(uuid4())}
    second = dict(first) if same_request else {**first, 'affiliation': 'Second draft', 'idempotency_key': str(uuid4())}
    barrier = Barrier(2, timeout=5)
    locks = []
    def before_lock(connection, cursor, statement, parameters, context, executemany):
        if 'FROM users' in statement and 'FOR UPDATE' in statement:
            locks.append(connection)
            barrier.wait()  # Both real requests reach the account lock together.
    try:
        with ExitStack() as stack:
            clients = [stack.enter_context(TestClient(app)) for _ in range(2)]
            for client in clients:
                client.cookies.set(settings.cookie_name, token)
            assert clients[0].get('/api/me/profile').json()['profile']['revision'] == 0
            event.listen(app.state.db.engine, 'before_cursor_execute', before_lock)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(client.put, '/api/me/profile', json=body, headers=headers)
                               for client, body in zip(clients, (first, second))]
                    responses = [future.result(timeout=15) for future in futures]
            finally:
                event.remove(app.state.db.engine, 'before_cursor_execute', before_lock)
            assert len(locks) == 2 and locks[0] is not locks[1]
            assert sorted(response.status_code for response in responses) == ([200, 200] if same_request else [200, 409])
            saved = next(response.json()['profile'] for response in responses if response.status_code == 200)
            assert saved['revision'] == 1
            if same_request:
                assert responses[0].json()['profile'] == responses[1].json()['profile']
            else:
                assert next(response.json()['code'] for response in responses if response.status_code == 409) == 'profile_revision_conflict'
            winner = first if saved['affiliation'] == first['affiliation'] else second
            assert clients[0].put('/api/me/profile', json=winner, headers=headers).json()['profile'] == saved
            mismatch = clients[0].put('/api/me/profile', json={**winner, 'affiliation': 'Reused key'}, headers=headers)
            assert mismatch.status_code == 409 and mismatch.json()['code'] == 'profile_idempotency_conflict'
            update = {**first, 'expected_revision': 1, 'affiliation': 'Explicit next revision', 'idempotency_key': str(uuid4())}
            final = clients[0].put('/api/me/profile', json=update, headers=headers)
            assert final.status_code == 200 and final.json()['profile']['revision'] == 2
            assert clients[0].put('/api/me/profile', json=winner, headers=headers).status_code == 409
            assert clients[0].get('/api/me/profile').json()['profile'] == final.json()['profile']
        with postgres.runtime_engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM researcher_profiles')) == 1
        with postgres.backup_engine.connect() as connection:
            assert connection.execute(text('SELECT user_id, revision, affiliation FROM researcher_profiles')).one() == (
                postgres.ids['user'], 2, 'Explicit next revision')
        assert_original_unchanged(postgres)
    finally:
        app.state.db.engine.dispose()

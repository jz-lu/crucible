"""Rollback bridge retains old API behavior against the additive profile schema."""
import pytest
from sqlalchemy import inspect

from crucible import dh_v4, registry
from crucible.researcher_profiles import ResearcherProfile
from test_beta_boundary import private_app


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


def test_bridge_retains_old_member_reads_without_enabling_profile_routes(private_app):
    actor = private_app.test_login('participant')
    assert ResearcherProfile.__tablename__ in inspect(private_app.state.db.engine).get_table_names()
    response = actor.client.get('/api/people/participant?include_milestones=false')
    assert response.status_code == 200
    assert response.json()['person']['handle'] == 'participant'
    assert 'researcher_profile' not in response.json()['person']
    assert all(route.path != '/api/me/profile' for route in private_app.routes if hasattr(route, 'path'))

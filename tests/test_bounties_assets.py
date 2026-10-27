"""Bounty examples stay behind the existing member asset boundary."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from crucible import beta_access
from crucible.public_assets import PUBLIC_FILES, PROTECTED_FILES
from test_beta_boundary import private_app

FILES = ('research-guide.js', 'research-guide-data.js', 'research-guide.css')
WEB = Path(__file__).resolve().parents[1] / 'web'

@pytest.mark.parametrize('name', FILES)
def test_bounty_assets_require_current_membership_and_are_not_cacheable(private_app, name):
    path = '/notebook/' + name
    assert path in PROTECTED_FILES and path not in PUBLIC_FILES
    anonymous = TestClient(private_app)
    for method in ('GET', 'HEAD'):
        denied = anonymous.request(method, path + '?v=' + 'a' * 24)
        assert denied.status_code == 401
        assert denied.headers['cache-control'] == 'no-store'
    member = private_app.test_login('participant')
    admitted = member.client.get(path, headers={'Accept-Encoding': 'gzip'})
    assert admitted.status_code == 200
    assert admitted.content == (WEB / 'notebook' / name).read_bytes()
    assert admitted.headers['cache-control'] == 'no-store'
    assert admitted.headers['content-type'].startswith('text/css' if name.endswith('.css') else 'text/javascript')
    assert 'etag' not in admitted.headers
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, 'revoked', private_app.test_now[0])
    denied = member.client.get(path)
    assert denied.status_code == 401 and denied.headers['cache-control'] == 'no-store'

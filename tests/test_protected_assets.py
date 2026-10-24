"""Smaller bundled map/notebook transfers without caching private responses."""
import gzip

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete

from crucible import beta_access
from crucible.config import ROOT
from crucible.public_assets import PROTECTED_FILES, ProtectedAssets, is_public_ui
from test_beta_boundary import private_app
from test_browser_admission import selects
from test_public_assets import asset_request


@pytest.mark.parametrize('path', PROTECTED_FILES)
def test_fixed_inventory_preserves_exact_source_and_no_store(path):
    assets = ProtectedAssets(ROOT / 'web')
    plain = assets.response(asset_request(path, encoding='identity'))
    packed = assets.response(asset_request(path, encoding='gzip'))
    assert not is_public_ui(path)
    assert plain.status_code == packed.status_code == 200
    assert plain.body == (ROOT / 'web' / path.lstrip('/')).read_bytes()
    if len(plain.body) >= 1024:
        assert packed.headers['content-encoding'] == 'gzip'
        assert gzip.decompress(packed.body) == plain.body
        assert len(packed.body) < len(plain.body)
    else:
        assert 'content-encoding' not in packed.headers and packed.body == plain.body
    for response in (plain, packed):
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['vary'] == 'Accept-Encoding'
        assert response.headers['content-length'] == str(len(response.body))
        assert response.headers['content-type'].split(';')[0] == PROTECTED_FILES[path]
        assert 'etag' not in response.headers and 'last-modified' not in response.headers
    assert assets.response(asset_request(path, encoding='gzip')).body == packed.body


@pytest.mark.parametrize(('header', 'expected'), [
    (None, 'identity'), ('gzip', 'gzip'), ('gzip;q=0, *;q=1', 'identity'),
    ('gzip;q=0.2, identity;q=0.8', 'identity'), ('gzip;q=0.8, identity;q=0.2', 'gzip'),
    ('*', 'gzip'), ('*;q=0', None), ('gzip;q=1, gzip;q=0', 'identity'),
    ('gzip;q=NaN', 'identity'), ('gzip;q=1.1', 'identity'),
])
def test_protected_encoding_uses_the_same_quality_rules(header, expected):
    response = ProtectedAssets(ROOT / 'web').response(asset_request('/map/notes-data.js', encoding=header))
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['vary'] == 'Accept-Encoding'
    if expected is None:
        assert response.status_code == 406 and response.body == b''
    else:
        assert response.status_code == 200
        assert response.headers.get('content-encoding', 'identity') == expected


def test_head_and_old_validators_never_create_private_304_or_partial_responses():
    assets = ProtectedAssets(ROOT / 'web')
    for encoding in ('identity', 'gzip'):
        get = assets.response(asset_request('/map/notes.json', encoding=encoding))
        head = assets.response(asset_request('/map/notes.json', encoding=encoding, method='HEAD'))
        assert head.body == b'' and head.headers == get.headers
        conditional = assets.response(asset_request('/map/notes.json', encoding=encoding,
            query=b'v=old', headers=[(b'if-none-match', b'*'), (b'range', b'bytes=0-15')]))
        assert conditional.status_code == 200 and conditional.body == get.body


def test_compression_cache_invalidates_source_edits_and_refuses_escapes(tmp_path):
    (tmp_path / 'map').mkdir()
    source = tmp_path / 'map/app.js'
    source.write_bytes(b'old' * 1000)
    assets = ProtectedAssets(tmp_path)
    before = assets.response(asset_request('/map/app.js', encoding='gzip'))
    source.write_bytes(b'newer' * 1000)
    after = assets.response(asset_request('/map/app.js', encoding='gzip'))
    assert gzip.decompress(before.body) == b'old' * 1000
    assert gzip.decompress(after.body) == source.read_bytes()
    assert before.body != after.body
    source.unlink()
    outside = tmp_path / 'account-private.txt'
    outside.write_text('not a bundled map asset')
    source.symlink_to(outside)
    assert assets.response(asset_request('/map/app.js')).status_code == 404
    for path in ('/account-private.txt', '/api/notebook/notes', '/map/account-private.txt'):
        assert assets.response(asset_request(path)).status_code == 404


def test_compressed_private_assets_still_check_membership_each_request(private_app):
    anonymous = TestClient(private_app)
    member = private_app.test_login('participant')
    paths = ('/map/notes-data.js', '/map/notes.json', '/notebook/notebook.js')
    for path in paths:
        denied = anonymous.get(path, headers={'Accept-Encoding': 'gzip', 'If-None-Match': '*'})
        assert denied.status_code == 401 and denied.headers['cache-control'] == 'no-store'
        assert 'content-encoding' not in denied.headers
        with selects(private_app) as queries:
            response = member.client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert len(queries) == 1 and 'FROM login_sessions' in queries[0]
        assert response.status_code == 200 and response.headers['content-encoding'] == 'gzip'
        assert response.headers['cache-control'] == 'no-store'
        assert response.content == (ROOT / 'web' / path.lstrip('/')).read_bytes()
    with private_app.state.db.transaction(write=True) as session:
        session.execute(delete(beta_access.BetaMembership).where(
            beta_access.BetaMembership.user_id == private_app.test_member))
    for path in paths:
        revoked = member.client.get(path, headers={'Accept-Encoding': 'gzip', 'If-None-Match': '*'})
        assert revoked.status_code == 403 and revoked.headers['cache-control'] == 'no-store'
        assert 'content-encoding' not in revoked.headers


def test_no_compression_is_added_to_account_or_notebook_api(private_app):
    member = private_app.test_login('participant')
    for path in ('/api/session', '/api/notebook/notes', '/api/notebook/export'):
        response = member.client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert 'content-encoding' not in response.headers

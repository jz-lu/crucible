"""Reload fast without caching participant data or stale code after a release."""
import re
import base64
import hashlib

from fastapi.testclient import TestClient
from starlette.requests import Request

from crucible.public_assets import PUBLIC_FILES, PublicAssets
from test_beta_boundary import private_app


def test_shell_versions_public_assets_and_reuses_identical_bytes(private_app):
    client = TestClient(private_app)
    shell = client.get('/')
    assert shell.status_code == 200
    assert shell.headers['cache-control'] == 'private, no-cache'
    critical_style, = re.findall(r'<style>(.*?)</style>', shell.text, re.S)
    style_hash = base64.b64encode(hashlib.sha256(critical_style.encode()).digest()).decode()
    assert "'sha256-" + style_hash + "'" in shell.headers['content-security-policy']
    assert 'unsafe-inline' not in shell.headers['content-security-policy']
    assert shell.text.index('<style>') < shell.text.index('rel="stylesheet"')
    urls = re.findall(r'(?:src|href)="(/[^"?]+\?v=[a-f0-9]{24})"', shell.text)
    assert '/app.js' in {url.split('?')[0] for url in urls}
    assert '/vendor/katex/katex.min.css' in {url.split('?')[0] for url in urls}
    assert '/assets/crucible-logo.png' in {url.split('?')[0] for url in urls}
    for url in set(urls):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers['cache-control'] == 'private, max-age=31536000, immutable'
        assert response.headers['x-content-type-options'] == 'nosniff'
        unchanged = client.get(url, headers={'If-None-Match': response.headers['etag']})
        assert unchanged.status_code == 304 and unchanged.content == b''
    assert client.get('/', headers={'If-None-Match': shell.headers['etag']}).status_code == 304


def test_wrong_version_revalidates_instead_of_freezing_old_url(private_app):
    client = TestClient(private_app)
    first = client.get('/app.js?v=old-version')
    assert first.headers['cache-control'] == 'private, no-cache'
    assert client.get('/app.js', headers={'If-None-Match': 'W/' + first.headers['etag']}).status_code == 304
    assert client.get('/app.js', headers={'If-None-Match': '"unrelated"'}).status_code == 200


def test_fonts_are_versioned_and_unknown_paths_never_become_cacheable(private_app):
    client = TestClient(private_app)
    css = client.get('/vendor/katex/katex.min.css')
    fonts = re.findall(r'url\((fonts/[^)]+\?v=[a-f0-9]{24})\)', css.text)
    assert fonts
    font = client.get('/vendor/katex/' + fonts[0])
    assert font.status_code == 200 and 'immutable' in font.headers['cache-control']
    for path in ('/vendor/katex/missing.js', '/vendor/katex/%2e%2e/app.js', '/unknown.js'):
        response = client.get(path)
        assert response.status_code == 404 and response.headers['cache-control'] == 'no-store'


def test_version_query_cannot_cache_or_bypass_private_routes(private_app):
    anonymous = TestClient(private_app)
    for path in ('/api/schemes', '/api/notebook/export', '/notebook/store.js', '/map/notes.json'):
        denied = anonymous.get(path + '?v=' + 'a' * 24)
        assert denied.status_code == 401 and denied.headers['cache-control'] == 'no-store'
    member = private_app.test_login('participant')
    for path in ('/api/session', '/notebook/store.js', '/api/notebook/notes'):
        response = member.client.get(path + '?v=' + 'a' * 24)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'


def test_content_change_changes_url_and_development_sees_edits(tmp_path):
    for url in PUBLIC_FILES:
        path = tmp_path / url.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'old')
    (tmp_path / 'index.html').write_text('<script src="/app.js"></script>')
    assets = PublicAssets(tmp_path)
    request = Request({'type': 'http', 'path': '/', 'method': 'GET', 'query_string': b'', 'headers': []})
    first = assets.response(request)
    (tmp_path / 'app.js').write_bytes(b'new code with changed content')
    second = assets.response(request)
    assert first.body != second.body and first.headers['etag'] != second.headers['etag']
    frozen = PublicAssets(tmp_path, frozen=True)
    frozen_before = frozen.response(request)
    (tmp_path / 'app.js').write_bytes(b'another edit')
    assert frozen.response(request).body == frozen_before.body
    assert PublicAssets(tmp_path, frozen=True).response(request).body != frozen_before.body


def test_ui_types_do_not_depend_on_the_host_mime_database(monkeypatch):
    from crucible.config import ROOT
    from crucible import public_assets
    monkeypatch.setattr(public_assets.mimetypes, 'guess_type', lambda *_: (None, None))
    assets = PublicAssets(ROOT / 'web').snapshot()
    for url, expected in (
        ('/app.js', 'text/javascript'), ('/styles.css', 'text/css'),
        ('/assets/crucible-logo.png', 'image/png'),
        ('/vendor/katex/fonts/KaTeX_Main-Regular.woff2', 'font/woff2'),
        ('/vendor/katex/fonts/KaTeX_Main-Regular.woff', 'font/woff'),
        ('/vendor/katex/fonts/KaTeX_Main-Regular.ttf', 'font/ttf'),
    ):
        assert assets[url].media_type == expected

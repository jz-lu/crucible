"""Reload fast without caching participant data or stale code after a release."""
import re
import base64
import hashlib
import gzip

import pytest

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
    assert shell.text.index('<style>') < shell.text.index('<style data-main-style>')
    main_css, = re.findall(r'<style data-main-style>(.*?)</style>', shell.text, re.S)
    assert main_css.encode() == client.get('/styles.css').content
    main_hash = base64.b64encode(hashlib.sha256(main_css.encode()).digest()).decode()
    assert "'sha256-" + main_hash + "'" in shell.headers['content-security-policy']
    assert len(shell.headers.get_list('content-security-policy')) == 1
    bootstrap, = re.findall(r'<script data-bootstrap>(.*?)</script>', shell.text, re.S)
    assert bootstrap.encode() == client.get('/bootstrap.js').content
    script_hash = base64.b64encode(hashlib.sha256(bootstrap.encode()).digest()).decode()
    assert "script-src 'self' 'sha256-" + script_hash + "';" in shell.headers['content-security-policy']
    assert 'src="/bootstrap.js' not in shell.text
    assert shell.text.index('<script data-bootstrap>') < shell.text.index('<body')
    assert 'href="/styles.css' not in shell.text
    urls = re.findall(r'(?:src|href|data-katex-script|data-katex-style)="(/[^"?]+\?v=[a-f0-9]{24})"', shell.text)
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
        denied = anonymous.get(path + '?v=' + 'a' * 24, headers={'Accept-Encoding': 'gzip'})
        assert denied.status_code == 401 and denied.headers['cache-control'] == 'no-store'
        assert 'content-encoding' not in denied.headers
    member = private_app.test_login('participant')
    for path in ('/api/session', '/api/notebook/notes'):
        response = member.client.get(path + '?v=' + 'a' * 24, headers={'Accept-Encoding': 'gzip'})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert 'content-encoding' not in response.headers
    bundled = member.client.get('/notebook/store.js?v=' + 'a' * 24,
                                headers={'Accept-Encoding': 'gzip'})
    assert bundled.status_code == 200
    assert bundled.headers['cache-control'] == 'no-store'
    assert bundled.headers['content-encoding'] == 'gzip'


def test_content_change_changes_url_and_development_sees_edits(tmp_path):
    for url in PUBLIC_FILES:
        path = tmp_path / url.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'old')
    (tmp_path / 'index.html').write_text('<script src="/bootstrap.js" async></script><script src="/app.js"></script><link rel="stylesheet" href="/styles.css">')
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


@pytest.fixture
def frozen_public_assets():
    from crucible.config import ROOT
    return PublicAssets(ROOT / 'web', frozen=True)


def asset_request(path='/app.js', *, encoding=None, method='GET', query=b'', headers=()):
    fields = list(headers)
    if encoding is not None:
        fields.append((b'accept-encoding', encoding.encode()))
    return Request({'type': 'http', 'path': path, 'method': method,
                    'query_string': query, 'headers': fields})


def small_public_root(tmp_path, css=b'body{color:navy}\n'):
    for url in PUBLIC_FILES:
        path = tmp_path / url.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(css if url == '/styles.css' else b'public fixture')
    (tmp_path / 'index.html').write_text('<!-- critical-canvas -->'
        '<script src="/bootstrap.js" async></script><script src="/app.js" defer></script>'
        '<link rel="stylesheet" href="/styles.css">')
    return tmp_path


def embedded_style(asset):
    style, = re.findall(r'<style data-main-style>(.*?)</style>', asset.body.decode(), re.S)
    return style.encode()


def embedded_bootstrap(asset):
    script, = re.findall(r'<script data-bootstrap>(.*?)</script>', asset.body.decode(), re.S)
    return script.encode()


@pytest.mark.parametrize('encoding', ['identity', 'gzip'])
@pytest.mark.parametrize('method', ['GET', 'HEAD'])
@pytest.mark.parametrize('conditional', [False, True])
def test_html_policy_is_bound_to_exact_selected_asset_on_every_representation(
        frozen_public_assets, encoding, method, conditional):
    assets = frozen_public_assets
    initial = assets.response(asset_request('/', encoding=encoding))
    request = asset_request('/', encoding=encoding, method=method,
        headers=[(b'if-none-match', initial.headers['etag'].encode())] if conditional else [])
    response = assets.response(request)
    selected = request.scope['_crucible_public_asset']
    assert selected is assets._snapshot['/']
    style = embedded_style(selected)
    digest = base64.b64encode(hashlib.sha256(style).digest())
    assert b"'sha256-" + digest + b"'" in selected.content_security_policy
    script_digest = base64.b64encode(hashlib.sha256(embedded_bootstrap(selected)).digest())
    assert b"script-src 'self' 'sha256-" + script_digest + b"';" in selected.content_security_policy
    assert response.status_code == (304 if conditional else 200)
    if conditional or method == 'HEAD':
        assert response.body == b''
    else:
        assert (gzip.decompress(response.body) if encoding == 'gzip' else response.body) == selected.body
    assert response.headers['cache-control'] == 'private, no-cache'


def test_selected_policy_does_not_spill_into_other_assets_or_unknown_paths(frozen_public_assets):
    from crucible.public_assets import CONTENT_SECURITY_POLICY
    request = asset_request('/')
    frozen_public_assets.response(request)
    frozen_public_assets.response(request, '/app.js')
    assert request.scope['_crucible_public_asset'] is frozen_public_assets._snapshot['/app.js']
    assert request.scope['_crucible_public_asset'].content_security_policy == CONTENT_SECURITY_POLICY
    assert frozen_public_assets.response(request, '/notebook/notebook.css').status_code == 404
    assert '_crucible_public_asset' not in request.scope
    refused = asset_request('/', encoding='identity;q=0,gzip;q=0')
    assert frozen_public_assets.response(refused).status_code == 406
    assert refused.scope['_crucible_public_asset'] is frozen_public_assets._snapshot['/']


def test_embedded_sources_are_not_url_rewritten_and_policy_adds_only_exact_hashes(tmp_path):
    from crucible.public_assets import CONTENT_SECURITY_POLICY
    css = '/* café */\nbody::before{content:"/app.js"}\n'.encode()
    small_public_root(tmp_path, css)
    bootstrap = b'window.fixture = "/app.js";\n'
    (tmp_path / 'bootstrap.js').write_bytes(bootstrap)
    assets = PublicAssets(tmp_path, frozen=True)._snapshot
    assert embedded_style(assets['/']) == css == assets['/styles.css'].body
    digest = base64.b64encode(hashlib.sha256(css).digest())
    script_digest = base64.b64encode(hashlib.sha256(bootstrap).digest())
    assert embedded_bootstrap(assets['/']) == bootstrap == assets['/bootstrap.js'].body
    assert (assets['/'].content_security_policy
            .replace(b" 'sha256-" + digest + b"'", b'', 1)
            .replace(b" 'sha256-" + script_digest + b"'", b'', 1)) == CONTENT_SECURITY_POLICY
    assert b'unsafe-inline' not in assets['/'].content_security_policy
    assert assets['/'].body.count(b'<style data-main-style>') == 1
    assert b'href="/styles.css' not in assets['/'].body
    assert b'src="/bootstrap.js' not in assets['/'].body
    assert assets['/bootstrap.js'].content_security_policy == CONTENT_SECURITY_POLICY


def test_inline_bootstrap_normalizes_html_line_endings_before_hashing(tmp_path):
    small_public_root(tmp_path)
    original = b'window.fixture = 1;\r\n// comment\rwindow.fixture++;\n'
    normalized = original.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    (tmp_path / 'bootstrap.js').write_bytes(original)
    assets = PublicAssets(tmp_path, frozen=True)._snapshot
    assert embedded_bootstrap(assets['/']) == normalized
    assert assets['/bootstrap.js'].body == original
    digest = base64.b64encode(hashlib.sha256(normalized).digest())
    assert b"script-src 'self' 'sha256-" + digest + b"';" in assets['/'].content_security_policy


@pytest.mark.parametrize('script', [b'</script>', b'// </ScRiPt >', b'<!-- escaped',
                                   b'// <script>', b'window.fixture="\0";', b'\xff'])
def test_unsafe_or_non_utf8_bootstrap_cannot_create_a_public_shell(tmp_path, script):
    small_public_root(tmp_path)
    (tmp_path / 'bootstrap.js').write_bytes(script)
    with pytest.raises((RuntimeError, UnicodeDecodeError)):
        PublicAssets(tmp_path, frozen=True)


@pytest.mark.parametrize('count', [0, 2])
def test_shell_requires_exactly_one_bootstrap_tag(tmp_path, count):
    small_public_root(tmp_path)
    (tmp_path / 'index.html').write_text('<script src="/bootstrap.js" async></script>' * count
                                       + '<link rel="stylesheet" href="/styles.css">')
    with pytest.raises(RuntimeError, match='one bootstrap script'):
        PublicAssets(tmp_path, frozen=True)


def test_bootstrap_edit_updates_html_policy_and_etag_but_not_frozen_snapshot(tmp_path):
    small_public_root(tmp_path)
    dev, frozen = PublicAssets(tmp_path), PublicAssets(tmp_path, frozen=True)
    before_request = asset_request('/')
    before = dev.response(before_request)
    old_asset = before_request.scope['_crucible_public_asset']
    (tmp_path / 'bootstrap.js').write_text('window.fixture = "new bootstrap";\n')
    after_request = asset_request('/', headers=[(b'if-none-match', before.headers['etag'].encode())])
    after = dev.response(after_request)
    new_asset = after_request.scope['_crucible_public_asset']
    assert after.status_code == 200 and after.headers['etag'] != before.headers['etag']
    assert old_asset.content_security_policy != new_asset.content_security_policy
    assert embedded_style(old_asset) == embedded_style(new_asset)
    assert embedded_bootstrap(new_asset) == b'window.fixture = "new bootstrap";\n'
    assert frozen.response(asset_request('/')).body == before.body
    assert frozen._snapshot['/'].content_security_policy == old_asset.content_security_policy
    assert set(frozen._snapshot) == set(PUBLIC_FILES) | {'/'}


def test_inline_css_normalizes_html_line_endings_before_hashing(tmp_path):
    css = b'body{\r\ncolor:navy;\r}\n'
    assets = PublicAssets(small_public_root(tmp_path, css), frozen=True)._snapshot
    normalized = b'body{\ncolor:navy;\n}\n'
    assert embedded_style(assets['/']) == normalized
    assert assets['/styles.css'].body == css
    digest = base64.b64encode(hashlib.sha256(normalized).digest())
    assert b"'sha256-" + digest + b"'" in assets['/'].content_security_policy


@pytest.mark.parametrize('css', [b'</style>', b'/* </StYlE > */', b'body{content:"\0"}', b'\xff'])
def test_unsafe_or_non_utf8_main_css_cannot_create_a_public_shell(tmp_path, css):
    with pytest.raises((RuntimeError, UnicodeDecodeError)):
        PublicAssets(small_public_root(tmp_path, css), frozen=True)


@pytest.mark.parametrize('count', [0, 2])
def test_shell_requires_exactly_one_main_link(tmp_path, count):
    small_public_root(tmp_path)
    (tmp_path / 'index.html').write_text('<link rel="stylesheet" href="/styles.css">' * count)
    with pytest.raises(RuntimeError, match='one main stylesheet'):
        PublicAssets(tmp_path, frozen=True)


def test_css_edit_updates_development_html_policy_and_etag_but_not_frozen_snapshot(tmp_path):
    small_public_root(tmp_path)
    # A colocated protected file must never be folded into the public shell.
    (tmp_path / 'private-note.txt').write_text('PRIVATE_NOTE_SENTINEL')
    dev, frozen = PublicAssets(tmp_path), PublicAssets(tmp_path, frozen=True)
    before_request = asset_request('/')
    before = dev.response(before_request)
    old_asset = before_request.scope['_crucible_public_asset']
    (tmp_path / 'styles.css').write_text('body{color:darkgreen}\n')
    after_request = asset_request('/', headers=[(b'if-none-match', before.headers['etag'].encode())])
    after = dev.response(after_request)
    new_asset = after_request.scope['_crucible_public_asset']
    assert after.status_code == 200 and after.headers['etag'] != before.headers['etag']
    assert old_asset.content_security_policy != new_asset.content_security_policy
    assert embedded_style(old_asset) == b'body{color:navy}\n'
    assert embedded_style(new_asset) == b'body{color:darkgreen}\n'
    assert frozen.response(asset_request('/')).body == before.body
    assert frozen._snapshot['/'].content_security_policy == old_asset.content_security_policy
    assert b'PRIVATE_NOTE_SENTINEL' not in after.body
    assert set(frozen._snapshot) == set(PUBLIC_FILES) | {'/'}


def test_critical_canvas_matches_base_typography_with_one_exact_csp_hash(frozen_public_assets):
    from crucible.public_assets import CONTENT_SECURITY_POLICY
    shell = frozen_public_assets.response(asset_request('/')).body.decode()
    css = frozen_public_assets.response(asset_request('/styles.css')).body.decode()
    critical, = re.findall(r'<style>(.*?)</style>', shell, re.S)
    sans, = re.findall(r'--sans:\s*([^;]+);', css)
    assert 'font:14px/1.6 ' + sans + ';' in critical
    assert 'font: 14px/1.6 var(--sans);' in css
    assert 'background:#f6f9fd' in critical and '--paper: #f6f9fd;' in css
    assert 'color:#2d4563' in critical and '--text: #2d4563;' in css
    assert '[hidden]{display:none!important}' in critical
    assert 'visibility:' not in critical and 'animation:' not in critical
    digest = base64.b64encode(hashlib.sha256(critical.encode()).digest()).decode()
    assert "'sha256-" + digest + "'" in CONTENT_SECURITY_POLICY.decode()
    assert 'unsafe-inline' not in CONTENT_SECURITY_POLICY.decode()


@pytest.mark.parametrize(('header', 'expected'), [
    (None, 'identity'), ('', 'identity'), ('identity', 'identity'),
    ('gzip', 'gzip'), ('GZip;Q=1.000', 'gzip'), ('br, gzip;q=0.5', 'gzip'),
    ('gzip;q=0', 'identity'), ('gzip;q=0, *;q=1', 'identity'),
    ('gzip;q=0.2, identity;q=0.8', 'identity'),
    ('gzip;q=0.8, identity;q=0.2', 'gzip'),
    ('gzip;q=0.5, identity;q=0.5', 'gzip'),
    ('*', 'gzip'), ('*;q=0.5', 'gzip'), ('*;q=0, gzip;q=1', 'gzip'),
    ('*;q=0, identity;q=1', 'identity'),
    ('gzip;q=0, identity;q=0', None), ('*;q=0', None),
    ('br, identity;q=0', None),
    ('gzip;q=NaN', 'identity'), ('gzip;q=1.1', 'identity'),
    ('gzip;q=0.1234', 'identity'), ('gzip;q=0.5;q=1', 'identity'),
    ('gzip;q=1, gzip;q=0', 'identity'),
])
def test_public_encoding_preferences(frozen_public_assets, header, expected):
    response = frozen_public_assets.response(asset_request(encoding=header))
    assert response.headers['vary'] == 'Accept-Encoding'
    if expected is None:
        assert response.status_code == 406
        assert response.headers['cache-control'] == 'no-store'
        assert response.body == b'' and 'etag' not in response.headers
    else:
        assert response.status_code == 200
        assert response.headers.get('content-encoding', 'identity') == expected


@pytest.mark.parametrize('path', ['/', '/app.js', '/styles.css', '/vendor/katex/katex.min.js'])
def test_public_gzip_bytes_etags_and_versions(frozen_public_assets, path):
    assets = frozen_public_assets
    plain = assets.response(asset_request(path, encoding='identity'))
    compressed = assets.response(asset_request(path, encoding='gzip'))
    assert gzip.decompress(compressed.body) == plain.body
    assert len(compressed.body) < len(plain.body)
    assert compressed.headers['content-type'] == plain.headers['content-type']
    assert compressed.headers['etag'] != plain.headers['etag']
    assert plain.headers['etag'] == '"' + hashlib.sha256(plain.body).hexdigest() + '"'
    assert compressed.headers['content-length'] == str(len(compressed.body))
    assert plain.headers['content-length'] == str(len(plain.body))
    assert assets.response(asset_request(path, encoding='gzip')).body == compressed.body
    for encoding, representation in [('identity', plain), ('gzip', compressed)]:
        tag = representation.headers['etag']
        unchanged = assets.response(asset_request(path, encoding=encoding,
            headers=[(b'if-none-match', ('W/' + tag).encode())]))
        assert unchanged.status_code == 304 and unchanged.body == b''
        assert unchanged.headers['etag'] == tag
        assert unchanged.headers['vary'] == 'Accept-Encoding'
        assert 'content-length' not in unchanged.headers
        assert unchanged.headers.get('content-encoding') == representation.headers.get('content-encoding')
        other = compressed if encoding == 'identity' else plain
        changed_representation = assets.response(asset_request(path, encoding=encoding,
            headers=[(b'if-none-match', other.headers['etag'].encode())]))
        assert changed_representation.status_code == 200
        head = assets.response(asset_request(path, encoding=encoding, method='HEAD'))
        assert head.body == b'' and head.headers == representation.headers
        # These endpoints intentionally do not implement partial responses.
        ranged = assets.response(asset_request(path, encoding=encoding,
            headers=[(b'range', b'bytes=0-15')]))
        assert ranged.status_code == 200 and ranged.body == representation.body
        assert 'content-range' not in ranged.headers
    if path != '/':
        version = plain.headers['etag'][1:25]
        for encoding in ('identity', 'gzip'):
            versioned = assets.response(asset_request(path, encoding=encoding,
                query=('v=' + version).encode()))
            assert versioned.headers['cache-control'] == 'private, max-age=31536000, immutable'


def test_public_gzip_combines_header_lines_and_preserves_binary_assets(frozen_public_assets):
    response = frozen_public_assets.response(asset_request(headers=[
        (b'accept-encoding', b'gzip;q=1'), (b'accept-encoding', b'gzip;q=0'),
    ]))
    assert 'content-encoding' not in response.headers
    for path in ('/assets/crucible-logo.png', '/vendor/katex/fonts/KaTeX_Main-Regular.woff2'):
        response = frozen_public_assets.response(asset_request(path, encoding='gzip'))
        assert response.status_code == 200 and 'content-encoding' not in response.headers
        assert frozen_public_assets.response(asset_request(path, encoding='gzip, identity;q=0')).status_code == 406


def test_production_snapshot_precomputes_compression(frozen_public_assets, monkeypatch):
    from crucible import public_assets
    def unexpected_compression(*args, **kwargs):
        raise AssertionError('A request recompressed an immutable asset')
    monkeypatch.setattr(public_assets.gzip, 'compress', unexpected_compression)
    assert frozen_public_assets.response(asset_request(encoding='gzip')).status_code == 200


def test_math_mount_head_uses_negotiated_get_metadata(private_app):
    client = TestClient(private_app)
    path = '/vendor/katex/katex.min.js'
    for encoding in ('identity', 'gzip'):
        headers = {'Accept-Encoding': encoding}
        get = client.get(path, headers=headers)
        head = client.head(path, headers=headers)
        assert get.status_code == head.status_code == 200
        assert head.content == b''
        for name in ('content-length', 'etag', 'vary', 'cache-control', 'content-encoding'):
            assert head.headers.get(name) == get.headers.get(name)
        unchanged = client.head(path, headers={**headers, 'If-None-Match': get.headers['etag']})
        assert unchanged.status_code == 304 and unchanged.content == b''

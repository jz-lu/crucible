"""Separate public UI caching from protected bundled-asset compression.

The HTML shell always revalidates. Its content-addressed asset URLs let the
browser reuse identical code across visits without serving old code on deploy.
No notebook, map, API, or user-owned files enter the public inventory. A separate
fixed protected inventory compresses bundled map/notebook files only; admission
still runs for every request and responses remain no-store.
"""
from dataclasses import dataclass
from functools import cached_property, lru_cache
import base64
import gzip
import hashlib
import mimetypes
from pathlib import Path
import re

from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles


# Paint the canvas before the render-blocking stylesheets arrive. Only this
# fixed, server-owned style is allowed inline; scripts and user CSS stay blocked.
FIRST_PAINT_STYLE = (
    'html{background:#f6f9fd;color-scheme:light}'
    'body{margin:0;background:#f6f9fd;color:#2d4563;'
    "font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    '-webkit-font-smoothing:antialiased}[hidden]{display:none!important}'
)
_style_hash = base64.b64encode(hashlib.sha256(FIRST_PAINT_STYLE.encode()).digest()).decode()
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'sha256-" + _style_hash
    + "'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
).encode('ascii')


PUBLIC_FILES = (
    '/styles.css', '/app.js', '/bootstrap.js', '/math.js', '/assumptions.js',
    '/proposal-retractions.js', '/assets/crucible-logo.png',
)

PROTECTED_FILES = {
    '/map/' + name: media for name, media in {
        'index.html': 'text/html', 'notes.html': 'text/html',
        'styles.css': 'text/css', 'notes.css': 'text/css',
        'app.js': 'text/javascript', 'map-views.js': 'text/javascript',
        'notes.js': 'text/javascript', 'note-ui.js': 'text/javascript',
        'catalogue.js': 'text/javascript', 'notes-data.js': 'text/javascript',
        'catalogue.json': 'application/json', 'notes.json': 'application/json',
        'PIPELINE.txt': 'text/plain', 'NOTE-FORMAT.txt': 'text/plain',
    }.items()
} | {
    '/notebook/' + name: media for name, media in {
        'seed.js': 'text/javascript', 'store.js': 'text/javascript',
        'notebook.js': 'text/javascript', 'notebook.css': 'text/css',
        'request-types.json': 'application/json',
    }.items()
}

# Minimal Linux images do not always have a system MIME database. The browser
# must receive the same declared types for our fixed UI inventory on every host.
UI_MEDIA_TYPES = {
    '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
    '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
}


@lru_cache(maxsize=256)
def _read(path, modified, size, inode):
    # The stat fields invalidate local-development edits; deployed files are
    # immutable. A different source root cannot borrow another root's cache.
    return Path(path).read_bytes()


def read(path):
    stat = path.stat()
    return _read(str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)


@lru_cache(maxsize=32)
def _gzip(body):
    # Production snapshots retain these bytes; this small cache also avoids
    # recompressing unchanged public files on every development request.
    return gzip.compress(body, compresslevel=4, mtime=0)


def _encoding(header, *, gzip_available):
    """Choose a public representation; identity is the implicit fallback.

    Explicit identity preferences compete with gzip. An explicit exclusion
    overrides '*'; conflicting duplicates and malformed weights fail closed
    for that coding. See RFC 9110 section 12.5.3.
    """
    qualities = {}
    for item in header.split(','):
        parts = [part.strip() for part in item.split(';')]
        coding = parts[0].lower()
        if coding not in {'gzip', 'identity', '*'}:
            continue
        quality = 1.0
        if len(parts) > 1:
            name, _, value = parts[1].partition('=')
            valid = (len(parts) == 2 and name.strip().lower() == 'q'
                     and re.fullmatch(r'(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)', value.strip()))
            quality = float(value.strip()) if valid else 0.0
        qualities[coding] = min(quality, qualities.get(coding, 1.0))
    compressed = qualities.get('gzip', qualities.get('*', 0.0))
    identity = qualities.get('identity', 0.0 if qualities.get('*') == 0 else 1.0)
    if (gzip_available and compressed > 0
            and ('identity' not in qualities or compressed >= identity)):
        return 'gzip'
    return 'identity' if identity > 0 else None


@dataclass(frozen=True)
class Asset:
    body: bytes
    media_type: str

    @cached_property
    def version(self):
        return self.etag[1:25]

    @cached_property
    def etag(self):
        return '"' + hashlib.sha256(self.body).hexdigest() + '"'

    @cached_property
    def gzip_body(self):
        if self.media_type in {'text/html', 'text/css', 'text/javascript'}:
            return _gzip(self.body)
        return None

    @cached_property
    def gzip_etag(self):
        return '"' + hashlib.sha256(self.gzip_body).hexdigest() + '-gzip"'


class PublicAssets:
    def __init__(self, web_root, *, frozen=False):
        self.root = Path(web_root).resolve()
        self.frozen = frozen
        self._snapshot = self.snapshot() if frozen else None

    def snapshot(self):
        paths = {url: self.root / url.lstrip('/') for url in PUBLIC_FILES}
        vendor = self.root / 'vendor' / 'katex'
        for path in vendor.rglob('*'):
            if path.is_file() and path.resolve().is_relative_to(vendor.resolve()):
                paths['/' + path.relative_to(self.root).as_posix()] = path
        assets = {}
        for url, path in paths.items():
            if not path.resolve().is_relative_to(self.root):
                raise RuntimeError('Public UI asset escaped its fixed directory.')
            media = UI_MEDIA_TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0]
            assets[url] = Asset(read(path), media or 'application/octet-stream')
        css_url = '/vendor/katex/katex.min.css'
        if css_url in assets:
            def font_url(match):
                relative = match.group(1)
                font = assets.get('/vendor/katex/' + relative)
                return 'url(' + relative + '?v=' + font.version + ')' if font else match.group(0)
            css = re.sub(r'url\((fonts/[^\s)]+)\)', font_url, assets[css_url].body.decode())
            assets[css_url] = Asset(css.encode(), 'text/css')
        index = self.root / 'index.html'
        if not index.resolve().is_relative_to(self.root):
            raise RuntimeError('Public HTML shell escaped its fixed directory.')
        html = read(index).decode()
        html = html.replace('<!-- critical-canvas -->', '<style>' + FIRST_PAINT_STYLE + '</style>')
        for url, asset in assets.items():
            html = html.replace('"' + url + '"', '"' + url + '?v=' + asset.version + '"')
        assets['/'] = Asset(html.encode(), 'text/html')
        for asset in assets.values():
            # Compress after CSS/font URLs and the HTML shell are finalized.
            # Requests only select an existing representation.
            if asset.gzip_body is not None:
                _ = asset.gzip_etag
        return assets

    def response(self, request, path=None):
        path = path or request.url.path
        asset = (self._snapshot if self.frozen else self.snapshot()).get(path)
        if asset is None:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        encoding = _encoding(','.join(request.headers.getlist('accept-encoding')),
                             gzip_available=asset.gzip_body is not None)
        if encoding is None:
            return Response(status_code=406, headers={
                'Cache-Control': 'no-store', 'Vary': 'Accept-Encoding',
            })
        body = asset.gzip_body if encoding == 'gzip' else asset.body
        etag = asset.gzip_etag if encoding == 'gzip' else asset.etag
        versioned = path != '/' and request.query_params.get('v') == asset.version
        headers = {
            'Cache-Control': 'private, max-age=31536000, immutable' if versioned else 'private, no-cache',
            'ETag': etag,
            'Vary': 'Accept-Encoding',
        }
        if encoding == 'gzip':
            headers['Content-Encoding'] = 'gzip'
        validators = [part.strip().removeprefix('W/') for part in request.headers.get('if-none-match', '').split(',')]
        if '*' in validators or etag in validators:
            return Response(status_code=304, headers=headers)
        # Range remains unsupported: these routes return a complete 200, never
        # identity byte offsets against a compressed representation.
        headers['Content-Length'] = str(len(body))
        return Response(body if request.method != 'HEAD' else b'',
                        media_type=asset.media_type, headers=headers)


class PublicMathFiles(StaticFiles):
    """Keep the existing classified mount and fixed inventory containment."""
    def __init__(self, assets):
        super().__init__(directory=assets.root / 'vendor' / 'katex', follow_symlink=False)
        self.assets = assets

    async def get_response(self, path, scope):
        if scope['method'] not in {'GET', 'HEAD'}:
            return Response(status_code=405)
        return self.assets.response(Request(scope), '/vendor/katex/' + path)


class ProtectedAssets:
    """Compress fixed, shared source bytes; never cache rights or account data.

    These routes remain outside is_public_ui and behind the normal member
    admission. No ETag/304 storage is introduced: a new request always checks
    access and receives the selected complete representation with no-store.
    """
    def __init__(self, web_root):
        self.root = Path(web_root).resolve()

    def response(self, request):
        path = request.url.path
        media = PROTECTED_FILES.get(path)
        headers = {'Cache-Control': 'no-store', 'Vary': 'Accept-Encoding'}
        if media is None:
            return Response(status_code=404, headers=headers)
        if request.method not in {'GET', 'HEAD'}:
            return Response(status_code=405, headers=headers)
        source = (self.root / path.lstrip('/')).resolve()
        directory = self.root / path.split('/')[1]
        if not source.is_relative_to(directory):
            return Response(status_code=404, headers=headers)
        try:
            body = read(source)
        except OSError:
            return Response(status_code=404, headers=headers)
        encoding = _encoding(','.join(request.headers.getlist('accept-encoding')),
                             gzip_available=len(body) >= 1024)
        if encoding is None:
            return Response(status_code=406, headers=headers)
        if encoding == 'gzip':
            body = _gzip(body)
            headers['Content-Encoding'] = 'gzip'
        headers['Content-Length'] = str(len(body))
        return Response(body if request.method == 'GET' else b'',
                        media_type=media, headers=headers)


def is_public_ui(path):
    return path == '/' or path in PUBLIC_FILES or path.startswith('/vendor/katex/')

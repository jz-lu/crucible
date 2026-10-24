"""Version only already-public UI assets; account/research responses stay no-store.

The HTML shell always revalidates. Its content-addressed asset URLs let the
browser reuse identical code across visits without serving old code on deploy.
No notebook, map, API, or user-owned files are included in this inventory.
"""
from dataclasses import dataclass
from functools import cached_property, lru_cache
import base64
import hashlib
import mimetypes
from pathlib import Path
import re

from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles


# Paint the canvas before the render-blocking stylesheets arrive. Only this
# fixed, server-owned style is allowed inline; scripts and user CSS stay blocked.
FIRST_PAINT_STYLE = 'html{background:#f6f9fd;color-scheme:light}body{margin:0;background:#f6f9fd}[hidden]{display:none!important}'
_style_hash = base64.b64encode(hashlib.sha256(FIRST_PAINT_STYLE.encode()).digest()).decode()
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'sha256-" + _style_hash
    + "'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
).encode('ascii')


PUBLIC_FILES = (
    '/styles.css', '/app.js', '/bootstrap.js', '/math.js', '/assumptions.js',
    '/proposal-retractions.js', '/assets/crucible-logo.png',
)

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
        return assets

    def response(self, request, path=None):
        path = path or request.url.path
        asset = (self._snapshot if self.frozen else self.snapshot()).get(path)
        if asset is None:
            return Response(status_code=404, headers={'Cache-Control': 'no-store'})
        versioned = path != '/' and request.query_params.get('v') == asset.version
        headers = {
            'Cache-Control': 'private, max-age=31536000, immutable' if versioned else 'private, no-cache',
            'ETag': asset.etag,
        }
        validators = [part.strip().removeprefix('W/') for part in request.headers.get('if-none-match', '').split(',')]
        if '*' in validators or asset.etag in validators:
            return Response(status_code=304, headers=headers)
        headers['Content-Length'] = str(len(asset.body))
        return Response(asset.body if request.method != 'HEAD' else b'',
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


def is_public_ui(path):
    return path == '/' or path in PUBLIC_FILES or path.startswith('/vendor/katex/')

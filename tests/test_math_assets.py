"""The local math asset mount must not expose adjacent application files."""
import re

import pytest
from fastapi.testclient import TestClient

import crucible.app as application
from crucible.config import ROOT, Settings


@pytest.fixture
def math_client(tmp_path):
    settings = Settings(f"sqlite:///{tmp_path / 'math.db'}", b"m" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = application.create_app(settings, initialize=True)
    with TestClient(app) as client:
        yield client
    app.state.db.engine.dispose()


def test_math_assets_are_local_and_all_css_fonts_exist(math_client):
    for path, content_type in (("/math.js", "javascript"),
                               ("/vendor/katex/katex.min.js", "javascript"),
                               ("/vendor/katex/katex.min.css", "text/css"),
                               ("/vendor/katex/fonts/KaTeX_Main-Regular.woff2", "font/woff2")):
        response = math_client.get(path)
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]
        policy = response.headers["content-security-policy"]
        assert "script-src 'self'" in policy and "style-src 'self'" in policy
        assert "unsafe-inline" not in policy

    directory = ROOT / "web" / "vendor" / "katex"
    references = re.findall(r"url\(([^)]+)\)", (directory / "katex.min.css").read_text())
    assert len(references) == 60
    for reference in references:
        assert reference.startswith("fonts/")
        assert (directory / reference).is_file()
    assert "MIT License" in (directory / "LICENSE").read_text()
    assert "SIL OPEN FONT LICENSE" in (directory / "OFL.txt").read_text()


@pytest.mark.parametrize("path", [
    "/vendor/katex/%2e%2e/%2e%2e/index.html",
    "/vendor/katex/%2e%2e/%2e%2e/%2e%2e/crucible/app.py",
    "/vendor/katex/%2e%2e/%2e%2e/%2e%2e/.local/server.key",
    "/vendor/katex/",
])
def test_math_mount_rejects_traversal_and_directory_listing(math_client, path):
    assert math_client.get(path).status_code == 404


def test_math_mount_rejects_symlinks_outside_the_distribution(tmp_path, monkeypatch):
    from crucible.public_assets import PUBLIC_FILES
    for url in (*PUBLIC_FILES, '/index.html'):
        asset = tmp_path / 'web' / url.lstrip('/')
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text('synthetic public asset')
    (tmp_path / 'web/index.html').write_text('<script src="/bootstrap.js" async></script><link rel="stylesheet" href="/styles.css">')
    vendor = tmp_path / "web" / "vendor" / "katex"
    vendor.mkdir(parents=True)
    private = tmp_path / "private.txt"
    private.write_text("private test content")
    (vendor / "escape.txt").symlink_to(private)
    monkeypatch.setattr(application, "ROOT", tmp_path)
    settings = Settings(f"sqlite:///{tmp_path / 'test.db'}", b"m" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = application.create_app(settings, initialize=True)
    with TestClient(app) as client:
        response = client.get("/vendor/katex/escape.txt")
        assert response.status_code == 404
        assert "private test content" not in response.text
    app.state.db.engine.dispose()

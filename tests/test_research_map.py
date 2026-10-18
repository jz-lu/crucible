"""The curated map follows beta admission and serves only reviewed assets."""
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from crucible import notebook
from test_beta_boundary import private_app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ('src', 'href'):
            if key in attrs:
                self.urls.append(attrs[key])


def test_map_is_private_in_beta(private_app):
    with TestClient(private_app) as visitor:
        for path in ('index.html', 'notes.html', 'catalogue.js', 'notes-data.js',
                     'notes.json', 'catalogue.json'):
            for method in ('GET', 'HEAD'):
                result = visitor.request(method, '/map/' + path)
                assert result.status_code == 401
                assert result.headers['cache-control'] == 'no-store'


def test_map_links_and_assets_for_members(private_app):
    member = private_app.test_login('participant').client
    shell = member.get('/')
    assert 'id="nav-notebook"' in shell.text
    assert 'id="nav-map"' not in shell.text
    assert '/map/index.html' in member.get('/notebook/notebook.js').text
    for page in ('index.html', 'notes.html'):
        response = member.get('/map/' + page)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-frame-options'] == 'DENY'
        links = Links()
        links.feed(response.text)
        for url in links.urls:
            if url.startswith('#'):
                continue
            target = url.split('#', 1)[0]
            if not target.startswith('/'):
                target = '/map/' + target
            assert member.get(target).status_code == 200, target
    for path in ('build.py', 'README.txt', '.env', 'archive', 'candidate-template.json'):
        assert member.get('/map/' + path).status_code == 404


def test_map_notes_and_projection_remain_consistent():
    root = Path(__file__).resolve().parents[1] / 'web' / 'map'
    workspace = json.loads((root / 'notes.json').read_text())
    catalogue = json.loads((root / 'catalogue.json').read_text())
    notebook._workspace(workspace)
    spec = importlib.util.spec_from_file_location('research_map_builder', root / 'build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.validate(catalogue, workspace)
    assert all(entry['kind'] == 'kem' for entry in catalogue['schemes'])
    scored = {entry['scheme'] for entry in catalogue['scoring_snapshot']['releases']}
    highlighted = {entry['scheme'] for entry in catalogue['highlights']}
    assert highlighted <= scored <= {entry['id'] for entry in catalogue['schemes']}

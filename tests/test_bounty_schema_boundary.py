"""Source-only packaging boundary; deployment tooling is absent from the app image."""
from pathlib import Path


def test_source_overlays_replace_bridge_only_modules():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'deploy/release.py').read_text()
    assert 'rm -rf /opt/crucible/crucible' in source
    assert 'COPY --chown=0:0 source/ /opt/crucible/' in source

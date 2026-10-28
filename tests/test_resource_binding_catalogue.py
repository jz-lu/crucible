"""Exact native-image mapping gate; no database, upload, or worker execution.

Fixture: crucible-schemes main757323a9 (producer3f24458), 2026-10-28.
Only its source envelope is synthetic. Bindings are actual approved declarations.
This frozen test evidence is not a second editable publication registry.
"""
import hashlib
from pathlib import Path
from crucible import resource_identity, resource_publications, resource_state


def test_all_initial_producer_bindings_match_native_website_profiles():
    raw = (Path(__file__).parent / 'fixtures/resource-bindings-20261028.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '6151cf662c2fcf523dd627406171d8726e2f1630edcb84aa6f45338d0438e59a'
    snapshot = resource_identity.strict_json(raw)
    assert snapshot['format'] == 'crucible-resource-desired-v1'
    assert len(snapshot['bindings']) == 14
    assert sum(len(item['binding']['profiles']) for item in snapshot['bindings']) == 395
    assert len({(item['binding']['scheme_id'], item['binding']['version']) for item in snapshot['bindings']}) == 14
    for item in snapshot['bindings']:
        assert resource_state.SHA.fullmatch(item['producer_sha256'])
        assert item['fingerprint'] == resource_state.digest({'binding': item['binding'], 'producer': item['producer_sha256']})
        resource_publications.validate_binding(item['binding'])

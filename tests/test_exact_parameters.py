"""Display copies preserve exact large public integers without rewriting contracts."""
import json
from crucible import ec_compressed, registry


def test_ec_display_preserves_exact_integers_and_frozen_manifest():
    originals = ec_compressed.manifests()
    before = json.dumps(originals, sort_keys=True)
    presented = registry.presentation_manifests(ec_compressed)
    unsafe = [p for p in presented if "exact_parameter_json" in p]
    assert len(unsafe) == 42
    assert unsafe[0]["instance_size"] == 9
    for original, shown in zip(originals, presented):
        assert "exact_parameter_json" not in original
        if "exact_parameter_json" in shown:
            assert json.loads(shown["exact_parameter_json"]) == {
                key: original[key] for key in ("algorithms", "parameters", "encoding", "kdf") if key in original}
    presented[-1]["parameters"]["p"] = 1
    assert json.dumps(ec_compressed.manifests(), sort_keys=True) == before
    assert registry.presentation_manifests(ec_compressed)[-1]["parameters"]["p"] == originals[-1]["parameters"]["p"]


def test_exact_range_includes_nested_negative_values_without_rewriting_strings():
    assert not registry._has_unsafe_json_integer({"small": [True, -(2**53-1), 2**53-1], "string": str(2**100)})
    assert registry._has_unsafe_json_integer({"nested": [-(2**53)]})

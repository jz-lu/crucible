"""Mathematical presentation is additive and follows the pinned release."""
import hashlib
from pathlib import Path

import pytest

from crucible import dh_v3, dh_v4, kem, registry, rsa
from crucible.legacy import teaching_dh_v1


@pytest.mark.parametrize("adapter", [teaching_dh_v1, kem, dh_v3, dh_v4, rsa])
def test_math_descriptor_preserves_pinned_manifests_and_plain_fields(adapter):
    raw_profiles = adapter.manifests()
    size_parameter = dict(adapter.SIZE_PARAMETER) if hasattr(adapter, "SIZE_PARAMETER") else None
    scheme = registry.descriptor(adapter)
    assert scheme["profiles"] == raw_profiles
    assert scheme["protocol"]["construction"] == registry.DETAILS[adapter.SCHEME_ID]["construction"]
    assert scheme["protocol"]["public_input"] == registry.DETAILS[adapter.SCHEME_ID]["public_input"]
    assert scheme["profiles"][0]["correctness"]["argument"] == raw_profiles[0]["correctness"]["argument"]
    for field in ("construction_math", "public_input_math", "correctness_argument_math"):
        value = scheme["protocol"][field]
        assert value.count(r"\(") == value.count(r"\)") > 0
        assert not any(ord(character) < 32 for character in value)
    if size_parameter is None:
        assert scheme["size_parameter"] is None
    else:
        assert {key: value for key, value in scheme["size_parameter"].items() if key != "mapping_math"} == size_parameter
        assert dict(adapter.SIZE_PARAMETER) == size_parameter
        assert scheme["size_parameter"]["mapping_math"].count(r"\(") > 0
    source_hash = hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
    assert adapter.IMPLEMENTATION_SHA256 == source_hash
    assert all(profile["implementation"]["source_sha256"] == source_hash for profile in scheme["profiles"])


def test_mapping_math_keeps_archive_ranges_and_current_certificates_separate():
    current = registry.descriptor(dh_v4)["size_parameter"]["mapping_math"]
    release_three = registry.descriptor(dh_v3)["size_parameter"]["mapping_math"]
    previous = registry.descriptor(kem)["size_parameter"]["mapping_math"]
    assert "20 through 36" in previous
    assert "128" not in previous and "Pocklington" not in previous
    assert "37..128" in release_three and "300" not in release_three
    assert "37..300" in current and "Pocklington" in current
    assert r"2^{n-1}+1" in current and r"2^{n-1}+1" in previous
    assert r"q=r\,2^{\left\lceil\frac{n}{2}\right\rceil}+1" in current
    assert r"r \ge 2^{n-\left\lceil\frac{n}{2}\right\rceil-1}+1" in current
    assert registry.descriptor(teaching_dh_v1)["size_parameter"] is None
    rsa_mapping = registry.descriptor(rsa)["size_parameter"]["mapping_math"]
    assert r"\left\lceil\frac{n}{2}\right\rceil" in rsa_mapping
    assert r"\left\lfloor\frac{n}{2}\right\rfloor" in rsa_mapping
    assert r"e=65537" in rsa_mapping and "20 through 128" in rsa_mapping


def test_correctness_display_uses_the_actual_scheme_argument_in_all_releases():
    catalogue = registry.catalogue()
    dh = catalogue[0]
    for release in [dh, *dh["previous_releases"]]:
        argument = release["protocol"]["correctness_argument_math"]
        assert r"(g^x)^r \equiv g^{xr} \equiv (g^r)^x \pmod p" in argument
        assert "subgroup and nonidentity checks" in argument
        assert "nonunits" not in argument
    argument = catalogue[1]["protocol"]["correctness_argument_math"]
    assert r"ed \equiv 1 \pmod{(p-1)(q-1)}" in argument
    assert r"(z^e)^d \equiv z \pmod N" in argument
    assert "including nonunits" in argument and "Chinese remainder theorem" in argument

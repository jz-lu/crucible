"""Versioned human navigation over immutable protocol configurations.

A stage is an ordinal on one declared default progression, not a security unit
or an instance identifier. Membership and order are explicit review decisions:
changing either requires a new progression revision. Frozen protocol manifests
and their legacy ``instance_size`` fields are never rewritten here.
"""
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class _Progression:
    profile_ids: tuple[str, ...]
    rule: str
    parameter_space: str
    endpoint: str
    status: str
    limitations: str
    version: str = "1"
    selected_profile_ids: tuple[str, ...] | None = None
    selection_basis: str = "Every currently registered configuration is a default stage; no parameter or evidence is changed."


def _ids(prefix, count):
    """Spell out one frozen membership list; count is not a platform quota."""
    return tuple(f"{prefix}{number}" for number in range(1, count + 1))


_DEFAULTS = {
    ("teaching-dh-kem", "7"): _Progression(
        _ids("dh-v7-tier-", 50),
        "The 50 existing certified groups, ordered by increasing field size. "
        "Their historical sampling follows the frozen rho/NFS reference; 50 is not a required stage count.",
        "Certified prime field p, prime-order subgroup q, generator g and the declared secret-exponent interval.",
        "The existing 3072-bit field reference configuration.",
        "Reviewed configurations; progression and short-exponent fidelity remain qualified.",
        "The attack reference is incomplete. Short exponents change the secret distribution and require their own justification. "
        "These registered groups do not enumerate every admissible group or establish an optimal parameter frontier.",
    ),
    ("rsa-kem", "4"): _Progression(
        _ids("rsa-v4-tier-", 50),
        "The 50 existing modulus lengths from 32 through 3072 bits, ordered by length. "
        "Their historical sampling follows the frozen rho/NFS reference; 50 is not a required stage count.",
        "Balanced certified-prime modulus generation, modulus length and the fixed exponent e=65537.",
        "The existing 3072-bit modulus reference configuration.",
        "Reviewed configurations under a restricted factoring reference.",
        "Other factoring attacks and prime-generation distributions are outside the reference. "
        "The selected lengths do not enumerate every valid modulus length or establish global efficiency.",
    ),
    ("dhkem-x25519", "curve-compressed-v2"): _Progression(
        _ids("ec-compressed-v2-tier-", 50),
        "The 50 existing certified prime-order curves, ordered by group order. "
        "Their historical sampling follows a generic-rho reference; 50 is not a required stage count.",
        "Certified short-Weierstrass field and curve parameters, prime group order and generator, with full-range scalars and compressed points.",
        "The existing curve near the 128-bit generic-rho reference.",
        "Reviewed curve configurations under a generic attack reference.",
        "Curve-specific attacks and broader curve selection are not covered by generic rho. "
        "These are distinct research curves, not resized X25519; additional curves require separate certificates and review.",
    ),
    ("ml-kem", "packed-research-v3"): _Progression(
        _ids("module-lwe-v3-tier-", 11),
        "Ten default stages selected from 11 reviewed degree/rank configurations, ordered by increasing total dimension, "
        "with the smallest prime modulus satisfying the declared worst-case correctness rule. The intermediate configuration remains available.",
        "Polynomial degree n, module rank, modulus q, noise law and encoding; the current path fixes the declared noise and lossless encoding.",
        "The existing n=256, rank=3, q=24593 research configuration.",
        "Selected dimension path; joint parameter optimization unresolved.",
        "The flattened-LWE estimate omits ring/module and full-KEM effects; small settings remain unestimated. "
        "The ten-stage presentation does not enumerate every admissible setting. A fixed negligible failure bound could support other reviewed parameter tradeoffs.",
        selected_profile_ids=tuple(identifier for identifier in _ids("module-lwe-v3-tier-", 11)
                                   if identifier != "module-lwe-v3-tier-7"),
        selection_basis="Preserve the first configuration, every unestimated configuration, the first estimated configuration and the endpoint. "
        "Among remaining interior configurations, leave configuration 7 accessible off the default path: removing it creates the smallest merged adjacent reference gap, "
        "25.112 to 63.364. This is navigation sampling under an incomplete model, not evidence of equal difficulty or low scientific value.",
    ),
    ("frodokem", "anchor-research-v3"): _Progression(
        _ids("frodo-lwe-v3-tier-", 80),
        "Every matrix dimension n=8 through 640 in increments of 8, retaining the published Frodo640 parameter anchor.",
        "The current path varies matrix dimension while fixing the Frodo640 modulus, noise law, native message and salted transform.",
        "The published Frodo640 parameter anchor at n=640.",
        "Natural dimension progression with a common statistical correctness bound.",
        "All 80 settings meet the common failure bound; their actual failure probabilities need not be equal. "
        "The first 16 settings are unestimated. The lattice reference is not full-KEM security and this fixed-parameter path is not a global optimum.",
    ),
    ("sntrup", "3"): _Progression(
        _ids("sntrup-v3-tier-", 27),
        "Twenty-five default stages selected from the 27 existing certified tuples, which were selected from 103 candidates under the declared hybrid-inclusive planning reference. "
        "Both intermediate configurations remain available.",
        "Polynomial degree p, modulus q and weight w satisfying the declared ring, sampling and correctness constraints.",
        "The published sntrup653 tuple (653,4621,288).",
        "Finite candidate selection under a restricted attack model.",
        "The classical free-memory model is incomplete. The candidate set and selected targets are bounded design choices; "
        "the count is not universal and irregular parameter or encoding steps are expected.",
        selected_profile_ids=tuple(identifier for identifier in _ids("sntrup-v3-tier-", 27)
                                   if identifier not in {"sntrup-v3-tier-17", "sntrup-v3-tier-24"}),
        selection_basis="Preserve all unestimated introductory configurations, the first estimated configuration and both endpoints. "
        "Successively leave interior configurations 17 and 24 off the default path, choosing the smallest merged adjacent reference gap: "
        "34.651 to 44.019, then 96.506 to 107.454. Both remain accessible; these incomplete model references are not comparable security guarantees.",
    ),
    ("hqc", "growth-research-v2"): _Progression(
        _ids("hqc-growth-research-v2-tier-", 27),
        "Twenty-five default stages through equal weights w=wr=we from 1 through 27, choosing the smallest qualifying ring/code within the supported codec search. "
        "Weights 24 and 26 remain accessible as intermediate configurations.",
        "Secret, encryption and error weights, ring length and error-correcting code; this path fixes equal weights and the declared message size.",
        "Weight 27 at the current supported decoder/proof boundary.",
        "Partial progression; security frontier uncalibrated.",
        "Weight is not a security estimate. The current perfect-correctness search is restricted; it does not cover all HQC tradeoffs "
        "or reach a calibrated 128-bit endpoint. A common nonzero failure bound may permit a different reviewed path.",
        selected_profile_ids=tuple(identifier for identifier in _ids("hqc-growth-research-v2-tier-", 27)
                                   if identifier not in {"hqc-growth-research-v2-tier-24", "hqc-growth-research-v2-tier-26"}),
        selection_basis="Preserve the endpoints; successively leave weights 26 and 24 off the default path by choosing the smallest merged relative weight gap "
        "(27/25, then 25/23). Weight is a native parameter, not an estimated attack cost. Every weight remains available.",
    ),
    ("classic-mceliece", "length-research-v1"): _Progression(
        _ids("classic-mceliece-length-research-v1-tier-", 50),
        "The 50 existing byte-aligned code lengths from 840 through 3488, ordered by length with m=12 and t=64 fixed. "
        "Their historical sampling follows elementary Prange trial counts; 50 is not a required stage count.",
        "Field degree m, code length n and Goppa degree/error weight t; the current path varies only n.",
        "The published 348864 parameter anchor.",
        "Shortened-code path; joint parameter efficiency unresolved.",
        "Prange trials omit improved decoding, structural attacks and work per trial. "
        "Changing m, n and t jointly requires further implementation and attack review; changing the stage count alone would not resolve this limitation.",
    ),
}


def for_release(scheme_id, version, profiles):
    """Return a fresh description of the declared progression, or ``None``.

    Exact membership and legacy ordinal checks make accidental insertion,
    removal or renumbering fail closed. Caller ordering is immaterial: the
    immutable declared list determines navigation. Historical and named
    reference releases receive no invented default progression.
    """
    definition = _DEFAULTS.get((scheme_id, version))
    if definition is None:
        return None
    expected = definition.profile_ids
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("Default progression has invalid configuration membership")
    by_id = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("Default progression requires configuration manifests")
        identifier = profile.get("id")
        if not isinstance(identifier, str) or identifier in by_id:
            raise ValueError("Default progression requires unique configuration IDs")
        if (profile.get("scheme_id") != scheme_id or profile.get("version") != version
                or profile.get("profile_id", identifier) != identifier):
            raise ValueError("Default progression configuration identity differs from its release")
        by_id[identifier] = profile
    if set(by_id) != set(expected):
        raise ValueError("Default progression membership changed; a reviewed progression revision is required")
    for number, identifier in enumerate(expected, 1):
        original = by_id[identifier].get("instance_size")
        if type(original) is not int or original != number:
            raise ValueError("Frozen configuration ordinal changed; do not rewrite protocol manifests")
    selected = definition.selected_profile_ids or expected
    if (len(selected) != len(set(selected)) or not set(selected).issubset(expected)
            or tuple(identifier for identifier in expected if identifier in set(selected)) != selected
            or selected[0] != expected[0] or selected[-1] != expected[-1]):
        raise ValueError("Default stages must be an ordered configuration subset retaining both endpoints")
    membership = {"scheme_id": scheme_id, "scheme_version": version,
                  "progression_version": definition.version,
                  "configuration_ids": expected, "stage_configuration_ids": selected}
    return {
        "id": f"{scheme_id}/{version}/default-progression/v{definition.version}",
        "name": "Default progression",
        "version": definition.version,
        "stage_count": len(selected),
        "configuration_count": len(expected),
        "available_configuration_count": len(expected),
        "membership_sha256": hashlib.sha256(json.dumps(membership, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "additional_configuration_ids": [identifier for identifier in expected if identifier not in set(selected)],
        "stages": [{"number": number, "profile_id": identifier}
                   for number, identifier in enumerate(selected, 1)],
        "rule": definition.rule,
        "parameter_space": definition.parameter_space,
        "endpoint": definition.endpoint,
        "status": definition.status,
        "limitations": definition.limitations,
        "selection_basis": definition.selection_basis,
    }

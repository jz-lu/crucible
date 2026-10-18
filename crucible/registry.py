"""Trusted, explicit adapter releases and their common presentation contract.

Profile identifiers are globally namespaced across schemes. This invariant is
required by the existing profile incident and unique-round database keys. New
registrations must satisfy it; no submitted source is imported or executed.
"""
from urllib.parse import urlencode
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import re

from . import calibration, calibration_v2, dh_v3, dh_v4, dh_v5, dh_v6, kem, rsa, rsa_v2, rsa_v3
from .legacy import teaching_dh_v1
from . import dhkem_x25519, pq_mlkem, pq_frodokem, pq_hqc, pq_mceliece, sntrup, standard_schemes, research_schemes
from . import ec_research, code_hqc, code_mceliece, ntru_tiers, lattice_frodo, lattice_module
from . import lattice_module_v2, code_hqc_v2, ladder_designs
from . import dh_v7, rsa_v4, ec_compressed, lattice_module_v3, lattice_frodo_v2, ntru_frontier, lattice_frodo_v3
from . import progressions
from . import lattice_module_v4, code_hqc_v3
from . import security_references, display_notes

ADAPTERS = {
    (dh_v6.SCHEME_ID, dh_v6.SCHEME_VERSION): dh_v6,
    (rsa_v3.SCHEME_ID, rsa_v3.SCHEME_VERSION): rsa_v3,
    (dh_v5.SCHEME_ID, dh_v5.SCHEME_VERSION): dh_v5,
    (rsa_v2.SCHEME_ID, rsa_v2.SCHEME_VERSION): rsa_v2,
    (kem.SCHEME_ID, kem.SCHEME_VERSION): kem,
    (dh_v3.SCHEME_ID, dh_v3.SCHEME_VERSION): dh_v3,
    (dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4,
    (teaching_dh_v1.SCHEME_ID, teaching_dh_v1.SCHEME_VERSION): teaching_dh_v1,
    (rsa.SCHEME_ID, rsa.SCHEME_VERSION): rsa,
}
CURRENT = {kem.SCHEME_ID: dh_v6.SCHEME_VERSION, rsa.SCHEME_ID: rsa_v3.SCHEME_VERSION}
for _adapter in (dhkem_x25519, pq_mlkem, pq_frodokem, sntrup, pq_hqc, pq_mceliece):
    ADAPTERS[(_adapter.SCHEME_ID, _adapter.SCHEME_VERSION)] = _adapter
    CURRENT[_adapter.SCHEME_ID] = _adapter.SCHEME_VERSION
for _adapter in (ec_research, code_hqc, code_mceliece, ntru_tiers, lattice_frodo, lattice_module):
    ADAPTERS[(_adapter.SCHEME_ID, _adapter.SCHEME_VERSION)] = _adapter
    CURRENT[_adapter.SCHEME_ID] = _adapter.SCHEME_VERSION
for _adapter in (lattice_module_v2, code_hqc_v2):
    ADAPTERS[(_adapter.SCHEME_ID, _adapter.SCHEME_VERSION)] = _adapter
    CURRENT[_adapter.SCHEME_ID] = _adapter.SCHEME_VERSION
for _adapter in (dh_v7, rsa_v4, ec_compressed, lattice_module_v3, lattice_frodo_v2, ntru_frontier):
    ADAPTERS[(_adapter.SCHEME_ID, _adapter.SCHEME_VERSION)] = _adapter
    CURRENT[_adapter.SCHEME_ID] = _adapter.SCHEME_VERSION
ADAPTERS[(lattice_frodo_v3.SCHEME_ID, lattice_frodo_v3.SCHEME_VERSION)] = lattice_frodo_v3
CURRENT[lattice_frodo_v3.SCHEME_ID] = lattice_frodo_v3.SCHEME_VERSION
ADAPTERS[(lattice_module_v4.SCHEME_ID, lattice_module_v4.SCHEME_VERSION)] = lattice_module_v4
CURRENT[lattice_module_v4.SCHEME_ID] = lattice_module_v4.SCHEME_VERSION
ADAPTERS[(code_hqc_v3.SCHEME_ID, code_hqc_v3.SCHEME_VERSION)] = code_hqc_v3
CURRENT[code_hqc_v3.SCHEME_ID] = code_hqc_v3.SCHEME_VERSION
INITIAL_SECURITY_MODELS = {
    lattice_frodo_v3.INITIAL_SECURITY_MODEL["id"]: lattice_frodo_v3.INITIAL_SECURITY_MODEL,
    ntru_frontier.INITIAL_SECURITY_MODEL["id"]: ntru_frontier.INITIAL_SECURITY_MODEL,
    dh_v7.INITIAL_SECURITY_MODEL["id"]: dh_v7.INITIAL_SECURITY_MODEL,
    lattice_module_v3.INITIAL_SECURITY_MODEL["id"]: lattice_module_v3.INITIAL_SECURITY_MODEL,
    lattice_frodo_v2.INITIAL_SECURITY_MODEL["id"]: lattice_frodo_v2.INITIAL_SECURITY_MODEL,
    calibration.MODEL_ID: calibration.MODEL,
    calibration_v2.MODEL_ID: calibration_v2.MODEL,
    ec_research.INITIAL_SECURITY_MODEL["id"]: ec_research.INITIAL_SECURITY_MODEL,
    lattice_frodo.INITIAL_SECURITY_MODEL["id"]: lattice_frodo.INITIAL_SECURITY_MODEL,
    lattice_module.INITIAL_SECURITY_MODEL["id"]: lattice_module.INITIAL_SECURITY_MODEL,
    lattice_module_v2.INITIAL_SECURITY_MODEL["id"]: lattice_module_v2.INITIAL_SECURITY_MODEL,
}
# Only this explicit overlap may share progress. Receipts retain their source
# version; v1 is not included. The adapter, inputs, and KDF identity are tested.
COMPATIBLE = {(kem.SCHEME_ID, dh_v3.SCHEME_VERSION): {
    kem.SCHEME_VERSION: tuple(f"dh-{size}" for size in range(20, 37)),
}, (kem.SCHEME_ID, dh_v4.SCHEME_VERSION): {
    dh_v3.SCHEME_VERSION: tuple(f"dh-{size}" for size in range(20, 129)),
    kem.SCHEME_VERSION: tuple(f"dh-{size}" for size in range(20, 37)),
}}
COMPATIBILITY_BASIS = "The listed overlapping profiles have identical public parameters, honest key and encapsulation distributions, encodings, and KDF across these DH releases. Source releases and receipts remain separately identified."

DETAILS = {
    kem.SCHEME_ID: {
        "name": "Finite-field Diffie–Hellman",
        "summary": "Recover a shared secret from powers in a prime-order group.",
        "construction": "Key generation publishes g^x in a prime-order subgroup. Encapsulation sends g^r and derives the shared key from (g^x)^r. Decapsulation computes (g^r)^x and applies the same key derivation.",
        "public_input": "Prime p, subgroup order q, generator g, public key g^x, and ciphertext g^r; all integers are fixed-width hexadecimal.",
        "profile_metrics_note": "The public key and ciphertext each encode one group element. The registered field modulus, subgroup order, and generator are shared parameters and are excluded.",
        "example": "scripts/solve_teaching.py",
    },
    rsa.SCHEME_ID: {
        "name": "RSA over an integer modulus",
        "summary": "Recover a shared secret from an RSA ciphertext and a composite modulus.",
        "construction": "Key generation publishes N=pq and e=65537. Encapsulation samples z, sends c=z^e modulo N, and derives the shared key from z and the transcript. Decapsulation recovers z using the private exponent and applies the same key derivation.",
        "public_input": "Modulus N, exponent e=65537, public key N || e, and ciphertext c; all integers are fixed-width hexadecimal.",
        "profile_metrics_note": "The public key contains the modulus and exponent, each padded to the full modulus byte width. The ciphertext contains one modulus-width integer. These are this reference format's sizes, not DER or a minimal RSA encoding.",
        "example": "scripts/solve_rsa.py",
    },
}


for _scheme_id, _details in standard_schemes.SCHEMES.items():
    DETAILS[_scheme_id] = {**_details,
        "public_input": "The complete published public key and encapsulation, encoded as hexadecimal. Their exact binary formats and lengths are declared in the selected parameter profile.",
        "example": "scripts/inspect_standard.py"}


# Authored presentation facts for exact releases, not security ratings. Keep
# these separate from scheme-wide labels: a future release must not silently
# inherit statements about this implementation or its parameter ladder.
_DH_OVERVIEW = {
    "strengths": (
        "The public key and ciphertext each encode one group element; the group parameters are shared separately.",
        "Every new challenge independently samples the private exponent and encapsulation exponent in the registered prime-order group.",
    ),
    "limitations": (
        "Each configuration reuses its registered group. Group-specific preparation may be reusable across challenges and should be reported separately from online work.",
        "The configuration is the subgroup-order bit length, not estimated security bits. A 32-byte shared secret does not establish 256-bit security.",
        "The reference uses variable-time Python arithmetic and a custom transcript KDF; no IND-CPA or IND-CCA guarantee is claimed.",
    ),
    "security": "Finite-field discrete logarithm and computational Diffie–Hellman motivate the challenge. This adapter claims no security reduction or security level.",
}
OVERVIEWS = {
    (kem.SCHEME_ID, version): {
        **_DH_OVERVIEW,
        "correctness": f"For {scope}, honest parties compute the same group element and transcript hash, giving agreement with failure probability 0 under the specified arithmetic and encoding. Independent implementation review is pending.",
    }
    for version, scope in {
        "1": "the five legacy configurations 20, 24, 28, 32, and 36 in release 1",
        "2": "every registered configuration from 20 through 36 in release 2",
        "3": "every registered configuration from 20 through 128 in release 3",
        "4": "every registered configuration from 20 through 300 in release 4",
    }.items()
}
OVERVIEWS[(rsa.SCHEME_ID, "1")] = {
    "strengths": (
        "Key generation independently samples primes for each key; the modulus is not a shared scheme-wide parameter.",
        "The public key encodes the modulus and exponent; the ciphertext encodes one modulus-width integer.",
    ),
    "limitations": (
        "This is a custom transcript-KDF construction, not a standardized RSA-KEM implementation; no IND-CPA or IND-CCA guarantee is claimed.",
        "The configuration is the modulus bit length, not estimated security bits. A 32-byte shared secret does not establish 256-bit security.",
        "The reference uses variable-time Python arithmetic. Public-key lengths include a full-width padded exponent and are not minimal RSA encodings.",
    ),
    "correctness": "For every registered configuration from 20 through 128 in release 1, distinct primes and an invertible exponent give honest RSA inversion even for nonunits. Identical encodings and hashing give agreement with failure probability 0. Independent implementation review is pending.",
    "security": "RSA inversion motivates the challenge; factoring the modulus enables inversion, but equivalence is not claimed. This adapter claims no security reduction or security level.",
}


OVERVIEWS[(kem.SCHEME_ID, "5")] = {
    "strengths": _DH_OVERVIEW["strengths"],
    "limitations": (
        "Each configuration reuses a registered, exactly certified prime field and prime-order subgroup. Preparation may be amortized and must be reported separately.",
        "Configurations 1–50 are frozen initial classical rho/NFS references. Their estimates are heuristic, especially at small sizes, and do not measure online time or quantum resistance.",
        "The reference uses variable-time Python arithmetic, explicitly structured public setup and a custom transcript KDF; no IND-CPA or IND-CCA guarantee is claimed.",
    ),
    "correctness": "For all 50 registered configurations, exact primality certificates and prime-order checks establish the algebraic assumptions. Honest parties compute the same group element and transcript hash, with agreement failure probability0 under exact arithmetic and encoding. Independent review is pending.",
    "security": "The initial classical reference models generic subgroup discrete logarithm and normalized finite-field NFS including setup. It is not a security lower bound or an exhaustive attack estimate. No quantum or KEM confidentiality claim is made.",
}
OVERVIEWS[(rsa.SCHEME_ID, "2")] = {
    "strengths": OVERVIEWS[(rsa.SCHEME_ID, "1")]["strengths"],
    "limitations": (
        "Fresh balanced factors use an explicit structured recursive certified-prime distribution, not uniform sampling over all primes.",
        "Configurations 1–50 are frozen initial classical rho/NFS references. ECM, quadratic sieve, special-purpose attacks and finite-size constants are not included; estimates are rough.",
        "This variable-time Python custom-KDF construction is not standardized RSA-KEM and claims no IND-CPA, IND-CCA or quantum security.",
    ),
    "correctness": "For every configuration 1–50 and successfully generated certified-prime key, RSA inversion and identical transcript hashing give honest agreement with failure probability0 under exact arithmetic. A bounded prime-search abort returns no challenge and is not a correctness failure. Independent review is pending.",
    "security": "The initial reference models balanced-factor rho and normalized NFS, not all known factoring algorithms, RSA inversion complexity or general KEM confidentiality. The reference stays fixed when new attacks are recorded.",
}


OVERVIEWS[(kem.SCHEME_ID, "6")] = deepcopy(OVERVIEWS[(kem.SCHEME_ID, "5")])
OVERVIEWS[(rsa.SCHEME_ID, "3")] = deepcopy(OVERVIEWS[(rsa.SCHEME_ID, "2")])
OVERVIEWS[(kem.SCHEME_ID, "7")] = deepcopy(OVERVIEWS[(kem.SCHEME_ID, "6")])
OVERVIEWS[(kem.SCHEME_ID, "7")]["strengths"] = (
    "Certified groups retain the field sizes selected by the original rho/NFS reference.",
    "Explicit shorter exponent intervals reduce secret exponentiation work while retaining the declared interval-search margin.")
OVERVIEWS[(kem.SCHEME_ID, "7")]["limitations"] += (
    "Short exponents change the distribution; the margin is model-dependent and subgroup-validation cost remains unchanged.",)
OVERVIEWS[(rsa.SCHEME_ID, "4")] = deepcopy(OVERVIEWS[(rsa.SCHEME_ID, "3")])
OVERVIEWS[(rsa.SCHEME_ID, "4")]["strengths"] = (
    "The same fresh certified balanced-factor distribution with a compact three-byte exponent encoding.",
    "The modulus size is the smallest meeting each target under the declared restricted factoring model.")


def metadata_identity():
    """Bound caches to the exact registered adapters and metadata providers.

    These are presentation snapshots, not execution/admission verification.
    Replacing a registered adapter/provider invalidates the snapshot; deployment
    of changed frozen artifacts still requires a process restart and new pins.
    """
    return tuple((scheme_id, version, adapter, adapter.manifests,
                  getattr(adapter, "IMPLEMENTATION_SHA256", None))
                 for (scheme_id, version), adapter in sorted(ADAPTERS.items()))


@lru_cache(maxsize=64)
def _presentation_profiles(adapter, provider, source_sha256):
    return deepcopy(provider())


def presentation_manifests(adapter):
    """A fresh copy of immutable display data; never used to generate keys."""
    profiles = deepcopy(_presentation_profiles(adapter, adapter.manifests,
                                               getattr(adapter, "IMPLEMENTATION_SHA256", None)))
    for profile in profiles:
        fields = {key: profile[key] for key in ("algorithms", "parameters", "encoding", "kdf") if key in profile}
        if _has_unsafe_json_integer(fields):
            profile["exact_parameter_json"] = json.dumps(fields, indent=2, ensure_ascii=False)
    return profiles


def _has_unsafe_json_integer(value):
    """Browser JSON.parse rounds integers beyond Number's exact range."""
    if type(value) is int:
        return abs(value) > 2**53 - 1
    if isinstance(value, dict):
        return any(_has_unsafe_json_integer(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_unsafe_json_integer(item) for item in value)
    return False


def overview_for(scheme_id, version, *, first_manifest=None):
    """Return fresh bounded display metadata, without inheriting release claims."""
    if scheme_id in standard_schemes.SCHEMES:
        adapter = ADAPTERS[(scheme_id, version)]
        first = first_manifest if first_manifest is not None else presentation_manifests(adapter)[0]
        details = research_schemes.details_for(adapter, DETAILS[scheme_id], first_manifest=first)
        correctness = getattr(adapter, "CORRECTNESS_SPEC", first["correctness"])
        result = {"strengths": list(details["strengths"]), "limitations": list(details["limitations"]),
                "correctness": correctness["argument"] + " " + correctness.get("evidence_status", "Independent implementation review pending."),
                "security": first["security_claim"]}
        return {key: _configuration_prose(value) for key, value in result.items()}
    overview = OVERVIEWS.get((scheme_id, version), {
        "strengths": (), "limitations": (),
        "correctness": "No correctness overview has been reviewed for this release.",
        "security": "No security overview has been reviewed for this release.",
    })
    return {key: list(value) if isinstance(value, tuple) else value for key, value in overview.items()}


# Display-only TeX. The pinned manifests and plain-text protocol fields above
# remain the executable/reproducible contract. Versions select their own actual
# parameter mapping, rather than inheriting the current release's ladder.
MATH_DETAILS = {
    kem.SCHEME_ID: {
        "construction_math": r"Key generation publishes \(g^x\) in a prime-order subgroup. Encapsulation sends \(g^r\) and derives the shared key from \((g^x)^r\). Decapsulation computes \((g^r)^x\) and applies the same key derivation.",
        "public_input_math": r"Prime \(p\), subgroup order \(q\), generator \(g\), public key \(g^x\), and ciphertext \(g^r\); all integers are fixed-width hexadecimal.",
        "correctness_argument_math": r"For every admitted \(p,q,g\) and every honest \(x,r\), \((g^x)^r \equiv g^{xr} \equiv (g^r)^x \pmod p\). Both parties hash identical profile, parameters, public key, ciphertext, and fixed-width shared element, so their 32-byte outputs agree. Since \(q\) is prime and \(x,r\) are nonzero modulo \(q\), honest public values pass the subgroup and nonidentity checks.",
    },
    rsa.SCHEME_ID: {
        "construction_math": r"Key generation publishes \(N=pq\) and \(e=65537\). Encapsulation samples \(z\), sends \(c=z^e \bmod N\), and derives the shared key from \(z\) and the transcript. Decapsulation recovers \(z\) using the private exponent and applies the same key derivation.",
        "public_input_math": r"Modulus \(N\), exponent \(e=65537\), public key \(N \mathbin{\|} e\), and ciphertext \(c\); all integers are fixed-width hexadecimal.",
        "correctness_argument_math": r"For distinct primes \(p,q\) and \(ed \equiv 1 \pmod{(p-1)(q-1)}\), \(z^{ed} \equiv z\) modulo both \(p\) and \(q\) for every \(z\) in \(0,\ldots,N-1\), including nonunits, by Fermat's theorem and the zero case. The Chinese remainder theorem gives \((z^e)^d \equiv z \pmod N\). Identical transcript encodings and deterministic SHA-256 then give identical 32-byte outputs.",
    },
}
MAPPING_MATH = {
    (kem.SCHEME_ID, "6"): r"For \(t=1,\ldots,50\), let \(b(t)=8+120(t-1)/49\). The registered field has the smallest bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Exact certified \(p,q,g\) are pinned per configuration. Their initial reference is \(\min(\log_2(q)/2,G(L))\).",
    (rsa.SCHEME_ID, "3"): r"For \(t=1,\ldots,50\), let \(b(t)=8+120(t-1)/49\). Use the smallest modulus bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Generate fresh certified factors of \(\lceil L/2\rceil\) and \(\lfloor L/2\rfloor\) bits, requiring distinct primes, invertible \(e=65537\), and exactly \(L\)-bit product. Configuration 50 has \(L=3072\).",
    (kem.SCHEME_ID, "5"): r"For \(t=1,\ldots,50\), let \(b(t)=t+7\) for \(t\le25\), and \(b(t)=32+96(t-25)/25\) otherwise. The registered field has the smallest bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Exact certified \(p,q,g\) are pinned per configuration. Their initial reference is \(\min(\log_2(q)/2,G(L))\).",
    (rsa.SCHEME_ID, "2"): r"For \(t=1,\ldots,50\), use the shared initial target \(b(t)\) and smallest modulus bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\). Generate fresh certified factors of \(\lceil L/2\rceil\) and \(\lfloor L/2\rfloor\) bits, requiring distinct primes, invertible \(e=65537\), and exactly \(L\)-bit product. Configuration 50 has \(L=3072\). This is a limited classical reference model, not a security guarantee.",
    (kem.SCHEME_ID, "2"): r"For each supported \(n\), choose the first prime \(q \ge 2^{n-1}+1\) for which \(p=2q+1\) is prime, with \(g=4\). Only the registered \(n\) values 20 through 36 are admitted.",
    (kem.SCHEME_ID, "3"): r"For \(n=20,\ldots,36\), choose the first prime \(q \ge 2^{n-1}+1\) for which \(p=2q+1\) is prime, with \(g=4\). Sizes 20..36 retain the v2 constants. For 37..128, use the registered Pocklington-certified safe primes: \(q=r\,2^{\left\lceil\frac{n}{2}\right\rceil}+1\), for the first odd \(r \ge 2^{n-\left\lceil\frac{n}{2}\right\rceil-1}+1\) satisfying the published certificates for \(q\) and \(p=2q+1\). No prime search occurs when serving challenges.",
    (kem.SCHEME_ID, "4"): r"For \(n=20,\ldots,36\), choose the first prime \(q \ge 2^{n-1}+1\) for which \(p=2q+1\) is prime, with \(g=4\). Sizes 20..36 retain the v2 constants. For 37..300, use the registered Pocklington-certified safe primes: \(q=r\,2^{\left\lceil\frac{n}{2}\right\rceil}+1\), for the first odd \(r \ge 2^{n-\left\lceil\frac{n}{2}\right\rceil-1}+1\) satisfying the published certificates for \(q\) and \(p=2q+1\). No prime search occurs when serving challenges.",
    (rsa.SCHEME_ID, "1"): r"For each registered \(n\) from 20 through 128, generate distinct primes of \(\left\lceil\frac{n}{2}\right\rceil\) and \(\left\lfloor\frac{n}{2}\right\rfloor\) bits, with \(e=65537\) invertible modulo \((p-1)(q-1)\), and require their product \(N\) to have exactly \(n\) bits.",
}

# Audited legacy declarations: these exact registered DH releases serialize
# one fixed-width group element as each key/ciphertext. An element width is not
# a generic KEM key length; future adapters must declare their own byte counts.
PROFILE_METRIC_FIELDS = {
    (kem.SCHEME_ID, version): {
        "public_key_bytes": "group_element_bytes",
        "ciphertext_bytes": "group_element_bytes",
    } for version in ("1", "2", "3", "4")
}


def profile_metrics(manifest):
    """Read exact binary lengths without sampling, serializing JSON, or guessing.

    Explicit lengths take precedence, including an explicit unknown/variable
    declaration. Missing or noninteger lengths remain unknown rather than being
    inferred from a configuration, a hex-string length, or another protocol's convention.
    """
    encoding = manifest.get("encoding")
    if not isinstance(encoding, dict):
        encoding = {}
    rules = PROFILE_METRIC_FIELDS.get((manifest.get("scheme_id"), manifest.get("version")), {})
    result = {}
    for field in ("public_key_bytes", "ciphertext_bytes"):
        fallback_field = rules.get(field)
        value = encoding[field] if field in encoding else encoding.get(fallback_field) if fallback_field else None
        result[field] = value if type(value) is int and value >= 0 else None
    return result


def adapter_for(scheme_id=kem.SCHEME_ID, version=None):
    if scheme_id not in CURRENT:
        raise ValueError("Unknown scheme")
    version = CURRENT[scheme_id] if version is None else version
    try:
        return ADAPTERS[(scheme_id, version)]
    except KeyError:
        raise ValueError("Unknown scheme version") from None


def example_path(adapter):
    """Trusted examples bind to the release they can actually understand."""
    if (adapter.SCHEME_ID, adapter.SCHEME_VERSION) == ("ml-kem", "dimension-research-v2"):
        return "scripts/lattice_solve_module_v2_tier1.py"
    if (adapter.SCHEME_ID, adapter.SCHEME_VERSION) == ("hqc", "growth-research-v2"):
        return "scripts/hqc_growth_solve_tier1.py"
    overrides = {
        ("teaching-dh-kem", "7"): "scripts/solve_teaching_frontier.py",
        ("rsa-kem", "4"): "scripts/solve_rsa_frontier.py",
        ("ml-kem", "packed-research-v3"): "scripts/lattice_solve_module_v3_tier1.py",
        ("ml-kem", "compressed-research-v4"): "scripts/lattice_solve_module_v4_tier1.py",
        ("frodokem", "frontier-research-v2"): "scripts/lattice_solve_frodo_v2_tier1.py",
        ("frodokem", "anchor-research-v3"): "scripts/lattice_solve_frodo_v3_tier1.py",
        ("sntrup", "3"): "scripts/ntru_frontier_solve_tier1.py",
        ("hqc", "modeled-research-v3"): "scripts/hqc_model_solve_tier1.py",
    }
    return overrides.get((adapter.SCHEME_ID, adapter.SCHEME_VERSION), DETAILS[adapter.SCHEME_ID]["example"])


def manifest_for(profile_id, scheme_id=None, version=None):
    candidates = [adapter_for(scheme_id, version)] if scheme_id is not None else [
        adapter for (identifier, release), adapter in ADAPTERS.items()
        if release == (CURRENT.get(identifier) if version is None else version)]
    matches = []
    for adapter in candidates:
        try:
            matches.append(adapter.get_manifest(profile_id))
        except (KeyError, ValueError):
            continue
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous parameter profile")
    return matches[0]


def _native_parameter_label(profile):
    params, scheme = profile.get("parameters", {}), profile["scheme_id"]
    if scheme == "ml-kem":
        return f"n={params['n']}, rank={params['rank']}, q={params['q']}"
    if scheme == "frodokem":
        return f"n={params['n']}"
    if scheme == "sntrup":
        return f"p={params['p']}, q={params['q']}, w={params['w']}"
    if scheme == "hqc":
        return f"weight={params['secret_weight']}, n={params['n']}"
    if scheme == "classic-mceliece":
        return f"m={params['m']}, n={params['n']}, t={params['t']}"
    if scheme == "teaching-dh-kem":
        return f"{int(params['p'], 16).bit_length()}-bit field"
    return f"{profile['parameter_bits']}-bit " + ("modulus" if scheme == "rsa-kem" else "field")


def _attach_stages(profiles, progression):
    """Annotate display copies only; never alter a frozen protocol selector."""
    selected = {item["profile_id"]: item["number"] for item in progression["stages"]}
    anchors = [(index, selected[p["id"]]) for index, p in enumerate(profiles) if p["id"] in selected]
    for index, profile in enumerate(profiles):
        number = selected.get(profile["id"])
        native = _native_parameter_label(profile)
        profile.update(stage_number=number, native_parameter_label=native,
                       progression_id=progression["id"])
        if number is not None:
            profile.update(stage_label=f"Stage {number}", stage_position=number, stage_context=None)
        else:
            left_index, before = max((i, s) for i, s in anchors if i < index)
            right_index, after = min((i, s) for i, s in anchors if i > index)
            # Fractional position is navigation geometry, never an attack estimate.
            profile.update(stage_label=f"Configuration · {native}",
                           stage_position=before + (after - before) * (index - left_index) / (right_index - left_index),
                           stage_context={"before_stage": before, "after_stage": after})


def _configuration_prose(value):
    """Display vocabulary only; legacy specification bodies remain immutable."""
    if isinstance(value, list):
        return [_configuration_prose(item) for item in value]
    return re.sub(r"(?<![\w-])(Tiers|Tier|tiers|tier)(?![\w-])",
                  lambda match: {"Tiers": "Configurations", "Tier": "Configuration",
                                 "tiers": "configurations", "tier": "configuration"}[match[0]], value)


def descriptor(adapter):
    profiles = sorted(presentation_manifests(adapter), key=lambda item: item.get("instance_size", item["parameter_bits"]))
    first = profiles[0]
    progression = progressions.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION, profiles)
    if progression is not None:
        _attach_stages(profiles, progression)
    details = research_schemes.details_for(adapter, DETAILS[adapter.SCHEME_ID], first_manifest=first)
    details = {key: _configuration_prose(value) if key in {"summary", "construction"} else value
               for key, value in details.items()}
    if (adapter.SCHEME_ID, adapter.SCHEME_VERSION) == ("rsa-kem", "4"):
        details = {**details,
            "public_input": "N and e=65537; public key N || 010001, with N at modulus width and the exponent at three bytes; ciphertext c at modulus width.",
            "profile_metrics_note": "N uses modulus width and e uses three bytes. Ciphertext uses modulus width; JSON wrappers and shared conventions are excluded."}
    selector = urlencode({"scheme_id": adapter.SCHEME_ID, "version": adapter.SCHEME_VERSION})
    size_parameter = dict(adapter.SIZE_PARAMETER) if hasattr(adapter, "SIZE_PARAMETER") else None
    mapping_math = MAPPING_MATH.get((adapter.SCHEME_ID, adapter.SCHEME_VERSION))
    if size_parameter is not None and mapping_math is not None:
        size_parameter["mapping_math"] = mapping_math
    if progression is not None:
        size_parameter = {"name": "Stage", "symbol": "s", "unit": "stage",
                          "meaning": "Position on this scheme's default progression, not a security level",
                          "mapping": progression["rule"], "comparison_scope": progression["id"]}
    standard = first.get("profile_kind") == "standard-parameter-set"
    research_tier = first.get("profile_kind") == "research-tier"
    overview = overview_for(adapter.SCHEME_ID, adapter.SCHEME_VERSION, first_manifest=first)
    secret_lengths = sorted({p["shared_secret_bytes"] for p in profiles})
    return {**standard_schemes.beta_metadata(adapter.SCHEME_ID, first),
            "id": adapter.SCHEME_ID, "name": details["name"], "version": adapter.SCHEME_VERSION,
            "summary": details["summary"], "educational": first.get("educational", not standard), "profiles": profiles,
            "overview": overview,
            "default_progression": progression,
            "stage_range": {"min": 1, "max": progression["stage_count"], "step": 1} if progression else None,
            "ladder_design": ({**(ladder_designs.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION) or {}),
                               "progression": progression["rule"], "endpoint": progression["endpoint"]}
                              if progression else ladder_designs.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION)),
            "initial_security_model": deepcopy(getattr(adapter, "INITIAL_SECURITY_MODEL", INITIAL_SECURITY_MODELS.get((first.get("initial_security_estimate") or {}).get("model_id")))),
            "security_references": security_references.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION),
            "evidence_note": display_notes.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION),
            "profile_metrics": {profile["id"]: profile_metrics(profile) for profile in profiles},
            "profile_metrics_basis": {
                "unit": "bytes",
                "encoding": "Canonical binary encodings for this registered implementation, not optimized minima for the scheme family.",
                "excludes": "Hexadecimal and JSON wrappers, separately registered shared domain parameters, and descriptive metadata.",
                "notes": details.get("profile_metrics_note", "Lengths are shown only when explicitly declared by this registered implementation."),
            },
            "size_parameter": size_parameter,
            "instance_size_range": {"min": min(item.get("instance_size", item["parameter_bits"]) for item in profiles),
                                    "max": max(item.get("instance_size", item["parameter_bits"]) for item in profiles),
                                    "step": 1 if len(profiles) == profiles[-1].get("instance_size", profiles[-1]["parameter_bits"]) - profiles[0].get("instance_size", profiles[0]["parameter_bits"]) + 1 else None},
            "compatible_releases": [{"version": version, "profile_ids": list(profile_ids), "basis": COMPATIBILITY_BASIS}
                                    for version, profile_ids in COMPATIBLE.get((adapter.SCHEME_ID, adapter.SCHEME_VERSION), {}).items()],
            "protocol": {
                **({**MATH_DETAILS.get(adapter.SCHEME_ID, {}),
                    "public_input_math": r"Modulus \(N\), exponent \(e=65537\), public key \(N\,\|\,\mathtt{010001}\), and ciphertext \(c\). The exponent uses three bytes; \(N,c\) use modulus width."}
                   if (adapter.SCHEME_ID, adapter.SCHEME_VERSION) == ("rsa-kem", "4") else MATH_DETAILS.get(adapter.SCHEME_ID, {})),
                "construction": details["construction"],
                "assumption": first["assumption_context"],
                "public_input": details["public_input"],
                "shared_secret": (first["encoding"]["shared_secret"] if research_tier else f'{", ".join(map(str, secret_lengths))} bytes, according to the exact named parameter set; unmodified published KEM output.' if standard else f'{first["shared_secret_bytes"]} bytes, derived by the published transcript-bound SHA-256 KDF.'),
                "correctness": (overview["correctness"] if standard or research_tier else "Perfect honest agreement: failure probability 0 for the registered parameters and specified arithmetic and encoding. Independent implementation review is pending."),
                "source_url": f"/api/protocol/source?{selector}",
                "example_url": f"/api/protocol/example?{selector}",
                "correctness_url": f"/api/protocol/correctness?{selector}",
                **({"artifacts_url": f"/api/protocol/artifacts?{selector}"} if standard or research_tier else {}),
            }}


def catalogue():
    result = []
    for identifier in CURRENT:
        scheme = descriptor(adapter_for(identifier))
        scheme["previous_releases"] = [descriptor(adapter) for (scheme_id, version), adapter in ADAPTERS.items()
            if scheme_id == identifier and version != CURRENT[identifier]]
        result.append(scheme)
    return result


def catalogue_revision():
    """Identify immutable presentation metadata, never changing recovery records.

    As with presentation_manifests, deploy metadata edits by restarting workers.
    Adapter/provider replacement and changing the current release invalidate the
    fingerprint. Independent workers with identical catalogues produce one ID.
    """
    return _catalogue_revision(metadata_identity(), tuple(CURRENT.items()))


@lru_cache(maxsize=4)
def _catalogue_revision(identity, current):
    document = json.dumps(catalogue(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(document.encode()).hexdigest()


def comparison_versions(scheme_id, version, profile_id):
    """Current comparisons may include only an explicitly unchanged profile."""
    return [version] + [previous for previous, profiles in COMPATIBLE.get((scheme_id, version), {}).items()
                        if profile_id in profiles]


def participant_counts(session, releases):
    """Count submitted-attempt accounts for each displayed release in one query.

    Correct and incorrect answers both count; issuing/downloading an instance
    does not. Source profiles must belong to this release or its explicitly
    compatible subset. DISTINCT deduplicates accounts across profiles and rounds,
    and no unsuccessful account identities or receipt details leave this query.
    """
    from sqlalchemy import and_, case, distinct, func, or_, select
    from .db import Challenge, Submission

    columns, identities = [], []
    for release in releases:
        scheme_id, version = release["id"], release["version"]
        profiles = {profile["id"] for profile in release["profiles"]}
        eligible = [and_(Challenge.scheme_version == version, Challenge.profile_id.in_(profiles))]
        for source_version, declared_profiles in COMPATIBLE.get((scheme_id, version), {}).items():
            # Intersect both registered endpoints: compatibility cannot create a
            # profile that either release does not actually support.
            source = ADAPTERS.get((scheme_id, source_version))
            source_profiles = {profile["id"] for profile in presentation_manifests(source)} if source else set()
            shared = profiles.intersection(declared_profiles, source_profiles)
            if shared:
                eligible.append(and_(Challenge.scheme_version == source_version,
                                     Challenge.profile_id.in_(shared)))
        condition = and_(Challenge.scheme_id == scheme_id, or_(*eligible))
        columns.append(func.count(distinct(case((condition, Submission.user_id)))))
        identities.append((scheme_id, version))
    if not columns:
        return {}
    counts = session.execute(select(*columns).select_from(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .where(Submission.user_id == Challenge.user_id,
               Submission.profile_id == Challenge.profile_id,
               Submission.scheme_version == Challenge.scheme_version,
               Submission.round_id == Challenge.round_id)).one()
    return {identity: int(value) for identity, value in zip(identities, counts)}


def validate_registry():
    """Fail startup rather than alias two schemes into one legacy database key."""
    owners = {}
    for (scheme_id, version), adapter in ADAPTERS.items():
        if (adapter.SCHEME_ID, adapter.SCHEME_VERSION) != (scheme_id, version):
            raise ValueError("Adapter identity does not match its registry entry")
        for manifest in adapter.manifests():
            profile_id = manifest["id"]
            if (manifest["scheme_id"], manifest["version"]) != (scheme_id, version):
                raise ValueError("Manifest identity does not match its registered adapter")
            if profile_id in owners and owners[profile_id] != scheme_id:
                raise ValueError("Profile identifiers must be globally namespaced by scheme")
            owners[profile_id] = scheme_id
    for scheme_id, version in CURRENT.items():
        adapter_for(scheme_id, version)

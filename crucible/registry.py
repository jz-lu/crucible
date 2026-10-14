"""Trusted, explicit adapter releases and their common presentation contract.

Profile identifiers are globally namespaced across schemes. This invariant is
required by the existing profile incident and unique-round database keys. New
registrations must satisfy it; no submitted source is imported or executed.
"""
from urllib.parse import urlencode
from copy import deepcopy

from . import calibration, calibration_v2, dh_v3, dh_v4, dh_v5, dh_v6, kem, rsa, rsa_v2, rsa_v3
from .legacy import teaching_dh_v1

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
INITIAL_SECURITY_MODELS = {
    calibration.MODEL_ID: calibration.MODEL,
    calibration_v2.MODEL_ID: calibration_v2.MODEL,
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


# Authored presentation facts for exact releases, not security ratings. Keep
# these separate from scheme-wide labels: a future release must not silently
# inherit statements about this implementation or its parameter ladder.
_DH_OVERVIEW = {
    "strengths": (
        "The public key and ciphertext each encode one group element; the group parameters are shared separately.",
        "Every new challenge independently samples the private exponent and encapsulation exponent in the registered prime-order group.",
    ),
    "limitations": (
        "Each tier reuses its registered group. Group-specific preparation may be reusable across challenges and should be reported separately from online work.",
        "The tier is the subgroup-order bit length, not estimated security bits. A 32-byte shared secret does not establish 256-bit security.",
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
        "1": "the five legacy tiers 20, 24, 28, 32, and 36 in release 1",
        "2": "every registered tier from 20 through 36 in release 2",
        "3": "every registered tier from 20 through 128 in release 3",
        "4": "every registered tier from 20 through 300 in release 4",
    }.items()
}
OVERVIEWS[(rsa.SCHEME_ID, "1")] = {
    "strengths": (
        "Key generation independently samples primes for each key; the modulus is not a shared scheme-wide parameter.",
        "The public key encodes the modulus and exponent; the ciphertext encodes one modulus-width integer.",
    ),
    "limitations": (
        "This is a custom transcript-KDF construction, not a standardized RSA-KEM implementation; no IND-CPA or IND-CCA guarantee is claimed.",
        "The tier is the modulus bit length, not estimated security bits. A 32-byte shared secret does not establish 256-bit security.",
        "The reference uses variable-time Python arithmetic. Public-key lengths include a full-width padded exponent and are not minimal RSA encodings.",
    ),
    "correctness": "For every registered tier from 20 through 128 in release 1, distinct primes and an invertible exponent give honest RSA inversion even for nonunits. Identical encodings and hashing give agreement with failure probability 0. Independent implementation review is pending.",
    "security": "RSA inversion motivates the challenge; factoring the modulus enables inversion, but equivalence is not claimed. This adapter claims no security reduction or security level.",
}


OVERVIEWS[(kem.SCHEME_ID, "5")] = {
    "strengths": _DH_OVERVIEW["strengths"],
    "limitations": (
        "Each tier reuses a registered, exactly certified prime field and prime-order subgroup. Preparation may be amortized and must be reported separately.",
        "Tiers 1–50 are frozen initial classical rho/NFS references. Their estimates are heuristic, especially at small sizes, and do not measure online time or quantum resistance.",
        "The reference uses variable-time Python arithmetic, explicitly structured public setup and a custom transcript KDF; no IND-CPA or IND-CCA guarantee is claimed.",
    ),
    "correctness": "For all 50 registered tiers, exact primality certificates and prime-order checks establish the algebraic assumptions. Honest parties compute the same group element and transcript hash, with agreement failure probability0 under exact arithmetic and encoding. Independent review is pending.",
    "security": "The initial classical reference models generic subgroup discrete logarithm and normalized finite-field NFS including setup. It is not a security lower bound or an exhaustive attack estimate. No quantum or KEM confidentiality claim is made.",
}
OVERVIEWS[(rsa.SCHEME_ID, "2")] = {
    "strengths": OVERVIEWS[(rsa.SCHEME_ID, "1")]["strengths"],
    "limitations": (
        "Fresh balanced factors use an explicit structured recursive certified-prime distribution, not uniform sampling over all primes.",
        "Tiers 1–50 are frozen initial classical rho/NFS references. ECM, quadratic sieve, special-purpose attacks and finite-size constants are not included; estimates are rough.",
        "This variable-time Python custom-KDF construction is not standardized RSA-KEM and claims no IND-CPA, IND-CCA or quantum security.",
    ),
    "correctness": "For every tier 1–50 and successfully generated certified-prime key, RSA inversion and identical transcript hashing give honest agreement with failure probability0 under exact arithmetic. A bounded prime-search abort returns no challenge and is not a correctness failure. Independent review is pending.",
    "security": "The initial reference models balanced-factor rho and normalized NFS, not all known factoring algorithms, RSA inversion complexity or general KEM confidentiality. The reference stays fixed when new attacks are recorded.",
}


OVERVIEWS[(kem.SCHEME_ID, "6")] = deepcopy(OVERVIEWS[(kem.SCHEME_ID, "5")])
OVERVIEWS[(rsa.SCHEME_ID, "3")] = deepcopy(OVERVIEWS[(rsa.SCHEME_ID, "2")])


def overview_for(scheme_id, version):
    """Return fresh bounded display metadata, without inheriting release claims."""
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
    (kem.SCHEME_ID, "6"): r"For \(t=1,\ldots,50\), let \(b(t)=8+120(t-1)/49\). The registered field has the smallest bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Exact certified \(p,q,g\) are pinned per tier. Their initial reference is \(\min(\log_2(q)/2,G(L))\).",
    (rsa.SCHEME_ID, "3"): r"For \(t=1,\ldots,50\), let \(b(t)=8+120(t-1)/49\). Use the smallest modulus bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Generate fresh certified factors of \(\lceil L/2\rceil\) and \(\lfloor L/2\rfloor\) bits, requiring distinct primes, invertible \(e=65537\), and exactly \(L\)-bit product. Tier 50 has \(L=3072\).",
    (kem.SCHEME_ID, "5"): r"For \(t=1,\ldots,50\), let \(b(t)=t+7\) for \(t\le25\), and \(b(t)=32+96(t-25)/25\) otherwise. The registered field has the smallest bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\), where \(G(L)=128F(L)/F(3072)\) and \(F(L)=(L\ln2)^{1/3}[\ln(L\ln2)]^{2/3}\). Exact certified \(p,q,g\) are pinned per tier. Their initial reference is \(\min(\log_2(q)/2,G(L))\).",
    (rsa.SCHEME_ID, "2"): r"For \(t=1,\ldots,50\), use the shared initial target \(b(t)\) and smallest modulus bit length \(L\) with \(\min(L/4,G(L))\ge b(t)\). Generate fresh certified factors of \(\lceil L/2\rceil\) and \(\lfloor L/2\rfloor\) bits, requiring distinct primes, invertible \(e=65537\), and exactly \(L\)-bit product. Tier 50 has \(L=3072\). This is a limited classical reference model, not a security guarantee.",
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
    inferred from a tier, a hex-string length, or another protocol's convention.
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


def manifest_for(profile_id, scheme_id=None, version=None):
    candidates = [adapter_for(scheme_id, version)] if scheme_id is not None else [
        adapter for (identifier, release), adapter in ADAPTERS.items()
        if release == (CURRENT[identifier] if version is None else version)]
    matches = []
    for adapter in candidates:
        try:
            matches.append(adapter.get_manifest(profile_id))
        except (KeyError, ValueError):
            continue
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous parameter profile")
    return matches[0]


def descriptor(adapter):
    details = DETAILS[adapter.SCHEME_ID]
    profiles = sorted(adapter.manifests(), key=lambda item: item.get("instance_size", item["parameter_bits"]))
    first = profiles[0]
    selector = urlencode({"scheme_id": adapter.SCHEME_ID, "version": adapter.SCHEME_VERSION})
    size_parameter = dict(adapter.SIZE_PARAMETER) if hasattr(adapter, "SIZE_PARAMETER") else None
    mapping_math = MAPPING_MATH.get((adapter.SCHEME_ID, adapter.SCHEME_VERSION))
    if size_parameter is not None and mapping_math is not None:
        size_parameter["mapping_math"] = mapping_math
    return {"id": adapter.SCHEME_ID, "name": details["name"], "version": adapter.SCHEME_VERSION,
            "summary": details["summary"], "educational": True, "profiles": profiles,
            "overview": overview_for(adapter.SCHEME_ID, adapter.SCHEME_VERSION),
            "initial_security_model": deepcopy(INITIAL_SECURITY_MODELS.get(first.get("initial_security_estimate", {}).get("model_id"))),
            "profile_metrics": {profile["id"]: profile_metrics(profile) for profile in profiles},
            "profile_metrics_basis": {
                "unit": "bytes",
                "encoding": "Canonical binary public-key and ciphertext encodings.",
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
                **MATH_DETAILS[adapter.SCHEME_ID],
                "construction": details["construction"],
                "assumption": first["assumption_context"],
                "public_input": details["public_input"],
                "shared_secret": f'{first["shared_secret_bytes"]} bytes, derived by the published transcript-bound SHA-256 KDF.',
                "correctness": "Perfect honest agreement: failure probability 0 for the registered parameters and specified arithmetic and encoding. Independent implementation review is pending.",
                "source_url": f"/api/protocol/source?{selector}",
                "example_url": f"/api/protocol/example?{selector}",
            }}


def catalogue():
    result = []
    for identifier in CURRENT:
        scheme = descriptor(adapter_for(identifier))
        scheme["previous_releases"] = [descriptor(adapter) for (scheme_id, version), adapter in ADAPTERS.items()
            if scheme_id == identifier and version != CURRENT[identifier]]
        result.append(scheme)
    return result


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
            source_profiles = {profile["id"] for profile in source.manifests()} if source else set()
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

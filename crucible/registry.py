"""Trusted, explicit adapter releases and their common presentation contract.

Profile identifiers are globally namespaced across schemes. This invariant is
required by the existing profile incident and unique-round database keys. New
registrations must satisfy it; no submitted source is imported or executed.
"""
from urllib.parse import urlencode

from . import dh_v3, kem, rsa
from .legacy import teaching_dh_v1

ADAPTERS = {
    (kem.SCHEME_ID, kem.SCHEME_VERSION): kem,
    (dh_v3.SCHEME_ID, dh_v3.SCHEME_VERSION): dh_v3,
    (teaching_dh_v1.SCHEME_ID, teaching_dh_v1.SCHEME_VERSION): teaching_dh_v1,
    (rsa.SCHEME_ID, rsa.SCHEME_VERSION): rsa,
}
CURRENT = {kem.SCHEME_ID: dh_v3.SCHEME_VERSION, rsa.SCHEME_ID: rsa.SCHEME_VERSION}
# Only this explicit overlap may share progress. Receipts retain their source
# version; v1 is not included. The adapter, inputs, and KDF identity are tested.
COMPATIBLE = {(kem.SCHEME_ID, dh_v3.SCHEME_VERSION): {
    kem.SCHEME_VERSION: tuple(f"dh-{size}" for size in range(20, 37)),
}}
COMPATIBILITY_BASIS = "DH v2 and v3 have identical public parameters, honest key and encapsulation distributions, encodings, and KDF for sizes 20–36. Source releases and receipts remain separately identified."

DETAILS = {
    kem.SCHEME_ID: {
        "name": "Finite-field Diffie–Hellman",
        "summary": "Recover a shared secret from powers in a prime-order group.",
        "construction": "Key generation publishes g^x in a prime-order subgroup. Encapsulation sends g^r and derives the shared key from (g^x)^r. Decapsulation computes (g^r)^x and applies the same key derivation.",
        "public_input": "Prime p, subgroup order q, generator g, public key g^x, and ciphertext g^r; all integers are fixed-width hexadecimal.",
        "example": "scripts/solve_teaching.py",
    },
    rsa.SCHEME_ID: {
        "name": "RSA over an integer modulus",
        "summary": "Recover a shared secret from an RSA ciphertext and a composite modulus.",
        "construction": "Key generation publishes N=pq and e=65537. Encapsulation samples z, sends c=z^e modulo N, and derives the shared key from z and the transcript. Decapsulation recovers z using the private exponent and applies the same key derivation.",
        "public_input": "Modulus N, exponent e=65537, public key N || e, and ciphertext c; all integers are fixed-width hexadecimal.",
        "example": "scripts/solve_rsa.py",
    },
}


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
    (kem.SCHEME_ID, "2"): r"For each supported \(n\), choose the first prime \(q \ge 2^{n-1}+1\) for which \(p=2q+1\) is prime, with \(g=4\). Only the registered \(n\) values 20 through 36 are admitted.",
    (kem.SCHEME_ID, "3"): r"For \(n=20,\ldots,36\), choose the first prime \(q \ge 2^{n-1}+1\) for which \(p=2q+1\) is prime, with \(g=4\). Sizes 20..36 retain the v2 constants. For 37..128, use the registered Pocklington-certified safe primes: \(q=r\,2^{\left\lceil\frac{n}{2}\right\rceil}+1\), for the first odd \(r \ge 2^{n-\left\lceil\frac{n}{2}\right\rceil-1}+1\) satisfying the published certificates for \(q\) and \(p=2q+1\). No prime search occurs when serving challenges.",
    (rsa.SCHEME_ID, "1"): r"For each registered \(n\) from 20 through 128, generate distinct primes of \(\left\lceil\frac{n}{2}\right\rceil\) and \(\left\lfloor\frac{n}{2}\right\rfloor\) bits, with \(e=65537\) invertible modulo \((p-1)(q-1)\), and require their product \(N\) to have exactly \(n\) bits.",
}


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

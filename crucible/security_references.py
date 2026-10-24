"""Curated display evidence for exact research releases.

This companion neither changes frozen challenge manifests nor estimates unknown
configurations. Existing manifest references remain authoritative. A consumer
may use a supplemental point only when that exact profile has no frozen value
for the requested metric. Qualitative models deliberately have no points.

The word ``bits`` in a point is the logarithm of its identified model's work
quantity, not a universal security level, proven lower bound or measured time.
"""
from copy import deepcopy


FORMAT = "crucible-security-references-v1"


def _source(title, url):
    return {"title": title, "url": url}


def _metric(explanation, *, models=(), estimates=()):
    return {"explanation": explanation, "models": list(models), "estimates": list(estimates)}


_SHOR = _source(
    "Shor: polynomial-time quantum factorization and discrete logarithms",
    "https://arxiv.org/abs/quant-ph/9508027v2")
_EC_SHOR = _source(
    "Roetteler et al.: quantum circuits for prime-field elliptic-curve discrete logarithms",
    "https://arxiv.org/abs/1706.06752v3")
_KYBER = _source(
    "Kyber v3.02: Table 4, primal Core-SVP estimates (2021-08-04)",
    "https://pq-crystals.org/kyber/data/kyber-specification-round3-20210804.pdf#page=21")
_FIPS = _source(
    "FIPS 203: ML-KEM parameter sets, Table 2; changes from Kyber, Appendix C",
    "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf#page=48")
_FRODO = _source(
    "FrodoKEM security analysis: Table 6 and sections 7.2.2–7.2.3",
    "https://cic.iacr.org/p/2/3/25/pdf#page=25")


def _shor_metric(problem, *, elliptic_curve=False):
    return _metric(
        "A sufficiently capable quantum computer can use Shor's algorithm for "
        + problem + ", giving a polynomial-time recovery route. No configuration-specific "
        "quantum resource estimate is published here; an empty curve does not mean zero cost.",
        models=[{
            "id": "shor-polynomial-quantum-v1",
            "label": "Polynomial-time quantum recovery route",
            "unit": "Qualitative; no numerical work estimate",
            "scope": "The underlying " + problem + " problem for this exact release. "
                     "Recovering the private algebraic value is sufficient to compute the public-transcript KDF.",
            "assumptions": [
                "The quantum computation and reversible arithmetic can be executed with adequate accuracy.",
                "This is an algorithmic assessment; no physical device, error-correction budget or execution time is specified.",
            ],
            "limitations": [
                "Polynomial time is not zero work or a numerical security-bit estimate.",
                "Logical gates, logical qubits, physical qubits and wall-clock time require different explicit resource models.",
                "No curve is obtained by halving the classical reference.",
            ],
            "sources": [_SHOR, *([_EC_SHOR] if elliptic_curve else [])],
        }])


def _kyber_model(metric):
    return {
        "id": "kyber2021-primal-core-svp-" + metric,
        "label": "Published 2021 primal Core-SVP reference (" + metric + ")",
        "unit": "log2 Core-SVP work",
        "scope": "Only the n=256, q=3329, k=2, eta1=3, eta2=2, du=10, dv=4 endpoint. "
                 "Table 4 uses BKZ block size 406 and lattice dimension 999. "
                 "The correspondence is to the underlying lattice parameters, not to the complete ML-KEM transform.",
        "assumptions": [
            "One SVP call at BKZ block size beta, with cost 2^(0.292 beta) classically "
            "or 2^(0.265 beta) for the published quantum Grover-sieving model; exponential-memory access is free.",
            "The paper's heuristic treatment of the compression contribution applies to its attack analysis.",
        ],
        "limitations": [
            "Historical primal-only model; not a current exhaustive best-attack estimate or a proven lower bound.",
            "The original Kyber transform differs from ML-KEM. This parameter correspondence transfers no full-KEM security proof.",
            "Core-SVP exponents are not physical gate counts or measured time. The same table's refined classical gate estimate uses a different model.",
            "NIST category 1 is not converted into a universal 128-bit scalar, and no value is extrapolated to reduced degrees.",
        ],
        "sources": [_KYBER, _FIPS],
    }


_FRODO_QUANTUM_MODEL = {
    "id": "frodo-qrw-core-svp-quantum-v1",
    "label": "Published quantum random-walk Core-SVP reference",
    "unit": "log2 modeled lattice work",
    "scope": "Only the n=640 Frodo endpoint. The value 123.0 is the smallest raw "
             "underlying-LWE estimate among Table 6's Q-RW-Sieve attack rows "
             "(uSVP 123.0, BDD 124.0, dual 129.3 and dual-sieve-FFT 128.3).",
    "assumptions": [
        "The paper's Gaussian-LWE approximation, BKZ/GSA and Core-SVP model apply.",
        "Quantum random-walk sieving includes the paper's dimensions-for-free and arithmetic-vector overhead, "
        "with free quantum memory and unlimited coherent circuit depth.",
    ],
    "limitations": [
        "An underlying lattice-attack reference, not a quantum IND-CCA bound or a security lower bound.",
        "The reduction-adjusted 119 value from another table is not used; it describes a different claim.",
        "Quantum-memory and circuit-depth feasibility need separate assessment; this is not physical gate count or wall time.",
        "No extrapolation to smaller dimensions, and no replacement of the differently modeled frozen classical curve.",
    ],
    "sources": [_FRODO],
}


_REFERENCES = {
    ("teaching-dh-kem", "7"): {
        "classical": _metric(
            "Every configuration has a frozen subgroup-rho, interval-search and normalized "
            "number-field-sieve reference. These are limited classical attack models, not "
            "a security lower bound or a measured runtime; fixed-group preprocessing needs separate accounting."),
        "quantum": _shor_metric("finite-field discrete logarithms"),
    },
    ("rsa-kem", "4"): {
        "classical": _metric(
            "Every configuration has a frozen balanced-factor rho and normalized number-field-sieve "
            "reference. It omits other factoring and inversion routes and does not certify this "
            "structured prime distribution or the full KEM."),
        "quantum": _shor_metric("integer factorization"),
    },
    ("dhkem-x25519", "curve-compressed-v2"): {
        "classical": _metric(
            "Every configuration has a frozen generic discrete-log reference, log2(q)/2 for its "
            "actual prime group order. Curve-specific algorithms, constants, memory tradeoffs "
            "and implementation attacks are outside that reference."),
        "quantum": _shor_metric("prime-field elliptic-curve discrete logarithms", elliptic_curve=True),
    },
    ("ml-kem", "compressed-research-v4"): {
        "classical": _metric(
            "The exact ML-KEM-512 endpoint has a published historical lattice-parameter attack reference. "
            "Reduced degrees have no calibrated estimate here; they do not inherit the endpoint's "
            "security or a scaled version of its number.",
            models=[_kyber_model("classical")],
            estimates=[{"profile_id": "module-lwe-v4-tier-6", "model_id": "kyber2021-primal-core-svp-classical", "bits": 118}]),
        "quantum": _metric(
            "A published quantum primal Core-SVP reference is available for the exact endpoint's "
            "lattice parameters. This model has no reviewed values for the five reduced-degree "
            "configurations, and it is not a full-KEM security bound.",
            models=[_kyber_model("quantum")],
            estimates=[{"profile_id": "module-lwe-v4-tier-6", "model_id": "kyber2021-primal-core-svp-quantum", "bits": 107}]),
    },
    ("frodokem", "anchor-research-v3"): {
        "classical": _metric(
            "The frozen lattice estimator supplies references for dimensions 136 through 640. "
            "Dimensions 8 through 128 encounter its small-block floor or unsupported regime, "
            "so their blank values are intentionally uncalibrated. The native message-space "
            "enumeration count uses different units and is not substituted for lattice work."),
        "quantum": _metric(
            "The published n=640 endpoint has a quantum random-walk sieving reference for the "
            "underlying LWE problem. Other research dimensions have not been assessed under "
            "this model; the single endpoint does not define an interpolated curve.",
            models=[_FRODO_QUANTUM_MODEL],
            estimates=[{"profile_id": "frodo-lwe-v3-tier-80", "model_id": "frodo-qrw-core-svp-quantum-v1", "bits": 123.0}]),
    },
    ("sntrup", "3"): {
        "classical": _metric(
            "Fourteen configurations have references from the pinned non-hybrid and hybrid attack "
            "search. The first thirteen small configurations are outside its supported calibration "
            "or hit a block-size floor. Exhaustive-input counts are reported separately, not used "
            "to fill these gaps with incompatible work units."),
        "quantum": _metric(
            "No reviewed quantum attack-cost model is attached to these exact research tuples and "
            "samplers. The existing classical hybrid reference cannot be converted by simply "
            "halving its exponent; missing values remain unknown."),
    },
    ("hqc", "modeled-research-v3"): {
        "classical": _metric(
            "The endpoint reproduces the August 2025 HQC-1 construction, historically submitted "
            "for NIST category 1. The October 2026 specification raises weights after new "
            "cryptanalysis; this release does not inherit that newer calibration. "
            "Attack-cost estimates for the endpoint and reduced configurations are still pending.",
            models=[{
                "id": "hqc2025-endpoint-reassessment-v1",
                "label": "Historical endpoint; current assessment pending",
                "unit": "Qualitative; no numerical work estimate",
                "scope": "The exact 2025 HQC-1 endpoint and its reduced research tuples, not the changed 2026 parameter sets.",
                "assumptions": ["An applicable attack assessment must use this release's actual weights, samplers and decoding problem."],
                "limitations": [
                    "A historical NIST category is not a current scalar attack-cost estimate.",
                    "The conditional honest-correctness calculation is not a confidentiality estimate or a security lower bound.",
                ],
                "sources": [
                    _source("HQC specification, 2025-08-22", "https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf"),
                    _source("HQC specification, 2026-10-12: updated parameters and attack analysis", "https://pqc-hqc.org/doc/hqc_specifications_2026_10_12.pdf"),
                ],
            }]),
        "quantum": _metric(
            "No reviewed quantum cost estimate is published for this exact research release. "
            "The historical endpoint category and the newer HQC parameter assessment are "
            "not numerical quantum estimates for these configurations."),
    },
    ("classic-mceliece", "length-research-v1"): {
        "classical": _metric(
            "Current attack-cost calibration is pending for this fixed-field, fixed-degree "
            "shortening study. Elementary Prange support-hit trials omit per-trial cost, "
            "improved decoding and structural attacks, so they are not plotted as security bits.",
            models=[{
                "id": "mceliece-structural-reassessment-20261017",
                "label": "Decoding and structural-attack assessment pending",
                "unit": "Qualitative; no numerical work estimate",
                "scope": "The registered Goppa-code distribution at m=12,t=64 and lengths 840 through 3488.",
                "assumptions": [
                    "Direct syndrome decoding and recovery of equivalent private structure must both be assessed.",
                    "Conditional structural-attack estimates require explicit heuristic, success, memory and preprocessing assumptions.",
                ],
                "limitations": [
                    "A public-code distinguisher is not by itself recovery of the shared key.",
                    "The published endpoint is an anchor, not a validated current 128-bit estimate for this study.",
                    "Do not equate Prange trials, arithmetic operations and measured attack time.",
                ],
                "sources": [
                    _source("Classic McEliece specification, 2022-10-23", "https://classic.mceliece.org/mceliece-spec-20221023.pdf"),
                    _source("Ghoshal et al.: structural distinction and heuristic recovery", "https://eprint.iacr.org/2026/1630"),
                    _source("Weis: improved structural recovery with explicit heuristic assumptions", "https://eprint.iacr.org/2026/1984"),
                ],
            }]),
        "quantum": _metric(
            "No reviewed quantum attack-cost model is attached to these exact shortened-code "
            "configurations. Quantum decoding estimates would need their own resource and "
            "success model alongside the relevant structural attacks."),
    },
}


def for_release(scheme_id, version):
    """Return fresh presentation evidence, or None for an unreviewed identity."""
    key = (scheme_id, str(version))
    metrics = _REFERENCES.get(key)
    if metrics is None:
        return None
    return {"format": FORMAT, "scheme_id": key[0], "version": key[1], "revision": 1,
            "metrics": deepcopy(metrics)}

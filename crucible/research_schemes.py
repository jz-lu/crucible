"""Authored presentation for explicitly versioned mathematical tier families.

Published references keep their original manifests. A family page is a browsing
container, not a claim that a research derivative is standard-conformant.
"""
SCHEMES = {
    "dhkem-x25519": {
        "name": "Elliptic-curve Diffie–Hellman",
        "summary": "Recover a shared secret on a registered prime-order elliptic curve.",
        "construction": "Research tiers use distinct certified short-Weierstrass curves, full uniform nonzero scalars, canonical full-point encodings and a transcript-bound SHA-256 KDF. This is a custom elliptic-curve DH construction, not X25519 or RFC 9180. The exact X25519 DHKEM release remains a published reference.",
        "strengths": ["Full-distribution curves spanning small public recovery challenges to a generic rho reference near 128 bits.", "Exact public primality and group-order certificates for every tier."],
        "limitations": ["Custom curves, full-point encoding and KDF differ from X25519 DHKEM; no standardized-KEM security claim.", "Generic rho references omit other attacks and quantum computation. Arithmetic is variable-time."],
    },
    "ml-kem": {
        "name": "Module-lattice encryption",
        "summary": "Explore a module-LWE research ladder alongside the published ML-KEM reference.",
        "construction": "Research tiers vary actual module-LWE parameters with full messages and noise distributions. Lossless ciphertexts permit a separately justified correctness argument. These research encodings and parameters are not FIPS 203 ML-KEM; exact published ML-KEM sets remain separately identified references.",
        "strengths": ["A structured-lattice comparison with plain LWE and NTRU families.", "Every tier specifies its modulus, dimensions, noise law, encoding and correctness scope."],
        "limitations": ["Research construction with different ciphertext encoding from ML-KEM; no inherited FIPS conformance or confidentiality theorem.", "Heuristic lattice estimates depend on an attack model and are especially uncertain at tiny costs."],
    },
    "frodokem": {
        "name": "FrodoKEM",
        "summary": "Explore a plain-LWE matrix-dimension ladder with the full Frodo noise distribution.",
        "construction": "Research tiers vary the matrix dimension of salted FrodoKEM-640-SHAKE while retaining its modulus, finite noise law, full message, salt and seed lengths. The 640-dimensional endpoint is checked against the published construction; smaller dimensions are explicitly experimental.",
        "strengths": ["Plain LWE offers a comparison without module/ring structure.", "An explicit analytical agreement bound covers all admitted dimensions."],
        "limitations": ["Smaller dimensions are not published FrodoKEM parameter sets.", "Attack estimates are heuristic; matrix and communication costs differ substantially from structured lattices."],
    },
    "sntrup": {
        "name": "Streamlined NTRU Prime",
        "summary": "Explore a certified polynomial-degree ladder ending at sntrup761.",
        "construction": "Research tiers use explicit admissible p, q and w in the published Streamlined NTRU Prime construction, with certified primality, irreducible x^p−x−1 and honest-correctness inequalities. Full-distribution uniform samplers and published encodings/transform are retained; smaller tuples are research parameters.",
        "strengths": ["An NTRU-style comparison with a different ring design.", "Perfect honest correctness for the registered admissible tuples; the endpoint is checked against sntrup761 vectors."],
        "limitations": ["Tier order is a parameter progression, not a proved monotone security scale.", "A standalone recovery is not a break of an SSH hybrid or a published production parameter set unless it targets that exact set."],
    },
    "hqc": {
        "name": "HQC",
        "summary": "Explore low-weight research challenges using the published HQC ring and decoder.",
        "construction": "Research tiers retain the August 2025 HQC-1 ring, encodings and RM/RS decoder while setting the secret, encryption and error weights to the tier number. The 27 admitted weights have a deterministic correctness bound. They are not published HQC security levels or the later October 2026 revision.",
        "strengths": ["A quasi-cyclic Hamming-code comparison with the lattice families.", "The admitted low weights allow an explicit worst-case honest-correctness argument."],
        "limitations": ["The ladder stops at weight 27; extending it requires a new correctness analysis.", "No calibrated security estimate or equivalence to a published HQC parameter set is asserted."],
    },
    "classic-mceliece": {
        "name": "Classic McEliece",
        "summary": "Explore a binary Goppa-code length ladder with the original decoder.",
        "construction": "Research tiers keep the m=12 field and t=64 Goppa degree while varying byte-aligned code length. The original decoding construction and KEM transform remain; the final length is the published 348864 endpoint. Smaller lengths are separately identified research parameters.",
        "strengths": ["A large-public-key, compact-ciphertext comparison with HQC.", "Goppa distance gives perfect honest correctness under valid generation and exact implementation."],
        "limitations": ["Elementary Prange trial counts exclude per-trial work, improved decoding and structural attacks; they are not security-bit estimates.", "Recent structural cryptanalysis matters. This research target is not a deployment recommendation."],
    },
}


RELEASE_DETAILS = {
    ("ml-kem", "dimension-research-v2"): {
        "summary": "Explore growing module-LWE dimensions alongside the published ML-KEM reference.",
        "construction": "Eleven research settings grow the polynomial degree and module rank, from degree 8 at rank 1 to degree 256 at rank 3. Each uses the smallest prime modulus satisfying the same worst-case zero-failure bound. Native message capacity grows with the degree; full noise and seed distributions are retained. This lossless construction is not FIPS 203 ML-KEM.",
        "strengths": ["Growing dimensions increase the actual transmitted data and polynomial arithmetic.", "Every profile has an explicit perfect-correctness argument and exact parameter mapping."],
        "limitations": ["Small native messages permit enumeration; tiny lattice-estimator floor cases have no numerical claim.", "A nonstandard lossless construction: no inherited ML-KEM confidentiality theorem or complete attack analysis."],
    },
    ("hqc", "growth-research-v2"): {
        "summary": "Explore increasing weights with a ring and error-correcting code sized for each tier.",
        "construction": "Research tiers grow the secret and encryption weights, choosing the shortest qualifying ring/code within an explicit Reed–Solomon and repeated Reed–Muller search. All tiers retain one deterministic zero-failure bound, full messages and seed distributions. These are experimental parameters derived from the August 2025 HQC construction, not named published HQC levels.",
        "strengths": ["Increasing weights require larger codes and rings in steps, with actual communication costs shown.", "Exact code-distance and noise-support bounds establish honest correctness for every registered profile."],
        "limitations": ["Minimal encoded size is only within the declared codec search; no global optimality claim.", "No calibrated security-bit estimate or equivalence to a published HQC parameter set is asserted."],
    },
}


RELEASE_DETAILS.update({
    ("sntrup", "3"): {
        "summary": "Certified NTRU Prime choices selected under a bounded hybrid-inclusive attack reference.",
        "construction": "Twenty-seven certified tuples retain the Streamlined NTRU Prime transform and perfect-correctness constraints. The finite search compares public-key plus ciphertext bytes under the declared classical non-hybrid and hybrid attack reference; tiny settings remain unestimated. The endpoint is the published sntrup653 parameter tuple, with an explicitly specified full-distribution research sampler.",
        "strengths": ["A reproducible search includes candidate alternatives and hybrid attacks that penalize sparse-secret shortcuts.", "Prime and irreducibility certificates and a fixed correctness bound cover every selected tuple."],
        "limitations": ["The attack model uses idealized free memory and omits structural, dual and quantum attacks; no global optimum or security lower bound.", "Published parameter equality does not itself make the research sampler and wrapper byte-identical to every published implementation."],
    },
    ("dhkem-x25519", "curve-compressed-v2"): {
        "summary": "Certified prime-order curves with compact public points.",
        "construction": "The same certified short-Weierstrass curves and full nonzero scalar distributions use lossless SEC1-style compressed points. A square-root operation recovers the omitted ordinate; the custom transcript KDF binds compressed encodings. These research curves and KDF are not X25519 or RFC9180.",
        "strengths": ["Approximately halves transmitted point sizes without changing the curve or scalar distribution.", "Exact prime and group-order certificates remain inspectable."],
        "limitations": ["Compression adds square-root decoding work, so this is a communication objective, not a claim of lower cost in every dimension.", "Generic rho omits specialized and quantum attacks; variable-time reference arithmetic is not a deployment library."],
    },
    ("ml-kem", "packed-research-v3"): {
        **RELEASE_DETAILS[("ml-kem", "dimension-research-v2")],
        "construction": "Eleven degree/rank settings retain full noise distributions and perfect honest correctness. Coefficients use their exact lossless bit width instead of padded16-bit storage. Native message capacity, matrix expansion and polynomial arithmetic remain explicit tradeoffs; these are not FIPS203 parameters or encodings.",
    },
    ("frodokem", "frontier-research-v2"): {
        "summary": "Natural matrix dimensions with a bounded cost-versus-reference search.",
        "construction": "Seventy-four dimensions n=8,16,...,592 retain the same Frodo noise, modulus, message and transform contracts. Each step increases actual matrix size. The search stops at the first dimension attaining the planning reference ceiling within this restricted domain. First16 settings have no supported numerical estimate; the exact published640-dimensional release remains available separately.",
        "strengths": ["A reproducible candidate sweep records all80 dimensions and stops before cost grows after the reference ceiling.", "Every profile meets the same declared honest-disagreement bound."],
        "limitations": ["The search fixes modulus, noise law, message and transform; this is not global Frodo optimization or a recommendation to replace published parameters.", "The lattice reference and message enumeration have distinct work units; the planning ceiling is not a security proof."],
    },
})


RELEASE_DETAILS[("frodokem", "anchor-research-v3")] = {
    "summary": "Natural matrix dimensions through the published Frodo640 reference endpoint.",
    "construction": "Eighty dimensions n=8,16,...,640 preserve the Frodo640 modulus, noise, native message and salted transform. The common honest-disagreement bound is 2^-133. The first 16 settings have no supported numerical estimate. The lattice reference is not capped by message entropy and is not a full-KEM security estimate.",
    "strengths": ["The published 640-dimensional reference remains the endpoint, with every natural intermediate dimension retained.", "All profiles meet the same correctness requirement; parameters are not changed to manufacture a security plotting plateau."],
    "limitations": ["A fixed-modulus/noise dimension path is not a joint optimization over all valid Frodo parameters.", "Reduced profiles are research instances. Lattice-estimator work and candidate-message encryptions have different units; neither tier labels nor the plotted curve establish comparable full-KEM security."],
}


def details_for(adapter, fallback, *, first_manifest=None):
    first = first_manifest if first_manifest is not None else adapter.manifests()[0]
    if first.get("profile_kind") != "research-tier":
        return fallback
    return {**fallback, **SCHEMES[adapter.SCHEME_ID],
            **RELEASE_DETAILS.get((adapter.SCHEME_ID, adapter.SCHEME_VERSION), {})}

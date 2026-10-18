"""Reviewed presentation of exact stage ladders, separate from frozen protocols.

These records explain parameter choices. They do not certify security or claim
that the implementations attain an optimal security/cost frontier.
"""
from copy import deepcopy


DESIGNS = {
    ("teaching-dh-kem", "6"): {
        "progression": "Larger prime fields and prime-order subgroups.",
        "cost": "Longer encoded group elements and larger arithmetic operands.",
        "correctness_bound": "0",
        "qualification": "The large subgroup supports simple exact primality certificates; its exponent size is not optimized for the nominal attack reference. Shared group setup is excluded from transmitted sizes.",
    },
    ("rsa-kem", "3"): {
        "progression": "Larger balanced RSA moduli with freshly generated certified factors.",
        "cost": "Longer ciphertexts and larger arithmetic operands.",
        "correctness_bound": "0",
        "qualification": "The current format pads e=65537 to the modulus width. Public-key bytes describe that format, not a compact RSA encoding. The rho/NFS reference omits other attacks.",
    },
    ("dhkem-x25519", "curve-research-v1"): {
        "progression": "Larger certified prime-order elliptic curves with full-range scalars.",
        "cost": "Longer point encodings and larger field/scalar arithmetic.",
        "correctness_bound": "0",
        "qualification": "Full affine point coordinates are sent; compressed encodings could be smaller. These are distinct research curves, not resized X25519 parameter sets.",
    },
    ("ml-kem", "dimension-research-v2"): {
        "progression": "Eleven dimension settings grow degree and module rank, including intermediate total dimensions of 192, 384 and 640. Each uses the smallest prime modulus satisfying the declared worst-case correctness bound.",
        "cost": "More coefficients to transmit and more polynomial arithmetic as dimension grows.",
        "correctness_bound": "0",
        "qualification": "The native message has min(degree, 256) bits; all messages are sampled from that full space. Message enumeration limits any attack reference. Tiny estimator-floor cases have no numerical estimate. The endpoint is not forced to 128 bits.",
    },
    ("frodokem", "dimension-research-v1"): {
        "progression": "Larger plain-LWE matrices, retaining the full noise distribution and message space.",
        "cost": "Larger public keys, ciphertexts and matrix arithmetic.",
        "correctness_bound": "2^-133",
        "qualification": "One common bound covers all dimensions; the smallest is additionally perfect. The last dimension is the published 640 anchor, not the smallest dimension attaining the displayed 128-bit cap.",
    },
    ("sntrup", "2"): {
        "progression": "Increasing polynomial degree and weight in certified admissible NTRU Prime tuples.",
        "cost": "More polynomial coefficients and larger arithmetic; prime-modulus choices can create small encoding dips.",
        "correctness_bound": "0",
        "qualification": "The endpoint is sntrup761. Admissibility and degree order do not prove monotone attack cost; search-space counts are not security estimates.",
    },
    ("hqc", "growth-research-v2"): {
        "progression": "Higher weights paired with the shortest qualifying ring/code among the declared Reed–Solomon and repeated Reed–Muller choices.",
        "cost": "Code and ring sizes grow in steps, increasing transmitted bytes and arithmetic. Adjacent weights can share a code size.",
        "correctness_bound": "0",
        "qualification": "The zero-failure requirement is fixed. Minimal size is only within the declared codec search, not all HQC designs. No calibrated security-bit estimate or published endpoint equivalence is asserted.",
    },
    ("classic-mceliece", "length-research-v1"): {
        "progression": "Longer binary Goppa codes with field degree 12 and error weight 64 fixed.",
        "cost": "Larger public matrices and more matrix work; syndrome ciphertext length stays fixed.",
        "correctness_bound": "0",
        "qualification": "The endpoint is the published 348864 configuration. Elementary Prange trial counts omit improved decoding, structural attacks and work per trial; they are not security bits.",
    },
    ("ml-kem", "modulus-research-v1"): {
        "progression": "Archived modulus ladder: rank 1 at the first profile, then fixed rank 3 with decreasing modulus.",
        "cost": "Most higher stages have smaller encodings. This was a deliberately weakened challenge path, not a representative security/cost ladder.",
        "correctness_bound": "0",
        "qualification": "Preserved for original evidence. Its stages are not mapped to the replacement dimension ladder.",
    },
    ("hqc", "weight-research-v1"): {
        "progression": "Archived weight ladder inside a fixed ring and decoder.",
        "cost": "Public-key and ciphertext sizes remain fixed; increasing weight mainly consumes unused decoding capacity.",
        "correctness_bound": "0",
        "qualification": "Preserved for original evidence. Its stages are not mapped to the replacement code-growth ladder.",
    },
}


# Parameter selection is a scoped design argument, not an optimality certificate.
DESIGNS.update({
    ("teaching-dh-kem", "7"): {
        "progression": "Retain certified field sizes selected by the frozen rho/NFS baseline; use explicitly sized secret-exponent intervals.",
        "cost": "Larger field elements and modular arithmetic. Within each field, shorter exponents reduce secret exponentiation work; subgroup validation still uses the full subgroup order.",
        "correctness_bound": "0",
        "qualification": "The public interval preserves approximately16 extra generic interval-search reference bits where the subgroup permits. This is a separately specified short-exponent variant, not full-range DH or RFC7919 compliance; no measured speedup or full attack coverage is claimed.",
    },
    ("rsa-kem", "4"): {
        "progression": "Use the smallest modulus bit length reaching each target in the restricted factoring reference, with the same certified balanced-factor distribution.",
        "cost": "Larger modulus arithmetic and ciphertexts. Public keys now encode e=65537 in three bytes instead of padding it to the modulus width.",
        "correctness_bound": "0",
        "qualification": "This removes representation waste without changing factor sizes or the mathematical RSA task. The rho/NFS baseline omits other factoring attacks and does not prove a global security/cost optimum.",
    },
    ("dhkem-x25519", "curve-compressed-v2"): {
        "progression": "Use the existing certified prime-order curves and full scalar distributions with lossless compressed points.",
        "cost": "One coordinate plus a parity byte instead of two coordinates; decoding adds a field square-root operation.",
        "correctness_bound": "0",
        "qualification": "The communication objective favors compression; computation may not. Curve choice itself is only justified under the declared generic-rho reference, not optimized over every curve family.",
    },
    ("ml-kem", "packed-research-v3"): {
        "progression": "Eleven degree/rank settings retain their mathematical parameters and use the exact lossless coefficient bit width.",
        "cost": "Smaller public keys and ciphertexts than padded16-bit storage. Dimension growth still increases polynomial work; packing has its own processing cost.",
        "correctness_bound": "0",
        "qualification": "Alternatives in degree/rank trade native-message capacity, matrix expansion and arithmetic. This improves representation within the declared construction; it is not a globally optimized lattice parameter frontier.",
    },
    ("frodokem", "frontier-research-v2"): {
        "progression": "Every natural matrix dimension from8 through592 in steps of8. The first16 stages remain unestimated; later candidates are compared under the pinned planning reference.",
        "cost": "Public-key/ciphertext bytes and matrix work grow with dimension. The ladder ends before further cost rises after the planning reference reaches its message-search ceiling.",
        "correctness_bound": "2^-133",
        "qualification": "The candidate sweep fixes modulus, noise law, message and transform. n592 is the first candidate meeting the restricted128-bit planning target, not a recommended replacement for published Frodo640. Enumeration and lattice-estimator work units differ.",
    },
})

SELECTIONS = {
    ("teaching-dh-kem", "7"): {
        "status": "Restricted-model choice",
        "objective": "Minimum field width under the baseline; then reduce secret-exponent work subject to a fixed interval-search margin.",
        "domain": "The fifty existing certified groups and full or bounded exponent intervals; public validation and correctness fixed.",
        "evidence": "docs/classical-parameter-frontier.txt; tests/test_classical_frontier_audit.py",
        "limitations": "No global group search, exhaustive attack model or measured runtime optimum.",
    },
    ("rsa-kem", "4"): {
        "status": "Restricted-model choice",
        "objective": "Minimize transmitted bytes with balanced certified factors and e=65537 fixed.",
        "domain": "Integer modulus lengths under the frozen rho/NFS reference; padded versus canonical three-byte e.",
        "evidence": "docs/classical-parameter-frontier.txt; tests/test_classical_frontier.py",
        "limitations": "Other factoring attacks and alternative key-generation distributions remain outside this search.",
    },
    ("dhkem-x25519", "curve-compressed-v2"): {
        "status": "Representation improved",
        "objective": "Minimize transmitted point bytes for the same curve and scalar distribution.",
        "domain": "Full-point versus SEC1-style compressed encoding of the registered curves.",
        "evidence": "docs/classical-parameter-frontier.txt; tests/test_classical_frontier_audit.py",
        "limitations": "Compression adds decoding work; no optimum across all curves or implementations is claimed.",
    },
    ("ml-kem", "packed-research-v3"): {
        "status": "Representation improved",
        "objective": "Reduce public-key and ciphertext bytes while preserving each profile's full mathematical contract.",
        "domain": "Lossless coefficient widths at fixed n/rank/q/noise; same-total-dimension alternatives audited for capacity and matrix costs.",
        "evidence": "docs/lattice-parameter-frontier.json; scripts/lattice_parameter_frontier.py",
        "limitations": "No exhaustive joint search over modulus, noise, compression and module structure, or complete attack model.",
    },
    ("frodokem", "frontier-research-v2"): {
        "status": "Restricted-model frontier",
        "objective": "Minimize public-key plus ciphertext bytes at each supported reference, with the same correctness envelope.",
        "domain": "All80 dimensions n=8..640 by8; modulus, noise law, message and transform fixed. Unestimated candidates are not ordered by guessed security.",
        "evidence": "docs/lattice-parameter-frontier.json; scripts/lattice_parameter_frontier.py",
        "limitations": "The joint Frodo design space and omitted attacks remain outside this bounded search. The planning ceiling is not measured security.",
    },
    ("hqc", "growth-research-v2"): {
        "status": "Security frontier uncalibrated",
        "objective": "Minimize encoded size at each chosen weight while retaining the deterministic correctness bound.",
        "domain": "The supported Reed–Solomon/repeated-Reed–Muller codecs and certified ring selection, not all HQC designs.",
        "evidence": "docs/hqc-growth.txt; scripts/hqc_growth_parameters.py",
        "limitations": "Weight is not a calibrated security level. Codec minimality does not establish efficiency at equal security; some adjacent profiles share byte sizes.",
    },
    ("classic-mceliece", "length-research-v1"): {
        "status": "Security frontier unresolved",
        "objective": "Expose a shortened-code research path while keeping perfect correctness.",
        "domain": "Byte-aligned n with m=12 and t=64 fixed; joint m/n/t optimization has not been established.",
        "evidence": "docs/code-ntru-ladder-review.txt",
        "limitations": "The current path is not certified efficient at equal security. Elementary Prange counts omit improved decoding and structural attacks.",
    },
    ("sntrup", "2"): {
        "status": "Parameter efficiency under revision",
        "objective": "Increasing admissible degree and weight with perfect correctness.",
        "domain": "The old prescribed weight curve and certified rings; not a security/cost optimum.",
        "evidence": "docs/code-ntru-ladder-review.txt",
        "limitations": "Known avoidable parameter choices and missing hybrid-attack coverage require a separately versioned revision.",
    },
}


def for_release(scheme_id, version):
    result = deepcopy(DESIGNS.get((scheme_id, version)))
    if result is not None:
        result["endpoint"] = ENDPOINTS.get((scheme_id, version))
        result["selection"] = deepcopy(SELECTIONS.get((scheme_id, version)))
    return result


ENDPOINTS = {
    ("teaching-dh-kem", "6"): "Initial target: roughly 128 bits under the declared rho/NFS reference; Stage 50 reaches that model anchor.",
    ("rsa-kem", "3"): "Initial target: roughly 128 bits under the declared rho/NFS reference; Stage 50 reaches that model anchor.",
    ("dhkem-x25519", "curve-research-v1"): "Initial target: roughly 128 bits under the generic-rho reference; other attacks are outside that estimate.",
    ("ml-kem", "dimension-research-v2"): "Initial target: roughly 128 reference bits. The first chosen natural setting above it is about 138 under the flattened-LWE model; this is not a full-KEM security claim.",
    ("frodokem", "dimension-research-v1"): "Initial target: roughly 128 reference bits. The final profile preserves the published 640-dimensional construction; its displayed reference is capped by message enumeration.",
    ("sntrup", "2"): "Provisional endpoint: published sntrup761 parameters. This ladder has no calibrated 128-bit attack reference.",
    ("hqc", "growth-research-v2"): "Provisional partial ladder: ends at the current decoder/proof boundary, not a calibrated 128-bit endpoint.",
    ("classic-mceliece", "length-research-v1"): "Provisional endpoint: published 348864 parameters. Elementary Prange trials do not establish a 128-bit security estimate.",
}


ENDPOINTS.update({
    ("teaching-dh-kem", "7"): "Initial target roughly128 reference bits; interval search is included. This remains a restricted classical reference.",
    ("rsa-kem", "4"): ENDPOINTS[("rsa-kem", "3")],
    ("dhkem-x25519", "curve-compressed-v2"): ENDPOINTS[("dhkem-x25519", "curve-research-v1")],
    ("ml-kem", "packed-research-v3"): ENDPOINTS[("ml-kem", "dimension-research-v2")],
    ("frodokem", "frontier-research-v2"): "First candidate n592 reaching the restricted128-bit planning ceiling. The published n640 reference remains separately available; this is not a deployment-security claim.",
})


DESIGNS[("sntrup", "3")] = {
    "progression": "Twenty-seven distinct certified tuples selected from a finite candidate set, using a classical hybrid-inclusive planning reference where supported.",
    "cost": "Select the smallest public-key plus ciphertext total reaching each reference target in the reviewed candidate domain. Prime choices and encodings can create genuine irregular steps.",
    "correctness_bound": "0",
    "qualification": "The search rejects sparse-secret choices that only appear strong when hybrid attacks are omitted. Tiny profiles remain unestimated; the published653 endpoint reaches about129 reference bits in this incomplete, free-memory model.",
}
SELECTIONS[("sntrup", "3")] = {
    "status": "Restricted-model frontier",
    "objective": "Minimize public-key plus ciphertext bytes under fixed perfect correctness and the declared classical attack reference.",
    "domain": "103 certified candidates; supported non-hybrid and hybrid sieving estimates, deduplicated reference targets and separately unestimated introductory settings.",
    "evidence": "docs/ntru-parameter-frontier.txt; scripts/ntru_frontier_parameters.py",
    "limitations": "Classical free-memory heuristic model, not all attacks or implementations. A narrower non-hybrid model picked a misleading sparse endpoint and was rejected.",
}
ENDPOINTS[("sntrup", "3")] = "Published sntrup653 tuple (653,4621,288), about129 bits under the restricted hybrid-inclusive classical reference; not a security lower bound."

DESIGNS[("frodokem", "anchor-research-v3")] = {
    "progression": "Every natural matrix dimension from 8 through 640 in steps of 8, preserving the published Frodo640 reference endpoint.",
    "cost": "Larger public keys, ciphertexts and matrix arithmetic as dimension grows.",
    "correctness_bound": "2^-133",
    "qualification": "Every profile meets one common statistical correctness bound; exact failure probabilities need not be identical. Lattice attack references are shown without a plotting cap, separately from the 128-bit native message space. The first 16 settings remain unestimated.",
}
SELECTIONS[("frodokem", "anchor-research-v3")] = {
    "status": "Published endpoint retained",
    "objective": "Faithful dimension scaling through the published Frodo640 anchor under a common correctness requirement.",
    "domain": "All 80 dimensions n=8..640 by 8, keeping the Frodo640 modulus, noise distribution, native message and salted transform.",
    "evidence": "docs/frodo-anchor-ladder.txt; tests/test_frodo_anchor_ladder.py",
    "limitations": "This fixes one natural parameter path, not a global efficiency optimum. The lattice estimator omits attacks and full-KEM effects; its work units differ from candidate-message encryptions.",
}
ENDPOINTS[("frodokem", "anchor-research-v3")] = "Published Frodo640 parameter anchor at Stage 80. Its lattice attack reference is not a full-KEM security level; a capped plot is not a reason to discard the endpoint."

DESIGNS[("ml-kem", "compressed-research-v4")] = {
    "progression": "Six power-of-two polynomial degrees, with ML-KEM-512's rank, modulus, noise parameters and compression fixed.",
    "cost": "More coefficients and polynomial work as degree grows; the endpoint has exactly 800-byte public keys and 768-byte ciphertexts.",
    "correctness_bound": "2^-128 under the explicitly stated model; stronger bounds at smaller degrees",
    "qualification": "The endpoint is exact FIPS 203 ML-KEM-512. Reduced degrees have separate hash domains and no inherited security level. The upper two bounds use a heuristic uniform-input compression model; this is not a proof of that model's applicability.",
}
SELECTIONS[("ml-kem", "compressed-research-v4")] = {
    "status": "Published endpoint; natural reduced degrees",
    "objective": "Preserve the published ML-KEM-512 construction and compression at the endpoint, with a shared honest-failure ceiling instead of mandatory zero failure.",
    "domain": "Degrees 8,16,32,64,128,256; fixed rank2, q3329, eta1=3, eta2=2, du10,dv4. Reduced degrees use full native message spaces and explicit parameter/purpose hash separation.",
    "evidence": "FIPS 203; docs/mlkem-correctness-candidates.txt; exact rational certificates; independent NIST ACVP endpoint vectors.",
    "limitations": "An explicit family-aligned path, not a global efficiency optimum or monotonic security proof. Small rings may have additional algebraic weaknesses. Model acceptance and implementation conformance remain separate reviews.",
}
ENDPOINTS[("ml-kem", "compressed-research-v4")] = "FIPS 203 ML-KEM-512 at Stage 6: published category1 assessment, byte-conformance tests, no independent implementation certification. Reduced degrees inherit no security estimate."

DESIGNS[("hqc", "modeled-research-v3")] = {
    "progression": "66 secret weights from 1 through 66, retaining the 2025 HQC-1 encryption/error-weight balance and full message, seed and salt spaces.",
    "cost": "Smallest public-key plus ciphertext footprint among supported codecs passing the conditional failure bound at each weight pair; costs grow in natural steps.",
    "correctness_bound": "2^-128 under the explicitly stated independent-noise surrogate and conservative-transfer conjecture",
    "qualification": "A conditional arithmetic certificate is not proof of the model's applicability. The endpoint exactly matches August 2025 HQC-1; its current security calibration remains pending.",
}
SELECTIONS[("hqc", "modeled-research-v3")] = {
    "status": "Conditional correctness frontier; historical published endpoint",
    "objective": "Minimize public-key plus ciphertext bytes at each fixed weight pair under the explicit conservative correctness acceptance rule.",
    "domain": "45 supported RS/RM codecs; the smallest primitive-prime ring above each code; secret weights 1..66 with wr=we=ceil(75w/66).",
    "evidence": "docs/hqc-model-v3.txt; scripts/hqc_model_parameters.py; exact rational certificates and native conformance checks.",
    "limitations": "No optimized cost at equal estimated security is claimed. Current attacks and correlated-noise model transfer remain distinct open assessments; large ring truncations and unsupported decoder extensions are excluded.",
}
ENDPOINTS[("hqc", "modeled-research-v3")] = "Exact August 2025 HQC-1 reference at n=17669 and weights 66/75/75. Historical category-1 submission; October 2026 parameters differ and current security calibration is pending."

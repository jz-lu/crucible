"""Versioned, human-readable policy; no benchmarks execute in the web process."""

from . import correctness_policy

METHOD = {
    "id": "reference-operations-v1",
    "version": 1,
    "title": "Reference implementation timings",
    "summary": "Measured costs of a named implementation on a named machine. These are implementation measurements, not intrinsic costs or security estimates.",
    "workload": "Each trial uses a fresh key pair, one honest encapsulation and its decapsulation. Time the three operations separately, then check that the shared secrets agree. Run sequentially in a warmed worker.",
    "includes": [
        "Fresh randomness, arithmetic, encoding and key derivation inside each measured operation.",
        "The checks performed by the declared reference API; the implementation and any validation limitations remain identified.",
        "Completed operation samples from failed as well as successful trials, with the trial outcome retained.",
    ],
    "excludes": [
        "Worker startup and source verification; worker initialization and end-to-end durations are recorded separately. Generating precomputed public parameters is outside this benchmark and has not been timed.",
        "Website requests, database work, JSON transport, network latency and the remaining TLS handshake.",
        "Attack time, concurrent server capacity, and claims about optimized or constant-time implementations.",
    ],
    "statistics": "The current reference run requests 3 warmup and 20 measured trials at every valid configuration of each supported implementation. Show the median and middle 50% (25th–75th percentiles, linear interpolation), retain raw nanosecond samples, and publish actual sample counts and all failures. The middle 50% is sample spread, not a confidence interval or a tail-latency guarantee. Filled points represent complete, agreeing runs. Hollow points may show successful-exchange medians when every planned trial finished and all other trials were bounded key-generation aborts, with explicit sample and abort counts. These medians condition on key generation succeeding; aborted-attempt durations were not recorded, so they do not measure total attempt latency. Other partial, failed or timed-out runs remain unplotted. Lines join adjacent complete-run measurements on ordered research progressions and stop at missing or incomplete configurations, including hollow points. They guide the eye, not predict unmeasured timings. Named parameter sets stay discrete.",
    "limits": "Each report declares its per-configuration wall-time budget: the initial sparse pilot used 30 seconds; the full configuration run allows 120 seconds. This changes the collection allowance, not the algorithms or their internal abort rules. A timeout is missing performance evidence, not a cryptographic failure or a maximum allowed scheme cost. Resource limits bound trusted benchmark jobs; they are not a sandbox for submitted code.",
    "correctness": correctness_policy.REQUIREMENT + " " + correctness_policy.SCOPE + " Require an explicit claimed correctness guarantee, parameter coverage, underlying assumptions and supporting reasoning. Conditional, model-based and conjectural claims are eligible; identify unproved links and abort conditions. New approval/admission requires a claimed honest-failure bound at most 2^-128, or perfect agreement; a statistical declaration uses a conservative exponent of at least 128. The policy identifier is optional. Claims may rest on meaningful, independently motivated unproved premises, not an assumption that simply restates the target. A proof gap or challenged premise undermines the justification but does not alone show the actual target is false. A looser upper bound or one failing sample does not by itself establish a failure probability above the target. Tests support implementation conformance; they cannot establish a negligible failure probability. Existing research releases have mathematical arguments and tests, with independent implementation review still pending. Correctness does not establish IND-CCA security.",
    "interpretation": "Local pilot results are provisional. Compare only compatible runs with the same environment and method, and account for language, optimization and API differences. A Python reference versus optimized native code is not a fair comparison of the underlying mathematics. There is no universal NIST or TLS millisecond cutoff. No latency ranking or performance admission threshold is imposed yet.",
    "tls_context": "The three measured KEM operations contribute to key establishment. A TLS handshake also includes other hybrid components where applicable, certificates, signatures and network round trips. Those costs are outside these measurements. The selected ML-KEM-512 reference supplies a published category-1 endpoint; it is not a claim that every challenge configuration provides the same security.",
    "baseline": {
        "name": "ML-KEM-512 (FIPS 203)",
        "public_key_bytes": 800,
        "ciphertext_bytes": 768,
        "shared_secret_bytes": 32,
        "note": "These are KEM byte lengths, not full TLS messages. ML-KEM-512 is the selected published category-1 endpoint; reduced configurations do not inherit its security assessment. The module-lattice endpoint graph measures the identified Python implementation. A separate pinned native reference run is retained as implementation evidence; language and optimization differences prevent direct mathematical cost comparisons. Standardized parameters alone do not supply a universal timing baseline.",
    },
    "sources": [
        {"title": "NIST SP 800-227: KEM recommendations", "url": "https://csrc.nist.gov/pubs/sp/800/227/final"},
        {"title": "NIST FIPS 203: ML-KEM", "url": "https://csrc.nist.gov/pubs/fips/203/final"},
        {"title": "RFC 10024: hybrid key agreement for TLS 1.3", "url": "https://www.rfc-editor.org/rfc/rfc10024.html"},
        {"title": "SUPERCOP: separate KEM operation benchmarks", "url": "https://bench.cr.yp.to/call-kem.html"},
    ],
}

CRUCIBLE — INITIAL KEM CATALOGUE SELECTION
Decision record, 2026-10-14
Scope approved by the user: complete reviews and update the isolated trial first.
New live challenges, runtime policy changes and admission awards are not in scope.

DECISION: EIGHT VISIBLE SCHEMES

Diffie–Hellman & RSA
  Finite-field Diffie–Hellman — existing custom Crucible benchmark, release 6.
  RSA encapsulation — existing custom Crucible benchmark, release 3.
  DHKEM (X25519) — RFC 9180's exact X25519/HKDF-SHA256 construction.
Lattice-based
  ML-KEM — FIPS 203, with the published potential corrections inspected.
  FrodoKEM — current salted construction; public draft -03, June 2026.
  Streamlined NTRU Prime — initially sntrup761, not the different LPRime KEM.
Code-based
  HQC — team's 2026-10-12 specification, not yet a live adapter.
  Classic McEliece — 2022-10-23 specification, dated research assessment.

The first two remain existing benchmarks. The other six are reference entries
selected for eventual implementation review, not approved executable schemes.
All eight constructions were previously identified. None becomes a Crucible
discovery or an awarded proposal merely by appearing in this catalogue.

WHY EIGHT, AND WHY THESE EIGHT?

The objective is an understandable research portfolio with meaningful comparisons,
not a ranking of production cryptography or a complete taxonomy of assumptions.
Eight leaves two possible slots without treating ten as a quota. An eighth
distinct experiment is more valuable than a tenth nearly duplicated slider.

Apply the same inclusion test to incumbents and future proposals:
  1. A precisely identifiable KEM, or explicitly scoped custom KEM benchmark.
  2. A substantial reason to study it: deployment, a different attack setting,
     a novel construction, or an especially informative comparison.
  3. An account of its closest existing neighbor and what it adds.
  4. Enough inspectable evidence to make a bounded selection decision, with
     unavailable sources and unverified claims recorded rather than hidden.
  5. A credible path to reproducible implementation and correctness review.

For live admission, all implementation and correctness gates must actually pass.
Selection can include a well-defined research target whose security assessment
is changing; it is not an endorsement for protecting real communications.

The marginal argument for each slot:

- Finite-field DH: shared public groups, discrete logarithms, and reusable
  preprocessing. Removing it loses the clearest comparison of preparation
  versus online attack work. Its custom construction must stay explicit.
- RSA: inversion/factoring with fresh moduli. Removing it loses a fundamental
  classical attack setting. Its role is a benchmark, not a claim about modern
  TLS key-exchange prevalence or RFC conformance of our current adapter.
- DHKEM (X25519): a precise elliptic-curve KEM around a widely deployed primitive.
  Removing it leaves classical coverage skewed toward older finite-field/RSA
  settings. It is not just a different bit size of the existing DH benchmark.
- ML-KEM: a standardized module-lattice baseline. Removing it misses a central
  current migration target and the most natural structured-lattice reference.
- FrodoKEM: plain-LWE versus algebraically structured lattices, with visible
  communication/computation tradeoffs. Removing it loses this comparison.
- Streamlined NTRU Prime: quotient-NTRU construction and different ring design,
  with real relevance through OpenSSH's hybrid deployment. Removing it leaves
  lattice entries concentrated on noisy linear-relation constructions.
- HQC: quasi-cyclic Hamming-code construction and statistical correctness.
  Removing it loses a selected non-lattice standardization direction.
- Classic McEliece: hidden Goppa structure, perfect honest decoding and very
  different public-key/ciphertext tradeoffs. Current structural attacks make it
  an unusually useful research case. Removing it would falsely equate all code
  KEMs with HQC, or imply that only schemes believed safe may be researched.

These are independent reasons for attention, not a proof of independent security
assumptions. Attacks and tooling can transfer within and between categories.
The individual reviews contain the primary sources supporting factual claims.

WHAT WE ARE DELIBERATELY NOT ADDING

Saber: the first deferred lattice candidate. Module-LWR and rounding are real
differences, but ML-KEM/Frodo/sntrup give more initial breadth. Reconsider for a
concrete rounding-versus-noise experiment or a maintained reviewed adapter.

NTRU-HPS/HRSS and NTRU LPRime: genuinely distinct, not interchangeable with
sntrup. A further NTRU comparison can merit a slot, but not simply a second
project name. Preserve the exact construction and its correctness argument.

BIKE: serious QC-MDPC alternative. Reconsider with a precise decoder/version,
failure model and experiment that adds to HQC/Classic, rather than another
quasi-cyclic row whose advertised probability we cannot operationally verify.

RQC/ROLLO and current rank-metric proposals: important uncovered direction.
The old submissions, revised parameters and newer constructions must not be
combined into a fictitious current KEM. Choose and review one coherent exact
construction before adding it. See code-coverage.txt.

Hybrids: show deployments such as X25519MLKEM768 and sntrup/X25519 as context
on their component pages. Do not multiply every possible pairing into catalogue
rows. A hybrid is a construction with its own combiner and proof obligations;
recovering one component does not generally recover its combined secret.
An experiment specifically about composition could justify a later entry.
RFC 10024 is the current TLS reference checked for this review:
https://www.ietf.org/ietf-ftp/rfc/rfc10024.html
OpenSSH's operator chronology: https://www.openssh.org/pq.html

P-256, X448, AES/SHAKE choices, security-category sizes, optimized backends:
related profiles/options where appropriate, not automatically another front-page
scheme or another scoring award. Curves still have different exact algorithm
identities, and their results must never silently inherit one another.

Isogeny, multivariate and other research directions: not represented by a vetted
current KEM in this pass. This is an explicit coverage limitation, not a claim
that those directions lack value or that their assumptions are equivalent to
one of our rows. Future novelty is eligible without prior mass deployment.
Signatures, symmetric encryption and the full TLS stack are outside KEM scope.

THE PRECEDENT FOR FUTURE PROPOSALS

A scheme page is a browsing unit. An immutable release/profile is an experiment.
A scoring lineage is an incentive boundary. These must be separate objects.
Names, folders and implementation rewrites do not determine new contribution
awards or independent security. Related versions can share a page without
sharing test results, hardness claims, correctness assurances or receipts.

Known versus Crucible-discovered records attributable construction origin.
Assumption origin is another field: a new construction may use known assumptions.
“New to the website” is not discovery. Origin, review status, deployment suitability
and whether a challenge exists are separate properties.

The existing intake is specification-first, with immutable revisions, explicit
manual review and trusted integration. Retain that model. The accompanying audit
vets the established rules against actual enforcement and states where policy
is only prose. Documentation was corrected where it still called old DH2 the
current implementation. No administrator approval has been fabricated here.

Correctness means agreement under a declared distribution, with perfect or
statistical assurance explicitly distinguished. A proof/analytical bound,
heuristic estimate and observed failure rate are different evidence. Neither
random testing nor “standardized” substitutes for our implementation checks.

Standard profiles do not imply a 1–50 challenge ladder. Any reduced family needs
its own valid parameters, distribution and correctness coverage. Preserve exact
standard names and do not describe reduced-tier recovery as breaking a standard.
The current service needs a statistical-failure policy before probabilistically
correct new adapters go live. Silent agreement-conditioned resampling is barred.

WHAT THE NUMERICAL GAME MEANS

The shared hourly game asks for the entire encapsulated shared secret of the
issued instance before the deadline. It is a submission window, not a measured
or enforced one-hour compute allowance. Reports need code/method, online effort,
parallel hardware and reusable preparation. A successful answer proves recovery
of that sample, not a general attack cost or distinguishing advantage; an unsolved
sample proves no security lower bound. Passive recovery does not cover all
chosen-ciphertext, side-channel, failure-oracle or compositional attacks.

FRESH EVIDENCE AND LIMITS

HQC's October 12 specification and the available August 2025 tagged code do not
yet form a matching implementation bundle. Classic McEliece has material recent
structural-attack claims; published full-size estimates remain impractical and
model-dependent, while toy-instance demonstrations are real but different.
The lattice review records new asymptotic work without converting it into a
claim of reduced concrete security for standardized parameters.

This is a sourced selection review, not exhaustive cryptanalysis or independent
verification of every proof. Source acquisition gaps and remaining implementation
gates are listed in each review. Read inaccessible standards as publication
metadata only, not as text we audited. Preserve dates and revisit new evidence.

DELIVERABLE MAP

finite-field-dh.txt, rsa.txt, dhkem-x25519.txt
ml-kem.txt, frodokem.txt, sntrup.txt, saber.txt
hqc.txt, classic-mceliece.txt
lattice-coverage.txt, code-coverage.txt
admission-and-coverage-challenge.txt — audit of established rules and enforcement
verification.txt — actual local test scope, command and hashes
source-index.txt — source URLs grouped by review; not an exhaustive bibliography
selection.json — machine-readable roster and decisions, not a runtime registry

Trial: outputs/classification-trial/index.html, with optional reviews.html.
The familiar all-visible shallow table remains. Six added references have honest
empty graphs; no invented estimates, scores, participants or standard-tier maps.

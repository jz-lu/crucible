"""Curated mathematical reading drafts for exact registered scheme releases.

These inert descriptions do not execute notes, import adapters, certify claims,
change challenge configuration, or promote publication/review authority. Exact
configuration facts continue to come from the registered profile manifests.
"""
from copy import deepcopy
from urllib.parse import urlsplit

FORMAT = "crucible-assumption-sheet-v1"
ROLES = frozenset({"hardness", "model", "derived", "reduction", "implementation"})
TARGETS = (
    {"id": "security", "title": "Shared-secret recovery",
     "statement": "Recover the complete encapsulated key from this release's public transcript. Stronger confidentiality games require their own argument.",
     "status": "challenge-objective",
     "limitations": ["An underlying hard problem alone does not establish IND-CPA or IND-CCA security for this construction."]},
    {"id": "correctness", "title": "Honest agreement",
     "statement": "The registered configuration states its claimed honest-agreement bound and conditions. Supporting algebra, model assumptions and implementation obligations are distinct.",
     "status": "configuration-specific-claim",
     "limitations": ["Correctness concerns fresh honest draws; a service that withholds disagreements must not substitute its issued-instance distribution."]},
)
LIMITATIONS = [
    "Partial, manually authored reading draft; inclusion is not mathematical review, a security certification or an exhaustive list of dependencies.",
    "Small research configurations are deliberately attackable. A hardness question here is not an assertion that every stage is secure.",
    "Links provide source context. Applying a published result to these exact research parameters and implementation requires a separate justification.",
    "Undermining a model assumption can invalidate an argument without proving the actual scheme violates its claimed bound. A looser upper bound is not a counterexample; one disagreement refutes perfect correctness but does not alone refute a statistical target.",
]


def _source(title, url):
    return {"title": title, "url": url}


def _claim(identifier, title, role, supports, summary, statement, scope, challenge, sources, limitations,
           *, purpose=None, depends_on=None, status=None):
    claim = {"id": identifier, "title": title, "role": role, "supports": list(supports),
             "summary": summary, "precise_statement": statement, "scope": scope,
             "challenge": challenge, "sources": list(sources), "limitations": list(limitations)}
    if purpose is not None:
        claim["purpose"] = purpose
    if depends_on is not None:
        claim["depends_on"] = list(depends_on)
    if status is not None:
        claim["status"] = status
    return claim


_FIPS = _source("NIST FIPS 203: ML-KEM algorithms and parameters", "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf")
_ACVP = _source("NIST ACVP reference vectors, pinned release", "https://github.com/usnistgov/ACVP-Server/tree/v1.1.0.41/gen-val/json-files")
_FRODO_PAPER = _source("FrodoKEM security analysis, appendices A and C", "https://cic.iacr.org/p/2/3/25/pdf")
_FRODO_CODE = _source("Pinned FrodoKEM reference source", "https://github.com/microsoft/PQCrypto-LWEKE/tree/a2f9dec8917ccc3464b3378d46b140fa7353320d")
_FRODO_SPEC = _source("FrodoKEM standard proposal, 2025-09-29", "https://frodokem.org/files/FrodoKEM_standard_proposal_20250929.pdf")
_HQC2025 = _source("HQC specification, 2025-08-22", "https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf")
_HQC2026 = _source("HQC specification, 2026-10-12", "https://pqc-hqc.org/doc/hqc_specifications_2026_10_12.pdf")

# Only exact identities are enrolled. Neither the current registry pointer nor a
# similarly named published construction silently inherits one of these sheets.
_SHEETS = {}
_REVISIONS = {}
_PRESENTATIONS = {}


def _validate_claims(claims):
    identifiers = set()
    for claim in claims:
        identifier = claim["id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("Assumption claim IDs must be nonempty and unique within a sheet.")
        identifiers.add(identifier)
        if claim["role"] not in ROLES or not claim["supports"] or not set(claim["supports"]) <= {"security", "correctness"}:
            raise ValueError("Unknown assumption role or supported target.")
        for field in ("title", "summary", "precise_statement", "scope", "challenge"):
            if not isinstance(claim[field], str) or not claim[field].strip():
                raise ValueError("A curated assumption needs explicit text and scope.")
        if not claim["limitations"] or any(not isinstance(item, str) or not item.strip() for item in claim["limitations"]):
            raise ValueError("State the limits of every curated claim.")
        if not claim["sources"]:
            raise ValueError("This draft requires a source context for every claim.")
        for source in claim["sources"]:
            url = urlsplit(source["url"])
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.port not in (None, 443) or not source["title"].strip()
                    or any(ord(char) <= 32 for char in source["url"])):
                raise ValueError("Use a direct HTTPS source URL without credentials or controls.")
        if "purpose" in claim and (not isinstance(claim["purpose"], str) or not claim["purpose"].strip()):
            raise ValueError("An authored purpose must explain the item's role.")
        if "status" in claim and (claim["status"] != "open-obligation" or claim["role"] != "reduction"):
            raise ValueError("Only an explicitly open reduction obligation has an authored status in this draft.")
    # These references guide reading only. Checking that an ID exists does not
    # check a proof, logical independence, sufficiency or applicability.
    for claim in claims:
        if "depends_on" in claim:
            links = claim["depends_on"]
            if (not isinstance(links, list) or any(not isinstance(link, str) for link in links)
                    or len(set(links)) != len(links) or claim["id"] in links
                    or not set(links) <= identifiers):
                raise ValueError("Reading references must identify distinct other items in this exact sheet.")


def _validate_presentation(presentation, claims):
    if not isinstance(presentation.get("lead"), str) or not presentation["lead"].strip():
        raise ValueError("A curated presentation needs a lead.")
    section_ids, linked_ids = set(), []
    sections = presentation.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("A curated presentation needs explicit sections.")
    for section in sections:
        if any(not isinstance(section.get(field), str) or not section[field].strip()
               for field in ("id", "title", "description")):
            raise ValueError("A presentation section needs a label and purpose.")
        if section["id"] in section_ids:
            raise ValueError("Presentation section IDs must be unique.")
        section_ids.add(section["id"])
        links = section.get("claim_ids")
        if not isinstance(links, list) or not links or any(not isinstance(link, str) for link in links):
            raise ValueError("A presentation section must identify its items.")
        linked_ids.extend(links)
    if len(set(linked_ids)) != len(linked_ids) or set(linked_ids) != {claim["id"] for claim in claims}:
        raise ValueError("Presentation sections must include each exact-sheet item once.")
    overview = presentation.get("overview")
    if overview is not None:
        if not isinstance(overview, dict) or any(
                not isinstance(overview.get(field), str) or not overview[field].strip()
                for field in ("goal", "scope", "boundary")):
            raise ValueError("A reading overview needs an explicit goal and scope.")
        groups = overview.get("groups")
        if (not isinstance(groups, list) or len(groups) != 3
                or [group.get("id") for group in groups if isinstance(group, dict)]
                != ["evidence", "assumptions", "gaps"]):
            raise ValueError("Separate existing evidence, remaining assumptions and gaps.")
        for group in groups:
            if not isinstance(group.get("title"), str) or not group["title"].strip():
                raise ValueError("A reading overview group needs a title.")
            items = group.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= 4:
                raise ValueError("Keep overview groups concise and explicit.")
            for item in items:
                if not isinstance(item, dict) or any(
                        not isinstance(item.get(field), str) or not item[field].strip()
                        for field in ("title", "text", "claim_id")):
                    raise ValueError("An overview statement needs text and an exact-sheet reference.")
                if item["claim_id"] not in linked_ids:
                    raise ValueError("Overview references must stay in this exact sheet.")


def sheet_for(scheme_id, version):
    """Return a fresh draft, or an explicit empty sheet for an uncurated release.

    The API should first resolve the exact registered adapter using
    registry.adapter_for(scheme_id, version); this function never selects a newer
    release or loads configuration/secret data. It is safe to cache static drafts
    only behind the same access gate as the corresponding catalogue.
    """
    version = str(version)
    key = (scheme_id, version)
    claims = _SHEETS.get(key)
    if claims is not None:
        _validate_claims(claims)
    result = {
        "format": FORMAT, "schema_revision": 1, "revision": _REVISIONS.get(key, 1),
        "scheme_id": scheme_id, "scheme_version": version,
        "curation_status": "draft" if claims is not None else "uncurated",
        "completeness": "partial",
        "introduction": ("The mathematical questions and dependencies behind this exact release. Open a statement to inspect its scope, sources and possible challenges."
                         if claims is not None else "An assumptions sheet has not been curated for this exact release."),
        "scope": {"kind": "exact-release", "configuration_source": "registered-profile-manifests"},
        "targets": list(TARGETS), "claims": claims or [],
        "limitations": LIMITATIONS if claims is not None else [
            "No claims have been copied from another version. Consult this release's original protocol and configuration manifests.",
            "An empty sheet means uncurated, not assumption-free or secure."]}
    presentation = _PRESENTATIONS.get(key)
    if claims is not None and presentation is not None:
        _validate_presentation(presentation, claims)
        result["presentation"] = presentation
    return deepcopy(result)

_SHEETS[("frodokem", "anchor-research-v3")] = [
    _claim("frodo-lwe", "Distinguishing noisy linear equations", "hardness", ["security"],
        "Tell noisy equations from random data under the selected parameters.",
        r"For uniform \(A\in\mathbb{Z}_q^{m\times n}\), \(s\) sampled from \(\chi^n\) and \(e\) from \(\chi^m\), distinguish \((A,As+e\bmod q)\) from \((A,u)\) for uniform \(u\). Here \(q=32768\) and \(\chi\) is the exact Frodo640 finite-CDF law. The published single-target PKE argument uses \(m=n\) and \(m=n+8\); \(m=n+8\) can cover both experiments.",
        r"The selected dimension and full distribution of anchor-research-v3; \(n=640\) is the published parameter anchor, while smaller dimensions are research targets.",
        "Give a distinguisher with explicit advantage and resources, or improve an applicable attack estimate. Secret recovery is sufficient attack evidence, not the weakest possible break.",
        [_FRODO_PAPER, _FRODO_SPEC],
        ["No numerical hardness bound or IND-CCA guarantee is asserted here for the reduced dimensions.", "The site's estimator sample-count convention is not the sample count in this theorem's premise."],
        purpose="Identifies the problem protecting confidentiality; honest agreement does not depend on its hardness.",
        depends_on=[]),
    _claim("frodo-expansion", "Hash idealizations for security", "model", ["security"],
        "A security proof models specified hash roles as random oracles.",
        "The published security analysis uses its stated random-oracle treatment of public-matrix expansion and the salted transform. Applying that argument requires matching the exact hash roles, domains, inputs and query model; concrete SHAKE is not literally a random oracle.",
        "Security analysis of this exact release. The public matrix seed is public; this is not an ordinary secret-seed pseudorandom-generator assumption. Correctness sampling is described separately.",
        "Find a hash-role or domain mismatch, or behavior of the concrete functions that invalidates the proposed security-model transfer.",
        [_FRODO_PAPER, _FRODO_CODE],
        ["Do not assume a matrix is independently uniform after fixing concrete SHAKE and its public seed.", "This premise alone proves neither hardness nor full-KEM security."],
        purpose="States the hash idealizations used by a security argument.",
        depends_on=[]),
    _claim("frodo-noise-model", "Sampling model for correctness", "model", ["correctness"],
        "The statistical calculation uses a specified ideal noise experiment.",
        r"For the tail calculation, entries of \(S,E,S',E',E''\) are sampled independently from the exact Frodo640 finite-CDF law \(\chi\). The concrete implementation expands finite seeds through SHAKE. Applying the ideal calculation to that implementation requires a justified sampling-model bridge, including its domains and repeated-input behavior; the two experiments are not distributionally identical.",
        r"Statistical agreement bounds for registered dimensions \(n>8\), averaged over fresh honest keys and encapsulation. At \(n=8\), the deterministic support argument needs the sampler's bounded support but not sample independence. No LWE hardness premise is used in either calculation.",
        "Check the exact finite-CDF law and expansion domains; analyze correlations or repeated inputs relevant to the residual tails, or derive a bound retaining those dependencies.",
        [_FRODO_PAPER, _FRODO_SPEC, _FRODO_CODE],
        ["Independent noise is a premise of this calculation, not a claim that the listed assumptions are logically independent.", "A sampling-model gap challenges the justification without by itself proving actual disagreement exceeds the target."],
        purpose="States the probability experiment used to calculate honest agreement.",
        depends_on=[]),
    _claim("frodo-agreement", "Noise stays inside the decoding boundary", "derived", ["correctness"],
        "A conditional calculation bounds disagreement for fresh honest exchanges.",
        r"The residual is \(Z=S'E-E'S+E''\). Failure requires some \(|Z_{ij}|\ge4096\). A Crucible exact rational Chernoff calculation and union bound over 64 coordinates gives probability at most \(2^{-133}\) for \(n\le640\) under the stated independent finite-CDF experiment. At \(n=8\), the deterministic support bound \(2316<4096\) gives perfect agreement.",
        r"Fresh honest keys and encapsulation, faithful arithmetic and encodings. The sampling-model link applies to the statistical result; \(n=8\) uses only bounded support. Neither result assumes LWE hardness. Independence across the 64 output coordinates is not needed for the union bound. Crucible certificate: docs/lattice-frodo-correctness.json; external papers provide mathematical context.",
        "Reproduce the certificate, check the residual algebra and rounding threshold, or establish a violation with the correct sampling scope.",
        [_FRODO_PAPER, _FRODO_SPEC],
        ["This is a derived result within stated premises, not an additional assumption or an experimentally measured rare-event rate.", "Generation aborts and implementation faults are recorded separately."],
        purpose="Gives the conditional honest-agreement result, rather than adding another assumption.",
        depends_on=["frodo-noise-model", "frodo-implementation"]),
    _claim("frodo-security-argument", "Applying a security proof to this release", "reduction", ["security"],
        "FrodoKEM has published conditional IND-CCA proofs. Their explicit application to Crucible's research profiles is still open in this sheet.",
        "A complete application must specify its security game, adversary resources and oracle access, identify the applicable published PKE and salted-KEM results, and check their hypotheses for this exact parameter tuple and transform. It must account for reduction losses, query bounds and failure-handling terms; a fresh-honest average disagreement bound alone does not discharge every CCA theorem's requirements. This sheet does not provide that application or establish IND-CPA, IND-CCA or a numerical shared-secret-recovery bound for every research profile.",
        "Each exact anchor-research-v3 profile separately. The published endpoint supplies source context; parameter resemblance or byte conformance does not discharge the application to reduced dimensions. Reading links identify relevant work, not a verified implication or a complete dependency list.",
        "Supply a scoped reduction/application argument with explicit losses, or identify a missing hypothesis, transform mismatch or attack outside its claimed game.",
        [_FRODO_PAPER, _FRODO_SPEC],
        ["Open proof obligation: no completed application is claimed by this card.", "A shared-secret recovery challenge supplies narrower evidence than an IND-CCA analysis; the absence of a site decapsulation oracle does not establish IND-CCA security."],
        purpose="Shows the remaining work needed for a precise full-scheme security claim.",
        depends_on=["frodo-lwe", "frodo-expansion", "frodo-agreement", "frodo-implementation"],
        status="open-obligation"),
    _claim("frodo-implementation", "The complete salted construction", "implementation", ["security", "correctness"],
        "The reference must preserve the specified randomness, encoding and decapsulation.",
        "Retain the full 128-bit message, 256-bit salt/noise seed, specified samplers, full ciphertext comparison and prescribed implicit rejection. Implement the pinned equations and hash framing faithfully.",
        "Exact v3 artifact; the 640-dimensional endpoint has independent native byte-conformance evidence, while smaller dimensions are research modifications.",
        "Compare independent implementations and inspect malformed-input handling, rounding, samplers and the transform.",
        [_FRODO_CODE, _FRODO_SPEC],
        ["Conformance is not a full proof, security certification or side-channel audit.", "The Python reference is variable-time; the challenge service exposes no decapsulation oracle."],
        purpose="Checks that the code implements the mathematics being analyzed.",
        depends_on=[]),
]

_REVISIONS[("frodokem", "anchor-research-v3")] = 3
_PRESENTATIONS[("frodokem", "anchor-research-v3")] = {
    "lead": "FrodoKEM has a published conditional security argument. The task here is to make its premises, supporting evidence and remaining work explicit.",
    "overview": {
        "goal": "The shared secret should look random even to an attacker who can obtain decapsulation results for other, attacker-chosen ciphertexts. This is the IND-CCA security goal.",
        "scope": "Published FrodoKEM supplies the starting argument. Crucible also includes smaller research configurations, whose security must be assessed separately.",
        "groups": [
            {"id": "evidence", "title": "What we have", "items": [
                {"title": "A published security proof", "claim_id": "frodo-security-argument",
                 "text": "FrodoKEM has conditional IND-CCA proofs in classical and quantum random-oracle models. These connect the full construction to underlying hardness and model hypotheses."},
                {"title": "A correctness calculation", "claim_id": "frodo-agreement",
                 "text": r"Crucible's calculation bounds honest disagreement by \(2^{-133}\) under its noise model, meeting the \(2^{-128}\) goal. The smallest profile has a zero-failure support argument."},
                {"title": "Implementation checks", "claim_id": "frodo-implementation",
                 "text": "The 640-dimensional endpoint has byte-for-byte conformance evidence against an independent native implementation."},
            ]},
            {"id": "assumptions", "title": "What we still assume", "items": [
                {"title": "LWE is hard at the chosen parameters", "claim_id": "frodo-lwe",
                 "text": "The security reduction does not prove this hardness. Small challenge profiles are deliberately attackable; attack estimates are not proven lower bounds."},
                {"title": "The idealizations fit the concrete scheme", "claim_id": "frodo-expansion",
                 "text": "Security uses random-oracle models; the statistical correctness calculation uses an ideal noise-sampling model. Concrete SHAKE is not literally either ideal experiment."},
            ]},
            {"id": "gaps", "title": "What is missing here", "items": [
                {"title": "A complete argument for each research profile", "claim_id": "frodo-security-argument",
                 "text": "Check the published theorem's hypotheses at the exact parameters, and state the resulting bound, attack resources and proof losses. Honest correctness alone does not cover every CCA failure term."},
                {"title": "Implementation assurance beyond conformance", "claim_id": "frodo-implementation",
                 "text": "The variable-time Python reference has no side-channel assurance. Conformance checks do not settle malformed-input behavior or establish a complete implementation proof."},
            ]},
        ],
        "boundary": "The live challenge tests shared-secret recovery. An unsolved challenge does not establish IND-CCA security, and completing the checks above would still leave the stated assumptions.",
    },
    "sections": [
        {"id": "details", "title": "The argument in detail", "description": "Precise statements, their scope, sources and ways to challenge them.",
         "claim_ids": ["frodo-lwe", "frodo-expansion", "frodo-noise-model", "frodo-agreement", "frodo-security-argument", "frodo-implementation"]},
    ],
}

_MODULE_NOISE_LAWS = (
    "CBD3 means the centered binomial distribution with parameter 3: add three independent fair 0/1 bits, "
    "then subtract the sum of three other independent fair bits. The result lies from -3 to 3, but is not "
    "uniform; values near zero are more likely. CBD2 uses two bits in each sum and ranges from -2 to 2, "
    "also nonuniformly. "
)

_SHEETS[("ml-kem", "compressed-research-v4")] = [
    _claim("module-lwe", "Noisy equations over a polynomial ring", "hardness", ["security"],
        "Distinguish noisy module equations from random data for the selected ring.",
        _MODULE_NOISE_LAWS + r"Work in \(R=\mathbb{Z}_{3329}[x]/(x^n+1)\): polynomial coefficients are reduced modulo 3329, with \(x^n=-1\). For a uniform public matrix \(A\in R^{2\times2}\), secret vector \(s\in R^2\), and noise vector \(e\in R^2\), sample each coefficient of \(s,e\) from CBD3. The question is whether \((A,As+e)\) can be distinguished from a uniform pair of the same shape. Encryption also uses CBD3 secrets and CBD2 errors; the exact transform and profile matter.",
        r"The six registered degrees \(n=8,16,32,64,128,256\). Only \(n=256\) is the ML-KEM-512 parameter endpoint; smaller rings are research targets with an \(n\)-bit native-message enumeration route.",
        "Give a distinguisher, algebraic attack or shared-secret recovery with exact parameters and stated resources.",
        [_FIPS],
        ["Smaller rings have different factorization; no hardness reduction to the endpoint is supplied.", "This question is not a numerical hardness bound or a full-KEM security guarantee."]),
    _claim("module-expansion", "Hash expansion and sampled noise", "model", ["security", "correctness"],
        "Statistical analysis uses ideal independently expanded noise with the exact laws.",
        _MODULE_NOISE_LAWS + "Statistical agreement bounds assume independent noise samples with these laws. The implementation obtains sampling bits from SHA3 hash functions and SHAKE, a deterministic function that expands an input into a chosen number of output bytes. Treating that expansion as ideal independent randomness is an explicit premise, not distributional identity.",
        r"Statistical bounds at \(n=32,64,128,256\). Support-based agreement at \(n=8,16\) does not need independence. Reduced profiles add purpose/parameter separation; the endpoint retains FIPS 203 framing.",
        "Find an expansion or domain-separation error, or assess a dependence relevant to the model bridge.",
        [_FIPS, _ACVP],
        ["Known-answer vectors do not establish cryptographic hash security or ideal-noise independence."]),
    _claim("module-rounding", "Rounding at the two largest degrees", "model", ["correctness"],
        "The two largest profiles additionally rely on an explicit rounding-error model.",
        r"The ciphertext has a vector component \(u\) and a polynomial component \(v\). Compression stores fewer bits per coefficient; a residual is the error after reconstructing a compressed coefficient. For \(n=128,256\), \(u\)-compression residuals are modeled as independent draws from the residual law for a uniform coefficient, jointly independent of the relevant secrets and noise. The \(v\)-rounding error remains deterministically bounded by 104; it is not assumed independent. Profiles \(n=32,64\) retain dependent \(u\)-rounding in a more conservative argument.",
        r"Only \(n=128,256\) require this extra premise. Coefficients use modulus \(q=3329\); rank 2 means two polynomials per vector. Compression stores \(d_u=10\) bits per coefficient of \(u\) and \(d_v=4\) of \(v\), with the specified centered-binomial noise laws. Lower-degree deterministic or conservative bounds have different hypotheses.",
        "Identify relevant correlations or derive a justified tail bound that preserves dependence.",
        [_FIPS, _source("Analysis of KEM decryption-failure models", "https://www.cryptojedi.org/papers/frodoec-20250831.pdf")],
        [r"A model mismatch challenges the justification without alone proving actual failure exceeds \(2^{-128}\).", "No result about ML-KEM-768 is inherited by this sheet."]),
    _claim("module-agreement", "A bound for each exact configuration", "derived", ["correctness"],
        "The claimed agreement goal is common; Crucible calculations differ by degree.",
        r"At \(n=8,16\) the support argument bounds every possible noise value and gives zero failure. At \(n=32,64\) the bounds are at most \(2^{-367}\) and \(2^{-193}\) under independent expanded noise. At \(n=128,256\) the additional rounding model gives at most \(2^{-236}\) and \(2^{-128}\). The integer decoding threshold 832 and worst-case rounding error 104 in the second ciphertext component \(v\) yield residual threshold 728 for the exact rational tail certificates.",
        "Average fresh honest exchange for the exact selected profile. The union bound adds individual coordinate-failure bounds to bound the chance that any coordinate fails; it does not require independence across output coordinates. The exact Crucible calculations are in docs/mlkem-correctness-candidates.txt, docs/mlkem-correctness-candidates.json and scripts/mlkem_correctness_certificate.py; the external sources do not state these reduced-degree bounds.",
        "Reproduce the certificate and inspect arithmetic, residual algebra and decoding boundaries; distinguish a premise gap from a demonstrated statistical violation.",
        [_FIPS, _source("Context for decryption-failure models", "https://www.cryptojedi.org/papers/frodoec-20250831.pdf")],
        ["These bounds are derived inside declared premises, not an unconditional theorem about fixed SHAKE or a worst-case guarantee for every key.", "The endpoint calculation is not a substitute for the published standard's complete security argument."]),
    _claim("module-implementation", "Faithful complete decapsulation", "implementation", ["security", "correctness"],
        "Arithmetic, packing, sampling and rejection must match the exact tuple.",
        r"Implement the generalized incomplete number-theoretic transform (NTT, a modular-arithmetic method for polynomial multiplication), unambiguous byte packing, compression, sampling and validation exactly as pinned. Re-encrypt the recovered message to check the ciphertext; implicit rejection means deriving a replacement shared secret if that check fails. The \(n=256\) endpoint is checked against independent NIST reference test vectors (fixed input/output examples); lower degrees are separately tested research code.",
        "The selected exact profile and artifact; no automatic extension to another ladder revision.",
        "Find an algebraic, serialization or validation discrepancy, or compare independent implementations.",
        [_FIPS, _ACVP],
        ["Tests are evidence, not FIPS certification, a complete implementation proof or a constant-time deployment guarantee."]),
]

_SHEETS[("hqc", "modeled-research-v3")] = [
    _claim("hqc-decoding", "Quasi-cyclic decoding at the selected weights", "hardness", ["security"],
        "The decoding questions depend on exact weights, parity and truncation.",
        r"One public-key relation is \(s=x+hy\) in \(\mathbb{F}_2[X]/(X^n-1)\), with independent weight-\(w\) supports \(x,y\). A random comparison must respect \(s(1)=w(1+h(1))\pmod 2\); ignoring this parity gives a trivial false break. Published arguments use parity-aware 2-DQCSD-P and parity/truncation-aware 3-DQCSD-PT; encryption also depends on the declared \(w_r,w_e\) distributions and truncation.",
        "The exact v3 tuple and retained 2025 transform. The final tuple matches 2025 HQC-1; smaller tuples are research targets, not asserted secure.",
        "Give an applicable distinguisher, decoding attack or shared-secret recovery with parity, truncation, weights and resources specified.",
        [_HQC2025, _HQC2026],
        ["No complete attack-cost calibration or automatic IND-CCA claim is supplied.", "The October 2026 parameters and cryptanalysis differ; the historical endpoint is not newly certified at 128 bits."]),
    _claim("hqc-noise-model", "A model for correlated decoding noise", "model", ["correctness"],
        "The exact calculation uses a surrogate whose conservativity remains a premise.",
        r"For residual \(xr_2+r_1y+e\), compute the exact one-coordinate marginal under independent uniform fixed supports. Model 1 substitutes independent Bernoulli coordinates with that marginal. The conjectural transfer premise says the complete adjusted model bound conservatively bounds the real decoder failure for this tuple. Actual cyclic products have correlations and fixed parity, so they are not literally IID.",
        "Each exact v3 tuple, ideal-XOF sampling and the retained sampler-bias correction. The current 2026 Model 1 is applied to the 2025 construction; transfer to reduced parameters is unproved.",
        "Analyze relevant correlations or concentrated error patterns, test surrogate conservativity, or derive a justified joint-distribution bound.",
        [_HQC2026],
        ["The arithmetic certificate does not prove this model-transfer premise.", "A model error or a looser upper bound does not alone establish actual failure beyond the claimed target."]),
    _claim("hqc-agreement", "A conditional certificate for every tuple", "derived", ["correctness"],
        "Crucible calculates a certificate for each tuple inside the declared model.",
        r"Exact rational arithmetic combines the improved repeated-RM bound with two-way and multiway ties, the outer RS tail, tie-coupling term \(\binom{n_1}{17}P_{\mathrm{tie}}^{17}\), and sampler-density factor \((1+n/2^{32})^{2w_r+w_e}\). Each tuple passes an integer comparison with \(2^{-128}\). No zero-failure claim is imposed.",
        "Average fresh honest exchange with uniform 128-bit messages and faithful native decoding; Crucible results and calculator: crucible/data/hqc_model_profiles.json and crucible/hqc_model_correctness.py. The external specification supplies model formulas, not certificates for these reduced tuples.",
        "Reproduce the certificates or identify a derivation, rounding, decoder or scope error; separate honest disagreement from resource and API faults.",
        [_HQC2026],
        ["This is a model-conditional upper bound, not an empirical rare-event measurement.", "The current specification's different Model 2 bound is neither this certificate nor a lower bound on actual failure."]),
    _claim("hqc-implementation", "The pinned construction and samplers", "implementation", ["security", "correctness"],
        "The reference must preserve the selected weights, code, randomness and transform.",
        "Retain full 128-bit messages and salts, 256-bit seeds, the 2025 salted transform, rejection-based key supports, the specified slightly biased encapsulation sampler, and full re-encryption with implicit rejection. Generation observes one honest exchange without agreement-conditioned resampling.",
        "Exact v3 native artifact. The endpoint has byte-conformance evidence with unmodified 2025 HQC-1; lower tuples stay within the reviewed codec domain.",
        "Find a parameter/source mismatch, decoder boundary failure, sampler discrepancy, malformed-input error or unintended secret reuse.",
        [_HQC2025, _source("Pinned HQC native implementation", "https://github.com/open-quantum-safe/liboqs/tree/5a1a854b0dc9f2141bdc771c555ee60c37950183/src/kem/hqc")],
        ["Native/API faults are not mathematical failure observations.", "Boundary tests and conformance do not establish scientific admission, side-channel security or the transform's complete IND-CCA hypotheses."]),
]

# Exact classical and code-family reading drafts.
_SHEETS.update({('teaching-dh-kem', '7'): [{'id': 'interval-dh-recovery',
                             'title': 'Recovering the hidden group element',
                             'role': 'hardness',
                             'supports': ['security'],
                             'summary': 'Security depends on the actual short-exponent DH problem in each fixed '
                                        'public group.',
                             'precise_statement': 'For the registered prime-order subgroup generated by g, sample '
                                                  'independent x,r uniformly from 1..M and reveal \\(A=g^x\\) and '
                                                  '\\(B=g^r\\). The algebraic recovery problem is to compute '
                                                  '\\(Z=g^{xr}\\). Here '
                                                  '\\(M=\\min(q-1,2^{\\lceil2b+32\\rceil}-1)\\), with b the '
                                                  "release's frozen reference. Recovering x, r or Z suffices to "
                                                  'compute the challenge key through its public KDF.',
                             'scope': 'The fixed, deterministically generated groups and interval distribution of '
                                      'release 7; successful recovery probabilities must be stated over its '
                                      'honest exponent draws. Exact release evidence: crucible/dh_v7.py. External '
                                      'references provide context, not certification of this release.',
                             'challenge': 'Recover Z or the key from public transcripts; investigate interval '
                                          'search, field-specific algorithms and reusable preprocessing for each '
                                          'shared group. Report preprocessing separately from per-instance work.',
                             'sources': [{'title': 'Short-exponent optimization background, RFC 7919 §5.2; not '
                                                   'this custom construction',
                                          'url': 'https://www.rfc-editor.org/rfc/rfc7919.html#section-5.2'}],
                             'limitations': ['No resource/success lower bound is established for this '
                                             'distribution; low tiers are intentionally breakable.',
                                             'DLP recovery is sufficient, not claimed equivalent to recovering '
                                             'the hashed key. These are not uniformly sampled groups or RFC 7919 '
                                             'profiles.']},
                            {'id': 'dh-honest-agreement',
                             'title': 'Why honest keys agree',
                             'role': 'derived',
                             'supports': ['correctness'],
                             'summary': 'The two parties compute the same element by an exact group identity.',
                             'precise_statement': 'The public certificates establish prime p and q, q dividing '
                                                  'p−1, and a nonidentity g with g^q=1. Thus g has order q. For '
                                                  'every permitted x,r, \\((g^x)^r=(g^r)^x\\) modulo p. Identical '
                                                  'canonical transcript encoding and deterministic hashing give '
                                                  'identical 32-byte keys.',
                             'scope': 'All honest draws in all registered profiles, conditional on the stated '
                                      'arithmetic, certificate verification and encoding being faithfully '
                                      'implemented. Malformed inputs and process aborts are separate cases. Exact '
                                      'release evidence: crucible/dh_v7.py. External references provide context, '
                                      'not certification of this release.',
                             'challenge': 'Find an accepted invalid parameter certificate, an arithmetic or '
                                          'encoding counterexample, or an honestly generated ciphertext whose two '
                                          'outputs differ.',
                             'sources': [{'title': 'Constructive primality and Pocklington certificates, Handbook '
                                                   'of Applied Cryptography, chapter 4',
                                          'url': 'https://cacr.uwaterloo.ca/hac/about/chap4.pdf'}],
                             'limitations': ['The identity is mathematical; executable fidelity is not '
                                             'independently formally verified.',
                                             'Hash collision resistance is unnecessary for honest agreement. '
                                             'Agreement proves no confidentiality.']},
                            {'id': 'dh-cost-reference',
                             'title': 'What the initial security curve assumes',
                             'role': 'model',
                             'supports': ['security'],
                             'summary': 'The curve compares a few classical attack references, not all possible '
                                        'attacks.',
                             'precise_statement': 'The displayed exponent is min(b,log2(M)/2), where b is the '
                                                  'rounded minimum of log2(q)/2 and 128 F(L)/F(3072), '
                                                  'L=bit_length(p), \\(F(L)=(L\\ln 2)^{1/3}[\\ln(L\\ln '
                                                  '2)]^{2/3}\\). It is a subgroup-rho, normalized '
                                                  'number-field-sieve and interval-search reference.',
                             'scope': 'A frozen classical reference in heuristic work units. The 3072-bit '
                                      'normalization and approximately 16-bit interval-search margin are design '
                                      'choices. Exact release evidence: crucible/dh_v7.py. External references '
                                      'provide context, not certification of this release.',
                             'challenge': 'Replace one model term with a justified analysis or reproducible '
                                          'attack; state success rate, work units, memory and preprocessing so '
                                          'the comparison is meaningful.',
                             'sources': [{'title': 'Conventional finite-field strength comparisons, NIST SP '
                                                   '800-57 Part 1 Rev. 5, Table 2',
                                          'url': 'https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-57pt1r5.pdf'}],
                             'limitations': ['Not a security lower bound, wall-clock prediction or exhaustive '
                                             'best-attack estimate.',
                                             'Structured-group attacks, finite-size constants, amortized setup, '
                                             'quantum attacks and the KEM reduction are excluded.']},
                            {'id': 'dh-kem-bridge',
                             'title': 'The remaining KEM security argument',
                             'role': 'reduction',
                             'supports': ['security'],
                             'summary': "Hardness of DH alone does not establish this exact KEM's IND-CCA "
                                        'security.',
                             'precise_statement': 'A full claim would need a reduction for this interval '
                                                  'distribution, canonical subgroup validation, and the exact '
                                                  "transcript-bound SHA-256 KDF. It must state the adversary's "
                                                  'decapsulation access, permitted ciphertexts, hash model and '
                                                  'quantitative reduction loss. No such proof is supplied for '
                                                  'release 7.',
                             'scope': 'The custom KEM, including malformed-input behavior; distinct from the '
                                      "site's passive single-transcript shared-key recovery challenge. Exact "
                                      'release evidence: crucible/dh_v7.py. External references provide context, '
                                      'not certification of this release.',
                             'challenge': 'Supply a matching game-based reduction, expose a missing premise, or '
                                          'give a distinguishing/chosen-ciphertext attack on the specified '
                                          'construction.',
                             'sources': [{'title': 'Explicit DHKEM construction and security scope for '
                                                   'comparison, RFC 9180',
                                          'url': 'https://www.rfc-editor.org/rfc/rfc9180.html'}],
                             'limitations': ['RFC 9180 analyzes different algorithms and does not certify this '
                                             'release.',
                                             'A random-oracle proof, if supplied, would remain conditional on its '
                                             'model; concrete SHA-256 and side-channel behavior need separate '
                                             'assessment.']}],
 ('rsa-kem', '4'): [{'id': 'rsa-inversion',
                     'title': 'Inverting this RSA distribution',
                     'role': 'hardness',
                     'supports': ['security'],
                     'summary': "The relevant problem is RSA inversion for this generator's structured primes.",
                     'precise_statement': "Generate \\(N=pq\\) with the release's bounded, balanced, recursively "
                                          'certified prime sampler and e=65537. For z uniform in 0..N−1 and '
                                          '\\(c=z^e\\bmod N\\), recover z from (N,e,c). Factoring N suffices to '
                                          'recover z and hence the transcript-derived key.',
                     'scope': 'Conditioned on successful key generation, including its exact rejection and '
                              'timeout rules. This is not the uniform distribution over all RSA primes or a claim '
                              'about every modulus of the same length. Exact release evidence: '
                              'crucible/rsa_v4.py. External references provide context, not certification of this '
                              'release.',
                     'challenge': 'Invert honest public instances; test factoring, exploitation of the '
                                  'prime-generation structure, and shortcuts that recover z or the final key '
                                  'without factoring.',
                     'sources': [{'title': 'RSA-KEM inversion background, RFC 9690',
                                  'url': 'https://www.rfc-editor.org/rfc/rfc9690.html'}],
                     'limitations': ['Factoring and RSA inversion are not asserted equivalent; neither is '
                                     'asserted equivalent to final-key recovery.',
                                     'No concrete lower bound is established, and low tiers are intentionally '
                                     'breakable.']},
                    {'id': 'rsa-honest-agreement',
                     'title': 'Why honest keys agree',
                     'role': 'derived',
                     'supports': ['correctness'],
                     'summary': 'CRT gives exact recovery, even when the sampled integer is not a unit.',
                     'precise_statement': 'For distinct certified primes p,q and '
                                          '\\(ed\\equiv1\\pmod{(p-1)(q-1)}\\), z^(ed)=z modulo p and modulo q for '
                                          "every z in 0..pq−1, using Fermat's theorem and the zero case. The "
                                          'Chinese remainder theorem gives \\((z^e)^d=z\\) modulo N. Identical '
                                          'encodings and deterministic hashing therefore give identical keys.',
                     'scope': 'Every honest encapsulation after successful bounded generation. A prime-search '
                              'timeout yields no key; it is not an honest decapsulation mismatch. Exact release '
                              'evidence: crucible/rsa_v4.py. External references provide context, not '
                              'certification of this release.',
                     'challenge': 'Find an accepted invalid certificate or inverse, an arithmetic/serialization '
                                  'mismatch, or an honest nonunit test case violating agreement.',
                     'sources': [{'title': 'RSA primitives and CRT representation, RFC 8017',
                                  'url': 'https://www.rfc-editor.org/rfc/rfc8017.html'},
                                 {'title': 'Pocklington certificates, Handbook of Applied Cryptography, chapter 4',
                                  'url': 'https://cacr.uwaterloo.ca/hac/about/chap4.pdf'}],
                     'limitations': ['Conditional on faithful arithmetic and encoding, not an independent formal '
                                     'verification of the executable.',
                                     'No collision-resistance assumption is needed for agreement; this says '
                                     'nothing about secrecy.']},
                    {'id': 'rsa-cost-reference',
                     'title': 'What the initial security curve assumes',
                     'role': 'model',
                     'supports': ['security'],
                     'summary': 'Modulus length is mapped through a restricted factoring-cost reference.',
                     'precise_statement': 'For modulus bit length L, the reference is min(L/4,128 F(L)/F(3072)), '
                                          'with \\(F(L)=(L\\ln 2)^{1/3}[\\ln(L\\ln 2)]^{2/3}\\). The terms '
                                          'represent a balanced-factor rho reference and a normalized '
                                          'number-field-sieve shape, not a proof of inversion cost.',
                     'scope': 'A frozen classical comparison used to select the tier sizes, with a 3072-bit '
                              'endpoint. Exact release evidence: crucible/rsa_v4.py. External references provide '
                              'context, not certification of this release.',
                     'challenge': 'Provide a better applicable factoring or inversion analysis with explicit '
                                  'resources and success probability, especially one exploiting the exact prime '
                                  'distribution.',
                     'sources': [{'title': 'Conventional RSA strength comparisons, NIST SP 800-57 Part 1 Rev. 5, '
                                           'Table 2',
                                  'url': 'https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-57pt1r5.pdf'}],
                     'limitations': ['ECM, structured-prime attacks, other inversion routes, constants and '
                                     'quantum algorithms are not covered.',
                                     'A modeled attack cost is not a security lower bound or measured runtime.']},
                    {'id': 'rsa-kem-bridge',
                     'title': 'The remaining KEM security argument',
                     'role': 'reduction',
                     'supports': ['security'],
                     'summary': 'Published RSA-KEM results do not automatically cover this custom KDF and key '
                                'generator.',
                     'precise_statement': 'A reduction must match this structured key distribution, uniform '
                                          'all-residue encapsulation, fixed-width encodings, explicit '
                                          'invalid-input rejection and transcript-bound SHA-256 KDF. Its IND-CCA '
                                          'game, hash idealization and loss must be stated. The current release '
                                          'supplies no such complete reduction.',
                     'scope': 'Custom RSA release 4, not a claim of RFC 9690 compliance; the website currently '
                              'checks passive recovery of the entire shared key. Exact release evidence: '
                              'crucible/rsa_v4.py. External references provide context, not certification of this '
                              'release.',
                     'challenge': 'Supply the matching reduction or demonstrate an exploitable distinction, '
                                  'malformed-input issue or adaptive-ciphertext attack.',
                     'sources': [{'title': 'RSA-KEM construction and random-oracle qualification, RFC 9690 §3',
                                  'url': 'https://www.rfc-editor.org/rfc/rfc9690.html#section-3'}],
                     'limitations': ['A gap in a proof is not an attack. A published proof for another KDF is not '
                                     'a proof for this one.',
                                     'The variable-time reference implementation has no deployment side-channel '
                                     'assurance.']}],
 ('dhkem-x25519', 'curve-compressed-v2'): [{'id': 'ec-shared-point',
                                            'title': 'Recovering the hidden curve point',
                                            'role': 'hardness',
                                            'supports': ['security'],
                                            'summary': 'The recovery problem uses each exact public curve, not an '
                                                       'abstract group of the same size.',
                                            'precise_statement': 'On the registered prime-order curve with '
                                                                 'generator G of order q, draw independent x,r '
                                                                 'uniformly from 1..q−1. Given \\(X=xG\\) and '
                                                                 '\\(C=rG\\), recover \\(Z=xrG\\). Either '
                                                                 'discrete logarithm is sufficient; recovering Z '
                                                                 'allows computation of the transcript-derived '
                                                                 'key.',
                                            'scope': 'The deterministically selected short-Weierstrass curves and '
                                                     'full nonzero scalar distribution of this release. These are '
                                                     'not resized X25519 curves. Exact release evidence: '
                                                     'crucible/ec_compressed.py, crucible/ec_parameters.json. '
                                                     'External references provide context, not certification of '
                                                     'this release.',
                                            'challenge': 'Recover the shared point or final key, or identify a '
                                                         'curve-specific shortcut, weak parameter choice or '
                                                         'reusable precomputation.',
                                            'sources': [{'title': 'Elliptic-curve primitives and representations, '
                                                                  'SEC 1 v2.0',
                                                         'url': 'https://www.secg.org/sec1-v2.pdf'}],
                                            'limitations': ['No concrete lower bound is established for these '
                                                            'curves; low tiers are deliberately breakable.',
                                                            'Discrete-log hardness, computational DH hardness and '
                                                            'hashed-key security are distinct claims.']},
                                           {'id': 'ec-honest-agreement',
                                            'title': 'Why honest keys agree',
                                            'role': 'derived',
                                            'supports': ['correctness'],
                                            'summary': 'Public certificates establish the group, and scalar '
                                                       'multiplication gives the agreement identity.',
                                            'precise_statement': 'Exact certificates establish prime p,q. A '
                                                                 'nonidentity G with \\(qG=\\mathcal O\\) and '
                                                                 'uniqueness of the appropriate multiple of q in '
                                                                 "Hasse's interval establish that the curve has "
                                                                 'order q. For all honest nonzero x,r, '
                                                                 '\\(r(xG)=x(rG)\\). Canonical compressed-point '
                                                                 'encoding and identical deterministic hashing '
                                                                 'then give equal keys.',
                                            'scope': 'All registered curves and honest scalars, under faithful '
                                                     'certificate verification, curve arithmetic and encoding. '
                                                     'Invalid point encodings are rejected separately. Exact '
                                                     'release evidence: crucible/ec_compressed.py, '
                                                     'crucible/ec_parameters.json. External references provide '
                                                     'context, not certification of this release.',
                                            'challenge': 'Refute an accepted certificate or curve-order argument, '
                                                         'or produce an honest arithmetic/encoding disagreement.',
                                            'sources': [{'title': 'Point compression and validation, SEC 1 v2.0',
                                                         'url': 'https://www.secg.org/sec1-v2.pdf'},
                                                        {'title': 'ECPP certificate definitions, PARI '
                                                                  'documentation',
                                                         'url': 'https://pari.math.u-bordeaux.fr/dochtml/html-stable/Arithmetic_functions.html#primecert'}],
                                            'limitations': ['The code is not independently formally verified. '
                                                            'Parameter certificates certify algebraic facts, not '
                                                            'resistance to every attack.',
                                                            'Agreement requires deterministic hashing, not '
                                                            'collision resistance.']},
                                           {'id': 'ec-cost-reference',
                                            'title': 'What the initial security curve assumes',
                                            'role': 'model',
                                            'supports': ['security'],
                                            'summary': 'The curve shows a generic discrete-log attack reference.',
                                            'precise_statement': 'The reference value is log2(q)/2, corresponding '
                                                                 'to square-root generic discrete-log work at '
                                                                 'group order q. It is applied to the actual '
                                                                 'certified q for each profile; it is not a '
                                                                 'theorem about the fastest attack on that '
                                                                 'concrete curve.',
                                            'scope': 'Classical generic group-operation reference, omitting '
                                                     'constants and implementation-specific timing. Exact release '
                                                     'evidence: crucible/ec_compressed.py, '
                                                     'crucible/ec_parameters.json. External references provide '
                                                     'context, not certification of this release.',
                                            'challenge': 'Give an applicable curve-specific attack or a more '
                                                         'precise resource analysis; separate group operations, '
                                                         'memory, parallelism and wall-clock measurements.',
                                            'sources': [{'title': 'Generic discrete-log methods, Handbook of '
                                                                  'Applied Cryptography, chapter 3',
                                                         'url': 'https://cacr.uwaterloo.ca/hac/about/chap3.pdf'}],
                                            'limitations': ['A generic-group limitation is not a lower bound '
                                                            'against algorithms using the concrete field or curve '
                                                            'representation.',
                                                            'Quantum algorithms and side channels are outside '
                                                            'this curve.']},
                                           {'id': 'ec-kem-bridge',
                                            'title': 'The remaining KEM security argument',
                                            'role': 'reduction',
                                            'supports': ['security'],
                                            'summary': 'The custom compressed-curve KEM still needs its own '
                                                       'complete security argument.',
                                            'precise_statement': 'A full IND-CCA claim must connect the exact '
                                                                 'curve/CDH distribution to the exact '
                                                                 'transcript-bound SHA-256 KDF and '
                                                                 'point-validation behavior under an explicit '
                                                                 'hash and decapsulation-oracle model. This '
                                                                 'release does not supply that reduction.',
                                            'scope': 'This research KEM is distinct from RFC 9180 DHKEM and its '
                                                     'named curves, encodings and key schedule. Exact release '
                                                     'evidence: crucible/ec_compressed.py, '
                                                     'crucible/ec_parameters.json. External references provide '
                                                     'context, not certification of this release.',
                                            'challenge': 'Supply a matching quantitative reduction or a concrete '
                                                         'distinguishing or chosen-ciphertext attack, rather than '
                                                         'inferring security from point-recovery difficulty '
                                                         'alone.',
                                            'sources': [{'title': 'DHKEM specification and security discussion '
                                                                  'for comparison, RFC 9180',
                                                         'url': 'https://www.rfc-editor.org/rfc/rfc9180.html'}],
                                            'limitations': ['A missing proof does not demonstrate insecurity; a '
                                                            'proof for HPKE does not transfer automatically.',
                                                            'Variable-time Python curve arithmetic has no '
                                                            'side-channel assurance for deployment.']}],
 ('sntrup', '3'): [{'id': 'ntru-quotient-recovery',
                    'title': 'Recovering the small hidden input',
                    'role': 'hardness',
                    'supports': ['security'],
                    'summary': 'The target is the exact quotient-NTRU and rounded-ciphertext distribution.',
                    'precise_statement': 'In \\(R_q=\\mathbb F_q[X]/(X^p-X-1)\\), sample weight-w ternary f and r '
                                         'uniformly, and ternary g uniformly conditioned on invertibility modulo '
                                         '3. Publish \\(h=g/(3f)\\), and the specified encoding and confirmation '
                                         'of Round(h r). Recover r or the final HashSession key. Recovering a '
                                         'suitable secret short representation of h is another sufficient attack '
                                         'route.',
                    'scope': "Each certified (p,q,w), the exact uniform sampler substitutions, and the release's "
                             'complete public transcript. The endpoint has sntrup653 algebra and byte formats; '
                             'its sampling is explicitly different from the original near-uniform mapping. Exact '
                             'release evidence: crucible/ntru_frontier.py, docs/ntru-parameter-frontier.txt. '
                             'External references provide context, not certification of this release.',
                    'challenge': 'Recover the encapsulation input or key using lattice, hybrid, structural or '
                                 'enumeration methods; quantify success on the actual distributions.',
                    'sources': [{'title': 'Streamlined NTRU Prime construction, 2020-10-07 specification',
                                 'url': 'https://ntruprime.cr.yp.to/nist/ntruprime-20201007.pdf'}],
                    'limitations': ['No hardness lower bound is established for every tuple; small profiles are '
                                    'intentionally breakable.',
                                    'Enumerating all 2^w binomial(p,w) possible r values is an explicit attack '
                                    'ceiling, not a security estimate.']},
                   {'id': 'ntru-honest-agreement',
                    'title': 'Why honest keys agree',
                    'role': 'derived',
                    'supports': ['correctness'],
                    'summary': 'The admissible tuples enforce the deterministic no-wrap correctness argument.',
                    'precise_statement': 'For the specified small-polynomial distributions, the NTRU Prime '
                                         'rounding residual m satisfies the coefficient bound '
                                         '\\(\\lVert3fm+gr\\rVert_\\infty\\le8w\\). The certified condition '
                                         '\\(q\\ge16w+1\\) makes this strictly less than q/2, so centered lifting '
                                         'does not wrap. Reduction modulo 3 and multiplication by g inverse '
                                         'recover r; matching encoding and confirmation preserve the shared key.',
                    'scope': 'Successful generation for the pinned tuples, including the certified '
                             'irreducibility/invertibility conditions and \\(2p\\ge3w\\). Here m denotes the '
                             'rounding residual, not a user message. Exact release evidence: '
                             'crucible/ntru_frontier.py, docs/ntru-parameter-frontier.txt. External references '
                             'provide context, not certification of this release.',
                    'challenge': 'Find a tuple that passes certification but violates a premise, an honest '
                                 'polynomial counterexample to the coefficient bound, or an implementation '
                                 'disagreement.',
                    'sources': [{'title': 'Deterministic correctness bound, NTRU Prime 2020 specification, '
                                          'Theorem 4',
                                 'url': 'https://ntruprime.cr.yp.to/nist/ntruprime-20201007.pdf'}],
                    'limitations': ['Resource exhaustion is an abort, not an agreement failure; '
                                    'invalid-ciphertext behavior is outside this honest-input theorem.',
                                    'The mathematical argument is conditional on faithful arithmetic and '
                                    'encoding, not an independent formal implementation audit.']},
                   {'id': 'ntru-attack-reference',
                    'title': 'What the initial security curve assumes',
                    'role': 'model',
                    'supports': ['security'],
                    'summary': 'Selection uses a finite candidate pool and an explicitly incomplete hybrid-attack '
                               'model.',
                    'precise_statement': 'Where supported, the recorded reference is the minimum of the pinned '
                                         "2020 estimator's non-hybrid, simple-hybrid and meet-in-the-middle "
                                         'hybrid branches under its classical sieving, free-memory and success '
                                         'heuristics. The selected tuples minimize public-key plus ciphertext '
                                         'bytes within the published candidate pool under that model.',
                    'scope': 'The recorded bounded search, not all parameters or all attacks. Unsupported tiny '
                             'configurations have no numerical reference. Exact release evidence: '
                             'crucible/ntru_frontier.py, docs/ntru-parameter-frontier.txt. External references '
                             'provide context, not certification of this release.',
                    'challenge': 'Improve an applicable attack estimate, test a success heuristic empirically, '
                                 'account for realistic memory, or exhibit a better tuple under the same declared '
                                 'constraints.',
                    'sources': [{'title': "NTRU Prime authors' security discussion and qualifications",
                                 'url': 'https://ntruprime.cr.yp.to/security.html'},
                                {'title': 'Pinned 2020-09-27 estimator',
                                 'url': 'https://ntruprime.cr.yp.to/estimate-20200927.sage'}],
                    'limitations': ['Not a global parameter optimum or a security lower bound. Dual, structural, '
                                    'quantum, multi-target and side-channel attacks are omitted.',
                                    'Enumeration counts, lattice-cost exponents and measured times are different '
                                    'quantities and cannot be interchanged without a model.']},
                   {'id': 'ntru-kem-bridge',
                    'title': 'The remaining KEM security argument',
                    'role': 'reduction',
                    'supports': ['security'],
                    'summary': "The transform's proof premises must be checked for these parameters and samplers.",
                    'precise_statement': 'The worker uses the published encoding, confirmation, reencryption '
                                         'check and domain-separated HashSession construction, with fallback on '
                                         'invalid ciphertexts. A full security claim still needs a matching '
                                         'reduction for each changed parameter distribution and exact uniform '
                                         'sampler, including its hash assumptions and quantitative losses.',
                    'scope': 'The reduced research family and the sampling-modified endpoint. Correct decoding '
                             'and published byte formats alone do not establish IND-CCA security. Exact release '
                             'evidence: crucible/ntru_frontier.py, docs/ntru-parameter-frontier.txt. External '
                             'references provide context, not certification of this release.',
                    'challenge': 'Map every theorem premise to the exact release, or demonstrate a transform, '
                                 'sampler or adaptive-ciphertext attack; distinguish those claims from passive '
                                 'recovery records.',
                    'sources': [{'title': 'NTRU Prime KEM transform and security framework, 2020 specification',
                                 'url': 'https://ntruprime.cr.yp.to/nist/ntruprime-20201007.pdf'}],
                    'limitations': ['No blanket inheritance of the published security result is asserted for all '
                                    'reduced tuples.',
                                    'Hash-model reductions and implementation/side-channel assurances are '
                                    'separate questions.']}],
 ('classic-mceliece', 'length-research-v1'): [{'id': 'mceliece-syndrome-recovery',
                                               'title': 'Decoding this structured code distribution',
                                               'role': 'hardness',
                                               'supports': ['security'],
                                               'summary': 'Both direct decoding and recovery of hidden Goppa '
                                                          'structure matter.',
                                               'precise_statement': 'Generate the registered systematic binary '
                                                                    'Goppa parity-check matrix H at m=12,t=64 and '
                                                                    'length n. For uniform weight-64 e and '
                                                                    'syndrome \\(C=He\\), recover e or the final '
                                                                    'SHAKE256(1||e||C,32) key. Generic decoding '
                                                                    'and recovery of an equivalent private '
                                                                    'decoding key are distinct sufficient attack '
                                                                    'routes.',
                                               'scope': 'The exact seeded Goppa/support distribution and '
                                                        'shortened lengths n=840..3488, conditioned on successful '
                                                        'systematic-key generation. Hardness on uniformly random '
                                                        'codes cannot simply be substituted for this '
                                                        'distribution. Exact release evidence: '
                                                        'crucible/code_mceliece.py, crucible/code_native.py, '
                                                        'docs/mceliece-parameter-review-20261017.txt. External '
                                                        'references provide context, not certification of this '
                                                        'release.',
                                               'challenge': 'Decode honest syndromes, recover equivalent private '
                                                            'structure, or validate structural-attack heuristics '
                                                            'and their concrete costs on these parameters.',
                                               'sources': [{'title': 'Classic McEliece specification, 2022-10-23',
                                                            'url': 'https://classic.mceliece.org/mceliece-spec-20221023.pdf'},
                                                           {'title': 'Ghoshal–Ishai–Jain–Sun: public-code '
                                                                     'distinction and heuristic key-recovery '
                                                                     'extensions',
                                                            'url': 'https://eprint.iacr.org/2026/1630'}],
                                               'limitations': ['A code distinguisher alone is not recovery of a '
                                                               'shared key. Recent structural-recovery analyses '
                                                               'require explicit heuristic and cost accounting.',
                                                               'No current full-security estimate is asserted for '
                                                               'this shortening study; small tiers are '
                                                               'intentionally breakable.']},
                                              {'id': 'mceliece-honest-agreement',
                                               'title': 'Why honest keys agree',
                                               'role': 'derived',
                                               'supports': ['correctness'],
                                               'summary': 'The Goppa distance bound makes every honest error '
                                                          'uniquely decodable.',
                                               'precise_statement': 'The valid binary irreducible Goppa '
                                                                    'construction has minimum distance at least '
                                                                    '\\(2t+1=129\\). Two distinct errors of '
                                                                    'weight at most t=64 cannot have the same '
                                                                    'syndrome: their difference would be a '
                                                                    'nonzero codeword of weight at most 128. A '
                                                                    'faithful bounded-distance decoder therefore '
                                                                    'recovers each honest e; the same '
                                                                    'deterministic KDF yields the same key.',
                                               'scope': 'All registered lengths at fixed m=12,t=64, successful '
                                                        'valid key generation, and honest weight-64 errors. The '
                                                        'distance fact and the correctness of the actual decoder '
                                                        'are separate premises. Exact release evidence: '
                                                        'crucible/code_mceliece.py, crucible/code_native.py, '
                                                        'docs/mceliece-parameter-review-20261017.txt. External '
                                                        'references provide context, not certification of this '
                                                        'release.',
                                               'challenge': 'Refute a parameter/admissibility premise, find a '
                                                            'valid honest error the implementation decodes '
                                                            'incorrectly, or identify a transcript/KDF mismatch.',
                                               'sources': [{'title': 'Classic McEliece construction, decoder and '
                                                                     'parameter definitions',
                                                            'url': 'https://classic.mceliece.org/mceliece-spec-20221023.pdf'}],
                                               'limitations': ['Unique decoding is not, by itself, verification '
                                                               'of the executable decoder.',
                                                               'Generation aborts and malformed-ciphertext '
                                                               'rejection are separate from honest disagreement. '
                                                               'Structural confidentiality attacks do not refute '
                                                               'this distance bound.']},
                                              {'id': 'mceliece-prange-reference',
                                               'title': 'What the elementary work reference means',
                                               'role': 'model',
                                               'supports': ['security'],
                                               'summary': 'The historical spacing proxy counts support hits for '
                                                          'one elementary attack.',
                                               'precise_statement': 'The recorded Prange proxy is '
                                                                    '\\(\\log_2[\\binom '
                                                                    'n{64}/\\binom{768}{64}]\\), the inverse '
                                                                    'probability that a selected 768-coordinate '
                                                                    'set contains all 64 error positions. It '
                                                                    'omits rejection of singular submatrices and '
                                                                    'the elimination and verification cost of '
                                                                    'each trial.',
                                               'scope': 'A historical shortening/spacing reference with fixed '
                                                        "field and Goppa degree. It is not the site's "
                                                        'estimated-security curve and is not an optimization '
                                                        'across m,n,t. Exact release evidence: '
                                                        'crucible/code_mceliece.py, crucible/code_native.py, '
                                                        'docs/mceliece-parameter-review-20261017.txt. External '
                                                        'references provide context, not certification of this '
                                                        'release.',
                                               'challenge': 'Compare improved information-set decoding and '
                                                            'applicable structural attacks under a common '
                                                            'success, memory and preprocessing model; report '
                                                            'missing analyses as unknown.',
                                               'sources': [{'title': 'Progressive sieving ISD and its heuristic '
                                                                     'scope',
                                                            'url': 'https://eprint.iacr.org/2026/633'},
                                                           {'title': 'Improved structural key recovery with '
                                                                     'explicitly conditional estimates',
                                                            'url': 'https://eprint.iacr.org/2026/1984'}],
                                               'limitations': ['Not a security lower bound, a best-known attack '
                                                               'cost or total runtime.',
                                                               'Do not equate support trials with operations or '
                                                               'merge different memory-cost models. A shared '
                                                               'nominal reference does not establish equal '
                                                               'security of alternative tuples.']},
                                              {'id': 'mceliece-kem-bridge',
                                               'title': 'The remaining KEM security argument',
                                               'role': 'reduction',
                                               'supports': ['security'],
                                               'summary': 'Retaining the transform does not settle security of '
                                                          'every shortened configuration.',
                                               'precise_statement': 'A full IND-CCA argument must apply the exact '
                                                                    'KEM transform to this key/error '
                                                                    'distribution, explain its hash idealization '
                                                                    'and ciphertext rejection behavior, and '
                                                                    'account for both decoding and structural-key '
                                                                    'attacks. Endpoint conformance tests '
                                                                    'establish byte-level agreement with the '
                                                                    'reference, not those hardness claims or a '
                                                                    'theorem for every shorter length.',
                                               'scope': 'The m=12,t=64 research shortening family and the '
                                                        'unchanged non-pc SHAKE-based transform. Exact release '
                                                        'evidence: crucible/code_mceliece.py, '
                                                        'crucible/code_native.py, '
                                                        'docs/mceliece-parameter-review-20261017.txt. External '
                                                        'references provide context, not certification of this '
                                                        'release.',
                                               'challenge': 'Supply a release-specific quantitative reduction '
                                                            'with all premises identified, or demonstrate a '
                                                            'concrete distinguishing, adaptive-ciphertext or '
                                                            'implementation attack.',
                                               'sources': [{'title': 'Classic McEliece KEM specification',
                                                            'url': 'https://classic.mceliece.org/mceliece-spec-20221023.pdf'}],
                                               'limitations': ['No claim that random-code indistinguishability is '
                                                               'automatically a necessary premise of every KEM '
                                                               'reduction.',
                                                               'Full IND-CCA assurance, passive shared-key '
                                                               'recovery, and implementation side-channel '
                                                               'resistance remain separate claims.']}]})


# Shared reading format for the remaining current releases. These summaries
# are authored against the exact claims above, not inferred security judgments.
_PRESENTATIONS.update({('teaching-dh-kem', '7'): {'lead': 'Diffie–Hellman gives an exact agreement identity. Secrecy requires a '
                                    "separate argument for this release's groups, exponent distribution and key "
                                    'derivation.',
                            'overview': {'goal': 'The stronger goal is IND-CCA security: the shared secret should '
                                                 'look random even with access to decapsulation results for '
                                                 'other, attacker-chosen ciphertexts.',
                                         'scope': "This is Crucible's custom finite-field construction. Each "
                                                  'public group is reused, while fresh exponents come from a '
                                                  'specified bounded interval.',
                                         'groups': [{'id': 'evidence',
                                                     'title': 'What we have',
                                                     'items': [{'title': 'Exact honest agreement',
                                                                'text': 'Parameter certificates and the identity '
                                                                        '\\((g^x)^r=(g^r)^x\\) establish '
                                                                        'agreement under faithful arithmetic, '
                                                                        'encoding and deterministic hashing. No '
                                                                        'hardness or hash-collision assumption is '
                                                                        'needed.',
                                                                'claim_id': 'dh-honest-agreement'},
                                                               {'title': 'An explicit comparison model',
                                                                'text': 'The initial curve combines subgroup, '
                                                                        'interval-search and normalized '
                                                                        'number-field-sieve references. These are '
                                                                        'heuristic work estimates, not measured '
                                                                        'runtimes or security lower bounds.',
                                                                'claim_id': 'dh-cost-reference'}]},
                                                    {'id': 'assumptions',
                                                     'title': 'What we still assume',
                                                     'items': [{'title': 'Hardness in these particular groups',
                                                                'text': 'Confidentiality needs difficulty at the '
                                                                        'selected parameters and exponent '
                                                                        'distribution. Low tiers are deliberately '
                                                                        'breakable. Reusable group preprocessing '
                                                                        'must be counted separately from each new '
                                                                        'recovery.',
                                                                'claim_id': 'interval-dh-recovery'}]},
                                                    {'id': 'gaps',
                                                     'title': 'What is missing here',
                                                     'items': [{'title': 'A matching full-KEM argument',
                                                                'text': 'Specify the hash model, subgroup '
                                                                        'validation, decapsulation access and '
                                                                        'quantitative losses for this exact '
                                                                        'transcript-bound SHA-256 construction. '
                                                                        'Recovering a discrete logarithm is '
                                                                        'sufficient, but not asserted equivalent '
                                                                        'to recovering its key.',
                                                                'claim_id': 'dh-kem-bridge'},
                                                               {'title': 'Executable assurance',
                                                                'text': 'The algebra does not independently '
                                                                        'verify certificate checking, arithmetic '
                                                                        'or encodings. Malformed-input and '
                                                                        'side-channel behavior need their own '
                                                                        'assessment.',
                                                                'claim_id': 'dh-honest-agreement'}]}],
                                         'boundary': 'Published DHKEM analyses cover specified constructions; '
                                                     'they do not automatically cover this one. The live '
                                                     'challenge tests passive shared-secret recovery, and an '
                                                     'unsolved challenge does not establish IND-CCA security.'},
                            'sections': [{'id': 'details',
                                          'title': 'The argument in detail',
                                          'description': 'Precise statements, their scope, sources and ways to '
                                                         'challenge them.',
                                          'claim_ids': ['interval-dh-recovery',
                                                        'dh-honest-agreement',
                                                        'dh-cost-reference',
                                                        'dh-kem-bridge']}]},
 ('rsa-kem', '4'): {'lead': 'RSA gives exact inversion for honest exchanges. The security question concerns this '
                            "release's actual prime generator, encapsulation distribution and key derivation.",
                    'overview': {'goal': 'The stronger goal is IND-CCA security: the shared secret should look '
                                         'random even with access to decapsulation results for other, '
                                         'attacker-chosen ciphertexts.',
                                 'scope': 'This custom release uses bounded generation of balanced, recursively '
                                          'certified primes and samples the encapsulated integer uniformly from '
                                          'every residue modulo N. It is not an RFC 9690 compliance claim.',
                                 'groups': [{'id': 'evidence',
                                             'title': 'What we have',
                                             'items': [{'title': 'Exact honest recovery',
                                                        'text': 'The Chinese remainder theorem establishes '
                                                                '\\((z^e)^d=z\\) modulo N, including nonunits. '
                                                                'With faithful arithmetic and encoding, honest '
                                                                'keys agree without any hardness assumption. A '
                                                                'generation timeout produces no key.',
                                                        'claim_id': 'rsa-honest-agreement'},
                                                       {'title': 'A declared factoring reference',
                                                        'text': 'The initial curve compares balanced-factor rho '
                                                                'and normalized number-field-sieve estimates. It '
                                                                'does not prove RSA inversion cost or cover all '
                                                                'attacks on this prime distribution.',
                                                        'claim_id': 'rsa-cost-reference'}]},
                                            {'id': 'assumptions',
                                             'title': 'What we still assume',
                                             'items': [{'title': 'Inversion is difficult at the chosen parameters',
                                                        'text': 'The relevant distribution comes from this exact '
                                                                'successful key generator. Factoring suffices for '
                                                                'inversion; their equivalence is not asserted. '
                                                                'Other routes to the final key remain possible, '
                                                                'and low tiers are deliberately attackable.',
                                                        'claim_id': 'rsa-inversion'}]},
                                            {'id': 'gaps',
                                             'title': 'What is missing here',
                                             'items': [{'title': 'An exact-release security reduction',
                                                        'text': 'Match the prime sampler, all-residue '
                                                                'distribution, transcript-bound SHA-256 KDF and '
                                                                'invalid-input rejection to a precise game, hash '
                                                                'model and quantitative bound.',
                                                        'claim_id': 'rsa-kem-bridge'},
                                                       {'title': 'Assurance for the executable',
                                                        'text': 'The mathematical identity does not formally '
                                                                'verify this implementation. The variable-time '
                                                                'reference has no deployment side-channel '
                                                                'assurance.',
                                                        'claim_id': 'rsa-kem-bridge'}]}],
                                 'boundary': 'RSA-KEM has published conditional security results; applying them '
                                             "to this custom release is the missing work. The site's passive "
                                             'key-recovery challenge neither proves nor fully tests IND-CCA '
                                             'security.'},
                    'sections': [{'id': 'details',
                                  'title': 'The argument in detail',
                                  'description': 'Precise statements, their scope, sources and ways to challenge '
                                                 'them.',
                                  'claim_ids': ['rsa-inversion',
                                                'rsa-honest-agreement',
                                                'rsa-cost-reference',
                                                'rsa-kem-bridge']}]},
 ('dhkem-x25519', 'curve-compressed-v2'): {'lead': 'Elliptic-curve Diffie–Hellman gives exact honest agreement. '
                                                   'Security depends on the actual curve and complete KEM, not '
                                                   'just the group-order label.',
                                           'overview': {'goal': 'The stronger goal is IND-CCA security: the '
                                                                'shared secret should look random even with '
                                                                'access to decapsulation results for other, '
                                                                'attacker-chosen ciphertexts.',
                                                        'scope': 'These are custom, deterministically selected '
                                                                 'short-Weierstrass curves with compressed points '
                                                                 'and uniformly sampled nonzero scalars. They are '
                                                                 'not resized X25519 or RFC 9180 DHKEM profiles.',
                                                        'groups': [{'id': 'evidence',
                                                                    'title': 'What we have',
                                                                    'items': [{'title': 'Certified algebra and '
                                                                                        'exact agreement',
                                                                               'text': 'Parameter certificates '
                                                                                       'establish the required '
                                                                                       'prime-order groups. The '
                                                                                       'identity '
                                                                                       '\\(r(xG)=x(rG)\\) gives '
                                                                                       'equal keys under faithful '
                                                                                       'arithmetic, encoding and '
                                                                                       'deterministic hashing, '
                                                                                       'independently of '
                                                                                       'hardness.',
                                                                               'claim_id': 'ec-honest-agreement'},
                                                                              {'title': 'A generic attack '
                                                                                        'reference',
                                                                               'text': 'The initial curve uses '
                                                                                       'square-root discrete-log '
                                                                                       'work at the certified '
                                                                                       'group order. This is a '
                                                                                       'classical group-operation '
                                                                                       'reference, not a proven '
                                                                                       'fastest attack or a '
                                                                                       'measured runtime.',
                                                                               'claim_id': 'ec-cost-reference'}]},
                                                                   {'id': 'assumptions',
                                                                    'title': 'What we still assume',
                                                                    'items': [{'title': 'Hardness on each '
                                                                                        'concrete curve',
                                                                               'text': 'A security argument must '
                                                                                       'address these specific '
                                                                                       'curves and distributions, '
                                                                                       'including '
                                                                                       'representation-specific '
                                                                                       'shortcuts and reusable '
                                                                                       'preprocessing. '
                                                                                       'Discrete-log recovery, '
                                                                                       'shared-point recovery and '
                                                                                       'hashed-key security are '
                                                                                       'distinct claims; small '
                                                                                       'tiers are deliberately '
                                                                                       'breakable.',
                                                                               'claim_id': 'ec-shared-point'}]},
                                                                   {'id': 'gaps',
                                                                    'title': 'What is missing here',
                                                                    'items': [{'title': 'A matching full-KEM '
                                                                                        'reduction',
                                                                               'text': 'Connect the curve problem '
                                                                                       'to the exact '
                                                                                       'transcript-bound SHA-256 '
                                                                                       'KDF, point validation and '
                                                                                       'decapsulation behavior, '
                                                                                       'with an explicit hash '
                                                                                       'model and quantitative '
                                                                                       'losses.',
                                                                               'claim_id': 'ec-kem-bridge'},
                                                                              {'title': 'Assurance beyond '
                                                                                        'parameter certificates',
                                                                               'text': 'Certificates establish '
                                                                                       'algebraic facts, not '
                                                                                       'resistance to every '
                                                                                       'attack. They do not '
                                                                                       'formally verify the '
                                                                                       'executable or give '
                                                                                       'variable-time Python '
                                                                                       'arithmetic a deployment '
                                                                                       'side-channel guarantee.',
                                                                               'claim_id': 'ec-honest-agreement'}]}],
                                                        'boundary': 'Published DHKEM analyses do not '
                                                                    'automatically transfer to these custom '
                                                                    'curves and algorithms. The live challenge '
                                                                    'tests passive shared-secret recovery; an '
                                                                    'unsolved challenge is not an IND-CCA '
                                                                    'guarantee.'},
                                           'sections': [{'id': 'details',
                                                         'title': 'The argument in detail',
                                                         'description': 'Precise statements, their scope, sources '
                                                                        'and ways to challenge them.',
                                                         'claim_ids': ['ec-shared-point',
                                                                       'ec-honest-agreement',
                                                                       'ec-cost-reference',
                                                                       'ec-kem-bridge']}]},
 ('ml-kem', 'compressed-research-v4'): {'lead': 'This research ladder ends at ML-KEM-512. Its smaller rings and '
                                                'profile-specific correctness arguments need their own '
                                                'assessment.',
                                        'overview': {'goal': 'IND-CCA security means the shared secret looks '
                                                             'random even with access to decapsulation of other, '
                                                             'attacker-chosen ciphertexts.',
                                                     'scope': 'This release has six polynomial degrees, from 8 to '
                                                              '256. Only the final profile is the standardized '
                                                              'ML-KEM-512 endpoint.',
                                                     'groups': [{'id': 'evidence',
                                                                 'title': 'What we have',
                                                                 'items': [{'title': 'A standardized endpoint',
                                                                            'text': 'FIPS 203 specifies the '
                                                                                    'endpoint construction. '
                                                                                    'Crucible checks its '
                                                                                    'implementation against '
                                                                                    'independent NIST vectors; '
                                                                                    'the smaller-degree '
                                                                                    'implementations are research '
                                                                                    'code.',
                                                                            'claim_id': 'module-implementation'},
                                                                           {'title': 'A calculation for each '
                                                                                     'profile',
                                                                            'text': 'All six meet the claimed '
                                                                                    '\\(2^{-128}\\) disagreement '
                                                                                    'target under their stated '
                                                                                    'premises. Degrees 8 and 16 '
                                                                                    'have zero-failure support '
                                                                                    'arguments; statistical '
                                                                                    'premises differ above them.',
                                                                            'claim_id': 'module-agreement'}]},
                                                                {'id': 'assumptions',
                                                                 'title': 'What we still assume',
                                                                 'items': [{'title': 'The exact module problem is '
                                                                                     'hard',
                                                                            'text': 'Hardness depends on the '
                                                                                    'ring, dimension and noise '
                                                                                    'law. Smaller rings have '
                                                                                    'different structure and '
                                                                                    'short-message enumeration '
                                                                                    'attacks; endpoint security '
                                                                                    'does not transfer '
                                                                                    'automatically.',
                                                                            'claim_id': 'module-lwe'},
                                                                           {'title': 'The sampling idealization '
                                                                                     'applies',
                                                                            'text': 'Statistical bounds use '
                                                                                    'independent small integer '
                                                                                    'noise, sampled by subtracting '
                                                                                    'sums of fair bits. Treating '
                                                                                    'the implementation\'s hash-based '
                                                                                    'sampling as that ideal '
                                                                                    'experiment requires justification.',
                                                                            'claim_id': 'module-expansion'},
                                                                           {'title': 'Extra rounding premises at '
                                                                                     'larger degrees',
                                                                            'text': 'Degrees 128 and 256 '
                                                                                    'additionally model '
                                                                                    'rounding errors in the first '
                                                                                    'ciphertext component as if '
                                                                                    'they came from independent '
                                                                                    'uniform coefficients, '
                                                                                    'independently of relevant '
                                                                                    'secrets and noise. The second '
                                                                                    'component\'s error is bounded '
                                                                                    'without this assumption.',
                                                                            'claim_id': 'module-rounding'}]},
                                                                {'id': 'gaps',
                                                                 'title': 'What is missing here',
                                                                 'items': [{'title': 'A full argument for each '
                                                                                     'research profile',
                                                                            'text': 'Check the security game, '
                                                                                    'reduced-ring construction, '
                                                                                    'hash domains, message space '
                                                                                    'and failure terms. The '
                                                                                    "standard endpoint's security "
                                                                                    'argument does not discharge '
                                                                                    'those changes.',
                                                                            'claim_id': 'module-lwe'},
                                                                           {'title': 'Implementation assurance '
                                                                                     'beyond vectors',
                                                                            'text': 'Audit arithmetic, validation '
                                                                                    'and rejection against the '
                                                                                    'exact specification. Passing '
                                                                                    'tests is neither FIPS '
                                                                                    'certification nor a complete '
                                                                                    'proof or side-channel '
                                                                                    'guarantee.',
                                                                            'claim_id': 'module-implementation'}]}],
                                                     'boundary': 'The live challenge tests shared-secret '
                                                                 'recovery, not the complete IND-CCA game. These '
                                                                 'premises do not assert that small research '
                                                                 'profiles are secure.'},
                                        'sections': [{'id': 'details',
                                                      'title': 'The argument in detail',
                                                      'description': 'Precise statements, their scope, sources '
                                                                     'and ways to challenge them.',
                                                      'claim_ids': ['module-lwe',
                                                                    'module-expansion',
                                                                    'module-rounding',
                                                                    'module-agreement',
                                                                    'module-implementation']}]},
 ('hqc', 'modeled-research-v3'): {'lead': "HQC's published construction supplies the anchor. Crucible's smaller "
                                          'configurations have conditional correctness certificates and '
                                          'unresolved security calibration.',
                                  'overview': {'goal': 'IND-CCA security means the shared secret looks random '
                                                       'even with access to decapsulation of other, '
                                                       'attacker-chosen ciphertexts.',
                                               'scope': 'This 66-profile release retains the 2025 HQC '
                                                        'construction. Its final tuple matches 2025 HQC-1; it '
                                                        'does not adopt the revised October 2026 parameters.',
                                               'groups': [{'id': 'evidence',
                                                           'title': 'What we have',
                                                           'items': [{'title': 'A published construction and '
                                                                               'matching endpoint',
                                                                      'text': 'The endpoint has byte-conformance '
                                                                              'evidence against unmodified 2025 '
                                                                              'HQC-1. Smaller profiles stay '
                                                                              'within the reviewed native codec '
                                                                              'domain.',
                                                                      'claim_id': 'hqc-implementation'},
                                                                     {'title': 'A conditional certificate for '
                                                                               'every profile',
                                                                      'text': 'Exact rational calculations meet '
                                                                              'the \\(2^{-128}\\) disagreement '
                                                                              'target inside the declared Model 1 '
                                                                              'analysis, including decoder ties '
                                                                              'and sampler-bias corrections.',
                                                                      'claim_id': 'hqc-agreement'}]},
                                                          {'id': 'assumptions',
                                                           'title': 'What we still assume',
                                                           'items': [{'title': 'The specified decoding problems '
                                                                               'are hard',
                                                                      'text': 'The security questions depend on '
                                                                              'exact weights, parity and '
                                                                              'truncation. A random comparison '
                                                                              'that ignores public parity gives a '
                                                                              'false break; small profiles are '
                                                                              'deliberately attackable.',
                                                                      'claim_id': 'hqc-decoding'},
                                                                     {'title': 'The noise model is conservative',
                                                                      'text': 'Actual cyclic-product noise is '
                                                                              'correlated. Model 1 substitutes '
                                                                              'independent coordinates with the '
                                                                              'exact marginal; transferring its '
                                                                              'adjusted failure bound to each '
                                                                              'real decoder remains conjectural.',
                                                                      'claim_id': 'hqc-noise-model'}]},
                                                          {'id': 'gaps',
                                                           'title': 'What is missing here',
                                                           'items': [{'title': 'Current security calibration',
                                                                      'text': 'Assess current attacks against '
                                                                              'every exact tuple. The 2026 '
                                                                              'parameters and cryptanalysis '
                                                                              'differ; the historical endpoint is '
                                                                              'not a newly established 128-bit '
                                                                              'security bound.',
                                                                      'claim_id': 'hqc-decoding'},
                                                                     {'title': 'The complete security argument '
                                                                               'for this release',
                                                                      'text': "Check the published argument's "
                                                                              'hypotheses, transform, models and '
                                                                              'reduction losses for these '
                                                                              'research parameters. A decoding '
                                                                              'assumption alone does not '
                                                                              'establish full IND-CCA security.',
                                                                      'claim_id': 'hqc-decoding'},
                                                                     {'title': 'Implementation assurance beyond '
                                                                               'conformance',
                                                                      'text': 'Audit the pinned samplers, decoder '
                                                                              'and rejection behavior. '
                                                                              'Conformance and boundary tests do '
                                                                              'not establish a complete '
                                                                              'implementation proof or '
                                                                              'side-channel security.',
                                                                      'claim_id': 'hqc-implementation'}]}],
                                               'boundary': 'The live challenge tests shared-secret recovery. '
                                                           'Exact certificate arithmetic does not prove the '
                                                           'noise-model transfer, and an unsolved challenge does '
                                                           'not establish IND-CCA security.'},
                                  'sections': [{'id': 'details',
                                                'title': 'The argument in detail',
                                                'description': 'Precise statements, their scope, sources and ways '
                                                               'to challenge them.',
                                                'claim_ids': ['hqc-decoding',
                                                              'hqc-noise-model',
                                                              'hqc-agreement',
                                                              'hqc-implementation']}]},
 ('sntrup', '3'): {'lead': 'Streamlined NTRU Prime supplies the construction and security framework. Here we '
                           "separate that foundation from the work needed for Crucible's parameter and sampler "
                           'changes.',
                   'overview': {'goal': 'The shared secret should look random even with decapsulation access for '
                                        'other, attacker-chosen ciphertexts: the IND-CCA goal.',
                                'scope': 'Crucible uses reduced parameter tuples and exact uniform samplers. Its '
                                         'endpoint retains sntrup653 algebra and byte formats, with a changed '
                                         'sampling distribution.',
                                'groups': [{'id': 'evidence',
                                            'title': 'What we have',
                                            'items': [{'title': 'A deterministic correctness argument',
                                                       'text': 'Certified parameter conditions prevent '
                                                               'coefficient wraparound, so honest decapsulation '
                                                               'recovers the input exactly under faithful '
                                                               'arithmetic and encoding.',
                                                       'claim_id': 'ntru-honest-agreement'},
                                                      {'title': 'Published machinery and implementation checks',
                                                       'text': 'The published confirmation, reencryption and '
                                                               'rejection machinery is retained. All research '
                                                               'profiles have independent decapsulation checks; '
                                                               'the endpoint decapsulates original sntrup653 test '
                                                               'vectors.',
                                                       'claim_id': 'ntru-kem-bridge'}]},
                                           {'id': 'assumptions',
                                            'title': 'What we still assume',
                                            'items': [{'title': 'The exact recovery problem is hard',
                                                       'text': 'Security depends on the selected ring and '
                                                               'small-polynomial distributions, including rounded '
                                                               'ciphertexts. Small tiers are deliberately weak; '
                                                               'attack estimates are not hardness guarantees.',
                                                       'claim_id': 'ntru-quotient-recovery'},
                                                      {'title': 'The model matches the implementation',
                                                       'text': 'A conditional security argument must specify its '
                                                               'hash idealizations and rely on faithful '
                                                               'randomness, encoding and rejection behavior. '
                                                               'Correctness checks do not establish these '
                                                               'security premises.',
                                                       'claim_id': 'ntru-kem-bridge'}]},
                                           {'id': 'gaps',
                                            'title': 'What is missing here',
                                            'items': [{'title': 'A complete application to this release',
                                                       'text': 'Check the published argument against each changed '
                                                               'parameter and sampler, with explicit hypotheses '
                                                               'and quantitative losses. The gap concerns this '
                                                               'research release, not an assertion that published '
                                                               'NTRU Prime lacks a proof.',
                                                       'claim_id': 'ntru-kem-bridge'},
                                                      {'title': 'Broader attack and parameter analysis',
                                                       'text': 'The current selection uses a finite candidate '
                                                               'pool and incomplete hybrid-attack model. Other '
                                                               'attacks, realistic memory costs and global '
                                                               'parameter efficiency remain unassessed.',
                                                       'claim_id': 'ntru-attack-reference'}]}],
                                'boundary': 'The live challenge tests passive shared-secret recovery. An unsolved '
                                            'instance does not establish IND-CCA security or validate the '
                                            'assumptions above.'},
                   'sections': [{'id': 'details',
                                 'title': 'The argument in detail',
                                 'description': 'Precise statements, their scope, sources and ways to challenge '
                                                'them.',
                                 'claim_ids': ['ntru-quotient-recovery',
                                               'ntru-honest-agreement',
                                               'ntru-attack-reference',
                                               'ntru-kem-bridge']}]},
 ('classic-mceliece', 'length-research-v1'): {'lead': 'Classic McEliece supplies the construction. Crucible '
                                                      'studies shorter codes while keeping the field and Goppa '
                                                      'degree fixed; their security needs its own assessment.',
                                              'overview': {'goal': 'The shared secret should look random even '
                                                                   'with decapsulation access for other, '
                                                                   'attacker-chosen ciphertexts: the IND-CCA '
                                                                   'goal.',
                                                           'scope': 'This research family varies code length from '
                                                                    '840 to 3488 at fixed field degree 12 and '
                                                                    'Goppa degree 64. Its endpoint matches the '
                                                                    'published 348864 construction.',
                                                           'groups': [{'id': 'evidence',
                                                                       'title': 'What we have',
                                                                       'items': [{'title': 'A deterministic '
                                                                                           'correctness argument',
                                                                                  'text': 'The code-distance '
                                                                                          'bound makes every '
                                                                                          'honest weight-64 error '
                                                                                          'uniquely decodable. '
                                                                                          'Correct recovery then '
                                                                                          'follows if generation, '
                                                                                          'the decoder and '
                                                                                          'encoding faithfully '
                                                                                          'implement the '
                                                                                          'construction.',
                                                                                  'claim_id': 'mceliece-honest-agreement'},
                                                                                 {'title': 'A retained transform '
                                                                                           'and checked endpoint',
                                                                                  'text': 'The decoder and '
                                                                                          'SHAKE-based KEM '
                                                                                          'transform are '
                                                                                          'retained. Existing '
                                                                                          'conformance checks '
                                                                                          'match the published '
                                                                                          'endpoint; they do not '
                                                                                          'prove security of '
                                                                                          'every shorter code.',
                                                                                  'claim_id': 'mceliece-kem-bridge'}]},
                                                                      {'id': 'assumptions',
                                                                       'title': 'What we still assume',
                                                                       'items': [{'title': 'Decoding and hidden '
                                                                                           'structure resist '
                                                                                           'attack',
                                                                                  'text': 'Security concerns '
                                                                                          'these Goppa codes, not '
                                                                                          'arbitrary random '
                                                                                          'codes. Direct decoding '
                                                                                          'and equivalent-key '
                                                                                          'recovery both matter. '
                                                                                          'Small tiers are '
                                                                                          'deliberately weak; a '
                                                                                          'code distinguisher '
                                                                                          'alone is not key '
                                                                                          'recovery.',
                                                                                  'claim_id': 'mceliece-syndrome-recovery'},
                                                                                 {'title': 'The concrete KEM '
                                                                                           'meets the proof '
                                                                                           'premises',
                                                                                  'text': 'A full argument must '
                                                                                          'state its hash '
                                                                                          'idealizations and '
                                                                                          'match the exact key '
                                                                                          'distribution, error '
                                                                                          'sampling, encoding and '
                                                                                          'rejection behavior. '
                                                                                          'Code distance alone '
                                                                                          'supplies no '
                                                                                          'confidentiality '
                                                                                          'guarantee.',
                                                                                  'claim_id': 'mceliece-kem-bridge'}]},
                                                                      {'id': 'gaps',
                                                                       'title': 'What is missing here',
                                                                       'items': [{'title': 'A current, comparable '
                                                                                           'attack assessment',
                                                                                  'text': 'Improved decoding and '
                                                                                          'recent conditional '
                                                                                          'structural attacks '
                                                                                          'need compatible cost '
                                                                                          'analyses. Elementary '
                                                                                          'Prange trial counts '
                                                                                          'are not security '
                                                                                          'estimates, and this '
                                                                                          'fixed-degree '
                                                                                          'shortening study does '
                                                                                          'not establish '
                                                                                          'parameter optimality.',
                                                                                  'claim_id': 'mceliece-prange-reference'},
                                                                                 {'title': 'A complete argument '
                                                                                           'for the research '
                                                                                           'family',
                                                                                  'text': 'Apply the published '
                                                                                          'KEM framework to each '
                                                                                          'shortened '
                                                                                          'configuration, with '
                                                                                          'explicit assumptions '
                                                                                          'and quantitative '
                                                                                          'losses. Endpoint '
                                                                                          'conformance cannot '
                                                                                          'fill this gap or '
                                                                                          'certify the executable '
                                                                                          'decoder.',
                                                                                  'claim_id': 'mceliece-kem-bridge'}]}],
                                                           'boundary': 'The live challenge tests passive '
                                                                       'shared-secret recovery. An unsolved '
                                                                       'challenge is not an IND-CCA proof; '
                                                                       'structural attacks on secrecy do not '
                                                                       'invalidate the honest-correctness '
                                                                       'distance bound.'},
                                              'sections': [{'id': 'details',
                                                            'title': 'The argument in detail',
                                                            'description': 'Precise statements, their scope, '
                                                                           'sources and ways to challenge them.',
                                                            'claim_ids': ['mceliece-syndrome-recovery',
                                                                          'mceliece-honest-agreement',
                                                                          'mceliece-prange-reference',
                                                                          'mceliece-kem-bridge']}]}})

_REVISIONS.update({('teaching-dh-kem', '7'): 2,
 ('rsa-kem', '4'): 2,
 ('dhkem-x25519', 'curve-compressed-v2'): 2,
 ('ml-kem', 'compressed-research-v4'): 3,
 ('hqc', 'modeled-research-v3'): 2,
 ('sntrup', '3'): 2,
 ('classic-mceliece', 'length-research-v1'): 2})

"""Bounded, human-readable KEM specifications; never executable adapters.

Validation checks the document's shape and internal labels, not the truth of
correctness or security claims. Uploaded algorithms, URLs, and entry points
are inert text. Registry admission is a separate, reviewer-controlled action.
"""
import json
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SPEC_FORMAT = "crucible-kem-spec-v1"
SPEC_MAX_BYTES = 48 * 1024


def _text(limit):
    return Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=limit)]


ShortText = _text(1000)
LongText = _text(6000)
Tier = Annotated[int, Field(strict=True, ge=1, le=1_000_000_000)]


def _encoded_document(value):
    """Stable compact UTF-8 bytes; not a claim of RFC 8785 canonicalization."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("Specification must contain finite JSON values and valid Unicode.") from None
    if len(encoded) > SPEC_MAX_BYTES:
        raise ValueError("Specification must fit within 48 KiB of compact UTF-8 JSON.")
    return encoded


def _safe_url(value):
    if any(not char.isprintable() or char.isspace() for char in value) or "\\" in value:
        raise ValueError("Use an HTTPS URL without credentials or a registered source URL.")
    try:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed/out-of-range port strings.
        parsed.port
    except ValueError:
        raise ValueError("Invalid source or reference URL.") from None
    if (parsed.scheme == "https" and parsed.hostname and parsed.username is None
            and parsed.password is None):
        return value
    # Relative URLs are reserved to exact, registered source artifacts. No
    # supplied URL is fetched; do not broaden this into an arbitrary local path.
    from . import registry
    allowed = {"/api/protocol/source?" + urlencode({"scheme_id": scheme, "version": version})
               for scheme, version in registry.ADAPTERS}
    if value in allowed:
        return value
    raise ValueError("Use an HTTPS URL without credentials or an exact registered source URL.")


class SpecPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class TierRange(SpecPart):
    kind: Literal["range"]
    min: Tier
    max: Tier
    step: Tier

    @model_validator(mode="after")
    def ordered(self):
        if self.max < self.min or (self.max - self.min) % self.step:
            raise ValueError("The inclusive configuration-selector range must end on its positive step.")
        return self


class TierList(SpecPart):
    kind: Literal["list"]
    values: list[Tier] = Field(min_length=1, max_length=1024)

    @field_validator("values")
    @classmethod
    def ordered(cls, value):
        if value != sorted(set(value)):
            raise ValueError("Explicit configuration selectors must be unique and in increasing order.")
        return value


class Algorithms(SpecPart):
    setup: LongText = Field(description="Define public setup, its validation, and any trust assumptions.")
    keygen: LongText = Field(description="Define key generation, randomness, inputs, outputs, and checks.")
    encapsulate: LongText = Field(description="Define encapsulation, randomness, outputs, and public-key checks.")
    decapsulate: LongText = Field(description="Define decapsulation, validation, and behavior on invalid inputs, including rejection or a rejection key.")


class Parameters(SpecPart):
    tier_meaning: ShortText = Field(description="Legacy field name: explain the numeric configuration selector and distinguish it from a stage ordinal on the versioned default progression. Neither is comparable security bits.")
    supported_tiers: Annotated[TierRange | TierList, Field(discriminator="kind", description="Legacy numeric configuration selectors; may include configurations outside the default stage progression.")]
    mapping: LongText = Field(description="Map legacy supported_tiers selectors to stable exact configurations or reviewed generation rules. Separately map stage ordinals on the versioned default progression to configuration identities, including any reviewed off-route settings and their scope.")
    distribution: LongText = Field(description="Specify all randomness, conditioning, rejection, and sampling distributions.")
    reuse: _text(3000) = Field(description="Identify public and secret values reused across setups, keys, instances, accounts, or rounds.")


class Encoding(SpecPart):
    public_key: _text(3000)
    ciphertext: _text(3000)
    shared_secret: _text(3000) = Field(description="Give the secret encoding, KDF framing/domain separation, and any configuration-dependent output-length mapping.")
    shared_secret_bytes: Annotated[int, Field(strict=True, ge=1, le=4096)] | None = Field(
        description="Fixed output byte length, or null when variable/parameter-dependent; describe the mapping in shared_secret.")


class Correctness(SpecPart):
    kind: Literal["perfect", "statistical", "unknown"]
    failure_bound: ShortText | None = Field(description="Use '0' for perfect; a stated bound for statistical; null for unknown. Expressions are never evaluated.")
    argument: LongText
    scope: _text(3000) = Field(description="Specify admitted parameters, honest inputs, probability space/conditioning, and mathematical or implementation assumptions.")
    evidence: _text(3000) | None = None

    @model_validator(mode="after")
    def consistent_labels(self):
        if self.kind == "perfect" and self.failure_bound != "0":
            raise ValueError("Perfect correctness must state failure_bound '0'.")
        if self.kind == "statistical" and self.failure_bound is None:
            raise ValueError("Statistical correctness requires a stated failure bound.")
        if self.kind == "unknown" and self.failure_bound is not None:
            raise ValueError("Unknown correctness must use a null failure bound.")
        return self


class Security(SpecPart):
    notion: ShortText = Field(description="Name the claimed game/objective, or explicitly say no security notion is claimed.")
    model: ShortText = Field(description="State adversary/oracle, classical/quantum, cost and setup/precomputation model, or the absence of a security claim.")
    claim: _text(4000) = Field(description="Author's claim only; accepting this text does not verify or grant it.")
    assumptions: _text(4000)


class ParameterSelection(SpecPart):
    """A bounded selection argument, not a claim of global optimality."""
    objective: _text(3000) = Field(description="State the cost objective or Pareto tradeoff (for example ciphertext bytes versus computation), workload, and units. There is no universal cheapest construction.")
    constraints: _text(3000) = Field(description="Keep the mathematical family, distributions, transform, security game, common correctness envelope and interoperability constraints explicit. Justify coordinated parameter changes. Do not manufacture stages by padding, leaked information, specially easy keys or restricted seed/message entropy; identify inherent small-instance limits.")
    security_model: _text(4000) = Field(description="For each numerical target, pin threat and attack-cost models, estimator/source versions, setup/precomputation treatment and assumptions. If unavailable, state the missing evidence and keep the target unestimated.")
    search_domain: _text(4000) = Field(description="Specify candidate parameter ranges and admissibility filters, including natural family constraints. State what was actually searched and what was excluded; do not assert global optimality from a limited search.")
    comparison_evidence: _text(4000) = Field(description="Provide reproducible candidate comparisons or a mathematical dominance argument at each target. Within the stated model/domain/constraints, explain why a cheaper candidate meeting the target was not selected, or why the chosen point is non-dominated. Distinguish measured costs from modeled estimates.")
    tradeoffs_and_uncertainty: _text(4000) = Field(description="Explain competing objectives, model uncertainty, unsearched alternatives and justified exceptions (such as a published anchor). A weaker target alone is not a reason to make another parameter worse.")


class TierDesign(SpecPart):
    """Reviewable ladder choices, never an executable estimator or proof."""
    parameter_progression: _text(4000) = Field(description="Define admissible configurations separately from the versioned default human progression. State its ordered configuration mapping, changing parameters, invariants and natural scaling rule. Stages are local ordinal positions, not cross-scheme difficulty units. Distinct named parameter sets may have separate roles rather than form a progression.")
    cost_tradeoff: _text(4000) = Field(description="Identify communication, computation or memory costs and justify plateaus or published-anchor/encoding exceptions. Distinguish measured values from expectations; do not manufacture cost growth.")
    fixed_correctness: _text(4000) = Field(description="Explain why every supported profile meets the single correctness.failure_bound under its stated scope. A common negligible statistical bound is allowed; zero failure and identical per-profile probabilities are not required. Disclose per-profile bounds and any material variation. Separate generation/resource aborts from the common requirement.")
    endpoint_status: Literal["supported-estimate", "provisional", "unestimated"] = Field(description="Status of the endpoint attack-cost reference, not security certification. Supported estimates still need their model, scope and evidence.")
    endpoint_target: _text(2000) = Field(description="Retain the published or approved reference endpoint where applicable, or justify a new proposal's reference configuration. State its security target or absence of a numerical claim. The last stage does not acquire a standard security level by convention.")
    endpoint_evidence: _text(4000) = Field(description="Give source/model/version and reproducible evidence, applicable message/seed enumeration limits, or explicitly identify what is missing. Unknown estimates must remain unknown.")
    intermediate_steps: _text(4000) = Field(description="Explain which reviewed configurations form useful natural stages, any omitted alternatives, coarse gaps and practicality uncertainty. Prefer a default-stage count in multiples of ten, or five when more natural, if useful reviewed settings support it. This is a presentation preference, never an admissibility gate: retain off-route configurations and do not change cryptography for a count. Explain gaps and preserve configuration identity when the route changes.")
    parameter_selection: ParameterSelection | None = Field(default=None, description="Optional for historical documents and early drafts; required for new approval/admission, with explicit review of the selection objective, model, alternatives and evidence.")


class Implementation(SpecPart):
    url: _text(2048)
    sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = None
    artifact_sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, description="Exact full implementation closure hash when dependencies or multiple source artifacts participate.")
    runtime: ShortText | None = None
    entrypoint: ShortText | None = None

    _validate_url = field_validator("url")(_safe_url)


class Reference(SpecPart):
    title: _text(200)
    url: _text(2048)

    _validate_url = field_validator("url")(_safe_url)


class ProtocolSpec(SpecPart):
    format: Literal["crucible-kem-spec-v1"]
    name: _text(100)
    version: _text(32)
    summary: ShortText
    algorithms: Algorithms
    parameters: Parameters
    encoding: Encoding
    correctness: Correctness
    security: Security
    tier_design: TierDesign | None = Field(default=None, description="Optional for drafts and historical documents; required with explicit manual review before any new approval or admission.")
    implementation: Implementation | None = None
    references: list[Reference] = Field(max_length=16)
    instructions: dict[_text(100), _text(4000)] = Field(
        default_factory=dict, alias="_instructions", exclude=True, max_length=32,
        description="Optional template guidance. Excluded from the canonical stored specification and its hash.")

    @model_validator(mode="before")
    @classmethod
    def bounded_json(cls, value):
        if isinstance(value, dict):
            _encoded_document(value)
            if "_instructions" in value and value["_instructions"] is None:
                raise ValueError("When present, _instructions must be an object of strings.")
        return value

    @model_validator(mode="after")
    def bounded_canonical(self):
        result = self.model_dump(mode="json")
        if result.get("tier_design") is None:
            result.pop("tier_design", None)
        elif result["tier_design"].get("parameter_selection") is None:
            result["tier_design"].pop("parameter_selection", None)
        _encoded_document(result)
        return self


def canonical_spec(spec: ProtocolSpec) -> dict:
    """Return the bounded normalized document with template guidance removed."""
    if not isinstance(spec, ProtocolSpec):
        raise ValueError("Validate the specification with ProtocolSpec first.")
    result = spec.model_dump(mode="json")
    # New optional review metadata must not alter historical document hashes.
    if result.get("tier_design") is None:
        result.pop("tier_design", None)
    elif result["tier_design"].get("parameter_selection") is None:
        result["tier_design"].pop("parameter_selection", None)
    # Preserve canonical bytes for historical single-file reviewed revisions.
    if result.get("implementation") and result["implementation"].get("artifact_sha256") is None:
        result["implementation"].pop("artifact_sha256", None)
    _encoded_document(result)
    return result


def template_spec() -> dict:
    """An intentionally incomplete form, not an admissible example or claim."""
    return {
        "format": SPEC_FORMAT, "name": "", "version": "", "summary": "",
        "algorithms": {"setup": "", "keygen": "", "encapsulate": "", "decapsulate": ""},
        "parameters": {"tier_meaning": "", "supported_tiers": {"kind": "list", "values": []},
                       "mapping": "", "distribution": "", "reuse": ""},
        "encoding": {"public_key": "", "ciphertext": "", "shared_secret": "", "shared_secret_bytes": None},
        "correctness": {"kind": "unknown", "failure_bound": None, "argument": "", "scope": "", "evidence": None},
        "security": {"notion": "", "model": "", "claim": "", "assumptions": ""},
        "tier_design": {"parameter_progression": "", "cost_tradeoff": "", "fixed_correctness": "",
                        "endpoint_status": "unestimated", "endpoint_target": "", "endpoint_evidence": "",
                        "intermediate_steps": "", "parameter_selection": {
                            "objective": "", "constraints": "", "security_model": "", "search_domain": "",
                            "comparison_evidence": "", "tradeoffs_and_uncertainty": ""}},
        "implementation": None, "references": [],
        "_instructions": {
            "purpose": "Describe a KEM for human review. Passing validation checks document shape only; it does not prove correctness, security, executable conformity, or admission. No uploaded code is run or URL fetched.",
            "complete": "Replace every blank string and the empty supported_tiers list. Required text must be nonblank. No unlisted fields are accepted. Optional implementation and correctness.evidence may be null. Optional references may be an empty list.",
            "algorithms": "Describe setup, key generation, encapsulation and decapsulation completely: inputs, outputs, randomness, checks, KDF, and behavior on malformed public keys/ciphertexts, including explicit or implicit rejection. Pseudocode is inert text.",
            "parameters": "tier_meaning, supported_tiers, mapping, distribution and reuse retain their v1 wire names. supported_tiers lists legacy numeric configuration selectors, including reviewed off-route configurations; it need not match the default-stage count. Describe stable configuration identities and exact parameter sets or reviewed generation rules. Separately map each stage of the versioned default human progression to a configuration. Explain all random choices, conditioning, and reused public/secret values across setups, keys, instances, accounts and rounds. A stage is a scheme-local ordinal, not security bits or a cross-scheme difficulty comparison.",
            "tier_design": "Complete the seven stage-progression fields under the legacy tier_design key and the parameter_selection object before requesting approval. Separate admissible configurations from the default human route. Explain faithful natural scaling, real cost tradeoffs, one strong common correctness envelope, the reference endpoint and evidence, and useful intermediates or coarse gaps. Negligible nonzero failure is allowed; disclose stronger per-configuration bounds and material variation. Preserve a published or approved endpoint where applicable; justify a new construction's endpoint. Prefer a default-stage count in multiples of ten, or five when useful natural settings make that more appropriate. This is a presentation preference, not a scientific requirement or admission gate. Keep omitted reviewed configurations accessible; never change the construction, remove an endpoint or invent weak settings merely to fit a count. All useful natural settings may be stages when curation is unhelpful. No fixed 50 or linear security spacing is required. endpoint_status is supported-estimate, provisional, or unestimated; none grants a security claim. Keep native message/seed enumeration limits distinct from modeled attack costs. The plan may be omitted or null for an early draft, but new approval/admission requires it and manual checks.",
            "parameter_selection": "Choose defensible parameters within the declared construction, correctness contract, workload, cost objective and attack model. State search domain, reproducible comparison evidence and uncertainty; explain competing Pareto tradeoffs and justified reference anchors. Fidelity comes before a favorable estimator result. Coordinated changes in dimension, modulus, noise or compression are allowed with justification. No padding, specially easy keys, leaked information, selective resampling or artificially reduced randomness to manufacture stages. A naturally smaller input space may be inherent; report generic attacks and limits on extrapolation. A finite search is not a global optimum or security proof. If calibrated estimates are unavailable, compare justified structural/resource choices and retain unestimated claims. Text is never executed, formulas evaluated, or URLs fetched.",
            "supported_tiers": "Legacy v1 name for the supported numeric configuration selectors, including any reviewed off-route settings. These retain their original meaning when the displayed progression is curated. Separately describe default stage ordinals 1..N and their exact configuration mapping in mapping/parameter_progression; stage numbers are not stable configuration IDs. Use {\"kind\":\"range\",\"min\":1,\"max\":11,\"step\":1} (inclusive, max lies on step), or {\"kind\":\"list\",\"values\":[1,2,3]}. Values are strictly positive integers at most 1000000000, never booleans; lists are ascending, unique and contain at most 1024 items. Existing historical labels remain valid.",
            "versioning": "Version exact configuration identities, the default progression mapping, and security evidence separately in mapping/parameter_progression and endpoint_evidence. Stage numbers are presentation labels: inserting or reordering a stage must not redefine an existing configuration or relabel past recovery receipts. Frozen releases/manifests retain their exact bytes. The v1 service binds proposals to registered release/profile IDs; it does not yet provide an arbitrary configuration explorer or an independent progression-edit API. Describe new identities and migration explicitly for review. Ordinal scoring remains provisional. Curation or renumbering must not change existing configuration credit; this template does not change awards or adopt a new formula.",
            "encoding": "Specify canonical public-key/ciphertext/secret formats, integer widths, framing and KDF domains. shared_secret_bytes is an integer 1..4096 for a fixed byte length, or null with its parameter-dependent length mapping described in shared_secret.",
            "correctness": "Use kind perfect and failure_bound \"0\"; statistical and a nonblank bound string; or unknown and null. Explain the argument or what remains unknown. scope gives honest inputs, admitted configurations, probability space/conditioning and mathematical/implementation assumptions. Every admitted configuration, including off-route settings, must meet one reviewed common bound; proof coverage of a generation rule must cover its entire admitted domain. Nonzero negligible failure is allowed. Disclose stronger individual bounds and material variation; keep resource aborts separate. For adapter admission, statistical profiles declare the exact common bound text; stronger results go in evidence. Perfect profiles with bound \"0\" may satisfy a statistical envelope. Bounds are never automatically proved.",
            "security": "State the notion, adversary and oracle/model assumptions, classical/quantum and precomputation/cost context, claim and underlying assumptions. Explicitly say when no security guarantee or reduction is claimed. Recovery of an instance is not a general security proof.",
            "implementation": "Initially null or {\"url\":\"https://example.org/source.py\",\"sha256\":null,\"runtime\":null,\"entrypoint\":null}. A reviewed reference artifact and exact SHA-256 are required before activation; submitting a link does not cause execution. A hash, when supplied, is 64 lowercase hexadecimal characters. Reviewer-controlled adapter binding is separate.",
            "links": "References are {\"title\":\"...\",\"url\":\"https://...\"}. At most 16. URLs must be HTTPS without credentials, or an exact existing Crucible /api/protocol/source?scheme_id=...&version=... URL. Source links and hashes identify artifacts, not endorsement.",
            "limits": "UTF-8 JSON file and compact normalized specification must each fit 48 KiB (49152 bytes); the complete API request also has a 64 KiB limit. No NaN, infinity or unpaired Unicode surrogates. name 100; version 32; summary 1000; each algorithm 6000; mapping/distribution 6000; reuse 3000; each encoding description 3000; correctness argument 6000, scope/evidence 3000, bound 1000; security notion/model 1000, claim/assumptions 4000; tier_design text fields 4000 except endpoint_target 2000; nested parameter_selection objective/constraints 3000 and other fields 4000; URL 2048; reference title 200; runtime/entrypoint 1000 characters.",
            "instructions": "This optional _instructions object may contain at most 32 nonblank string entries (keys at most 100, values 4000 characters). It is validated then removed from the normalized stored document and its content hash. Old files without it are accepted.",
        },
    }


def builtin_spec(scheme_id, version=None) -> dict:
    """Describe an exact trusted release without generating any instance.

    These two audited translations are deliberately explicit. A future adapter
    must provide its own description rather than inheriting guessed semantics.
    """
    from . import registry
    adapter = registry.adapter_for(scheme_id, version)
    from .standard_schemes import SCHEMES
    if scheme_id in SCHEMES:
        if adapter.manifests()[0].get("profile_kind") == "research-tier":
            from .research_schemes import details_for
            return _research_spec(adapter, details_for(adapter, registry.DETAILS[scheme_id]))
        return _standard_spec(adapter, registry.DETAILS[scheme_id])
    if scheme_id not in ("teaching-dh-kem", "rsa-kem"):
        raise ValueError("No specification translation is registered for this scheme.")
    profiles = adapter.manifests()
    first = profiles[0]
    for profile in profiles[1:]:
        for field in ("algorithms", "distribution", "kdf", "correctness", "security_claim",
                      "assumption_context", "shared_secret_bytes", "implementation"):
            if profile[field] != first[field]:
                raise ValueError("This release requires a parameter-dependent specification translation.")
    tiers = sorted(profile.get("instance_size", profile["parameter_bits"]) for profile in profiles)
    supported = ({"kind": "range", "min": tiers[0], "max": tiers[-1], "step": 1}
                 if tiers == list(range(tiers[0], tiers[-1] + 1))
                 else {"kind": "list", "values": tiers})
    size = first.get("size_parameter")
    mapping = size["mapping"] if size else "Select the exact registered parameters for each subgroup-order bit length: " + "; ".join(
        f'{profile["parameter_bits"]}: p=0x{profile["parameters"]["p"]}, q=0x{profile["parameters"]["q"]}, g=0x{profile["parameters"]["g"]}'
        for profile in profiles)
    algorithms = dict(first["algorithms"])
    model = registry.INITIAL_SECURITY_MODELS.get(first.get("initial_security_estimate", {}).get("model_id"))
    calibrated = model is not None
    if scheme_id == "teaching-dh-kem":
        algorithms["setup"] = "Select the registered p,q,g for the chosen profile; p=2q+1 and q are prime, and g has order q. Validate the profile using the checks in this release's pinned source. Setup uses fixed public parameters; it does not sample fresh primes."
        if calibrated:
            algorithms["setup"] = "Select the tier's pinned certified p,q,g. q is a prime divisor of p-1, q^2>p, and g has order q. Verify the recursive Pocklington certificates embedded in the source. Public SHAKE-derived setup constants are reproducible; no prime search occurs while serving DH challenges."
        check = "Require exactly ceil(bit_length(p)/8) bytes encoding an integer a with 1<a<p-1 and a^q mod p=1; otherwise raise ValueError."
        algorithms["encapsulate"] += " Public-key validation: " + check
        exponent_range = ("1..M, where M=min(q-1,2^ceil(2*initial_reference_bits+32)-1), with the exact bound pinned in each profile"
                          if adapter.SCHEME_VERSION == "7" else "1..q-1")
        algorithms["decapsulate"] += " Ciphertext validation: " + check + " The private exponent must be an integer in " + exponent_range + ". Invalid inputs raise ValueError, not a replacement key. The challenge service exposes no decapsulation oracle."
        reuse = "The registered public p,q,g for a profile are shared across all keys, accounts and rounds. Each newly issued instance uses independent fresh secret x and encapsulation exponent r. Retrieving the same issued instance reuses that instance, rather than resampling it."
        public_key = "One group element g^x mod p; " + first["encoding"]["group_elements"] + ". Width is ceil(bit_length(p)/8)=ceil((n+1)/8) bytes at tier n. Registered p,q,g are separate shared public parameters, not public-key bytes."
        if calibrated:
            public_key = "One group element g^x mod p; " + first["encoding"]["group_elements"] + ". Width is ceil(bit_length(p)/8) for the exact registered field; tier and subgroup-order bit length are separate. Registered p,q,g are shared parameters, excluded from public-key bytes."
        ciphertext = "One group element g^r mod p; same fixed-width encoding as the public key."
    else:
        algorithms["setup"] = "Select a registered modulus-bit-length profile n and the fixed public exponent e=65537. No shared RSA modulus or secret setup is used; key generation creates the primes and modulus."
        if calibrated:
            algorithms["setup"] = "Select tier t in1..50 and its exact registered modulus bit length n. The fixed public exponent is e=65537. Key generation samples fresh balanced recursively certified primes from the explicit structured distribution, with bounded candidate searches; no shared modulus or secret setup is used."
        public_key_length = "ceil(n/8)+3" if adapter.SCHEME_VERSION == "4" else "2*ceil(n/8)"
        algorithms["encapsulate"] += " Validate an exact " + public_key_length + "-byte public key encoding N||e, with N odd of exactly n bits and e=65537; invalid encodings raise ValueError."
        algorithms["decapsulate"] += " Require exactly ceil(n/8) ciphertext bytes encoding 0<=c<N; malformed or out-of-range input raises ValueError. Validate the private factors' registered bit lengths, primality and distinctness, the modulus bit length, and ed=1 mod ((p-1)(q-1)); invalid private keys raise ValueError. There is no replacement rejection key or challenge decapsulation oracle."
        reuse = "The exponent e=65537 and profile definition are shared across instances. Each newly issued instance independently generates fresh primes p,q and thus N,d, then a fresh encapsulation z. Retrieving the same issued instance returns those existing public values, rather than resampling. Independently sampled values may coincide."
        public_key = first["encoding"]["public_key"] + "; " + first["encoding"]["integers"] + ". integer_bytes=ceil(n/8); e is padded to the same width as N. This is not DER or a minimal-exponent encoding."
        if adapter.SCHEME_VERSION == "4":
            public_key = first["encoding"]["public_key"] + "; " + first["encoding"]["integers"] + ". N uses ceil(n/8) bytes and e=65537 uses exactly three big-endian bytes 01 00 01, including in the KDF binding. This is the registered compact encoding, not DER."
        ciphertext = first["encoding"]["ciphertext"] + "; " + first["encoding"]["integers"] + ". integer_bytes=ceil(n/8)."
    source_url = "/api/protocol/source?" + urlencode({"scheme_id": scheme_id, "version": adapter.SCHEME_VERSION})
    correctness = first["correctness"]
    refs = [{"title": "Exact registered reference source artifact", "url": source_url}]
    refs.extend({"title": ref["title"], "url": ref["url"]} for ref in first["references"] if "url" in ref)
    if calibrated:
        existing = {ref["url"] for ref in refs}
        refs.extend(ref for ref in model["references"] if ref["url"] not in existing)
    details = registry.DETAILS[scheme_id]
    result = {
        "format": SPEC_FORMAT, "name": details["name"], "version": adapter.SCHEME_VERSION,
        "summary": details["summary"], "algorithms": algorithms,
        "parameters": {"tier_meaning": size["meaning"] if size else first["parameter_label"],
                       "supported_tiers": supported, "mapping": mapping,
                       "distribution": first["distribution"], "reuse": reuse},
        "encoding": {"public_key": public_key, "ciphertext": ciphertext,
                     "shared_secret": first["encoding"]["shared_secret"] + ". Exact KDF definition: " + json.dumps(first["kdf"], ensure_ascii=False, sort_keys=True),
                     "shared_secret_bytes": first["shared_secret_bytes"]},
        "correctness": {"kind": correctness["kind"], "failure_bound": correctness["failure_bound"],
                        "argument": correctness["argument"],
                        "scope": correctness["scope"] + " Assumptions: " + correctness["assumptions"],
                        "evidence": correctness["evidence_status"] + " " + correctness["implementation_checks"]},
        "security": {"notion": "The challenge objective is full encapsulated shared-secret recovery. No IND-CPA or IND-CCA security claim is made for this adapter.",
                     "model": "Public transcript and shared setup are available to the challenger; no decapsulation oracle. No classical or quantum security level, concrete attack-cost bound, or precomputation limit is asserted.",
                     "claim": first["security_claim"], "assumptions": first["assumption_context"]},
        "implementation": {"url": source_url, "sha256": first["implementation"]["source_sha256"],
                           "runtime": first["implementation"]["runtime"],
                           "entrypoint": "keygen(profile_id); encapsulate(profile_id, public_key); decapsulate(profile_id, private_key, ciphertext)"},
        "references": refs,
    }
    if calibrated:
        result["security"]["model"] = model["scope"]
        result["security"]["claim"] = first["security_claim"] + " " + model["formula"] + " " + " ".join(model["limitations"])
    return canonical_spec(ProtocolSpec.model_validate(result))


def _research_spec(adapter, details):
    """Bounded specification for a pinned mathematical parameter ladder.

    Exact potentially-large public constants live in the manifest/source
    closure. The mapping names that immutable authority; no numeric table is
    silently truncated to fit the prose submission format.
    """
    profiles = adapter.manifests()
    first = profiles[0]
    correctness = getattr(adapter, "CORRECTNESS_SPEC", first["correctness"])
    for profile in profiles:
        if profile["profile_kind"] != "research-tier" or (not hasattr(adapter, "CORRECTNESS_SPEC") and profile["correctness"] != correctness):
            raise ValueError("Research ladder needs a common correctness statement covering every registered profile.")
    source_url = "/api/protocol/source?" + urlencode({"scheme_id": adapter.SCHEME_ID, "version": adapter.SCHEME_VERSION})
    parameters = first["size_parameter"]
    def common(path):
        values = [profile[path[0]][path[1]] if len(path) == 2 else profile[path[0]] for profile in profiles]
        return "\n".join(dict.fromkeys(map(str, values)))
    lengths = {profile["shared_secret_bytes"] for profile in profiles}
    exact_policy = "Records apply only to the exact recovered profile." if first.get("record_inheritance", False) is False else "Higher-tier recognition at lower tiers is a leaderboard convention; it does not assert recovery of their individual instances."
    result = {
        "format": SPEC_FORMAT, "name": details["name"], "version": adapter.SCHEME_VERSION,
        "summary": details["summary"],
        "algorithms": {"setup": details["construction"] + " The registered manifests and pinned source/data closure define every exact public parameter and validation rule.",
                       **{field: common(("algorithms", field)) for field in ("keygen", "encapsulate", "decapsulate")}},
        "parameters": {"tier_meaning": parameters.get("meaning", "Scheme-specific research parameter progression; no cross-scheme equivalence."),
            "supported_tiers": {"kind": "list", "values": [p["instance_size"] for p in profiles]},
            "mapping": parameters["mapping"] + f' Exact profile IDs run from {profiles[0]["id"]} through {profiles[-1]["id"]}. For every profile, the registered manifest parameters and encoding fields are normative, bound by implementation artifact SHA-256 {first["implementation"]["artifact_sha256"]}; inspect the source/data closure for the complete reproducible table. The source URL is ' + source_url,
            "distribution": common(("distribution",)) + " The service records each attempted draw and checks honest agreement once. It withholds disagreements without retrying; issued transcripts are consequently conditioned on agreement. The ledger includes unsuccessful and discarded attempts.",
            "reuse": "Registered public parameters and implementation are reused; each issued challenge uses fresh secret key and encapsulation randomness. Re-fetching returns the same per-account, per-profile, hourly instance. " + exact_policy},
        "encoding": {"public_key": common(("encoding", "public_key")) + " Exact byte lengths are declared per profile; they exclude hexadecimal and JSON wrappers.",
            "ciphertext": common(("encoding", "ciphertext")) + " Exact byte lengths are declared per profile.",
            "shared_secret": common(("encoding", "shared_secret")) + " KDF contract: " + json.dumps(first["kdf"], sort_keys=True),
            "shared_secret_bytes": next(iter(lengths)) if len(lengths) == 1 else None},
        "correctness": {"kind": correctness["kind"], "failure_bound": correctness["failure_bound"],
            "argument": correctness["argument"], "scope": correctness["scope"],
            "evidence": correctness.get("evidence_status", "Independent review pending.") + " " + correctness["implementation_checks"]},
        "security": {"notion": "Recover the entire encapsulated shared secret from an honest public transcript; no general confidentiality theorem is claimed for research derivatives.",
            "model": "Passive public-input recovery without a decapsulation oracle. Compute and precomputation are self-reported; deadlines do not impose a compute budget.",
            "claim": common(("security_claim",)), "assumptions": common(("assumption_context",))},
        "implementation": {"url": source_url, "sha256": first["implementation"]["source_sha256"],
            "artifact_sha256": first["implementation"]["artifact_sha256"], "runtime": first["implementation"]["runtime"],
            "entrypoint": "generate(profile_id); full source/data/native dependency closure is pinned separately and available from /api/protocol/artifacts."},
        "references": [{"title": "Exact registered research adapter", "url": source_url}, *first["references"]],
    }
    return canonical_spec(ProtocolSpec.model_validate(result))


def _standard_spec(adapter, details):
    """Translate explicitly registered named-profile adapters, without a ladder.

    v1's supported_tiers field remains an ordinal compatibility index. The
    mapping names every exact profile; the index carries no cost/security claim.
    All facts come from that immutable profile, including varying output sizes.
    """
    profiles = adapter.manifests()
    if not profiles or any(p.get("profile_kind") != "standard-parameter-set" for p in profiles):
        raise ValueError("Expected exact published parameter sets.")
    first = profiles[0]
    def per_profile(field):
        values = [p[field] for p in profiles]
        return values[0] if all(v == values[0] for v in values) else "\n".join(
            p["profile_label"] + ": " + str(p[field]) for p in profiles)
    def algorithm(field):
        return "\n".join(p["profile_label"] + ": " + p["algorithms"][field] for p in profiles)
    def encoding(field):
        return "\n".join(p["profile_label"] + ": " + p["encoding"][field]
            + (f' ({p["encoding"][field + "_bytes"]} bytes)' if field + "_bytes" in p["encoding"] else "")
            for p in profiles)
    correctness = first["correctness"]
    if any(p["correctness"] != correctness for p in profiles):
        raise ValueError("Parameter-dependent correctness needs an explicit specification translation.")
    lengths = {p["shared_secret_bytes"] for p in profiles}
    source_url = "/api/protocol/source?" + urlencode({"scheme_id": adapter.SCHEME_ID, "version": adapter.SCHEME_VERSION})
    refs = [{"title": "Exact registered adapter source", "url": source_url}]
    seen = {source_url}
    for p in profiles:
        for ref in p["references"]:
            if ref.get("url") and ref["url"] not in seen:
                refs.append({"title": ref["title"], "url": ref["url"]})
                seen.add(ref["url"])
    result = {
        "format": SPEC_FORMAT, "name": details["name"], "version": adapter.SCHEME_VERSION,
        "summary": details["summary"],
        "algorithms": {"setup": "Choose an exact published parameter set from the mapping. No interpolation or reduced-entropy variants are permitted.",
                       **{field: algorithm(field) for field in ("keygen", "encapsulate", "decapsulate")}},
        "parameters": {"tier_meaning": "Compatibility ordinals for named published parameter sets only; not challenge tiers, security bits, or comparable difficulty.",
            "supported_tiers": {"kind": "list", "values": sorted(p["instance_size"] for p in profiles)},
            "mapping": "\n".join(f'{p["instance_size"]}: {p["id"]} — {p["profile_label"]}; ' + json.dumps(p["parameters"], sort_keys=True) for p in profiles),
            "distribution": per_profile("distribution") + " The service records each draw and withholds any failed agreement check without resampling. Thus issued challenges are conditioned on honest agreement; this service policy is separate from the published algorithm's distribution.",
            "reuse": "Only the published scheme definition and fixed parameters are reused. Fresh private keys and encapsulation randomness are generated for each issued instance. Re-fetching an issued challenge returns that same instance. Each named set has independent records; no cross-profile recovery inheritance."},
        "encoding": {"public_key": encoding("public_key"), "ciphertext": encoding("ciphertext"),
            "shared_secret": encoding("shared_secret") + ". Recover the complete published KEM output, with no application-level shortening. KDF details: " + json.dumps(first["kdf"], sort_keys=True),
            "shared_secret_bytes": next(iter(lengths)) if len(lengths) == 1 else None},
        "correctness": {"kind": correctness["kind"], "failure_bound": correctness["failure_bound"],
            "argument": correctness["argument"], "scope": correctness["scope"],
            "evidence": correctness["evidence_status"] + " " + correctness["implementation_checks"]},
        "security": {"notion": "This challenge measures recovery of the entire encapsulated KEM shared secret from one honest public transcript.",
            "model": "Passive public-input recovery with no decapsulation oracle; self-reported compute and preparation. This narrower experiment neither proves nor exhaustively tests IND-CCA security or implementation side channels.",
            "claim": per_profile("security_claim"), "assumptions": per_profile("assumption_context")},
        "implementation": {"url": source_url, "sha256": first["implementation"]["source_sha256"],
            "artifact_sha256": first["implementation"]["artifact_sha256"],
            "runtime": first["implementation"]["runtime"],
            "entrypoint": "generate(profile_id) in the registered adapter; complete dependency/source closure is pinned separately in each profile's implementation.artifact_sha256 and available from /api/protocol/artifacts."},
        "references": refs,
    }
    return canonical_spec(ProtocolSpec.model_validate(result))

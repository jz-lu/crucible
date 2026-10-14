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
            raise ValueError("The inclusive tier range must end on its positive step.")
        return self


class TierList(SpecPart):
    kind: Literal["list"]
    values: list[Tier] = Field(min_length=1, max_length=1024)

    @field_validator("values")
    @classmethod
    def ordered(cls, value):
        if value != sorted(set(value)):
            raise ValueError("Explicit tiers must be unique and in increasing order.")
        return value


class Algorithms(SpecPart):
    setup: LongText = Field(description="Define public setup, its validation, and any trust assumptions.")
    keygen: LongText = Field(description="Define key generation, randomness, inputs, outputs, and checks.")
    encapsulate: LongText = Field(description="Define encapsulation, randomness, outputs, and public-key checks.")
    decapsulate: LongText = Field(description="Define decapsulation, validation, and behavior on invalid inputs, including rejection or a rejection key.")


class Parameters(SpecPart):
    tier_meaning: ShortText = Field(description="A scheme-specific control, not automatically comparable security bits.")
    supported_tiers: Annotated[TierRange | TierList, Field(discriminator="kind")]
    mapping: LongText = Field(description="Map each supported tier to exact parameters or a fully specified generation procedure.")
    distribution: LongText = Field(description="Specify all randomness, conditioning, rejection, and sampling distributions.")
    reuse: _text(3000) = Field(description="Identify public and secret values reused across setups, keys, instances, accounts, or rounds.")


class Encoding(SpecPart):
    public_key: _text(3000)
    ciphertext: _text(3000)
    shared_secret: _text(3000) = Field(description="Give the secret encoding, KDF framing/domain separation, and any tier-dependent output-length mapping.")
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


class Implementation(SpecPart):
    url: _text(2048)
    sha256: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = None
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
        _encoded_document(self.model_dump(mode="json"))
        return self


def canonical_spec(spec: ProtocolSpec) -> dict:
    """Return the bounded normalized document with template guidance removed."""
    if not isinstance(spec, ProtocolSpec):
        raise ValueError("Validate the specification with ProtocolSpec first.")
    result = spec.model_dump(mode="json")
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
        "implementation": None, "references": [],
        "_instructions": {
            "purpose": "Describe a KEM for human review. Passing validation checks document shape only; it does not prove correctness, security, executable conformity, or admission. No uploaded code is run or URL fetched.",
            "complete": "Replace every blank string and the empty supported_tiers list. Required text must be nonblank. No unlisted fields are accepted. Optional implementation and correctness.evidence may be null. Optional references may be an empty list.",
            "algorithms": "Describe setup, key generation, encapsulation and decapsulation completely: inputs, outputs, randomness, checks, KDF, and behavior on malformed public keys/ciphertexts, including explicit or implicit rejection. Pseudocode is inert text.",
            "parameters": "tier_meaning, mapping, distribution and reuse must explain exact parameters, all random choices and conditioning, and which public/secret values are reused across setups, keys, instances, accounts and rounds. Tier labels do not establish comparable security.",
            "supported_tiers": "Use {\"kind\":\"range\",\"min\":20,\"max\":40,\"step\":1} (inclusive, max lies on step), or {\"kind\":\"list\",\"values\":[20,24,28]}. Values are strictly positive integers at most 1000000000, never booleans; lists are ascending, unique and contain at most 1024 items.",
            "encoding": "Specify canonical public-key/ciphertext/secret formats, integer widths, framing and KDF domains. shared_secret_bytes is an integer 1..4096 for a fixed byte length, or null with its parameter-dependent length mapping described in shared_secret.",
            "correctness": "Use kind perfect and failure_bound \"0\"; statistical and a nonblank bound string; or unknown and null. Explain the argument or what remains unknown. scope must give honest inputs, admitted parameters, the probability space/conditioning and implementation/mathematical assumptions. Evidence is a description, not an attestation.",
            "security": "State the notion, adversary and oracle/model assumptions, classical/quantum and precomputation/cost context, claim and underlying assumptions. Explicitly say when no security guarantee or reduction is claimed. Recovery of an instance is not a general security proof.",
            "implementation": "Initially null or {\"url\":\"https://example.org/source.py\",\"sha256\":null,\"runtime\":null,\"entrypoint\":null}. A reviewed reference artifact and exact SHA-256 are required before activation; submitting a link does not cause execution. A hash, when supplied, is 64 lowercase hexadecimal characters. Reviewer-controlled adapter binding is separate.",
            "links": "References are {\"title\":\"...\",\"url\":\"https://...\"}. At most 16. URLs must be HTTPS without credentials, or an exact existing Crucible /api/protocol/source?scheme_id=...&version=... URL. Source links and hashes identify artifacts, not endorsement.",
            "limits": "UTF-8 JSON file and compact normalized specification must each fit 48 KiB (49152 bytes); the complete API request also has a 64 KiB limit. No NaN, infinity or unpaired Unicode surrogates. name 100; version 32; summary 1000; each algorithm 6000; mapping/distribution 6000; reuse 3000; each encoding description 3000; correctness argument 6000, scope/evidence 3000, bound 1000; security notion/model 1000, claim/assumptions 4000; URL 2048; reference title 200; runtime/entrypoint 1000 characters.",
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
        algorithms["decapsulate"] += " Ciphertext validation: " + check + " The private exponent must be an integer in 1..q-1. Invalid inputs raise ValueError, not a replacement key. The challenge service exposes no decapsulation oracle."
        reuse = "The registered public p,q,g for a profile are shared across all keys, accounts and rounds. Each newly issued instance uses independent fresh secret x and encapsulation exponent r. Retrieving the same issued instance reuses that instance, rather than resampling it."
        public_key = "One group element g^x mod p; " + first["encoding"]["group_elements"] + ". Width is ceil(bit_length(p)/8)=ceil((n+1)/8) bytes at tier n. Registered p,q,g are separate shared public parameters, not public-key bytes."
        if calibrated:
            public_key = "One group element g^x mod p; " + first["encoding"]["group_elements"] + ". Width is ceil(bit_length(p)/8) for the exact registered field; tier and subgroup-order bit length are separate. Registered p,q,g are shared parameters, excluded from public-key bytes."
        ciphertext = "One group element g^r mod p; same fixed-width encoding as the public key."
    else:
        algorithms["setup"] = "Select a registered modulus-bit-length profile n and the fixed public exponent e=65537. No shared RSA modulus or secret setup is used; key generation creates the primes and modulus."
        if calibrated:
            algorithms["setup"] = "Select tier t in1..50 and its exact registered modulus bit length n. The fixed public exponent is e=65537. Key generation samples fresh balanced recursively certified primes from the explicit structured distribution, with bounded candidate searches; no shared modulus or secret setup is used."
        algorithms["encapsulate"] += " Validate an exact 2*ceil(n/8)-byte public key encoding N||e, with N odd of exactly n bits and e=65537; invalid encodings raise ValueError."
        algorithms["decapsulate"] += " Require exactly ceil(n/8) ciphertext bytes encoding 0<=c<N; malformed or out-of-range input raises ValueError. Validate the private factors' registered bit lengths, primality and distinctness, the modulus bit length, and ed=1 mod ((p-1)(q-1)); invalid private keys raise ValueError. There is no replacement rejection key or challenge decapsulation oracle."
        reuse = "The exponent e=65537 and profile definition are shared across instances. Each newly issued instance independently generates fresh primes p,q and thus N,d, then a fresh encapsulation z. Retrieving the same issued instance returns those existing public values, rather than resampling. Independently sampled values may coincide."
        public_key = first["encoding"]["public_key"] + "; " + first["encoding"]["integers"] + ". integer_bytes=ceil(n/8); e is padded to the same width as N. This is not DER or a minimal-exponent encoding."
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

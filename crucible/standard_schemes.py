"""Presentation contracts for exact published KEM experiments.

Local beta availability is distinct from independent proposal admission. The
ordered profile list is a navigation convention, never a security calibration.
"""

SCHEMES = {
    "dhkem-x25519": {
        "name": "DHKEM (X25519)", "display_group": None,
        "summary": "Recover the shared secret of an RFC 9180 X25519 encapsulation.",
        "construction": "RFC 9180 DHKEM(X25519, HKDF-SHA256), KEM ID 0x0020. Base encapsulation derives a shared key with labeled HKDF from an ephemeral X25519 exchange and its public context.",
        "strengths": ["A precisely specified KEM around the widely used X25519 primitive.", "Compact public keys and encapsulations."],
        "limitations": ["This is the fixed X25519 parameter set, with no reduced-entropy tier ladder.", "The experiment does not test the complete HPKE encryption stack or implementation side channels."],
    },
    "ml-kem": {
        "name": "ML-KEM", "display_group": "lattices",
        "summary": "Explore the published ML-KEM parameter sets.",
        "construction": "The FIPS 203 module-lattice key encapsulation mechanism, with its specified encoding, hashing and implicit rejection. Each challenge uses the exact named parameter set.",
        "strengths": ["A standardized module-lattice baseline.", "Published parameter sets and conformance vectors."],
        "limitations": ["Honest agreement has a small nonzero failure probability under the analysis assumptions.", "Public-input recovery does not cover chosen-ciphertext or side-channel attacks."],
    },
    "frodokem": {
        "name": "FrodoKEM", "display_group": "lattices",
        "summary": "Explore the salted FrodoKEM construction based on plain LWE.",
        "construction": "The salted FrodoKEM construction with the named SHAKE parameter sets. Matrix dimensions and noise distributions remain those of the published construction.",
        "strengths": ["A plain-LWE comparison with the module-lattice entries.", "Explicit communication versus algebraic-structure tradeoffs."],
        "limitations": ["Larger public data than the module-lattice baseline.", "Statistical correctness requires its declared noise model and exact parameter set."],
    },
    "sntrup": {
        "name": "Streamlined NTRU Prime", "display_group": "lattices",
        "summary": "Recover a shared secret from the published sntrup761 construction.",
        "construction": "The Streamlined NTRU Prime sntrup761 KEM over its specified quotient ring, with the published encodings, hash domains and confirmation checks. This is not NTRU LPRime or an SSH hybrid.",
        "strengths": ["An NTRU-style comparison with a different ring design.", "Perfect honest correctness for admissible parameters."],
        "limitations": ["A standalone recovery does not establish a break of an SSH hybrid key exchange.", "No smaller experimental parameter family is implied by this named set."],
    },
    "hqc": {
        "name": "HQC", "display_group": "codes",
        "summary": "Explore the published August 2025 HQC construction.",
        "construction": "HQC as published on 22 August 2025 and implemented in the pinned native release. The October 2026 specification changes parameters and encodings; this challenge does not claim conformance to that later revision.",
        "strengths": ["A quasi-cyclic code comparison with the lattice entries.", "A different decoder and correctness model from Classic McEliece."],
        "limitations": ["This exact August 2025 release predates the October 2026 revision.", "The decryption-failure analysis is statistical and model-dependent; compiled implementation behavior needs separate review."],
    },
    "classic-mceliece": {
        "name": "Classic McEliece", "display_group": "codes",
        "summary": "Study shared-secret recovery for the published binary Goppa-code KEM.",
        "construction": "The published Classic McEliece KEM, with explicit named profiles and canonical encodings. Its hidden Goppa structure and public decoding target are distinct from HQC.",
        "strengths": ["Perfect honest correctness under valid generation and exact implementation.", "A useful large-public-key, small-ciphertext comparison."],
        "limitations": ["Recent structural cryptanalysis has materially changed security assessments; this is a research target, not a deployment recommendation.", "Public keys can exceed one megabyte and require bounded generation and serialization."],
    },
}


def scoring_presentation(manifest):
    # Imported at call time: registry construction also imports this module.
    # Policy changes must not edit a frozen adapter or historical manifest.
    from . import scoring
    return {"scoring_eligible": scoring.manifest_eligible(manifest), "scoring_policy_id": scoring.POLICY_ID}


def profile_presentation(manifest):
    """Attach only public presentation metadata, preserving exact record scope."""
    if manifest.get("profile_kind") == "research-tier":
        return {"selection_kind": "tier", "profile_kind": "research-tier",
                "profile_label": manifest.get("profile_label", f'Tier {manifest["instance_size"]}'),
                "record_inheritance": manifest.get("record_inheritance", False), **scoring_presentation(manifest)}
    if manifest.get("profile_kind") != "standard-parameter-set":
        return scoring_presentation(manifest)
    return {"selection_kind": "parameter-set", "profile_kind": "standard-parameter-set",
            "profile_label": manifest["profile_label"], "record_inheritance": False,
            **scoring_presentation(manifest)}


def beta_metadata(scheme_id, manifest=None):
    if scheme_id not in SCHEMES:
        return {"selection_kind": "tier", "display_group": None, "record_inheritance": True,
                **scoring_presentation(manifest or {})}
    if manifest and manifest.get("profile_kind") == "research-tier":
        return {"selection_kind": "tier", "release_kind": "research-tier",
                "display_group": SCHEMES[scheme_id]["display_group"],
                "record_inheritance": manifest.get("record_inheritance", False), **scoring_presentation(manifest),
                "review_status": "local-beta", "origin": "known",
                "review_note": "Research tiers. Exact published references are separately identified; independent admission remains pending. Points follow the separately versioned scoring policy.",
                "failure_policy": "hold-on-disagreement-v1"}
    return {"selection_kind": "parameter-set", "display_group": SCHEMES[scheme_id]["display_group"],
            "record_inheritance": False, **scoring_presentation(manifest or {}),
            "review_status": "local-beta", "origin": "known",
            "review_note": "Local beta experiment. Independent admission and scoring enrollment remain pending.",
            "failure_policy": "hold-on-disagreement-v1"}

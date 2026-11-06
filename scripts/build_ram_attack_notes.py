#!/usr/bin/env python3
"""Reproduce the local bit-RAM curves and their authored note companion.

No attacks, model API calls, database writes, or publication actions are run by
this command. It creates bounded analytic evidence and updates only the local
eight-note fixture source. prepare_note_integration.py performs a separate,
explicit revision/publication-fixture step with its ordinary access checks.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crucible import registry
from crucible.attack_models import classical, codes, lattice, common
from crucible.attack_models.catalogue import digest

PRESENTATIONS = ROOT / "crucible/data/scheme_note_presentations.json"
OUTPUT = ROOT / "crucible/data/ram_attack_curves.json"
LABELS = {
    "module-native-message-enumeration-v1": "Re-encrypting every native message",
    "frodo-native-message-enumeration-v1": "Re-encrypting every native message",
    "frodo-bounded-secret-column-enumeration-v1": "Searching the bounded secret columns",
    "ntru-native-input-enumeration-v1": "Searching the fixed-weight input space",
    "sntrup-native-input-enumeration-v1": "Searching the fixed-weight input space",
    "Prange syndrome decoding with bounded sampling": "An information-set decoding attack",
    "Prange recovery of the quasi-cyclic private key": "An information-set attack on the private key",
    "Exhaustive private-support recovery": "Searching the private support",
}

FORMULAS = {
    "interval-bsgs-v1": r"For an exponent interval \(1\le x\le M\), the table has \(m=\lceil\sqrt{M+1}\rceil\) entries. The work includes building and sorting it, every lookup and the final shared-key derivation.",
    "curve-bsgs-v1": r"For prime group order \(q\), use \(m=\lceil\sqrt q\rceil\) baby steps. Group operations are expanded into field arithmetic; neither a group operation nor a table lookup is treated as one bit operation.",
    "module-native-message-enumeration-v1": r"At polynomial degree \(n\), there are \(2^n\) native messages. Try each one, reproduce its full ciphertext and return the corresponding shared key at the first match. The plot includes hashing, sampling, polynomial arithmetic and comparison. Faster lattice attacks are outside this baseline.",
    "frodo-native-message-enumeration-v1": r"The native message space has \(2^{128}\) elements at every stage. The fixed public salt is not guessed. Where honest message-decoding failure is \(\delta\), the first-match argument gives success at least \(1-2\delta\).",
    "frodo-bounded-secret-column-enumeration-v1": r"Search \(8\cdot25^n\) bounded column candidates. A false accepted column and an honest decoding failure are the two ways the recovery argument can fail; their probabilities are bounded by the visible premises.",
    "Prange syndrome decoding with bounded sampling": r"For \(r=mt\) parity checks and a weight-\(t\) error, a random \(r\)-column set contains its support with probability \(\pi=\binom rt/\binom nt\). The budget uses \(R=\lceil32/\pi\rceil\) bounded trials, including binary elimination. The rank premise is essential.",
}

# Source checks for the three exact reductions whose readable summaries did
# not previously include their formulas. Hash-transfer slack remains explicit
# in the authored claim, without a new numerical hardness estimate.
CODE_SECURITY_SOURCE_FORMULAS = {
    "sntrup@3": "Pr[A recovers K] <= min(1, OW_core(T_B) + (2Q+1)/2^256).",
    "hqc@modeled-research-v3": "Pr[A recovers K] <= min(1,q_G*OW_PKE(T_B)+2^-256).",
    "classic-mceliece@length-research-v1": "Pr[A recovers K] <= min(1, OW_SD(T_B) + 2^-256).",
}


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:55]


def clean(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > (1 << 53)-1:
        return str(value)
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def result_for(scheme_id, version, profile):
    family = classical if scheme_id in {"rsa-kem", "teaching-dh-kem", "dhkem-x25519"} else codes if scheme_id in {"hqc", "classic-mceliece"} else lattice
    result = family.estimate(scheme_id, version, profile)
    if result["profile_id"] != profile["id"] or result["scheme_id"] != scheme_id or result["version"] != version:
        raise ValueError("Attack targets a different profile")
    if (type(result["work_bound"]) is not int or result["work_bound"] <= 0
            or type(result["memory_bits"]) is not int or result["memory_bits"] <= 0
            or sum(result["breakdown"].values()) != result["work_bound"]):
        raise ValueError("Incomplete integer work account")
    return result


def role_claim(release, role):
    """State the existing proposition without its derivation or premise labels.

    These authored statements retain the source experiments and symbolic
    resource qualifications. They do not assign small values to success bounds.
    Proof exposition stays in readable sections and the unchanged Full text.
    """
    prefix = "For every registered native configuration of exact research release `" + release + "`, "
    if role == "correctness":
        experiment = (prefix + r"run honest \((pk,sk)\leftarrow\mathsf{KeyGen}\), "
            r"\((C,K)\leftarrow\mathsf{Encaps}(pk)\) and \(K'=\mathsf{Decaps}(sk,C)\) "
            "using its specified samplers, mathematical algorithms, exact arithmetic and canonical encodings. "
            "Condition on valid successful key generation and completed operations, never on agreement.\n\n" +
            r"The claim is \(\Pr[K'\ne K]\le d\), averaged over the honest keys and encapsulation randomness, with ")
        bounds = {
            "teaching-dh-kem@7": r"\(d=0\) for every allowed exponent pair.",
            "rsa-kem@4": r"\(d=0\) for every \(z\in\mathbb Z_N\), including zero and nonunits.",
            "dhkem-x25519@curve-compressed-v2": r"\(d=0\) for every allowed nonzero scalar pair on each registered certified curve.",
            "sntrup@3": r"\(d=0\) for every allowed honest input in all 27 registered tuples, including the two configurations outside the default stage progression.",
            "classic-mceliece@length-research-v1": r"\(d=0\) for every allowed weight-64 honest error at each registered length.",
            "ml-kem@compressed-research-v4": r"\(d=d_n\), where \(n\) is the native polynomial degree and"
                "\n" + r"\[d_n=\begin{cases}0&n=8,16,\\2^{-367}&n=32,\\2^{-193}&n=64,\\2^{-236}&n=128,\\2^{-128}&n=256.\end{cases}\]",
            "frodokem@anchor-research-v3": r"\(d=d_n\), where \(n\) is the native matrix dimension and"
                "\n" + r"\[d_n=\begin{cases}0&n=8,\\2^{-133}&n\in\{16,24,\ldots,640\}.\end{cases}\]",
            "hqc@modeled-research-v3": r"\(d=d_j\), where"
                "\n" + r"\[d_j=\begin{cases}0&j=1,2,3,4,\\2^{-128}&j=5,6,\ldots,66.\end{cases}\]"
                "\nwhere j identifies the corresponding frozen native tuple in this release's progression.",
        }
        definition = experiment + bounds[release]
        if release.startswith(("ml-kem@", "frodokem@", "hqc@")):
            definition += ("\n\nThe same bound also applies to failure to recover the entire native message. "
                "Where the bound is zero, agreement holds for every allowed honest draw. "
                "Positive bounds concern the average honest experiment, not each key individually.")
        definition += ("\n\nThese are mathematical statements about the specified algorithms. "
            "They exclude invalid inputs, entropy or implementation faults, runtime aborts and side channels; "
            "they do not certify native executable correctness.")
        return "Honest decoding and key agreement" if release.startswith(("ml-kem@", "frodokem@", "hqc@")) else "Honest key agreement", definition

    key_bits = 128 if release.startswith("frodokem@") else 256
    experiment = (prefix + "generate one fresh honest public key and one encapsulation. "
        "Let R be the supremum probability, among classical adversaries using at most T total computation work and the stated query budgets, of returning the complete " +
        str(key_bits) + "-bit shared key in one output. Probability includes honest generation, encapsulation and the adversary's coins; "
        "condition on valid successful generation. Pre-challenge computation and designated hash queries count toward the same budgets.\n\n")
    exclusion = ("\n\nNo decapsulation, key-checking or leakage oracle is available. "
        "The bounds concern a single classical target; they do not assert CCA, quantum or multi-target security. "
        "The success/advantage bounds and hash-transfer slack are explicit parameters, not numerically certified small values.")
    # Resource-success functions refer to the exact distributions, not generic
    # named assumptions or an inferred equivalence to conventional LWE.
    if release.startswith(("teaching-dh-kem@", "dhkem-x25519@", "rsa-kem@")):
        experiment += (r"Let \(Q\) bound transcript-hash queries. Let \(I\) be the supremum absolute difference of output-1 probabilities over the same adversary class "
            r"when the adversary additionally receives the real key or an independent uniform 256-bit string. "
            r"Let \(\delta\) bound the increase of each of \(R,I\) when the transcript random oracle is replaced by the concrete SHA-256 role, "
            "at the same work/query budgets.\n\n")
        if release.startswith("rsa-kem@"):
            symbols = (r"Let \(\varepsilon\) bound the greatest success probability, at work \(T'\), of recovering a uniform "
                r"\(z\in\mathbb Z_N^*\) from \((N,65537,z^{65537}\bmod N)\), for the exact bounded generator's emitted-key distribution. "
                r"Let \(\rho=\mathbb E[1/p+1/q-1/(pq)]\) over those emitted keys \(N=pq\). "
                r"The budget \(T'\) covers the adversary, lazy-hash bookkeeping and at most \(Q\) canonical parsing/public exponentiation checks.")
            equation = r"\[R\le\min\{1,2^{-256}+\varepsilon+\rho+\delta\},\qquad I\le\min\{1,\varepsilon+\rho+\delta\}.\]"
        else:
            distribution = (r"from \((p,q,g,M,g^x,g^r)\), recovering \(g^{xr}\) for independent uniform "
                r"\(x,r\in\{1,\ldots,M\}\), with the configuration's certified group parameters"
                if release.startswith("teaching-dh-kem@") else
                r"from the registered curve, generator \(G\), order \(q\) and points \((xG,rG)\), recovering \(xrG\) "
                r"for independent uniform \(x,r\in\mathbb Z_q^*\)")
            symbols = (r"Let \(\varepsilon\) bound the greatest success probability at work \(T'\) of the following task: " +
                distribution + ". " + r"The budget \(T'\) covers the adversary, lazy-hash bookkeeping, canonical transcript/element parsing "
                r"and extracting one of at most \(Q\) candidate hash queries; it is not restricted to generic-group operations.")
            equation = r"\[R\le\min\{1,2^{-256}+Q\varepsilon+\delta\},\qquad I\le\min\{1,Q\varepsilon+\delta\}.\]"
        return "Passive key recovery and distinguishing", experiment + equation + "\n\n" + symbols + exclusion

    if release.startswith(("ml-kem@", "frodokem@")):
        if release.startswith("ml-kem@"):
            equation = r"\[R\le\min\{1,\epsilon_{\rm key}+\epsilon_{\rm ephemeral}+Q_G2^{-n}+2^{-256}+\epsilon_{\rm hash}\}.\]"
            distribution = (r"Here \(n\in\{8,16,32,64,128,256\}\) is the native message length. "
                r"With \(R_q=\mathbb Z_q[X]/(X^n+1)\) and the native rank, \(\epsilon_{\rm key}\) bounds the absolute distinguishing advantage "
                r"between \((\rho,A(\rho)s+e)\) and \((\rho,U)\); \(\epsilon_{\rm ephemeral}\) bounds it between "
                r"\((\rho,t,A(\rho)^Ty+e_1,t^Ty+e_2)\) and \((\rho,t,U,V)\). "
                r"In the second pair \(t\) is uniform; all comparison values \(U,V\) are independent uniform values of their required shapes. "
                "The public-seed marginal, matrix map and joint noise laws are exactly those of the registered native configuration, including shared-seed correlations. "
                r"The two budgets include the adversary, ciphertext formation and lazy-oracle bookkeeping. \(Q_G\) bounds transform-hash queries, "
                r"and \(\epsilon_{\rm hash}\) bounds the excess recovery probability for concrete \(G(M,h(pk))\) over an independent consistent random function, at the same budgets.")
        else:
            equation = r"\[R\le\min\{1,\epsilon_{\rm key}+\epsilon_{\rm ephemeral}+(Q_G+Q_F+1)2^{-128}+\epsilon_{\rm hash}\}.\]"
            distribution = (r"The native message is uniform 128 bits, and the independent uniform 256-bit salt is public. "
                r"For \(n\in\{8,16,\ldots,640\}\), \(q=32768\), and native matrices with eight message columns, "
                r"\(\epsilon_{\rm key}\) bounds the absolute distinguishing advantage between \((\rho,A(\rho)S+E)\) and \((\rho,U)\); "
                r"\(\epsilon_{\rm ephemeral}\) bounds it between \((\rho,B,S'A(\rho)+E',S'B+E'')\) and \((\rho,B,U',V')\). "
                r"In the second pair \(B\) is uniform; comparison values \(U,U',V'\) are independent uniform matrices of the required shapes. "
                "All public-seed marginals, expansions and joint finite-CDF sampling laws are the exact native ones, including shared-seed correlations. "
                r"The budgets include the adversary, ciphertext formation and lazy-oracle bookkeeping. \(Q_G,Q_F\) bound queries to the transform and final-key hash roles. "
                r"\(\epsilon_{\rm hash}\) bounds excess recovery for concrete \(G,F\) over independent consistent random functions at the same budgets.")
        return "Passive shared-key recovery bound", experiment + equation + "\n\n" + distribution + exclusion

    if release == "sntrup@3":
        equation = r"\[R\le\min\{1,\epsilon_{\rm core}(T_B)+(2Q+1)2^{-256}+\delta_{\rm hash}(T,Q)\}.\]"
        symbols = (r"\(\epsilon_{\rm core}(T_B)\) bounds the greatest success, at total work \(T_B\), of recovering the full fixed-weight polynomial "
            r"\(r\) from \((h,\operatorname{Round}(hr))\) under this release's exact key/input samplers, conditioned on successful generation. "
            r"\(Q\) bounds total \(H_1,H_2,H_3\) queries. \(T_B\) includes the adversary, lazy-table bookkeeping and at most \(Q\) "
            r"canonical decoding and public rounded-product checks. \(\delta_{\rm hash}(T,Q)\) bounds excess recovery when the concrete domain-separated "
            r"256-bit SHA-512 roles \(H_1,H_2,H_3\) replace independent random functions; the public \(H_4\) role remains fixed.")
    elif release == "hqc@modeled-research-v3":
        equation = r"\[R\le\min\{1,q_G\epsilon_{\rm PKE}(T_B)+2^{-256}+\delta_{\rm hash}(T,q_G)\}.\]"
        symbols = (r"\(\epsilon_{\rm PKE}(T_B)\) bounds the greatest success, at work \(T_B\), of recovering uniform 128-bit \(m\) from "
            r"\((pk,\mathsf{Encrypt}(pk,m;\theta))\) with independent uniform 256-bit \(\theta\) and the exact native key distribution, seed maps, biased samplers and codec. "
            r"\(q_G\) bounds distinct queries to the domain-separated \(G\) role. \(T_B\) includes the adversary, public digest/salt handling and lazy-table simulation. "
            r"\(\delta_{\rm hash}(T,q_G)\) bounds excess recovery when concrete SHA3-512 role \(G\) replaces an independent random function to 512 bits; "
            "other hash/expansion roles remain fixed. The actual encapsulation uses its specified public salt and coupled key/coins derivation.")
    elif release == "classic-mceliece@length-research-v1":
        equation = r"\[R\le\min\{1,\epsilon_{\rm SD}(T_B)+2^{-256}+\delta_{\rm hash}(T,Q)\}.\]"
        symbols = (r"\(\epsilon_{\rm SD}(T_B)\) bounds the greatest success, at work \(T_B\), of recovering the uniform weight-64 honest error \(e\) "
            r"from \((H,He)\) for the exact seeded binary-Goppa/support distribution at the registered length, conditioned on successful systematic key generation. "
            r"This success function includes structural attacks. \(Q\) bounds designated session-hash queries, and \(T_B\) includes the adversary, oracle simulation, "
            r"canonical parsing, weight checks and at most \(Q\) public matrix-vector products. \(\delta_{\rm hash}(T,Q)\) bounds excess recovery "
            "when the concrete domain-1 SHAKE session hash replaces an independent random function to 256 bits; all other expansion roles remain fixed.")
    else:
        raise ValueError("Author an exact security conclusion for this release: " + release)
    return "Passive shared-key recovery bound", experiment + equation + "\n\n" + symbols + exclusion


def canonical_note_blocks(release, row, analysis):
    """A representational conversion, with no new mathematical inference.

    A dependency without extra use text invokes the complete proposition as
    written. Existing scope prose is retained where available; no minimal bound
    or symbol substitution is invented to populate a mandatory form.
    """
    from crucible.statement_workflow import Argument, Statement

    sections = {item["id"]: item for item in row["sections"]}
    premises = {item["id"]: item for item in row["assumptions"]}
    profiles = set(row["profile_ids"])
    statements = []
    for premise in row["assumptions"]:
        section = sections[premise["section_id"]]
        definition = premise.get("statement") or section["body"]
        title = re.sub(r"^A-[1-9][0-9]*\s*[·:]\s*", "", section["title"])
        statement = {"id": premise["id"], "label": premise["label"], "title": title,
                     "definition": definition}
        Statement.model_validate(statement)
        statements.append(statement)

    def conclusion_statement(identifier, label, title, definition):
        statement = {"id": "claim-" + identifier, "label": label,
                     "title": title, "definition": definition}
        Statement.model_validate(statement)
        statements.append(statement)
        return {"statement_id": statement["id"]}

    def dependency(identifier, configuration_ids=None):
        premise = premises[identifier]
        coverage = list(premise["profile_ids"])
        if configuration_ids is not None:
            coverage = [identifier for identifier in coverage if identifier in configuration_ids]
        if not coverage or not set(coverage) <= profiles:
            raise ValueError("A canonical scheme dependency needs exact nonempty configuration coverage")
        return {"statement": {"statement_id": identifier}, "configuration_ids": coverage,
                **({"use": premise["scope"]} if premise.get("scope") else {})}

    arguments = []
    role_arguments = {}
    for role in ("security", "correctness"):
        claim = row["scheme_arguments"][role]
        identifier = "scheme-" + role
        role_arguments[role] = identifier
        title, definition = role_claim(release, role)
        if role == "security" and release in CODE_SECURITY_SOURCE_FORMULAS:
            original = CODE_SECURITY_SOURCE_FORMULAS[release]
            if original not in row["full_text_argument_appendix"]:
                raise ValueError("The authored Full-text reduction changed; review its readable conclusion: " + release)
        arguments.append({"id": identifier, "title": sections[role]["title"],
            "conclusion_statement": conclusion_statement(identifier, role.title() + " claim",
                title, definition),
            "scope": claim["scope"],
            "status": claim["status"], "section_id": role,
            "dependencies": [dependency(identifier) for identifier in row["scheme_assumptions"][role]]})

    for identifier, graph in row["graph_models"].items():
        graph["argument_id"] = identifier
        section = sections[graph["section_id"]]
        if graph["metric"] == "ram":
            points = [point for point in analysis["estimates"] if point["model_id"] == identifier]
            if not points:
                raise ValueError("A RAM argument needs its exact generated profile accounts")
            dependencies = []
            for premise_id in graph["assumption_ids"]:
                used = {point["profile_id"] for point in points if any(
                    "ram-" + item["id"] == premise_id for item in point["assumptions"])}
                dependencies.append(dependency(premise_id, used))
            statuses = {point["success_status"] for point in points}
            if dependencies:
                status = "conditional"
            elif statuses <= {"proved-for-honest-inputs", "analytical-bound"}:
                status = "unconditional"
            else:
                status = "incomplete"
            scope = ("Exact research release " + release + ", under " + common.MODEL_ID +
                ". The generated account specifies the registered configurations, work budget and complete-key success event. "
                "This is an upper bound for this attack, not a security lower bound or a best-known-attack claim.")
            conclusion = ("For exact research release `" + release + "`, use computation model `" + common.MODEL_ID +
                "` and algorithm `" + identifier + "`. For each configuration j listed below, let W_j and M_j be the exact "
                "`work_bound` and `memory_bits` integers of its row in `crucible/data/ram_attack_curves.json`, release analysis SHA-256 `" +
                analysis["analysis_sha256"] + "`. That account also pins the native parameter manifest and source implementations.\n\n" +
                r"The stated attack, with all setup charged to one fresh honest challenge, uses at most \(W_j\) bit-RAM work and \(M_j\) bits of storage, and has \(\Pr[\text{recover the complete shared key}]\ge1/2\). " +
                "Probability is over that configuration's honest challenge and the attack's randomness. "
                "The argument records any premises separately with their exact configuration coverage.\n\n" +
                "**Exact configurations.** " + ", ".join("`" + point["profile_id"] + "`" for point in points) +
                ".\n\nThis is an upper bound for this named attack in the stated model. It is neither a lower bound against all attacks "
                "nor a claim that this is the best known attack. Full text contains the model, method and reproduction instructions.")
            claim_label = "Attack claim"
        else:
            dependencies = [dependency(premise_id) for premise_id in graph["assumption_ids"]]
            # A historical or qualitative model is not a new quantified theorem.
            status = "incomplete"
            scope = ("Exact research release " + release + "; " + graph["metric"] +
                " model " + identifier + " (" + graph["kind"] + "). "
                "Retain the source's qualifications; this is not converted into a current comparable bit-RAM estimate.")
            conclusion = ("**Unquantified reference.** " + section["body"] + "\n\n**Scope.** " + scope +
                " This entry does not supply a complete quantified attack guarantee in the common computation model.")
            claim_label = "Reference claim"
        arguments.append({"id": identifier, "title": section["title"],
            "conclusion_statement": conclusion_statement(identifier, claim_label,
                section["title"] + " — stated conclusion", conclusion),
            "scope": scope, "status": status, "section_id": graph["section_id"], "dependencies": dependencies})

    for argument in arguments:
        Argument.model_validate(argument)

    # Avoid separately editable copies of the precise definition in the readable
    # companion. Move only literal formal prefixes / complete RAM premise blocks;
    # all remaining explanatory prose and Full text are retained unchanged.
    readable = deepcopy(row["sections"])
    reasoning_sections = {argument["section_id"] for argument in arguments}
    removed = set()
    for premise in row["assumptions"]:
        item = next(section for section in readable if section["id"] == premise["section_id"])
        definition = premise.get("statement")
        if definition and item["body"].startswith(definition):
            item["body"] = item["body"][len(definition):].lstrip()
        elif not definition and item["id"] not in reasoning_sections:
            removed.add(item["id"])
        if not item["body"].strip():
            if item["id"] in reasoning_sections:
                raise ValueError("A reasoning section must not disappear during canonical conversion")
            removed.add(item["id"])
    readable = [item for item in readable if item["id"] not in removed]
    if not reasoning_sections <= {section["id"] for section in readable}:
        raise ValueError("Canonical arguments lost their exact reasoning sections")
    return {"statements": statements, "arguments": arguments, "readable_sections": readable,
            "argument_bindings": role_arguments,
            "canonical_conversion": {"format": "crucible-note-block-conversion-v1",
                "scope": "Representation of the existing authored propositions and arguments, not new proof, independent review or scheme admission. Each conclusion has one canonical statement; an argument references it rather than repeating authoritative conclusion prose.",
                "dependency_convention": "Use each whole proposition as written, with any retained source scope and exact native configuration IDs. No weaker sufficient threshold or substitution is inferred.",
                "legacy_input": "Existing assumptions, scheme_arguments and graph_models in the authoring source remain generator inputs; generated notes use only these canonical blocks."}}


def build(*, check=False):
    source = json.loads(PRESENTATIONS.read_text())
    models_path = ROOT / "crucible/attack_models"
    source_files = [models_path / name for name in ("common.py", "classical.py", "lattice.py", "codes.py")]
    source_files.append(Path(__file__).resolve())
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    artifact = {"format": "crucible-ram-attack-catalogue-v1", "status": "local-research-draft",
                "computation_model": common.MODEL, "source_sha256": hashes, "releases": {}}
    next_label = max(int(a["label"].split("-")[1]) for row in source["releases"].values()
                     for a in row["assumptions"] if not a["id"].startswith("ram-")) + 1
    total = 0
    for release, row in source["releases"].items():
        scheme_id, version = release.split("@", 1)
        descriptor = registry.descriptor(registry.adapter_for(scheme_id, version))
        profiles = descriptor["profiles"]
        records = [result_for(scheme_id, version, profile) for profile in profiles]
        grouped = defaultdict(list)
        for profile, record in zip(profiles, records):
            grouped[record["algorithm"]].append((profile, record))
        # This command owns only its prefixed additions, not earlier readable
        # sections, security/correctness premises, citations, or source snapshots.
        row["sections"] = [s for s in row["sections"] if not s["id"].startswith("ram-")]
        row["assumptions"] = [a for a in row["assumptions"] if not a["id"].startswith("ram-")]
        row["source_refs"] = [r for r in row["source_refs"] if not r["id"].startswith("ram-")]
        row["graph_models"] = {key: value for key, value in row["graph_models"].items() if value["metric"] != "ram"}
        new_sections = [{"id": "ram-model", "title": "One common measure of attack work",
            "body": "These curves use **Classical bit-RAM v1**: elementary Boolean operations and one-bit memory transfers share a fixed accounting rule. All attack setup is charged to one fresh challenge. Memory capacity is unlimited in this abstraction, and the storage bound remains visible beside the graph.\n\nThe plotted quantity is \\(\\log_2 W\\), where \\(W\\) bounds a specified strategy's work and its stated assumptions imply at least **50% probability of recovering the complete shared key**. These are reproducible **baseline upper bounds**, not the cheapest known attacks or a security lower bound. A bigger value does not establish a more secure scheme. Full text contains the cost rules, proofs, parameter table and reproduction commands."}]
        premises = {}
        models = []
        appendix = ["## Reproducible bit-RAM attack analysis", common.TECHNICAL_MODEL,
            "## Reproduction\n\nFrom this source checkout:\n```text\npython scripts/build_ram_attack_notes.py --check\npython -m pytest tests/test_ram_common.py tests/test_ram_classical_attacks.py tests/test_ram_lattice_attacks.py tests/test_ram_code_attacks.py tests/test_ram_catalogue.py\n```\nThe generated `crucible/data/ram_attack_curves.json` retains integer cost components, parameters, assumptions and source-file hashes. No cryptographic-size attack is executed. Small examples check implementation paths; they do not verify heuristic premises at large sizes. No external model service or paid API is used."]
        for algorithm, entries in grouped.items():
            representative = entries[-1][1]
            mid = "ram-" + slug(algorithm)
            title = representative.get("label") or LABELS.get(algorithm) or algorithm.replace("-", " ")
            assumption_ids = []
            for profile, record in entries:
                for premise in record["assumptions"]:
                    identity = "ram-" + premise["id"]
                    if identity not in premises:
                        premises[identity] = {"premise": premise, "profiles": [], "stages": set(), "variants": set()}
                    item = premises[identity]
                    if profile["id"] not in item["profiles"]:
                        item["profiles"].append(profile["id"])
                    if type(profile.get("stage_number")) is int:
                        item["stages"].add(profile["stage_number"])
                    item["variants"].add(premise["technical"])
                    if identity not in assumption_ids:
                        assumption_ids.append(identity)
            human = FORMULAS[algorithm] if algorithm == "module-native-message-enumeration-v1" else representative["human"]
            if algorithm in FORMULAS and algorithm != "module-native-message-enumeration-v1":
                human += "\n\n" + FORMULAS[algorithm]
            new_sections.append({"id": mid, "title": title, "body": human})
            row["graph_models"][mid] = {"section_id": mid, "metric": "ram", "kind": "explicit-attack-work-upper-bound", "assumption_ids": assumption_ids}
            models.append({"id": mid, "section_id": mid, "label": title, "unit": "log2 bit operations",
                "computation_model_id": common.MODEL_ID, "scope": "Complete shared-key recovery on this exact release; at least 50% success under the note's scoped premises.",
                "assumptions": [], "limitations": ["Research baseline with deliberately elementary arithmetic. Not best-known attack cost or a security lower bound."],
                "sources": representative["sources"]})
            appendix.extend(["## " + title, "Worked configuration: " + entries[-1][0]["id"] +
                ". The same algorithm and parameterized formulas generate the other rows below.", representative["technical"],
                "Success statement for this worked configuration: " + str(representative["success_lower_bound"])])
        for identity, item in premises.items():
            premise = item["premise"]
            label = "A-" + str(next_label)
            next_label += 1
            section_id = "ram-premise-" + hashlib.sha256(identity.encode()).hexdigest()[:10]
            row["assumptions"].append({"id": identity, "label": label, "section_id": section_id,
                "status": "unproved", "stages": sorted(item["stages"]), "profile_ids": item["profiles"]})
            # Include every distinct quantitative statement in readable form.
            # Repeated structural prose is reserved for Full text.
            # The shared weak event bound is the premise. Different ideal-noise
            # calculations motivate it at different stages; do not turn those
            # stronger, differently scoped arguments into hidden prerequisites.
            human = premise["human"]
            if identity == "ram-module-v4-message-correctness":
                human = r"For the actual seeded scheme at the linked stages, require \(\Pr[D(F(m))\ne m]\le1/8\). This suffices for at least \(3/4\) success of the enumeration attack. Idealized correctness calculations motivate this weak threshold: degrees32/64 use a worst-case compression bound, while degrees128/256 also model rounding. The required statement is about actual message-decoding failures; transferring those calculations to the implementation remains unproved."
            scoped = sorted(item["stages"])
            if scoped:
                human += "\n\nApplies to " + (f"Stage {scoped[0]}." if len(scoped)==1 else f"Stages {scoped[0]}–{scoped[-1]}." if scoped == list(range(scoped[0],scoped[-1]+1)) else "Stages " + ", ".join(map(str,scoped)) + ".")
            new_sections.append({"id": section_id, "title": label + " · " + premise["title"],
                "body": human})
            appendix.extend(["## " + label + " · " + premise["title"], *sorted(item["variants"])])
        points = []
        for profile, record in zip(profiles, records):
            point = clean(record)
            point.update(model_id="ram-" + slug(record["algorithm"]), computation_model_id=common.MODEL_ID,
                profile_id=profile["id"], manifest_sha256=digest(registry.adapter_for(scheme_id, version).get_manifest(profile["id"])),
                work_bound=str(record["work_bound"]), memory_bits=str(record["memory_bits"]),
                bits=common.log2_int(record["work_bound"]), memory_log2_bits=common.log2_int(record["memory_bits"]),
                success_lower_bound="1/2", analysis_success_statement=str(record["success_lower_bound"]),
                success_status=record.get("success_status", record.get("status", "conditional-analytical-bound")))
            points.append(point)
        analysis = {"scheme_id": scheme_id, "version": version, "models": models, "estimates": points,
                    "computation_model_sha256": digest(common.MODEL), "source_sha256": hashes}
        analysis["analysis_sha256"] = digest(analysis)
        artifact["releases"][release] = analysis
        row["ram_analysis_sha256"] = analysis["analysis_sha256"]
        appendix.append("## Configuration results\n\nThese are upper bounds for the named baseline, not lower bounds on security. Exact integer accounts are in the generated artifact.\n\n| Configuration | log₂ work | log₂ stored bits |\n|---|---:|---:|\n" +
            "\n".join(f"| {p['profile_id']} | {p['bits']:.3f} | {p['memory_log2_bits']:.3f} |" for p in points))
        row["full_text_appendix"] = "\n\n".join(appendix)
        row["sections"].extend(new_sections)
        for path, sha in hashes.items():
            row["source_refs"].append({"id": "ram-source-" + Path(path).stem, "title": "Reproducible bit-RAM account: " + path, "path": path, "sha256": sha})
        for i, ref in enumerate(common.MODEL["sources"]):
            row["source_refs"].append({"id": "ram-model-source-" + str(i), **ref})
        for i, ref in enumerate({r["url"]: r for record in records for r in record["sources"]}.values()):
            row["source_refs"].append({"id": "ram-attack-source-" + str(i), **ref})
        row.update(canonical_note_blocks(release, row, analysis))
        total += len(points)
    source["scope"] = "Eight exact current research releases with readable scheme evidence and explicit bit-RAM recovery baselines. Local research draft; no scientific admission or changes to challenge parameters."
    rendered = {OUTPUT: json.dumps(artifact, indent=2, ensure_ascii=False)+"\n",
                PRESENTATIONS: json.dumps(source, indent=2, ensure_ascii=False)+"\n"}
    if check:
        mismatches = [str(path.relative_to(ROOT)) for path, text in rendered.items() if not path.exists() or path.read_text() != text]
        if mismatches:
            raise SystemExit("Generated attack evidence differs: " + ", ".join(mismatches))
    else:
        for path, text in rendered.items():
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(text)
            temporary.replace(path)
    print(f"{'Verified' if check else 'Built'} {total} exact-profile attack accounts across {len(artifact['releases'])} releases; no database or hosted changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    build(**vars(parser.parse_args()))

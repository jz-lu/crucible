#!/usr/bin/env python3
"""Seed an isolated, explicitly illustrative statement lifecycle rehearsal.

Uses the normal note/publication/update APIs. Never touches live hosting,
executable scheme definitions, scores, existing research or challenge records.
The durable journal and deterministic API keys make interrupted runs resumable.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = "crucible-statement-sampler-audit-v1"
AUTHOR = str(uuid5(NAMESPACE_URL, "crucible-readable-note-local-author-v1"))
REVIEWER = str(uuid5(NAMESPACE_URL, "crucible-readable-note-local-reviewer-v1"))
TARGET_ATTACK = "trial-sampler-attack"
TARGET_CORRECTNESS = "trial-sampler-correctness"
WARNING = "Illustrative finite experiment for testing Crucible's research workflow. This is not a KEM or a cryptographic security result."


def audit():
    """Exact arithmetic, deliberately small enough to inspect independently."""
    outcomes = range(256)
    counts = {"alpha": sum(x < 1 for x in outcomes), "beta": sum(x < 8 for x in outcomes)}
    probabilities = {key: Fraction(value, 256) for key, value in counts.items()}
    assert probabilities["alpha"] <= Fraction(1, 128) < probabilities["beta"]
    assert probabilities["beta"] <= Fraction(1, 16) <= Fraction(1, 8)
    assert 1 - probabilities["beta"] >= Fraction(7, 8)
    return {"counts": counts, "probabilities": {key: str(value) for key, value in probabilities.items()},
            "beta_success": str(1 - probabilities["beta"]), "outcomes": 256}


def source(note, **extra):
    return {"note_id": note.get("id", note.get("note_id")), "revision": note["revision"],
            "content_sha256": note["content_sha256"], **extra}


def link(note, **location):
    return "/#notebook?" + urlencode({"note": note.get("id", note.get("note_id")),
        "revision": note["revision"], "sha256": note["content_sha256"], **location})


def note(title, claim, body, *, sections=None, statements=None, arguments=None, references=None):
    result = {"title": title, "claim": claim, "conditions": WARNING, "body": body,
        "tags": ["illustrative-trial", "sampler-audit"], "kind": "research",
        "extensions": {"statement_trial": FIXTURE, "illustrative": True}}
    for key, value in (("sections", sections), ("statements", statements),
                       ("arguments", arguments), ("references", references)):
        if value is not None:
            result[key] = value
    return result


EXPERIMENT = r"""Let \(X\) be uniform on \(\{0,\ldots,255\}\).
Configuration \(\alpha\) fails exactly when \(X<1\); configuration \(\beta\)
fails exactly when \(X<8\). Write \(\delta_c=\Pr[F_c(X)=1]\).
Every draw and failure predicate in this finite experiment is specified here.
The analogy is a decoder-failure audit, but there is no encryption or secrecy claim."""


def premise_document(*, restricted=False):
    domain = r"c=\alpha" if restricted else r"c\in\{\alpha,\beta\}"
    statement = {"id": "sparse-failure", "label": "Trial A-1", "title": "Sparse failure",
        "definition": EXPERIMENT + "\n\n" + r"The proposed bound is" + "\n" +
        r"\[\forall " + domain + r",\qquad \delta_c\leq\frac1{128}.\]"}
    body = "# Sparse failure\n\nThe canonical Trial A-1 block contains the experiment and its exact quantified claim.\n\n"
    body += ("This revision restricts the quantified domain to alpha. It is a different mathematical "
             "definition, not a cosmetic correction. The original all-configuration statement remains "
             "available at revision 1; its existing uses must not be silently retargeted.\n\n"
             "The exact count 1/256 establishes the revised alpha-only statement within this finite model."
             if restricted else
             "This intentionally overstrong hypothesis is the starting point of the rehearsal. "
             "It is false at beta, as the later exact enumeration shows. Presenting it as a premise "
             "tests whether the application can preserve an old argument and record a scoped correction. "
             "Its initial conditional use is not evidence that it is true.")
    return note("Sparse failure: a statement that changes scope" if restricted else "Sparse failure: the original hypothesis",
        "An exact statement shared by an attack argument and a stronger correctness claim.", body,
        statements=[statement], sections=[{"id": "why", "title": "What changes", "body": body}])


def rough_document(premise):
    return note("A useful bound that does not settle the stronger claim",
        "A loose bound is sufficient for the attack; it does not refute the stronger claim.",
        r"""# A bound with a specific use

This note deliberately has no readable companion. Its full text is the complete note.

For either configuration, failure requires \(X<16\). Therefore
\[\delta_c\leq\Pr[X<16]=\frac{16}{256}=\frac1{16}.\]
This proves the attack's required bound \(\delta_c\leq1/8\), so success is at
least \(15/16\), above its target \(7/8\). It does not establish
\(\delta_c\leq1/128\). It also does not refute that stronger claim: an upper
bound can be loose.

```python
from fractions import Fraction
for threshold in (1, 8):
    assert all(not (x < threshold) or x < 16 for x in range(256))
assert Fraction(16, 256) == Fraction(1, 16) <= Fraction(1, 8)
```

The statement under discussion is pinned to the original revision in the reference below.
This is finite arithmetic under the stated uniform sampler, not a probabilistic test or
an empirical estimate. No model provider is needed to verify it.
""", references=[{"url": link(premise, statement="sparse-failure"), "relation": "responds_to",
                    "scope": "A weaker upper bound neither proves nor refutes Trial A-1."}])


def exact_document(premise):
    proof = r"""The failure sets have respectively one and eight elements out of 256:
\[\delta_\alpha=\frac1{256},\qquad\delta_\beta=\frac8{256}=\frac1{32}.\]
Thus alpha satisfies \(1/128\); beta does not. Trial A-1's original universal
statement is false because beta belongs to its quantified domain. This is not
a statement about any deployed KEM or a probability inferred from one failed run.

The attack experiment succeeds whenever failure does not occur, giving
\[\Pr[\text{success at }\beta]=1-\delta_\beta=\frac{31}{32}\geq\frac78.\]
The stronger correctness claim fails at beta while this attack-success conclusion survives."""
    return note("Exact enumeration: a contradiction with limited consequences",
        "Beta contradicts the stronger bound; the attack's success guarantee survives.",
        "# Exact enumeration\n\n" + EXPERIMENT + "\n\n" + proof + r"""

```python
from fractions import Fraction
counts = {name: sum(x < threshold for x in range(256))
          for name, threshold in [('alpha', 1), ('beta', 8)]}
assert counts == {'alpha': 1, 'beta': 8}
assert Fraction(counts['alpha'], 256) <= Fraction(1, 128)
assert Fraction(counts['beta'], 256) > Fraction(1, 128)
assert 1 - Fraction(counts['beta'], 256) >= Fraction(7, 8)
```

Every probability is an exact rational number. Reproducible code is evidence;
the application never executes code from this note. A reviewer checks the
argument before changing which justification is selected.
""", sections=[{"id": "count", "title": "What the count establishes", "body": proof}],
        references=[{"url": link(premise, statement="sparse-failure"), "relation": "challenges",
                     "scope": "The original universal bound fails at beta; alpha is unaffected."}])


def conditional_document():
    definition = r"""Let \(\Omega=\{0,\ldots,255\}\), let \(P\) be uniform on
\(\Omega\), and let \(Q\) be a specified but otherwise unrestricted distribution
on the same set. The premise on \(Q\) is
\[d_{\mathrm{TV}}(P,Q)=\frac12\sum_{x\in\Omega}|P(x)-Q(x)|\leq\frac7{256}.\]
This note does not establish this bound for an actual new sampler."""
    argument = r"""For the fixed failure event \(E=\{0\}\), total variation gives
\[Q(E)\leq P(E)+d_{\mathrm{TV}}(P,Q)\leq\frac1{256}+\frac7{256}=\frac1{32}.\]
This is a conditional proof. The distance premise remains necessary. It is not
an assertion that every distribution \(Q\) meets that premise.

A claim that A follows from B and B follows from A does not prove either one.
Such a circular proposal must retain its unresolved premise rather than acquire
an unconditional badge. The trial records this dependency explicitly; it does
not attempt automated theorem composition."""
    return note("A proof can leave a different assumption open",
        "A total-variation premise implies a failure bound, but remains a premise.",
        "# Conditional transfer\n\nThe canonical Trial A-2 block specifies the sample space, distributions and distance premise.\n\n" + argument,
        statements=[{"id": "sampler-distance", "label": "Trial A-2", "title": "Sampler distance", "definition": definition}],
        sections=[{"id": "transfer", "title": "The conditional argument", "body": argument}],
        arguments=[{"id": "distance-transfer", "title": "Transfer from the uniform experiment",
            "conclusion": r"For \(E=\{0\}\), the selected \(Q\) has \(Q(E)\leq1/32\).",
            "scope": "Only distributions Q satisfying Trial A-2; this is a separate sampler family, not alpha or beta.",
            "status": "conditional", "section_id": "transfer", "dependencies": [{
                "statement": {"statement_id": "sampler-distance"}, "configuration_ids": ["trial-gamma"],
                "required": r"\(d_{\mathrm{TV}}(P,Q)\leq7/256\)",
                "mapping": "P is uniform on the 256-element sample space; the failure event is {0}.",
                "justification": "The defining event inequality for total variation bounds Q(E) by P(E)+distance."}]}])


def argument_document(premise, rough, exact):
    ref = source(premise, statement_id="sparse-failure")
    common = {"statement": ref, "configuration_ids": ["trial-alpha", "trial-beta"],
              "mapping": "Use the same failure event and uniform sampler defined by Trial A-1."}
    arguments = [
        {"id": "attack-hypothesis", "title": "Attack via sparse failure",
         "conclusion": r"The finite attack experiment succeeds with probability at least \(7/8\).",
         "scope": "Alpha and beta in the specified finite experiment.", "status": "conditional", "section_id": "attack",
         "dependencies": [{**common, "required": r"Only \(\delta_c\leq1/8\) is needed.",
             "justification": "Trial A-1 states the stronger bound 1/128, which implies 1/8; success is 1−failure."}]},
        {"id": "attack-loose-bound", "title": "Attack via a direct finite bound",
         "conclusion": r"The finite attack experiment succeeds with probability at least \(7/8\).",
         "scope": "Alpha and beta; fixed uniform 256-outcome experiment.", "status": "unconditional", "section_id": "alternative",
         "dependencies": []},
        {"id": "correctness-hypothesis", "title": "The stronger correctness target",
         "conclusion": r"Failure is at most \(1/128\) for both configurations.",
         "scope": "Alpha and beta in the specified finite experiment.", "status": "conditional", "section_id": "correctness",
         "dependencies": [{**common, "required": r"The full bound \(\delta_c\leq1/128\) is needed.",
             "justification": "This use needs the bound as stated; the attack's weaker threshold is insufficient."}]},
        {"id": "correctness-unavailable", "title": "The original target cannot be maintained",
         "conclusion": r"The claimed bound \(\delta_c\leq1/128\) over both configurations is unavailable.",
         "scope": "The contradiction is beta; alpha's bound survives. No executable challenge changes.",
         "status": "incomplete", "section_id": "contradiction", "dependencies": []},
    ]
    sections = [
        {"id": "attack", "title": "One claim, two possible arguments", "body":
            r"The attack-success target is \(7/8\). The first argument uses Trial A-1: a bound of \(1/128\) would be more than sufficient. This is a conditional implication, not proof that Trial A-1 is true."},
        {"id": "alternative", "title": "An independent route survives", "body":
            "[The direct bound](" + link(rough) + r") proves \(\delta_c\leq1/16\) without Trial A-1. Therefore success is at least \(15/16\geq7/8\). "
            "[Exact enumeration](" + link(exact, note_section="count") + r") gives \(31/32\) at beta. These are alternative arguments for the same target; their premises must not be merged into one mandatory list."},
        {"id": "correctness", "title": "The same premise, a stronger use", "body":
            r"The correctness target needs \(\delta_c\leq1/128\). A bound of \(1/16\) does not meet it. Evidence useful for the attack must not automatically discharge this use."},
        {"id": "contradiction", "title": "A scoped correction", "body":
            r"Exact enumeration gives \(\delta_\beta=1/32>1/128\), refuting Trial A-1 as originally quantified. Alpha has \(\delta_\alpha=1/256\), so a new alpha-only statement is viable. Revising the statement's domain does not retroactively change either old use."},
    ]
    return note("Two uses, and an alternative argument",
        "A shared premise can fail while an independently justified conclusion survives.",
        "# Arguments and their scopes\n\n" + EXPERIMENT + "\n\n" + "\n\n".join(
            "## " + item["title"] + "\n\n" + item["body"] for item in sections),
        sections=sections, arguments=arguments,
        references=[{"url": link(premise, statement="sparse-failure"), "relation": "cites", "scope": "The original exact statement."},
                    {"url": link(rough), "relation": "cites", "scope": "Alternative proof sufficient for the attack."},
                    {"url": link(exact, note_section="count"), "relation": "cites", "scope": "Scoped contradiction and exact count."}])


def guide_document(pins, updates):
    cases = [
        ("start", "Start with the two uses", "[Open the original statement](" + link(pins["premise-v1"], statement="sparse-failure") +
         "). Trial A-1 appears in an attack argument and a stricter correctness argument. "
         "[See both arguments](" + link(pins["arguments"], argument="attack-hypothesis") + "). These are two uses, not two copies of the definition."),
        ("weaker", "1 · A useful weaker proof", "[Read the full-text-only bound](" + link(pins["rough"]) +
         r"). It establishes \(1/16\): enough for the attack's \(1/8\) threshold, but neither a proof nor a refutation of \(1/128\). "
         "[Inspect the pending update](/#notebook?statement_update=" + updates["attack-replacement"] + ")."),
        ("contradiction", "2 · A contradiction with a precise scope", "[Inspect exact enumeration](" + link(pins["exact"], note_section="count") +
         r"). Beta has failure \(1/32\), so the universal \(1/128\) claim is false; alpha still meets it. "
         "[Inspect the correctness update](/#notebook?statement_update=" + updates["correctness-correction"] + "). Neither publication nor this proposed correction changes a real scheme."),
        ("conditional", "3 · A proof that leaves another premise open", "[Open the transfer argument](" + link(pins["conditional"], argument="distance-transfer") +
         "). It proves a bound conditional on Trial A-2. A circular claim A-from-B-from-A would leave the premise unresolved; it cannot create an unconditional result."),
        ("alternative", "4 · One argument fails, another survives", "[Compare the alternative argument](" + link(pins["arguments"], argument="attack-loose-bound") +
         "). Its direct bound does not use Trial A-1. [View the selected attack justification](/#notebook?statement_target=" + TARGET_ATTACK +
         "). The pending proposal can replace the justification without changing the success target."),
        ("revision", "5 · Change the definition, preserve the old use", "[Original Trial A-1](" + link(pins["premise-v1"], statement="sparse-failure") +
         ") quantifies over alpha and beta. [Revised Trial A-1](" + link(pins["premise-v2"], statement="sparse-failure") +
         ") covers alpha only. Existing arguments still cite the original exact revision. A new definition does not silently repair old uses."),
        ("history", "6 · Apply research without changing a challenge", "[The current correctness justification](/#notebook?statement_target=" + TARGET_CORRECTNESS +
         ") is a local display target. Approval records a new adoption event; it does not issue instances, change deadlines, award points or alter previous submissions. "
         "The fixture builder checks existing research, accounts, challenges and submissions before and after preparation."),
        ("review", "Try the review flow", "[Switch to the local reviewer](/preview?persona=reviewer&trial=1), then open "
         "[the attack update](/#reviews?section=proposals&statement_update=" + updates["attack-replacement"] +
         "). The review compares the old and proposed exact arguments. Approve and apply it, return to the target, and inspect its history. "
         "[Return to the author](/preview?persona=author&trial=1). These personas exist only in this loopback preview."),
    ]
    connections = {
        "start": "The live-scheme analogue is one precisely defined premise supporting several arguments. The real uses must additionally identify their own parameter maps and distributions.",
        "weaker": "For the lattice notes, attack analysis and scheme correctness can need different bounds—and different events, such as message failure versus shared-key disagreement. This toy deliberately shares one event; it does not justify identifying those real events.",
        "contradiction": "For any probabilistic correctness claim, a counterargument must address the actual sampler, event and configuration. A weaker upper bound or an isolated failure is not interchangeable with this exact probability calculation.",
        "conditional": "This represents the structure of an ideal-to-actual distribution argument, including a quantified transfer loss. It supplies no distance bound for any real lattice, code or cryptographic sampler.",
        "alternative": "A real scheme can have several attack methods or security arguments. Their unproved premises belong to each argument separately; the direct finite bound here is not a substitute for evaluating those real algorithms.",
        "revision": "The same discipline matters when a code-based premise changes its target distribution, or a bound covers a smaller configuration domain. For number-theory schemes, unconditional correctness and conditional hardness also remain different conclusions.",
        "history": "All eight local scheme examples need this separation: new scientific evidence may change a displayed justification while an existing hourly round retains its original executable identity.",
    }
    cases = [(key, title, body + ("\n\n" + connections[key] if key in connections else ""))
             for key, title, body in cases]
    sections = [{"id": key, "title": title, "body": body} for key, title, body in cases]
    return note("Start here · Research that changes without losing its history",
        "Six small cases to try statements, alternative arguments and reviewed updates.",
        "# Local statement trial\n\n" + WARNING + "\n\n" + "\n\n".join(
            "## " + item["title"] + "\n\n" + item["body"] for item in sections), sections=sections)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def prepare(data_dir, *, check=False):
    from crucible import notebook
    from crucible.config import load_settings
    from crucible.notebook import NoteContent

    # Validate all fixed content without a database or publication side effects.
    audit()
    fake = {"id": "00000000-0000-0000-0000-000000000001", "revision": 1, "content_sha256": "a" * 64}
    for content in (premise_document(), premise_document(restricted=True), rough_document(fake),
                    exact_document(fake), conditional_document(), argument_document(fake, fake, fake)):
        NoteContent.model_validate(content)
    if check:
        print(json.dumps({"fixture": FIXTURE, "valid": True, "writes": 0, "finite_audit": audit()}, indent=2))
        return

    data_dir = data_dir.resolve()
    if not data_dir.is_relative_to(ROOT / ".local") or not (data_dir / "note-integration-receipt.json").is_file():
        raise SystemExit("Use the existing identified local note fixture under this checkout's .local directory.")
    if os.getenv("DATABASE_URL") or os.getenv("CRUCIBLE_ENV", "development") != "development":
        raise SystemExit("Use a local development environment without DATABASE_URL.")
    os.environ["CRUCIBLE_DATA_DIR"] = str(data_dir)
    os.environ["CRUCIBLE_LOCAL_NOTE_CORPUS_ONLY"] = "true"
    settings = replace(load_settings(), origins=("http://127.0.0.1:8000",), rate_limits=False,
                       reviewer_ids=(REVIEWER,), publication_screening_mode="disabled")
    if settings.production or not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("This builder is restricted to local SQLite development data.")
    notebook.MAX_WORKSPACE_BYTES = max(notebook.MAX_WORKSPACE_BYTES, 8 * 1024 * 1024)
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from crucible.app import create_app
    from crucible.db import User, LoginSession, Challenge, Submission
    from crucible.security import token_hash, mac
    app = create_app(settings)
    from crucible.statement_workflow import StatementUpdate, StatementCurrent, StatementAdoption
    for model in (StatementUpdate, StatementCurrent, StatementAdoption):
        model.__table__.create(app.state.db.engine, checkfirst=True)
    receipt_path = data_dir / "statement-trial-receipt.json"
    journal = json.loads(receipt_path.read_text()) if receipt_path.exists() else {
        "fixture": FIXTURE, "operations": {}, "notes": {}, "updates": {}}
    if journal.get("fixture") != FIXTURE:
        raise SystemExit("The trial journal belongs to another fixture.")

    def save():
        temp = receipt_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(journal, indent=2) + "\n")
        temp.replace(receipt_path)

    with app.state.db.transaction() as session:
        if any(session.get(User, identifier) is None for identifier in (AUTHOR, REVIEWER)):
            raise SystemExit("Prepare the existing readable-note fixture first; no accounts were added.")
        original_notes = {row.id: (row.content_sha256, digest(row.content))
            for row in session.scalars(select(notebook.NotebookRevision))}
        original_accounts = {row.id: (row.handle, row.password_hash) for row in session.scalars(select(User))}
        # Sensitive column values remain only in memory and are never printed or saved.
        def history_snapshot(model):
            return {row.id: digest({column.name: getattr(row, column.name) for column in model.__table__.columns})
                    for row in session.scalars(select(model))}
        original_challenges, original_submissions = history_snapshot(Challenge), history_snapshot(Submission)

    class Actor:
        def __init__(self, identifier):
            self.identifier = identifier
            self.client = TestClient(app)
            raw = str(uuid4()) + str(uuid4())
            with app.state.db.transaction(write=True) as session:
                session.add(LoginSession(token_hash=token_hash(raw), user_id=identifier, expires_at=time.time() + 3600))
            self.client.cookies.set(settings.cookie_name, raw)
            self.csrf = mac(settings.secret, "csrf-v1", raw.encode("ascii"))

        def post(self, slot, path, body):
            payload = {**body, "idempotency_key": str(uuid5(NAMESPACE_URL, FIXTURE + ":" + slot))}
            signature = digest({"actor": self.identifier, "path": path, "body": payload})
            previous = journal["operations"].get(slot)
            if previous:
                if previous["request_sha256"] != signature:
                    raise RuntimeError("A prepared trial operation changed; refusing to overwrite " + slot)
                return previous["response"]
            response = self.client.post(path, json=payload, headers={"Origin": settings.origins[0],
                "X-CSRF-Token": self.csrf, "Host": "127.0.0.1:8000"})
            if response.status_code != 200:
                raise RuntimeError(f"Local fixture API {path}: {response.status_code}: {response.text[:1000]}")
            value = response.json()
            journal["operations"][slot] = {"request_sha256": signature, "response": value}
            save()
            return value

    owner, reviewer = Actor(AUTHOR), Actor(REVIEWER)
    pins = {}

    def put(slot, content, previous=None):
        normalized = notebook._content(content)
        if previous:
            payload = {"content": content, "expected_revision": previous["revision"],
                       "content_sha256": previous["content_sha256"]}
            path = f'/api/notebook/notes/{previous["id"]}/revisions'
        else:
            path, payload = "/api/notebook/notes", {"content": content}
        record = owner.post("note-" + slot, path, payload)["note"]
        pin = {key: record[key] for key in ("id", "revision", "content_sha256")}
        with app.state.db.transaction() as session:
            revision = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == pin["id"], notebook.NotebookRevision.revision == pin["revision"]))
            if revision is None or revision.content_sha256 != pin["content_sha256"] or revision.content != normalized:
                raise RuntimeError("An exact fixture revision is unavailable or changed: " + slot)
        pub = {key: pin[key] for key in ("revision", "content_sha256")}
        owner.post("publish-" + slot, f'/api/notebook/notes/{pin["id"]}/publication-requests', pub)
        reviewer.post("share-" + slot, f'/api/notebook/notes/{pin["id"]}/publication-reviews', {
            **pub, "decision": "approved", "reason": "Clearly labelled local arithmetic rehearsal; publication is not scientific adoption of a KEM claim."})
        pins[slot] = pin
        journal["notes"][slot] = pin
        save()
        return pin

    premise = put("premise-v1", premise_document())
    rough = put("rough", rough_document(premise))
    exact = put("exact", exact_document(premise))
    put("conditional", conditional_document())
    arguments = put("arguments", argument_document(premise, rough, exact))
    put("premise-v2", premise_document(restricted=True), premise)

    def target(identifier, title, kind):
        return {"id": identifier, "title": title, "kind": kind,
            "scope": "Illustrative finite sampler only; alpha and beta, no real KEM or executable challenge.",
            "configuration_ids": ["trial-alpha", "trial-beta"]}

    targets = {"attack": target(TARGET_ATTACK, "Finite attack · success at least 7/8", "attack"),
               "correctness": target(TARGET_CORRECTNESS, "Finite correctness · failure at most 1/128", "correctness")}
    initial = {}
    for kind in ("attack", "correctness"):
        response = owner.post("initial-" + kind, "/api/notebook/statement-workflow/updates", {
            "target": targets[kind], "argument": source(arguments, argument_id=kind + "-hypothesis"),
            "evidence": [source(premise)], "expected_adoption_id": None,
            "reason": "Initialize the local illustrative conditional argument. Its premise remains a hypothesis; this is not a scientific admission."})
        update = response.get("update", response)
        reviewed = reviewer.post("adopt-initial-" + kind,
            f'/api/notebook/statement-workflow/updates/{update["id"]}/review', {
                "packet_sha256": update["packet_sha256"], "decision": "approve",
                "reason": "Local rehearsal: adopt the conditional implication only, without asserting its intentionally questionable premise is true."})
        initial[kind] = reviewed

    def adoption_id(kind):
        response = owner.client.get("/api/notebook/statement-workflow/targets/" + targets[kind]["id"],
            headers={"Host": "127.0.0.1:8000"})
        if response.status_code != 200:
            raise RuntimeError("Could not read the initial local adoption: " + response.text[:500])
        return response.json()["current"]["id"]

    updates = {}
    for slot, kind, arg, evidence, reason in (
        ("attack-replacement", "attack", "attack-loose-bound", rough,
         "Replace the Trial A-1-dependent attack justification with the direct 1/16 proof. Keep the same 7/8 success target; the stronger correctness claim is outside this update."),
        ("correctness-correction", "correctness", "correctness-hypothesis", exact,
         "Record that the original all-configuration correctness target cannot be maintained: beta has exact failure 1/32. Alpha and every executable challenge remain unchanged."),
    ):
        previous = journal["operations"].get("proposal-" + slot)
        if previous:
            update = previous["response"].get("update", previous["response"])
        else:
            response = owner.post("proposal-" + slot, "/api/notebook/statement-workflow/updates", {
                "target": targets[kind], "argument": source(arguments, argument_id=arg),
                "operation": "withdraw_argument" if kind == "correctness" else "select_argument",
                "evidence": [source(evidence)], "expected_adoption_id": adoption_id(kind), "reason": reason})
            update = response.get("update", response)
        updates[slot] = update["id"]
    journal["updates"].update(updates)
    guide = put("guide", guide_document(pins, updates))
    journal["guide_note_id"] = guide["id"]
    journal["guide_url"] = link(guide)
    with app.state.db.transaction() as session:
        for identifier, (sha, content_digest) in original_notes.items():
            revision = session.get(notebook.NotebookRevision, identifier)
            assert revision is not None and (revision.content_sha256, digest(revision.content)) == (sha, content_digest)
        assert original_accounts == {row.id: (row.handle, row.password_hash) for row in session.scalars(select(User))}
        assert original_challenges == history_snapshot(Challenge)
        assert original_submissions == history_snapshot(Submission)
    journal["preservation"] = {"existing_revisions_unchanged": True, "accounts_unchanged": True,
        "challenges_unchanged": True, "submissions_unchanged": True}
    journal["finite_audit"] = audit()
    save()
    app.state.db.engine.dispose()
    print(json.dumps({"fixture": FIXTURE, "guide_url": "http://127.0.0.1:8000" + link(guide),
        "notes": len({pin["id"] for pin in pins.values()}), "pending_update_ids": updates,
        "preservation": journal["preservation"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Validate finite arithmetic and note shapes without any database writes")
    options = parser.parse_args()
    prepare(options.data_dir, check=options.check)

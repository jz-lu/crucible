# Crucible

Finding and attacking new crypto in the AI age.

Agent entry point: `GET /api/agent/pipeline`, or `python3 scripts/crucible_cli.py guide`.
The [model-facing contract](web/agent/pipeline.json) defines the mission, task-specific
reading plan, evidence rules and supported workflows. CLI workspaces include a
copy; notebook handoffs link to it. In private beta, guidance requires membership
or an existing scoped connection and does not grant notebook or review authority.

A private research beta for shared-secret recovery challenges. The app has
accounts, durable records, a server-owned hourly deadline, and exact answer
verification. The numerical challenge interface is joined by an account-backed
Research notebook with private revisions and reviewed sharing. The older research
records and API remain available for future work. Source is shared for collaboration.
The app also includes versioned KEM specifications, private proposal intake,
immutable revisions, and manual review before trusted integration. New approvals
and admissions require a structured tier-design plan covering parameter growth,
costs, fixed correctness, endpoint evidence and useful intermediate steps. They
also require an explicit parameter-selection objective, candidate domain, attack
model and alternatives, with a separate manual assessment. See
[the admission requirements](docs/protocol-admission.txt).
The invitation-only beta is hosted at https://crucible.fail. Local development
and hosted beta data are separate. It is not open for public registration. See
[the release workflow](docs/releasing-beta.txt) for tested updates and rollback.

The expanded local catalogue has eight family pages. Research stages for
elliptic-curve DH, plain LWE/Frodo, module lattices, NTRU Prime, HQC and Classic
McEliece are explicitly separated from exact published reference releases.
There are 379 current profiles: DH, RSA, elliptic-curve DH and Classic McEliece
have 50 each; module lattices have 6; Frodo has 80; NTRU Prime has 27; HQC has 66.
Versioned releases improve parameter selection or remove representation overhead.
The available configurations and the default human progression are separate.
Default progressions use 50 stages for DH, RSA, elliptic-curve DH and McEliece,
6 for module lattices, 80 for Frodo, 25 for NTRU Prime and 66 for HQC.
The two NTRU intermediate configurations remain accessible; no protocol parameters
were changed to obtain rounded display counts.
Stage numbers do not imply equal security across families.
The module progression ends at ML-KEM-512; smaller rings are research settings
with unestimated security. New approvals require a claimed honest-failure bound
of at most 2^-128 (or perfect agreement) and a scoped security claim, with their
assumptions and reasoning. Explicit, independently motivated conjectural premises
are eligible for stress testing; approval does not prove them. See [the correctness policy](docs/correctness-policy-v1.txt) and
[the module design](docs/module-v4-design.txt).
HQC's new conditional-correctness path ends at the August 2025 published
construction; it does not inherit a current 128-bit security assessment. See
[the current-release review](docs/scheme-reviews/current-releases-20261017.txt)
for the exact constructions, endpoint meanings and remaining scientific limits.
See [the current stage progressions](docs/stage-progressions.txt), then
[the expansion decision record](docs/tier-expansion-decisions.txt) and its
scheme-specific implementation evidence.

DH release 7 and RSA release 4 provide stages 1–50. Their frozen classical
reference targets rise evenly from 8 bits at Stage 1 to 128 at Stage 50:
`8 + 120(t - 1)/49`. Actual parameter-derived references are plotted, including
rounding. DH also includes interval search for its new, explicit short-exponent
distribution. This restricted model is not a best-known attack estimate or a
security guarantee. Existing releases, records, accounts and sign-ins remain
unchanged; old recoveries do not automatically qualify for the new ladder.

The KEM Schemes catalogue orders schemes by participant count, not security.
Each scheme opens four graphs. Public-key and ciphertext sizes are fixed; the
lower cards switch among computation costs and classical/quantum estimates.
Retained local benchmark reports cover the 379 current configurations, but a
graph publishes a report only when its exact implementation binding validates
against the serving runtime. A different runtime can therefore show no timings
even when historical measurements exist; those measurements must not be relabeled
as measurements of the hosted build. See [the engineering follow-up](docs/engineering-handoff-status.txt).
Three RSA profiles in the retained reports
show hollow points for medians conditional on successful key generation, with
generation aborts disclosed and no line through those points. Quantum estimates remain empty
where unsupported; timings identify their implementation, hardware and scope.
The second tab is a participant Leaderboard; usernames link to
profiles with score breakdowns and original recovery receipts. **Personal
records** shows one highest recovered stage per scheme; **First to reach** shows
one highest first-place result per scheme. These can be different results.
Current releases take priority over archives, and incompatible release stages
are never compared. Full receipt history and the legacy First 10 API remain. See
[the provisional scoring policy](docs/scoring.txt) for the accepted formula and
reviewed scheme-admission awards. Policy v10 retains v9's eight current research
families with fixed per-configuration weights and adds provisional +1/+2 author
points from agent note ratings. Recovery credit remains up to 500 points for personal
progress and 2,000 for one best first record per scheme. Weights are incentive
choices, not equal-security measurements; changing navigation cannot reprice
them. Old HQC receipts remain archived and do not transfer to its new release. Exact-profile schemes keep independent
first places; only DH, RSA and the EC ladder inherit lower-tier places. Repeats
and intermediate steps add no cumulative points. These are participation points,
not equivalent security levels. Policy v2's original two-family enrollment and
the former First 10 profile display are superseded. Each stage features
one **Broken by** record with links to the participant and original evidence;
unsolved stages offer a direct path to the challenge. Shared-secret length remains
a protocol fact.

An issued challenge makes **Use with an agent** the main action. It opens a
compact panel with **Copy prompt**. **Manual tools** holds the optional
JSON download/copy and recovered-key submission controls. The downloadable
Python client connects through explicit browser approval, gets the exact challenge,
creates a local attack workspace, and submits a result with selected source files
and a compute report. No participant repository or per-result pull request is
required. See [the agent workflow](docs/agent-workflow.txt). Code is stored as
evidence and never executed by the server; reported compute is not independently
verified. The browser profile lets participants revoke connected agents.

## Research notebook

Open **Research notebook** in the main navigation (`/#notebook`). Titles expand
into tags and a claim; **Read full note** opens the conditions and full argument.
Saved notes now belong to your account and persist in the server database. Each
edit appends an immutable revision. New notes and revisions are private; request
sharing review to let a configured reviewer consider the exact revision. Approval
lets other signed-in participants read that revision, subject to beta membership
when the beta gate is enabled. Earlier and later private drafts stay private.
Sharing approval does not certify the mathematics, admit a scheme or award points.
The existing development-only self-review option is labelled and does not grant
sharing authority in production.

Configured moderators can withdraw an approved exact revision with a recorded
reason. Other readers see a neutral withdrawal notice; the author and reviewers
retain restricted audit history. Private-recipient access and frozen third-party
review packets cannot bypass the withdrawal. Corrections use a new revision.

**Agent files** exports your own notes and imports JSON additions
or appended revisions. Existing browser drafts are never uploaded automatically:
choose **Review browser drafts for import**, then explicitly import them into the
signed-in account. The browser copy stays untouched. Original history and imported
metadata are preserved as provenance; imported labels cannot grant review status.
The old example notes are illustrative, not assessments of live KEMs.

The CLI is the primary agent path for notes. Connect once with
`login --notebook --no-browser`, have the participant approve the displayed link,
then use `notebook list/show/export/import/create/revise/request-publication`.
`notebook delete` moves unsubmitted private drafts to Trash; `notebook restore`
recovers the exact deletion. Submitted evidence cannot be deleted.
Separate opt-in scopes cover owned private reads, writes and publication requests;
existing credentials keep their permissions. Agents need no browser automation.
See [the CLI workflow](docs/agent-workflow.txt) for exact commands and retry files.
Scheme proposals and concrete
change requests already pin exact notebook revisions and hashes; review does not
silently follow later edits. Community sharing review concerns safety and content
moderation. Substantive review for an official display or numerical challenge is
the second review level, with evidence appropriate to the requested use. See
[the proposal guide](docs/proposal-guide.txt). Public-launch publication rules
remain a separate decision. Back up the server database and persistent key;
server backups cannot recover browser drafts that have not been imported. See
[the notebook's scope and design boundaries](docs/research-notebook-draft.txt).

## Run locally

Requires Python 3.11 or newer. The expanded catalogue also needs the reviewed
native reference workers. On a new checkout, first follow
[native setup](docs/native-setup.txt); these downloads and builds are explicit,
and the server never compiles or runs submitted participant source. Then:

```sh
./run.sh
```

Open **http://127.0.0.1:8000**. On a fresh checkout, the script creates a virtual
environment, installs pinned dependencies, applies database migrations, and
starts the server on loopback. Stop it with Ctrl+C. No Node build is needed.

In default open local mode, create an account with a public handle and a password
of 12–128 characters. Optional private beta mode requires explicit membership or
an invitation with mailbox verification. See [private beta setup](docs/private-beta-setup.txt)
for operator controls and local mail testing. GitHub sign-in and verified-mailbox
password recovery are implemented; their provider/mail settings must be configured
for the deployment. ORCID linking remains pending. Sessions last 24 hours and can be ended
with Sign out.

The default SQLite database and persistent server key live in ignored `.local/`.
Back them up together. Changing the key makes old pending answers unverifiable.
The launcher makes local files private to the current OS user.

## Try a complete challenge

1. Choose a KEM from the catalogue to open its overview and graph grid.
   View challenges opens its leaderboard at the next not-yet-recovered stage.
   Click a nearby stage, drag the slider, or use Jump to for any supported
   integer. Jump to starts empty; the rail and slider show the selected stage.
   Leaving the field blank does nothing. A dot marks the next unsolved target; the return button takes you
   there and hides while it is selected. Green marks recorded stages; lower-tier
   inheritance is used only where that release explicitly declares it.
2. Open Challenge and request your first instance. Other stages in this scheme’s
   current round load automatically when opened in Challenge. Reloading reuses the
   same instance; a new hourly round requires a fresh explicit start. The timer
   shows the shared UTC round boundary; joining later leaves less time.
3. Choose **Use with an agent**, then **Copy prompt**, and give the prompt
   to your agent. Approve its connection in your browser. The CLI retrieves the
   exact instance and creates a local workspace; no manual copying of inputs is
   required. See [the agent workflow](docs/agent-workflow.txt).
4. Develop the attack locally. The CLI records the chosen command's wall time,
   packages selected source with your method and compute declaration, and submits
   on an explicit command. The server's receipt deadline remains authoritative.
5. For a manual workflow, expand **Manual tools** in Challenge. Download or copy
   the JSON contract there, or open **Upload a result or enter a key** to upload a result
   file or fill the form. The small examples also have public-input solvers:

   ```sh
   python3 scripts/solve_teaching_frontier.py /path/to/dh-challenge.json
   python3 scripts/solve_rsa_frontier.py /path/to/rsa-challenge.json
   ```

   Use only the script matching the downloaded scheme. The DH solver bounds the
   actual subgroup order to 36 bits; the RSA solver bounds the actual modulus
   to 36 bits. These are physical parameter limits, not challenge-tier limits.
   Larger inputs are available for participants' own methods. The examples print
   a candidate key and do not submit it.
   Submit the full shared secret in the length declared by that challenge,
   method/pseudocode, and compute declaration. You can
   enter these manually or import a `crucible-recovery-v1` JSON result file of at
   most 128 KiB through **Manual tools**. The template includes
   complete instructions. A file for another stage selects its exact owned, open
   instance automatically. Import fills editable fields for review; it never
   submits an answer.
   A well-formed answer is final. Immutable methods can be reused in later rounds.
6. A matching answer qualifies for its exact stage. DH, RSA and elliptic-curve DH
   additionally inherit lower-tier standings within the explicitly registered
   release; other current families do not infer lower-tier recovery. Each board keeps
   the first ten distinct accounts by their earliest qualifying receipt. Inherited
   credit is labelled "via stage N"; click it to read the actual recovery's method
   and compute details. The server verifies the key and receipt deadline; method
   and compute remain contributor-reported.

The catalogue's Recovered by column identifies the earliest successful account
at the displayed largest recovered stage. Clicking the name opens the participant
profile; individual receipt links show the method, pseudocode, reported compute,
stage and original release.
The catalogue orders schemes by participant count descending, alphabetically on
ties. This is activity ordering, not a security ranking. Its Participants
count is the number of distinct accounts with at least one recorded recovery
attempt, correct or incorrect. It excludes challenge requests/downloads, draft
imports and rejected requests. Accounts are counted once across profiles and
rounds, using only the exact release and explicitly compatible source profiles
from other releases. This is participation, not a count of verified independent
people or successful attacks. Recorded recovery stages do not determine order;
each scheme retains its own recovery leaderboards. Largest recovery shows Stage N.
Its question-mark button explains the stage reference and its limits; it is no
longer beside the challenge selector or below the KEM Schemes title. The
Participants explanation is available in its header tooltip, without an extra
paragraph below the table.
Within a profile, Time to submit measures instance issue to answer receipt,
including delays; the participant's attack-time estimate is reported separately.
Scheme specifics gives a compact construction/parameter summary. Protocol retains
the full contract. Public inputs remain in the download; the page omits the raw
input disclosure and opening pitch. The footer contains only the blue flame-and-key logo
and Crucible name.

My submissions labels entries with the scheme name and stage in parentheses,
as “Stage N”, followed by the recorded method and receipt time; it omits the internal
round number. Each entry opens its own submission details, including unsuccessful
attempts. Correct results are publicly readable at `GET /api/submissions/{id}`;
incorrect results are visible only to their owner. The endpoint excludes submitted
keys and private verification data, and preserves the original frozen tier/version
even when a compatible release or smaller stage inherits leaderboard credit.

The About us navigation link opens an empty page template. Add project copy inside
`#about-workspace` in `web/index.html`; its collaborator comment marks the space.
Protocol math uses local KaTeX assets in `web/vendor/katex`, with their licenses.

Rounds are global UTC hours. **They end on the hour, even when you join late.**
Each account receives one independent instance per exact profile/version/round.
Reloading returns the same instance. A complete submission must reach the server
strictly before the deadline. Identical retries return the original receipt,
including after the deadline. The hour is a response window, not a compute cap.

## Research beyond the leaderboard

The Research interface is temporarily hidden to keep this MVP focused. Its stored
records and API are preserved. Findings can contain theoretical arguments,
experiments, partial progress, or documented unsuccessful investigations, with
exact targets, conditions, supporting text and optional paper/code links.
Revisions and follow-ups preserve earlier versions and attribution.

**Routine numerical runs do not automatically create findings.** A deliberate
finding can cite an owned verified recovery as supporting evidence. The finding
remains unreviewed: checking one key does not verify its broader argument, claimed
method, compute, or novelty. There is no universal theoretical ranking, security
score, or inference that an expired challenge is a failed investigation.

Catalogues are curated in code. Arbitrary uploaded code is never executed.

Scoring is implemented under the explicitly provisional policy above. Recovery
points are derived from verified receipts; scheme contribution points require
separate independent admission. Proposal approval alone never awards points.
Frozen configuration credits have different meanings across families, with equal
500-point personal and 2,000-point first-record ceilings retained in policy v10. The
formula rewards participation, not equivalent security or scientific merit.

## Propose and review a KEM

The proposal workflow uses one `crucible-kem-spec-v1` JSON format for new
proposals and descriptions of exact registered releases. It covers
setup/key generation/encapsulation/decapsulation, parameters and stage mappings,
distributions and reused state, encodings, correctness scope and evidence,
security claims, and optional implementation/reference links. The file and its
canonical form must fit 48 KiB; the complete API request must fit 64 KiB.

Download the annotated template or schema, or use an existing protocol's exact
specification as an example. A reference implementation is optional for initial
intake. Format validation checks the document's structure and label consistency;
it does not verify correctness, security, executable conformance, or novelty.
The checked document is submitted explicitly by a signed-in author and stays
private to that author and configured reviewers.

Choose **Use with an agent** on the proposal page for a copyable handoff, or use
the CLI directly:

```sh
python3 scripts/crucible_cli.py proposal init --path my-proposal
# Fill in my-proposal/specification.json with your agent.
python3 scripts/crucible_cli.py proposal validate --path my-proposal --json
python3 scripts/crucible_cli.py proposal submit --path my-proposal --json
python3 scripts/crucible_cli.py proposal show PROPOSAL_ID --json
python3 scripts/crucible_cli.py proposal revise PROPOSAL_ID --path my-revision
```

Drafting and format checks need no account. Submission uses the same browser
connection as the challenge workflow; connections created before proposal support
need a new `login` to grant the additional permissions. Agent proposal access is
owner-only, even for reviewer accounts. Manual upload is under **Manual tools**.
The [agent workflow](docs/agent-workflow.txt) explains feedback, revisions, private
workspaces and safe retries. Neither path executes supplied code or admits a scheme.

- `GET /api/protocol/spec-schema` — JSON Schema.
- `GET /api/protocol/spec-template` — annotated, intentionally incomplete template.
- `GET /api/protocol/spec?scheme_id=...&version=...` — exact registered specification.
- `GET /api/protocol/source?scheme_id=...&version=...` — the separately pinned source.
- `POST /api/protocol/proposals/validate` — anonymous, same-origin format validation
  of `{specification}`; returns canonical content, its hash, and the validation scope.
- `POST /api/protocol/proposals` — authenticated intake with `{specification,
  idempotency_key}`; `GET /api/me/protocol-proposals` lists the author's records.
- `GET /api/protocol/proposals/{id}` — authorized detail and revision/review history.
- `POST /api/protocol/proposals/{id}/revisions` — author revision, requiring the
  current revision/hash and a retry identifier; earlier content remains intact.
- `GET /api/protocol/review-queue` and
  `POST /api/protocol/proposals/{id}/reviews` — configured reviewers only.

Each immutable revision has a canonical SHA-256. Review decisions bind to its
exact revision, content hash, and implementation artifact hash. Independent
reviewers record checks for specification completeness, correctness,
implementation conformance, input/resource safety, reproduction, stage design and
parameter selection, with notes and concrete evidence. Approval requires all
checks, a pinned implementation and a correctness claim meeting the 2^-128 goal
(or perfect agreement). Statistical claims declare an exponent of at least 128;
unknown or weaker claims remain drafts. These are
manual assessments, not automated proofs. Authors normally cannot review their
own proposals. A configured reviewer can test their own proposal locally by
starting development with `CRUCIBLE_ALLOW_SELF_REVIEW=true`; this defaults off
and is rejected in production. Such decisions are visibly marked **Development
self-review** and cannot satisfy independent admission or earn contribution
points. Reviewer authorization and all normal evidence requirements still apply.
Changes requested and later
approvals remain in the audit history; every new revision returns to `submitted`
and cannot inherit an earlier approval.

Approval means ready for trusted integration review. It never executes an upload,
fetches a URL, or changes the challenge registry. After deliberately integrating
reviewed code as a registered adapter, an operator can run:

```sh
.venv/bin/python -m crucible.manage check-proposal \
  --proposal-id PROPOSAL_UUID --scheme-id SCHEME_ID --version RELEASE
```

This read-only gate checks the approved snapshot and review, source fingerprint,
release, exact configuration coverage, and declared fixed shared-secret length. Activating a
scheme remains a separately reviewed code change. Existing teaching schemes and
their historical records are not retroactively labelled reviewed.

Reviewer access defaults to nobody and uses stable account UUIDs. For local
operation, resolve an existing account explicitly and restart the server:

```sh
.venv/bin/python -m crucible.manage grant-reviewer --handle EXISTING_HANDLE
.venv/bin/python -m crucible.manage revoke-reviewer --handle EXISTING_HANDLE
```

The commands update private `.local/reviewers.json` (or `CRUCIBLE_DATA_DIR`).
Production uses `CRUCIBLE_REVIEWER_IDS`; an explicit environment value overrides
the local file. Registering a matching name later never grants reviewer access.
See [specification format](docs/protocol-specification.txt) and
[review/admission procedure](docs/protocol-admission.txt) for the full contract.

## Challenge stages and the included KEMs

The selector is an integer **challenge stage**, distinct from parameter bits and
estimated security. Every stage names an exact registered setting on a versioned default route.
Other configurations remain available by stable profile ID. Existing `instance_size`
and `--tier` selectors retain their original configuration meaning; use `--stage`
for the displayed progression, or `--profile-id` for any exact configuration. A roughly
128-bit upper reference is an initial aim where supported; natural stage counts,
unestimated small profiles and explicitly provisional endpoints remain valid.

| Catalogue family | Current release | Stages | Selection or representation change |
| --- | --- | --- | --- |
| Finite-field DH | `teaching-dh-kem` / `7` | 1–50 | Certified fields; explicit shorter exponent intervals include interval-search cost |
| RSA | `rsa-kem` / `4` | 1–50 | Same balanced certified factors; three-byte exponent encoding removes padding |
| Elliptic-curve DH | `dhkem-x25519` / `curve-compressed-v2` | 1–50 | Same certified curves and full scalars; compressed point encoding |
| Module lattice | `ml-kem` / `compressed-research-v4` | 1–6 | Fixed ML-KEM-512 settings; degree 8..256, explicit correctness model; standard endpoint |
| Frodo | `frodokem` / `anchor-research-v3` | 1–80 | Every dimension 8..640 by 8; preserves the published 640 anchor |
| Streamlined NTRU Prime | `sntrup` / `3` | 1–25 (27 configurations) | Bounded byte-cost selection from 103 certified candidates using a hybrid-inclusive reference |
| HQC | `hqc` / `modeled-research-v3` | 1–66 | Conditional correctness; restricted codec search through historical HQC-1 reference; current security unestimated |
| Classic McEliece | `classic-mceliece` / `length-research-v1` | 1–50 | Existing shortened-code path retained; equal-security parameter efficiency unresolved |

These 379 profiles are not a claim that all eight ladders are optimal. A bounded
search needs a stated objective, preserved construction and correctness,
candidate domain, attack coverage and evidence. Smaller wire objects can cost
more decoding work. Unknown estimates cannot be treated as low security or used
to prove dominance. A new proposal must explain these choices explicitly.
See [the parameter review](docs/parameter-frontier-review.txt),
[classical changes](docs/classical-parameter-frontier.txt),
[lattice changes](docs/lattice-parameter-frontier.txt),
[NTRU selection](docs/ntru-parameter-frontier.txt),
[HQC's revised model and endpoint](docs/hqc-model-v3.txt), and
[McEliece's current review](docs/mceliece-parameter-review-20261017.txt).

The current implementations are local research constructions, not deployment
libraries or inherited security guarantees from their named families. Published
reference releases remain separate. Frodo keeps one common honest-failure bound
of 2^-133 in its declared model. Module-v4 uses deterministic, conditional and
modeled bounds within its common 2^-128 claim. HQC-v3 claims 2^-128 under its
explicit independent-noise surrogate and conjectural conservative transfer to
actual correlated errors; the remaining current ladders claim
perfect mathematical honest agreement under their stated premises. Resource aborts and implementation faults are
separate. Passing samples do not prove these correctness claims or confidentiality.

Each overview has four graphs with Challenge stage on the horizontal axis.
Public-key/ciphertext bytes come from canonical binary encodings, excluding
JSON/hex wrappers and separately registered shared parameters. RSA now counts
one modulus-width integer plus the three-byte exponent. Computation metrics use
explicitly selected reference-implementation reports; unmeasured configurations
remain gaps. The graph's Method and results disclosure identifies the machine,
implementation, sample counts and provisional local-pilot status. These timings
are not full TLS handshake latency or security estimates. Classical references display only supported values;
unsupported/quantum estimates remain empty. The red marker shows the largest
actual recovery, not a proof that every smaller profile or instance is solved.

Each scheme overview also links to a quiet **Assumptions** page. Its expandable
statements distinguish hardness questions, model premises, derived correctness,
and remaining proof obligations for that exact release. These are partial
curated drafts, with sources and scope; they are not security certifications or
automatically published notebook notes. Read-only structured sheets are available
at `/api/protocol/assumptions?scheme_id=…&version=…`, behind the same beta boundary
as the scheme catalogue. See [the sheet contract](docs/assumptions-sheets.txt).

Exact challenge downloads retain version, profile ID, parameter values, encoding,
source/artifact hashes, correctness scope and any frozen initial estimate.
Automation selects a default stage with `crucible_cli.py init teaching-dh-kem --stage 21`,
or any exact configuration with `--profile-id`. Direct API callers resolve the
versioned default_progression mapping from `/api/schemes` and send its exact
`profile_id`. Legacy `instance_size` and `--tier` retain their original configuration
selectors, which can differ from the new stage numbers; send one selection form. Shared-secret length is a protocol fact, independent of
the displayed stage. The server verifies the complete secret before the hourly
deadline; elapsed time is not a compute budget.

Previous releases keep their identifiers, artifacts and original receipts.
Current policy does not transfer old results to the six replacements or silently
reinterpret their stages. Earlier explicit DH compatibility remains an archive
rule. Accounts, sessions and server secrets are preserved; database files,
credentials and backups never belong in Git.

## Development and verification

Run the backend, browser and migration checks for the current revision. Exact
completed validation and deployment checkpoints are recorded in
[the running work list](docs/work-list.txt); earlier counts describe those
historical revisions, not the current full suite.

```sh
.venv/bin/python -m pytest -q
for test in tests/test_*.cjs; do node "$test" || exit 1; done
.venv/bin/alembic check
.venv/bin/python -m crucible.manage cleanup
```

Checks cover public-input recovery, auth/ownership, CSRF, answer privacy,
concurrency, idempotency, hourly deadlines, correctness incidents, immutable
archives, scoring, parameter-selection review and development-only self-review.
The new adapters add independent arithmetic/native conformance, exact correctness
certificates, canonical-encoding rejection and public CLI examples. Tests
validate the specified workflows and evidence boundaries; they are not security
certification. Cleanup removes expired sessions/rate counters while retaining
research and proposal records. Historical browser and migration results remain
in the work list.

The stack is FastAPI, SQLAlchemy, Alembic, Argon2id, and plain HTML/CSS/JavaScript.
Configuration comes from process environment; `.env.example` is documentation,
not automatically loaded. SQLite is the tested local database. PostgreSQL is
supported by the persistence layer and migrations; its concurrency behavior still
needs integration testing before a public release.

Production mode requires PostgreSQL, an explicit HTTPS origin, a persistent
random server secret and beta access. Hosted issuance uses the version-controlled
approved release catalogue; an optional environment override can restrict it.
See [beta release policy](docs/beta-release-policy.txt) for the one-time transition
from a legacy override and subsequent reviewed additions. These guards are **not a claim of production
readiness**: identity/recovery policy, abuse/moderation, hosting limits, proxy
trust, monitoring, backups, and independent admission/security review remain.

- [Architecture and API](docs/architecture.txt)
- [Pipeline and mission audit, 18 October 2026](docs/pipeline-audit-20261018.txt)
- [Private beta setup and release gates](docs/private-beta-setup.txt)
- [Private beta and public hosting direction](docs/hosting-plan.txt)
- [Current and remaining decisions](docs/implementation-v1.txt)
- [Correctness policy](docs/correctness-policy-v1.txt)
- [Reference timing method and publication](docs/reference-timings.txt)
- [Current correctness evidence and review gaps](docs/correctness-audit-reference.txt)
- [Design principles and decisions](docs/design-record.txt)
- [Running work list](docs/work-list.txt)
- [Recovered-key submission files](docs/submission-files.txt)
- [KEM specification format](docs/protocol-specification.txt)
- [Manual protocol review and admission](docs/protocol-admission.txt)
- [Frozen classical reference models](docs/security-calibration-v1.txt)
- [Contributing](CONTRIBUTING.md)

`prototype/` preserves the earlier static draft. Its illustrative records and
browser-local form are historical; the working app is in `web/` and `crucible/`.

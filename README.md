# Crucible

Finding and attacking new crypto in the AI age.

A working local foundation for shared-secret recovery challenges. The app has
accounts, durable records, a server-owned hourly deadline, and exact answer
verification. The current interface focuses on numerical results; the research
records and API remain available for future work. Source is shared for collaboration.
The app also includes versioned KEM specifications, private proposal intake,
immutable revisions, and manual review before trusted integration.
The hosted service has not been deployed and is not ready for public operation.

DH release 6 and RSA release 3 provide tiers 1–50. Their frozen classical
reference targets rise evenly from 8 bits at Tier 1 to 128 at Tier 50:
`8 + 120(t - 1)/49`. Actual parameter-derived references are plotted, including
rounding. This restricted rho/NFS model is not a best-known attack estimate or a
security guarantee. Existing releases, records, accounts and sign-ins remain
unchanged; old recoveries do not automatically qualify for the new ladder.

The KEM Schemes catalogue orders schemes by participant count, not security.
Each scheme opens four graphs. Public-key and ciphertext sizes are fixed; the
lower cards switch among computation costs and classical/quantum estimates.
Timing and quantum estimates remain empty until supported evidence exists.
The second tab is a participant Leaderboard; usernames link to
profiles with score breakdowns and original recovery receipts. See
the **First 10 milestones** candidate on profiles for standout early results and
expandable history; it adds recognition without changing points. See
[the provisional scoring policy](docs/scoring.txt) for the accepted formula and
reviewed scheme-admission awards. Policy v2 gives record bonuses to first place
only, while preserving everyone's personal-progress points. Each tier features
one **Broken by** record with links to the participant and original evidence;
unsolved tiers offer a direct path to the challenge. Shared-secret length remains
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

## Run locally

Requires Python 3.11 or newer. From this checkout:

```sh
./run.sh
```

Open **http://127.0.0.1:8000**. On a fresh checkout, the script creates a virtual
environment, installs pinned dependencies, applies database migrations, and
starts the server on loopback. Stop it with Ctrl+C. No Node build is needed.

Create an account with a public handle and a password of 12–128 characters. This
local iteration has no email verification or password-reset service; use a
password manager. Sessions last 24 hours and can be ended with Sign out.

The default SQLite database and persistent server key live in ignored `.local/`.
Back them up together. Changing the key makes old pending answers unverifiable.
The launcher makes local files private to the current OS user.

## Try a complete challenge

1. Choose a KEM from the catalogue to open its overview and graph grid.
   View challenges opens its leaderboard at the next not-yet-recovered tier.
   Click a nearby tier, drag the slider, or use Jump to for any supported
   integer. Jump to starts empty; the rail and slider show the selected tier.
   Leaving the field blank does nothing. A dot marks the next unsolved target; the return button takes you
   there and hides while it is selected. Earlier tiers are shaded light green.
2. Open Challenge and request your first instance. Other tiers in this scheme’s
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
   python3 scripts/solve_teaching.py /path/to/dh-challenge.json
   python3 scripts/solve_rsa.py /path/to/rsa-challenge.json
   ```

   Use only the script matching the downloaded scheme. The DH solver bounds the
   actual subgroup order to 36 bits; the RSA solver bounds the actual modulus
   to 36 bits. These are physical parameter limits, not challenge-tier limits.
   Larger inputs are available for participants' own methods. The examples print
   a candidate key and do not submit it.
   Submit the full 32-byte key, method/pseudocode, and compute declaration. You can
   enter these manually or import a `crucible-recovery-v1` JSON result file of at
   most 128 KiB through **Manual tools**. The template includes
   complete instructions. A file for another tier selects its exact owned, open
   instance automatically. Import fills editable fields for review; it never
   submits an answer.
   A well-formed answer is final. Immutable methods can be reused in later rounds.
6. A matching answer qualifies for that tier and lower-tier boards in the same
   release, subject to the explicit release-compatibility rules. Each board keeps
   the first ten distinct accounts by their earliest qualifying receipt. Inherited
   credit is labelled "via tier N"; click it to read the actual recovery's method
   and compute details. The server verifies the key and receipt deadline; method
   and compute remain contributor-reported.

The catalogue's Recovered by column identifies the earliest successful account
at the displayed largest recovered tier. Clicking the name opens that exact
receipt's method, pseudocode, reported compute, tier, and original release.
The catalogue orders schemes by participant count descending, alphabetically on
ties. This is activity ordering, not a security ranking. Its Participants
count is the number of distinct accounts with at least one recorded recovery
attempt, correct or incorrect. It excludes challenge requests/downloads, draft
imports and rejected requests. Accounts are counted once across profiles and
rounds, using only the exact release and explicitly compatible source profiles
from other releases. This is participation, not a count of verified independent
people or successful attacks. Recorded recovery tiers do not determine order;
each scheme retains its own recovery leaderboards. Largest recovery shows Tier N.
Its question-mark button explains the tier reference and its limits; it is no
longer beside the challenge selector or below the KEM Schemes title. The
Participants explanation is available in its header tooltip, without an extra
paragraph below the table.
Within a profile, Time to submit measures instance issue to answer receipt,
including delays; the participant's attack-time estimate is reported separately.
Scheme specifics gives a compact construction/parameter summary. Protocol retains
the full contract. Public inputs remain in the download; the page omits the raw
input disclosure and opening pitch. The footer contains only the blue flame-and-key logo
and Crucible name.

My submissions labels entries with the scheme name and tier in parentheses,
as “Tier N”, followed by the recorded method and receipt time; it omits the internal
round number. Each entry opens its own submission details, including unsuccessful
attempts. Correct results are publicly readable at `GET /api/submissions/{id}`;
incorrect results are visible only to their owner. The endpoint excludes submitted
keys and private verification data, and preserves the original frozen tier/version
even when a compatible release or smaller tier inherits leaderboard credit.

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

Scoring is not implemented, and no final formula has been adopted. The foundations
are explicit recovery receipts, method/compute declarations, release provenance,
and manual scheme review. Rewards for accepted new schemes, personal progress
and first breakthroughs remain goals for deliberate incentive analysis, including
repetition and multiple-account risks. Proposal approval does not award points.

## Propose and review a KEM

The proposal workflow uses one `crucible-kem-spec-v1` JSON format for new
proposals and descriptions of the exact registered DH/RSA releases. It covers
setup/key generation/encapsulation/decapsulation, parameters and tier mappings,
distributions and reused state, encodings, correctness scope and evidence,
security claims, and optional implementation/reference links. The file and its
canonical form must fit 48 KiB; the complete API request must fit 64 KiB.

Download the annotated template or schema, or use an existing protocol's exact
specification as an example. A reference implementation is optional for initial
intake. Format validation checks the document's structure and label consistency;
it does not verify correctness, security, executable conformance, or novelty.
The checked document is submitted explicitly by a signed-in author and stays
private to that author and configured reviewers.

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
implementation conformance, input/resource safety, and reproduction, with notes
and concrete evidence. Approval requires all checks, a pinned implementation,
and an explicit correctness claim. These are manual assessments, not automated
proofs. Authors cannot review their own proposals. Changes requested and later
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
release, tier coverage, and declared fixed shared-secret length. Activating a
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

## Challenge tiers and the included KEMs

The common selector is an integer **challenge tier**, distinct from the physical
parameter bit length. The new candidate releases share a frozen initial target
curve: tiers 1–25 target 8–32 reference bits in one-bit steps; tiers 26–50 target
35.84–128 in 3.84-bit steps. Each tier maps to exact registered parameters.

| Catalogue name | Candidate release | Supported tiers | Physical parameter mapping |
| --- | --- | --- | --- |
| Finite-field Diffie–Hellman | `teaching-dh-kem` / 5 | Every integer 1–50 | Prime field size 32–3072 bits; subgroup order has ceil(field bits / 2) + 1 bits |
| RSA over an integer modulus | `rsa-kem` / 2 | Every integer 1–50 | Balanced RSA modulus size 32–3072 bits |

The `classical-baseline-v1` reference takes the minimum of a generic rho term
and a normalized NFS leading term, anchored at 3072 bits → 128 reference bits.
For RSA the rho term is one quarter of the modulus bit length; for DH it is
one half of log2 of the actual subgroup order. This restricted model omits ECM,
other attacks, and important cost constants. It is not a best-known attack
estimate, equal real-world difficulty, an hourly success prediction, a security
lower bound, or an IND-CPA/IND-CCA claim. Classical and quantum costs remain
separate. See [the frozen model, references and limitations](docs/security-calibration.txt).
An accepted key records one recovery; it does not by itself contradict the model.

Both adapters output 32-byte shared secrets independently of tier and are
unsuitable for protecting information. They use custom transcript-bound SHA-256
KDFs and variable-time Python arithmetic. RSA does not claim standardized RSA-KEM
conformance. Manifests and downloads retain the exact tier in `instance_size`,
physical size in `parameter_bits`, parameters, encoding, source hash, release,
correctness scope, and frozen initial estimate. Automation can select a challenge
with `{"scheme_id":"teaching-dh-kem","instance_size":21}` or the corresponding
`profile_id`; send exactly one selection form.

DH release 5 uses offline-generated, embedded prime-field and subgroup parameters
with exact recursive Pocklington certificates. The prime subgroup order divides
p−1 and its square exceeds p; the registered generator has that exact order.
The public group remains deliberately shared across accounts and rounds at each
tier, while new instances use fresh secret and encapsulation exponents. This
allows legitimate group-specific preparation and its amortization across targets.
The generation procedure is reproducible and structured, not uniform over all
possible groups. Source and parameter artifacts are pinned.

RSA release 2 generates fresh balanced prime factors for every new key. Private
recursive Pocklington certificates prove primality exactly, with trial division
at the leaves; primality is proved rather than accepted probabilistically. The
structured distribution
is not uniform over all primes. Generation is bounded by a shared candidate cap,
factor-pair cap, and five-second monotonic deadline checked between bounded
operations and before returning a key. This is not a hard real-time SLA. Exhaustion
returns no instance and can be retried explicitly; it is separate from correctness.
Generation runs outside the database writer transaction, with identity, round,
source and duplicate-issuance checks repeated before saving.

Both correctness arguments establish perfect agreement for honest inputs under
the registered arithmetic and encodings, conditioned on successful key generation.
RSA's CRT argument includes nonunit encapsulation values. Implementation tests
check executable behavior; neither those tests nor prime certificates certify
confidentiality or production security. A correctness mismatch or unexpected
adapter fault records an incident and pauses issuance, without retrying until
an agreement succeeds. Independent admission review remains pending.

Each scheme overview has a two-by-two graph grid with Challenge tier on each
horizontal axis. The first two cards show public-key and ciphertext bytes.
Values come
from exact release metadata and are also in expandable keyboard-accessible tables.
Counts use canonical binary encodings, excluding JSON/hex wrappers and registered
shared domain parameters. DH counts one field element for each public key and
ciphertext; RSA counts two modulus-width integers for the public key (including
padded e), and one for ciphertext. These are representation costs, not security.

The computation card offers key generation, encapsulation and decapsulation but
has no fabricated timings. The security card shows the frozen classical reference
for calibrated releases; quantum and unsupported archived estimates remain empty.
All graph cards mark the largest actual recorded recovery when one exists. The
marker emphasizes that record; it is not evidence that every lower tier or
instance was solved. Overview notes are temporarily hidden to keep the page
focused; protocol/source descriptions retain the underlying facts. Browsing
never issues a challenge, and returning through the overview preserves the
selected profile and release.

Old DH releases 1–4 and RSA release 1 remain unchanged, with their original source
hashes, ranges and parameter meanings. Earlier explicit DH compatibility remains
an archive rule. DH 5 and RSA 2 have new profile identifiers and KDF domains;
old bit-length tiers are not relabelled as calibrated tiers or folded into their
new boards. The separately authorized local activity reset was completed after
a private backup. Existing accounts, sign-ins and server secrets were preserved;
no database, credentials, session records or backup files belong in Git.

## Development and verification

Current verification: 508 backend tests and 118 frontend groups passed, plus
isolated browser workflows and read-only live desktop/mobile checks.

```sh
.venv/bin/python -m pytest -q
node tests/test_frontend.cjs
node tests/test_recovery_upload.cjs
node tests/test_math.cjs
node tests/test_round_access.cjs
node tests/test_submission_details.cjs
.venv/bin/alembic check
.venv/bin/python -m crucible.manage cleanup
```

The latest agent-workflow checkpoint passes 732 backend/CLI cases and 143 frontend
checks, including device authorization, revocation, immutable source snapshots,
retry behavior, and additive migrations. Isolated Chrome and a real CLI run
exercise approval through an accepted RSA recovery and downloadable evidence.

Tests cover recovery, auth and ownership, CSRF, input privacy, concurrent
issue/submit, idempotency, completed-body receipt timing, exact hourly boundaries,
correctness incidents, release compatibility, ranking, research revisions,
navigation, and result-file import. Cleanup removes expired sessions and rate
counters, retaining research and proposal records. The previous verified
checkpoint passed 392 backend tests and 115 frontend groups: 37 navigation/overview, 34 upload,
11 round-access, 9 submission-detail, and 24 proposal/review groups. Isolated
Chrome verified upload, format errors, registration with a retained draft,
submission, requested changes, author revision, exact-hash approval, audit history,
privacy, and desktop/320px layouts. Approval left the live catalogue unchanged.
Earlier isolated checks cover both KEMs from public-input solving through file
review and accepted recovery. These checks validate workflows, not cryptographic
security. That checkpoint was deployed locally on port 8000; the calibrated batch
above passed 508 backend tests and 118 frontend groups, and is now deployed
locally after isolated/live browser verification. Migration 0003
preserved all existing rows in ten application tables; sources and catalogue
records were unchanged in that rollout. The authorized countableclouds account
has reviewer access.
Additional browser checks covered exact chart values, metric switching, then-empty
timing/security templates, archived releases, participant order, moved tier help,
and 320px/768px/
1440px layouts. Upstream logo/theme commits through acefcb9 were merged while
preserving local work. See the running work list for remaining work.

The stack is FastAPI, SQLAlchemy, Alembic, Argon2id, and plain HTML/CSS/JavaScript.
Configuration comes from process environment; `.env.example` is documentation,
not automatically loaded. SQLite is the tested local database. PostgreSQL is
supported by the persistence layer and migrations; its concurrency behavior still
needs integration testing before a public release.

Production mode requires PostgreSQL, an explicit HTTPS origin and a persistent
random server secret. See `.env.example`. That guard is **not a claim of production
readiness**: identity/recovery policy, abuse/moderation, hosting limits, proxy
trust, monitoring, backups, and independent admission/security review remain.

- [Architecture and API](docs/architecture.txt)
- [Current and remaining decisions](docs/implementation-v1.txt)
- [Correctness policy](docs/correctness-policy-v0.txt)
- [Design principles and decisions](docs/design-record.txt)
- [Running work list](docs/work-list.txt)
- [Recovered-key submission files](docs/submission-files.txt)
- [KEM specification format](docs/protocol-specification.txt)
- [Manual protocol review and admission](docs/protocol-admission.txt)
- [Frozen classical reference model](docs/security-calibration.txt)
- [Contributing](CONTRIBUTING.md)

`prototype/` preserves the earlier static draft. Its illustrative records and
browser-local form are historical; the working app is in `web/` and `crucible/`.

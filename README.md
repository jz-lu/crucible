# Crucible

Finding and attacking new crypto in the AI age.

A working local foundation for shared-secret recovery challenges. The app has
accounts, durable records, a server-owned hourly deadline, and exact answer
verification. The current interface focuses on numerical results; the research
records and API remain available for future work. Source is shared for collaboration.
The hosted service has not been deployed and is not ready for public operation.

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

1. Choose a KEM from the catalogue. Its leaderboard opens first, at the next
   not-yet-recovered size. Click a nearby size, drag the slider, or use Jump to for any supported
   integer. A dot marks the next unsolved target; the return button takes you
   there and hides while it is selected. Earlier sizes are shaded light blue.
2. Open Challenge and request your instance. The boxed timer shows the next UTC
   round boundary; requesting an instance later leaves less time.
3. Download the JSON contract: public key, encapsulation, exact parameters,
   encoding, KDF, release, correctness argument, and deadline.
4. Investigate with your own tools. The small teaching examples can be solved with
   the matching public-input demonstration script:

   ```sh
   python3 scripts/solve_teaching.py /path/to/dh-challenge.json
   python3 scripts/solve_rsa.py /path/to/rsa-challenge.json
   ```

   Use only the script matching the downloaded scheme. Both examples refuse sizes
   above 36 to bound their basic algorithms' cost; larger inputs are available for
   participants' own methods. They print a candidate key and do not submit it.
5. Submit the full 32-byte key, method/pseudocode, and compute declaration. You can
   enter these manually or import a `crucible-recovery-v1` JSON result file of at
   most 128 KiB. Import fills editable fields for review; it never submits an answer.
   A well-formed answer is final. Immutable methods can be reused in later rounds.
6. A matching answer qualifies for that size and smaller-size boards in the same
   release, subject to the explicit release-compatibility rules. Each board keeps
   the first ten distinct accounts by their earliest qualifying receipt. Inherited
   credit is labelled "via size N"; click it to read the actual recovery's method
   and compute details. The server verifies the key and receipt deadline; method
   and compute remain contributor-reported.

The catalogue's First recovery column identifies the earliest successful account
at the displayed largest recovered size. It is not a count or universal security
ranking. Within a profile, Time to submit measures instance issue to answer receipt,
including delays; the participant's attack-time estimate is reported separately.
Scheme specifics gives a compact construction/parameter summary. Protocol retains
the full contract. Public inputs remain in the download; the page omits the raw
input disclosure and opening pitch. The footer contains only the blue flame-and-key logo
and Crucible name.

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

## Instance sizes and the included KEMs

The common selector is an integer **instance size**, `n`. Each scheme and release
maps supported values to exact registered parameters. Equal values across schemes
do not imply comparable strength, security bits, or attack cost.

| Catalogue name | Scheme / current release | Supported sizes | Meaning of `n` |
| --- | --- | --- | --- |
| Finite-field Diffie–Hellman | `teaching-dh-kem` / 3 | Every integer 20–128 | Subgroup order bit length |
| RSA over an integer modulus | `rsa-kem` / 1 | Every integer 20–128 | Modulus bit length |

Both output 32-byte shared secrets independently of `n`. They are teaching
constructions, unsuitable for protecting information. The RSA example uses a
custom transcript-bound SHA-256 derivation; it is not an implementation claiming
conformance to standardized RSA-KEM.

Manifests and downloads preserve `instance_size`, exact parameters, encodings,
source hash, release, and correctness evidence. Automation can select a challenge
with `{"scheme_id":"teaching-dh-kem","instance_size":21}` or the corresponding
`profile_id`; send exactly one selection form. The selected protocol supplies its
own source and example-solver links.

Honest encapsulation/decapsulation agreement is perfect under the registered
arithmetic and encodings. DH's larger fixed groups have exact primality
certificates; RSA's factors are checked within the deterministic primality-test
range. Implementation tests check executable behavior. None of this certifies
confidentiality, chosen-ciphertext resistance, or production security. Independent
admission review remains pending. Correctness mismatches and adapter exceptions
record an incident and pause new issuance; there is no silent retry-until-success.

DH release 3 extends the ladder to 128 while retaining the same construction at
sizes 20–36. Release 2 is archived unchanged with its original 20–36 range; release
1 retains its five original profiles. Source hashes and historical receipts stay
pinned to their original releases. The registry explicitly permits current DH
progress/boards to include compatible release-2 recoveries at sizes 20–36 only;
those receipts retain their actual source and version. Release 1 remains separate.
This compatibility is registered for that exact subset, not inferred across
arbitrary releases or schemes. Archived sources and exact-version records remain
available through the API. Findings about registered older releases remain revisable.

## Development and verification

```sh
.venv/bin/python -m pytest -q
node tests/test_frontend.cjs
node tests/test_recovery_upload.cjs
node tests/test_math.cjs
.venv/bin/alembic check
.venv/bin/python -m crucible.manage cleanup
```

Tests cover recovery, auth and ownership, CSRF, input privacy, concurrent
issue/submit, idempotency, completed-body receipt timing, exact hourly boundaries,
correctness incidents, release compatibility, ranking, research revisions,
navigation, and result-file import. Cleanup removes expired sessions and rate
counters, retaining research records. The current build passes 158 backend tests,
31 navigation regression groups, and 13 upload regression groups. Isolated Chrome
checks cover both KEMs from public-input solving through file review and accepted
submission, including record details, size controls, and 320px/390px layouts.
These checks validate the local workflow, not the security of the teaching KEMs.
The verified build is running locally on port 8000; its serving update preserved
existing account and recovery records. See the running work list for remaining work.

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
- [Contributing](CONTRIBUTING.md)

`prototype/` preserves the earlier static draft. Its illustrative records and
browser-local form are historical; the working app is in `web/` and `crucible/`.

# Crucible / towards-new-crypto

Finding and attacking new crypto in the AI age.

A working local foundation for shared-secret recovery challenges and inspectable
research findings. The app has real accounts, durable records, a server-owned
hourly deadline, and exact answer verification. Source is shared for collaboration;
the hosted service has not been deployed and is not ready for public operation.

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

## Try the complete experiment

1. Choose Teaching DH-KEM and an instance size, then request your instance.
2. Download its JSON contract: public key, encapsulation, exact parameters,
   encoding, KDF, version, correctness argument, and deadline.
3. Investigate with your own tools. For an end-to-end demonstration, the supplied
   public-input solver works against this deliberately weak teaching KEM:

   ```sh
   python3 scripts/solve_teaching.py /path/to/downloaded-challenge.json
   ```

4. Submit the full 32-byte key, method/pseudocode, and compute declaration.
   A well-formed answer is final. Immutable methods can be reused in later rounds.
5. A matching answer qualifies for the profile's recovery board. The server
   verifies the key and receipt deadline; method and compute remain reported.

Rounds are global UTC hours. **They end on the hour, even when you join late.**
Each account receives one independent instance per exact profile/version/round.
Reloading returns the same instance. A complete submission must reach the server
strictly before the deadline. Identical retries return the original receipt,
including after the deadline. The hour is a response window, not a compute cap.

## Research beyond the leaderboard

The Research workspace browses schemes and underlying problems. Findings can
contain theoretical arguments, experiments, partial progress, or documented
unsuccessful investigations, with exact targets, conditions, supporting text and
optional paper/code links. Authors can append revisions; earlier versions remain.
Follow-ups link earlier contributions without rewriting their attribution.

**Routine numerical runs do not automatically create findings.** A deliberate
finding can cite an owned verified recovery as supporting evidence. The finding
remains unreviewed: checking one key does not verify its broader argument, claimed
method, compute, or novelty. There is no universal theoretical ranking, security
score, or inference that an expired challenge is a failed investigation.

Catalogues are curated in code. Arbitrary uploaded code is never executed.

## Instance sizes and the included KEM

The common selection is an integer **instance size**, `n`. Each scheme and release
maps its supported values of `n` to exact registered parameters. A future KEM may
support only selected integers or map `n` to a parameter vector. The same `n` does
not imply comparable strength or attack cost across schemes.

`crucible/kem.py` implements Teaching DH-KEM with 17 sizes: every integer from 20
through 36. Here, `n` is the subgroup order's bit length; for example, size 21 maps
to profile `dh-21` and its fixed group parameters. The shared-secret output is
32 bytes at every size, independently of `n`. The construction is deliberately
attackable and unsuitable for protecting information.

Scheme metadata explains its `size_parameter`. Manifests and public challenge
contracts retain `instance_size` together with the full parameters, encodings,
and release identity. Automation may request a challenge using either
`{"scheme_id":"teaching-dh-kem","instance_size":21}` or the existing
`{"profile_id":"dh-21"}` selector; send exactly one selection form.

Every profile includes canonical encodings, exact randomness, a source hash, and
a perfect-correctness argument. Known-answer, boundary, validation, and randomized
round-trip tests check executable behavior. Neither these tests nor the algebraic
argument certify confidentiality. Independent admission review is pending; the
profiles belong only to the teaching lane. A correctness mismatch or adapter
exception records an incident and pauses new issuance for that profile.
There is no silent regenerate-until-success loop.

Adapter release 2 expands the parameter ladder. The original five profiles and
KDF domain remain unchanged; release 1 is archived byte for byte. Source hashes
are pinned to releases, and previous challenges, receipts, and research histories
remain intact. The example solver supports both releases. The board defaults to
the current release; request `scheme_version=1` on the leaderboard API to inspect
earlier records. `/api/protocol/source?version=1` downloads the archived source.
Findings about earlier registered releases remain revisable.

## Development and verification

```sh
.venv/bin/python -m pytest -q
.venv/bin/alembic check
.venv/bin/python -m crucible.manage cleanup
```

Tests cover recovery, auth and ownership, CSRF, input privacy, concurrent
issue/submit, idempotency, completed-body receipt timing, exact hourly boundaries,
correctness incidents, ranking, and research revisions. Cleanup removes expired
sessions and rate counters, retaining research records.

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
- [Contributing](CONTRIBUTING.md)

`prototype/` preserves the earlier static draft. Its illustrative records and
browser-local form are historical; the working app is in `web/` and `crucible/`.

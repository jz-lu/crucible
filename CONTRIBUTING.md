# Contributing

Crucible is an invitation-only research beta. Development uses a separate local
database; code releases must preserve the hosted beta's accounts and research.

## Branches and releases

Keep `main` tested and ready for release. Start unfinished work on a short-lived
branch, open a pull request, and merge after the `CI passed` check passes. There
is no separate deployment-ready branch in the normal workflow. Repository owners
should require that check on `main` and disable force pushes and branch deletion.

Merging does not deploy the website. The intentional **Release beta** GitHub
Actions workflow builds and tests an image from a commit on `main`, then publishes
that exact image and its immutable tag, then promotes the verified image to the
`beta` channel. The existing DigitalOcean service watches that channel through
Autodeploy. Ordinary releases need no environment-variable edits or manual tag
selection; the hosted database and configuration stay in place. Verify the live
source revision after deployment. Merging a PR alone never promotes the channel.
See [the release instructions](docs/releasing-beta.txt) for setup, verification,
and rollback. The release checks preserve reviewed native artifacts; changing a
KEM implementation, runtime dependency, or database schema requires the separate
review and migration process described there.

## Start locally

Use Python 3.11 or newer:

```sh
git clone https://github.com/jz-lu/crucible.git
cd crucible
./run.sh
```

Open http://127.0.0.1:8000. The launcher installs dependencies on the first run,
applies migrations, and starts a loopback server. Stop with Ctrl+C. Configuration
comes from process environment; `.env.example` lists options and is not loaded
automatically. Keep `.local/`, credentials, database files, and server keys private.

## Checks

After local setup:

```sh
.venv/bin/python -m pytest -q
.venv/bin/alembic check
git diff --check
```

Run relevant checks for your changes and describe their results. Source CI covers
the supported Python versions, fresh migrations, portable backend tests, all
standalone JavaScript regressions, and a real Chromium browser suite. See
[browser checks](docs/browser-checks.txt) for the disposable local setup and its
verification boundaries. Pull requests run these checks; feature branches do
not duplicate them with a second push-triggered run. The release workflow also tests the image with
its reviewed Linux native runtime. The critical-workflow gate separately covers
research, publication, permissions, ratings and scoped agents with disposable
SQLite and a portable test catalogue. Its exact suite list is in
`tests/run_critical_workflows.py`; skipped checks fail that gate. A dedicated
PostgreSQL 17 job checks metadata/scoring query parity. These checks do not imply
complete native coverage or PostgreSQL lifecycle-race verification.
Add a reviewed migration for schema changes.
Browser changes should be checked at desktop and mobile widths, including errors,
empty states, keyboard use, and the affected interaction. Measure both first visits
and repeat navigation with network latency; a fast local server alone can hide
unnecessary request waterfalls.

## Repository map

- `crucible/`: API, accounts, persistence, KEM adapters, and research records.
- `web/`: the working interface, with no JavaScript build step.
- `migrations/`: versioned database changes.
- `tests/`: numerical, correctness, account, timing, and research checks.
- `scripts/solve_teaching.py`: public-input solver for the teaching KEM.
- `docs/`: architecture, current decisions, and proposed admission policy.
- `prototype/`: historical static draft, not the working application.

## Preserve evidence and scope

Keep archived adapters byte for byte and use a new release when changing a pinned
implementation. Preserve exact parameters, encodings, KDF behavior, randomness,
correctness claims, and source provenance. Existing records must remain interpretable
and old receipts must not be relabeled as results for a newer release.

For each scheme and release, define a natural progression of exact registered
parameter configurations. Stages are positions within that progression; equal
stage numbers across schemes do not establish equal security. Preserve existing
API identifiers and parameter encodings, including legacy `instance_size` fields.
Do not interpolate unregistered parameter sets or relabel historical records.

The teaching KEM is deliberately weak. Adding a research candidate requires a
reviewed specification, adapter, exact profiles, correctness evidence, conformance
checks, and a failure policy. Do not add a route that executes unreviewed uploads.

Preserve the distinction between a verified recovered key, a contributor's reported
method or compute, and an unreviewed research finding. Numerical runs do not create
findings automatically. Favor focused changes that keep the interface clear, and
record consequential design choices and remaining limitations for review.

# Contributing

Crucible is a local development foundation for inspectable cryptographic research.
The source is shared for collaboration; the service is not ready for public hosting.

## Start locally

Use Python 3.11 or newer:

```sh
git clone https://github.com/jz-lu/towards-new-crypto.git
cd towards-new-crypto
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

Run relevant checks for your changes and describe their results. Add a migration
for schema changes. CI covers Python 3.11 and 3.14 with SQLite; PostgreSQL integration
still needs validation. Browser changes should be checked at desktop and mobile
widths, including errors, empty states, keyboard use, and the affected interaction.

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

For each scheme and release, define how the common integer instance size `n`
maps to exact registered parameters, including its supported values and
`size_parameter` description. A KEM may support only selected integers or map
`n` to a parameter vector. Preserve `instance_size` and full parameters in its
contracts. Do not interpret equal sizes across schemes as equal strength, equate
size with output-key length, or interpolate unregistered parameter sets.

The teaching KEM is deliberately weak. Adding a research candidate requires a
reviewed specification, adapter, exact profiles, correctness evidence, conformance
checks, and a failure policy. Do not add a route that executes unreviewed uploads.

Preserve the distinction between a verified recovered key, a contributor's reported
method or compute, and an unreviewed research finding. Numerical runs do not create
findings automatically. Favor focused changes that keep the interface clear, and
record consequential design choices and remaining limitations for review.

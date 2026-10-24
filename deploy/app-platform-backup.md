# App Platform backup job candidate

Run `python deploy/backup-app-platform.py` as a separate **nonroot** scheduled job,
using the same immutable release as the beta website. The image must contain this
adapter, `deploy/backup-beta.py`, SQLAlchemy/psycopg, `/usr/bin/restic`, and a
`/usr/bin/pg_dump` compatible with the server. This is a prepared adapter, not a
provisioned schedule or proof that the provider's scheduling/secret delivery works.
The existing Droplet root service and local file lock are unchanged.

The job only creates encrypted snapshots. It does not initialize a repository,
prune, restore, migrate, admit implementations, or import/start the application.
Configure one explicit HTTPS S3 repository that the operator has already prepared.

## Configuration

All secrets below must come from runtime secret injection, never image build
arguments, source control, command-line arguments, or logs. Set
`CRUCIBLE_BACKUP_JOB=app-platform` and supply:

- `CRUCIBLE_BACKUP_CONFIGURATION_JSON`: the declaration below.
- `CRUCIBLE_BACKUP_DATABASE_URL`: separate backup role, exact same database host,
  port and database as the web service, with explicit `sslmode=verify-full`.
  No `hostaddr`, service indirection or arbitrary connection options are accepted.
- `CRUCIBLE_BACKUP_CA_PEM`: the database provider CA, as multiline PEM.
- `CRUCIBLE_BACKUP_RESTIC_PASSWORD`: the existing repository encryption password.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and, where applicable,
  `AWS_SESSION_TOKEN` and `AWS_DEFAULT_REGION`.
- `RESTIC_REPOSITORY`: explicit `s3:https://…` repository.
- `CRUCIBLE_BACKUP_RELEASE_MANIFEST`: absolute path to the immutable image's
  `crucible-host-release-v1` release manifest, readable by the job and owned by
  root or the job user, without group/world write permission.

Use a backup database role with the required CONNECT, schema USAGE and SELECT
permissions, not the application account or a superuser. Ensure grants include
tables introduced by future migrations. The job uses read-only transactions;
access to the dump still exposes all private beta data, so protect its credentials.

The configuration declaration has exactly four keys:

```json
{
  "format": "crucible-app-platform-backup-config-v1",
  "release_id": "the-actual-deployed-release-id",
  "runtime_env": {"NAME": "exact deployed value"},
  "runtime_env_sha256": "SHA-256 of runtime_bytes(runtime_env)"
}
```

The operator must generate this from the **same configuration/secret version**
used for that web release. Include every explicitly configured application value,
including the original `CRUCIBLE_SECRET`, database URL, database CA PEM, reviewer
and administrator IDs, release allowlist, and enabled mail/GitHub settings and
credentials. Values are strings; optional empty strings and multiline PEM are
preserved. Defaults, when deliberately omitted, belong to the exact pinned release.
Do not copy the backup role or repository credentials into the web configuration.

`runtime_bytes(mapping)` in the adapter is the deterministic serializer used to
compute the digest: sort variable names, encode every value as a JSON string
with `ensure_ascii=False`, write `NAME=<JSON string>` plus a newline, then UTF-8
encode. The digest covers these exact bytes. The adapter checks that digest,
production/private-beta identity, original-secret shape, database target and
release-ID agreement. These checks detect mismatches; they cannot prove that an
operator declaration is complete or matches the provider's actual live settings.
Verify that correspondence before enabling the schedule and after secret changes.

## Concurrency and completion

A dedicated PostgreSQL session holds the fixed `crucible-beta-backup-v1` advisory
lock for the paired configuration and database snapshots, through receipt upload.
All scheduled jobs must use this adapter and target database. A competing job
fails immediately. The connection uses `NullPool`, AUTOCOMMIT, verified TLS, a
five-second connection/statement timeout, and a one-second lock timeout. It is
not an idle transaction. Closing the physical connection releases the lock.

Immediately before the completion receipt, the adapter verifies the same DBAPI
connection, backend PID, and exact advisory lock. A lost/invalidated connection
cannot reconnect silently. A failure leaves an incomplete set without a new
completion receipt. There is no distributed transaction between PostgreSQL and
object storage: the lock can still disappear during the final receipt upload.
A completion receipt records successful snapshot operations, not a restore test.

Runtime credentials exist only in a fresh mode-0700 temporary directory with
mode-0600 files and are removed on ordinary success or failure. The job container
must remain ephemeral; forced container termination cannot run Python cleanup.
Unrelated application environment variables are not passed to subprocesses.

PostgreSQL documents the [session advisory lock semantics](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)
and the [lock identity fields](https://www.postgresql.org/docs/current/view-pg-locks.html)
used by the ownership check.

## Recovery format

The existing `crucible-recovery-set-v1` format remains unchanged: encrypted
configuration snapshot (`runtime.env` and `RELEASE.json`), custom-format
`database.dump`, and a completion receipt identifying both snapshots and exact
configuration-file SHA-256 values. Use the paths recorded in the receipt to find
configuration files, because their original temporary directory is gone.

Restore into a separate, nonpublic environment, validate receipt hashes, and
preserve the original server secret together with its database. The generated
`runtime.env` is a recovery serialization, **not a shell script**. For each line,
split once at the first `=`, require a valid unique variable name, and JSON-decode
the remaining value to a string. Populate an environment mapping with those
strings. Never `source`, `eval`, expand or interpolate the file. Multiline values
are represented by JSON escapes on one physical line. Any absolute file paths
must be restored or explicitly remapped; the CA PEM is preserved when included
in the declared runtime configuration.

The isolated Linux fixture has verified nonroot encrypted backup and exact
database/configuration recovery, independent-connection contention, and server
connection loss before completion. It uses a temporary PostgreSQL cluster,
synthetic accounts/notes, and a local encrypted repository. Actual provider job
scheduling, production permissions/connectivity, remote object storage,
monitoring/retention and an end-to-end remote restore still need validation.

Run ordinary tests with `pytest tests/test_app_platform_backup.py`. The optional
Linux fixture is `CRUCIBLE_APP_BACKUP_FIXTURE=1 pytest
tests/test_app_platform_backup_fixture.py`; it requires an existing nonroot Linux
environment with PostgreSQL server/client binaries, OpenSSL and restic. It creates
and stops its own localhost PostgreSQL cluster and never uses an existing server.

Temporary old-UI schema bridge for the November display launch
============================================================

This release is based on live/main f9719ab5d3a4a99d7040e7d2d83ebc83608dc946.
It retains that site's UI, scientific records, model settings, routes and
ordinary invitation behavior. It can read the provenance of invitations issued
by the display launch's one-off signup campaign. It cannot issue a campaign or
expose the new signup/statement-review UI. No environment compatibility switch,
provider run-command override, automatic migration or process-wide schema cache
is introduced.

Startup compatibility (all other preflight gates still apply)
-------------------------------------------------------------
                               0031       0032       0033       other/partial
Original live/main f9719ab      accepts    refuses    refuses    refuses
This bridge                    accepts    accepts    accepts    refuses
Final display candidate        refuses    refuses    accepts    refuses

The bridge's active Alembic and ORM head stays exactly 0031. Preflight rejects
any different bridge migration source head. On database 0031 all five future
tables must be absent; 0032 must have exactly the two signup tables and no trial
tables; 0033 must have all five. Expanded startup also checks exact columns,
nullability, primary keys and the signup uniqueness/foreign-key boundaries.
Unversioned databases never silently become a 0031 fallback. Test-only metadata
fixtures explicitly create their own 0031 marker; application code never does.

The retained image entrypoint runs scripts.beta_preflight --runtime --database
before execing the hosted factory. The bridge changes that schema check only;
TLS, configured membership, native registry and saved implementation checks
remain mandatory. The hosted factory repeats the read-only schema check before
creating the app/worker, including when invoked without that entrypoint.
Passing source tests is not proof that the built image starts on the platform.

Each invitation-source lookup reads the head plus table presence in one fresh
SQL statement using the caller's transaction. A connection that saw 0031 before
expansion can see 0032/0033 on its next transaction without restart. There is no
catch-undefined-table-and-admit fallback. Unknown/incomplete schema causes the
invitation workflow to fail closed with a bounded 503 message.

Campaign authority during overlap and rollback
---------------------------------------------
The three backported checks are:
* Lock the parent campaign before locking its recipient-bound invitation, using
  the same order as the candidate issuer/closer.
* Require a source parent to exist, remain unrevoked, and be strictly before its
  expiry whenever validating an unused invitation. This covers registration,
  GitHub signup, mailbox-code sends and redemption through their existing shared
  validator. Closing a campaign does not alter an ordinary operator invitation.
* A campaign-derived invitation cannot restore revoked membership even if it
  was issued after revocation. A fresh explicit operator invitation retains the
  existing regrant rule.

Already admitted accounts survive campaign expiry/closure. A replay of a
previous success can return its existing receipt only under the original replay
checks; it cannot revive revoked membership or create another grant.

Operators can read or permanently close the existing one-off campaign on either
expanded schema, without revealing/creating/extending its secret link:

  python -B -m scripts.beta_access launch-link-status
  python -B -m scripts.beta_access close-launch-link --reason 'Reviewed reason'

These commands use the same fixed campaign identity as the final candidate.
Closing is idempotent and records one signup_link_revoked event. On schema0031,
or before campaign creation, they refuse without creating anything. The bridge
has no launch-link creation command. There is no extra provider setting to
remember when closing or rolling back.

Reviewed expansion and recovery boundary
---------------------------------------
expansion.json pins the exact original 0032/0033 migration bytes at candidate
618c707deeea94ee933ad799495111d575aa616f. The copies in this directory are evidence
for disposable database qualification, not files in the active migration tree.
No existing table is altered by either migration. Local/CI tests copy the bridge
migration tree into their own temporary directory and append these exact files.
Production expansion must use the reviewed final candidate, its migrator/owner
role, and its normal exact schema head; do not copy test migrations onto a host.

Required rollout order, only after both actual images qualify:
1. Verify a recent complete backup and a tested restore; record the immutable
   original, bridge and final image digests plus their source revisions.
2. Deploy the bridge on 0031, retaining the current site and settings. Verify
   real entrypoint/startup, readiness, existing session and ordinary invitations.
3. Apply the exact additive 0032 and 0033 migrations with the owner role and
   bounded lock/statement timeouts while the bridge remains available. Runtime
   must retain DML only; backup retains SELECT on the new tables through reviewed
   default privileges. Check original records/provenance, grants and head.
4. Qualify/restart the bridge on 0033 as the rollback image; then deploy the final
   candidate, which must refuse 0031/0032 and require 0033. Verify real signup,
   mailbox proof, existing account access and early campaign closure before
   sharing the one-week link. Source-only tests do not authorize those actions.

If the final UI/app must be rolled back, retain database0033 and deploy the
qualified bridge image. Pending campaign invitations still consult their source
parent, so expiry and early closure remain effective. Use the closure command if
the campaign should stop; it is not necessary to revoke admitted members. The
bridge can service existing recipient invitations but does not offer campaign
issuance. Continue forward with a corrected final image after verification.

Do not roll the database back or deploy unmodified f9719ab after campaign data
exists. The migration downgrades refuse populated source/trial history. Seven
days of expiry is not a schema/data deletion deadline: provenance is still
required to keep revocation effective. Returning to 0031 requires a separately
reviewed recovery/data plan; restoring a backup would lose later writes and is
not a routine application rollback. beta_transfer retains its exact-head guard;
do not use the bridge's 0031 transfer utility to manipulate an expanded DB.

Verification limits
-------------------
Synthetic SQLite tests cover the full 0031→0032/0033 path, unchanged old records,
ordinary and source-derived invitations, expiry, closure, consumed-code replay,
revoked memberships, operator idempotency, malformed schema, factory refusal and
fresh 0031 Alembic/ORM parity. PG17 tests use only the established fixed disposable
service, unique synthetic roles/schema, real row locks, migration grants, and a
parent/child lock-order race. The existing PG suite also remains in CI.

Required release evidence still includes the actual retained-entrypoint image on
0031/0032/0033, final-image refusal before0033, process overlap, ready-state probes,
runtime and backup grants, existing data comparison, pending-code early closure,
post-migration rollback, and live ingress/SMTP verification. No zero-downtime
claim is made before those actual-image and rollout checks succeed.

Portable focused checks:
  python -B -m pytest -q tests/test_schema_bridge.py tests/test_beta_invitations.py
    tests/test_beta_preflight.py tests/test_beta_operator.py tests/test_github_auth.py
    tests/test_app_platform.py tests/test_release_pipeline.py

Existing disposable PG17 service (never use a hosted DSN):
  CRUCIBLE_METADATA_POSTGRES=1 python -B -m pytest -q
    tests/test_schema_bridge_postgresql.py tests/test_researcher_profiles_postgresql.py

The command lines above wrap for readability; supply each invocation on one line.

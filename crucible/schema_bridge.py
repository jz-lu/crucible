"""Release-owned 0031→0033 compatibility; never migrates or creates schema.

This module belongs only to the temporary old-UI bridge release. Its own
Alembic/ORM head remains 0031. The expansion sources and their exact hashes are
recorded in docs/schema-bridge/expansion.json; they are not active migrations.
There is no operator toggle or process-cached pre-migration mode.
"""
from sqlalchemy import Float, String, column, inspect, select, table, text
from sqlalchemy.exc import SQLAlchemyError


BASELINE_HEAD = "0031"
SUPPORTED_HEADS = frozenset({"0031", "0032", "0033"})
LAUNCH_SIGNUP_ID = "172f41a6-3691-49ef-b8d4-8d6507b974f0"
SIGNUP_TABLES = ("beta_signup_links", "beta_signup_invitations")
TRIAL_TABLES = ("local_statement_updates", "local_statement_current", "local_statement_adoptions")
EXPANSION_TABLES = SIGNUP_TABLES + TRIAL_TABLES

# Core-only projections: do not register future tables on Base.metadata or
# change fresh bridge migrations/ORM parity. All values remain SQL parameters.
links = table("beta_signup_links", column("id", String(36)),
              column("expires_at", Float), column("revoked_at", Float))
sources = table("beta_signup_invitations", column("invitation_id", String(36)),
                column("signup_link_id", String(36)))


class SchemaBridgeError(ValueError):
    """Fixed, non-secret message suitable for preflight/operator reports."""


def _error():
    return SchemaBridgeError("This bridge requires intact schema 0031, 0032 or 0033.")


def schema_head(connection, *, full=False):
    """Observe the version and all five table-presence flags in one snapshot.

    Each invocation reads the caller's current transaction. In particular, a
    pooled connection/process that previously saw 0031 must see signup sources
    after expansion. Unknown, unversioned or partially expanded schemas fail
    closed; an undefined-table exception is never treated as 0031.
    """
    dialect = connection.dialect.name
    if dialect == "postgresql":
        flags = ["to_regclass(quote_ident(current_schema()) || '." + name + "') IS NOT NULL"
                 for name in EXPANSION_TABLES]
    elif dialect == "sqlite":
        flags = ["EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='" + name + "')"
                 for name in EXPANSION_TABLES]
    else:
        raise _error()
    try:
        rows = connection.execute(text("SELECT version_num, " + ", ".join(flags) + " FROM alembic_version")).all()
    except SQLAlchemyError:
        raise _error() from None
    if len(rows) != 1 or rows[0][0] not in SUPPORTED_HEADS:
        raise _error()
    head, *present = rows[0]
    expected = ([False] * 5 if head == "0031" else
                [True, True, False, False, False] if head == "0032" else [True] * 5)
    if [bool(value) for value in present] != expected:
        raise _error()
    if full and head != "0031":
        _check_expanded_shape(connection, head)
    return head


def _check_expanded_shape(connection, head):
    """Bounded startup guard for the exact additive tables used by this bridge."""
    inspector = inspect(connection)
    expected = {
        "beta_signup_links": ({"id", "token_hash", "label", "created_by", "created_at", "expires_at", "revoked_at"},
                              {"created_by", "revoked_at"}, ["id"]),
        "beta_signup_invitations": ({"invitation_id", "signup_link_id", "email"}, set(), ["invitation_id"]),
    }
    if head == "0033":
        expected.update({
            "local_statement_updates": ({"id", "author_id", "created_at", "packet", "packet_sha256", "idempotency_key", "assessment"}, {"assessment"}, ["id"]),
            "local_statement_current": ({"target_id", "adoption_id"}, {"adoption_id"}, ["target_id"]),
            "local_statement_adoptions": ({"id", "update_id", "target_id", "reviewer_id", "created_at", "previous_id"}, {"previous_id"}, ["id"]),
        })
    for name, (names, nullable, primary) in expected.items():
        columns = inspector.get_columns(name)
        if ({item["name"] for item in columns} != names
                or {item["name"] for item in columns if item["nullable"]} != nullable
                or inspector.get_pk_constraint(name)["constrained_columns"] != primary):
            raise _error()
    unique_links = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("beta_signup_links")}
    unique_sources = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("beta_signup_invitations")}
    source_fks = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
                  for item in inspector.get_foreign_keys("beta_signup_invitations")}
    if (("token_hash",) not in unique_links or ("signup_link_id", "email") not in unique_sources
            or source_fks != {(("invitation_id",), "beta_invitations", ("id",)),
                              (("signup_link_id",), "beta_signup_links", ("id",))}):
        raise _error()


def invitation_source(session, invitation_id):
    if schema_head(session.connection()) == "0031":
        return None
    return session.scalar(select(sources.c.signup_link_id).where(sources.c.invitation_id == invitation_id))


def signup_parent(session, identifier, *, lock=False):
    query = select(links).where(links.c.id == identifier)
    if lock:
        query = query.with_for_update()
    return session.execute(query).mappings().one_or_none()


def signup_status(link, now):
    if link is None:
        return "missing"
    if link["revoked_at"] is not None:
        return "revoked"
    return "available" if link["expires_at"] > now else "expired"


def launch_link_status(session, now, *, close_reason=None):
    """Read/close the reviewed one-off campaign, never issue or extend a link."""
    if schema_head(session.connection(), full=True) == "0031":
        raise ValueError("The launch campaign is not installed on schema 0031.")
    link = signup_parent(session, LAUNCH_SIGNUP_ID, lock=close_reason is not None)
    if link is None:
        raise ValueError("The launch campaign has not been created.")
    if close_reason is not None:
        if not close_reason.strip() or len(close_reason) > 500:
            raise ValueError("Supply a reason between 1 and 500 characters.")
        if link["revoked_at"] is None:
            # Match candidate lock order and event semantics. Existing members
            # keep their accounts; every pending child consults this parent.
            from .beta_access import _event
            session.execute(links.update().where(links.c.id == LAUNCH_SIGNUP_ID).values(revoked_at=now))
            _event(session, None, None, "signup_link_revoked", now,
                   signup_link_id=LAUNCH_SIGNUP_ID, reason=close_reason)
            link = signup_parent(session, LAUNCH_SIGNUP_ID)
    return {"id": LAUNCH_SIGNUP_ID, "status": signup_status(link, now), "expires_at": link["expires_at"]}

"""Index exact note citations without changing authored revision history."""
import re
from urllib.parse import parse_qs, urlsplit

from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _pins(content):
    # Frozen v1 parser: migrations must not depend on later application rules.
    values = content.get("references")
    if not isinstance(values, list):
        values = [] if values is None else [values]
    for position, value in enumerate(values):
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            continue
        try:
            url = urlsplit(value["url"])
            if url.scheme or url.netloc or url.path not in ("", "/") or url.query or not url.fragment.startswith("notebook?"):
                continue
            fields = parse_qs(url.fragment[len("notebook?"):], strict_parsing=True, max_num_fields=3)
            if set(fields) != {"note", "revision", "sha256"} or any(len(items) != 1 for items in fields.values()):
                continue
            identifier, number, digest = (fields[key][0] for key in ("note", "revision", "sha256"))
            if not 1 <= len(identifier) <= 100 or identifier != identifier.strip() or any(ord(c) < 32 for c in identifier):
                continue
            if not re.fullmatch(r"[1-9][0-9]?|100", number) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
        except ValueError:
            continue
        relation = value.get("relation")
        yield {"position": position, "target_note_id": identifier, "target_revision": int(number),
               "target_sha256": digest, "relation": relation if isinstance(relation, str) and relation in
               {"cites", "builds_on", "supports", "challenges", "reproduces", "responds_to"} else "cites",
               "scope": value.get("scope", "")[:10000] if isinstance(value.get("scope", ""), str) else ""}


def upgrade():
    table = op.create_table("notebook_references",
        sa.Column("source_revision_id", sa.String(36), sa.ForeignKey("notebook_revisions.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("target_note_id", sa.String(100), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("target_sha256", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False))
    op.create_index("ix_notebook_reference_target", "notebook_references", ["target_note_id", "target_revision", "target_sha256"])
    connection = op.get_bind()
    notes = sa.table("notebook_notes", sa.column("id", sa.String), sa.column("author_id", sa.String), sa.column("source_id", sa.String))
    revisions = sa.table("notebook_revisions", sa.column("id", sa.String), sa.column("note_id", sa.String), sa.column("content", sa.JSON))
    after = ""
    while True:
        batch = connection.execute(sa.select(revisions.c.id, revisions.c.content, notes.c.author_id)
            .join(notes, notes.c.id == revisions.c.note_id).where(revisions.c.id > after).order_by(revisions.c.id).limit(100)).all()
        if not batch:
            break
        for identifier, content, author in batch:
            rows = [{"source_revision_id": identifier, **pin} for pin in _pins(content)]
            if rows:
                mapping = dict(connection.execute(sa.select(notes.c.source_id, notes.c.id).where(
                    notes.c.author_id == author, notes.c.source_id.is_not(None))).all())
                for row in rows:
                    row["target_note_id"] = mapping.get(row["target_note_id"], row["target_note_id"])
                connection.execute(table.insert(), rows)
        after = batch[-1][0]


def downgrade():
    op.drop_table("notebook_references")

"""Permanent human-facing aliases for saved and bundled research notes.

Numbers identify notes, not revisions, and never grant access. Keep reservations
when a note is withdrawn or deleted so an old reference cannot change targets.
"""
import re

from sqlalchemy import Identity, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class NotebookNumber(Base):
    __tablename__ = "notebook_numbers"
    __table_args__ = (
        UniqueConstraint("note_id", name="uq_notebook_number_identity"),
        {"sqlite_autoincrement": True},
    )
    # PostgreSQL IDENTITY uses table INSERT privileges; unlike SERIAL, runtime
    # writers do not need USAGE on the sequence. Backups still need SELECT on
    # the owned sequence and must restore its state as well as the alias rows.
    # SQLite ignores Identity and uses its non-reusing AUTOINCREMENT allocator.
    number: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    # Curated identities do not live in notebook_notes, so this is not an FK.
    note_id: Mapped[str] = mapped_column(String(108), nullable=False)


def ensure(connection, identifiers):
    """Allocate missing aliases in the caller's transaction, allowing gaps.

    PostgreSQL's unique identity constraint resolves competing writers; after
    ON CONFLICT, the next READ COMMITTED statement sees the committed winner.
    SQLite callers acquire BEGIN IMMEDIATE before any reads. Numbers are never
    derived from a catalogue position, revision, or number of existing notes.
    """
    identifiers = sorted(set(identifiers))
    if not identifiers:
        return {}
    table = NotebookNumber.__table__
    query = select(table.c.note_id, table.c.number).where(table.c.note_id.in_(identifiers))
    numbers = dict(connection.execute(query).all())
    missing = [{"note_id": identifier} for identifier in identifiers if identifier not in numbers]
    if missing:
        insert = {"sqlite": sqlite_insert, "postgresql": pg_insert}[connection.dialect.name]
        inserted = connection.execute(insert(table).values(missing)
            .on_conflict_do_nothing(index_elements=["note_id"])
            .returning(table.c.note_id, table.c.number))
        numbers.update(inserted.all())
        # A concurrent writer may have won an alias after our initial read.
        unresolved = [identifier for identifier in identifiers if identifier not in numbers]
        if unresolved:
            numbers.update(connection.execute(select(table.c.note_id, table.c.number)
                .where(table.c.note_id.in_(unresolved))).all())
    return numbers


def search_number(value):
    """Only an explicit #number is a reference; ordinary numeric text still searches."""
    match = re.fullmatch(r"#([0-9]+)", value)
    if match is None:
        return None
    digits = match[1].lstrip("0")
    if not digits or len(digits) > 10:
        return -1
    number = int(digits)
    return number if number <= 2_147_483_647 else -1

"""Freeze browser-approved agent permissions for each connection request.

Revision ID: 0008
Revises: 0007

All existing requests retain the original six participant permissions, whether
pending, approved or already exchanged. Existing machine tokens are untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# This historical snapshot must not import the application's mutable SCOPES.
_LEGACY_JSON = '["challenges:read","challenges:write","methods:read","methods:write","submissions:read","submissions:write"]'


def upgrade():
    # A constant legacy default safely backfills existing rows on both SQLite
    # and PostgreSQL. New application requests explicitly snapshot their scopes.
    # Retaining this conservative DB default also makes old-code inserts safe
    # during a rolling upgrade; it can never grant newly introduced permissions.
    op.add_column("device_connections", sa.Column(
        "scopes", sa.JSON(), nullable=False, server_default=sa.text("'" + _LEGACY_JSON + "'")))


def downgrade():
    op.drop_column("device_connections", "scopes")

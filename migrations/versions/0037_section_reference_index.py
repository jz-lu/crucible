"""Index section citations without changing note content or existing locs."""
from alembic import op
import sqlalchemy as sa

revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None


def backfill(connection):
    from crucible.notebook_graph import references
    revisions = sa.table('notebook_revisions', sa.column('id', sa.String), sa.column('note_id', sa.String),
        sa.column('revision', sa.Integer), sa.column('content_sha256', sa.String), sa.column('content', sa.JSON))
    edges = sa.table('notebook_references', sa.column('source_revision_id', sa.String), sa.column('position', sa.Integer),
        sa.column('target_note_id', sa.String), sa.column('target_revision', sa.Integer), sa.column('target_sha256', sa.String),
        sa.column('target_anchor', sa.String), sa.column('relation', sa.String), sa.column('scope', sa.Text))
    notes = sa.table('notebook_notes', sa.column('id', sa.String), sa.column('author_id', sa.String), sa.column('source_id', sa.String))
    for row in connection.execute(sa.select(revisions)).mappings():
        if not isinstance(row['content'], dict) or 'body' not in row['content']:
            continue
        base = {key: row[key] for key in ('note_id', 'revision', 'content_sha256')}
        previous = {edge['position']: edge for edge in connection.execute(sa.select(edges)
            .where(edges.c.source_revision_id == row['id'])).mappings()}
        author = sa.select(notes.c.author_id).where(notes.c.id == row['note_id']).scalar_subquery()
        remapping = dict(connection.execute(sa.select(notes.c.source_id, notes.c.id)
            .where(notes.c.author_id == author, notes.c.source_id.is_not(None))).all())
        for item in references(row['content'], connection, base=base):
            # Keep original import remappings and edge identities intact.
            if item['position'] in previous:
                connection.execute(edges.update().where(edges.c.source_revision_id == row['id'], edges.c.position == item['position'])
                    .values(target_anchor=item.get('target_anchor')))
            else:
                item['target_note_id'] = remapping.get(item['target_note_id'], item['target_note_id'])
                connection.execute(edges.insert().values(source_revision_id=row['id'], **item))


def upgrade():
    op.add_column('notebook_references', sa.Column('target_anchor', sa.String(100), nullable=True))
    backfill(op.get_bind())


def downgrade():
    op.drop_column('notebook_references', 'target_anchor')

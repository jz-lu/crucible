"""The citation index is derived; upgrading never rewrites authored evidence."""
from alembic import command
from sqlalchemy import MetaData, select, text

from crucible.db import Database
from crucible.notebook import _hash
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def test_backfill_preserves_research_and_import_mapping_and_is_replayable(tmp_path, monkeypatch):
    config = migration_config(f"sqlite:///{tmp_path / 'references.db'}", tmp_path, monkeypatch)
    command.upgrade(config, '0036')
    db = Database(f"sqlite:///{tmp_path / 'references.db'}")
    try:
        original = MetaData(); original.reflect(bind=db.engine)
        original.remove(original.tables['alembic_version'])
        edges = original.tables['notebook_references']; original.remove(edges)
        revisions = original.tables['notebook_revisions']
        with db.engine.begin() as connection:
            ids, _ = seed_original(connection, original)
            link = '/#notebook?note=original%3Afixture&revision=1&sha256='+'a'*64+'&passage=p1-b-1-2'
            content = {'title':'Preserved source','body':'[An exact citation]('+link+')', 'references':[{'url':link}]}
            connection.execute(revisions.update().where(revisions.c.id == ids['revision']).values(content=content,content_sha256=_hash(content)))
            connection.execute(edges.insert().values(source_revision_id=ids['revision'],position=0,
                target_note_id=ids['note'],target_revision=1,target_sha256='a'*64,relation='cites',scope=''))
            before = snapshot(connection, original)
        command.upgrade(config, 'head'); command.check(config)
        for iteration in range(2):
            with db.engine.connect() as connection:
                assert snapshot(connection, original) == before
                rows = connection.execute(text('SELECT position,target_note_id,target_anchor FROM notebook_references ORDER BY position')).all()
                assert rows == [(0,ids['note'],'passage:p1-b-1-2'),(1,ids['note'],'passage:p1-b-1-2')]
                assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            if iteration == 0:
                command.downgrade(config,'0036'); command.upgrade(config,'head')
    finally:
        db.engine.dispose()

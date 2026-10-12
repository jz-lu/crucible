"""Small explicit administration entry point; no public admin or code-upload API."""
import argparse
import time
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from .config import ROOT, load_settings
from .db import Database, LoginSession, RateBucket


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "cleanup"])
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "migrate":
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
    else:
        database = Database(settings.database_url)
        with database.transaction(write=True) as session:
            session.execute(delete(LoginSession).where(LoginSession.expires_at <= time.time()))
            session.execute(delete(RateBucket).where(RateBucket.expires_at <= time.time()))
        print("Expired sessions and rate counters removed. Research records were retained.")


if __name__ == "__main__":
    main()

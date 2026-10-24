"""Small explicit administration entry point; no public admin or code-execution API."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID
from alembic import command
from alembic.config import Config
from sqlalchemy import case, delete, select
from .config import ROOT, load_settings
from .db import Database, LoginSession, RateBucket, User


def _latest_reviewers(target, fallback):
    """Read while holding the separate lock; never replace malformed policy."""
    try:
        document = json.loads(target.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return set(fallback)
    except (OSError, ValueError, UnicodeError):
        raise ValueError('The local reviewer configuration is unreadable or invalid; no changes were made.') from None
    try:
        if (not isinstance(document, dict) or set(document) != {'reviewer_ids'}
                or not isinstance(document['reviewer_ids'], list)
                or any(not isinstance(value, str) for value in document['reviewer_ids'])):
            raise ValueError
        return {str(UUID(value)) for value in document['reviewer_ids']}
    except (ValueError, AttributeError):
        raise ValueError('The local reviewer configuration must contain explicit account UUIDs; no changes were made.') from None


def configure_reviewer(settings, handle, *, grant):
    """Serialize and atomically replace local reviewer policy on macOS/Linux."""
    if settings.production or 'CRUCIBLE_REVIEWER_IDS' in os.environ:
        raise ValueError('Reviewer IDs are environment-managed. Update CRUCIBLE_REVIEWER_IDS explicitly instead.')
    database = Database(settings.database_url)
    try:
        with database.transaction() as session:
            user = session.scalar(select(User).where(User.handle == handle.strip().lower()))
            if user is None:
                raise ValueError('No existing account has that handle. Create the account before assigning reviewer access.')
            identifier, actual_handle = user.id, user.handle
    finally:
        database.engine.dispose()
    directory = Path(os.getenv('CRUCIBLE_DATA_DIR', str(ROOT / '.local')))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / 'reviewers.json'
    # Lock a stable separate inode: locking reviewers.json itself would stop
    # coordinating other processes after atomic replacement changes its inode.
    descriptor = os.open(directory / '.reviewers.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            reviewers = _latest_reviewers(target, settings.reviewer_ids)
            if grant:
                reviewers.add(identifier)
            else:
                reviewers.discard(identifier)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                                 prefix='.reviewers-', delete=False) as stream:
                    temporary = Path(stream.name)
                    os.chmod(temporary, 0o600)
                    json.dump({'reviewer_ids': sorted(reviewers)}, stream)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return actual_handle


def cleanup_expired(database, now=None):
    """Remove transient expired state while retaining accounts and credentials."""
    from .machine_auth import DeviceConnection, EXCHANGE_SECONDS
    from .github_auth import GitHubFlow
    from .password_recovery import PasswordReset
    now = time.time() if now is None else now
    device_deadline = case((DeviceConnection.approved_at.is_(None), DeviceConnection.expires_at),
                           else_=DeviceConnection.approved_at + EXCHANGE_SECONDS)
    with database.transaction(write=True) as session:
        session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
        session.execute(delete(RateBucket).where(RateBucket.expires_at <= now))
        session.execute(delete(DeviceConnection).where(device_deadline <= now))
        session.execute(delete(GitHubFlow).where(GitHubFlow.expires_at <= now))
        session.execute(delete(PasswordReset).where(PasswordReset.expires_at <= now))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "cleanup", "check-proposal", "grant-reviewer", "revoke-reviewer"])
    parser.add_argument("--proposal-id")
    parser.add_argument("--scheme-id")
    parser.add_argument("--version")
    parser.add_argument("--handle")
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "migrate":
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
    elif args.action in {"grant-reviewer", "revoke-reviewer"}:
        if not args.handle:
            parser.error("Reviewer configuration requires --handle for an existing account")
        try:
            handle = configure_reviewer(settings, args.handle, grant=args.action == 'grant-reviewer')
        except ValueError as error:
            parser.exit(1, str(error) + "\n")
        print(f"Reviewer access {'granted to' if args.action == 'grant-reviewer' else 'removed from'} {handle}. Restart the server to load the updated local configuration.")
    elif args.action == "check-proposal":
        if not all((args.proposal_id, args.scheme_id, args.version)):
            parser.error("check-proposal requires --proposal-id, --scheme-id, and --version")
        from .protocol_admission import check_admission
        database = Database(settings.database_url)
        try:
            with database.transaction() as session:
                result = check_admission(session, args.proposal_id, args.scheme_id, args.version)
            print(json.dumps(result, indent=2))
        except ValueError as error:
            parser.exit(1, str(error) + "\n")
        finally:
            database.engine.dispose()
    else:
        database = Database(settings.database_url)
        try:
            cleanup_expired(database)
        finally:
            database.engine.dispose()
        print("Expired sessions, rate counters and connection requests removed. Accounts, agent credentials and research records were retained.")


if __name__ == "__main__":
    main()

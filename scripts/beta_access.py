"""Trusted operator controls: python -m scripts.beta_access --help.

Run only on the service host with its explicit database/configuration. This is
not a participant API. It never resets accounts, passwords or contribution IDs.
"""
import argparse
import json
import sys
import time
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from crucible.beta_access import (AccountState, BetaInvitation, BetaMembership, BetaSignupInvitation,
                                 BetaSignupLink, create_invitation, create_signup_link, invalidate_invitation,
                                 invitation_status, revoke_signup_link, set_membership, set_suspension,
                                 signup_link_data)
from crucible.config import load_settings
from crucible.db import Database, User

# One launch window per database. Retrying cannot mint another campaign or
# extend/reopen the original one. Ordinary recipient invitations stay separate.
LAUNCH_SIGNUP_ID = "172f41a6-3691-49ef-b8d4-8d6507b974f0"


def launch_link(session, settings, now, expires_at=None):
    if expires_at is None:
        link = session.get(BetaSignupLink, LAUNCH_SIGNUP_ID)
        if link is None:
            raise ValueError("Create the launch link with --expires-at and an explicit timezone.")
    else:
        deadline = datetime.fromisoformat(expires_at)
        if deadline.tzinfo is None:
            raise ValueError("The signup deadline must include an explicit timezone.")
        link = create_signup_link(session, settings, "Crucible launch", now,
                                  request_id=LAUNCH_SIGNUP_ID, expires_at=deadline.timestamp())
        session.flush()
    result = signup_link_data(settings, link, now)
    if result["status"] != "available":
        result["url"] = None
    result["joined"] = session.scalar(select(func.count()).select_from(BetaSignupInvitation)
        .join(BetaInvitation, BetaInvitation.id == BetaSignupInvitation.invitation_id)
        .where(BetaSignupInvitation.signup_link_id == link.id, BetaInvitation.redeemed_at.is_not(None)))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    invite = sub.add_parser("invite", help="Create one recipient-bound invitation; outputs its secret link once")
    invite.add_argument("email")
    invitations = sub.add_parser("invitations", help="List invitation IDs and status without secret links or codes")
    invitations.add_argument("--limit", type=int, default=100)
    invitations.add_argument("--offset", type=int, default=0)
    revoke_invite = sub.add_parser("revoke-invite", help="Withdraw an invitation; does not revoke an already admitted account")
    revoke_invite.add_argument("invitation_id")
    revoke_invite.add_argument("--reason", required=True)
    launch = sub.add_parser("launch-link", help="Show the one-time launch link, or create it with a fixed deadline")
    launch.add_argument("--expires-at", help="ISO-8601 deadline with timezone, at most seven days ahead; immutable")
    close_launch = sub.add_parser("close-launch-link", help="Permanently close the launch window; joined accounts keep access")
    close_launch.add_argument("--reason", required=True)
    for action in ("grant", "revoke", "suspend", "restore"):
        command = sub.add_parser(action, help="Change access for one existing account")
        command.add_argument("handle")
        command.add_argument("--reason", required=True)
    sub.add_parser("list", help="List stable account IDs and current membership")
    args = parser.parse_args(argv)
    settings = load_settings()
    db = Database(settings.database_url)
    try:
        if hasattr(args, "reason") and (not args.reason.strip() or len(args.reason) > 500):
            raise ValueError("Supply a reason between 1 and 500 characters.")
        with db.transaction(write=args.action not in {"list", "invitations"}) as session:
            if args.action == "launch-link":
                result = launch_link(session, settings, time.time(), args.expires_at)
            elif args.action == "close-launch-link":
                revoke_signup_link(session, LAUNCH_SIGNUP_ID, time.time(), reason="Operator: " + args.reason)
                result = launch_link(session, settings, time.time())
            elif args.action == "invite":
                invitation, link = create_invitation(session, settings, args.email, time.time())
                result = {"id": invitation.id, "expires_at": invitation.expires_at, "invitation_url": link}
            elif args.action == "invitations":
                if not 1 <= args.limit <= 1000 or args.offset < 0:
                    raise ValueError("Use --limit 1..1000 and a nonnegative --offset.")
                rows = session.scalars(select(BetaInvitation).order_by(BetaInvitation.created_at.desc(), BetaInvitation.id)
                                       .offset(args.offset).limit(args.limit)).all()
                result = [{"id": row.id, "email": row.email, "status": invitation_status(row, time.time()),
                           "expires_at": row.expires_at} for row in rows]
            elif args.action == "revoke-invite":
                invitation = invalidate_invitation(session, args.invitation_id, time.time(), reason="Operator: " + args.reason)
                result = {"id": invitation.id, "status": "revoked"}
            elif args.action == "list":
                rows = session.execute(select(User, BetaMembership, AccountState)
                    .outerjoin(BetaMembership, User.id == BetaMembership.user_id)
                    .outerjoin(AccountState, User.id == AccountState.user_id)).all()
                result = [{"id": user.id, "handle": user.handle, "membership": membership.status if membership else "pending",
                           "suspended": bool(state and state.suspended_at is not None)} for user, membership, state in rows]
            else:
                user = session.scalar(select(User).where(User.handle == args.handle.strip().lower()))
                if user is None:
                    raise ValueError("That account does not exist.")
                if args.action in {"grant", "revoke"}:
                    set_membership(session, user.id, "active" if args.action == "grant" else "revoked", time.time(), reason="Operator: " + args.reason)
                else:
                    set_suspension(session, user.id, args.action == "suspend", time.time(), reason="Operator: " + args.reason)
                result = {"id": user.id, "handle": user.handle, "action": args.action}
        print(json.dumps(result, indent=2))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except HTTPException as error:
        print(str(error.detail), file=sys.stderr)
        return 1
    except IntegrityError:
        if args.action != "launch-link":
            raise
        print("The launch link may have been created concurrently. Rerun with the same deadline to recover it.", file=sys.stderr)
        return 1
    finally:
        db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

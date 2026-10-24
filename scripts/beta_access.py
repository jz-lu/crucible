"""Trusted operator controls: python -m scripts.beta_access --help.

Run only on the service host with its explicit database/configuration. This is
not a participant API. It never resets accounts, passwords or contribution IDs.
"""
import argparse
import json
import sys
import time

from sqlalchemy import select
from fastapi import HTTPException

from crucible.beta_access import (AccountState, BetaInvitation, BetaMembership, create_invitation,
                                 invalidate_invitation, invitation_status, set_membership, set_suspension)
from crucible.config import load_settings
from crucible.db import Database, User


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
            if args.action == "invite":
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
    finally:
        db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

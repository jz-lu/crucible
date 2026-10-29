"""Versioned monthly action allowance; independent of earned research scores.

UTC buckets expire. A refund belongs to the original allocation month, never
turning an expired allocation into a fresh grant. All changes leave journal rows.
"""
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from . import beta_access, protocol_proposals
from .db import User
from .bounty_models import BountyAllowanceEvent as Event, BountyPolicy, BountyWallet


class PolicyConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    monthly_units: int = Field(default=10, ge=0, le=100, strict=True)
    proposal_cost_units: int = Field(default=1, ge=1, le=10, strict=True)
    boost_cost_units: int = Field(default=1, ge=1, le=10, strict=True)
    points_per_boost: int = Field(default=100, ge=1, le=1000, strict=True)
    proposal_reward_points: int = Field(default=100, ge=1, le=1000, strict=True)
    carryover: Literal['expires'] = 'expires'
    refund_rule: Literal['original_month'] = 'original_month'
    claim_order: Literal['earliest_qualifying'] = 'earliest_qualifying'


INITIAL_MONTH = '2026-10'
INITIAL_CONFIGURATION = PolicyConfiguration().model_dump()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


INITIAL_ID = digest({'effective_month': INITIAL_MONTH, 'configuration': INITIAL_CONFIGURATION})


def month(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m')


def current_policy(session, now):
    value = session.scalar(select(BountyPolicy).where(BountyPolicy.effective_month <= month(now))
                           .order_by(BountyPolicy.effective_month.desc()).limit(1))
    if value is None:
        raise HTTPException(503, 'The monthly bounty allowance policy is unavailable.')
    PolicyConfiguration.model_validate(value.configuration)
    if digest({'effective_month': value.effective_month, 'configuration': value.configuration}) != value.id:
        raise HTTPException(409, 'The immutable bounty policy identity does not match its terms.')
    return value


def policy_data(policy):
    return {'status': 'ready', 'id': policy.id, 'effective_month': policy.effective_month,
            **policy.configuration, 'default_base_reward_points': policy.configuration['proposal_reward_points']}


def configure_policy(db, settings, *, actor_id, effective_month, configuration, now):
    """Trusted operator entry point, never an agent or automatic HTTP action.

    Future months only: don't change grants or promises in the current bucket.
    Initial migration installs the reviewed pilot independently of any account.
    """
    try:
        parsed = datetime.strptime(effective_month, '%Y-%m').strftime('%Y-%m')
    except (TypeError, ValueError):
        raise ValueError('Use a UTC month in YYYY-MM form.') from None
    if parsed != effective_month or effective_month <= month(now):
        raise ValueError('Policy changes must begin in a future UTC month.')
    value = PolicyConfiguration.model_validate(configuration).model_dump()
    identifier = digest({'effective_month': effective_month, 'configuration': value})
    with db.transaction(write=True) as session:
        actor = beta_access.lock_user(session, actor_id)
        beta_access.require_access(session, settings, actor, now)
        if not protocol_proposals.is_reviewer(settings, actor):
            raise HTTPException(403, 'A configured reviewer must set a future bounty policy.')
        prior = session.scalar(select(BountyPolicy).where(BountyPolicy.effective_month == effective_month))
        if prior:
            if prior.id != identifier:
                raise HTTPException(409, 'This month already has an immutable bounty policy.')
            return policy_data(prior)
        record = BountyPolicy(id=identifier, effective_month=effective_month, configuration=value,
                              created_by=actor_id, created_at=now)
        session.add(record)
        session.flush()
        return policy_data(record)


def eligible(session, settings, user_id, now):
    try:
        beta_access.require_access(session, settings, session.get(User, user_id), now)
        return True
    except HTTPException:
        return False


def wallet(session, user_id, now, *, create=False):
    value = session.scalar(select(BountyWallet).where(BountyWallet.user_id == user_id).with_for_update())
    if value is None and create:
        # The authenticated mutation already holds this account's User lock.
        value = BountyWallet(user_id=user_id, available_units=0, version=0, last_month=month(now))
        session.add(value)
        session.flush()
    if value is None:
        raise HTTPException(409, 'The allocation wallet is unavailable.')
    return value


def append(session, wallet, policy, now, *, kind, event_key, delta, units, task=None,
           points=0, original=None, bucket=None):
    existing = session.scalar(select(Event).where(Event.user_id == wallet.user_id, Event.event_key == event_key))
    if existing:
        return existing
    if wallet.available_units + delta < 0:
        raise HTTPException(409, 'Not enough monthly bounty allowance remains.')
    record = Event(id=str(uuid4()), user_id=wallet.user_id, policy_id=policy.id,
                   month=bucket or month(now), kind=kind, event_key=event_key, wallet_version=wallet.version + 1,
                   available_delta=delta, units=units, reward_points=points,
                   task_id=task.id if task else None, task_sequence=task.sequence if task else None,
                   terms_sha256=task.terms_sha256 if task else None,
                   original_event_id=original.id if original else None, created_at=now)
    wallet.available_units += delta
    wallet.version += 1
    session.add(record)
    session.flush()
    return record


def advance(session, wallet, policy, now, *, grant):
    current = month(now)
    if wallet.last_month > current:
        raise HTTPException(409, 'The allowance clock moved behind its current UTC month.')
    if wallet.last_month != current:
        if wallet.available_units:
            append(session, wallet, policy, now, kind='expiry', event_key='expiry:' + wallet.last_month,
                   delta=-wallet.available_units, units=wallet.available_units, bucket=wallet.last_month)
        wallet.last_month = current
    if grant:
        append(session, wallet, policy, now, kind='grant', event_key='grant:' + current,
               delta=policy.configuration['monthly_units'], units=policy.configuration['monthly_units'])
    return wallet


def prepare_spending(session, user, settings, now, policy_id):
    policy = current_policy(session, now)
    if policy.id != policy_id:
        raise HTTPException(409, 'The allowance policy changed. Review its current cost before continuing.')
    value = wallet(session, user.id, now, create=True)
    advance(session, value, policy, now, grant=True)
    return value, policy


def refund(session, original, task, now, *, kind='refund'):
    prior = session.scalar(select(Event).where(Event.original_event_id == original.id, Event.kind == kind))
    if prior:
        return prior
    value = wallet(session, original.user_id, now)
    advance(session, value, current_policy(session, now), now, grant=False)
    policy = session.get(BountyPolicy, original.policy_id)
    # Preserve the original bucket even if its units have expired. The receipt
    # records units returned and zero newly spendable units in that case.
    return append(session, value, policy, now, kind=kind, event_key=kind + ':' + original.id,
                  delta=original.units if original.month == month(now) else 0,
                  units=original.units, task=task, original=original, bucket=original.month)


def summary(session, user, settings, now):
    policy = current_policy(session, now)
    value = session.get(BountyWallet, user.id)
    balance = value.available_units if value and value.last_month == month(now) else 0
    granted = session.scalar(select(Event.id).where(Event.user_id == user.id,
                              Event.event_key == 'grant:' + month(now))) is not None
    pending = 0 if granted or not eligible(session, settings, user.id, now) else policy.configuration['monthly_units']
    return {'available_units': balance + pending, 'recorded_available_units': balance,
            'monthly_units': policy.configuration['monthly_units'], 'month': month(now),
            'policy_id': policy.id, 'policy_status': 'ready', 'pending_grant_units': pending,
            'version': value.version if value else 0, 'refund_rule': 'original_month',
            'expires_at_month_end': True}


def grant_current_month(db, settings, *, now):
    """Idempotent operator/scheduler helper; no grants for past months.

    Each eligible account is its own transaction. A rerun resumes safely.
    There is no retrospective accrual for inactive or unprocessed old months.
    """
    with db.transaction() as session:
        identifiers = list(session.scalars(select(User.id).order_by(User.id)))
    count = 0
    for identifier in identifiers:
        with db.transaction(write=True) as session:
            beta_access.lock_user(session, identifier)
            if not eligible(session, settings, identifier, now):
                continue
            value = wallet(session, identifier, now, create=True)
            advance(session, value, current_policy(session, now), now, grant=True)
            count += 1
    return {'month': month(now), 'eligible_accounts': count}


def bounty_points_relation():
    """Additive award component; callers choose the overall scoring version."""
    from .bounty_models import BountyAward
    return select(BountyAward.user_id, func.sum(BountyAward.points).label('bounty_points')).group_by(BountyAward.user_id)

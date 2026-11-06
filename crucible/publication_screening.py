"""Publication visibility and consent; screening grants no scientific authority."""
import re

from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import aliased

from . import publication_screening_models  # Register durable models before create_all.


def approved_clause(*, allow_development=False):
    from .notebook import NotebookPublication as Publication, NotebookRevision as Revision
    from .notebook_moderation import withdrawn_ids
    Receipt = publication_screening_models.AutomaticPublication
    # Legacy human decisions retain their actor. Automated decisions require a
    # matching immutable receipt, never just an author-supplied label or NULL actor.
    human = and_(or_(Publication.decision_source.is_(None), Publication.decision_source == "human"),
                 Publication.reviewer_id.is_not(None),
                 or_(Publication.reviewer_id != Publication.author_id,
                     and_(allow_development, Publication.test_only_self_review.is_(True))))
    screened_revision = Revision.__table__.alias("screened_publication_revision")
    automatic = and_(Publication.decision_source == "automated", Publication.reviewer_id.is_(None),
        select(Receipt.publication_id).join(screened_revision, screened_revision.c.id == Receipt.revision_id)
        .where(Receipt.publication_id == Publication.id, Receipt.revision_id == Publication.revision_id,
               Receipt.content_sha256 == screened_revision.c.content_sha256)
        .correlate(Publication).exists())
    return and_(Publication.decision == "approved", or_(human, automatic),
                or_(Publication.test_only_self_review.is_(False), allow_development),
                Publication.revision_id.not_in(withdrawn_ids()))


def public_policy(settings):
    """Small nonsecret notice; no model calls or unpublished content reads."""
    mode = settings.publication_screening_mode
    if mode == "disabled":
        return {"mode": mode, "screening_required": False}
    from .publication_screening_worker import policy_for
    return {"mode": mode, "screening_required": True,
            "policy_id": policy_for(settings).id, "processor": "OpenAI",
            "model": settings.publication_screening_model,
            "notice": "This exact revision will be sent to OpenAI to check for abuse, privacy exposure, "
                      "harmful misuse, spam and gratuitously hostile language. "
                      "Private drafts are not sent. API data is not used for training by default; "
                      "standard monitoring retention can be up to 30 days, with exceptions.",
            "privacy_url": "https://developers.openai.com/api/docs/guides/your-data",
            "publication": ("A passing check publishes this revision to admitted participants. "
                            "Other results go to a human. Later edits stay private."
                            if mode == "automatic" else
                            "This evaluation check does not publish the note. Human publication review still applies.")}


_STATUS_COPY = {
    "queued": "Awaiting automated screening. A reviewer can also review this request.",
    "running": "Automated screening is in progress. This is not a scientific review.",
    "human_review": "Awaiting human publication review. Automated screening has not approved this request.",
    "completed": "The evaluation check is complete. Human publication review is still required.",
    "published": "This revision was published after automated screening.",
    "superseded": "The screening result did not change publication. The publication decision remains authoritative.",
    "cancelled": "The author cancelled this publish request. This revision remains private.",
}
_FAILURE_COPY = {
    "provider_timeout": "The screening service timed out.",
    "provider_unavailable": "The screening service was unavailable.",
    "provider_unconfigured": "The screening service is not configured.",
    "provider_authentication_failed": "The screening service credentials need operator attention.",
    "provider_access_denied": "The screening service access needs operator attention.",
    "provider_quota_exhausted": "The screening provider quota is exhausted.",
    "provider_configuration_error": "The screening service configuration needs operator attention.",
    "input_too_large": "This revision exceeds the automated screening size limit.",
    "input_token_limit": "This revision exceeds the automated screening token limit.",
    "input_content_size_limit": "This revision exceeds the automated screening content-size limit.",
    "input_structure_limit": "This revision exceeds the automated screening structure limit.",
    "provider_count_unavailable": "The screening service could not determine the input size.",
    "coverage_incomplete": "The automated check could not cover this complete revision.",
    "unsupported_input": "This revision could not be prepared for automated screening.",
    "invalid_response": "The automated response did not meet the required format.",
    "publication_unavailable": "This request was no longer eligible for automatic publication.",
    "policy_changed": "The screening policy changed after this request was submitted.",
    "disabled": "Automated screening was disabled before completion.",
    "budget_exhausted": "The configured screening budget is exhausted.",
    "lease_expired": "The screening attempt did not finish within its allotted time.",
    "usage_invalid": "The screening service returned invalid accounting information.",
    "usage_exceeded_reservation": "The screening attempt exceeded its reserved budget.",
    "internal_error": "The automated check could not finish.",
}
_FIELD_LABELS = {"title": "Title", "claim": "Summary", "conditions": "Conditions",
                 "body": "Body", "tags": "Tags", "references": "References",
                 "kind": "Note type", "extensions": "Additional metadata"}
_CATEGORY_LABELS = {
    "privacy_or_credentials": "Privacy or credentials",
    "targeted_abuse": "Targeted abuse",
    "gratuitous_hostility": "Hostile language",
    "unauthorized_harm": "Unauthorized harm",
    "deceptive_content": "Deceptive content",
    "spam": "Spam or off-topic content",
    "other_concrete_harm": "Other concrete harm",
}


def _latest_job_id(publication, revision):
    job = aliased(publication_screening_models.ScreeningJob)
    return select(job.id).where(job.publication_id == publication.id,
        job.revision_id == revision.id, job.author_id == publication.author_id,
        job.content_sha256 == revision.content_sha256).order_by(
            job.created_at.desc(), job.id).limit(1).correlate(publication, revision).scalar_subquery()


def _latest_attempt_id(job):
    attempt = aliased(publication_screening_models.ScreeningAttempt)
    return select(attempt.id).where(attempt.job_id == job.id).order_by(
        attempt.number.desc(), attempt.id).limit(1).correlate(job).scalar_subquery()


def flagged_clause():
    """Trusted worker triage state, bound to the exact pending submitted revision.

    Apply in SQL before keyset pagination. Operational failures never become
    content flags, even when a previous attempt had concerns. Stored findings
    are checked separately before displaying any category; only the full reader
    can revalidate their input/evidence against the submitted body.
    """
    from .notebook import NotebookPublication as Publication, NotebookRevision as Revision
    Job, Attempt = publication_screening_models.ScreeningJob, publication_screening_models.ScreeningAttempt
    return select(Job.id).join(Attempt, Attempt.id == _latest_attempt_id(Job)).where(
        Job.id == _latest_job_id(Publication, Revision), Job.status == "human_review",
        Job.error_code.is_(None), Attempt.error_code.is_(None),
        Attempt.finished_at.is_not(None), Attempt.outcome == "human_review_needed",
        Attempt.result_sha256.is_not(None)
    ).correlate(Publication, Revision).exists()


def attention_context(session, settings, now):
    """Read operator state after reviewer authorization; never start/repair work."""
    Control = publication_screening_models.ScreeningControl
    control = session.execute(select(Control.mode, Control.policy_id, Control.active_job_id,
        Control.lease_expires_at).where(Control.id == 1)).one_or_none()
    enabled = settings.publication_screening_mode != "disabled"
    if enabled:
        from .publication_screening_worker import policy_for
        policy_id = policy_for(settings).id
    else:
        policy_id = None  # Disabled deployments need no provider/model configuration.
    return {"enabled": enabled and control is not None and control.mode != "disabled",
        "policy_id": policy_id, "control_policy_id": control.policy_id if control else None,
        "active_job_id": control.active_job_id if control else None,
        "lease_expires_at": control.lease_expires_at if control else None, "now": now}


def attention_clause(context):
    """Pending human work excludes only an executable exact queued/running job.

    Failed checks, shadow outcomes and legacy requests without a matching job
    all remain actionable. Disabled/mismatched policy or stale running leases
    also need human attention; this read never mutates them. Apply before
    pagination using the same exact/latest job as authorized reader detail.
    """
    from .notebook import NotebookPublication as Publication, NotebookRevision as Revision
    Job = publication_screening_models.ScreeningJob
    if not context["enabled"] or context["policy_id"] != context["control_policy_id"]:
        return true()
    live_lease = context["lease_expires_at"] is not None and context["lease_expires_at"] > context["now"]
    return ~select(Job.id).where(Job.id == _latest_job_id(Publication, Revision),
        Job.policy_id == context["policy_id"],
        or_(Job.status == "queued", and_(Job.status == "running", live_lease,
            Job.id == context["active_job_id"]))).correlate(Publication, Revision).exists()


def _inactive_job_reason(row, context):
    if row.status not in {"queued", "running"}:
        return None
    if not context["enabled"]:
        return "disabled"
    if (context["policy_id"] != context["control_policy_id"]
            or row.policy_id != context["policy_id"]):
        return "policy_changed"
    if row.status == "running" and (row.job_id != context["active_job_id"]
            or context["lease_expires_at"] is None or context["lease_expires_at"] <= context["now"]):
        return "lease_expired"
    return None


def _recorded_admission(policy, result):
    """Check recorded admission shape without rerunning today's prompt/budget.

    The enclosing result digest and exact input digest bind the historical
    receipt. This is a reader check, never fresh publication authority.
    """
    configuration = policy.get("model_configuration", {})
    if type(configuration) is not dict:
        raise ValueError("Invalid screening configuration")
    version = configuration.get("input_admission")
    if version is None:
        return  # Older policies predate the optional receipt.
    if version != "utf8-bound-or-responses-count-v1":
        raise ValueError("Unknown screening admission")
    admission, budget = result.get("input_admission"), policy.get("max_input_tokens")
    if (type(budget) is not int or not 1 <= budget <= 1_000_000
            or type(admission) is not dict or set(admission) != {
                "method", "budget_units", "input_tokens", "count_request_sha256"}
            or type(admission["budget_units"]) is not int or admission["budget_units"] < 1):
        raise ValueError("Invalid screening admission")
    if admission["method"] == "utf8_upper_bound":
        if (admission["budget_units"] > budget or admission["input_tokens"] is not None
                or admission["count_request_sha256"] is not None):
            raise ValueError("Invalid screening admission")
    elif admission["method"] == "responses_input_tokens":
        if (admission["budget_units"] <= budget or type(admission["input_tokens"]) is not int
                or not 0 <= admission["input_tokens"] <= budget
                or type(admission["count_request_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", admission["count_request_sha256"]) is None):
            raise ValueError("Invalid screening admission")
    else:
        raise ValueError("Unknown screening admission method")


def _summary_findings(policy, policy_id, result, result_sha256, outcome):
    """Bounded integrity/shape checks, explicitly not a recheck of source text."""
    from .publication_screening_jobs import digest
    from .publication_screening_policy import (INPUT_FORMAT_VERSION, MAX_CONTENT_BYTES,
        MAX_FIELDS, MAX_VERDICT_BYTES, canonical_json)
    if (type(policy) is not dict or len(canonical_json(policy).encode()) > 8192
            or digest(policy) != policy_id or type(result) is not dict
            or len(canonical_json(result).encode()) > MAX_VERDICT_BYTES
            or digest(result) != result_sha256 or result.get("outcome") != outcome
            or outcome != "human_review_needed" or result.get("full_coverage") is not True
            or result.get("input_format_version") != INPUT_FORMAT_VERSION
            or policy.get("input_format_version") != INPUT_FORMAT_VERSION
            or type(result.get("input_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", result["input_sha256"]) is None):
        raise ValueError("Unverified screening summary")
    _recorded_admission(policy, result)
    concerns, limitations = result.get("concerns"), result.get("limitations")
    if (type(concerns) is not list or len(concerns) > 4
            or type(limitations) is not list or len(limitations) > 2
            or not (concerns or limitations)):
        raise ValueError("Invalid screening findings")

    def reference(item):
        identifier = item.get("field_id")
        if (type(identifier) is not str or re.fullmatch(r"f[0-9]{5}", identifier) is None
                or not 1 <= int(identifier[1:]) <= MAX_FIELDS):
            raise ValueError("Invalid screening field")

    def prose(value):
        if type(value) is not str or not value.strip() or len(value) > 600 or "\x00" in value:
            raise ValueError("Invalid screening prose")

    counts = {}
    for item in concerns:
        if (type(item) is not dict or set(item) != {
                "category", "field_id", "line_start", "line_end", "explanation", "next_step"}
                or type(item["category"]) is not str or item["category"] not in _CATEGORY_LABELS):
            raise ValueError("Invalid screening concern")
        reference(item)
        start, end = item["line_start"], item["line_end"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= MAX_CONTENT_BYTES:
            raise ValueError("Invalid screening location")
        prose(item["explanation"])
        prose(item["next_step"])
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    for item in limitations:
        if type(item) is not dict or set(item) != {"field_id", "reason"}:
            raise ValueError("Invalid screening limitation")
        reference(item)
        prose(item["reason"])
    return ([{"code": code, "label": label, "count": counts[code]}
             for code, label in _CATEGORY_LABELS.items() if code in counts], len(concerns), len(limitations))


def _attention_summary(data, *, manual=False):
    """Neutral presentation categories, separate from any model concern code."""
    if data["reason_code"] in {"model_concerns", "screening_limitations", "model_concerns_and_limitations"}:
        kind, label = "content_findings", "Screening requested review"
    elif manual or (data["status"] in {"completed", "superseded", "published"}
                    and data["reason_code"] not in _FAILURE_COPY):
        kind, label = "manual_review_needed", "Manual review needed"
        data.update(reason_code="manual_review_needed", reason=(
            "This publication request requires manual review." if manual else
            "Automated screening is complete. Manual publication review is still required."
            if data["status"] == "completed" else
            "Automatic screening did not settle this publication request. Manual review is required."))
    else:
        kind, label = "screening_unavailable", "Screening unavailable"
        if data["reason_code"] not in _FAILURE_COPY and data["reason_code"] != "findings_unavailable":
            data.update(reason_code="findings_unavailable",
                reason="Automated screening did not provide usable findings. Review the submitted revision directly.")
    data.update(attention_reason=kind, attention_label=label)
    return data


def queue_summaries(session, publication_ids, *, attention=None):
    """One bounded page lookup, only for an authorized browser review queue.

    No revision bodies are loaded. A recorded input digest is checked for shape
    and bound through its exact job, but cannot be recomputed here. Digest and
    structural checks justify compact triage labels, never a verified finding.
    A damaged result stays pending and exposes only fixed unavailable copy.
    """
    from .notebook import NotebookPublication as Publication, NotebookRevision as Revision
    Job, Attempt = publication_screening_models.ScreeningJob, publication_screening_models.ScreeningAttempt
    if not publication_ids:
        return {}
    query = select(Publication.id, Job.id.label("job_id"), Job.status, Job.error_code, Job.policy, Job.policy_id,
        Attempt.outcome, Attempt.error_code.label("attempt_error"), Attempt.finished_at,
        Attempt.result, Attempt.result_sha256).join(Revision, Revision.id == Publication.revision_id)
    query = (query.outerjoin(Job, Job.id == _latest_job_id(Publication, Revision)) if attention is not None else
             query.join(Job, Job.id == _latest_job_id(Publication, Revision)))
    rows = session.execute(query
        .outerjoin(Attempt, Attempt.id == _latest_attempt_id(Job))
        .where(Publication.id.in_(publication_ids), Publication.decision.is_(None)))
    summaries = {}
    for row in rows:
        status = row.status if row.status in _STATUS_COPY else "human_review"
        code = row.error_code if row.error_code is not None else row.attempt_error
        if attention is not None:
            code = _inactive_job_reason(row, attention) or code
        has_error = code is not None
        data = {"status": status, "reason_code": code if code in _FAILURE_COPY else
                ("internal_error" if has_error else status),
                "reason": _FAILURE_COPY.get(code, "The automated check could not finish.")
                    if has_error else _STATUS_COPY[status],
                "categories": [], "concern_count": 0, "limitation_count": 0,
                "verification": "summary_only"}
        summaries[row.id] = data
        if (row.status != "human_review" or has_error or row.outcome != "human_review_needed"
                or row.finished_at is None or row.result_sha256 is None):
            if attention is not None:
                _attention_summary(data, manual=row.job_id is None)
            continue
        try:
            categories, count, limitations = _summary_findings(row.policy, row.policy_id,
                row.result, row.result_sha256, row.outcome)
            data.update(categories=categories, concern_count=count, limitation_count=limitations,
                reason_code=("model_concerns_and_limitations" if count and limitations else
                             "model_concerns" if count else "screening_limitations"),
                reason="Automated screening requested human attention.")
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError, UnicodeError):
            data.update(reason_code="findings_unavailable",
                reason="Stored screening findings are unavailable. Open the submitted revision to review it.")
        if attention is not None:
            _attention_summary(data)
    return summaries


def detail(session, publication, revision, *, reviewer=False):
    """Caller must authorize the author/reviewer; return only selected-revision detail.

    Authors receive fixed status copy, never model text. Reviewer findings are
    revalidated against the immutable input and bounded output contract. No
    usage, credentials, provider response IDs or arbitrary metadata keys escape.
    """
    from .publication_screening_models import ScreeningAttempt, ScreeningJob
    from .publication_screening_jobs import digest
    from .publication_screening_policy import reconstruct_input, validate_verdict
    if publication is None:
        return None
    job = session.scalar(select(ScreeningJob).where(
        ScreeningJob.publication_id == publication.id,
        ScreeningJob.revision_id == revision.id,
        ScreeningJob.author_id == publication.author_id,
        ScreeningJob.content_sha256 == revision.content_sha256)
        .order_by(ScreeningJob.created_at.desc(), ScreeningJob.id).limit(1))
    if job is None:
        return None
    data = {"revision": revision.revision, "content_sha256": revision.content_sha256,
            "status": job.status if job.status in _STATUS_COPY else "human_review",
            "message": _STATUS_COPY.get(job.status, "A human publication review is required.")}
    if job.status == "human_review" and job.error_code in _FAILURE_COPY:
        data["message"] = _FAILURE_COPY[job.error_code] + " A human can review this request."
    if not reviewer:
        return data
    findings = {"reason": _FAILURE_COPY.get(job.error_code, "The automated check could not finish.")
                if job.error_code else None, "outcome": None, "concerns": [], "limitations": []}
    data["reviewer"] = findings
    attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id)
                             .order_by(ScreeningAttempt.number.desc()).limit(1))
    if attempt is None or attempt.result is None:
        return data
    try:
        result = attempt.result
        if (type(result) is not dict or digest(result) != attempt.result_sha256
                or type(job.policy) is not dict or digest(job.policy) != job.policy_id):
            raise ValueError("Unverified result")
        _recorded_admission(job.policy, result)
        prepared = reconstruct_input(revision.content,
            input_format_version=job.policy.get("input_format_version"))
        if (prepared.content_sha256 != revision.content_sha256
                or result.get("input_sha256") != prepared.input_sha256
                or result.get("input_format_version") != prepared.input_format_version
                or result.get("full_coverage") is not True):
            raise ValueError("Unverified input")
        verdict = validate_verdict({key: result.get(key) for key in ("outcome", "concerns", "limitations")}, prepared)
        if verdict["outcome"] != attempt.outcome:
            raise ValueError("Unverified outcome")

        def located(item):
            field = prepared.fields[item["field_id"]]
            label = _FIELD_LABELS.get(field.location[0] if field.location else None, "Note metadata")
            return {**item, "field_label": label + (" (field name)" if field.is_key else "")}

        findings.update(outcome=verdict["outcome"],
            concerns=[located(item) for item in verdict["concerns"]],
            limitations=[located(item) for item in verdict["limitations"]])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        findings["unavailable"] = "Stored screening findings could not be verified. Review this revision directly."
    return data

"""Serialize the JSON-native public catalogue without a second recursive walk.

Compression is explicitly opt-in per public endpoint, never middleware applied
to private/account responses. No response body or dynamic score is cached here.
"""
import gzip
import math

from starlette.responses import JSONResponse, Response


PROGRESS_FIELDS = ("participant_count", "recovered_profile_ids", "largest_recovered_profile_id",
                   "next_profile_id", "profile_recoveries", "first_recovery", "generation_admission",
                   "recovery_summary")


def catalogue_progress(catalogue):
    """Small public projection; no protocol metadata or private challenge data."""
    return [{key: release[key] for key in ("id", "version", *PROGRESS_FIELDS) if key in release}
            for scheme in catalogue for release in (scheme, *scheme["previous_releases"])]


def accepts_gzip(header):
    explicit, wildcard = [], []
    for item in (header or "").split(","):
        parts = [part.strip() for part in item.split(";")]
        coding = parts[0].lower()
        if coding not in {"gzip", "*"}:
            continue
        quality = 1.0
        for option in parts[1:]:
            name, _, value = option.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                    if not math.isfinite(quality) or not 0 <= quality <= 1:
                        quality = 0.0
                except ValueError:
                    quality = 0.0
        (explicit if coding == "gzip" else wildcard).append(quality)
    # An explicit prohibition must not be overridden by a wildcard or an
    # ambiguous duplicate coding. No header means an ordinary plain response.
    qualities = explicit if explicit else wildcard
    return bool(qualities) and min(qualities) > 0


def catalogue_response(document, accept_encoding):
    body = JSONResponse(document).body
    headers = {"Vary": "Accept-Encoding"}
    if len(body) >= 1024 and accepts_gzip(accept_encoding):
        body = gzip.compress(body, compresslevel=4, mtime=0)
        headers["Content-Encoding"] = "gzip"
    return Response(body, media_type="application/json", headers=headers)

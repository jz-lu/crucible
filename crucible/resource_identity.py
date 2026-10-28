"""A narrowly scoped GitHub Actions OIDC identity for resource publication.

There are no repository, participant or deployment credentials in this capability.
Keys come only from GitHub's fixed issuer, never from a JWT-supplied URL.
"""
import base64
from functools import lru_cache
import json
import re
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
import httpx

ISSUER = "https://token.actions.githubusercontent.com"
REPOSITORY = "countableclouds/crucible-schemes"
REPOSITORY_ID = "1435154180"
OWNER_ID = "24554788"
WORKFLOW = REPOSITORY + "/.github/workflows/publish-resources.yml@refs/heads/main"
UPLOAD_PATH = "/api/resource-publications"


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("Nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@lru_cache(maxsize=2)
def signing_keys(bucket):
    # The fixed, small document is cached for five minutes. Network failures fail
    # closed and do not affect normal participant/API requests.
    with httpx.Client(timeout=8, follow_redirects=False) as client:
        with client.stream("GET", ISSUER + "/.well-known/jwks") as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 131072:
                    raise ValueError("Oversized issuer keys")
    return strict_json(bytes(raw))["keys"]


def verify(authorization, audience, now=None, *, keys=None):
    now = time.time() if now is None else now
    try:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or len(authorization) > 16384:
            raise ValueError
        encoded_header, encoded_claims, signature = authorization[7:].split(".")
        header, claims = strict_json(decode(encoded_header)), strict_json(decode(encoded_claims))
        if header.get("alg") != "RS256" or header.get("typ") != "JWT" or not isinstance(header.get("kid"), str):
            raise ValueError
        candidates = keys if keys is not None else signing_keys(int(now // 300))
        matches = [key for key in candidates if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
                   and key.get("use", "sig") == "sig" and key.get("alg", "RS256") == "RS256"]
        if len(matches) != 1:
            raise ValueError
        key = matches[0]
        public = rsa.RSAPublicNumbers(int.from_bytes(decode(key["e"]), "big"),
                                      int.from_bytes(decode(key["n"]), "big")).public_key()
        public.verify(decode(signature), (encoded_header + "." + encoded_claims).encode(), padding.PKCS1v15(), hashes.SHA256())
        expected = {"iss": ISSUER, "aud": audience, "repository": REPOSITORY,
                    "repository_id": REPOSITORY_ID, "repository_owner_id": OWNER_ID,
                    "ref": "refs/heads/main", "ref_type": "branch", "workflow_ref": WORKFLOW}
        if any(claims.get(k) != v for k, v in expected.items()):
            raise ValueError
        subjects = {f"repo:{REPOSITORY}:ref:refs/heads/main",
                    f"repo:countableclouds@{OWNER_ID}/crucible-schemes@{REPOSITORY_ID}:ref:refs/heads/main"}
        if claims.get("sub") not in subjects or claims.get("event_name") not in {"push", "workflow_dispatch"}:
            raise ValueError
        # GitHub also emits these job-level claims for direct jobs. Admit only
        # the same pinned workflow and source; a different reusable workflow is
        # not delegated publication authority by the calling workflow.
        job_ref = claims.get("job_workflow_ref")
        job_sha = claims.get("job_workflow_sha")
        if job_ref is not None or job_sha is not None:
            if job_ref != WORKFLOW or job_sha != claims.get("sha"):
                raise ValueError
        if claims.get("environment"):
            raise ValueError
        if any(type(claims.get(k)) is not int for k in ("iat", "nbf", "exp")):
            raise ValueError
        if not (claims["nbf"] <= now + 30 and now < claims["exp"]
                and now - 600 <= claims["iat"] <= now + 30
                and 0 < claims["exp"] - claims["iat"] <= 900):
            raise ValueError
        if (not re.fullmatch(r"[0-9a-f]{40}", claims.get("sha", ""))
                or claims.get("workflow_sha") != claims["sha"]
                or any(not isinstance(claims.get(k), str) or not re.fullmatch(r"[1-9][0-9]{0,17}", claims[k]) for k in ("run_id", "run_attempt"))):
            raise ValueError
        if int(claims["run_attempt"]) > 1_000_000:
            raise ValueError
        return {"repository": REPOSITORY, "repository_id": REPOSITORY_ID, "commit": claims["sha"],
                "run_id": claims["run_id"], "run_attempt": claims["run_attempt"]}
    except (httpx.HTTPError, OSError):
        raise HTTPException(503, "GitHub publication identity could not be checked. Retry later.") from None
    except Exception:
        raise HTTPException(401, "Invalid or expired resource publication identity.") from None

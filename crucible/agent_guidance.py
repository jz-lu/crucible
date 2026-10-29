"""Bounded first-party agent documentation, never a repository file browser.

These resources contain pipeline policy, not participant records or credentials.
Keep aliases stable while advancing the referenced policy edition deliberately.
The site membership boundary still applies, including to scoped machine reads.
"""

PIPELINE_PATH = "/api/agent/pipeline"
RESOURCE_PREFIX = "/api/agent/guidance/"
RESOURCES = {
    "bounties.txt": ("docs/bounties.txt", "text/plain"),
    "agent-workflow.txt": ("docs/agent-workflow.txt", "text/plain"),
    "note-format.txt": ("web/map/NOTE-FORMAT.txt", "text/plain"),
    "note-requests.txt": ("docs/note-requests.txt", "text/plain"),
    "request-types.json": ("web/notebook/request-types.json", "application/json"),
    "proposal-guide.txt": ("docs/proposal-guide.txt", "text/plain"),
    "correctness-policy.txt": ("docs/correctness-policy-v1.txt", "text/plain"),
    "ladder-principles.txt": ("docs/ladder-principles.txt", "text/plain"),
    "scoring-policy.txt": ("docs/scoring-v11.txt", "text/plain"),
    "scoring-base.txt": ("docs/scoring-v11.txt", "text/plain"),
    "scheme-admission-policy.txt": ("docs/scheme-admission-points-v2.txt", "text/plain"),
    "benchmark-policy.txt": ("docs/reference-benchmarks.txt", "text/plain"),
}


def is_guidance_read(method, path):
    return method == "GET" and (
        path == PIPELINE_PATH or path in {RESOURCE_PREFIX + name for name in RESOURCES}
    )

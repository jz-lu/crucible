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


def pipeline(root, *, bounties_enabled=False):
    """Advertise only workflows enabled by this application release."""
    import json
    value = json.loads((root / "web" / "agent" / "pipeline.json").read_text())
    value["features"] = {"bounties": bounties_enabled}
    if not bounties_enabled:
        for resource in value["resources"]:
            if resource["id"] == "scoring-policy.txt":
                resource["role"] = "Current provisional points policy crucible-points-v11: numerical frontier awards, actual admission recognition and note support."
        value["resources"] = [row for row in value["resources"] if row["id"] != "bounties.txt"]
        value["reading_plan"] = [row for row in value["reading_plan"] if row["task"] != "research_bounty"]
        value["rules"] = [row for row in value["rules"] if row["id"] != "scoring.bounty-settlement"]
        value["capabilities"]["current"] = [row for row in value["capabilities"]["current"]
                                               if row["id"] != "agent-research-bounties"]
    return value


def guidance(root, resource_name, *, bounties_enabled=False):
    resource = RESOURCES.get(resource_name)
    if resource is None or (resource_name == "bounties.txt" and not bounties_enabled):
        return None
    path, media_type = resource
    body = (root / path).read_bytes()
    # Explicit source markers keep release-specific sections independent of prose.
    # Enabled guidance omits the markers too; no ad-hoc sentence matching.
    import re
    body = re.sub(rb"<!-- feature:bounties -->\n(.*?)<!-- endfeature:bounties -->\n",
                  lambda match: match[1] if bounties_enabled else b"", body, flags=re.DOTALL)
    return body, media_type

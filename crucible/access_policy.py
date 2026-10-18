"""Explicit site admission boundary, separate from ownership and agent scopes.

Every registered route has a reviewed classification. Unknown paths are denied
in beta, including routes added after startup. Handlers still enforce resource
ownership, reviewer permissions, CSRF and authorization inside write transactions.
"""
from starlette.routing import Mount

PUBLIC = {
    ("GET", "/"), ("GET", "/styles.css"), ("GET", "/app.js"),
    ("GET", "/math.js"), ("GET", "/assumptions.js"), ("GET", "/proposal-retractions.js"), ("GET", "/assets/crucible-logo.png"),
    ("GET", "/health"), ("GET", "/api/session"), ("GET", "/api/agent/client"),
    ("POST", "/api/auth/register"), ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"), ("POST", "/api/agent/connect/start"),
    ("POST", "/api/agent/connect/poll"),
    ("GET", "/api/access/status"),
    ("POST", "/api/access/invitations/verify/start"),
    ("POST", "/api/access/invitations/redeem"),
}

# Explicit paths from the existing application. This is deliberately not a
# prefix exemption: adding a route requires choosing its audience here.
MEMBER_PATHS = {
    "/api/agent/pipeline", "/api/agent/guidance/{resource_name}",
    "/api/agent/notes", "/api/agent/notes/{note_id}", "/api/agent/notes/{note_id}/rating",
    "/openapi.json", "/api/schemes", "/api/challenges",
    "/api/challenges/{challenge_id}/download", "/api/challenges/{challenge_id}/submit",
    "/api/methods", "/api/methods/{method_id}/source", "/api/methods/{method_id}/source/download",
    "/api/leaderboard", "/api/submissions/{submission_id}", "/api/me/submissions",
    "/map/{asset_name}",
    "/notebook/{asset_name}", "/api/protocol/source", "/api/protocol/artifacts",
    "/api/notebook/research-requests", "/api/notebook/research-requests/{request_id}",
    "/api/notebook/research-requests/{request_id}/assessments",
    "/api/notebook/research-requests/{request_id}/retraction",
    "/api/notebook/timeline", "/api/notebook/graph", "/api/notebook/notes", "/api/notebook/notes/{note_id}",
    "/api/notebook/notes/{note_id}/private-shares",
    "/api/notebook/private-shares", "/api/notebook/private-shares/{share_id}/response",
    "/api/notebook/notes/{note_id}/revisions", "/api/notebook/export",
    "/api/notebook/import", "/api/notebook/imports/{import_id}",
    "/api/notebook/notes/{note_id}/proposal-submissions",
    "/api/notebook/notes/{note_id}/publication-requests",
    "/api/notebook/notes/{note_id}/publication-withdrawals",
    "/api/notebook/notes/{note_id}/publication-reviews", "/api/notebook/review-queue",
    "/api/protocol/correctness", "/api/protocol/artifact-source", "/api/protocol/example",
    "/api/protocol/reference-method", "/api/protocol/benchmarks", "/api/protocol/assumptions", "/api/protocol/evidence-note",
    "/api/agent/connect/{code}", "/api/agent/connect/{code}/approve", "/api/agent/connect/{code}/deny",
    "/api/agent/tokens", "/api/agent/tokens/{token_id}/revoke",
    "/api/people", "/api/people/{handle}", "/api/people/{handle}/milestones",
    "/api/targets", "/api/contributions", "/api/contributions/{contribution_id}",
    "/api/contributions/{contribution_id}/revisions", "/api/protocol/spec-schema",
    "/api/protocol/spec-template", "/api/protocol/proposal-guide", "/api/protocol/spec", "/api/protocol/proposals/validate",
    "/api/protocol/proposals", "/api/me/protocol-proposals", "/api/protocol/review-queue",
    "/api/protocol/proposals/{proposal_id}/revisions", "/api/protocol/proposals/{proposal_id}/reviews",
    "/api/protocol/proposals/{proposal_id}/retraction",
    "/api/protocol/proposals/{proposal_id}",
    "/api/access/invitations", "/api/access/invitations/{invitation_id}/revoke",
    "/api/access/members", "/api/access/members/{user_id}/revoke",
    "/api/access/members/{user_id}/suspend",
    "/api/access/members/{user_id}/restore",
}


class AccessPolicy:
    def __init__(self):
        self.routes = ()

    def register(self, routes):
        classified = []
        for route in routes:
            if isinstance(route, Mount):
                if route.path != "/vendor/katex":
                    raise RuntimeError(f"Unclassified static mount: {route.path}")
                classified.append((route.path_regex, {"GET", "HEAD"}, "public"))
                continue
            for method in route.methods or ():
                audience = "public" if (method, route.path) in PUBLIC else "member" if route.path in MEMBER_PATHS else None
                # FastAPI's OpenAPI route includes HEAD automatically.
                if method == "HEAD" and ("GET", route.path) in PUBLIC:
                    audience = "public"
                if audience is None:
                    raise RuntimeError(f"Unclassified route: {method} {route.path}")
                classified.append((route.path_regex, {method}, audience))
        self.routes = tuple(classified)

    def audience(self, method, path):
        for expression, methods, audience in self.routes:
            if expression.fullmatch(path) and (method in methods or method == "HEAD" and "GET" in methods):
                return audience
        return None

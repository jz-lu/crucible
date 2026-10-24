"""Dedicated App Platform entry point, conditional on a verified ingress boundary.

The service port must be reachable only through DigitalOcean's trusted ingress.
Before enabling beta access, verify that ingress replaces client-supplied
do-connecting-ip headers. The header itself is not authentication. Do not use
this factory on a directly exposed server or enable Uvicorn proxy-header parsing.
Local development continues to use crucible.app:create_app.
"""
import os
from ipaddress import ip_address, IPv6Address
from urllib.parse import urlsplit


def client_ip(headers):
    values = [value for name, value in headers if name.lower() == b"do-connecting-ip"]
    if len(values) != 1 or not 1 <= len(values[0]) <= 45:
        raise ValueError("One provider client address is required.")
    try:
        value = values[0].decode("ascii")
        if "%" in value:
            raise ValueError
        address = ip_address(value)
    except (UnicodeError, ValueError):
        raise ValueError("Invalid provider client address.") from None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


async def _response(send, status, body, *, head=False):
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]})
    await send({"type": "http.response.body", "body": b"" if head else body})


class AppPlatformIngress:
    """Normalize the single provider address; never trust arbitrary proxy chains.

    Health probes may arrive without Host/client-IP headers. Only their exact
    GET/HEAD paths bypass those checks; readiness still uses the application's
    database check. Liveness merely confirms the ASGI event loop is responding.
    All other requests retain their original Host for TrustedHostMiddleware.
    """

    def __init__(self, app, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path
                or parsed.port not in (None, 443)):
            raise ValueError("App Platform requires a canonical HTTPS origin.")
        self.app = app
        self.host = parsed.netloc.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        probe = scope["method"] in {"GET", "HEAD"} and scope["path"] in {
            "/health", "/health/live",
        }
        if probe and scope["path"] == "/health/live":
            return await _response(send, 200, b'{"status":"ok"}', head=scope["method"] == "HEAD")
        headers = scope.get("headers", [])
        if not probe:
            try:
                address = client_ip(headers)
            except ValueError:
                return await _response(send, 400, b'{"detail":"Invalid ingress request."}',
                                       head=scope["method"] == "HEAD")
        forwarded = {b"forwarded", b"x-real-ip", b"do-connecting-ip"}
        cleaned = [(name, value) for name, value in headers
                   if name.lower() not in forwarded and not name.lower().startswith(b"x-forwarded-")
                   and not (probe and name.lower() == b"host")]
        if probe:
            cleaned.append((b"host", self.host))
        scoped = dict(scope, headers=cleaned, scheme="https")
        if not probe:
            scoped["client"] = (address, 0)
        elif scope["method"] == "HEAD":
            # FastAPI's GET readiness route does not automatically serve HEAD.
            # Execute that same DB check and retain its real response status,
            # while preserving HEAD semantics without changing the local app.
            scoped["method"] = "GET"

            async def head_send(message):
                if message["type"] == "http.response.body":
                    message = dict(message, body=b"")
                await send(message)

            return await self.app(scoped, receive, head_send)
        await self.app(scoped, receive, send)


def create_app():
    """Explicit hosting-only factory; no database or implementation admission."""
    # Refuse accidental local invocation before configuration loading can create
    # development directories, a server key, or touch any database settings.
    if (os.environ.get("CRUCIBLE_ENV") != "production"
            or os.environ.get("CRUCIBLE_ACCESS_MODE") != "beta"):
        raise RuntimeError("This entry point requires an explicitly configured production beta.")
    from .config import load_settings
    from .app import create_app as application

    settings = load_settings()
    if not settings.production or settings.access_mode != "beta" or len(settings.origins) != 1:
        raise RuntimeError("This entry point requires an explicitly configured production beta.")
    return AppPlatformIngress(application(settings), settings.origins[0])

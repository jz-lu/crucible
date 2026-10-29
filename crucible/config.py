"""Configuration. Local convenience must not silently become production defaults."""
from dataclasses import dataclass, field
import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse, urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
APPROVED_BETA_RELEASES = ROOT / "crucible" / "data" / "approved_beta_releases.json"


def approved_beta_releases() -> tuple[str, ...]:
    """Read deployment policy, without importing or automatically admitting adapters."""
    message = "The packaged beta release catalogue is missing or invalid."

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        with APPROVED_BETA_RELEASES.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError
        data = json.loads(raw, object_pairs_hook=unique_object)
        if (type(data) is not dict or set(data) != {"format", "review_record", "releases"}
                or data["format"] != "crucible-approved-beta-releases-v1"
                or data["review_record"] != "docs/beta-release-policy.txt"
                or type(data["releases"]) is not list or not 1 <= len(data["releases"]) <= 128):
            raise ValueError
        releases = data["releases"]
        if (any(type(value) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}@[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value)
                for value in releases) or len(set(releases)) != len(releases)):
            raise ValueError
        return tuple(releases)
    except (OSError, ValueError, TypeError, RecursionError):
        raise RuntimeError(message) from None


def configured_beta_releases(*, production: bool) -> tuple[str, ...]:
    value = os.getenv("CRUCIBLE_BETA_RELEASES", "").strip()
    if not production:
        return tuple(part.strip() for part in value.split(",") if part.strip())
    approved = approved_beta_releases()  # Even an override requires valid packaged policy.
    if not value:
        return approved
    if value == "none":
        return ()
    selected = tuple(part.strip() for part in value.split(","))
    if len(set(selected)) != len(selected) or not set(selected) <= set(approved):
        raise RuntimeError("CRUCIBLE_BETA_RELEASES must be a subset of the packaged approved releases, or none.")
    return selected


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret: bytes = field(repr=False)
    origins: tuple[str, ...] = ("http://127.0.0.1:8000", "http://localhost:8000")
    production: bool = False
    rate_limits: bool = True
    session_seconds: int = 86400
    reviewer_ids: tuple[str, ...] = ()
    allow_self_review: bool = False
    # Release capability: enable deliberately in code once the task board is ready.
    # No deployment environment variable is required for the interim release.
    bounties_enabled: bool = False
    access_mode: str = "open"
    access_admin_ids: tuple[str, ...] = ()
    generation_concurrency: int = 2
    generation_per_account: int = 1
    auth_concurrency: int = 2
    beta_releases: tuple[str, ...] = ()
    beta_issuance_disabled: bool = False
    mail_backend: str = "disabled"
    mail_outbox_dir: str | None = None
    mail_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_starttls: bool = True
    github_login_enabled: bool = False
    github_client_id: str = ""
    github_client_secret: str = field(default="", repr=False)

    def __post_init__(self):
        if type(self.bounties_enabled) is not bool:
            raise RuntimeError("Bounty availability must be explicitly enabled or disabled.")
        if type(self.github_login_enabled) is not bool:
            raise RuntimeError("GitHub sign-in must be explicitly enabled or disabled.")
        if self.github_login_enabled:
            if (type(self.github_client_id) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", self.github_client_id)
                    or type(self.github_client_secret) is not str or not 20 <= len(self.github_client_secret) <= 512
                    or not self.github_client_secret.isascii()
                    or any(not 33 <= ord(character) <= 126 for character in self.github_client_secret)
                    or len(self.origins) != 1 or _canonical_origin(self.origins[0]) != self.origins[0]
                    or (self.production and not self.origins[0].startswith("https://"))):
                raise RuntimeError("GitHub sign-in requires explicit app credentials and one canonical site origin.")
        if type(self.allow_self_review) is not bool or (self.production and self.allow_self_review):
            raise RuntimeError("Self-review is available only through an explicit development setting.")
        if self.access_mode not in {"open", "beta"}:
            raise RuntimeError("Access mode must be open or beta.")
        if self.production and self.access_mode != "beta":
            raise RuntimeError("Production currently requires beta access; public publication policy is not implemented.")
        for name in ("generation_concurrency", "generation_per_account", "auth_concurrency"):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 32:
                raise RuntimeError(f"{name} must be an integer from 1 to 32.")
        if self.generation_per_account > self.generation_concurrency:
            raise RuntimeError("Per-account generation capacity cannot exceed total capacity.")
        if self.mail_backend not in {"disabled", "local", "smtp"}:
            raise RuntimeError("Mail backend must be disabled, local, or smtp.")
        if self.production and self.mail_backend == "local":
            raise RuntimeError("Local mail delivery is for development only.")
        if self.mail_backend == "local" and not self.mail_outbox_dir:
            raise RuntimeError("Local mail delivery requires an explicit private outbox directory.")
        if self.mail_backend == "smtp" and (not self.smtp_host or not self.mail_from or
                type(self.smtp_port) is not int or not 1 <= self.smtp_port <= 65535 or
                not self.smtp_starttls):
            raise RuntimeError("SMTP requires a host, sender, valid port and TLS.")
        if any("\n" in value or "\r" in value for value in (self.mail_from, self.smtp_host)):
            raise RuntimeError("Invalid mail configuration.")
        try:
            if any(str(UUID(value)) != value for value in self.access_admin_ids):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Access administrators must be explicit account UUIDs.") from None
        if any(not isinstance(value, str) or value.count("@") != 1 or
               not all(value.split("@")) for value in self.beta_releases):
            raise RuntimeError("Beta releases must use scheme_id@version identifiers.")
        if type(self.beta_issuance_disabled) is not bool or (self.beta_issuance_disabled and self.beta_releases):
            raise RuntimeError("Disabled beta issuance cannot select releases.")

    @property
    def cookie_name(self):
        return "__Host-crucible" if self.production else "crucible_session"

    @property
    def hosts(self):
        return list({urlparse(origin).hostname for origin in self.origins})

    @property
    def github_callback_url(self):
        return self.origins[0] + "/api/auth/github/callback"

    @property
    def github_cookie_name(self):
        return "__Host-crucible-github" if self.production else "crucible_github"


def _canonical_origin(value: str) -> str:
    message = "CRUCIBLE_ORIGIN must be an origin such as https://crucible.example."
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise RuntimeError(message)
    try:
        parsed = urlsplit(value)
        port = parsed.port  # Validates numeric syntax and the port range.
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.netloc.endswith(":")):
            raise ValueError
    except ValueError:
        raise RuntimeError(message) from None
    host = host.lower()
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != {"http": 80, "https": 443}[parsed.scheme]:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


def load_settings() -> Settings:
    environment = os.getenv("CRUCIBLE_ENV", "development")
    if environment not in {"development", "production"}:
        raise RuntimeError("CRUCIBLE_ENV must be exactly development or production.")
    production = environment == "production"
    self_review = os.getenv("CRUCIBLE_ALLOW_SELF_REVIEW", "false")
    if self_review not in {"true", "false"}:
        raise RuntimeError("CRUCIBLE_ALLOW_SELF_REVIEW must be exactly true or false.")
    if production and self_review == "true":
        raise RuntimeError("CRUCIBLE_ALLOW_SELF_REVIEW cannot be enabled in production.")
    data_dir = Path(os.getenv("CRUCIBLE_DATA_DIR", str(ROOT / ".local")))
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{data_dir / 'crucible.db'}")
    origin = os.getenv("CRUCIBLE_ORIGIN")
    origin = _canonical_origin(origin) if origin else None
    origins = (origin,) if origin else ("http://127.0.0.1:8000", "http://localhost:8000")
    encoded = os.getenv("CRUCIBLE_SECRET")
    if production and (not encoded or not origin or not origin.startswith("https://") or not database_url.startswith("postgresql")):
        raise RuntimeError("Production requires CRUCIBLE_SECRET, an HTTPS CRUCIBLE_ORIGIN, and a PostgreSQL DATABASE_URL.")
    if not os.getenv("DATABASE_URL"):
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if encoded:
        try:
            secret = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise RuntimeError("CRUCIBLE_SECRET must be base64 encoding of at least 32 random bytes.") from exc
    else:
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        key_file = data_dir / "server.key"
        if not key_file.exists() and (data_dir / "crucible.db").exists():
            raise RuntimeError("The database exists but its server key is missing. Restore the original key; do not replace it.")
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            secret = key_file.read_bytes()
        else:
            secret = os.urandom(32)
            with os.fdopen(fd, "wb") as out:
                out.write(secret)
    if len(secret) < 32:
        raise RuntimeError("Server secret must contain at least 32 bytes.")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        if "CRUCIBLE_REVIEWER_IDS" in os.environ:
            identifiers = [item.strip() for item in os.environ["CRUCIBLE_REVIEWER_IDS"].split(",") if item.strip()]
        elif not production and (data_dir / "reviewers.json").exists():
            document = json.loads((data_dir / "reviewers.json").read_text())
            if not isinstance(document, dict) or set(document) != {"reviewer_ids"} or not isinstance(document["reviewer_ids"], list):
                raise ValueError("Invalid reviewer configuration.")
            identifiers = document["reviewer_ids"]
        else:
            identifiers = []
        if any(not isinstance(value, str) for value in identifiers):
            raise ValueError("Invalid reviewer identifier.")
        reviewers = tuple(dict.fromkeys(str(UUID(value)) for value in identifiers))
    except (ValueError, OSError, TypeError, AttributeError):
        raise RuntimeError("Reviewer configuration must contain explicit account UUIDs.") from None
    def number(name, default):
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            raise RuntimeError(f"{name} must be an integer.") from None
    try:
        admins = tuple(dict.fromkeys(str(UUID(value.strip())) for value in
                       os.getenv("CRUCIBLE_ACCESS_ADMIN_IDS", "").split(",") if value.strip()))
    except ValueError:
        raise RuntimeError("CRUCIBLE_ACCESS_ADMIN_IDS requires explicit account UUIDs.") from None
    tls = os.getenv("CRUCIBLE_SMTP_STARTTLS", "true")
    if tls not in {"true", "false"}:
        raise RuntimeError("CRUCIBLE_SMTP_STARTTLS must be true or false.")
    github_enabled = os.getenv("CRUCIBLE_GITHUB_ENABLED", "false")
    if github_enabled not in {"true", "false"}:
        raise RuntimeError("CRUCIBLE_GITHUB_ENABLED must be true or false.")
    return Settings(database_url=database_url, secret=secret, origins=origins, production=production,
                    reviewer_ids=reviewers, allow_self_review=self_review == "true",
                    access_mode=os.getenv("CRUCIBLE_ACCESS_MODE", "beta" if production else "open"),
                    access_admin_ids=admins,
                    generation_concurrency=number("CRUCIBLE_GENERATION_CONCURRENCY", 2),
                    generation_per_account=number("CRUCIBLE_GENERATION_PER_ACCOUNT", 1),
                    auth_concurrency=number("CRUCIBLE_AUTH_CONCURRENCY", 2),
                    beta_releases=configured_beta_releases(production=production),
                    beta_issuance_disabled=production and os.getenv("CRUCIBLE_BETA_RELEASES", "").strip() == "none",
                    mail_backend=os.getenv("CRUCIBLE_MAIL_BACKEND", "disabled"),
                    mail_outbox_dir=os.getenv("CRUCIBLE_MAIL_OUTBOX_DIR"),
                    mail_from=os.getenv("CRUCIBLE_MAIL_FROM", ""), smtp_host=os.getenv("CRUCIBLE_SMTP_HOST", ""),
                    smtp_port=number("CRUCIBLE_SMTP_PORT", 587), smtp_username=os.getenv("CRUCIBLE_SMTP_USERNAME", ""),
                    smtp_password=os.getenv("CRUCIBLE_SMTP_PASSWORD", ""), smtp_starttls=tls == "true",
                    github_login_enabled=github_enabled == "true",
                    github_client_id=os.getenv("CRUCIBLE_GITHUB_CLIENT_ID", ""),
                    github_client_secret=os.getenv("CRUCIBLE_GITHUB_CLIENT_SECRET", ""))

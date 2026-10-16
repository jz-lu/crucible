"""Configuration. Local convenience must not silently become production defaults."""
from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret: bytes
    origins: tuple[str, ...] = ("http://127.0.0.1:8000", "http://localhost:8000")
    production: bool = False
    rate_limits: bool = True
    session_seconds: int = 86400
    reviewer_ids: tuple[str, ...] = ()
    allow_self_review: bool = False

    def __post_init__(self):
        if type(self.allow_self_review) is not bool or (self.production and self.allow_self_review):
            raise RuntimeError("Self-review is available only through an explicit development setting.")

    @property
    def cookie_name(self):
        return "__Host-crucible" if self.production else "crucible_session"

    @property
    def hosts(self):
        return list({urlparse(origin).hostname for origin in self.origins})


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
    return Settings(database_url=database_url, secret=secret, origins=origins, production=production,
                    reviewer_ids=reviewers, allow_self_review=self_review == "true")

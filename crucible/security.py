"""Password hashing and purpose-separated verifiers; never persist raw credentials."""
import hashlib
import hmac
import json
import secrets
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
DUMMY_PASSWORD_HASH = passwords.hash(secrets.token_urlsafe(32))


def verify_password(encoded, candidate):
    try:
        return passwords.verify(encoded or DUMMY_PASSWORD_HASH, candidate)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def mac(secret, purpose, value):
    return hmac.new(secret, purpose.encode("ascii") + b"\x00" + value, hashlib.sha256).hexdigest()


def target_mac(secret, challenge_id, target):
    return mac(secret, "crucible-answer-v1", challenge_id.encode("ascii") + b"\x00" + target)


def payload_mac(secret, value):
    return mac(secret, "crucible-payload-v1", json.dumps(value, sort_keys=True, separators=(",", ":")).encode())

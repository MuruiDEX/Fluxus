"""Password hashing — Argon2id only, no custom crypto.

Uses argon2-cffi defaults (Argon2id, OWASP-suitable parameters) plus
transparent rehash on login when parameters evolve. Verification failures
(VerifyMismatchError AND InvalidHash) are indistinguishable to callers —
both mean "wrong password", never leaked.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHash, AttributeError):
        return True

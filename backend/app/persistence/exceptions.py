"""Repository-level domain errors.

These map to the contract error envelope at the API boundary (Stage 5+):
DanglingReferenceError -> 422 dangling_reference,
InvalidIdError / InvalidEnumError -> 422 validation_error/invalid_enum.
Raised here so integrity rules live in one place, not in routers.
"""

import re
import uuid

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")


class PersistenceError(Exception):
    """Base for all persistence-layer domain errors."""


class DanglingReferenceError(PersistenceError):
    """A cross-entity reference points at a missing or out-of-scope row."""


class InvalidIdError(PersistenceError):
    """An ID violates the contract charset/length rules."""


class InvalidEnumError(PersistenceError):
    """A status/category literal is not part of the frozen contract set."""


class ImmutableFieldError(PersistenceError):
    """Attempt to change an immutable field (e.g. marketplace slug)."""


class AgentDisabledError(PersistenceError):
    """An operation requires an active (hired) agent, but the scoped row is
    disabled. Maps to 422 `agent_disabled` at the API boundary."""


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise InvalidIdError(f"Invalid id: {value!r}")
    return value


def new_id() -> str:
    """Server-generated id inside the contract charset (uuid4 hex)."""
    return uuid.uuid4().hex

"""Encrypted-at-rest credentials and action payloads.

Fernet (AES-128-CBC + HMAC-SHA256, versioned) is used for every secret that
touches the database: OAuth access/refresh tokens, the short-lived PKCE
verifier and gated message payloads. Keys come only from
`APP_INTEGRATION_ENCRYPTION_KEY`.

Accepted forms:
- a Fernet key (32 bytes, urlsafe base64 — generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`);
- any sufficiently long passphrase, which is derived with SHA-256.

A missing/invalid key never silently stores plaintext: writes fail with
`credentials_unavailable`, and the provider list reports `configured: false`.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.integrations.types import IntegrationCredentialsUnavailable

_MIN_PASSPHRASE_CHARS = 16


class CredentialCipher:
    def __init__(self, key: str | None) -> None:
        self._fernet: Fernet | None = None
        raw = (key or "").strip()
        if not raw:
            return
        try:
            self._fernet = Fernet(raw.encode("ascii"))
            return
        except (ValueError, UnicodeEncodeError):
            pass
        if len(raw) < _MIN_PASSPHRASE_CHARS:
            return
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise IntegrationCredentialsUnavailable(
                "Credential encryption is not configured on the server."
            )
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if self._fernet is None:
            raise IntegrationCredentialsUnavailable(
                "Credential encryption is not configured on the server."
            )
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise IntegrationCredentialsUnavailable(
                "Stored credentials could not be decrypted; reconnect the account."
            ) from exc


def cipher_for(settings: object) -> CredentialCipher:
    key = getattr(settings, "integration_encryption_key", None)
    return CredentialCipher(key if isinstance(key, str) else None)


__all__ = ["CredentialCipher", "cipher_for"]

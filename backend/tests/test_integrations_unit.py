"""External integration unit tests — no network, no database.

Covers the security-critical primitives: credential encryption, OAuth scope
policy, grant resolution, Gmail input validation / error mapping, bounded
retries for safe reads and the absolute no-retry rule for external side
effects.
"""

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet

from app.capabilities.registry import resolve_grants
from app.core.config import Settings
from app.integrations.crypto import CredentialCipher
from app.integrations.providers.gmail import (
    GMAIL,
    SCOPE_COMPOSE,
    SCOPE_READONLY,
    build_raw_message,
    validate_message,
)
from app.integrations.types import (
    IntegrationAmbiguous,
    IntegrationAuthError,
    IntegrationCredentialsUnavailable,
    IntegrationError,
    IntegrationScopeMissing,
)
from app.main import create_app  # noqa: F401 - composes provider registrations

KEY = Fernet.generate_key().decode()


def _settings(**over: object) -> Settings:
    data: dict = {
        "environment": "test",
        "integration_encryption_key": KEY,
        "google_oauth_client_id": "client-id.apps.googleusercontent.com",
        "google_oauth_client_secret": "client-secret",
        "integration_http_max_retries": 1,
    }
    data.update(over)
    return Settings(**data)


def _message(**over: object) -> dict:
    data = {
        "to": ["alex@example.com"],
        "subject": "Launch update",
        "body": "Hello Alex,\n\nHere is the update.",
    }
    data.update(over)
    return data


# ---------------------------------------------------------------- encryption


def test_cipher_roundtrip_and_missing_key_fails_closed() -> None:
    cipher = CredentialCipher(KEY)
    assert cipher.configured is True
    token = "ya29.a0Af-secret-token"
    stored = cipher.encrypt(token)
    assert stored != token and token not in stored
    assert cipher.decrypt(stored) == token

    missing = CredentialCipher(None)
    assert missing.configured is False
    with pytest.raises(IntegrationCredentialsUnavailable):
        missing.encrypt(token)

    short = CredentialCipher("too-short")
    assert short.configured is False


def test_cipher_accepts_a_long_passphrase_and_rejects_wrong_key() -> None:
    passphrase = "a-long-operator-passphrase-for-tests"
    cipher = CredentialCipher(passphrase)
    stored = cipher.encrypt("secret")
    assert CredentialCipher(passphrase).decrypt(stored) == "secret"
    with pytest.raises(IntegrationCredentialsUnavailable):
        CredentialCipher(Fernet.generate_key().decode()).decrypt(stored)


# ------------------------------------------------------------ scope policy


def test_gmail_requests_read_scope_by_default_and_compose_only_when_asked() -> None:
    settings = _settings()
    read_only = GMAIL.requested_scopes(settings, include_writes=False)
    assert read_only == [SCOPE_READONLY]

    with_writes = GMAIL.requested_scopes(settings, include_writes=True)
    assert SCOPE_READONLY in with_writes and SCOPE_COMPOSE in with_writes


def test_authorization_url_contains_state_pkce_and_offline_access() -> None:
    settings = _settings()
    url = GMAIL.authorization_url(
        settings, state="state-123", code_challenge="challenge-456", include_writes=False
    )
    parsed = httpx.URL(url)
    params = parse_qs(parsed.query.decode())
    assert parsed.host == "accounts.google.com"
    assert params["state"] == ["state-123"]
    assert params["code_challenge"] == ["challenge-456"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["access_type"] == ["offline"]
    assert params["redirect_uri"] == [settings.integration_oauth_redirect_uri]
    assert params["scope"] == [SCOPE_READONLY]
    assert "client_secret" not in params


# ---------------------------------------------------------------- grants


def test_gmail_alias_grants_reads_but_never_writes() -> None:
    granted = {spec.id for spec in resolve_grants(["Gmail"], {})}
    assert granted == {
        "gmail.messages.list",
        "gmail.messages.get",
        "gmail.threads.get",
        "gmail.labels.list",
    }


def test_gmail_writes_require_an_explicit_alias_and_deny_list_wins() -> None:
    granted = {spec.id for spec in resolve_grants(["Gmail", "gmail send", "gmail drafts"], {})}
    assert "gmail.messages.send" in granted and "gmail.drafts.create" in granted

    denied = {
        spec.id
        for spec in resolve_grants(
            ["Gmail", "gmail send"], {"denied": ["gmail send", "gmail drafts"]}
        )
    }
    assert "gmail.messages.send" not in denied and "gmail.drafts.create" not in denied
    assert "gmail.messages.list" in denied


def test_unrelated_email_profile_grants_no_gmail_capability() -> None:
    assert resolve_grants(["Email", "Read Email"], {}) == []
    assert resolve_grants(["Email"], {"write": ["Send email"]}) == []


# ---------------------------------------------------------- input validation


def test_message_validation_accepts_a_normal_message() -> None:
    message = validate_message(_message())
    assert message["to"] == ["alex@example.com"]
    assert message["cc"] == [] and message["bcc"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"to": ["not-an-email"]},
        {"to": []},
        {"subject": ""},
        {"subject": "Hi\r\nBcc: evil@example.com"},
        {"subject": "x" * 201},
        {"body": ""},
        {"body": "x" * 20_001},
        {"to": [f"a{i}@example.com" for i in range(21)]},
        {"cc": ["bad address@example.com"]},
    ],
)
def test_message_validation_rejects_unsafe_or_unbounded_input(patch: dict) -> None:
    with pytest.raises(IntegrationError) as excinfo:
        validate_message(_message(**patch))
    assert excinfo.value.code == "invalid_arguments"


def test_raw_message_is_rfc822_with_headers_and_body() -> None:
    raw = build_raw_message(validate_message(_message(cc=["cc@example.com"])))
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    text = decoded.decode("utf-8", errors="replace")
    assert "To: alex@example.com" in text
    assert "Cc: cc@example.com" in text
    assert "Subject: Launch update" in text
    assert "Here is the update." in text


# ------------------------------------------------------- provider wire errors


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    monkeypatch.setattr(
        GMAIL,
        "client_factory",
        lambda _settings: httpx.AsyncClient(transport=transport),
    )
    return calls


async def test_list_messages_is_bounded_and_marked_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_snippet = "i" * 600

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/messages")
        assert request.headers["authorization"] == "Bearer access-1"
        return httpx.Response(
            200,
            json={
                "messages": [
                    {"id": "m_1", "threadId": "th_1", "snippet": long_snippet},
                ],
                "resultSizeEstimate": 1,
            },
        )

    _mock(monkeypatch, handler)
    result = await GMAIL.execute(
        _settings(),
        capability="gmail.messages.list",
        access_token="access-1",
        arguments={"max_results": 999, "query": "from:alex"},
    )
    assert result.data["untrusted"] is True
    assert result.data["messages"][0]["id"] == "m_1"
    assert len(result.data["messages"][0]["snippet"]) == 500


async def test_safe_read_retries_a_rate_limit_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {}})
        return httpx.Response(200, json={"labels": []})

    calls = _mock(monkeypatch, handler)
    result = await GMAIL.execute(
        _settings(),
        capability="gmail.labels.list",
        access_token="access-1",
        arguments={},
    )
    assert result.data["labels"] == []
    assert len(calls) == 2


async def test_external_side_effect_is_never_retried_and_reports_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock(monkeypatch, lambda _request: httpx.Response(503, json={"error": {}}))
    with pytest.raises(IntegrationAmbiguous):
        await GMAIL.execute(
            _settings(),
            capability="gmail.messages.send",
            access_token="access-1",
            arguments=validate_message(_message()),
            side_effect=True,
        )
    assert len(calls) == 1


async def test_unauthorized_and_missing_scope_map_to_safe_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock(monkeypatch, lambda _request: httpx.Response(401, json={"error": {}}))
    with pytest.raises(IntegrationAuthError):
        await GMAIL.execute(
            _settings(),
            capability="gmail.labels.list",
            access_token="expired",
            arguments={},
        )

    _mock(
        monkeypatch,
        lambda _request: httpx.Response(403, json={"error": {"code": "insufficientPermissions"}}),
    )
    with pytest.raises(IntegrationScopeMissing):
        await GMAIL.execute(
            _settings(),
            capability="gmail.messages.send",
            access_token="access-1",
            arguments=validate_message(_message()),
            side_effect=True,
        )


async def test_token_exchange_keeps_refresh_token_and_verified_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code_verifier"] == ["verifier"]
            return httpx.Response(
                200,
                json={
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                    "scope": f"{SCOPE_READONLY} {SCOPE_COMPOSE}",
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(200, json={"emailAddress": "Owner@Example.com"})

    _mock(monkeypatch, handler)
    bundle, account = await GMAIL.exchange_code(
        _settings(), code="code-1", code_verifier="verifier", redirect_uri="http://cb"
    )
    assert bundle.access_token == "access-2"
    assert bundle.refresh_token == "refresh-2"
    assert SCOPE_COMPOSE in bundle.scopes
    assert account.external_account_id == "owner@example.com"

    # Nothing secret may be serialized into an API/result payload.
    assert "access-2" not in json.dumps({"account": account.display_name})

"""Integration lifecycle service.

Owns OAuth state/PKCE, encrypted token storage, token refresh, revocation and
the execution of human-approved external actions. Provider modules stay
isolated behind `app.integrations.registry`.

Safety rules encoded here:

- The OAuth state is random, single-use, expiring and bound to the user who
  started the flow; only its SHA-256 hash is stored.
- Tokens and message payloads are encrypted before any repository write.
- A connection is usable only while `connected` and only with the scopes the
  capability needs.
- External side effects execute ONLY from an approved approval, in their own
  transactions. Ambiguous outcomes are recorded as `unknown`, never retried.
"""

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.integrations.crypto import CredentialCipher, cipher_for
from app.integrations.registry import get_provider, list_providers
from app.integrations.repositories import (
    IntegrationActionRepository,
    IntegrationConnectionRepository,
    IntegrationOAuthStateRepository,
)
from app.integrations.types import (
    IntegrationAmbiguous,
    IntegrationAuthError,
    IntegrationError,
    IntegrationNotConfigured,
    IntegrationProviderError,
)
from app.persistence.enums import IntegrationActionStatus, IntegrationConnectionStatus
from app.persistence.models.integration import IntegrationAction, IntegrationConnection
from app.persistence.repositories.concrete import (
    ActivityRepository,
    ApprovalRepository,
    MembershipRepository,
    UserRepository,
)

log = get_logger(__name__)

connections = IntegrationConnectionRepository()
oauth_states = IntegrationOAuthStateRepository()
actions = IntegrationActionRepository()
approvals = ApprovalRepository()
activity = ActivityRepository()
users = UserRepository()
memberships = MembershipRepository()


def capability_scopes(provider: Any, capability_id: str) -> tuple[str, ...]:
    """Scopes a provider requires for one of its capabilities.

    Deriving this from provider metadata keeps the generic layer free of
    vendor scope strings: read capabilities use `read_scopes`, writes use
    `write_scopes` (which providers keep narrow and explicit).
    """
    for capability in provider.spec.capabilities:
        if capability.id != capability_id:
            continue
        scopes = (
            provider.spec.write_scopes if capability.risk == "write" else provider.spec.read_scopes
        )
        return tuple(scopes)
    return ()


def _cipher(settings: object) -> CredentialCipher:
    return cipher_for(settings)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def provider_is_configured(provider: Any, settings: object) -> bool:
    configured = getattr(provider, "configured", None)
    return bool(configured(settings)) if callable(configured) else False


def provider_listing(settings: object) -> list[dict[str, Any]]:
    """Frontend-safe provider catalog. Never includes secrets or tokens."""
    rows: list[dict[str, Any]] = []
    for provider in list_providers():
        missing = provider.missing_configuration(settings)
        rows.append(
            {
                "id": provider.spec.id,
                "name": provider.spec.name,
                "description": provider.spec.description,
                "docsUrl": provider.spec.docs_url,
                "readScopes": list(provider.spec.read_scopes),
                "writeScopes": list(provider.spec.write_scopes),
                "capabilities": [
                    {
                        "id": capability.id,
                        "title": capability.title,
                        "risk": capability.risk,
                    }
                    for capability in provider.spec.capabilities
                ],
                "configured": not missing,
                "missingConfiguration": missing,
            }
        )
    return rows


def connection_wire(connection: IntegrationConnection) -> dict[str, Any]:
    """Owner-facing connection shape. Tokens never cross this boundary."""
    provider = get_provider(connection.provider)
    granted = set(connection.scopes or [])
    capabilities = []
    if provider is not None:
        for capability in provider.spec.capabilities:
            required = capability_scopes(provider, capability.id)
            capabilities.append(
                {
                    "id": capability.id,
                    "title": capability.title,
                    "risk": capability.risk,
                    "granted": all(scope in granted for scope in required),
                }
            )
    expires_at = _aware(connection.token_expires_at)
    return {
        "id": connection.id,
        "provider": connection.provider,
        "providerName": provider.spec.name if provider is not None else connection.provider,
        "externalAccountId": connection.external_account_id,
        "displayName": connection.display_name,
        "status": connection.status,
        "health": _health(connection),
        "scopes": list(connection.scopes or []),
        "capabilities": capabilities,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "lastRefreshedAt": (
            _aware(connection.last_refreshed_at).isoformat()
            if connection.last_refreshed_at is not None
            else None
        ),
        "lastError": connection.last_error,
        "canWrite": bool(
            provider is not None
            and provider.spec.write_scopes
            and all(scope in granted for scope in provider.spec.write_scopes)
        ),
    }


def _health(connection: IntegrationConnection) -> str:
    if connection.status == IntegrationConnectionStatus.REVOKED.value:
        return "revoked"
    if connection.status == IntegrationConnectionStatus.ERROR.value:
        return "needs_reconnect"
    if connection.last_error:
        return "degraded"
    if not connection.scopes:
        return "needs_reconnect"
    return "ok"


async def start_oauth(
    session: AsyncSession,
    settings: object,
    *,
    business_id: str,
    user_id: str,
    provider_id: str,
    include_writes: bool,
) -> dict[str, Any]:
    provider = get_provider(provider_id)
    if provider is None:
        raise IntegrationError("Unknown integration provider.", code="unknown_provider")
    if not provider_is_configured(provider, settings):
        raise IntegrationNotConfigured(
            "This integration is not configured on the server; contact the operator."
        )
    cipher = _cipher(settings)
    if not cipher.configured:
        raise IntegrationNotConfigured(
            "Credential encryption is not configured on the server; contact the operator."
        )

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    ttl_s = int(getattr(settings, "integration_oauth_state_ttl_s", 600))
    expires_at = _now() + timedelta(seconds=ttl_s)
    scopes = provider.requested_scopes(settings, include_writes=include_writes)
    redirect_uri = str(getattr(settings, "integration_oauth_redirect_uri", ""))
    await oauth_states.create(
        session,
        business_id,
        {
            "user_id": user_id,
            "provider": provider_id,
            "state_hash": hashlib.sha256(state.encode("utf-8")).hexdigest(),
            "code_verifier_encrypted": cipher.encrypt(verifier),
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "expires_at": expires_at,
        },
    )
    authorization_url = provider.authorization_url(
        settings,
        state=state,
        code_challenge=challenge,
        include_writes=include_writes,
    )
    return {
        "authorizationUrl": authorization_url,
        "provider": provider_id,
        "scopes": scopes,
        "expiresAt": expires_at.isoformat(),
    }


async def complete_oauth(
    session: AsyncSession,
    settings: object,
    *,
    user_id: str,
    state: str,
    code: str,
    error: str,
) -> IntegrationConnection:
    """Validate a callback and store the connection in the caller's transaction.

    The state row supplies business/provider; the authenticated user must be
    the one who started the flow. Nothing is written unless every check passes.
    """
    if error:
        raise IntegrationError(
            "The external authorization was denied or cancelled.", code="authorization_denied"
        )
    if not state or not code:
        raise IntegrationError("The authorization response is incomplete.", code="invalid_state")
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    row = await oauth_states.consume_by_hash(session, user_id, state_hash, now=_now())
    if row is None:
        raise IntegrationError(
            "This authorization link is invalid or has expired; start again.",
            code="invalid_state",
        )
    if not await memberships.lock_owner(session, user_id, row.business_id):
        raise IntegrationError("This authorization is no longer valid.", code="invalid_state")
    provider = get_provider(row.provider)
    if provider is None or not provider_is_configured(provider, settings):
        raise IntegrationNotConfigured(
            "This integration is not configured on the server; contact the operator."
        )
    cipher = _cipher(settings)
    verifier = cipher.decrypt(row.code_verifier_encrypted)
    bundle, account = await provider.exchange_code(
        settings,
        code=code,
        code_verifier=verifier,
        redirect_uri=row.redirect_uri,
    )
    if not bundle.scopes:
        bundle.scopes = list(row.scopes or [])
    connection = await connections.upsert_connected(
        session,
        row.business_id,
        provider=provider.spec.id,
        account=account,
        bundle=bundle,
        encrypted_access_token=cipher.encrypt(bundle.access_token),
        encrypted_refresh_token=(
            cipher.encrypt(bundle.refresh_token) if bundle.refresh_token else None
        ),
        connected_by_user_id=user_id,
    )
    user = await users.get(session, user_id)
    actor = user.email if user is not None else user_id
    await activity.append(
        session,
        row.business_id,
        {
            "agent_name": actor,
            "kind": "system",
            "text": f"{provider.spec.name} account {account.display_name} connected by {actor}.",
        },
    )
    return connection


async def disconnect(
    session: AsyncSession,
    settings: object,
    *,
    business_id: str,
    connection_id: str,
    actor: str,
) -> bool:
    """Best-effort external revoke, then a local wipe that always wins."""
    connection = await connections.get(session, business_id, connection_id)
    if connection is None:
        return False
    provider = get_provider(connection.provider)
    token = ""
    cipher = _cipher(settings)
    if connection.encrypted_access_token and cipher.configured:
        try:
            token = cipher.decrypt(connection.encrypted_access_token)
        except IntegrationError:
            token = ""
    if token and provider is not None:
        try:
            await provider.revoke(settings, token)
        except IntegrationError:
            # Local revocation must not depend on the provider being reachable.
            log.warning(
                "integration revoke failed; wiping local credentials",
                extra={"context": {"provider": connection.provider}},
            )
    await connections.revoke(session, business_id, connection_id)
    await activity.append(
        session,
        business_id,
        {
            "agent_name": actor,
            "kind": "system",
            "text": (
                f"{connection.provider} account {connection.display_name} disconnected by {actor}."
            ),
        },
    )
    return True


def _required_scopes(provider: Any, capability: str) -> tuple[str, ...]:
    return capability_scopes(provider, capability)


async def resolve_capability_connection(
    session: AsyncSession,
    *,
    business_id: str,
    provider: str,
    capability: str,
    connection_id: str | None,
) -> IntegrationConnection:
    """Widen a capability failure into a safe, model-facing error."""
    implementation = get_provider(provider)
    if implementation is None or not _required_scopes(implementation, capability):
        raise IntegrationError("Unsupported capability.", code="not_authorized")
    if connection_id is not None:
        connection = await connections.usable_for_capability(
            session, business_id, provider=provider, connection_id=connection_id
        )
        if connection is None:
            raise IntegrationError(
                "That connected account is unavailable for this business.",
                code="not_connected",
            )
    else:
        count = await connections.count_connected(session, business_id, provider=provider)
        if count == 0:
            raise IntegrationError(
                "No connected account is available. "
                "Ask the owner to connect the integration first.",
                code="not_connected",
            )
        if count > 1:
            raise IntegrationError(
                "Several accounts are connected; pass connection_id to choose one.",
                code="connection_required",
            )
        connection = await connections.usable_for_capability(
            session, business_id, provider=provider
        )
        if connection is None:  # pragma: no cover - count/usable race
            raise IntegrationError("The connected account is unavailable.", code="not_connected")
    granted = set(connection.scopes or [])
    missing = [
        scope for scope in _required_scopes(implementation, capability) if scope not in granted
    ]
    if missing:
        raise IntegrationError(
            "The connected account lacks the permission required for this action; "
            "reconnect it with the requested scope.",
            code="scope_missing",
        )
    return connection


async def ensure_access_token(
    session: AsyncSession, settings: object, connection: IntegrationConnection
) -> str:
    """Return a live access token, refreshing within this transaction if needed."""
    cipher = _cipher(settings)
    expires_at = _aware(connection.token_expires_at)
    if connection.encrypted_access_token and (
        expires_at is None or expires_at > _now() + timedelta(seconds=30)
    ):
        return cipher.decrypt(connection.encrypted_access_token)
    provider = get_provider(connection.provider)
    if provider is None:
        raise IntegrationProviderError("The integration provider is unavailable.")
    if not connection.encrypted_refresh_token:
        await connections.mark_error(
            session,
            connection.business_id,
            connection.id,
            "The account must be reconnected to restore access.",
        )
        raise IntegrationAuthError("The account must be reconnected to restore access.")
    refresh_token = cipher.decrypt(connection.encrypted_refresh_token)
    try:
        bundle = await provider.refresh(settings, refresh_token)
    except IntegrationAuthError:
        await connections.revoke(session, connection.business_id, connection.id)
        raise
    except IntegrationError as exc:
        await connections.note_error(session, connection.business_id, connection.id, exc.message)
        raise
    await connections.mark_connected(
        session,
        connection.business_id,
        connection.id,
        bundle=bundle,
        encrypted_access_token=cipher.encrypt(bundle.access_token),
        encrypted_refresh_token=(
            cipher.encrypt(bundle.refresh_token) if bundle.refresh_token else None
        ),
    )
    return bundle.access_token


def _action_audit_text(action: IntegrationAction, status: str) -> str:
    return f"External action {action.capability} ({action.id}) → {status}"


async def _finish_action(
    session: AsyncSession,
    action: IntegrationAction,
    *,
    status: IntegrationActionStatus,
    result: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    await actions.finish(
        session,
        action.business_id,
        action.id,
        status=status,
        result=result,
        error_code=error_code,
        error_message=error_message,
    )
    await activity.append(
        session,
        action.business_id,
        {
            "project_id": action.project_id,
            "agent_name": "Integration",
            "kind": "system",
            "text": _action_audit_text(action, status.value),
        },
    )


async def execute_action_for_approval(
    factory: async_sessionmaker[AsyncSession],
    settings: object,
    *,
    business_id: str,
    approval_id: str,
    user_id: str,
) -> str:
    """Run a pending external action after its approval was granted.

    Returns the terminal status: `succeeded`, `failed`, `unknown`, `rejected`
    or `skipped` (no action / already handled). Never raises for a provider
    outcome: the approval decision is already durable and must not be undone.
    """
    async with factory() as session:
        async with session.begin():
            if not await memberships.lock_owner(session, user_id, business_id):
                return "skipped"
            approval = await approvals.get(session, business_id, approval_id)
            if approval is None or approval.status != "approved":
                return "skipped"
            action = await actions.claim(session, business_id, approval_id)
            if action is None:
                return "skipped"
            connection = await connections.lock_connection(
                session, business_id, action.connection_id
            )
            if (
                connection is None
                or connection.status != IntegrationConnectionStatus.CONNECTED.value
            ):
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code="connection_unavailable",
                    error_message="The connected account is unavailable; reconnect it.",
                )
                return "failed"
            provider = get_provider(connection.provider)
            if provider is None:
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code="provider_unavailable",
                    error_message="The integration provider is unavailable.",
                )
                return "failed"
            try:
                payload = json.loads(_cipher(settings).decrypt(action.payload_encrypted))
                access_token = await ensure_access_token(session, settings, connection)
            except IntegrationError as exc:
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                return "failed"
            if not isinstance(payload, dict):
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code="invalid_payload",
                    error_message="The stored action payload is invalid.",
                )
                return "failed"
            snapshot = {
                "capability": action.capability,
                "provider": provider,
                "encrypted_access_token": connection.encrypted_access_token,
                "payload": payload,
            }
            await activity.append(
                session,
                business_id,
                {
                    "project_id": action.project_id,
                    "agent_name": "Integration",
                    "kind": "system",
                    "text": f"Owner {user_id} authorized dispatch of external action {action.id}.",
                },
            )

    async with factory() as session:
        async with session.begin():
            # Keep the durable claim across crashes. The row lock fences
            # reconciliation against a dispatch whose provider call is live.
            owner = await memberships.lock_owner(session, user_id, business_id)
            action = await actions.get_for_approval(session, business_id, approval_id, lock=True)
            if action is None or action.status != IntegrationActionStatus.EXECUTING.value:
                return "skipped"
            if not owner:
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code="owner_required",
                    error_message="The authorizing user is no longer a business owner.",
                )
                return "failed"
            connection = await connections.lock_connection(
                session, business_id, action.connection_id
            )
            if (
                connection is None
                or connection.status != IntegrationConnectionStatus.CONNECTED.value
                or connection.encrypted_access_token != snapshot["encrypted_access_token"]
                or not set(_required_scopes(snapshot["provider"], action.capability)).issubset(
                    connection.scopes or []
                )
            ):
                await _finish_action(
                    session,
                    action,
                    status=IntegrationActionStatus.FAILED,
                    error_code="connection_changed",
                    error_message="The authorized connection changed before dispatch.",
                )
                return "failed"
            return await _dispatch_action(session, action, settings, snapshot, access_token)


async def _dispatch_action(
    session: AsyncSession,
    action: IntegrationAction,
    settings: object,
    snapshot: dict[str, Any],
    access_token: str,
) -> str:
    status = IntegrationActionStatus.FAILED
    result: dict[str, Any] | None = None
    error_code = "provider_error"
    error_message = "The provider rejected the action."
    try:
        provider_result = await snapshot["provider"].execute(
            settings,
            capability=snapshot["capability"],
            access_token=access_token,
            arguments=snapshot["payload"],
            side_effect=True,
        )
        if not provider_result.external_id:
            raise IntegrationAmbiguous("The provider returned no action confirmation.")
        status = IntegrationActionStatus.SUCCEEDED
        result = provider_result.data
        error_code = ""
        error_message = ""
    except IntegrationAmbiguous as exc:
        status = IntegrationActionStatus.UNKNOWN
        error_code = exc.code
        error_message = exc.message
    except IntegrationError as exc:
        status = IntegrationActionStatus.FAILED
        error_code = exc.code
        error_message = exc.message
    except Exception:
        status = IntegrationActionStatus.UNKNOWN
        error_code = "provider_result_unknown"
        error_message = "The provider outcome could not be confirmed."
        log.warning("external action failed unexpectedly")

    await _finish_action(
        session,
        action,
        status=status,
        result=result,
        error_code=error_code,
        error_message=error_message,
    )
    return status.value


async def reconcile_action(
    session: AsyncSession, business_id: str, approval_id: str
) -> IntegrationAction | None:
    """Close a stranded dispatch conservatively, without calling Gmail."""
    action = await actions.get_for_approval(session, business_id, approval_id, lock=True)
    if action is not None and action.status == IntegrationActionStatus.EXECUTING.value:
        await _finish_action(
            session,
            action,
            status=IntegrationActionStatus.UNKNOWN,
            error_code="dispatch_interrupted",
            error_message="Dispatch was interrupted and the provider outcome is unconfirmed.",
        )
    return action


async def reject_action(session: AsyncSession, action: IntegrationAction) -> None:
    if await actions.mark_rejected(session, action.business_id, action.approval_id):
        await activity.append(
            session,
            action.business_id,
            {
                "project_id": action.project_id,
                "agent_name": "Integration",
                "kind": "system",
                "text": _action_audit_text(action, "rejected"),
            },
        )


async def handle_approval_decision(
    factory: async_sessionmaker[AsyncSession],
    settings: object,
    *,
    business_id: str,
    approval_id: str,
    status: str,
    user_id: str,
) -> str:
    """Post-decision hook. Failures are audited, never re-raised."""
    try:
        if status in {"approved", "already_approved"}:
            return await execute_action_for_approval(
                factory, settings, business_id=business_id, approval_id=approval_id, user_id=user_id
            )
        if status in {"rejected", "already_rejected"}:
            async with factory() as session:
                async with session.begin():
                    if not await memberships.lock_owner(session, user_id, business_id):
                        return "skipped"
                    approval = await approvals.get(session, business_id, approval_id)
                    action = await actions.get_for_approval(session, business_id, approval_id)
                    if action is None or approval is None or approval.status != "rejected":
                        return "skipped"
                    await reject_action(session, action)
            return "rejected"
    except Exception:  # pragma: no cover - defensive boundary
        log.warning(
            "approval integration hook failed",
            extra={"context": {"approval_id": approval_id}},
        )
    return "skipped"


__all__ = [
    "capability_scopes",
    "complete_oauth",
    "connection_wire",
    "disconnect",
    "ensure_access_token",
    "execute_action_for_approval",
    "handle_approval_decision",
    "provider_is_configured",
    "provider_listing",
    "resolve_capability_connection",
    "start_oauth",
]

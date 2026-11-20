# External Integrations (Stage 19, first slice: Gmail)

The integration engine adds real external services to the existing Agent
Capability Engine. It is **one execution path**: a model still only requests a
capability, and the host still authorizes, validates, audits, and gates it.
There is no second tool loop and no provider-specific shortcut around grants,
leases, approvals, or usage accounting.

Gmail is the first real provider. It uses Google OAuth 2.0 + the official Gmail
API only. Other providers can be added behind the same registry without changing
the capability engine, API surface, or UI.

## Architecture

```
model requests capability
        │
        ▼
Agent Capability Engine (unchanged)
  grants / deny-list, JSON-schema validation, lease ownership,
  transaction boundaries, usage reservation + settlement, audit
        │
        ├── gmail.* read ─────────────────────────────► Gmail API
        │     connection scope check + token refresh        (read-only)
        │
        └── gmail.* write ──► pending human approval
                               + encrypted integration_actions row
                                      │
                              owner approves (Approvals UI/API)
                                      │
                              service.handle_approval_decision
                                      │
                              claim once: pending → executing
                                      │
                              Gmail API side effect (never retried)
                                      │
                              succeeded / failed / unknown
```

Key files:

| Concern | Location |
|---|---|
| Provider-agnostic types, errors | `backend/app/integrations/types.py` |
| Registry / provider specs | `backend/app/integrations/registry.py` |
| OAuth + connection + action lifecycle | `backend/app/integrations/service.py` |
| Scoped repositories (business-filtered) | `backend/app/integrations/repositories.py` |
| Fernet credential encryption | `backend/app/integrations/crypto.py` |
| Gmail OAuth/API client | `backend/app/integrations/providers/gmail.py` |
| Gmail capability specs + handlers | `backend/app/integrations/gmail_capabilities.py` |
| Capability registration | `backend/app/integrations/bootstrap.py` |
| API router | `backend/app/api/v1/integrations.py` |
| Persistence / migration | `backend/app/persistence/models/integration.py`, `backend/alembic/versions/0013_integrations.py` |
| Frontend page | `frontend/app/(app)/integrations/page.tsx` |
| Frontend live/action stores | `frontend/lib/store/integrations.ts` |

## Security and permission invariants

- **Opt-in only.** An agent can use Gmail only when the capability id (or one of
  its explicit aliases) is in its `tools` and permitted by its `permissions`.
  Generic `Email` / `Read Email` does not grant Gmail. A profile with no granted
  capability keeps the legacy single-call path. Denying `gmail` blocks the read
  aliases; write aliases are deliberately not generic, so deny `gmail send` /
  `gmail drafts` explicitly to block writes (writes are approval-gated anyway).
- **Read vs write scope.** A read-only connection requests
  `gmail.readonly`. `gmail.compose` is requested only when the owner explicitly
  connects or upgrades with write access. Connections that did not receive every
  write scope are reported as `canWrite: false` and write capabilities fail
  closed.
- **Writes never happen inside the model-requested capability.** A write
  capability validates addresses/subject/body, creates a **real pending
  approval**, and stores the exact action payload encrypted. Gmail is called only
  after a human approves. A rejected approval can never execute. The approval
  card intentionally shows a bounded plaintext preview (recipients, subject, and
  at most 1,200 characters of the body) so the member knows exactly what they
  are authorizing; the full payload stays encrypted and audit rows never include
  message content.
- **Idempotent approval execution.** The action row is claimed
  `pending → executing` once. Repeated decisions return the recorded outcome.
  Network loss, timeouts, and ambiguous 5xx results become `unknown` and are
  **never retried automatically** — a human can inspect/reconcile.
- **Tokens never reach models.** The wire responses contain no access token,
  refresh token, authorization code, or PKCE verifier. Capability results contain
  only Gmail ids/headers/bounded bodies plus `untrusted: true`.
- **Encrypted at rest.** Access tokens, refresh tokens, action payloads, and PKCE
  verifiers are Fernet-encrypted with `APP_INTEGRATION_ENCRYPTION_KEY`.
  Disconnect wipes stored tokens and marks the row revoked.
- **OAuth CSRF/replay defense.** The `state` is random, stored only as SHA-256,
  single-use, expires after `APP_INTEGRATION_OAUTH_STATE_TTL_S`, and bound to the
  user + business + provider. PKCE (`S256`) is mandatory. The callback
  re-authenticates the session and re-checks membership.
- **Tenant isolation.** Every connection/action lookup filters `business_id`.
  Managing connections (connect/disconnect) is owner-only; viewing requires
  membership. Foreign ids are a uniform 404.
- **Untrusted email content.** Message bodies and thread summaries are data.
  Instructions inside an email must never override system instructions,
  permissions, or approval requirements. Prompt boundaries and capability
  results are bounded by `APP_EXECUTION_CAPABILITY_RESULT_CHARS`.
- **Audit.** Connect, refresh failure, disconnect, approval-gated execution,
  success/failure/unknown, and rejections write activity/audit records without
  logging credentials or full message bodies.

## Gmail capabilities

Connection argument: every capability accepts an optional `connection_id` when
more than one Gmail account is connected. Omitting it resolves the single
connected account; an ambiguous or missing connection fails closed.

| Capability | Risk | Scope | Behavior |
|---|---|---|---|
| `gmail.messages.list` | read | `gmail.readonly` | Lists recent messages (`query`, `max_results` ≤ 50, `page_token`). Returns ids, thread ids, label ids, and snippets only. |
| `gmail.messages.get` | read | `gmail.readonly` | Returns headers and a bounded plain-text body (`include_body`). Body is untrusted data. |
| `gmail.threads.get` | read | `gmail.readonly` | Returns the thread’s message summaries in order. |
| `gmail.labels.list` | read | `gmail.readonly` | Lists up to 100 labels. |
| `gmail.drafts.create` | write | `gmail.compose` | Creates a real draft **only after approval**. `to`/`subject`/`body` required; `cc`/`bcc` optional. |
| `gmail.messages.send` | write | `gmail.compose` | Sends a real message **only after approval**. Same fields and validation. |

Aliases are explicit per capability. Read aliases include `gmail`, `google mail`,
`gmail messages`, `gmail list`, `gmail inbox`, `gmail read`, `gmail message`,
`gmail thread`, `email thread`, `gmail labels`. Write aliases are only
`gmail send`, `send gmail`, `gmail send email`, `gmail drafts`, `gmail draft`,
`create draft`.

Validation limits: at most 20 recipients per address field, 200-character
subject, 20,000-character body, valid single-line addresses, and no header
injection.

## Persistence

Migration `0013_integrations` is additive; no existing table or constraint is
modified.

| Table | Purpose |
|---|---|
| `integration_connections` | One authorized external account per business/provider/account id. Encrypted access + refresh tokens, granted scopes, status (`connected`/`revoked`/`error`), health/error metadata, connecting user. |
| `integration_oauth_states` | Single-use OAuth attempts: SHA-256 state hash, encrypted PKCE verifier, redirect URI, requested scopes, expiry, consumed timestamp. |
| `integration_actions` | Human-gated side effects: encrypted payload, approval/connection references, status (`pending`/`executing`/`succeeded`/`failed`/`rejected`/`unknown`), provider result ids and error code. |

## API endpoints

All business-scoped endpoints require the session cookie, the
`X-Client-Contract: 1` header, and live membership. Mutations also require the
in-memory CSRF header.

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/businesses/{business_id}/integrations/providers` | member | Provider catalog with `configured` + missing server settings. |
| `GET` | `/api/v1/businesses/{business_id}/integrations` | member | Connected accounts for this business only. |
| `GET` | `/api/v1/businesses/{business_id}/integrations/{connection_id}` | member | One connection, business-filtered. |
| `POST` | `/api/v1/businesses/{business_id}/integrations/{provider}/connect` | owner | Starts OAuth (`{"includeWrites": false}` by default) and returns `authorizationUrl`. |
| `DELETE` | `/api/v1/businesses/{business_id}/integrations/{connection_id}` | owner | Revokes + wipes stored credentials. |
| `GET` | `/api/v1/integrations/oauth/callback` | signed-in user | Google redirect target; validates state/PKCE, stores the connection, redirects to the frontend with `integration=connected|error`. |

Missing server configuration returns `503` with `not_configured`; the provider
list still renders with `configured: false` and the missing setting names.
Nothing pretends to be connected.

## Frontend

`/integrations` has two honest modes:

- **Live** (authenticated member business): provider cards with configured/
  unconfigured state, read vs write scope disclosure, connected accounts with
  health/scopes/granted capabilities, last provider error, owner-only
  disconnect (two-step confirm), and OAuth callback feedback. Connect navigates
  to the server-issued authorization URL; no token is ever stored client-side.
- **Demo**: states that integrations require a configured backend and sign-in.
  Connect buttons are disabled; no fake connection is shown.

The page follows the existing component system (`PageHeader`, `Card`, `Badge`,
`Button`, skeletons). URL query parameters from the OAuth callback are read once
and stripped from the address bar.

## Google Cloud setup

1. In **Google Cloud Console → APIs & Services → Library**, enable **Gmail API**.
2. Configure the **OAuth consent screen**:
   - User type: **External** (or **Internal** for a Workspace-only deployment).
   - Add scopes:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/gmail.compose` (only if you want writes)
   - While the app is in **Testing**, add every Google account that will connect
     as a test user. Testing-mode refresh tokens expire after 7 days; publish /
     verify the app for durable production use.
3. Create **Credentials → OAuth client ID → Web application**.
4. Add the exact **Authorized redirect URI**:
   - Local: `http://127.0.0.1:8000/api/v1/integrations/oauth/callback`
   - Production: `https://<your-domain>/api/v1/integrations/oauth/callback`
   Google requires an exact match (scheme, host, port, path). The URI must equal
   `APP_INTEGRATION_OAUTH_REDIRECT_URI`.
5. Put the client id/secret only in server env. Gmail scopes are restricted; a
   public production app needs Google's verification/security assessment for
   the requested scopes. Plan for that before onboarding external users.

### Backend environment

```powershell
# Fernet key (generate once, keep secret, never commit):
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
$env:APP_INTEGRATION_ENCRYPTION_KEY = "<fernet-key>"
$env:APP_GOOGLE_OAUTH_CLIENT_ID = "<google client id>"
$env:APP_GOOGLE_OAUTH_CLIENT_SECRET = "<google client secret>"

# Must exactly match the Google Cloud redirect URI.
$env:APP_INTEGRATION_OAUTH_REDIRECT_URI = "https://app.example.com/api/v1/integrations/oauth/callback"
# Browser destination after the callback (never user-controlled).
$env:APP_INTEGRATION_FRONTEND_URL = "https://app.example.com"

# Optional bounds:
$env:APP_INTEGRATION_OAUTH_STATE_TTL_S = "600"   # 60..3600
$env:APP_INTEGRATION_HTTP_TIMEOUT_S = "8"        # finite, > 0
$env:APP_INTEGRATION_HTTP_MAX_RETRIES = "1"      # 0..3; safe reads/refresh only
$env:APP_EXECUTION_MAX_WRITE_CALLS_PER_RUN = "3" # 0..10
```

With no Google config, the provider card says **Server setup required** and
every connect attempt returns `not_configured`. This is intentional fail-closed
behavior, not a simulated connection.

## Testing

CI never needs real Google credentials:

- `backend/tests/test_integrations_unit.py` — crypto, scopes, grants, Gmail
  request validation, retry/ambiguity classification.
- `backend/tests/test_integrations_api.py` — OAuth start/callback, CSRF/state,
  membership/isolation, read capabilities, approval-gated send/draft, refresh.
- Gmail HTTP is mocked through the provider's `client_factory`
  (`httpx.MockTransport`); no test constructs an unpatched provider.
- `frontend/lib/store/integrations.test.ts` — load/membership gates, late
  response discard, connect/disconnect single-flight, 403/503/network honesty.
- `frontend/lib/wire-manifest.test.ts` pins the integration routes/schemas to
  `docs/contracts/wire-manifest.json`.

Run:

```powershell
cd backend
py -3 -m pytest tests/test_integrations_unit.py tests/test_integrations_api.py `
  --pg-base-url "postgresql://muse@127.0.0.1:55434"

cd ..\frontend
npx vitest run lib/store/integrations.test.ts lib/wire-manifest.test.ts
npm run typecheck
```

## Limitations (Stage 1)

- Gmail is the only provider. The registry and UI are provider-generic, but
  Stripe/Calendar/Telegram/etc. are not implemented and must not be shown as
  connected.
- Gmail reads are intentionally bounded (max 50 list rows, 100 labels, bounded
  body/raw bytes). Attachments, message modification, label writes, trash, and
  history/watch APIs are not exposed.
- Dispatch starts from the owner approval request. If approval committed but the
  action is still `pending`, a current owner can repeat the approve request to
  authorize dispatch. An `executing` action is never dispatched a second time.
- A current owner can call
  `POST /api/v1/businesses/{business_id}/approvals/{approval_id}/reconcile-action`
  with the usual session and CSRF token. It waits for any live dispatch settlement
  and moves a stranded `executing` action to `unknown`. It returns the recorded
  status for other states without contacting Gmail.
- Finalization requires dispatch to be settled or reconciled. Reports for
  projects with external writes are rendered from persisted statuses and include
  failed and uncertain outcomes. These reports do not generate Brain learnings.
- Google app verification and the 7-day testing refresh-token limit are operator
  responsibilities.
- The UI cannot display the caller's role in a business (the session wire carries
  membership ids only), so owner-only actions are enforced by the backend and a
  `403` is surfaced honestly.
- Multi-account per provider is supported by the data model, but capability
  calls must pass `connection_id` when more than one account is connected;
  otherwise resolution fails closed.

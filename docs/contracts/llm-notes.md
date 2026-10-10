# LLM provider layer — notes (Stage 3)

## Interface

`app/llm/base.py::LlmProvider` (Protocol): `complete()` / `stream()`.
Implementations: `app/llm/mock.py::MockProvider` (default, offline),
`app/llm/adapters/openai_compat.py::OpenAICompatProvider` (raw httpx, no
vendor SDK). Construction only via `create_provider(settings)`; default
provider is `mock`, so key-less runs never touch the network.

Shared vocabulary: `app/llm/types.py`. Structured-output validation subset:
`app/llm/validate.py` (object/array/string/number/integer/boolean, required,
properties, items, enum — compliance is never claimed beyond validation).

## Error codes (envelope `error.code`)

| Code | HTTP* | Retry | Meaning |
|---|---|---|---|
| `provider_auth_error` | 401/403 from provider | never | OUR server-side credentials rejected — distinct from user `unauthorized` (contract amendment v1.2) |
| `rate_limited` | 429 | yes | backoff + jitter |
| `provider_timeout` | — (timer) | yes | full per-attempt timeout |
| `provider_error` | 5xx / network / other 4xx | 5xx + network only | 4xx (except 429) never retried |
| `bad_response` | 200 with bad body | never | non-JSON, missing choices, invalid JSON output, schema violation, malformed/truncated SSE |
| `configuration_error` | — | never | unknown provider name, missing key for `openai_compat` |

\* HTTP column is the typical mapping; envelope status follows §6.

`asyncio.CancelledError` is not an error code at all: it propagates unwrapped
(BaseException, not `LlmError`), is never retried, and streams/clients close
in `finally` blocks.

## Limits (defaults, all configurable via `APP_LLM_*`)

- max 2 retries (3 attempts max), backoff `min(8, 0.5·2ⁿ)s + U[0, 0.25)s`;
- per-attempt timeout (default 30s); `attempts` reported in response metadata
  (a retried generation may have executed provider-side with unknown outcome);
- `max_tokens` / `temperature` pass-through; usage tokens echoed when the
  provider reports them; cost only when provider data exists (no billing tables).

## Secrets

Key lives in `APP_LLM_API_KEY` → `Settings` → Authorization header, exactly
one use site. Never in logs, exceptions, details, or API responses
(enforced by `tests/test_llm_secrets.py` with a canary key).

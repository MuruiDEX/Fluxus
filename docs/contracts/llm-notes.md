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
| `rate_limited` | 429 | yes | provider rejection; backoff + jitter |
| `provider_timeout` | — (timer) | connect/pool pre-send only | post-send transport timeout, or the derived total deadline, is ambiguous and held; never retried |
| `provider_error` | 5xx / 408 / network / other 4xx | pre-send network only | 5xx and 408 are ambiguous; other 4xx are final |
| `bad_response` | 200 with bad body | never | non-JSON, missing choices, invalid JSON output, schema violation, malformed/truncated SSE |
| `configuration_error` | — | never | unknown provider name, missing key for `openai_compat` |

\* HTTP column is the typical mapping; envelope status follows §6.

`asyncio.CancelledError` is not an error code at all: it propagates unwrapped
(BaseException, not `LlmError`), is never retried, and streams/clients close
in `finally` blocks.

## Limits (defaults, all configurable via `APP_LLM_*`)

- max 2 retries (3 attempts max) for safe 429 and pre-send network failures,
  backoff `min(8, 0.5·2ⁿ)s + U[0, 0.25)s`; 5xx, HTTP 408 responses and
  post-send transport failures are not retried because processing or billing
  may have occurred;
- an HTTP 408 response is ambiguous and non-retryable: the provider received
  the request but did not answer in time, so it may already have processed and
  billed it. This is NOT a client-side connect/pool timeout (which remains a
  proven pre-send failure and may be retried/released);
- per-attempt inactivity timeout (default 30s): httpx applies it separately to
  connect/read/write/pool, so it does NOT cap total call duration;
- **total deadline (Stage 18 S2.1):** one wall-clock budget bounds the entire
  LOCAL provider operation. It is derived from existing inputs — no new
  setting — as
  `(1 + llm_max_retries) × per_attempt_timeout + Σ backoff maxima`, where
  `per_attempt_timeout` is `LlmRequest.timeout_s` (the executor/API cap) when
  supplied and otherwise `APP_LLM_TIMEOUT_S`, and each retry's backoff maximum
  is `min(8s, 0.5·2ⁿ) + 0.25s`. Defaults: `3 × 30s + 2s = 92s`; an executor
  request with its 300s cap: `3 × 300s + 2s = 902s`. The budget starts before
  the first attempt and covers client setup, every attempt, all backoff,
  response-body consumption and local parsing; no retry resets it. For
  `stream()`, each connection/read step must finish inside the same remaining
  budget. Expiry raises `provider_timeout` with `ambiguous=true` and is never
  retried, so an existing reservation is held `ambiguous`, never released;
  connect/pool timeouts stay pre-send and non-ambiguous as before;
- `attempts` reported in response metadata; ambiguous outcomes retain their
  budget reservation for reconciliation;
- local timeout/cancellation bounds only this process's work. It never
  guarantees that the remote provider stopped processing or billing the
  request; the provider contract is the only possible source of such a
  guarantee.
- output cap / `temperature` pass-through. The cap field is selected by
  `APP_LLM_MAX_TOKENS_PARAM`: `max_tokens` (default; gpt-4o-mini, Ollama, most
  compatible gateways) or `max_completion_tokens` (newer OpenAI reasoning
  models). Exactly one field is sent, with the same value used for the budget
  reservation. Usage tokens are echoed when the provider reports them; cost
  only when provider data exists (no billing tables).
- input-token reservation bound: for supported OpenAI-compatible byte-level
  BPE tokenizers the estimate uses UTF-8 byte length plus a conservative
  chat-framing allowance. This is not a tokenizer-independent proof; a gateway
  with a different tokenizer may still exceed it. Output remains capped by the
  selected `APP_LLM_MAX_TOKENS_PARAM` field.
- the adapter sends `Idempotency-Key` only to its configured official OpenAI
  endpoint. This header is not treated as proof of exactly-once billing and
  does not permit retrying an ambiguous outcome.

## Secrets

Key lives in `APP_LLM_API_KEY` → `Settings` → Authorization header, exactly
one use site. Never in logs, exceptions, details, or API responses
(enforced by `tests/test_llm_secrets.py` with a canary key).

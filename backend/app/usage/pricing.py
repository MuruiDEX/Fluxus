"""Pricing engine (Stage 16): turn usage into a cost, or honest uncertainty.

Rules (contract amendment v1.17):
- Provider-reported cost is authoritative and always `confirmed`.
- Locally computed cost is `confirmed` only when the provider actually
  reported usage. Missing usage is `estimated` only when the caller supplies
  real token evidence; with no evidence it is `unknown` (never zero).
- With no tariff the cost is `unknown` with `cost` = None — never 0.
- A conservative budget bound exists only with a known tariff AND a capped
  output; otherwise callers must fall back to fail-closed/opt-in. It is an
  estimate under the configured provider/tokenizer assumptions, not a
  tokenizer-independent mathematical guarantee.

The catalog is process-global (see tariffs.py) and empty by default. Tests and
operators register entries explicitly, so no price is ever fabricated.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.usage.tariffs import Tariff, TariffError, parse_tariffs_json

CENT = Decimal("0.01")
_PER_1K = Decimal(1000)
# Conservative chat-envelope allowance. OpenAI-compatible chat formats add a
# few tokens per message plus a fixed reply primer; these are deliberately
# generous so the bound stays on the safe side.
CHAT_FRAMING_TOKENS_BASE = 16
CHAT_FRAMING_TOKENS_PER_MESSAGE = 8


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _ceil(value: Decimal) -> Decimal:
    # Reservations must never round DOWN below the computed bound at cent
    # granularity. Actual ledger costs still use half-up (`_q`).
    return value.quantize(CENT, rounding=ROUND_CEILING)


class TariffRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], Tariff] = {}

    def register(self, tariff: Tariff) -> None:
        tariff.validate()
        self._entries[(tariff.provider, tariff.model)] = tariff

    def resolve(self, provider: str, model: str) -> Tariff | None:
        return self._entries.get((provider, model))

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


registry = TariffRegistry()


def register_tariff(tariff: Tariff) -> None:
    registry.register(tariff)


def resolve_tariff(provider: str, model: str) -> Tariff | None:
    return registry.resolve(provider, model)


def configure_tariffs(raw_json: str | None) -> None:
    """Load operator tariffs from settings (adds to, never clears, the catalog)."""
    if not raw_json:
        return
    for tariff in parse_tariffs_json(raw_json):
        registry.register(tariff)


@dataclass(frozen=True)
class CostQuote:
    status: str  # confirmed | estimated | unknown
    cost: Decimal | None
    cost_source: str  # provider | tariff | unknown
    tariff_version: str | None
    currency: str


def compute_cost(
    tariff: Tariff,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Decimal:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    cached = max(0, min(int(cached_tokens or 0), prompt))
    if cached and tariff.cached_input_per_1k is not None:
        billable = prompt - cached
        total = (
            billable * tariff.input_per_1k
            + cached * tariff.cached_input_per_1k
            + completion * tariff.output_per_1k
        )
    else:
        total = prompt * tariff.input_per_1k + completion * tariff.output_per_1k
    return _q(total / _PER_1K)


def price_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int | None = None,
    usage_reported: bool,
    provider_cost: Decimal | None = None,
) -> CostQuote:
    """Price one accounted call. Provider cost wins; otherwise the tariff.

    An unreported usage object with no token evidence (both counts zero) is
    `unknown`, not an estimated zero: the adapter's zero defaults do not prove
    that the provider charged nothing. Callers that have a real token bound
    must price that bound explicitly instead.
    """
    if provider_cost is not None:
        return CostQuote(
            status="confirmed",
            cost=_q(Decimal(str(provider_cost))),
            cost_source="provider",
            tariff_version=None,
            currency="USD",
        )
    if (
        not usage_reported
        and max(0, int(prompt_tokens or 0)) == 0
        and max(0, int(completion_tokens or 0)) == 0
    ):
        return CostQuote("unknown", None, "unknown", None, "USD")
    tariff = resolve_tariff(provider, model)
    if tariff is None:
        return CostQuote("unknown", None, "unknown", None, "USD")
    cost = compute_cost(
        tariff,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens or 0,
    )
    status = "confirmed" if usage_reported else "estimated"
    return CostQuote(status, cost, "tariff", tariff.version, tariff.currency)


def upper_bound_cost(
    *,
    provider: str,
    model: str,
    input_token_bound: int,
    max_output_tokens: int | None,
) -> CostQuote | None:
    """Conservative cost estimate for a call, or None when no bound exists.

    Requires a known tariff AND a positive capped output. The input bound must
    be a conservative upper bound on prompt tokens for the configured
    provider's tokenizer. The highest applicable input tariff is used, because
    a cached-input tariff can be more expensive than the normal input tariff.
    The result is an `estimated` quote rounded UP to cents; this is a
    conservative bound under the configured assumptions, not a proof that the
    provider's tokenizer or billing rules can never exceed it.
    """
    tariff = resolve_tariff(provider, model)
    if tariff is None or max_output_tokens is None or int(max_output_tokens) <= 0:
        return None
    input_rate = tariff.input_per_1k
    if tariff.cached_input_per_1k is not None:
        input_rate = max(input_rate, tariff.cached_input_per_1k)
    total = (
        max(0, int(input_token_bound)) * input_rate
        + max(0, int(max_output_tokens)) * tariff.output_per_1k
    )
    return CostQuote(
        "estimated", _ceil(total / _PER_1K), "tariff", tariff.version, tariff.currency
    )


def estimate_input_token_bound(*parts: str, message_count: int = 0) -> int:
    """Conservative prompt-token estimate based on UTF-8 bytes.

    Character counts are not an upper bound for Unicode input: one code point
    can become several UTF-8 bytes and several byte-level BPE tokens. For
    byte-level BPE tokenizers a token cannot span fewer than one input byte,
    so the UTF-8 byte length is the corresponding conservative bound. Chat
    framing tokens are added explicitly when `message_count` is supplied.

    This is not a tokenizer-independent proof. A gateway that uses a different
    tokenizer can still violate it; callers must keep treating the result as a
    conservative estimate, not a universal guarantee.
    """
    tokens = sum(len(part.encode("utf-8")) for part in parts if part)
    if message_count > 0:
        tokens += CHAT_FRAMING_TOKENS_BASE + CHAT_FRAMING_TOKENS_PER_MESSAGE * message_count
    return tokens


__all__ = [
    "CENT",
    "CHAT_FRAMING_TOKENS_BASE",
    "CHAT_FRAMING_TOKENS_PER_MESSAGE",
    "CostQuote",
    "Tariff",
    "TariffError",
    "TariffRegistry",
    "compute_cost",
    "configure_tariffs",
    "estimate_input_token_bound",
    "price_usage",
    "register_tariff",
    "registry",
    "resolve_tariff",
    "upper_bound_cost",
]

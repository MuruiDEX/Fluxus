"""Pricing engine (Stage 16): turn usage into a cost, or honest uncertainty.

Rules (contract amendment v1.17):
- Provider-reported cost is authoritative and always `confirmed`.
- Locally computed cost is `confirmed` only when the provider actually
  reported usage; when usage is missing it is `estimated` (never confirmed).
- With no tariff the cost is `unknown` with `cost` = None — never 0.
- The guaranteed budget upper bound exists only with a known tariff AND a
  capped output; otherwise callers must fall back to fail-closed/opt-in.

The catalog is process-global (see tariffs.py) and empty by default. Tests and
operators register entries explicitly, so no price is ever fabricated.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.usage.tariffs import Tariff, TariffError, parse_tariffs_json

CENT = Decimal("0.01")
_PER_1K = Decimal(1000)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


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
    """Price one accounted call. Provider cost wins; otherwise the tariff."""
    if provider_cost is not None:
        return CostQuote(
            status="confirmed",
            cost=_q(Decimal(str(provider_cost))),
            cost_source="provider",
            tariff_version=None,
            currency="USD",
        )
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
    """Proven maximum cost for a call, or None when it cannot be guaranteed.

    Requires a known tariff AND a capped output (max_output_tokens set). The
    input bound must be a genuine upper bound on prompt tokens (caller's
    responsibility — the executor uses character counts, which over-count).
    """
    tariff = resolve_tariff(provider, model)
    if tariff is None or max_output_tokens is None:
        return None
    total = (
        max(0, int(input_token_bound)) * tariff.input_per_1k
        + max(0, int(max_output_tokens)) * tariff.output_per_1k
    )
    return CostQuote("confirmed", _q(total / _PER_1K), "tariff", tariff.version, tariff.currency)


def estimate_input_token_bound(*parts: str) -> int:
    """Safe over-estimate of prompt tokens: token count never exceeds characters."""
    return sum(len(part) for part in parts if part)


__all__ = [
    "CENT",
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

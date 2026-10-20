"""Versioned tariff catalog (Stage 16).

A `Tariff` maps an exact (provider, model) pair to per-1k-token prices. The
catalog is EMPTY by default — an OpenAI-compatible endpoint is NOT assumed to
bill at OpenAI prices, and no price is invented. Operators supply real prices
via `APP_USAGE_TARIFFS_JSON` (or tests register one explicitly), and each
entry carries a version + source for historical reproducibility.

Unknown price is represented by the ABSENCE of a tariff — callers translate
that to `usage_status=unknown`, never to a zero cost.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Bump when the shipped catalog/semantics change; stored on every priced row.
TARIFF_VERSION = "2026-10-19"


class TariffError(ValueError):
    """A tariff entry is malformed (raises at registration, never silently)."""


@dataclass(frozen=True)
class Tariff:
    provider: str
    model: str
    input_per_1k: Decimal
    output_per_1k: Decimal
    currency: str = "USD"
    # Optional cached-input price. When absent, cached tokens bill as input.
    cached_input_per_1k: Decimal | None = None
    source: str = "operator"
    version: str = TARIFF_VERSION

    def validate(self) -> None:
        if not isinstance(self.provider, str) or not self.provider:
            raise TariffError("tariff.provider must be a non-empty string")
        if not isinstance(self.model, str) or not self.model:
            raise TariffError("tariff.model must be a non-empty string")
        if not isinstance(self.currency, str) or not self.currency:
            raise TariffError("tariff.currency must be a non-empty string")
        for name, value in (
            ("input_per_1k", self.input_per_1k),
            ("output_per_1k", self.output_per_1k),
        ):
            if not isinstance(value, Decimal) or value < 0:
                raise TariffError(f"tariff.{name} must be a Decimal >= 0")
        if self.cached_input_per_1k is not None and self.cached_input_per_1k < 0:
            raise TariffError("tariff.cached_input_per_1k must be >= 0 when set")


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise TariffError(f"not a number: {value!r}") from exc


def tariff_from_dict(raw: dict) -> Tariff:
    """Build a validated Tariff from a JSON object (ops config / tests)."""
    if not isinstance(raw, dict):
        raise TariffError("tariff entry must be an object")
    try:
        provider = str(raw["provider"])
        model = str(raw["model"])
        input_per_1k = _to_decimal(raw["input_per_1k"])
        output_per_1k = _to_decimal(raw["output_per_1k"])
    except KeyError as exc:
        raise TariffError(f"tariff entry missing field: {exc}") from exc
    cached = raw.get("cached_input_per_1k")
    tariff = Tariff(
        provider=provider,
        model=model,
        input_per_1k=input_per_1k,
        output_per_1k=output_per_1k,
        currency=str(raw.get("currency", "USD")),
        cached_input_per_1k=_to_decimal(cached) if cached is not None else None,
        source=str(raw.get("source", "operator")),
        version=str(raw.get("version", TARIFF_VERSION)),
    )
    tariff.validate()
    return tariff


def parse_tariffs_json(raw: str) -> list[Tariff]:
    """Parse `APP_USAGE_TARIFFS_JSON`: a JSON array of tariff objects."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TariffError(f"APP_USAGE_TARIFFS_JSON is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise TariffError("APP_USAGE_TARIFFS_JSON must be a JSON array")
    return [tariff_from_dict(item) for item in data]

"""Pricing engine: tariffs, provider cost, uncertainty, upper bounds (Stage 16)."""

import json
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.main import create_app
from app.usage import pricing
from app.usage.tariffs import Tariff, TariffError, parse_tariffs_json, tariff_from_dict

T = Tariff(
    provider="acme",
    model="acme-1",
    input_per_1k=Decimal("1.00"),
    output_per_1k=Decimal("2.00"),
    currency="USD",
    version="test-1",
)


def test_unknown_price_is_none_not_zero() -> None:
    quote = pricing.price_usage(
        provider="acme",
        model="unpriced",
        prompt_tokens=100,
        completion_tokens=50,
        usage_reported=True,
    )
    assert quote.status == "unknown"
    assert quote.cost is None
    assert quote.cost_source == "unknown"


def test_provider_reported_cost_is_authoritative() -> None:
    pricing.register_tariff(T)
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=100,
        completion_tokens=50,
        usage_reported=True,
        provider_cost=Decimal("0.42"),
    )
    assert quote.status == "confirmed"
    assert quote.cost == Decimal("0.42")
    assert quote.cost_source == "provider"


def test_tariff_reported_usage_is_confirmed_with_exact_cost() -> None:
    pricing.register_tariff(T)
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=1000,
        completion_tokens=500,
        usage_reported=True,
    )
    # 1000/1000*1.00 + 500/1000*2.00 = 1.00 + 1.00 = 2.00
    assert quote.status == "confirmed"
    assert quote.cost == Decimal("2.00")
    assert quote.cost_source == "tariff"
    assert quote.tariff_version == "test-1"


def test_tariff_missing_usage_without_token_evidence_is_unknown_not_zero() -> None:
    pricing.register_tariff(T)
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=0,
        completion_tokens=0,
        usage_reported=False,
    )
    # The adapter's zero defaults are missing data, not proof of a free call.
    assert quote.status == "unknown"
    assert quote.cost is None


def test_tariff_estimated_token_bound_is_estimated_not_confirmed() -> None:
    pricing.register_tariff(T)
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=1000,
        completion_tokens=500,
        usage_reported=False,
    )
    assert quote.status == "estimated"
    assert quote.cost == Decimal("2.00")


def test_cached_tokens_bill_at_cached_rate() -> None:
    pricing.register_tariff(
        Tariff(
            provider="acme",
            model="cached",
            input_per_1k=Decimal("1.00"),
            output_per_1k=Decimal("2.00"),
            cached_input_per_1k=Decimal("0.25"),
        )
    )
    # 800 cached at 0.25 + 200 fresh at 1.00 = 0.20 + 0.20 = 0.40
    quote = pricing.price_usage(
        provider="acme",
        model="cached",
        prompt_tokens=1000,
        completion_tokens=0,
        cached_tokens=800,
        usage_reported=True,
    )
    assert quote.cost == Decimal("0.40")


def test_half_up_rounding_to_cents() -> None:
    pricing.register_tariff(
        Tariff(
            provider="acme",
            model="tiny",
            input_per_1k=Decimal("0.001"),
            output_per_1k=Decimal("0.001"),
        )
    )
    # 5 tokens * 0.001 / 1000 = 0.000005 -> rounds to 0.00
    quote = pricing.price_usage(
        provider="acme",
        model="tiny",
        prompt_tokens=5,
        completion_tokens=0,
        usage_reported=True,
    )
    assert quote.cost == Decimal("0.00")
    pricing.register_tariff(
        Tariff(
            provider="acme",
            model="mid",
            input_per_1k=Decimal("1.00"),
            output_per_1k=Decimal("1.00"),
        )
    )
    quote2 = pricing.price_usage(
        provider="acme",
        model="mid",
        prompt_tokens=1805,
        completion_tokens=0,
        usage_reported=True,
    )
    assert quote2.cost == Decimal("1.81")  # 1.805 -> 1.81 (half up)


def test_upper_bound_requires_tariff_and_output_cap() -> None:
    assert (
        pricing.upper_bound_cost(
            provider="acme", model="acme-1", input_token_bound=100, max_output_tokens=200
        )
        is None
    )
    pricing.register_tariff(T)
    assert (
        pricing.upper_bound_cost(
            provider="acme", model="acme-1", input_token_bound=100, max_output_tokens=None
        )
        is None
    )
    assert (
        pricing.upper_bound_cost(
            provider="acme", model="acme-1", input_token_bound=1000, max_output_tokens=0
        )
        is None
    )
    quote = pricing.upper_bound_cost(
        provider="acme", model="acme-1", input_token_bound=1000, max_output_tokens=500
    )
    assert quote is not None
    # 1000/1000*1 + 500/1000*2 = 2.00
    assert quote.status == "estimated"
    assert quote.cost == Decimal("2.00")


def test_upper_bound_uses_highest_input_tariff_and_rounds_up() -> None:
    pricing.register_tariff(
        Tariff(
            provider="acme",
            model="cached-expensive",
            input_per_1k=Decimal("1.00"),
            output_per_1k=Decimal("1.00"),
            cached_input_per_1k=Decimal("3.00"),
            version="cached-high",
        )
    )
    quote = pricing.upper_bound_cost(
        provider="acme",
        model="cached-expensive",
        input_token_bound=1,
        max_output_tokens=1,
    )
    # Highest input rate 3.00 + output 1.00 = 0.004 -> ceil to 0.01.
    assert quote is not None
    assert quote.status == "estimated"
    assert quote.cost == Decimal("0.01")


def test_estimate_input_bound_is_utf8_byte_count() -> None:
    assert pricing.estimate_input_token_bound("abc", "de", "") == 5
    assert pricing.estimate_input_token_bound("Привет") == len("Привет".encode())
    assert pricing.estimate_input_token_bound("你好") == len("你好".encode())
    assert pricing.estimate_input_token_bound("") == 0
    assert pricing.estimate_input_token_bound("a" * 10_000) == 10_000


def test_estimate_input_bound_adds_conservative_chat_framing() -> None:
    assert pricing.estimate_input_token_bound("hi", message_count=2) == (
        2 + pricing.CHAT_FRAMING_TOKENS_BASE + 2 * pricing.CHAT_FRAMING_TOKENS_PER_MESSAGE
    )
    assert pricing.estimate_input_token_bound("", message_count=1) > 0


def test_tariff_override_keeps_version_stamp() -> None:
    pricing.register_tariff(T)
    pricing.register_tariff(
        Tariff(
            provider="acme",
            model="acme-1",
            input_per_1k=Decimal("9.00"),
            output_per_1k=Decimal("9.00"),
            version="test-2",
        )
    )
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=1000,
        completion_tokens=0,
        usage_reported=True,
    )
    assert quote.cost == Decimal("9.00")
    assert quote.tariff_version == "test-2"


def test_parse_tariffs_json_round_trip_and_errors() -> None:
    parsed = parse_tariffs_json(
        '[{"provider":"p","model":"m","input_per_1k":"0.5","output_per_1k":"1.5"}]'
    )
    assert parsed[0].input_per_1k == Decimal("0.5")
    with pytest.raises(TariffError):
        parse_tariffs_json('{"provider":"p"}')
    with pytest.raises(TariffError):
        parse_tariffs_json("not json")
    with pytest.raises(TariffError):
        parse_tariffs_json('[{"provider":"p","model":"m"}]')


# --- USD-only accounting currency (approved Option A policy) ---


def _usd_tariff(currency: object = "USD") -> Tariff:
    return Tariff(
        provider="acme",
        model="currency-1",
        input_per_1k=Decimal("1.00"),
        output_per_1k=Decimal("2.00"),
        currency=currency,  # type: ignore[arg-type]
        version="currency-test",
    )


@pytest.mark.parametrize("raw", ["USD", "usd", "Usd", "uSd", " usd ", "\tUSD\n"])
def test_tariff_currency_usd_is_normalized(raw: str) -> None:
    tariff = _usd_tariff(raw)
    assert tariff.currency == "USD"

    parsed = parse_tariffs_json(
        json.dumps(
            [
                {
                    "provider": "acme",
                    "model": "currency-1",
                    "input_per_1k": "1.00",
                    "output_per_1k": "2.00",
                    "currency": raw,
                }
            ]
        )
    )
    assert parsed[0].currency == "USD"

    pricing.register_tariff(tariff)
    resolved = pricing.resolve_tariff("acme", "currency-1")
    assert resolved is not None and resolved.currency == "USD"


@pytest.mark.parametrize("raw", ["EUR", "GBP", "USDT", "EURO", "usd-ish"])
def test_tariff_currency_non_usd_is_rejected(raw: str) -> None:
    with pytest.raises(TariffError):
        _usd_tariff(raw)
    with pytest.raises(TariffError):
        tariff_from_dict(
            {
                "provider": "acme",
                "model": "currency-1",
                "input_per_1k": "1.00",
                "output_per_1k": "2.00",
                "currency": raw,
            }
        )
    with pytest.raises(TariffError):
        parse_tariffs_json(
            json.dumps(
                [
                    {
                        "provider": "acme",
                        "model": "currency-1",
                        "input_per_1k": "1.00",
                        "output_per_1k": "2.00",
                        "currency": raw,
                    }
                ]
            )
        )


@pytest.mark.parametrize("raw", [None, 123, "", "   ", " \t\n "])
def test_tariff_currency_non_string_or_blank_is_rejected(raw: object) -> None:
    with pytest.raises(TariffError):
        _usd_tariff(raw)


def test_tariff_validate_is_a_second_currency_boundary() -> None:
    tariff = _usd_tariff()
    object.__setattr__(tariff, "currency", "EUR")
    with pytest.raises(TariffError):
        tariff.validate()


def test_invalid_currency_config_fails_before_app_startup() -> None:
    raw = json.dumps(
        [
            {
                "provider": "acme",
                "model": "usd-ok",
                "input_per_1k": "1.00",
                "output_per_1k": "2.00",
            },
            {
                "provider": "acme",
                "model": "eur-bad",
                "input_per_1k": "1.00",
                "output_per_1k": "2.00",
                "currency": "EUR",
            },
        ]
    )
    with pytest.raises(TariffError):
        create_app(Settings(environment="test", usage_tariffs_json=raw))
    # Parsing happens before registration, so no partial catalog is installed.
    assert len(pricing.registry) == 0


def test_provider_reported_and_unknown_quotes_remain_usd() -> None:
    provider_quote = pricing.price_usage(
        provider="acme",
        model="unpriced",
        prompt_tokens=10,
        completion_tokens=5,
        usage_reported=True,
        provider_cost=Decimal("0.42"),
    )
    assert provider_quote.currency == "USD"

    unknown_quote = pricing.price_usage(
        provider="acme",
        model="unpriced",
        prompt_tokens=0,
        completion_tokens=0,
        usage_reported=True,
    )
    assert unknown_quote.status == "unknown"
    assert unknown_quote.currency == "USD"


def test_existing_usd_tariff_pricing_is_unchanged() -> None:
    # No explicit currency: the default stays USD, with unchanged prices/version.
    tariff = Tariff(
        provider="acme",
        model="default-usd",
        input_per_1k=Decimal("1.00"),
        output_per_1k=Decimal("2.00"),
        version="test-1",
    )
    assert tariff.currency == "USD"
    pricing.register_tariff(tariff)

    quote = pricing.price_usage(
        provider="acme",
        model="default-usd",
        prompt_tokens=1000,
        completion_tokens=500,
        usage_reported=True,
    )
    assert quote.status == "confirmed"
    assert quote.cost == Decimal("2.00")
    assert quote.tariff_version == "test-1"
    assert quote.currency == "USD"

    bound = pricing.upper_bound_cost(
        provider="acme",
        model="default-usd",
        input_token_bound=1000,
        max_output_tokens=500,
    )
    assert bound is not None
    assert bound.cost == Decimal("2.00")
    assert bound.currency == "USD"

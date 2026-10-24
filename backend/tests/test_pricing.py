"""Pricing engine: tariffs, provider cost, uncertainty, upper bounds (Stage 16)."""

from decimal import Decimal

import pytest

from app.usage import pricing
from app.usage.tariffs import Tariff, TariffError, parse_tariffs_json

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


def test_tariff_missing_usage_is_estimated_not_confirmed() -> None:
    pricing.register_tariff(T)
    quote = pricing.price_usage(
        provider="acme",
        model="acme-1",
        prompt_tokens=0,
        completion_tokens=0,
        usage_reported=False,
    )
    assert quote.status == "estimated"
    assert quote.cost == Decimal("0.00")


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
    quote = pricing.upper_bound_cost(
        provider="acme", model="acme-1", input_token_bound=1000, max_output_tokens=500
    )
    assert quote is not None
    # 1000/1000*1 + 500/1000*2 = 2.00
    assert quote.cost == Decimal("2.00")


def test_estimate_input_bound_is_utf8_byte_count() -> None:
    assert pricing.estimate_input_token_bound("abc", "de", "") == 5
    assert pricing.estimate_input_token_bound("Привет") == len("Привет".encode())
    assert pricing.estimate_input_token_bound("你好") == len("你好".encode())
    assert pricing.estimate_input_token_bound("") == 0
    assert pricing.estimate_input_token_bound("a" * 10_000) == 10_000


def test_estimate_input_bound_adds_conservative_chat_framing() -> None:
    assert pricing.estimate_input_token_bound("hi", message_count=2) == (
        2
        + pricing.CHAT_FRAMING_TOKENS_BASE
        + 2 * pricing.CHAT_FRAMING_TOKENS_PER_MESSAGE
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

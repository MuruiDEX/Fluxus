"""Startup recovery-bound validation: fail fast on unsafe configuration."""

import pytest

from app.core.config import Settings, validate_recovery_bound


def test_defaults_satisfy_bound() -> None:
    validate_recovery_bound(Settings())  # must not raise


def test_tight_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="does not exceed worst-case"):
        validate_recovery_bound(Settings(recovery_stale_minutes=1))


def test_small_timeout_allows_tight_threshold() -> None:
    # Floor is dominated by DB phases (9 statements × 30s + 60s margin = 330s),
    # so a 7-minute threshold passes only with a small LLM budget.
    validate_recovery_bound(Settings(recovery_stale_minutes=7, execution_max_timeout_s=10.0))


def test_bound_tracks_adapter_retry_budget() -> None:
    # Bound must grow with the retry budget: more retries = longer live runs.
    from app.core import config as config_module

    base = Settings()
    assert (
        config_module._worst_case_live_run_seconds(
            attempts=3,
            max_timeout_s=base.execution_max_timeout_s,
            backoff_total_s=0,
            db_statements=0,
            statement_timeout_s=0,
            margin_s=0,
        )
        == 3 * base.execution_max_timeout_s
    )


def test_backoff_constants_pinned_to_adapter() -> None:
    # validate_recovery_bound hardcodes per-retry ceiling 8.0s + 0.25s jitter
    # headroom; if the adapter ever raises its cap, this fails loudly instead
    # of silently shrinking the safety bound.
    from app.llm.adapters import openai_compat as adapter

    assert adapter._BACKOFF_CAP_S == 8.0
    samples = [adapter.OpenAICompatProvider._backoff_delay(99) for _ in range(50)]
    assert all(s <= 8.25 for s in samples)

"""Startup recovery-bound validation: fail fast on unsafe configuration.

S2.2.2 replaces the old nine-statement approximation with a path-accurate
table: Team Lead business calls, Team Lead finalize, and the executor each
get their own provider deadline, statement count, pool acquisition and
margin. These tests pin the table and prove that a configuration exceeding
the cutoff for any single path is rejected.

S2.2.3 adds the cutoff policy on top of that table: the configured cutoff
must strictly exceed the longest path by _RECOVERY_CUTOFF_MARGIN_S (600s).
That margin is a time cushion for COMMIT/network work that statement_timeout
does not bound — it is not a proof against an arbitrarily long live run.
"""

import pytest

from app.core import config as config_module
from app.core.config import (
    Settings,
    _lifecycle_bounds,
    _provider_deadline_seconds,
    validate_recovery_bound,
)
from app.persistence.engine import create_engine_from_url


def test_defaults_satisfy_bound() -> None:
    validate_recovery_bound(Settings())  # must not raise


def test_create_app_rejects_unsafe_cutoff_before_serving() -> None:
    from app.main import create_app

    with pytest.raises(ValueError, match="does not exceed worst-case"):
        create_app(Settings(recovery_stale_minutes=1))


def test_tight_threshold_rejected() -> None:
    with pytest.raises(ValueError, match="does not exceed worst-case"):
        validate_recovery_bound(Settings(recovery_stale_minutes=1))


def test_small_timeout_allows_tight_threshold() -> None:
    # At 1s per statement the longest path is finalize at 402s, so the
    # cutoff must exceed 402 + 600 = 1002s: 17 minutes passes, 16 does not.
    base = {"execution_max_timeout_s": 10.0, "db_statement_timeout_ms": 1000}
    validate_recovery_bound(Settings(recovery_stale_minutes=17, **base))

    with pytest.raises(ValueError, match="does not exceed worst-case"):
        validate_recovery_bound(Settings(recovery_stale_minutes=16, **base))


def test_bound_tracks_adapter_retry_budget() -> None:
    # Bound must grow with the retry budget: more retries = longer live runs.
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
    # The path bound mirrors the adapter constants; if the adapter ever
    # raises its cap, this fails loudly instead of silently shrinking it.
    from app.llm.adapters import openai_compat as adapter

    assert config_module._BACKOFF_BASE_S == adapter._BACKOFF_BASE_S
    assert config_module._BACKOFF_CAP_S == adapter._BACKOFF_CAP_S
    assert config_module._BACKOFF_JITTER_MAX_S == 0.25
    samples = [adapter.OpenAICompatProvider._backoff_delay(99) for _ in range(50)]
    assert all(s <= 8.25 for s in samples)


def test_provider_deadline_matches_s21_contract() -> None:
    # (1 + retries) × timeout + worst-case backoff, with no second retry term.
    assert _provider_deadline_seconds(30.0, 2) == pytest.approx(92.0)
    assert _provider_deadline_seconds(300.0, 2) == pytest.approx(902.0)
    assert _provider_deadline_seconds(30.0, 0) == pytest.approx(30.0)


def test_default_lifecycle_table_is_path_accurate() -> None:
    bounds = _lifecycle_bounds(Settings())

    # 902 provider + 26 statements × 30s + 30s pool + 60s margin.
    assert bounds["executor"] == pytest.approx(1772.0)
    # 2 generations × (92 provider + 15 statements × 30s + 60s pool) + 60s.
    assert bounds["teamlead_questions"] == pytest.approx(1264.0)
    assert bounds["teamlead_summary"] == pytest.approx(1264.0)
    assert bounds["teamlead_planning"] == pytest.approx(1264.0)
    # Finalize settles a project, adding aiSpent recomputation (4 statements).
    assert bounds["teamlead_finalize"] == pytest.approx(1504.0)


def test_teamlead_generations_are_not_counted_once() -> None:
    bounds = _lifecycle_bounds(Settings())

    # One finalize generation would be 92 + 19×30 + 60 = 722, +60 margin.
    assert bounds["teamlead_finalize"] == pytest.approx(1504.0)
    assert bounds["teamlead_finalize"] > bounds["teamlead_questions"]


def test_executor_configuration_alone_can_fail_the_cutoff() -> None:
    settings = Settings(db_statement_timeout_ms=35000)

    with pytest.raises(ValueError) as caught:
        validate_recovery_bound(settings)

    message = str(caught.value)
    assert "executor bound" in message
    assert "teamlead_" not in message


def test_teamlead_provider_configuration_fails_the_worst_teamlead_path() -> None:
    settings = Settings(llm_timeout_s=600.0)

    with pytest.raises(ValueError) as caught:
        validate_recovery_bound(settings)

    # Finalize is the longest Team Lead path, so it is the one reported.
    message = str(caught.value)
    assert "teamlead_finalize" in message
    assert "executor bound" not in message


def test_finalize_bound_is_checked_independently() -> None:
    # 22 minutes = 1320s: finalize (1504s) is the longest path, so it is the
    # one the validator reports; the shorter paths are not mentioned.
    settings = Settings(recovery_stale_minutes=22, execution_max_timeout_s=50.0)

    with pytest.raises(ValueError) as caught:
        validate_recovery_bound(settings)

    message = str(caught.value)
    assert "teamlead_finalize" in message
    assert "teamlead_questions" not in message
    assert "executor bound" not in message


def test_cutoff_must_strictly_exceed_bound_plus_policy_margin() -> None:
    # 41s × 1 attempt, 1s per statement, 10s executor cap:
    # finalize = 2 × (41 + 19×1 + 60) + 60 = exactly 300s, so the cutoff must
    # exceed 300 + 600 = 900s. 15 minutes is exactly 900s and must be rejected;
    # 16 minutes (960s) is the first accepted whole minute.
    base = {
        "llm_timeout_s": 41.0,
        "db_statement_timeout_ms": 1000,
        "execution_max_timeout_s": 10.0,
        "llm_max_retries": 0,
    }
    assert _lifecycle_bounds(Settings(**base))["teamlead_finalize"] == pytest.approx(300.0)

    with pytest.raises(ValueError, match="does not exceed worst-case"):
        validate_recovery_bound(Settings(recovery_stale_minutes=15, **base))

    validate_recovery_bound(Settings(recovery_stale_minutes=16, **base))


def test_default_cutoff_is_the_smallest_safe_whole_minute() -> None:
    bounds = _lifecycle_bounds(Settings())
    bound = max(bounds.values())
    required = bound + config_module._RECOVERY_CUTOFF_MARGIN_S
    default_seconds = Settings().recovery_stale_minutes * 60.0

    assert bound == pytest.approx(1772.0)
    assert required == pytest.approx(2372.0)
    assert default_seconds == pytest.approx(2400.0)
    assert default_seconds - bound == pytest.approx(628.0)
    # 39 minutes (2340s) is below the required 2372s and must be rejected.
    assert 39 * 60.0 <= required
    with pytest.raises(ValueError, match="does not exceed worst-case"):
        validate_recovery_bound(Settings(recovery_stale_minutes=39))


def test_45_minute_policy_is_also_acceptable() -> None:
    settings = Settings(recovery_stale_minutes=45)
    validate_recovery_bound(settings)

    bound = max(_lifecycle_bounds(settings).values())
    assert settings.recovery_stale_minutes * 60.0 - bound == pytest.approx(928.0)


def test_policy_check_covers_every_lifecycle_path() -> None:
    assert set(_lifecycle_bounds(Settings())) == {
        "teamlead_questions",
        "teamlead_summary",
        "teamlead_planning",
        "teamlead_finalize",
        "executor",
    }


def test_teamlead_generation_default_matches_the_bound() -> None:
    # The bound sums two generations; it must fail loudly if a generator's
    # production default changes to a different number.
    import inspect

    from app.teamlead import finalize, planning, questions, summary

    for generator in (
        questions.generate_questions,
        summary.generate_summary,
        planning.generate_plan,
        finalize.generate_finalization,
    ):
        default = inspect.signature(generator).parameters["max_generations"].default
        assert default == config_module._TEAMLEAD_MAX_GENERATIONS


def test_pool_timeout_is_the_production_engine_default() -> None:
    engine = create_engine_from_url(
        "postgresql+asyncpg://u:p@127.0.0.1:1/db", statement_timeout_ms=30000
    )

    assert engine.pool.timeout() == config_module._DB_POOL_TIMEOUT_S

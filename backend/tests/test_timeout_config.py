"""S2.2.1: timeout settings must fail closed at Settings construction.

A zero/negative/non-finite timeout would disable the DB statement bound or
make the provider deadline non-finite, leaving the recovery cutoff unable to
prove that a live run has finished. These are configuration errors, not
runtime degradations: they must be rejected before the app can serve.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, validate_recovery_bound


def test_defaults_are_accepted_and_unchanged() -> None:
    settings = Settings()

    assert settings.db_statement_timeout_ms == 30000
    assert settings.llm_timeout_s == 30.0
    assert settings.execution_max_timeout_s == 300.0
    assert settings.llm_max_retries == 2
    assert settings.recovery_stale_minutes == 40


@pytest.mark.parametrize("value", [-1, -30000, 0])
def test_db_statement_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError, match="db_statement_timeout_ms must be a positive"):
        Settings(db_statement_timeout_ms=value)


@pytest.mark.parametrize("value", [1, 30000])
def test_positive_db_statement_timeout_is_accepted(value: int) -> None:
    assert Settings(db_statement_timeout_ms=value).db_statement_timeout_ms == value


@pytest.mark.parametrize("value", [-1.0, -0.5, 0, 0.0])
def test_llm_timeout_must_be_positive(value: float) -> None:
    with pytest.raises(ValidationError, match="llm_timeout_s must be a finite positive"):
        Settings(llm_timeout_s=value)


@pytest.mark.parametrize("value", [0.001, 1.5, 30.0])
def test_positive_llm_timeout_is_accepted(value: float) -> None:
    assert Settings(llm_timeout_s=value).llm_timeout_s == value


@pytest.mark.parametrize("value", [-1.0, -0.001, 0, 0.0])
def test_execution_max_timeout_must_be_positive(value: float) -> None:
    with pytest.raises(ValidationError, match="execution_max_timeout_s must be a finite positive"):
        Settings(execution_max_timeout_s=value)


@pytest.mark.parametrize("value", [0.001, 300.0])
def test_positive_execution_max_timeout_is_accepted(value: float) -> None:
    assert Settings(execution_max_timeout_s=value).execution_max_timeout_s == value


def test_negative_retries_rejected() -> None:
    with pytest.raises(ValidationError, match="llm_max_retries must be zero or greater"):
        Settings(llm_max_retries=-1)


@pytest.mark.parametrize("value", [0, 1, 5])
def test_zero_and_positive_retries_accepted(value: int) -> None:
    assert Settings(llm_max_retries=value).llm_max_retries == value


@pytest.mark.parametrize("value", [-1, 0])
def test_recovery_cutoff_must_be_positive(value: int) -> None:
    with pytest.raises(
        ValidationError, match="recovery_stale_minutes must be a positive number of minutes"
    ):
        Settings(recovery_stale_minutes=value)


@pytest.mark.parametrize("value", [40, 45])
def test_positive_recovery_cutoff_is_accepted(value: int) -> None:
    assert Settings(recovery_stale_minutes=value).recovery_stale_minutes == value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_llm_timeout_rejected(value: float) -> None:
    with pytest.raises(ValidationError, match="llm_timeout_s must be a finite positive"):
        Settings(llm_timeout_s=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_execution_timeout_rejected(value: float) -> None:
    with pytest.raises(ValidationError, match="execution_max_timeout_s must be a finite positive"):
        Settings(execution_max_timeout_s=value)


def test_environment_values_are_validated_not_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env overrides go through the same validators — no silent coercion."""

    monkeypatch.setenv("APP_DB_STATEMENT_TIMEOUT_MS", "0")
    with pytest.raises(ValidationError, match="db_statement_timeout_ms must be a positive"):
        Settings()

    monkeypatch.setenv("APP_DB_STATEMENT_TIMEOUT_MS", "12.5")
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("APP_DB_STATEMENT_TIMEOUT_MS", "not-a-number")
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("APP_DB_STATEMENT_TIMEOUT_MS", "30000")
    monkeypatch.setenv("APP_LLM_TIMEOUT_S", "nan")
    with pytest.raises(ValidationError, match="llm_timeout_s must be a finite positive"):
        Settings()


def test_env_supplied_cutoff_is_checked_against_the_same_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An env cutoff is validated by the same S2.2.3 policy, not bypassed."""
    monkeypatch.setenv("APP_RECOVERY_STALE_MINUTES", "5")
    with pytest.raises(ValueError, match="recovery_stale_minutes=5"):
        validate_recovery_bound(Settings())

    monkeypatch.setenv("APP_RECOVERY_STALE_MINUTES", "40")
    settings = Settings()
    assert settings.recovery_stale_minutes == 40
    validate_recovery_bound(settings)

"""Tests for philiprehberger_circuit_breaker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from philiprehberger_circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    circuit_breaker,
)


def _succeeding() -> str:
    return "ok"


def _failing() -> str:
    raise RuntimeError("boom")


class TestCircuitBreakerClosedState:
    def test_passes_through_on_success(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        result = cb.call(_succeeding)
        assert result == "ok"
        assert cb.state is CircuitState.CLOSED

    def test_stays_closed_below_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert cb.state is CircuitState.CLOSED


class TestCircuitBreakerOpensAfterFailures:
    def test_opens_after_reaching_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert cb.state is CircuitState.OPEN

    def test_raises_circuit_open_error_when_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call(_succeeding)
        assert exc_info.value.breaker is cb


class TestCircuitBreakerRecovery:
    def test_transitions_to_half_open_after_timeout(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert cb.state is CircuitState.OPEN

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 10  # type: ignore[operator]
            assert cb.state is CircuitState.HALF_OPEN

    def test_closes_on_success_in_half_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 10  # type: ignore[operator]
            result = cb.call(_succeeding)

        assert result == "ok"
        assert cb.state is CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 10  # type: ignore[operator]
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        assert cb.state is CircuitState.OPEN


class TestCircuitBreakerReset:
    def test_reset_returns_to_closed(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert cb.state is CircuitState.OPEN

        cb.reset()
        assert cb.state is CircuitState.CLOSED
        assert cb._failure_count == 0
        result = cb.call(_succeeding)
        assert result == "ok"


class TestCircuitBreakerDecorator:
    def test_decorator_wraps_function(self) -> None:
        @circuit_breaker(failure_threshold=3)
        def my_func() -> str:
            return "hello"

        assert my_func() == "hello"
        assert hasattr(my_func, "breaker")
        assert isinstance(my_func.breaker, CircuitBreaker)  # type: ignore[attr-defined]

    def test_decorator_opens_circuit_after_failures(self) -> None:
        @circuit_breaker(failure_threshold=2)
        def unstable() -> str:
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                unstable()

        with pytest.raises(CircuitOpenError):
            unstable()

    def test_decorator_preserves_function_name(self) -> None:
        @circuit_breaker()
        def my_named_function() -> None:
            pass

        assert my_named_function.__name__ == "my_named_function"

"""Tests for philiprehberger_circuit_breaker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from philiprehberger_circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerStats,
    CircuitOpenError,
    CircuitState,
    ExceptionFilter,
    HealthWindow,
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
            mock_time.monotonic.return_value = cb._last_failure_time + 11  # type: ignore[operator]
            assert cb.state is CircuitState.HALF_OPEN

    def test_closes_on_success_in_half_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0
        result = cb.call(_succeeding)

        assert result == "ok"
        assert cb.state is CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0
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

    def test_reset_clears_success_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.call(_succeeding)
        cb.call(_succeeding)
        assert cb._success_count == 2
        cb.reset()
        assert cb._success_count == 0

    def test_reset_clears_health_window(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=2)
        cb = CircuitBreaker(failure_threshold=10, health_window=hw)
        with pytest.raises(RuntimeError):
            cb.call(_failing)
        cb.reset()
        assert hw.failure_rate() == 0.0


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

    def test_decorator_passes_half_open_max_calls(self) -> None:
        @circuit_breaker(failure_threshold=2, half_open_max_calls=3)
        def my_func() -> str:
            return "ok"

        assert my_func.breaker.half_open_max_calls == 3  # type: ignore[attr-defined]

    def test_decorator_passes_health_window(self) -> None:
        hw = HealthWindow(window_size=30.0)

        @circuit_breaker(failure_threshold=5, health_window=hw)
        def my_func() -> str:
            return "ok"

        assert my_func.breaker.health_window is hw  # type: ignore[attr-defined]


class TestHalfOpenProbeLimiting:
    def test_default_half_open_max_calls_is_one(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        assert cb.half_open_max_calls == 1

    def test_allows_configured_number_of_probe_calls(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, half_open_max_calls=3)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0

        # Should allow 3 calls
        for _ in range(3):
            assert cb.call(_succeeding) == "ok"

    def test_rejects_after_probe_limit_exceeded(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, half_open_max_calls=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0

        # First call succeeds and closes the circuit, resetting half_open_calls
        assert cb.call(_succeeding) == "ok"
        assert cb.state is CircuitState.CLOSED

    def test_probe_limit_blocks_excess_calls(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, half_open_max_calls=1)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0

        # First call: allowed (increments _half_open_calls to 1, succeeds, closes circuit)
        # To test the limit, we need to keep it in half-open without closing
        # We'll use a function that fails to keep it open
        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 1  # simulate one call already made

        with pytest.raises(CircuitOpenError):
            cb.call(_succeeding)

    def test_half_open_calls_reset_on_transition_from_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10, half_open_max_calls=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 11  # type: ignore[operator]
            assert cb.state is CircuitState.HALF_OPEN
            assert cb._half_open_calls == 0


class TestExceptionFilter:
    def test_matches_base_exceptions(self) -> None:
        ef = ExceptionFilter(base_exceptions=(ValueError, TypeError))
        assert ef.matches(ValueError("test"))
        assert ef.matches(TypeError("test"))
        assert not ef.matches(RuntimeError("test"))

    def test_per_type_threshold_trips_circuit(self) -> None:
        ef = ExceptionFilter(
            base_exceptions=(ConnectionError, TimeoutError),
            thresholds={TimeoutError: 2},
        )
        cb = CircuitBreaker(failure_threshold=10, exception_filter=ef)

        def raise_timeout() -> None:
            raise TimeoutError("timed out")

        # Two TimeoutErrors should trip the circuit (threshold=2)
        for _ in range(2):
            with pytest.raises(TimeoutError):
                cb.call(raise_timeout)

        assert cb.state is CircuitState.OPEN

    def test_non_matching_exceptions_pass_through(self) -> None:
        ef = ExceptionFilter(base_exceptions=(ValueError,))
        cb = CircuitBreaker(failure_threshold=3, exception_filter=ef)

        def raise_runtime() -> None:
            raise RuntimeError("not tracked")

        # RuntimeError is not in base_exceptions, so it should not count
        for _ in range(5):
            with pytest.raises(RuntimeError):
                cb.call(raise_runtime)

        assert cb.state is CircuitState.CLOSED

    def test_reset_clears_per_type_counts(self) -> None:
        ef = ExceptionFilter(
            base_exceptions=(TimeoutError,),
            thresholds={TimeoutError: 3},
        )
        ef.record(TimeoutError())
        ef.record(TimeoutError())
        ef.reset()
        # Should not trip after reset + one more
        assert not ef.record(TimeoutError())


class TestEventListeners:
    def test_add_listener_on_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        events: list[str] = []
        cb.add_listener("on_open", lambda: events.append("opened"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        assert events == ["opened"]

    def test_add_listener_on_close(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        events: list[str] = []
        cb.add_listener("on_close", lambda: events.append("closed"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0
        cb.call(_succeeding)

        assert events == ["closed"]

    def test_add_listener_on_half_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
        events: list[str] = []
        cb.add_listener("on_half_open", lambda: events.append("half_open"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 11  # type: ignore[operator]
            _ = cb.state

        assert events == ["half_open"]

    def test_multiple_listeners_on_same_event(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        events: list[str] = []
        cb.add_listener("on_open", lambda: events.append("listener1"))
        cb.add_listener("on_open", lambda: events.append("listener2"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        assert events == ["listener1", "listener2"]

    def test_listeners_fire_alongside_constructor_callbacks(self) -> None:
        events: list[str] = []
        cb = CircuitBreaker(
            failure_threshold=2,
            on_open=lambda: events.append("constructor_open"),
        )
        cb.add_listener("on_open", lambda: events.append("listener_open"))

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        assert events == ["constructor_open", "listener_open"]

    def test_remove_listener(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        events: list[str] = []

        def on_open_handler() -> None:
            events.append("opened")

        cb.add_listener("on_open", on_open_handler)
        cb.remove_listener("on_open", on_open_handler)

        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        assert events == []

    def test_add_listener_invalid_event_raises(self) -> None:
        cb = CircuitBreaker()
        with pytest.raises(ValueError, match="Unknown event"):
            cb.add_listener("on_invalid", lambda: None)

    def test_remove_listener_invalid_event_raises(self) -> None:
        cb = CircuitBreaker()
        with pytest.raises(ValueError, match="Unknown event"):
            cb.remove_listener("on_invalid", lambda: None)

    def test_remove_unregistered_listener_raises(self) -> None:
        cb = CircuitBreaker()
        with pytest.raises(ValueError):
            cb.remove_listener("on_open", lambda: None)


class TestHealthWindow:
    def test_no_calls_returns_zero_failure_rate(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=5)
        assert hw.failure_rate() == 0.0

    def test_all_failures_returns_one(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=2)
        now = 100.0
        hw.record_failure(now)
        hw.record_failure(now + 1)
        assert hw.failure_rate(now + 2) == 1.0

    def test_mixed_calls_rate(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=2)
        now = 100.0
        hw.record_success(now)
        hw.record_failure(now + 1)
        hw.record_failure(now + 2)
        hw.record_success(now + 3)
        # 2 failures out of 4 = 0.5
        assert hw.failure_rate(now + 4) == 0.5

    def test_should_open_below_min_calls(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=5)
        now = 100.0
        hw.record_failure(now)
        hw.record_failure(now + 1)
        # Only 2 calls, min_calls=5, so should not open
        assert not hw.should_open(now + 2)

    def test_should_open_above_threshold(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=2)
        now = 100.0
        hw.record_failure(now)
        hw.record_failure(now + 1)
        assert hw.should_open(now + 2)

    def test_should_not_open_below_threshold(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=2)
        now = 100.0
        hw.record_success(now)
        hw.record_success(now + 1)
        hw.record_failure(now + 2)
        # 1 failure out of 3 = 0.33 < 0.5
        assert not hw.should_open(now + 3)

    def test_old_entries_pruned(self) -> None:
        hw = HealthWindow(window_size=10.0, failure_rate_threshold=0.5, min_calls=1)
        now = 100.0
        hw.record_failure(now)
        hw.record_failure(now + 1)
        # After window expires, old entries are pruned
        hw.record_success(now + 20)
        # Only the success at now+20 is within window
        assert hw.failure_rate(now + 20) == 0.0

    def test_reset_clears_all(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=1)
        now = 100.0
        hw.record_failure(now)
        hw.record_failure(now + 1)
        hw.reset()
        assert hw.failure_rate(now + 2) == 0.0
        assert not hw.should_open(now + 2)


class TestHealthWindowIntegration:
    def test_circuit_opens_on_high_failure_rate(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=4)
        cb = CircuitBreaker(failure_threshold=100, health_window=hw)

        # 3 failures, 1 success = 75% failure rate
        for _ in range(3):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        cb.call(_succeeding)

        # Now 4 calls total, 75% > 50%, next failure should trip
        with pytest.raises(RuntimeError):
            cb.call(_failing)

        assert cb.state is CircuitState.OPEN

    def test_circuit_stays_closed_with_low_failure_rate(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.8, min_calls=3)
        cb = CircuitBreaker(failure_threshold=100, health_window=hw)

        # 1 failure, 2 successes = 33% failure rate
        with pytest.raises(RuntimeError):
            cb.call(_failing)
        cb.call(_succeeding)
        cb.call(_succeeding)

        assert cb.state is CircuitState.CLOSED

    def test_health_window_records_on_success(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=1)
        cb = CircuitBreaker(failure_threshold=100, health_window=hw)

        cb.call(_succeeding)
        assert hw.failure_rate() == 0.0

    def test_health_window_reset_on_breaker_reset(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.5, min_calls=1)
        cb = CircuitBreaker(failure_threshold=100, health_window=hw)

        with pytest.raises(RuntimeError):
            cb.call(_failing)
        assert hw.failure_rate() > 0.0

        cb.reset()
        assert hw.failure_rate() == 0.0


class TestGetState:
    def test_returns_closed_initially(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.get_state() is CircuitState.CLOSED

    def test_returns_open_after_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert cb.get_state() is CircuitState.OPEN

    def test_matches_state_property(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.get_state() is cb.state


class TestGetStats:
    def test_initial_stats(self) -> None:
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        stats = cb.get_stats()
        assert stats.state is CircuitState.CLOSED
        assert stats.failure_count == 0
        assert stats.success_count == 0
        assert stats.last_failure_time is None
        assert stats.consecutive_opens == 0
        assert stats.current_recovery_timeout == 30.0
        assert stats.health_window_failure_rate is None

    def test_stats_after_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        stats = cb.get_stats()
        assert stats.failure_count == 2
        assert stats.last_failure_time is not None
        assert stats.state is CircuitState.CLOSED

    def test_stats_after_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        stats = cb.get_stats()
        assert stats.state is CircuitState.OPEN
        assert stats.consecutive_opens == 1

    def test_stats_tracks_success_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=5)
        cb.call(_succeeding)
        cb.call(_succeeding)
        cb.call(_succeeding)
        stats = cb.get_stats()
        assert stats.success_count == 3

    def test_stats_returns_frozen_dataclass(self) -> None:
        cb = CircuitBreaker()
        stats = cb.get_stats()
        assert isinstance(stats, CircuitBreakerStats)
        with pytest.raises(AttributeError):
            stats.state = CircuitState.OPEN  # type: ignore[misc]

    def test_stats_with_health_window(self) -> None:
        hw = HealthWindow(window_size=60.0, failure_rate_threshold=0.8, min_calls=2)
        cb = CircuitBreaker(failure_threshold=100, health_window=hw)

        with pytest.raises(RuntimeError):
            cb.call(_failing)
        cb.call(_succeeding)

        stats = cb.get_stats()
        assert stats.health_window_failure_rate is not None
        assert stats.health_window_failure_rate == 0.5

    def test_stats_without_health_window(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        stats = cb.get_stats()
        assert stats.health_window_failure_rate is None


class TestCallbacksOnStateTransitions:
    def test_on_open_callback(self) -> None:
        events: list[str] = []
        cb = CircuitBreaker(
            failure_threshold=2,
            on_open=lambda: events.append("open"),
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)
        assert events == ["open"]

    def test_on_close_callback(self) -> None:
        events: list[str] = []
        cb = CircuitBreaker(
            failure_threshold=2,
            on_close=lambda: events.append("close"),
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        cb._state = CircuitState.HALF_OPEN
        cb._half_open_calls = 0
        cb.call(_succeeding)
        assert events == ["close"]

    def test_on_half_open_callback(self) -> None:
        events: list[str] = []
        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=10,
            on_half_open=lambda: events.append("half_open"),
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(_failing)

        with patch("philiprehberger_circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = cb._last_failure_time + 11  # type: ignore[operator]
            _ = cb.state

        assert events == ["half_open"]

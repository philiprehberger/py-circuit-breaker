"""Circuit breaker pattern for fault-tolerant service calls."""

from __future__ import annotations

import enum
import functools
import threading
import time
from typing import Any, Callable, TypeVar

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "ExceptionFilter",
    "circuit_breaker",
]

T = TypeVar("T")


class CircuitState(enum.Enum):
    """Possible states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""

    def __init__(self, breaker: CircuitBreaker) -> None:
        self.breaker = breaker
        super().__init__(f"Circuit breaker is open (failures={breaker._failure_count})")


class ExceptionFilter:
    """Configures which exceptions count as failures, with optional per-type thresholds.

    If per-type thresholds are provided, those exception types use their own
    independent counters. All other exceptions matching ``base_exceptions``
    contribute to the default failure counter.

    Args:
        base_exceptions: Tuple of exception types that count as failures.
        thresholds: Mapping of exception type to its own failure threshold.
            When the count for a specific type reaches its threshold the
            circuit opens immediately, regardless of the default counter.
    """

    def __init__(
        self,
        base_exceptions: tuple[type[BaseException], ...] = (Exception,),
        thresholds: dict[type[BaseException], int] | None = None,
    ) -> None:
        self.base_exceptions = base_exceptions
        self.thresholds: dict[type[BaseException], int] = thresholds or {}
        self._counts: dict[type[BaseException], int] = {exc: 0 for exc in self.thresholds}

    def matches(self, exc: BaseException) -> bool:
        """Return True if *exc* is considered a trackable failure."""
        return isinstance(exc, self.base_exceptions)

    def record(self, exc: BaseException) -> bool:
        """Record a failure and return True if a per-type threshold was reached."""
        for exc_type, threshold in self.thresholds.items():
            if isinstance(exc, exc_type):
                self._counts[exc_type] = self._counts.get(exc_type, 0) + 1
                if self._counts[exc_type] >= threshold:
                    return True
        return False

    def reset(self) -> None:
        """Reset all per-type counters."""
        self._counts = {exc: 0 for exc in self.thresholds}


class CircuitBreaker:
    """Thread-safe circuit breaker for wrapping unreliable calls.

    Transitions:
        CLOSED  -> OPEN       after ``failure_threshold`` consecutive failures
        OPEN    -> HALF_OPEN  after ``recovery_timeout`` seconds have elapsed
        HALF_OPEN -> CLOSED   on the next successful call
        HALF_OPEN -> OPEN     on the next failed call

    Args:
        failure_threshold: Number of consecutive failures before opening the circuit.
        recovery_timeout: Seconds to wait before transitioning from OPEN to HALF_OPEN.
        expected_exceptions: Tuple of exception types that count as failures.
            Ignored when *exception_filter* is provided.
        on_open: Callback invoked when the circuit transitions to OPEN.
        on_close: Callback invoked when the circuit transitions to CLOSED.
        on_half_open: Callback invoked when the circuit transitions to HALF_OPEN.
        exception_filter: An :class:`ExceptionFilter` for per-exception-type thresholds.
        backoff_multiplier: Multiplier for exponential backoff on recovery timeout.
            Set to ``1.0`` (default) for fixed timeout. Values > 1 cause the
            recovery timeout to increase with each consecutive trip to OPEN.
        max_recovery_timeout: Upper bound for the recovery timeout when using
            exponential backoff.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30,
        expected_exceptions: tuple[type[BaseException], ...] = (Exception,),
        *,
        on_open: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        on_half_open: Callable[[], None] | None = None,
        exception_filter: ExceptionFilter | None = None,
        backoff_multiplier: float = 1.0,
        max_recovery_timeout: float = 300.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions
        self.on_open = on_open
        self.on_close = on_close
        self.on_half_open = on_half_open
        self.exception_filter = exception_filter
        self.backoff_multiplier = backoff_multiplier
        self.max_recovery_timeout = max_recovery_timeout

        self._failure_count: int = 0
        self._consecutive_opens: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._last_failure_time: float | None = None
        self._current_recovery_timeout: float = recovery_timeout
        self._lock = threading.Lock()

    def _effective_recovery_timeout(self) -> float:
        """Return the current recovery timeout (may be increased by backoff)."""
        return self._current_recovery_timeout

    def _fire_callback(self, callback: Callable[[], None] | None) -> None:
        """Invoke a callback outside the lock if it is not None."""
        if callback is not None:
            callback()

    def _check_open_to_half_open(self) -> bool:
        """Check whether OPEN should transition to HALF_OPEN. Must be called under lock."""
        if (
            self._state is CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self._effective_recovery_timeout()
        ):
            self._state = CircuitState.HALF_OPEN
            return True
        return False

    @property
    def state(self) -> CircuitState:
        """Return the current circuit state, transitioning OPEN -> HALF_OPEN when appropriate."""
        fire_half_open = False
        with self._lock:
            fire_half_open = self._check_open_to_half_open()
            current = self._state
        if fire_half_open:
            self._fire_callback(self.on_half_open)
        return current

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute *fn* through the circuit breaker.

        Raises:
            CircuitOpenError: If the circuit is currently open.
        """
        fire_half_open = False
        with self._lock:
            fire_half_open = self._check_open_to_half_open()
            if self._state is CircuitState.OPEN:
                raise CircuitOpenError(self)

        if fire_half_open:
            self._fire_callback(self.on_half_open)

        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            # Check whether this exception counts as a failure
            is_failure = False
            if self.exception_filter is not None:
                is_failure = self.exception_filter.matches(exc)
            else:
                is_failure = isinstance(exc, self.expected_exceptions)

            if is_failure:
                fire_open = False
                with self._lock:
                    self._failure_count += 1
                    self._last_failure_time = time.monotonic()

                    # Check per-type threshold (if filter exists)
                    per_type_tripped = False
                    if self.exception_filter is not None:
                        per_type_tripped = self.exception_filter.record(exc)

                    should_open = (
                        per_type_tripped
                        or self._failure_count >= self.failure_threshold
                        or self._state is CircuitState.HALF_OPEN
                    )

                    if should_open:
                        prev = self._state
                        self._state = CircuitState.OPEN
                        self._consecutive_opens += 1
                        # Apply exponential backoff
                        if self.backoff_multiplier > 1.0:
                            self._current_recovery_timeout = min(
                                self.recovery_timeout
                                * (self.backoff_multiplier ** self._consecutive_opens),
                                self.max_recovery_timeout,
                            )
                        if prev is not CircuitState.OPEN:
                            fire_open = True

                if fire_open:
                    self._fire_callback(self.on_open)

            raise
        else:
            fire_close = False
            with self._lock:
                prev = self._state
                self._failure_count = 0
                self._consecutive_opens = 0
                self._current_recovery_timeout = self.recovery_timeout
                self._state = CircuitState.CLOSED
                if self.exception_filter is not None:
                    self.exception_filter.reset()
                if prev is not CircuitState.CLOSED:
                    fire_close = True

            if fire_close:
                self._fire_callback(self.on_close)
            return result

    def reset(self) -> None:
        """Reset the circuit breaker to the closed state."""
        with self._lock:
            self._failure_count = 0
            self._consecutive_opens = 0
            self._current_recovery_timeout = self.recovery_timeout
            self._state = CircuitState.CLOSED
            self._last_failure_time = None
            if self.exception_filter is not None:
                self.exception_filter.reset()


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: float = 30,
    expected_exceptions: tuple[type[BaseException], ...] = (Exception,),
    *,
    on_open: Callable[[], None] | None = None,
    on_close: Callable[[], None] | None = None,
    on_half_open: Callable[[], None] | None = None,
    exception_filter: ExceptionFilter | None = None,
    backoff_multiplier: float = 1.0,
    max_recovery_timeout: float = 300.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator factory that wraps a function with a :class:`CircuitBreaker`.

    The :class:`CircuitBreaker` instance is accessible as the ``.breaker``
    attribute on the decorated function.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exceptions=expected_exceptions,
            on_open=on_open,
            on_close=on_close,
            on_half_open=on_half_open,
            exception_filter=exception_filter,
            backoff_multiplier=backoff_multiplier,
            max_recovery_timeout=max_recovery_timeout,
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return breaker.call(fn, *args, **kwargs)

        wrapper.breaker = breaker  # type: ignore[attr-defined]
        return wrapper

    return decorator

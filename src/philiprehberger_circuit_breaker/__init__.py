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


class CircuitBreaker:
    """Thread-safe circuit breaker for wrapping unreliable calls.

    Transitions:
        CLOSED  -> OPEN       after ``failure_threshold`` consecutive failures
        OPEN    -> HALF_OPEN  after ``recovery_timeout`` seconds have elapsed
        HALF_OPEN -> CLOSED   on the next successful call
        HALF_OPEN -> OPEN     on the next failed call
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30,
        expected_exceptions: tuple[type[BaseException], ...] = (Exception,),
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions

        self._failure_count: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Return the current circuit state, transitioning OPEN -> HALF_OPEN when appropriate."""
        with self._lock:
            if (
                self._state is CircuitState.OPEN
                and self._last_failure_time is not None
                and time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
            return self._state

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute *fn* through the circuit breaker.

        Raises:
            CircuitOpenError: If the circuit is currently open.
        """
        with self._lock:
            if (
                self._state is CircuitState.OPEN
                and self._last_failure_time is not None
                and time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN

            if self._state is CircuitState.OPEN:
                raise CircuitOpenError(self)

        try:
            result = fn(*args, **kwargs)
        except self.expected_exceptions:
            with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                if self._failure_count >= self.failure_threshold or self._state is CircuitState.HALF_OPEN:
                    self._state = CircuitState.OPEN
            raise
        else:
            with self._lock:
                self._failure_count = 0
                self._state = CircuitState.CLOSED
            return result

    def reset(self) -> None:
        """Reset the circuit breaker to the closed state."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: float = 30,
    expected_exceptions: tuple[type[BaseException], ...] = (Exception,),
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
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return breaker.call(fn, *args, **kwargs)

        wrapper.breaker = breaker  # type: ignore[attr-defined]
        return wrapper

    return decorator

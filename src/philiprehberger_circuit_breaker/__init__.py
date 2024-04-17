"""Circuit breaker pattern for fault-tolerant service calls."""

from __future__ import annotations

import collections
import enum
import functools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerStats",
    "CircuitOpenError",
    "CircuitState",
    "ExceptionFilter",
    "HealthWindow",
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


class HealthWindow:
    """Tracks success and failure counts over a rolling time window.

    Instead of tripping on consecutive failures, the circuit opens when the
    failure rate within the window exceeds a configurable threshold.

    Args:
        window_size: Duration of the rolling window in seconds.
        failure_rate_threshold: Failure rate (0.0 to 1.0) that triggers
            the circuit to open.
        min_calls: Minimum number of calls within the window before the
            failure rate is evaluated. Prevents tripping on a single early failure.
    """

    def __init__(
        self,
        window_size: float = 60.0,
        failure_rate_threshold: float = 0.5,
        min_calls: int = 5,
    ) -> None:
        self.window_size = window_size
        self.failure_rate_threshold = failure_rate_threshold
        self.min_calls = min_calls
        self._successes: collections.deque[float] = collections.deque()
        self._failures: collections.deque[float] = collections.deque()

    def _prune(self, now: float) -> None:
        """Remove entries older than the window."""
        cutoff = now - self.window_size
        while self._successes and self._successes[0] < cutoff:
            self._successes.popleft()
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def record_success(self, now: float | None = None) -> None:
        """Record a successful call."""
        now = now if now is not None else time.monotonic()
        self._prune(now)
        self._successes.append(now)

    def record_failure(self, now: float | None = None) -> None:
        """Record a failed call."""
        now = now if now is not None else time.monotonic()
        self._prune(now)
        self._failures.append(now)

    def should_open(self, now: float | None = None) -> bool:
        """Return True if the failure rate exceeds the threshold."""
        now = now if now is not None else time.monotonic()
        self._prune(now)
        total = len(self._successes) + len(self._failures)
        if total < self.min_calls:
            return False
        failure_rate = len(self._failures) / total
        return failure_rate >= self.failure_rate_threshold

    def failure_rate(self, now: float | None = None) -> float:
        """Return the current failure rate within the window (0.0 to 1.0)."""
        now = now if now is not None else time.monotonic()
        self._prune(now)
        total = len(self._successes) + len(self._failures)
        if total == 0:
            return 0.0
        return len(self._failures) / total

    def reset(self) -> None:
        """Clear all recorded calls."""
        self._successes.clear()
        self._failures.clear()


@dataclass(frozen=True)
class CircuitBreakerStats:
    """Snapshot of circuit breaker statistics for observability."""

    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: float | None
    consecutive_opens: int
    current_recovery_timeout: float
    health_window_failure_rate: float | None = field(default=None)


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
        half_open_max_calls: Maximum number of probe calls allowed in half-open
            state before a success is required. Defaults to ``1``.
        health_window: An optional :class:`HealthWindow` for rolling-window
            failure rate tracking. When provided, the circuit also opens if the
            failure rate exceeds the window threshold, even if the consecutive
            failure threshold has not been reached.
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
        half_open_max_calls: int = 1,
        health_window: HealthWindow | None = None,
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
        self.half_open_max_calls = half_open_max_calls
        self.health_window = health_window

        self._failure_count: int = 0
        self._success_count: int = 0
        self._consecutive_opens: int = 0
        self._half_open_calls: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._last_failure_time: float | None = None
        self._current_recovery_timeout: float = recovery_timeout
        self._lock = threading.Lock()
        self._listeners: dict[str, list[Callable[[], None]]] = {
            "on_open": [],
            "on_close": [],
            "on_half_open": [],
        }

    def add_listener(self, event: str, callback: Callable[[], None]) -> None:
        """Register a callback for a state transition event.

        Args:
            event: One of ``"on_open"``, ``"on_close"``, or ``"on_half_open"``.
            callback: A callable invoked when the event fires.

        Raises:
            ValueError: If *event* is not a recognized event name.
        """
        if event not in self._listeners:
            raise ValueError(
                f"Unknown event {event!r}. Must be one of: "
                f"{', '.join(sorted(self._listeners))}"
            )
        self._listeners[event].append(callback)

    def remove_listener(self, event: str, callback: Callable[[], None]) -> None:
        """Remove a previously registered callback.

        Args:
            event: One of ``"on_open"``, ``"on_close"``, or ``"on_half_open"``.
            callback: The callback to remove.

        Raises:
            ValueError: If *event* is not recognized or *callback* was not registered.
        """
        if event not in self._listeners:
            raise ValueError(
                f"Unknown event {event!r}. Must be one of: "
                f"{', '.join(sorted(self._listeners))}"
            )
        self._listeners[event].remove(callback)

    def _effective_recovery_timeout(self) -> float:
        """Return the current recovery timeout (may be increased by backoff)."""
        return self._current_recovery_timeout

    def _fire_callback(self, callback: Callable[[], None] | None, event: str) -> None:
        """Invoke a callback and all registered listeners outside the lock."""
        if callback is not None:
            callback()
        for listener in self._listeners.get(event, []):
            listener()

    def _check_open_to_half_open(self) -> bool:
        """Check whether OPEN should transition to HALF_OPEN. Must be called under lock."""
        if (
            self._state is CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self._effective_recovery_timeout()
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
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
            self._fire_callback(self.on_half_open, "on_half_open")
        return current

    def get_state(self) -> CircuitState:
        """Return the current circuit state.

        Equivalent to the ``state`` property but provided as a method for
        consistency with ``get_stats()``.
        """
        return self.state

    def get_stats(self) -> CircuitBreakerStats:
        """Return a snapshot of circuit breaker statistics."""
        with self._lock:
            hw_rate: float | None = None
            if self.health_window is not None:
                hw_rate = self.health_window.failure_rate()
            return CircuitBreakerStats(
                state=self._state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                last_failure_time=self._last_failure_time,
                consecutive_opens=self._consecutive_opens,
                current_recovery_timeout=self._current_recovery_timeout,
                health_window_failure_rate=hw_rate,
            )

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute *fn* through the circuit breaker.

        Raises:
            CircuitOpenError: If the circuit is currently open or half-open
                probe limit is exceeded.
        """
        fire_half_open = False
        with self._lock:
            fire_half_open = self._check_open_to_half_open()
            if self._state is CircuitState.OPEN:
                raise CircuitOpenError(self)
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(self)
                self._half_open_calls += 1

        if fire_half_open:
            self._fire_callback(self.on_half_open, "on_half_open")

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

                    if self.health_window is not None:
                        self.health_window.record_failure()

                    # Check per-type threshold (if filter exists)
                    per_type_tripped = False
                    if self.exception_filter is not None:
                        per_type_tripped = self.exception_filter.record(exc)

                    # Check health window threshold
                    hw_tripped = False
                    if self.health_window is not None:
                        hw_tripped = self.health_window.should_open()

                    should_open = (
                        per_type_tripped
                        or hw_tripped
                        or self._failure_count >= self.failure_threshold
                        or self._state is CircuitState.HALF_OPEN
                    )

                    if should_open:
                        prev = self._state
                        self._state = CircuitState.OPEN
                        self._half_open_calls = 0
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
                    self._fire_callback(self.on_open, "on_open")

            raise
        else:
            fire_close = False
            with self._lock:
                prev = self._state
                self._failure_count = 0
                self._success_count += 1
                self._consecutive_opens = 0
                self._half_open_calls = 0
                self._current_recovery_timeout = self.recovery_timeout
                self._state = CircuitState.CLOSED
                if self.health_window is not None:
                    self.health_window.record_success()
                if self.exception_filter is not None:
                    self.exception_filter.reset()
                if prev is not CircuitState.CLOSED:
                    fire_close = True

            if fire_close:
                self._fire_callback(self.on_close, "on_close")
            return result

    def reset(self) -> None:
        """Reset the circuit breaker to the closed state."""
        with self._lock:
            self._failure_count = 0
            self._success_count = 0
            self._consecutive_opens = 0
            self._half_open_calls = 0
            self._current_recovery_timeout = self.recovery_timeout
            self._state = CircuitState.CLOSED
            self._last_failure_time = None
            if self.exception_filter is not None:
                self.exception_filter.reset()
            if self.health_window is not None:
                self.health_window.reset()


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
    half_open_max_calls: int = 1,
    health_window: HealthWindow | None = None,
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
            half_open_max_calls=half_open_max_calls,
            health_window=health_window,
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return breaker.call(fn, *args, **kwargs)

        wrapper.breaker = breaker  # type: ignore[attr-defined]
        return wrapper

    return decorator

# philiprehberger-circuit-breaker

[![Tests](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml/badge.svg)](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml)
[![PyPI version](https://img.shields.io/pypi/v/philiprehberger-circuit-breaker.svg)](https://pypi.org/project/philiprehberger-circuit-breaker/)
[![Last updated](https://img.shields.io/github/last-commit/philiprehberger/py-circuit-breaker)](https://github.com/philiprehberger/py-circuit-breaker/commits/main)

Circuit breaker pattern for fault-tolerant service calls.

## Installation

```bash
pip install philiprehberger-circuit-breaker
```

## Usage

```python
from philiprehberger_circuit_breaker import circuit_breaker

@circuit_breaker(failure_threshold=3, recovery_timeout=60)
def call_external_service():
    return requests.get("https://api.example.com/data").json()

result = call_external_service()
```

### Class-Based Usage

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

result = breaker.call(requests.get, "https://api.example.com/data")
```

### Handling Open Circuits

```python
from philiprehberger_circuit_breaker import CircuitOpenError, circuit_breaker

@circuit_breaker(failure_threshold=3)
def fetch_data():
    return requests.get("https://api.example.com").json()

try:
    result = fetch_data()
except CircuitOpenError:
    result = cached_fallback()
```

### Checking Circuit State

```python
from philiprehberger_circuit_breaker import CircuitBreaker, CircuitState

breaker = CircuitBreaker(failure_threshold=3)

if breaker.get_state() is CircuitState.OPEN:
    print("Circuit is open, using fallback")
```

### Observability with get_stats

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

stats = breaker.get_stats()
print(f"State: {stats.state.value}")
print(f"Failures: {stats.failure_count}")
print(f"Successes: {stats.success_count}")
print(f"Last failure: {stats.last_failure_time}")
print(f"Recovery timeout: {stats.current_recovery_timeout}s")
```

### State Transition Callbacks

Register callbacks to be notified when the circuit changes state.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

def notify_open():
    print("Circuit opened!")

breaker = CircuitBreaker(
    failure_threshold=3,
    on_open=notify_open,
    on_close=lambda: print("Circuit closed."),
    on_half_open=lambda: print("Circuit half-open."),
)

result = breaker.call(requests.get, "https://api.example.com/data")
```

### Event Listeners

Register multiple callbacks for the same state transition event.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(failure_threshold=3)

breaker.add_listener("on_open", lambda: print("Listener 1: opened"))
breaker.add_listener("on_open", lambda: print("Listener 2: opened"))
breaker.add_listener("on_close", lambda: print("Service recovered"))

# Remove a listener when no longer needed
def my_handler():
    print("temporary handler")

breaker.add_listener("on_open", my_handler)
breaker.remove_listener("on_open", my_handler)
```

### Per-Exception-Type Failure Thresholds

Use `ExceptionFilter` to configure which exceptions count as failures and set independent thresholds per exception type.

```python
from philiprehberger_circuit_breaker import CircuitBreaker, ExceptionFilter

exc_filter = ExceptionFilter(
    base_exceptions=(ConnectionError, TimeoutError, OSError),
    thresholds={TimeoutError: 2},
)

breaker = CircuitBreaker(failure_threshold=5, exception_filter=exc_filter)
result = breaker.call(requests.get, "https://api.example.com/data")
```

### Half-Open Probe Limiting

Control how many test calls are allowed in the half-open state before requiring a success.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=30,
    half_open_max_calls=3,
)
```

### Health Window

Track success rate over a rolling time window instead of relying solely on consecutive failures.

```python
from philiprehberger_circuit_breaker import CircuitBreaker, HealthWindow

health_window = HealthWindow(
    window_size=60.0,
    failure_rate_threshold=0.5,
    min_calls=10,
)

breaker = CircuitBreaker(
    failure_threshold=100,
    health_window=health_window,
)

result = breaker.call(requests.get, "https://api.example.com/data")
```

### Exponential Backoff on Recovery Timeout

Instead of a fixed recovery timeout, the timeout can increase exponentially with consecutive circuit trips.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=10,
    backoff_multiplier=2.0,
    max_recovery_timeout=300.0,
)
```

### Resetting the Circuit

```python
from philiprehberger_circuit_breaker import circuit_breaker

@circuit_breaker(failure_threshold=3)
def my_service_call():
    ...

my_service_call.breaker.reset()
```

## API

### `CircuitBreaker`

| Function / Class | Description |
|------------------|-------------|
| `CircuitBreaker(failure_threshold, recovery_timeout, expected_exceptions, *, on_open, on_close, on_half_open, exception_filter, backoff_multiplier, max_recovery_timeout, half_open_max_calls, health_window)` | Create a circuit breaker instance |
| `call(fn, *args, **kwargs)` | Execute a function through the circuit breaker |
| `state` | Current circuit state (`CLOSED`, `OPEN`, or `HALF_OPEN`) |
| `get_state()` | Return the current circuit state |
| `get_stats()` | Return a `CircuitBreakerStats` snapshot |
| `add_listener(event, callback)` | Register a callback for a state transition event |
| `remove_listener(event, callback)` | Remove a previously registered callback |
| `reset()` | Reset the circuit breaker to the closed state |

### `CircuitBreakerStats`

| Function / Class | Description |
|------------------|-------------|
| `state` | Current circuit state |
| `failure_count` | Total failure count |
| `success_count` | Total success count |
| `last_failure_time` | Monotonic timestamp of the last failure, or `None` |
| `consecutive_opens` | Number of consecutive times the circuit has opened |
| `current_recovery_timeout` | Current recovery timeout in seconds |
| `health_window_failure_rate` | Failure rate from the health window, or `None` |

### `CircuitState`

| Function / Class | Description |
|------------------|-------------|
| `CLOSED` | Normal operation, calls pass through |
| `OPEN` | Circuit tripped, calls are rejected |
| `HALF_OPEN` | Recovery probe, next call determines transition |

### `CircuitOpenError`

| Function / Class | Description |
|------------------|-------------|
| `breaker` | Reference to the `CircuitBreaker` that raised the error |

### `ExceptionFilter`

| Function / Class | Description |
|------------------|-------------|
| `ExceptionFilter(base_exceptions, thresholds)` | Create an exception filter with optional per-type thresholds |
| `matches(exc)` | Return True if the exception counts as a failure |
| `record(exc)` | Record a failure; returns True if a per-type threshold was reached |
| `reset()` | Reset all per-type counters |

### `HealthWindow`

| Function / Class | Description |
|------------------|-------------|
| `HealthWindow(window_size, failure_rate_threshold, min_calls)` | Create a rolling health window |
| `record_success(now)` | Record a successful call |
| `record_failure(now)` | Record a failed call |
| `should_open(now)` | Return True if failure rate exceeds threshold |
| `failure_rate(now)` | Return current failure rate (0.0 to 1.0) |
| `reset()` | Clear all recorded calls |

### `circuit_breaker`

| Function / Class | Description |
|------------------|-------------|
| `circuit_breaker(failure_threshold, recovery_timeout, expected_exceptions, *, on_open, on_close, on_half_open, exception_filter, backoff_multiplier, max_recovery_timeout, half_open_max_calls, health_window)` | Decorator factory that wraps a function with a `CircuitBreaker` |

## Development

```bash
pip install -e .
python -m pytest tests/ -v
```

## Support

If you find this project useful:

⭐ [Star the repo](https://github.com/philiprehberger/py-circuit-breaker)

🐛 [Report issues](https://github.com/philiprehberger/py-circuit-breaker/issues?q=is%3Aissue+is%3Aopen+label%3Abug)

💡 [Suggest features](https://github.com/philiprehberger/py-circuit-breaker/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement)

❤️ [Sponsor development](https://github.com/sponsors/philiprehberger)

🌐 [All Open Source Projects](https://philiprehberger.com/open-source-packages)

💻 [GitHub Profile](https://github.com/philiprehberger)

🔗 [LinkedIn Profile](https://www.linkedin.com/in/philiprehberger)

## License

[MIT](LICENSE)

# philiprehberger-circuit-breaker

[![Tests](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml/badge.svg)](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml) [![PyPI version](https://img.shields.io/pypi/v/philiprehberger-circuit-breaker.svg)](https://pypi.org/project/philiprehberger-circuit-breaker/) [![GitHub release](https://img.shields.io/github/v/release/philiprehberger/py-circuit-breaker)](https://github.com/philiprehberger/py-circuit-breaker/releases) [![Last updated](https://img.shields.io/github/last-commit/philiprehberger/py-circuit-breaker)](https://github.com/philiprehberger/py-circuit-breaker/commits/main) [![License](https://img.shields.io/github/license/philiprehberger/py-circuit-breaker)](LICENSE) [![Bug Reports](https://img.shields.io/github/issues/philiprehberger/py-circuit-breaker/bug)](https://github.com/philiprehberger/py-circuit-breaker/issues?q=is%3Aissue+is%3Aopen+label%3Abug) [![Feature Requests](https://img.shields.io/github/issues/philiprehberger/py-circuit-breaker/enhancement)](https://github.com/philiprehberger/py-circuit-breaker/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement) [![Sponsor](https://img.shields.io/badge/sponsor-GitHub%20Sponsors-ec6cb9)](https://github.com/sponsors/philiprehberger)

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
from philiprehberger_circuit_breaker import CircuitState, circuit_breaker

@circuit_breaker(failure_threshold=3)
def my_service_call():
    ...

if my_service_call.breaker.state is CircuitState.OPEN:
    print("Circuit is open, using fallback")
```

### State Transition Callbacks

Register callbacks to be notified when the circuit changes state, useful for monitoring and alerting.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

def notify_open():
    print("Circuit opened! Alerting on-call team.")

def notify_close():
    print("Circuit closed. Service recovered.")

def notify_half_open():
    print("Circuit half-open. Testing recovery...")

breaker = CircuitBreaker(
    failure_threshold=3,
    on_open=notify_open,
    on_close=notify_close,
    on_half_open=notify_half_open,
)

result = breaker.call(requests.get, "https://api.example.com/data")
```

### Per-Exception-Type Failure Thresholds

Use `ExceptionFilter` to configure which exceptions count as failures and set independent thresholds per exception type.

```python
from philiprehberger_circuit_breaker import CircuitBreaker, ExceptionFilter

# TimeoutError opens the circuit after 2 occurrences,
# all other exceptions use the default failure_threshold (5)
exc_filter = ExceptionFilter(
    base_exceptions=(ConnectionError, TimeoutError, OSError),
    thresholds={TimeoutError: 2},
)

breaker = CircuitBreaker(failure_threshold=5, exception_filter=exc_filter)
result = breaker.call(requests.get, "https://api.example.com/data")
```

### Exponential Backoff on Recovery Timeout

Instead of a fixed recovery timeout, the timeout can increase exponentially with consecutive circuit trips.

```python
from philiprehberger_circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=10,         # initial timeout: 10 seconds
    backoff_multiplier=2.0,      # double the timeout each time
    max_recovery_timeout=300.0,  # cap at 5 minutes
)

# First trip: 20s, second trip: 40s, third trip: 80s, ... up to 300s
result = breaker.call(requests.get, "https://api.example.com/data")
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

| Method / Property | Description |
|-------------------|-------------|
| `CircuitBreaker(failure_threshold, recovery_timeout, expected_exceptions, *, on_open, on_close, on_half_open, exception_filter, backoff_multiplier, max_recovery_timeout)` | Create a circuit breaker instance |
| `call(fn, *args, **kwargs)` | Execute a function through the circuit breaker |
| `state` | Current circuit state (`CLOSED`, `OPEN`, or `HALF_OPEN`) |
| `reset()` | Reset the circuit breaker to the closed state |

### `CircuitState`

| Value | Description |
|-------|-------------|
| `CLOSED` | Normal operation, calls pass through |
| `OPEN` | Circuit tripped, calls are rejected |
| `HALF_OPEN` | Recovery probe, next call determines transition |

### `CircuitOpenError`

| Attribute | Description |
|-----------|-------------|
| `breaker` | Reference to the `CircuitBreaker` that raised the error |

### `ExceptionFilter`

| Method / Property | Description |
|-------------------|-------------|
| `ExceptionFilter(base_exceptions, thresholds)` | Create an exception filter with optional per-type thresholds |
| `matches(exc)` | Return True if the exception counts as a failure |
| `record(exc)` | Record a failure; returns True if a per-type threshold was reached |
| `reset()` | Reset all per-type counters |

### `circuit_breaker`

| Function | Description |
|----------|-------------|
| `circuit_breaker(failure_threshold, recovery_timeout, expected_exceptions, *, on_open, on_close, on_half_open, exception_filter, backoff_multiplier, max_recovery_timeout)` | Decorator factory that wraps a function with a `CircuitBreaker` |

## Development

```bash
pip install -e .
python -m pytest tests/ -v
```

## Support

If you find this package useful, consider giving it a star on GitHub — it helps motivate continued maintenance and development.

[![LinkedIn](https://img.shields.io/badge/Philip%20Rehberger-LinkedIn-0A66C2?logo=linkedin)](https://www.linkedin.com/in/philiprehberger)
[![More packages](https://img.shields.io/badge/more-open%20source%20packages-blue)](https://philiprehberger.com/open-source-packages)

## License

[MIT](LICENSE)

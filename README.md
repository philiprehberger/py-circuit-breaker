# philiprehberger-circuit-breaker

[![Tests](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml/badge.svg)](https://github.com/philiprehberger/py-circuit-breaker/actions/workflows/publish.yml)
[![PyPI version](https://img.shields.io/pypi/v/philiprehberger-circuit-breaker.svg)](https://pypi.org/project/philiprehberger-circuit-breaker/)
[![License](https://img.shields.io/github/license/philiprehberger/py-circuit-breaker)](LICENSE)
[![Sponsor](https://img.shields.io/badge/sponsor-GitHub%20Sponsors-ec6cb9)](https://github.com/sponsors/philiprehberger)

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

| Method | Description |
|--------|-------------|
| `CircuitBreaker(failure_threshold, recovery_timeout, expected_exceptions)` | Create a circuit breaker instance |
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

### `circuit_breaker`

| Function | Description |
|----------|-------------|
| `circuit_breaker(failure_threshold, recovery_timeout, expected_exceptions)` | Decorator factory that wraps a function with a `CircuitBreaker` |

## Development

```bash
pip install -e .
python -m pytest tests/ -v
```

## License

MIT

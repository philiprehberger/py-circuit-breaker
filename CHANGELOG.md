# Changelog

## 0.2.0 (2026-03-28)

- Add state transition event callbacks (on_open, on_close, on_half_open)
- Add per-exception-type failure thresholds via ExceptionFilter
- Add exponential backoff on recovery timeout
- Bring package into full compliance with guides

## 0.1.0 (2026-03-21)

- Initial release
- Circuit breaker pattern with CLOSED, OPEN, and HALF_OPEN states
- Thread-safe implementation with configurable failure threshold and recovery timeout
- Decorator factory for wrapping functions with circuit breaker logic
- CircuitOpenError exception with breaker reference

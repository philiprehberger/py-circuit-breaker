# Changelog

## 0.1.0 (2026-03-21)

- Initial release
- Circuit breaker pattern with CLOSED, OPEN, and HALF_OPEN states
- Thread-safe implementation with configurable failure threshold and recovery timeout
- Decorator factory for wrapping functions with circuit breaker logic
- CircuitOpenError exception with breaker reference

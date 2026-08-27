from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit is open and calls should fail fast."""


@dataclass(slots=True)
class CircuitBreaker:
    """Circuit breaker skeleton.

    Production-safe state machine:
    - CLOSED: calls pass through; count failures.
    - OPEN: fail fast until reset timeout elapses.
    - HALF_OPEN: allow a probe; close on success or re-open on failure.
    """

    name: str
    failure_threshold: int
    reset_timeout_seconds: float
    success_threshold: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    opened_at: float | None = None
    transition_log: list[dict[str, str | float]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _probe_in_flight: bool = field(default=False, repr=False)

    def allow_request(self) -> bool:
        """Return whether a request should be attempted.

        State-based gate logic:
        - CLOSED → always allow
        - HALF_OPEN → allow (probe request)
        - OPEN → check if reset_timeout_seconds has elapsed since opened_at
          - If elapsed: transition to HALF_OPEN (use _transition()) and allow
          - If not elapsed: deny (return False)

        Use time.monotonic() for elapsed time comparison.
        """
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
                return True
            if self.opened_at is None:
                self.opened_at = time.monotonic()
            if time.monotonic() - self.opened_at >= self.reset_timeout_seconds:
                self._transition(CircuitState.HALF_OPEN, "reset_timeout_elapsed")
                self._probe_in_flight = True
                return True
            return False

    def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Call a function through the circuit breaker.

        Wrapper behavior:
        1. Check allow_request() — if denied, raise CircuitOpenError
        2. Try calling fn(*args, **kwargs)
        3. On success: call record_success() and return the result
        4. On exception: call record_failure() and re-raise
        """
        if not self.allow_request():
            raise CircuitOpenError(f"circuit breaker {self.name} is open")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def record_success(self) -> None:
        """Record a successful call.

        Success counter behavior:
        1. Reset failure_count to 0
        2. Increment success_count
        3. If in HALF_OPEN and success_count >= success_threshold:
           - Transition to CLOSED with reason "probe_success"
           - Reset success_count to 0
        """
        with self._lock:
            self.failure_count = 0
            self.success_count += 1
            self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN and self.success_count >= self.success_threshold:
                self._transition(CircuitState.CLOSED, "probe_success")
                self.success_count = 0

    def record_failure(self) -> None:
        """Record a failed call.

        Failure counter behavior:
        1. Increment failure_count, reset success_count to 0
        2. If in HALF_OPEN state:
           - Immediately transition to OPEN with reason "probe_failure"
           - Set opened_at = time.monotonic()
        3. Else if failure_count >= failure_threshold:
           - Transition to OPEN with reason "failure_threshold_reached"
           - Set opened_at = time.monotonic()

        IMPORTANT: HALF_OPEN and threshold cases need DIFFERENT reasons
        and must be handled separately (if/elif, not combined with or).
        """
        with self._lock:
            self.failure_count += 1
            self.success_count = 0
            self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN:
                self.opened_at = time.monotonic()
                self._transition(CircuitState.OPEN, "probe_failure")
            elif self.failure_count >= self.failure_threshold:
                self.opened_at = time.monotonic()
                self._transition(CircuitState.OPEN, "failure_threshold_reached")

    def _transition(self, new_state: CircuitState, reason: str) -> None:
        if self.state == new_state:
            return
        self.transition_log.append(
            {"from": self.state.value, "to": new_state.value, "reason": reason, "ts": time.time()}
        )
        self.state = new_state


class RedisCircuitBreaker(CircuitBreaker):
    """Circuit breaker whose counters and state are shared through Redis."""

    __slots__ = ("_redis", "_redis_key", "state_ttl_seconds")

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_seconds: float,
        success_threshold: int = 1,
        redis_url: str = "redis://localhost:6379/0",
        state_ttl_seconds: int = 300,
    ) -> None:
        import redis as redis_lib

        super().__init__(name, failure_threshold, reset_timeout_seconds, success_threshold)
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._redis_key = f"rl:circuit:{name}"
        self.state_ttl_seconds = state_ttl_seconds
        self._initialize_shared_state()

    def _initialize_shared_state(self) -> None:
        self._redis.hsetnx(self._redis_key, "state", CircuitState.CLOSED.value)
        self._redis.hsetnx(self._redis_key, "failure_count", 0)
        self._redis.hsetnx(self._redis_key, "success_count", 0)
        self._redis.expire(self._redis_key, self.state_ttl_seconds)

    def _sync(self) -> None:
        values = self._redis.hgetall(self._redis_key)
        if not values:
            self._initialize_shared_state()
            values = self._redis.hgetall(self._redis_key)
        self.state = CircuitState(values.get("state", CircuitState.CLOSED.value))
        self.failure_count = int(values.get("failure_count", 0))
        self.success_count = int(values.get("success_count", 0))
        opened_at = values.get("opened_at")
        self.opened_at = float(opened_at) if opened_at else None

    def _persist(self) -> None:
        mapping: dict[str, str | int | float] = {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
        }
        if self.opened_at is not None:
            mapping["opened_at"] = self.opened_at
        self._redis.hset(self._redis_key, mapping=mapping)
        self._redis.expire(self._redis_key, self.state_ttl_seconds)

    def allow_request(self) -> bool:
        with self._lock:
            self._sync()
            if self.state == CircuitState.OPEN:
                if self.opened_at is None:
                    self.opened_at = time.time()
                    self._persist()
                    return False
                if time.time() - self.opened_at < self.reset_timeout_seconds:
                    return False
                self._transition(CircuitState.HALF_OPEN, "reset_timeout_elapsed")
                self._probe_in_flight = True
                self._persist()
                return True
            if self.state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
                return True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._sync()
            self.failure_count = 0
            self.success_count += 1
            self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN and self.success_count >= self.success_threshold:
                self._transition(CircuitState.CLOSED, "probe_success")
                self.success_count = 0
                self.opened_at = None
            self._persist()

    def record_failure(self) -> None:
        with self._lock:
            self._sync()
            self.failure_count = int(self._redis.hincrby(self._redis_key, "failure_count", 1))
            self.success_count = 0
            self._probe_in_flight = False
            if self.state == CircuitState.HALF_OPEN:
                self.opened_at = time.time()
                self._transition(CircuitState.OPEN, "probe_failure")
            elif self.failure_count >= self.failure_threshold:
                self.opened_at = time.time()
                self._transition(CircuitState.OPEN, "failure_threshold_reached")
            self._persist()

    def reset_shared_state(self) -> None:
        """Delete shared state, primarily for tests and reproducible simulations."""
        self._redis.delete(self._redis_key)
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.opened_at = None
        self._initialize_shared_state()

    def close(self) -> None:
        self._redis.close()

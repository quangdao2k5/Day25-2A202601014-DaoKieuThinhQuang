from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitState, RedisCircuitBreaker
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider


@given(st.integers(min_value=1, max_value=20))
def test_property_breaker_opens_exactly_at_threshold(threshold: int) -> None:
    breaker = CircuitBreaker("property", threshold, reset_timeout_seconds=10)
    for _ in range(threshold - 1):
        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


@given(st.text(max_size=80))
def test_property_similarity_is_bounded_and_reflexive(text: str) -> None:
    assert ResponseCache.similarity(text, text) == 1.0
    score = ResponseCache.similarity(text, "independent comparison text")
    assert 0.0 <= score <= 1.0


def test_cost_budget_routes_to_cheaper_provider_after_soft_limit() -> None:
    primary = FakeLLMProvider("primary", 0.0, 1, 0.1)
    backup = FakeLLMProvider("backup", 0.0, 1, 0.001)
    breakers = {
        name: CircuitBreaker(name, 3, 1) for name in ("primary", "backup")
    }
    gateway = ReliabilityGateway(
        [primary, backup],
        breakers,
        cost_budget=0.01,
        budget_soft_limit_ratio=0.8,
    )
    gateway.cumulative_cost = 0.008
    response = gateway.complete("use the cheaper provider")
    assert response.provider == "backup"
    assert response.route == "fallback"


def test_redis_cache_gracefully_degrades_to_memory() -> None:
    cache = SharedRedisCache(
        "redis://localhost:6399/0?socket_connect_timeout=0.05",
        ttl_seconds=60,
        similarity_threshold=0.8,
    )
    cache.set("fallback query", "fallback response")
    response, score = cache.get("fallback query")
    assert response == "fallback response"
    assert score == 1.0
    assert cache.redis_error_count >= 2
    cache.close()


def _redis_available() -> bool:
    cache = SharedRedisCache("redis://localhost:6379/0", 60, 0.8)
    available = cache.ping()
    cache.close()
    return available


@pytest.mark.skipif(not _redis_available(), reason="Redis is required for shared breaker test")
def test_redis_circuit_state_is_shared_between_instances() -> None:
    first = RedisCircuitBreaker("stretch-shared", 1, 10)
    second = RedisCircuitBreaker("stretch-shared", 1, 10)
    first.reset_shared_state()
    first.record_failure()
    assert not second.allow_request()
    assert second.state == CircuitState.OPEN
    first.reset_shared_state()
    first.close()
    second.close()

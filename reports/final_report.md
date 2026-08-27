# Day 25 Reliability Engineering Report

## 1. Architecture summary

User -> Gateway -> Cache -> Circuit Breaker -> Provider chain -> Static fallback

The gateway returns cache hits immediately, skips open provider circuits, falls back in provider order, and returns a static degraded response only when every provider fails.

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Limits consecutive provider failures. |
| reset_timeout_seconds | 2 | Allows recovery probes. |
| success_threshold | 1 | Closes after a successful probe. |
| cache TTL | 300 | Limits response staleness. |
| similarity_threshold | 0.92 | Conservative semantic matching. |
| load_test requests | 100 per scenario | Reproducible local chaos run. |
| cache backend | memory | Memory by default; Redis supports shared deployments. |
| providers | primary + backup | Ordered fallback chain with independent breakers. |

## 3. SLO summary

| SLI | Target | Actual | Met? |
|---|---:|---:|---|
| Availability | >= 99% | 0.9933 | Yes |
| Latency P95 | < 2500 ms | 460.71 ms | Yes |
| Fallback success rate | >= 95% | 0.9683 | Yes |
| Cache hit rate | >= 10% | 0.64 | Yes |
| Recovery time | < 5000 ms | 2348.9938974380493 ms | Yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 0.9933 |
| error_rate | 0.0067 |
| latency_p50_ms | 1.16 |
| latency_p95_ms | 460.71 |
| latency_p99_ms | 528.99 |
| fallback_success_rate | 0.9683 |
| cache_hit_rate | 0.64 |
| circuit_open_count | 8 |
| recovery_time_ms | 2348.9938974380493 |
| estimated_cost | 0.046316 |
| estimated_cost_saved | 0.192 |

## 5. Chaos scenarios

| Scenario | Expected | Observed | Status |
|---|---|---|---|
| primary_timeout_100 | Primary opens; backup serves traffic. | Metrics and circuit transitions recorded. | pass |
| primary_flaky_50 | Primary opens/reopens; backup handles failures. | Metrics and circuit transitions recorded. | pass |
| all_healthy | Primary serves traffic with no static fallback. | Metrics and circuit transitions recorded. | pass |


## 6. Redis shared cache

Redis makes response state visible across gateway instances and applies server-side TTL. The Redis test suite verified shared state, expiry, privacy guardrails, and false-hit rejection: 6 passed.

Redis CLI evidence: `docker compose exec redis redis-cli KEYS 'rl:test:*'` shows the shared cache namespace during the test; fixtures clean it afterward.

## 7. Failure analysis

The cache reduces repeated-request latency and provider cost. False-hit guardrails reject privacy-sensitive queries and mismatched four-digit years, such as refund policy 2024 vs 2026. Remaining availability misses occur when all providers fail in the same request; production should add a third provider, health-aware routing, and bounded retries with jitter.


## 8. Cache comparison

| Metric | With cache | Without cache |
|---|---:|---:|
| latency_p50_ms | 1.36 | 232.32 |
| latency_p95_ms | 511.82 | 527.4 |
| estimated_cost | 0.017848 | 0.049942 |
| cache_hit_rate | 0.61 | 0.0 |
| availability | 0.99 | 0.98 |

## 9. Next steps

1. Coordinate circuit-breaker state across instances.
2. Add seeded load tests and confidence intervals.
3. Add response-quality checks and per-user rate limiting.
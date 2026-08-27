# Day 25 Reliability Engineering Report

## 1. Architecture summary

```text
User -> Gateway -> Cache -- hit --> Cached response
                    | miss
                    v
             Primary breaker -> Primary provider
                    | failure/open
                    v
              Backup breaker -> Backup provider -> Static fallback
```

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
| cache enabled | True | Enables provider cost and latency savings. |
| cache backend | memory | Memory by default; Redis supports shared deployments. |
| redis URL | redis://localhost:6379/0 | Local Docker Redis endpoint. |
| primary fail rate | 0.25 | Baseline failure injection. |
| primary latency | 180 ms | Simulated provider latency. |
| primary cost/1K | 0.01 | Simulated provider cost. |
| backup fail rate | 0.05 | Independent fallback failure rate. |
| backup latency | 260 ms | Simulated fallback latency. |
| backup cost/1K | 0.006 | Cheaper fallback cost. |

## 3. SLO summary

| SLI | Target | Actual | Met? |
|---|---:|---:|---|
| Availability | >= 99% | 0.9933 | Yes |
| Latency P95 | < 2500 ms | 462.61 ms | Yes |
| Fallback success rate | >= 95% | 0.9683 | Yes |
| Cache hit rate | >= 10% | 0.64 | Yes |
| Recovery time | < 5000 ms | 2355.3284406661987 ms | Yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 0.9933 |
| error_rate | 0.0067 |
| latency_p50_ms | 1.09 |
| latency_p95_ms | 462.61 |
| latency_p99_ms | 529.06 |
| fallback_success_rate | 0.9683 |
| cache_hit_rate | 0.64 |
| circuit_open_count | 8 |
| recovery_time_ms | 2355.3284406661987 |
| estimated_cost | 0.046316 |
| estimated_cost_saved | 0.192 |

## 5. Chaos scenarios

| Scenario | Expected | Observed | Status |
|---|---|---|---|
| primary_timeout_100 | Primary opens; backup serves traffic. | availability=0.99, fallback=0.9714, cache_hit=0.65, circuit_opens=5 | pass |
| primary_flaky_50 | Primary opens/reopens; backup handles failures. | availability=0.99, fallback=0.9643, cache_hit=0.62, circuit_opens=3 | pass |
| all_healthy | Primary serves traffic with no static fallback. | availability=1.0, fallback=0.0, cache_hit=0.65, circuit_opens=0 | pass |


## 6. Redis shared cache

Redis makes response state visible across gateway instances and applies server-side TTL. The Redis test suite verified shared state, expiry, privacy guardrails, and false-hit rejection: 6 passed.

Shared-state and Redis CLI evidence:

```text
$ docker compose exec -T redis redis-cli KEYS 'rl:evidence:*'
rl:evidence:shared

$ docker compose exec -T redis redis-cli HGETALL rl:evidence:shared
query
shared query
response
shared response

$ pytest -q tests/test_redis_cache.py
......                                                                   [100%]
6 passed

The shared-state test writes through one SharedRedisCache instance and reads
the same response through a second independent instance using the same prefix.
The evidence key was deleted after capture.
```

Redis-backed chaos run: availability=0.9933, P95=333.92 ms, cache_hit_rate=0.7333.

## 7. Failure analysis

The cache reduces repeated-request latency and provider cost. False-hit guardrails reject privacy-sensitive queries and mismatched four-digit years, such as refund policy 2024 vs 2026. Remaining availability misses occur when all providers fail in the same request; production should add a third provider, health-aware routing, and bounded retries with jitter.


## 8. Cache comparison

| Metric | With cache | Without cache |
|---|---:|---:|
| latency_p50_ms | 1.19 | 234.08 |
| latency_p95_ms | 511.87 | 529.01 |
| estimated_cost | 0.017848 | 0.049942 |
| cache_hit_rate | 0.61 | 0.0 |
| availability | 0.99 | 0.98 |

## 9. Next steps

1. Coordinate circuit-breaker state across instances.
2. Add seeded load tests and confidence intervals.
3. Add response-quality checks and per-user rate limiting.
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
| circuit backend | memory | Memory default; Redis enables shared state. |
| circuit state TTL | 300 | Cleans stale shared state. |
| cache TTL | 300 | Limits response staleness. |
| similarity_threshold | 0.92 | Conservative semantic matching. |
| load_test requests | 100 per scenario | Reproducible local chaos run. |
| load_test workers | 8 | Exercises thread-safe concurrent routing. |
| cache enabled | True | Enables provider cost and latency savings. |
| cache backend | memory | Memory by default; Redis supports shared deployments. |
| redis URL | redis://localhost:6379/0 | Local Docker Redis endpoint. |
| primary fail rate | 0.25 | Baseline failure injection. |
| primary latency | 180 ms | Simulated provider latency. |
| primary cost/1K | 0.01 | Simulated provider cost. |
| backup fail rate | 0.05 | Independent fallback failure rate. |
| backup latency | 260 ms | Simulated fallback latency. |
| backup cost/1K | 0.006 | Cheaper fallback cost. |
| cost budget enabled | False | Optional production cost guardrail. |
| cost soft limit | 0.8 | Routes to cheaper provider near budget. |

## 3. SLO summary

| SLI | Target | Actual | Met? |
|---|---:|---:|---|
| Availability | >= 99% | 0.9967 | Yes |
| Latency P95 | < 2500 ms | 490.05 ms | Yes |
| Fallback success rate | >= 95% | 0.9863 | Yes |
| Cache hit rate | >= 10% | 0.5667 | Yes |
| Recovery time | < 5000 ms | 2270.9171772003174 ms | Yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 300 |
| availability | 0.9967 |
| error_rate | 0.0033 |
| latency_p50_ms | 1.47 |
| latency_p95_ms | 490.05 |
| latency_p99_ms | 524.44 |
| fallback_success_rate | 0.9863 |
| cache_hit_rate | 0.5667 |
| circuit_open_count | 2 |
| recovery_time_ms | 2270.9171772003174 |
| estimated_cost | 0.057448 |
| estimated_cost_saved | 0.17 |
| wall_time_ms | 5281.51 |
| throughput_rps | 56.8 |

## 5. Chaos scenarios

| Scenario | Expected | Observed | Status |
|---|---|---|---|
| primary_timeout_100 | Primary opens; backup serves traffic. | availability=1.0, fallback=1.0, cache_hit=0.59, circuit_opens=1 | pass |
| primary_flaky_50 | Primary opens/reopens; backup handles failures. | availability=0.99, fallback=0.9688, cache_hit=0.52, circuit_opens=1 | pass |
| all_healthy | Primary serves traffic with no static fallback. | availability=1.0, fallback=0.0, cache_hit=0.59, circuit_opens=0 | pass |


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

$ docker compose exec -T redis redis-cli HGETALL rl:circuit:evidence-shared
state
open
failure_count
1
success_count
0
opened_at
1787820183.72991

Two RedisCircuitBreaker instances using this key observe the same OPEN state.
The circuit evidence key was deleted after capture.
```

Redis-backed chaos run: availability=0.9933, P95=485.12 ms, cache_hit_rate=0.7133.

## 7. Failure analysis

The cache reduces repeated-request latency and provider cost. False-hit guardrails reject privacy-sensitive queries and mismatched four-digit years, such as refund policy 2024 vs 2026. Remaining availability misses occur when all providers fail in the same request; production should add a third provider, health-aware routing, and bounded retries with jitter.


## 8. Cache comparison

| Metric | With cache | Without cache |
|---|---:|---:|
| latency_p50_ms | 2.59 | 279.47 |
| latency_p95_ms | 506.13 | 505.77 |
| estimated_cost | 0.020462 | 0.045274 |
| cache_hit_rate | 0.53 | 0.0 |
| availability | 0.98 | 0.98 |

## 9. Extra-credit reliability features

| Feature | Evidence |
|---|---|
| Concurrent load | Sequential throughput=14.26 rps; concurrent throughput=77.61 rps. |
| Redis circuit state | Two breaker instances share OPEN state using Redis HINCRBY/EXPIRE. |
| Redis graceful degradation | Redis errors fall back to a privacy-safe in-memory cache. |
| Cost-aware routing | At 80% budget, providers are ordered by cost; at 100%, requests fail closed. |
| Property-based tests | Hypothesis checks breaker thresholds and similarity invariants. |

## 10. Next steps

1. Add confidence intervals around repeated benchmark runs.
2. Add response-quality checks and per-user rate limiting.
3. Move Redis breaker transitions to a Lua script for fully atomic distributed probes.
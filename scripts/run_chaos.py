from __future__ import annotations

import argparse
import random
from pathlib import Path

from reliability_lab.cache import SharedRedisCache
from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.circuit_breaker import RedisCircuitBreaker
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cache-backend", choices=["memory", "redis"])
    parser.add_argument("--circuit-backend", choices=["memory", "redis"])
    args = parser.parse_args()
    random.seed(args.seed)
    config = load_config(args.config)
    if args.cache_backend is not None:
        config.cache.enabled = True
        config.cache.backend = args.cache_backend
    if args.circuit_backend is not None:
        config.circuit_breaker.backend = args.circuit_backend
    if config.cache.enabled and config.cache.backend == "redis":
        redis_cache = SharedRedisCache(
            config.cache.redis_url,
            config.cache.ttl_seconds,
            config.cache.similarity_threshold,
        )
        redis_cache.flush()
        redis_cache.close()
    if config.circuit_breaker.backend == "redis":
        for provider in config.providers:
            breaker = RedisCircuitBreaker(
                provider.name,
                config.circuit_breaker.failure_threshold,
                config.circuit_breaker.reset_timeout_seconds,
                config.circuit_breaker.success_threshold,
                config.circuit_breaker.redis_url,
                config.circuit_breaker.state_ttl_seconds,
            )
            breaker.reset_shared_state()
            breaker.close()
    metrics = run_simulation(config, load_queries())
    metrics.write_json(args.out)
    metrics.write_csv(Path(args.out).with_suffix(".csv"))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

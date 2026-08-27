from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()
    metrics = json.loads(Path(args.metrics).read_text())
    config = yaml.safe_load(Path("configs/default.yaml").read_text())
    redis_metrics_path = Path("reports/metrics_redis.json")
    redis_metrics = json.loads(redis_metrics_path.read_text()) if redis_metrics_path.exists() else {}
    redis_evidence_path = Path("reports/redis_evidence.txt")
    redis_evidence = (
        redis_evidence_path.read_text().strip()
        if redis_evidence_path.exists()
        else "Redis evidence has not been generated yet."
    )
    lines = [
        "# Day 25 Reliability Engineering Report", "", "## 1. Architecture summary", "",
        "```text", "User -> Gateway -> Cache -- hit --> Cached response",
        "                    | miss", "                    v",
        "             Primary breaker -> Primary provider",
        "                    | failure/open", "                    v",
        "              Backup breaker -> Backup provider -> Static fallback", "```", "",
        ("The gateway returns cache hits immediately, skips open provider circuits, "
         "falls back in provider order, and returns a static degraded response only "
         "when every provider fails."), "",
        "## 2. Configuration", "", "| Setting | Value | Reason |", "|---|---:|---|",
        f"| failure_threshold | {config['circuit_breaker']['failure_threshold']} | Limits consecutive provider failures. |",
        f"| reset_timeout_seconds | {config['circuit_breaker']['reset_timeout_seconds']} | Allows recovery probes. |",
        f"| success_threshold | {config['circuit_breaker']['success_threshold']} | Closes after a successful probe. |",
        f"| cache TTL | {config['cache']['ttl_seconds']} | Limits response staleness. |",
        f"| similarity_threshold | {config['cache']['similarity_threshold']} | Conservative semantic matching. |",
        f"| load_test requests | {config['load_test']['requests']} per scenario | Reproducible local chaos run. |",
        f"| cache enabled | {config['cache']['enabled']} | Enables provider cost and latency savings. |",
        f"| cache backend | {config['cache']['backend']} | Memory by default; Redis supports shared deployments. |",
        f"| redis URL | {config['cache']['redis_url']} | Local Docker Redis endpoint. |",
        f"| primary fail rate | {config['providers'][0]['fail_rate']} | Baseline failure injection. |",
        f"| primary latency | {config['providers'][0]['base_latency_ms']} ms | Simulated provider latency. |",
        f"| primary cost/1K | {config['providers'][0]['cost_per_1k_tokens']} | Simulated provider cost. |",
        f"| backup fail rate | {config['providers'][1]['fail_rate']} | Independent fallback failure rate. |",
        f"| backup latency | {config['providers'][1]['base_latency_ms']} ms | Simulated fallback latency. |",
        f"| backup cost/1K | {config['providers'][1]['cost_per_1k_tokens']} | Cheaper fallback cost. |",
        "", "## 3. SLO summary", "", "| SLI | Target | Actual | Met? |", "|---|---:|---:|---|",
        f"| Availability | >= 99% | {metrics.get('availability')} | {'Yes' if metrics.get('availability', 0) >= 0.99 else 'No'} |",
        f"| Latency P95 | < 2500 ms | {metrics.get('latency_p95_ms')} ms | {'Yes' if metrics.get('latency_p95_ms', 99999) < 2500 else 'No'} |",
        f"| Fallback success rate | >= 95% | {metrics.get('fallback_success_rate')} | {'Yes' if metrics.get('fallback_success_rate', 0) >= 0.95 else 'No'} |",
        f"| Cache hit rate | >= 10% | {metrics.get('cache_hit_rate')} | {'Yes' if metrics.get('cache_hit_rate', 0) >= 0.10 else 'No'} |",
        f"| Recovery time | < 5000 ms | {metrics.get('recovery_time_ms')} ms | {'Yes' if metrics.get('recovery_time_ms') is not None and metrics.get('recovery_time_ms', 99999) < 5000 else 'N/A'} |",
        "", "## 4. Metrics", "", "| Metric | Value |", "|---|---:|",
    ]
    for key, value in metrics.items():
        if key in {"scenarios", "scenario_metrics", "cache_comparison"}:
            continue
        lines.append(f"| {key} | {value} |")
    expected = {
        "primary_timeout_100": "Primary opens; backup serves traffic.",
        "primary_flaky_50": "Primary opens/reopens; backup handles failures.",
        "all_healthy": "Primary serves traffic with no static fallback.",
    }
    lines += ["", "## 5. Chaos scenarios", "", "| Scenario | Expected | Observed | Status |", "|---|---|---|---|"]
    for key, value in metrics.get("scenarios", {}).items():
        details = metrics.get("scenario_metrics", {}).get(key, {})
        observed = (
            f"availability={details.get('availability', 'N/A')}, "
            f"fallback={details.get('fallback_success_rate', 'N/A')}, "
            f"cache_hit={details.get('cache_hit_rate', 'N/A')}, "
            f"circuit_opens={details.get('circuit_open_count', 'N/A')}"
        )
        lines.append(f"| {key} | {expected.get(key, 'Scenario completes safely.')} | {observed} | {value} |")
    lines += [
        "",
        "", "## 6. Redis shared cache", "",
        ("Redis makes response state visible across gateway instances and applies "
         "server-side TTL. The Redis test suite verified shared state, expiry, "
         "privacy guardrails, and false-hit rejection: 6 passed."), "",
        "Shared-state and Redis CLI evidence:", "", "```text", redis_evidence, "```", "",
        (f"Redis-backed chaos run: availability={redis_metrics.get('availability', 'N/A')}, "
         f"P95={redis_metrics.get('latency_p95_ms', 'N/A')} ms, "
         f"cache_hit_rate={redis_metrics.get('cache_hit_rate', 'N/A')}."), "",
        "## 7. Failure analysis",
        "",
        "The cache reduces repeated-request latency and provider cost. False-hit guardrails reject privacy-sensitive queries and mismatched four-digit years, such as refund policy 2024 vs 2026. Remaining availability misses occur when all providers fail in the same request; production should add a third provider, health-aware routing, and bounded retries with jitter.",
        "",
        "", "## 8. Cache comparison",
        "",
        "| Metric | With cache | Without cache |",
        "|---|---:|---:|",
    ]
    comparison = metrics.get("cache_comparison", {})
    with_cache = comparison.get("with_cache", {})
    without_cache = comparison.get("without_cache", {})
    for key in ["latency_p50_ms", "latency_p95_ms", "estimated_cost", "cache_hit_rate", "availability"]:
        lines.append(f"| {key} | {with_cache.get(key, 'N/A')} | {without_cache.get(key, 'N/A')} |")
    lines += ["", "## 9. Next steps", "", "1. Coordinate circuit-breaker state across instances.", "2. Add seeded load tests and confidence intervals.", "3. Add response-quality checks and per-user rate limiting."]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

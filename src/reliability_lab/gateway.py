from __future__ import annotations

import threading
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
        cost_budget: float | None = None,
        budget_soft_limit_ratio: float = 0.8,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache
        self.cost_budget = cost_budget
        self.budget_soft_limit_ratio = budget_soft_limit_ratio
        self.cumulative_cost = 0.0
        self._budget_lock = threading.Lock()

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        Request routing pipeline:

        1. CACHE CHECK — if self.cache is not None:
           - Call self.cache.get(prompt) → (cached_text, score)
           - If cached_text is not None, return GatewayResponse with:
             route=f"cache_hit:{score:.2f}", cache_hit=True, latency=0, cost=0

        2. PROVIDER FALLBACK CHAIN — iterate self.providers in order:
           - Get the circuit breaker: self.breakers[provider.name]
           - Try breaker.call(provider.complete, prompt)
           - On success:
             a. Store in cache: self.cache.set(prompt, response.text, {"provider": provider.name})
             b. Determine route: "primary" if first provider, else "fallback"
             c. Return GatewayResponse with provider info, latency, cost
           - On ProviderError or CircuitOpenError: save error, continue to next provider

        3. STATIC FALLBACK — if all providers fail:
           - Return GatewayResponse with:
             text="The service is temporarily degraded. Please try again soon."
             route="static_fallback", error=last_error

        Cost-budget routing is documented as a future production improvement.
        """
        if self.cache is not None:
            cached_text, score = self.cache.get(prompt)
            if cached_text is not None:
                return GatewayResponse(cached_text, f"cache_hit:{score:.2f}", None, True, 0.0, 0.0)

        last_error: str | None = None
        with self._budget_lock:
            spent = self.cumulative_cost
        if self.cost_budget is not None and spent >= self.cost_budget:
            return GatewayResponse(
                "The request budget is exhausted. Please try again later.",
                "static_fallback",
                None,
                False,
                0.0,
                0.0,
                "cost_budget_exhausted",
            )

        provider_chain = list(enumerate(self.providers))
        if (
            self.cost_budget is not None
            and spent >= self.cost_budget * self.budget_soft_limit_ratio
        ):
            provider_chain.sort(key=lambda item: item[1].cost_per_1k_tokens)

        for index, provider in provider_chain:
            breaker = self.breakers[provider.name]
            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
            except (ProviderError, CircuitOpenError) as exc:
                last_error = f"{provider.name}: {exc}"
                continue
            if self.cache is not None:
                self.cache.set(prompt, response.text, {"provider": provider.name})
            with self._budget_lock:
                self.cumulative_cost += response.estimated_cost
            route = "primary" if index == 0 else "fallback"
            return GatewayResponse(
                response.text,
                route,
                response.provider,
                False,
                response.latency_ms,
                response.estimated_cost,
            )

        return GatewayResponse(
            "The service is temporarily degraded. Please try again soon.",
            "static_fallback",
            None,
            False,
            0.0,
            0.0,
            last_error,
        )

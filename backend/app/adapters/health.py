"""Single-flight, thirty-second caching for live health probes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic

from app.adapters.factory import AdapterBundle
from app.adapters.interfaces import ServiceAdapter
from app.contract_models import ServiceHealth, ServicesHealth


class HealthChecker:
    def __init__(
        self,
        adapters: AdapterBundle,
        *,
        cache_ttl: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._services = adapters.health_services
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._cache: dict[str, tuple[float, ServiceHealth]] = {}
        self._locks = {name: asyncio.Lock() for name in self._services}

    async def _check(self, name: str, adapter: ServiceAdapter) -> ServiceHealth:
        async with self._locks[name]:
            override = getattr(adapter, "runtime_health", lambda: None)()
            if override is not None:
                return ServiceHealth.model_validate(override).model_copy(deep=True)
            cached = self._cache.get(name)
            if adapter.mode == "live" and cached and self._clock() < cached[0]:
                return cached[1].model_copy(deep=True)
            try:
                result = ServiceHealth.model_validate(await adapter.health_check())
                if result.mode != adapter.mode:
                    raise ValueError("An adapter health result cannot change service mode.")
            except Exception:
                # Never expose vendor exception messages or credential-bearing URLs.
                result = ServiceHealth(
                    status="error", mode=adapter.mode, detail="Service health check failed.",
                )
            if adapter.mode == "live":
                self._cache[name] = (self._clock() + self._cache_ttl, result)
            return result.model_copy(deep=True)

    async def check(self) -> ServicesHealth:
        names = list(self._services)
        results = await asyncio.gather(*(
            self._check(name, self._services[name]) for name in names
        ))
        return ServicesHealth.model_validate(dict(zip(names, results)))

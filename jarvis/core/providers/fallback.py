"""FallbackProvider — chain нескольких LLM-провайдеров с автопереключением.

Если primary падает (network, rate limit, 5xx, exhausted retries) —
автоматически уходим на fallback. Сохраняет интерфейс LLMProvider,
поэтому Router его не отличает от ClaudeProvider.
"""

from __future__ import annotations

from typing import Final

from core.logging import get_logger
from core.metrics import metrics
from core.providers.base import LLMProvider, Message

logger = get_logger(__name__)


class FallbackProvider:
    """Цепочка провайдеров: primary → secondary → ..."""

    name: Final = "fallback-chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = providers

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        last_exc: Exception | None = None
        for idx, prov in enumerate(self._providers):
            try:
                with metrics.timed("llm", provider=prov.name):
                    reply = await prov.chat(
                        messages=messages,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                if idx > 0:
                    logger.info(
                        "fallback_succeeded",
                        provider=prov.name,
                        position=idx,
                    )
                    metrics.record(
                        "fallback",
                        provider=prov.name,
                        meta={"position": idx, "from": self._providers[0].name},
                    )
                return reply
            except Exception as e:
                last_exc = e
                logger.warning(
                    "fallback_provider_failed",
                    provider=prov.name,
                    position=idx,
                    error=str(e)[:200],
                )
        # Все провайдеры упали
        assert last_exc is not None
        logger.error("fallback_all_providers_failed", count=len(self._providers))
        raise last_exc

    async def healthcheck(self) -> bool:
        """OK если хотя бы один провайдер живой."""
        for prov in self._providers:
            try:
                if await prov.healthcheck():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def close(self) -> None:
        for prov in self._providers:
            close = getattr(prov, "close", None)
            if close:
                try:
                    await close()
                except Exception as e:  # noqa: BLE001
                    logger.warning("provider_close_failed", provider=prov.name, error=str(e))

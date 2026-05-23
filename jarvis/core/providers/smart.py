"""SmartProvider — основной LLM-фасад Джарвиса.

Внутри:
  * primary  — ClaudeProvider (поддерживает tools + vision)
  * fallback — YandexGPTProvider (только базовый chat, на случай падения Claude)

Делегирует:
  * chat()             — primary с автопереключением на fallback
  * chat_with_tools()  — только primary (YandexGPT в нашей реализации не умеет tools)
  * chat_with_image()  — только primary (vision у YandexGPT отдельная модель)
  * healthcheck()      — OK если хоть один живой
  * close()            — оба
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final

from pathlib import Path

from core.logging import get_logger
from core.metrics import metrics
from core.providers.base import Message
from core.providers.claude import ClaudeProvider, ToolRunner
from core.providers.deepseek import DeepseekProvider
from core.providers.ollama import OllamaProvider
from core.providers.yandex_gpt import YandexGPTProvider

logger = get_logger(__name__)

# Choice persistence — Босс может голосом переключать primary LLM, выбор
# сохраняется в workspace/llm_choice.txt и читается каждый раз перед chat().
# Значения: 'claude' / 'deepseek' / 'yandex' / 'ollama' / 'auto' (default).
VALID_CHOICES = {"auto", "claude", "deepseek", "yandex", "ollama"}


class SmartProvider:
    """Composite: Claude + Deepseek + YandexGPT + Ollama. Runtime-switchable."""

    name: Final = "smart"

    def __init__(
        self,
        primary: ClaudeProvider,
        fallback: YandexGPTProvider | None,
        offline: OllamaProvider | None = None,
        deepseek: DeepseekProvider | None = None,
        choice_path: Path | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._offline = offline
        self._deepseek = deepseek
        self._choice_path = choice_path
        # Кэш текущего выбора — обновляется при изменении файла (через mtime check).
        self._cached_choice: str = "auto"
        self._cached_mtime: float = 0.0

    @property
    def primary(self) -> ClaudeProvider:
        return self._primary

    # ─── Runtime LLM selection ──────────────────────────────────────
    def get_choice(self) -> str:
        """Прочитать выбранный LLM (с файлом-инвалидацией кэша)."""
        if self._choice_path is None or not self._choice_path.exists():
            return "auto"
        try:
            mtime = self._choice_path.stat().st_mtime
            if mtime != self._cached_mtime:
                value = self._choice_path.read_text(encoding="utf-8").strip().lower()
                if value in VALID_CHOICES:
                    self._cached_choice = value
                    self._cached_mtime = mtime
                else:
                    self._cached_choice = "auto"
            return self._cached_choice
        except Exception:
            return "auto"

    def set_choice(self, choice: str) -> bool:
        """Зафиксировать выбор Босса. True если value валидный."""
        choice = (choice or "").lower().strip()
        if choice not in VALID_CHOICES or self._choice_path is None:
            return False
        try:
            self._choice_path.parent.mkdir(parents=True, exist_ok=True)
            self._choice_path.write_text(choice, encoding="utf-8")
            self._cached_choice = choice
            self._cached_mtime = self._choice_path.stat().st_mtime
            logger.info("smart_llm_choice_set", choice=choice)
            return True
        except Exception as e:
            logger.error("smart_choice_save_failed", error=str(e))
            return False

    def _build_chain(self) -> list:
        """Собрать ordered chain провайдеров с учётом выбора Босса.

        Если choice == 'auto' — Claude → Deepseek → Yandex → Ollama.
        Иначе — выбранный provider первым, остальные fallback в default order.
        """
        choice = self.get_choice()
        providers_by_name: dict[str, object] = {}
        if self._primary is not None:
            providers_by_name["claude"] = self._primary
        if self._deepseek is not None:
            providers_by_name["deepseek"] = self._deepseek
        if self._fallback is not None:
            providers_by_name["yandex"] = self._fallback
        if self._offline is not None:
            providers_by_name["ollama"] = self._offline

        default_order = ["claude", "deepseek", "yandex", "ollama"]
        if choice != "auto" and choice in providers_by_name:
            ordered = [choice] + [k for k in default_order if k != choice]
        else:
            ordered = default_order
        return [providers_by_name[k] for k in ordered if k in providers_by_name]

    async def _try_provider(
        self,
        provider,  # noqa: ANN001
        messages: list[Message],
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Helper — обёртка с metrics. Не ловит исключения."""
        with metrics.timed("llm", provider=provider.name):
            return await provider.chat(
                messages=messages,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        """Динамическая цепочка по выбору Босса (или default Claude → Deepseek
        → Yandex → Ollama). Каждый уровень авто-skip'ается при падении."""
        chain = self._build_chain()
        if not chain:
            raise RuntimeError("smart: no providers available")
        last_error: Exception | None = None

        for idx, provider in enumerate(chain):
            try:
                reply = await self._try_provider(
                    provider, messages, system, max_tokens, temperature
                )
                if idx > 0:
                    logger.info(
                        "smart_fallback_succeeded",
                        provider=provider.name,
                        position=idx,
                    )
                    metrics.record(
                        "fallback",
                        provider=provider.name,
                        meta={
                            "from": chain[0].name,
                            "position": idx,
                            "stage": "chat",
                        },
                    )
                return reply
            except Exception as e:
                last_error = e
                logger.warning(
                    "smart_provider_failed",
                    provider=provider.name,
                    position=idx,
                    error=str(e)[:200],
                )
                continue

        assert last_error is not None
        logger.error("smart_all_providers_failed", count=len(chain))
        raise last_error

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        tool_runner: ToolRunner,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 1.0,
        max_iterations: int = 4,
    ) -> tuple[str, list[dict[str, Any]]]:
        # Tools работают только на Claude (Yandex bare-chat без tool-use)
        with metrics.timed("llm", provider=self._primary.name, meta={"mode": "tools"}):
            return await self._primary.chat_with_tools(
                messages=messages,
                tools=tools,
                tool_runner=tool_runner,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                max_iterations=max_iterations,
            )

    async def chat_with_image(
        self,
        image_bytes: bytes,
        prompt: str,
        media_type: str = "image/jpeg",
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        # Vision только Claude
        with metrics.timed("llm", provider=self._primary.name, meta={"mode": "vision"}):
            return await self._primary.chat_with_image(
                image_bytes=image_bytes,
                prompt=prompt,
                media_type=media_type,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )

    async def healthcheck(self) -> bool:
        if await self._primary.healthcheck():
            return True
        if self._deepseek is not None and await self._deepseek.healthcheck():
            return True
        if self._fallback is not None and await self._fallback.healthcheck():
            return True
        if self._offline is not None and await self._offline.healthcheck():
            return True
        return False

    async def provider_status(self) -> dict[str, bool]:
        """Расширенный healthcheck — статус каждого провайдера отдельно.
        Используется HUD для индикатора 'какие LLM доступны'."""
        status: dict[str, bool] = {}
        try:
            status[self._primary.name] = await self._primary.healthcheck()
        except Exception:  # noqa: BLE001
            status[self._primary.name] = False
        if self._deepseek is not None:
            try:
                status[self._deepseek.name] = await self._deepseek.healthcheck()
            except Exception:  # noqa: BLE001
                status[self._deepseek.name] = False
        if self._fallback is not None:
            try:
                status[self._fallback.name] = await self._fallback.healthcheck()
            except Exception:  # noqa: BLE001
                status[self._fallback.name] = False
        if self._offline is not None:
            try:
                status[self._offline.name] = await self._offline.healthcheck()
            except Exception:  # noqa: BLE001
                status[self._offline.name] = False
        return status

    async def close(self) -> None:
        await self._primary.close()
        if self._deepseek is not None:
            await self._deepseek.close()
        if self._fallback is not None:
            await self._fallback.close()
        if self._offline is not None:
            await self._offline.close()

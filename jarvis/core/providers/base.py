"""Базовый Protocol для LLM-провайдеров.

Любой провайдер (Claude, Ollama, Groq, GigaChat) реализует один интерфейс,
чтобы Provider Registry мог их свободно swap'ать с failover.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    """Одно сообщение в диалоге (user или assistant)."""

    role: Role
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """Контракт LLM-провайдера. Все провайдеры async."""

    name: str  # "claude-sonnet-4.6", "qwen3-8b", "groq-llama-3.3"

    async def chat(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> str:
        """Получить ответ от модели на сообщения диалога. Возвращает текст."""
        ...

    async def healthcheck(self) -> bool:
        """Проверить доступность провайдера. Не делает реального запроса."""
        ...

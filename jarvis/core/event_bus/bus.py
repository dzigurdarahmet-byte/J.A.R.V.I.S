"""In-process asyncio Event Bus.

Простой pub/sub без Redis. Каждое опубликованное событие фан-аут'ится
всем подписанным coroutine'ам. Подписки через декоратор @bus.on(...).

Особенности:
- ВСЁ in-process. Если нужен multi-process (несколько Python-процессов
  обмениваются событиями) — заверни этот же EventBus через Redis Streams,
  это перебивается в будущем без ломания вызовов.
- Подписчики выполняются как asyncio.Task'и, ошибки в одном не валят bus.
- Можно подписаться на "*" (любое событие) — полезно для audit/metrics.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[["JarvisEvent"], Awaitable[None]]


@dataclass(slots=True)
class JarvisEvent:
    """Единый формат события."""

    type: str
    source: str                          # "channel:telegram", "router", "skill:weather"
    data: dict[str, Any] = field(default_factory=dict)
    channel: str = ""                    # из какого канала пришёл (telegram, voice, web_hud)
    request_id: str = ""
    priority: str = "normal"             # emergency, high, normal, low
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    """Async in-process pub/sub."""

    WILDCARD = "*"

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        """Декоратор подписки: @bus.on('user_input') / @bus.on('*')."""

        def deco(fn: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(fn)
            logger.info(
                "bus_handler_registered",
                subscribe_to=event_type,
                handler=fn.__qualname__,
            )
            return fn

        return deco

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Программная подписка (без декоратора)."""
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: JarvisEvent) -> None:
        """Опубликовать событие. Все подписчики получат его параллельно
        как fire-and-forget. Ошибка одного — не валит остальных."""
        logger.info(
            "bus_event",
            kind=event.type,
            source=event.source,
            channel=event.channel,
            request_id=event.request_id,
        )
        # точные подписки + wildcard
        handlers: list[Handler] = []
        handlers.extend(self._handlers.get(event.type, []))
        handlers.extend(self._handlers.get(self.WILDCARD, []))
        if not handlers:
            return

        # каждый handler запускается отдельной таской, чтобы один тормозящий
        # не блокировал остальных
        for fn in handlers:
            asyncio.create_task(self._safe_call(fn, event))

    @staticmethod
    async def _safe_call(fn: Handler, event: JarvisEvent) -> None:
        try:
            await fn(event)
        except Exception as e:
            logger.error(
                "bus_handler_error",
                handler=fn.__qualname__,
                kind=event.type,
                error=str(e),
            )


# ── Глобальный экземпляр ────────────────────────────────────────────
bus = EventBus()

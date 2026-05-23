"""Unified Event Bus по архитектуре v5.3.

Главный нерв Джарвиса: каналы публикуют события, подписчики реагируют.
В MVP реализован как in-process asyncio pub/sub. Redis Streams подключается
опционально через флаг RedisPersister — для multi-process сценариев.
"""

from .bus import EventBus, JarvisEvent, bus
from .events import EventType

__all__ = ["EventBus", "JarvisEvent", "EventType", "bus"]

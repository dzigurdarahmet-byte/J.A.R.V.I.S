"""ProactiveWatcher — фоновый цикл, который дёргает все триггеры каждые
WATCHER_TICK_SEC секунд. Сработавшие nudge'ы публикует в bus как
ASSISTANT_REPLY с source='proactive', все каналы (Telegram, HUD, voice,
avatar) подхватывают.

Quiet hours (по умолчанию 23-08) — нудж всё равно публикуется, но с
флагом `suppress_voice=True` (голосовой канал не озвучивает; Telegram/HUD
по-прежнему пушат текст).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger
from core.proactive.dedup import FiredStore
from core.proactive.triggers import ALL_TRIGGERS, TriggerContext, Notification

logger = get_logger(__name__)

WATCHER_TICK_SEC = 60.0  # как часто проверяем триггеры
QUIET_HOURS_START = 23   # с 23:00
QUIET_HOURS_END = 8      # до 08:00


def _is_quiet_hours(now: datetime) -> bool:
    h = now.hour
    if QUIET_HOURS_START >= QUIET_HOURS_END:
        # окно через полночь: 23..23, 0..7
        return h >= QUIET_HOURS_START or h < QUIET_HOURS_END
    return QUIET_HOURS_START <= h < QUIET_HOURS_END


class ProactiveWatcher:
    def __init__(
        self,
        *,
        claude,
        memory,
        workspace_dir: Path,
        todo_store=None,
        reminders_store=None,
    ) -> None:
        self._claude = claude
        self._memory = memory
        self._workspace = workspace_dir
        self._todo_store = todo_store
        self._reminders_store = reminders_store
        self._dedup = FiredStore(workspace_dir / "proactive_fired.jsonl")
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._last_user_input_at: datetime | None = None

        # Подписываемся на bus USER_INPUT events для long-focus tracker
        @bus.on(EventType.USER_INPUT)
        async def _track_input(event: JarvisEvent) -> None:
            self._last_user_input_at = datetime.now()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="proactive-watcher")
        logger.info("proactive_watcher_started", tick_sec=WATCHER_TICK_SEC)

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    await self._tick()
                except Exception as e:
                    logger.error("proactive_tick_failed", error=str(e))
                await asyncio.sleep(WATCHER_TICK_SEC)
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        now = datetime.now()
        ctx = TriggerContext(
            now=now,
            claude=self._claude,
            memory=self._memory,
            dedup=self._dedup,
            todo_store=self._todo_store,
            reminders_store=self._reminders_store,
            last_user_input_at=self._last_user_input_at,
        )
        for trigger in ALL_TRIGGERS:
            try:
                notif: Notification | None = await trigger(ctx)
            except Exception as e:
                logger.warning("proactive_trigger_failed",
                               trigger=trigger.__name__, error=str(e))
                continue
            if notif is None:
                continue
            await self._fire(notif, now=now)

    async def _fire(self, notif: Notification, *, now: datetime) -> None:
        quiet = _is_quiet_hours(now)
        suppress_voice = notif.suppress_voice or quiet
        await bus.publish(JarvisEvent(
            type=EventType.ASSISTANT_REPLY,
            source="proactive",
            channel="proactive",   # отдельный channel чтобы tone-polish не дёргался
            request_id=f"proactive-{notif.trigger_id}-{notif.dedup_key}",
            data={
                "text": notif.text,
                "speakable": not suppress_voice,
                "importance": notif.importance,
                "trigger_id": notif.trigger_id,
                "kind": "proactive",
            },
        ))
        logger.info("proactive_fired",
                    trigger=notif.trigger_id,
                    importance=notif.importance,
                    chars=len(notif.text),
                    quiet=quiet)

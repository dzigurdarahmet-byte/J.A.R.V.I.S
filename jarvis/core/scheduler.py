"""Простой asyncio-scheduler: задачи по времени дня (HH:MM).

Без croniter и прочих внешних зависимостей. Достаточно для:
- 08:00 каждый день
- 22:00 каждый день
- каждые N минут

Проверка раз в минуту. Защита от двойного запуска в одну минуту.

Catch-up: если job создан с catch_up=True, при старте Scheduler
проверяет — есть ли запись «последний запуск сегодня». Если нет
И время уже прошло (now >= scheduled time) — запускает немедленно.
Этим решается проблема пропущенных брифингов при позднем старте.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from core.logging import get_logger

logger = get_logger(__name__)

CHECK_INTERVAL_SEC: Final = 30.0


@dataclass(slots=True)
class DailyJob:
    """Задача, запускающаяся в HH:MM каждый день."""

    name: str
    hour: int
    minute: int
    fn: Callable[[], Awaitable[None]]
    catch_up: bool = False  # отправить пропущенное при старте если сегодня не было
    _last_run_minute: str = field(default="", init=False)  # YYYY-MM-DD HH:MM как маркер


class Scheduler:
    """In-process daily scheduler."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._jobs: list[DailyJob] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Опциональный persistent state — для catch-up через restart.
        # Формат: {job_name: "YYYY-MM-DD"} — дата последнего успешного run.
        self._persist = persist_path
        self._last_runs: dict[str, str] = {}
        if persist_path and persist_path.exists():
            try:
                self._last_runs = json.loads(persist_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("scheduler_state_load_failed", error=str(e))

    def _save_state(self) -> None:
        if self._persist is None:
            return
        try:
            self._persist.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._last_runs, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._persist)
        except Exception as e:
            logger.warning("scheduler_state_save_failed", error=str(e))

    def add_daily(
        self,
        name: str,
        hour: int,
        minute: int,
        fn: Callable[[], Awaitable[None]],
        *,
        catch_up: bool = False,
    ) -> None:
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"bad time: {hour}:{minute}")
        self._jobs.append(DailyJob(
            name=name, hour=hour, minute=minute, fn=fn, catch_up=catch_up,
        ))
        logger.info(
            "scheduler_job_added",
            name=name,
            time=f"{hour:02d}:{minute:02d}",
            catch_up=catch_up,
        )

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_with_catchup(), name="scheduler")
        logger.info("scheduler_started", jobs=len(self._jobs))

    async def _run_with_catchup(self) -> None:
        """Сначала ищем пропущенные jobs (catch_up=True, время прошло сегодня,
        ещё не было today), запускаем — потом обычный loop."""
        try:
            await self._catch_up_missed()
        except Exception as e:
            logger.error("scheduler_catchup_failed", error=str(e))
        await self._loop()

    async def _catch_up_missed(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        scheduled_now = now.replace(second=0, microsecond=0)
        for job in self._jobs:
            if not job.catch_up:
                continue
            job_time_today = scheduled_now.replace(hour=job.hour, minute=job.minute)
            if now < job_time_today:
                # Время ещё не наступило сегодня — обычный поток разберётся
                continue
            if self._last_runs.get(job.name) == today:
                # Уже было сегодня — пропускаем
                continue
            logger.info(
                "scheduler_catchup_firing",
                job=job.name,
                scheduled=f"{job.hour:02d}:{job.minute:02d}",
                now=now.strftime("%H:%M"),
            )
            try:
                await job.fn()
                self._last_runs[job.name] = today
                job._last_run_minute = now.strftime("%Y-%m-%d %H:%M")
                self._save_state()
            except Exception as e:
                logger.error("scheduler_catchup_error", job=job.name, error=str(e))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        logger.info("scheduler_stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CHECK_INTERVAL_SEC)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        now = datetime.now()
        marker = now.strftime("%Y-%m-%d %H:%M")
        today = now.strftime("%Y-%m-%d")
        for job in self._jobs:
            if job.hour == now.hour and job.minute == now.minute:
                if job._last_run_minute == marker:
                    continue
                job._last_run_minute = marker
                logger.info("scheduler_firing", job=job.name)
                try:
                    await job.fn()
                    self._last_runs[job.name] = today
                    self._save_state()
                except Exception as e:
                    logger.error("scheduler_job_error", job=job.name, error=str(e))

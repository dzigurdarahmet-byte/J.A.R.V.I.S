"""Skill: напоминания с фоновым планировщиком.

Storage: workspace/reminders.json.
Scheduler: фоновый asyncio.Task, проверяет каждые 10 сек что пора сработать.
При срабатывании публикует ASSISTANT_REPLY в bus → каналы озвучат/покажут.

Поддерживаемые фразы:
  add:
    "напомни через 30 мин X"
    "напомни через час позвонить"
    "напомни через 2 часа Y"
    "напомни в 15:30 X"
    "напомни сегодня в 18:00 X"
    "напомни завтра в 9 утра X"
  list:
    "какие напоминания", "покажи напоминания", "что напомнить"
  cancel:
    "отмени напоминание #3", "удали напоминание 2"

NB: scheduler стартует из канала (run_web_hud.py), не из skill — skill
сам по себе stateless вокруг storage. Если HUD не запущен — reminders
лежат в файле, при следующем старте scheduler их подхватит и:
  - если время уже прошло (<10 мин назад) → отрабатывает сразу с пометкой "пропущено пока был офлайн"
  - если давно прошло (>10 мин) → не fire, помечается как expired
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from core.event_bus import EventType, JarvisEvent, bus
from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

_LOCK = Lock()
SCHEDULER_TICK_SEC = 10.0
EXPIRED_GRACE_SEC = 600  # если пропущено >10 мин — считается expired, не fire


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ─── Парсинг времени из текста ──────────────────────────────────────
_REL_DURATION = re.compile(
    r"\bчерез\s+(?:(\d+)\s*)?"
    r"(минут\w*|мин|час\w*|секунд\w*|сек)\b",
    re.IGNORECASE,
)
_HALF_HOUR = re.compile(r"\bчерез\s+пол\s*-?\s*часа\b", re.IGNORECASE)
_AT_TIME = re.compile(
    r"\bв\s+(\d{1,2})(?:[:.]\s*(\d{2}))?\s*(утра|дня|вечера|ночи)?\b",
    re.IGNORECASE,
)
_TOMORROW = re.compile(r"\bзавтра\b", re.IGNORECASE)
_TODAY = re.compile(r"\bсегодня\b", re.IGNORECASE)


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Извлечь datetime из текста типа 'через 30 мин', 'завтра в 10 утра'."""
    now = now or _now()

    # "через пол часа"
    if _HALF_HOUR.search(text):
        return now + timedelta(minutes=30)

    # "через N мин/часов/секунд"
    m = _REL_DURATION.search(text)
    if m:
        qty_str, unit = m.group(1), m.group(2).lower()
        qty = int(qty_str) if qty_str else 1
        if unit.startswith(("мин",)):
            return now + timedelta(minutes=qty)
        if unit.startswith(("час",)):
            return now + timedelta(hours=qty)
        if unit.startswith(("сек",)):
            return now + timedelta(seconds=max(qty, 5))  # <5 сек — бессмысленно

    # "в HH:MM" (опционально с утра/вечера + завтра)
    m = _AT_TIME.search(text)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        period = (m.group(3) or "").lower()
        if period == "вечера" and hh < 12:
            hh += 12
        elif period == "ночи" and hh < 6:
            pass  # 1 ночи = 01:00
        elif period == "утра" and hh == 12:
            hh = 0
        elif period == "дня" and hh < 12:
            hh += 12
        if not (0 <= hh <= 23) or not (0 <= mm <= 59):
            return None
        is_tomorrow = bool(_TOMORROW.search(text))
        is_today_explicit = bool(_TODAY.search(text))
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if is_tomorrow:
            base += timedelta(days=1)
        elif not is_today_explicit and base <= now:
            # время уже прошло сегодня — переносим на завтра
            base += timedelta(days=1)
        return base

    return None


# ─── Storage ────────────────────────────────────────────────────────
class RemindersStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"next_id": 1, "items": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("reminders_load_failed", error=str(e))
            return {"next_id": 1, "items": []}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, text: str, at: datetime, channel_hint: str = "") -> dict[str, Any]:
        with _LOCK:
            data = self.load()
            item = {
                "id": data["next_id"],
                "text": text,
                "at_iso": _iso(at),
                "channel_hint": channel_hint,
                "fired": False,
                "fired_at": None,
                "created_at": _iso(_now()),
            }
            data["next_id"] += 1
            data["items"].append(item)
            self.save(data)
            return item

    def list_active(self) -> list[dict[str, Any]]:
        return [i for i in self.load().get("items", []) if not i.get("fired")]

    def mark_fired(self, item_id: int) -> dict[str, Any] | None:
        with _LOCK:
            data = self.load()
            for it in data["items"]:
                if it["id"] == item_id and not it["fired"]:
                    it["fired"] = True
                    it["fired_at"] = _iso(_now())
                    self.save(data)
                    return it
            return None

    def cancel(self, item_id: int) -> dict[str, Any] | None:
        with _LOCK:
            data = self.load()
            kept, removed = [], None
            for it in data["items"]:
                if it["id"] == item_id and not it["fired"] and removed is None:
                    removed = it
                else:
                    kept.append(it)
            if removed is not None:
                data["items"] = kept
                self.save(data)
            return removed


# ─── Background scheduler ───────────────────────────────────────────
class ReminderScheduler:
    """Каждые SCHEDULER_TICK_SEC проверяет активные reminders и fire'ит."""

    def __init__(self, store: RemindersStore) -> None:
        self._store = store
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="reminder-scheduler")
        logger.info("reminder_scheduler_started", tick_sec=SCHEDULER_TICK_SEC)

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
                    logger.error("reminder_tick_failed", error=str(e))
                await asyncio.sleep(SCHEDULER_TICK_SEC)
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        now = _now()
        for item in self._store.list_active():
            try:
                at = datetime.fromisoformat(item["at_iso"])
            except Exception:
                continue
            if at > now:
                continue
            age = (now - at).total_seconds()
            if age > EXPIRED_GRACE_SEC:
                # expired — помечаем fired без публикации, чтобы не спамить
                self._store.mark_fired(item["id"])
                logger.info("reminder_expired", id=item["id"], age_sec=int(age))
                continue
            # FIRE
            self._store.mark_fired(item["id"])
            text = f"Босс, напоминание: {item['text']}"
            await bus.publish(JarvisEvent(
                type=EventType.ASSISTANT_REPLY,
                source="reminder-scheduler",
                channel=item.get("channel_hint") or "web_hud",
                request_id=f"reminder-{item['id']}",
                data={"text": text, "speakable": True, "kind": "reminder"},
            ))
            logger.info("reminder_fired", id=item["id"], text=item["text"][:50])


# ─── Skill ──────────────────────────────────────────────────────────
_ADD_PATTERN = re.compile(r"\bнапомни(?:\s+мне)?\b\s+(.+)$", re.IGNORECASE | re.DOTALL)
_LIST_PATTERNS = [
    re.compile(r"\b(?:какие|покажи)\s+(?:мои\s+)?напоминан", re.IGNORECASE),
    re.compile(r"\bчто\s+(?:мне\s+)?напомнить\b", re.IGNORECASE),
    re.compile(r"\bсписок\s+напоминан", re.IGNORECASE),
]
_CANCEL_PATTERN = re.compile(
    r"\b(?:отмени|удали|забудь)\s+напоминан\w*\s*#?\s*(\d+)\b",
    re.IGNORECASE,
)


def _format_when(at: datetime, now: datetime | None = None) -> str:
    now = now or _now()
    delta = (at - now).total_seconds()
    if delta < 60:
        return f"через {int(delta)} сек"
    if delta < 3600:
        mins = int(delta // 60)
        return f"через {mins} мин"
    if delta < 86400 and at.date() == now.date():
        return f"сегодня в {at.strftime('%H:%M')}"
    if at.date() == (now.date() + timedelta(days=1)):
        return f"завтра в {at.strftime('%H:%M')}"
    return at.strftime("%d.%m в %H:%M")


class RemindersSkill(KeywordSkill):
    name = "reminders"
    keywords = [
        r"\bнапомни(?:\s+мне)?\b\s+",
        r"\b(?:какие|покажи)\s+(?:мои\s+)?напоминан",
        r"\bчто\s+(?:мне\s+)?напомнить\b",
        r"\bсписок\s+напоминан",
        r"\b(?:отмени|удали|забудь)\s+напоминан",
    ]

    def __init__(self, store: RemindersStore) -> None:
        super().__init__()
        self._store = store

    async def run(self, text: str, request_id: str) -> SkillResult:
        # CANCEL — самое специфичное, проверяем первым
        m = _CANCEL_PATTERN.search(text)
        if m:
            idn = int(m.group(1))
            removed = self._store.cancel(idn)
            if removed is None:
                return SkillResult(text=f"Босс, активного напоминания #{idn} не нашёл.", speakable=True)
            return SkillResult(text=f"Отменил #{idn}: {removed['text']}.", speakable=True)

        # LIST
        for pat in _LIST_PATTERNS:
            if pat.search(text):
                items = self._store.list_active()
                if not items:
                    return SkillResult(text="Напоминаний нет, Босс.", speakable=True)
                lines = []
                for it in items:
                    try:
                        at = datetime.fromisoformat(it["at_iso"])
                        when = _format_when(at)
                    except Exception:
                        when = "?"
                    lines.append(f"{it['id']}. {when} — {it['text']}")
                return SkillResult(text="\n".join(lines), speakable=True)

        # ADD
        m = _ADD_PATTERN.search(text)
        if not m:
            return SkillResult(
                text="Босс, не понял. Скажи 'напомни через 30 минут позвонить врачу' или 'напомни в 15:30 X'.",
                speakable=True,
            )
        rest = m.group(1).strip().rstrip(".!?")
        when = parse_when(rest)
        if when is None:
            return SkillResult(
                text="Босс, не понял когда напомнить. Скажи 'через N минут', 'в HH:MM' или 'завтра в HH'.",
                speakable=True,
            )
        # Текст напоминания: убираем фрагмент времени из rest
        body = _REL_DURATION.sub("", rest)
        body = _HALF_HOUR.sub("", body)
        body = _AT_TIME.sub("", body)
        body = _TOMORROW.sub("", body)
        body = _TODAY.sub("", body)
        # типичные стоп-слова в начале — "что", "о том"
        body = re.sub(r"^\s*(?:что|о\s+том|про)\s+", "", body, flags=re.IGNORECASE)
        body = body.strip().rstrip(".,;:")
        if not body:
            return SkillResult(text="Босс, что именно напомнить?", speakable=True)

        item = self._store.add(body, when, channel_hint="web_hud")
        return SkillResult(
            text=f"Напомню {_format_when(when)} — {body}. (#{item['id']})",
            speakable=True,
        )

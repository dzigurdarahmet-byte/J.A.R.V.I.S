"""Триггеры проактивных сообщений.

Каждый триггер — async-функция `check(ctx) -> Notification | None`.
ctx = TriggerContext (dataclass с deps: claude, memory, dedup store,
todo/reminders stores, текущее время now()).

Notification — dataclass с {text, importance, channel_hint}.
Возвращается None если ничего не триггерится.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class TriggerContext:
    now: datetime
    claude: Any
    memory: Any
    dedup: Any                 # FiredStore
    todo_store: Any = None
    reminders_store: Any = None
    last_user_input_at: datetime | None = None  # для long-focus warning


@dataclass(slots=True)
class Notification:
    text: str
    importance: str = "normal"        # 'normal' / 'high' / 'low'
    trigger_id: str = "unknown"
    dedup_key: str = ""
    suppress_voice: bool = False      # если True — пропустить voice/avatar TTS


# ─── Trigger 1: утренний брифинг с catch-up ────────────────────────
# Логика: окно для отправки 09:00-20:59. Раз в день (dedup по дате).
# Если HUD стартовал в 13:00 и сегодня ещё не было брифинга — отправим
# в первый же тик. После 21:00 — поздно (вечер, вместо утреннего).
MORNING_BRIEF_HOUR_START = 9    # с 09:00
MORNING_BRIEF_HOUR_END = 21     # до 21:00 (не включая)


async def trigger_morning_brief(ctx: TriggerContext) -> Notification | None:
    """Утренний брифинг с catch-up: если в окне 09-21 ещё не отправляли
    сегодня — отправим. Дедуп по дате, один раз в сутки.
    """
    if not (MORNING_BRIEF_HOUR_START <= ctx.now.hour < MORNING_BRIEF_HOUR_END):
        return None
    today_key = ctx.now.strftime("%Y-%m-%d")
    if ctx.dedup.already_fired("morning_brief", today_key):
        return None

    from core.briefings import live_morning_brief
    try:
        text = await live_morning_brief(
            ctx.memory, ctx.claude,
            todo_store=ctx.todo_store,
            reminders_store=ctx.reminders_store,
        )
    except Exception as e:
        logger.warning("proactive_morning_brief_failed", error=str(e))
        return None

    # Если catch-up (НЕ ровно 09:xx) — добавим короткую преамбулу
    if ctx.now.hour >= MORNING_BRIEF_HOUR_START + 2:  # позже 11:00
        text = f"Доброе утро запоздалое, Босс — раньше до тебя не дотянулся.\n\n{text}"

    ctx.dedup.mark_fired("morning_brief", today_key)
    return Notification(
        text=text,
        importance="high",
        trigger_id="morning_brief",
        dedup_key=today_key,
    )


# ─── Trigger 1b: вечерний брифинг с catch-up ───────────────────────
# Окно 21:00-23:59. Раз в день (dedup по дате).
EVENING_BRIEF_HOUR_START = 21
EVENING_BRIEF_HOUR_END = 24  # фактически до 23:59


async def trigger_evening_brief(ctx: TriggerContext) -> Notification | None:
    """Вечерний итог дня. Окно 21:00-23:59, один раз в сутки."""
    if not (EVENING_BRIEF_HOUR_START <= ctx.now.hour < EVENING_BRIEF_HOUR_END):
        return None
    today_key = ctx.now.strftime("%Y-%m-%d")
    if ctx.dedup.already_fired("evening_brief", today_key):
        return None

    from core.briefings import evening_brief
    try:
        text = await evening_brief(ctx.memory)
    except Exception as e:
        logger.warning("proactive_evening_brief_failed", error=str(e))
        return None

    ctx.dedup.mark_fired("evening_brief", today_key)
    return Notification(
        text=text,
        importance="normal",
        trigger_id="evening_brief",
        dedup_key=today_key,
    )


# ─── Trigger 2: ближайшее событие календаря (≤ 15 мин) ─────────────
CALENDAR_LEAD_MIN = 15  # за сколько минут до встречи нудж


def _parse_event_start(ev: dict) -> datetime | None:
    """Извлечь start как naive datetime в локальной zone."""
    start = ev.get("start") or {}
    dt_str = start.get("dateTime")
    if not dt_str:
        return None  # all-day event — пропускаем (не имеет точного времени)
    try:
        # Google отдаёт ISO с offset, например '2026-05-22T15:00:00+03:00'
        dt = datetime.fromisoformat(dt_str)
        # Приводим к naive local — у нас now() тоже naive
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


# Singleton для GoogleCalendarClient — создаётся лениво.
_GCAL_CLIENT = None


def _get_gcal_client():
    global _GCAL_CLIENT
    if _GCAL_CLIENT is None:
        try:
            from core.google_calendar import GoogleCalendarClient
            _GCAL_CLIENT = GoogleCalendarClient()
        except Exception as e:
            logger.debug("gcal_client_init_failed", error=str(e))
            return None
    return _GCAL_CLIENT


async def trigger_calendar_imminent(ctx: TriggerContext) -> Notification | None:
    """Найти ближайшее событие в Google Calendar ≤ 15 мин до начала.
    Если есть и ещё не нуджили — публикуем.
    """
    client = _get_gcal_client()
    if client is None:
        return None
    try:
        events = await client.list_today(limit=20)
    except Exception as e:
        logger.debug("proactive_calendar_check_failed", error=str(e))
        return None

    if not events:
        return None

    soon_threshold = ctx.now + timedelta(minutes=CALENDAR_LEAD_MIN)
    for ev in events:
        start = _parse_event_start(ev)
        if start is None:
            continue
        # Только future events в окне ≤ CALENDAR_LEAD_MIN
        if ctx.now <= start <= soon_threshold:
            evt_id = ev.get("id") or f"{ev.get('summary','?')}-{start.isoformat()}"
            if ctx.dedup.already_fired("calendar_imminent", evt_id):
                continue
            ctx.dedup.mark_fired("calendar_imminent", evt_id)
            mins_left = max(1, int((start - ctx.now).total_seconds() // 60))
            title = ev.get("summary") or "(без названия)"
            where = ev.get("location") or ""
            location_part = f" — {where}" if where else ""
            return Notification(
                text=f"Босс, через {mins_left} мин встреча: «{title}»{location_part}.",
                importance="high",
                trigger_id="calendar_imminent",
                dedup_key=evt_id,
            )
    return None


# ─── Trigger 3: long-focus warning (Босс долго не отвлекался) ──────
LONG_FOCUS_MIN = 90       # 1.5 часа активности без перерыва
LONG_FOCUS_COOLDOWN_H = 3  # не чаще раза в 3 часа


async def trigger_long_focus(ctx: TriggerContext) -> Notification | None:
    if ctx.last_user_input_at is None:
        return None
    # Если был перерыв за последние LONG_FOCUS_COOLDOWN_H часов — не нуджим
    cooldown_start = (ctx.now - timedelta(hours=LONG_FOCUS_COOLDOWN_H)).strftime("%Y-%m-%d-%H")
    if ctx.dedup.already_fired("long_focus", cooldown_start):
        return None
    # Если Босс был активен в последний LONG_FOCUS_MIN — значит ОН за компом
    active_since = ctx.last_user_input_at
    span_min = (ctx.now - active_since).total_seconds() / 60
    if span_min < 5:
        # Только что писал — не отвлекаем
        return None
    if span_min > 30:
        # Уже отошёл — не нуджим
        return None
    # А вот когда last_user_input давнее LONG_FOCUS_MIN — это уже долго
    # без переключения. Hmm, нужна другая логика — упростим.
    # Чек: если first_input_ever_in_session был более LONG_FOCUS_MIN назад
    # И при этом часто пишет — значит долгий focus.
    # Для MVP: триггеримся если last_input в окне 5-30 мин назад И прошло
    # > LONG_FOCUS_MIN с момента *_first_input_today (упрощаем — не делаем
    # сейчас, оставим placeholder).
    return None  # пока заглушка — нужен tracker first_input_today


# ─── Trigger 4: backup health (если последний backup упал) ─────────
async def trigger_backup_health(ctx: TriggerContext) -> Notification | None:
    """Проверить workspace/backup.log на последнюю ошибку.
    Если последняя запись содержит [ERROR] и она в последние 6 часов —
    нудж раз в день.
    """
    try:
        path = ctx.memory.workspace / "backup.log"
        if not path.exists():
            return None
        last_lines = path.read_text(encoding="utf-8").splitlines()[-30:]
    except Exception:
        return None

    # Ищем последний [ERROR] / [OK] из конца
    last_error_line: str | None = None
    for line in reversed(last_lines):
        if "[OK]" in line and "DONE" in line:
            # Свежий успешный backup — всё ок, не нуджим
            return None
        if "[ERROR]" in line:
            last_error_line = line
            break
    if last_error_line is None:
        return None

    # Дедуп: раз в день
    today_key = ctx.now.strftime("%Y-%m-%d")
    if ctx.dedup.already_fired("backup_failed", today_key):
        return None
    ctx.dedup.mark_fired("backup_failed", today_key)

    # Извлечём краткую суть ошибки
    short = last_error_line[-120:].strip()
    return Notification(
        text=f"Босс, backup упал. Последняя строка лога:\n{short}",
        importance="high",
        trigger_id="backup_failed",
        dedup_key=today_key,
    )


# ─── Trigger 5: weekly goal review (G2) ────────────────────────────
WEEKLY_REVIEW_HOUR = 21    # 21:00
WEEKLY_REVIEW_WEEKDAY = 6  # Sunday (Monday=0)


async def trigger_weekly_goal_review(ctx: TriggerContext) -> Notification | None:
    """Воскресенье 21:00 — обзор прогресса по всем активным целям.

    Использует тот же formatter что список целей, плюс мягкое summary
    через Claude — есть ли цели где отстаёшь.
    """
    if ctx.now.weekday() != WEEKLY_REVIEW_WEEKDAY:
        return None
    if ctx.now.hour != WEEKLY_REVIEW_HOUR:
        return None
    week_key = ctx.now.strftime("%Y-W%V")
    if ctx.dedup.already_fired("weekly_goal_review", week_key):
        return None
    # Ленивая загрузка goals store
    try:
        from core.skills.goals_skill import GoalsStore, _format_goal
        store = GoalsStore(ctx.memory.workspace / "goals.json")
        goals = store.list_active()
    except Exception as e:
        logger.warning("weekly_goal_review_load_failed", error=str(e))
        return None
    if not goals:
        return None
    ctx.dedup.mark_fired("weekly_goal_review", week_key)

    lines = ["Босс, недельный обзор целей:"]
    behind: list[str] = []
    for g in goals:
        lines.append(_format_goal(g))
        # детектим отставание
        pace_str = _format_goal(g)
        if "отстаёшь" in pace_str:
            behind.append(g["name"])

    if behind:
        lines.append("")
        if len(behind) == 1:
            lines.append(f"Подналечь стоит на: {behind[0]}.")
        else:
            lines.append(f"Подналечь стоит на: {', '.join(behind)}.")

    return Notification(
        text="\n".join(lines),
        importance="normal",
        trigger_id="weekly_goal_review",
        dedup_key=week_key,
    )


# ─── Триггер-роутер ─────────────────────────────────────────────────
ALL_TRIGGERS = [
    trigger_morning_brief,
    trigger_evening_brief,
    trigger_calendar_imminent,
    trigger_backup_health,
    trigger_weekly_goal_review,
    # trigger_long_focus,  # заглушка пока
]

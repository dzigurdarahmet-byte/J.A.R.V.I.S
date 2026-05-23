"""CalendarSkill — чтение событий из Google Calendar.

Триггеры: «что у меня сегодня», «когда встреча», «расписание на завтра»,
«календарь на неделю», «события сегодня».

Создание событий (для Phase 2) — через Claude tool-use, не через L1.
"""

from __future__ import annotations

from core.google_calendar import GoogleCalendar, format_events_human
from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


class CalendarSkill(KeywordSkill):
    name = "calendar"
    keywords = [
        r"\bчто\s+у\s+меня\s+(сегодня|завтра|на\s+(сегодня|завтра|неделю))\b",
        r"\bрасписание\s+на\s+(сегодня|завтра|неделю)\b",
        r"\bкалендарь\s+на\s+(сегодня|завтра|неделю)\b",
        r"\bкогда\s+встреча\b",
        r"\bближайш\w+\s+встреча\b",
        r"\bсобытия\s+(сегодня|завтра)\b",
        r"\bпокажи\s+календарь\b",
        r"\bкакие\s+у\s+меня\s+встречи\b",
    ]

    _cal_singleton: GoogleCalendar | None = None

    @classmethod
    def _get_cal(cls) -> GoogleCalendar:
        if cls._cal_singleton is None:
            cls._cal_singleton = GoogleCalendar()
        return cls._cal_singleton

    async def run(self, text: str, request_id: str) -> SkillResult:
        cal = self._get_cal()
        low = text.lower()

        # Определяем диапазон
        if "завтра" in low:
            events = await cal.list_tomorrow()
            label = "На завтра"
        elif "неделю" in low or "недел" in low:
            events = await cal.list_week()
            label = "На неделю"
        else:
            events = await cal.list_today()
            label = "На сегодня"

        if not events:
            return SkillResult(text=f"{label}: ничего не запланировано.", speakable=True)

        text_out = f"{label}:\n" + format_events_human(events)
        return SkillResult(text=text_out, speakable=False)

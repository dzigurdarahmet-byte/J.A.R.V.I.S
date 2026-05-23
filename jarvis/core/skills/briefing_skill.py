"""On-demand брифинг как L1-скилл.

Триггеры: «утренний брифинг», «вечерний брифинг», «дай брифинг», «сводка дня».
Если в init передан claude provider — используется live JARVIS-стиль брифинг
(через core.briefings.live_morning_brief), иначе fallback на шаблонный.
"""

from __future__ import annotations

from core.briefings import brief_now, live_morning_brief
from core.memory import MemoryManager
from core.router import SkillResult
from core.skills.base import KeywordSkill


class BriefingSkill(KeywordSkill):
    name = "briefing"
    keywords = [
        r"\bутренний\s+брифинг\b",
        r"\bвечерний\s+брифинг\b",
        r"\bдай\s+(мне\s+)?брифинг\b",
        r"\bсводка\s+дня\b",
        r"\bбрифинг\b",
        r"\bитог\s+дня\b",
    ]

    def __init__(
        self,
        memory: MemoryManager,
        claude=None,        # ClaudeProvider — для live JARVIS-стиля
        todo_store=None,    # TodoStore — для активных задач в брифинге
        reminders_store=None,  # RemindersStore — для напоминаний на сегодня
    ) -> None:
        super().__init__()
        self._memory = memory
        self._claude = claude
        self._todo_store = todo_store
        self._reminders_store = reminders_store

    async def run(self, text: str, request_id: str) -> SkillResult:
        low = text.lower()
        kind = "auto"
        if "утренн" in low or "утро" in low:
            kind = "morning"
        elif "вечерн" in low or "вечер" in low or "итог" in low or "сводка" in low:
            kind = "evening"

        # Live JARVIS-стиль — только для morning + если есть Claude
        if kind in ("morning", "auto") and self._claude is not None:
            from datetime import datetime
            if kind == "auto" and datetime.now().hour >= 16:
                # после 16 — вечерний, шаблонный
                result = await brief_now(self._memory, kind="evening")
            else:
                result = await live_morning_brief(
                    self._memory,
                    self._claude,
                    todo_store=self._todo_store,
                    reminders_store=self._reminders_store,
                )
            return SkillResult(text=result, speakable=False)

        # Иначе шаблонный
        result = await brief_now(self._memory, kind=kind)
        return SkillResult(text=result, speakable=False)

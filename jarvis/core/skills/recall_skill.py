"""RecallSkill — семантический поиск по Tier 3 vector store.

Триггеры: «что я говорил про X», «помнишь как», «найди в памяти», «когда я
упоминал», «вспомни про».
"""

from __future__ import annotations

import re
from datetime import datetime

from core.memory import MemoryManager
from core.router import SkillResult
from core.skills.base import KeywordSkill


class RecallSkill(KeywordSkill):
    name = "recall"
    keywords = [
        r"\bчто\s+я\s+говорил\s+про\b",
        r"\bчто\s+я\s+(говорил|писал|рассказывал)\b",
        r"\bпомнишь\s+(как|когда|про)\b",
        r"\bнайди\s+в\s+памяти\b",
        r"\bкогда\s+я\s+(говорил|упоминал)\b",
        r"\bвспомни\s+(про|о|как)\b",
        r"\bищи\s+в\s+памяти\b",
    ]

    def __init__(self, memory: MemoryManager) -> None:
        super().__init__()
        self._memory = memory

    async def run(self, text: str, request_id: str) -> SkillResult:
        # вытаскиваем «query» — то что после ключевого слова
        m = re.search(
            r"\b(?:говорил|писал|рассказывал|помнишь|про|о|как|когда)\s+(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        query = m.group(1).strip().rstrip("?.!,") if m else text
        if len(query) < 3:
            return SkillResult(
                text="Босс, уточни — про что искать?",
                speakable=True,
            )

        hits = await self._memory.search_vector(query, limit=5)
        if not hits:
            return SkillResult(
                text=f"В моей памяти нет ничего про «{query}».",
                speakable=True,
            )

        # Формируем читаемый ответ
        lines: list[str] = [f"Нашёл в памяти про «{query}»:"]
        for h in hits[:3]:
            ts_str = ""
            try:
                ts = float(h.get("ts") or 0)
                if ts > 0:
                    ts_str = datetime.fromtimestamp(ts).strftime("%d %b, %H:%M")
            except Exception:
                pass
            role = h.get("role") or "?"
            txt = (h.get("text") or "").strip().replace("\n", " ")
            score = h.get("score") or 0.0
            prefix = "Ты" if role == "user" else "Я"
            if ts_str:
                lines.append(f"— ({ts_str}, score {score:.2f}) {prefix}: {txt[:160]}")
            else:
                lines.append(f"— (score {score:.2f}) {prefix}: {txt[:160]}")
        return SkillResult(text="\n".join(lines), speakable=False)

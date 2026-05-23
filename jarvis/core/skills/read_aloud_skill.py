"""Skill: озвучить произвольный текст.

«Прочитай вслух X» / «озвучь X» / «произнеси X».

Технически — это echo-skill: вытаскивает целевой текст из команды и
возвращает его в SkillResult. Router публикует ASSISTANT_REPLY в bus,
avatar.html (если открыт) подхватит через WS и озвучит через
/api/avatar/speak. Voice-канал тоже подхватит и проиграет через свой TTS.
"""
from __future__ import annotations

import re

from core.router import SkillResult
from core.skills.base import KeywordSkill

_PATTERNS = [
    re.compile(r"\b(?:прочитай|зачитай)\s+(?:мне\s+)?(?:вслух|это)?[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bозвучь[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bпроизнеси[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bскажи\s+(?:вслух)?[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
]


class ReadAloudSkill(KeywordSkill):
    name = "read_aloud"
    keywords = [
        r"\bпрочитай\s+(?:мне\s+)?(?:вслух|это)\b",
        r"\bзачитай\b",
        r"\bозвучь\b",
        r"\bпроизнеси\b",
        r"\bскажи\s+вслух\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        for pat in _PATTERNS:
            m = pat.search(text)
            if m:
                body = m.group(1).strip().rstrip(".!?")
                if not body:
                    return SkillResult(text="Босс, что озвучить?", speakable=True)
                # Возвращаем body как answer — Router опубликует это как
                # ASSISTANT_REPLY → avatar.html авто-озвучит через WS.
                return SkillResult(text=body, speakable=True)
        return SkillResult(
            text="Босс, скажи «прочитай вслух X» или «озвучь X».",
            speakable=True,
        )

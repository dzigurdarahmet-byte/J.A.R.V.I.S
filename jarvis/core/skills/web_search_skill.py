"""Skill: веб-поиск через Yandex.XML или DuckDuckGo.

Фразы:
  "найди в интернете X"
  "поищи X"
  "погугли X"
  "посмотри в сети X"
  "что в интернете про X"

Returns top-3 results: заголовок + URL + snippet.
"""
from __future__ import annotations

import re

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill
from core.skills.search_providers import SearchProvider, get_default_provider

logger = get_logger(__name__)

_QUERY_PATTERNS = [
    re.compile(r"\b(?:найди|поищи|посмотри)\s+(?:в\s+интернете|в\s+сети|в\s+гугле|в\s+яндексе)?\s*[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:погугли|погуглить|загугли)\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bчто\s+(?:в\s+интернете|в\s+сети)\s+(?:про|об|о)\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bпоиск[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
]


class WebSearchSkill(KeywordSkill):
    name = "web_search"
    keywords = [
        r"\b(?:найди|поищи|посмотри)\s+(?:в\s+интернете|в\s+сети|в\s+гугле|в\s+яндексе)",
        r"\b(?:погугли|погуглить|загугли)\b",
        r"\bчто\s+(?:в\s+интернете|в\s+сети)\s+(?:про|об|о)\b",
        r"^\s*поиск[:\s]",
    ]

    def __init__(self, provider: SearchProvider | None = None) -> None:
        super().__init__()
        # Provider можно передать явно для тестов; иначе берём из .env
        self._provider = provider or get_default_provider()

    async def run(self, text: str, request_id: str) -> SkillResult:
        # Извлекаем query
        query = None
        for pat in _QUERY_PATTERNS:
            m = pat.search(text)
            if m:
                query = m.group(1).strip().rstrip(".!?")
                break
        if not query:
            return SkillResult(text="Босс, что искать?", speakable=True)

        try:
            results = await self._provider.search(query, limit=3)
        except Exception as e:
            logger.error("web_search_failed", error=str(e), query=query[:80])
            return SkillResult(
                text=f"Босс, не получилось — {self._provider.name} не ответил ({type(e).__name__}).",
                speakable=True,
            )

        if not results:
            return SkillResult(
                text=f"По '{query}' ничего не нашлось.",
                speakable=True,
            )

        # Форматируем top-3
        lines = [f"Результаты по '{query}' (через {self._provider.name}):", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}")
            if r.snippet:
                lines.append(f"   {r.snippet}")
            lines.append(f"   {r.url}")
            lines.append("")
        return SkillResult(text="\n".join(lines).strip(), speakable=True)

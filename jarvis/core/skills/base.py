"""Базовые классы для скиллов."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from core.router import SkillResult


class BaseSkill(ABC):
    """Минимальный контракт скилла.

    Два режима:
    - L1 (keyword): match(text) — оценка уверенности по тексту, run(text) — выполнение.
    - L2 (tool-use): as_tool() — schema для Claude, run_with_args(args) — выполнение
      по аргументам, которые Claude уже распарсил.
    Скилл может реализовывать ОБА режима или только один (другой остаётся опциональным).
    """

    name: str = "base"

    @abstractmethod
    def match(self, text: str) -> float: ...

    @abstractmethod
    async def run(self, text: str, request_id: str) -> SkillResult: ...

    # ── L2 Tool-use (опционально) ──────────────────────────────────────
    def as_tool(self) -> dict[str, Any] | None:
        """Anthropic tool schema (name, description, input_schema).

        Если возвращает None — скилл недоступен Claude как функция.
        Формат: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
        """
        return None

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        """Выполнение по распарсенным аргументам (от Claude tool_use).

        Дефолт — упасть. Скилл с as_tool() обязан переопределить.
        """
        raise NotImplementedError(
            f"skill {self.name!r} declares as_tool() but did not implement run_with_args()"
        )


class KeywordSkill(BaseSkill):
    """Скилл с матчингом по keyword-фразам (case-insensitive substring + границы слов).

    keywords — список regex-паттернов. Match score = max(1.0) если любой keyword
    найден в тексте, иначе 0.0. Простой и быстрый L1-уровень.

    Subclass должен задать:
        name = "..."
        keywords = [r"паттерн1", r"паттерн2", ...]
    И реализовать async run(text, request_id) -> SkillResult.
    """

    keywords: list[str] = []

    def __init__(self) -> None:
        self._patterns = [
            re.compile(p, flags=re.IGNORECASE | re.UNICODE) for p in self.keywords
        ]

    def match(self, text: str) -> float:
        for pat in self._patterns:
            if pat.search(text):
                return 1.0
        return 0.0

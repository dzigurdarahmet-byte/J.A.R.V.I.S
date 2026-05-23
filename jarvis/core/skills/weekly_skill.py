"""WeeklySkill — on-demand сводка недели через Claude.

Триггеры: «итог недели», «что было за неделю», «summary недели», «дай итог недели».
"""

from __future__ import annotations

from pathlib import Path

from core.logging import get_logger
from core.providers import ClaudeProvider
from core.router import SkillResult
from core.skills.base import KeywordSkill
from core.weekly_summary import generate_weekly_summary

logger = get_logger(__name__)
WORKSPACE_DIR = Path(__file__).resolve().parents[2] / "workspace"


class WeeklySkill(KeywordSkill):
    name = "weekly"
    keywords = [
        r"\bитог\s+недели\b",
        r"\bсводка\s+недели\b",
        r"\bчто\s+было\s+за\s+неделю\b",
        r"\bдай\s+итог\s+недели\b",
        r"\bкак\s+прошла\s+неделя\b",
        r"\bпокажи\s+итоги\s+недели\b",
    ]

    def __init__(self, claude: ClaudeProvider) -> None:
        super().__init__()
        self._claude = claude

    async def run(self, text: str, request_id: str) -> SkillResult:
        out = await generate_weekly_summary(self._claude, WORKSPACE_DIR)
        return SkillResult(text=out, speakable=False)

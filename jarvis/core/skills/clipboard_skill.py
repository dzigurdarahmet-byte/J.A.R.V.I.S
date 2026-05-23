"""Skill: работа с буфером обмена через pyperclip.

«Что в буфере» — прочитать
«положи в буфер X» — записать
«очисти буфер» — пусто
"""
from __future__ import annotations

import asyncio
import re

import pyperclip

from core.router import SkillResult
from core.skills.base import KeywordSkill


_READ_PATTERNS = [
    re.compile(r"\bчто\s+в\s+(?:буфере|clipboard|клипборде)\b", re.IGNORECASE),
    re.compile(r"\bпокажи\s+(?:мне\s+)?буфер\b", re.IGNORECASE),
    re.compile(r"\bпрочитай\s+буфер\b", re.IGNORECASE),
    re.compile(r"\b(?:содержимое|содержание)\s+буфера\b", re.IGNORECASE),
]
_WRITE_PATTERNS = [
    re.compile(r"\b(?:положи|запиши|скопируй)\s+(?:в\s+буфер|в\s+clipboard)[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:в\s+буфер)[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
]
_CLEAR_PATTERNS = [
    re.compile(r"\b(?:очисти|сотри|удали)\s+буфер\b", re.IGNORECASE),
]


class ClipboardSkill(KeywordSkill):
    name = "clipboard"
    keywords = [
        r"\bчто\s+в\s+(?:буфере|clipboard|клипборде)\b",
        r"\bпокажи\s+(?:мне\s+)?буфер\b",
        r"\bпрочитай\s+буфер\b",
        r"\b(?:содержимое|содержание)\s+буфера\b",
        r"\b(?:положи|запиши|скопируй)\s+(?:в\s+буфер|в\s+clipboard)",
        r"\b(?:в\s+буфер)[:\s]",
        r"\b(?:очисти|сотри|удали)\s+буфер\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        # CLEAR (специфичнее WRITE, проверяем первым)
        for pat in _CLEAR_PATTERNS:
            if pat.search(text):
                try:
                    await asyncio.to_thread(pyperclip.copy, "")
                    return SkillResult(text="Буфер очищен.", speakable=True)
                except pyperclip.PyperclipException as e:
                    return SkillResult(text=f"Не получилось очистить: {e}", speakable=True)

        # WRITE
        for pat in _WRITE_PATTERNS:
            m = pat.search(text)
            if m:
                content = m.group(1).strip().rstrip(".!?")
                if not content:
                    return SkillResult(text="Босс, что положить в буфер?", speakable=True)
                try:
                    await asyncio.to_thread(pyperclip.copy, content)
                    preview = content if len(content) <= 60 else content[:60] + "…"
                    return SkillResult(text=f"Положил в буфер: {preview}", speakable=True)
                except pyperclip.PyperclipException as e:
                    return SkillResult(text=f"Не получилось записать: {e}", speakable=True)

        # READ (default fallback если keyword сработал)
        for pat in _READ_PATTERNS:
            if pat.search(text):
                try:
                    content = await asyncio.to_thread(pyperclip.paste)
                except pyperclip.PyperclipException as e:
                    return SkillResult(text=f"Буфер недоступен: {e}", speakable=True)
                if not content:
                    return SkillResult(text="Буфер пустой.", speakable=True)
                # Длинный текст обрежем для голосового вывода
                preview = content if len(content) <= 300 else content[:300] + "…"
                return SkillResult(text=f"В буфере: {preview}", speakable=True)

        return SkillResult(text="Босс, не понял команду для буфера.", speakable=True)

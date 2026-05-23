"""Skill: системная громкость.

«Громче / тише / выключи звук / включи звук / громкость 50%».

Использует pycaw (Core Audio API) для процентного контроля и состояния.
Для step-команд (громче/тише) — тоже pycaw +/- 10%.
"""
from __future__ import annotations

import asyncio
import re

from pycaw.pycaw import AudioUtilities

from core.router import SkillResult
from core.skills.base import KeywordSkill


def _get_endpoint():
    # pycaw 1.11+: AudioUtilities.GetSpeakers() возвращает AudioDevice wrapper,
    # у которого готовый атрибут .EndpointVolume — это и есть IAudioEndpointVolume.
    return AudioUtilities.GetSpeakers().EndpointVolume


def _read_state() -> tuple[int, bool]:
    """Возвращает (volume_percent, is_muted)."""
    ep = _get_endpoint()
    scalar = ep.GetMasterVolumeLevelScalar()  # 0.0..1.0
    muted = bool(ep.GetMute())
    return int(round(scalar * 100)), muted


def _set_percent(pct: int) -> int:
    pct = max(0, min(100, pct))
    ep = _get_endpoint()
    ep.SetMasterVolumeLevelScalar(pct / 100.0, None)
    return pct


def _set_mute(state: bool) -> None:
    ep = _get_endpoint()
    ep.SetMute(1 if state else 0, None)


_PERCENT_PATTERN = re.compile(r"\bгромкость\s+(?:на\s+)?(\d{1,3})\s*%?\b", re.IGNORECASE)
_UP_PATTERN = re.compile(r"\b(?:громче|сделай\s+громче|увеличь\s+(?:громкость|звук)|погромче|прибавь)\b", re.IGNORECASE)
_DOWN_PATTERN = re.compile(r"\b(?:тише|сделай\s+тише|уменьши\s+(?:громкость|звук)|потише|убавь)\b", re.IGNORECASE)
_MUTE_PATTERN = re.compile(r"\b(?:выключи\s+звук|немое|mute|заглуши|вырубай\s+звук)\b", re.IGNORECASE)
_UNMUTE_PATTERN = re.compile(r"\b(?:включи\s+звук|unmute|верни\s+звук|разглуши)\b", re.IGNORECASE)
_STATUS_PATTERN = re.compile(r"\b(?:какая\s+(?:сейчас\s+)?громкость|сколько\s+(?:сейчас\s+)?громкость)\b", re.IGNORECASE)

STEP = 10  # шаг для громче/тише


class VolumeSkill(KeywordSkill):
    name = "volume"
    keywords = [
        r"\bгромкость\s+(?:на\s+)?\d",
        r"\b(?:громче|сделай\s+громче|увеличь\s+(?:громкость|звук)|погромче|прибавь)\b",
        r"\b(?:тише|сделай\s+тише|уменьши\s+(?:громкость|звук)|потише|убавь)\b",
        r"\b(?:выключи\s+звук|немое|mute|заглуши|вырубай\s+звук)\b",
        r"\b(?:включи\s+звук|unmute|верни\s+звук|разглуши)\b",
        r"\b(?:какая\s+(?:сейчас\s+)?громкость|сколько\s+(?:сейчас\s+)?громкость)\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        try:
            return await asyncio.to_thread(self._sync_run, text)
        except OSError as e:
            # Comtypes / Core Audio может не подтянуться (например без аудио-устройств)
            return SkillResult(text=f"Не могу управлять звуком: {e}", speakable=True)

    @staticmethod
    def _sync_run(text: str) -> SkillResult:
        # set percent
        m = _PERCENT_PATTERN.search(text)
        if m:
            pct = _set_percent(int(m.group(1)))
            _set_mute(False)
            return SkillResult(text=f"Громкость {pct}%.", speakable=True)

        if _MUTE_PATTERN.search(text):
            _set_mute(True)
            return SkillResult(text="Звук выключил.", speakable=True)

        if _UNMUTE_PATTERN.search(text):
            _set_mute(False)
            return SkillResult(text="Звук включил.", speakable=True)

        if _UP_PATTERN.search(text):
            cur, _ = _read_state()
            new = _set_percent(cur + STEP)
            _set_mute(False)
            return SkillResult(text=f"Громче — {new}%.", speakable=True)

        if _DOWN_PATTERN.search(text):
            cur, _ = _read_state()
            new = _set_percent(cur - STEP)
            return SkillResult(text=f"Тише — {new}%.", speakable=True)

        if _STATUS_PATTERN.search(text):
            cur, muted = _read_state()
            if muted:
                return SkillResult(text=f"Звук выключен (был на {cur}%).", speakable=True)
            return SkillResult(text=f"Громкость {cur}%.", speakable=True)

        return SkillResult(text="Босс, не понял команду громкости.", speakable=True)

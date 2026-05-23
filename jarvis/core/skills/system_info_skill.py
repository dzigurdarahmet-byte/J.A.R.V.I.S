"""Skill: системная информация — CPU/RAM/disk.

«Сколько занято на диске», «загрузка процессора», «свободно памяти».
"""
from __future__ import annotations

import asyncio
import re
import shutil

import psutil

from core.router import SkillResult
from core.skills.base import KeywordSkill


_GB = 1024 ** 3


def _fmt_gb(b: float) -> str:
    if b >= _GB:
        return f"{b / _GB:.1f} ГБ"
    return f"{b / (1024 ** 2):.0f} МБ"


async def _cpu_load() -> str:
    # psutil.cpu_percent() с interval=1.0 — синхронный, оборачиваем
    pct = await asyncio.to_thread(psutil.cpu_percent, 1.0)
    cores = psutil.cpu_count(logical=True)
    return f"Загрузка процессора {pct:.0f}% (на {cores} логических ядрах)."


def _ram() -> str:
    v = psutil.virtual_memory()
    return (
        f"Свободно памяти {_fmt_gb(v.available)} из {_fmt_gb(v.total)} "
        f"(занято {v.percent:.0f}%)."
    )


def _disk() -> str:
    """Сумма по всем фиксированным дискам + детали по каждому."""
    parts = psutil.disk_partitions(all=False)
    lines = []
    for p in parts:
        if "removable" in (p.opts or "").lower():
            continue
        try:
            u = shutil.disk_usage(p.mountpoint)
            lines.append(
                f"{p.device.rstrip('\\')} — свободно {_fmt_gb(u.free)} из {_fmt_gb(u.total)} "
                f"(занято {u.used / u.total * 100:.0f}%)"
            )
        except (OSError, PermissionError):
            continue
    if not lines:
        return "Босс, не дотянулся до дисков."
    return "\n".join(lines)


def _battery() -> str | None:
    b = psutil.sensors_battery()
    if b is None:
        return None  # стационарный ПК
    status = "заряжается" if b.power_plugged else "от батареи"
    return f"Батарея {b.percent:.0f}% ({status})."


_CPU_PATTERNS = [r"\b(?:загрузк[аи]?|нагрузк[аи]?)\s+(?:процессора|cpu)", r"\bcpu\b", r"\bпроцессор\b"]
_RAM_PATTERNS = [r"\b(?:свободно|занято)\s+памяти\b", r"\bпамят[ьи]\b", r"\b(?:ram|оперативк[аи])\b"]
_DISK_PATTERNS = [r"\b(?:свободно|занято)\s+(?:на\s+)?диск", r"\bдиск[аеу]?\b", r"\bdisk\b", r"\bстораж\b"]
_BATTERY_PATTERNS = [r"\bбатаре[яй]\b", r"\bзаряд\b"]
_ALL_PATTERN = re.compile(r"\b(?:статус|инфо)\s+(?:системы|компа|компьютера)\b", re.IGNORECASE)


class SystemInfoSkill(KeywordSkill):
    name = "system_info"
    keywords = [
        r"\b(?:загрузк[аи]?|нагрузк[аи]?)\s+(?:процессора|cpu)",
        r"\b(?:свободно|занято)\s+(?:памяти|на\s+диске)",
        r"\b(?:сколько\s+)?(?:свободно|занято)\s+памяти",
        r"\b(?:сколько\s+)?памят[ьи]\b",
        r"\bсколько\s+(?:места\s+)?на\s+диске",
        r"\bбатаре[яй]\b",
        r"\bстатус\s+(?:системы|компа|компьютера)\b",
        r"\bинфо\s+(?:системы|компа|компьютера)\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        low = text.lower()

        # «статус компьютера» — всё сразу
        if _ALL_PATTERN.search(text):
            cpu = await _cpu_load()
            parts = [cpu, _ram(), _disk()]
            bat = _battery()
            if bat:
                parts.append(bat)
            return SkillResult(text="\n".join(parts), speakable=True)

        # Дальше — точечные ответы по группам ключевых слов
        if any(re.search(p, low) for p in _CPU_PATTERNS):
            return SkillResult(text=await _cpu_load(), speakable=True)
        if any(re.search(p, low) for p in _RAM_PATTERNS):
            return SkillResult(text=_ram(), speakable=True)
        if any(re.search(p, low) for p in _DISK_PATTERNS):
            return SkillResult(text=_disk(), speakable=True)
        if any(re.search(p, low) for p in _BATTERY_PATTERNS):
            b = _battery()
            return SkillResult(text=b or "У этого компа нет батареи (стационарный).", speakable=True)

        # Дефолт — общий статус
        cpu = await _cpu_load()
        return SkillResult(text=f"{cpu}\n{_ram()}", speakable=True)

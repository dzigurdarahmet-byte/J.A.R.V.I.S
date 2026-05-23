"""Skill: открыть/закрыть приложения голосом.

«Открой VS Code», «закрой браузер», «запусти калькулятор».

Безопасность:
  - Whitelist — только из APP_MAP. Никаких произвольных команд.
  - Close работает по process name, system-process не трогаем.
  - System.Diagnostics.Process через subprocess — без shell=True.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AppSpec:
    """Описание приложения для whitelist."""
    canonical: str               # каноническое имя для ответа («VS Code»)
    open_cmd: str                # команда запуска (имя exe из PATH или полный путь)
    process_names: tuple[str, ...]  # имена процессов для закрытия (без .exe)


# Whitelist — добавляем только то, что Боссу действительно нужно голосом.
APP_MAP: dict[str, AppSpec] = {
    # ключи — алиасы (нижний регистр, без диакритики)
    "vscode": AppSpec("VS Code", "code", ("Code",)),
    "vs code": AppSpec("VS Code", "code", ("Code",)),
    "редактор кода": AppSpec("VS Code", "code", ("Code",)),
    "edge": AppSpec("Edge", "msedge", ("msedge",)),
    "браузер": AppSpec("Edge", "msedge", ("msedge",)),
    "chrome": AppSpec("Chrome", "chrome", ("chrome",)),
    "хром": AppSpec("Chrome", "chrome", ("chrome",)),
    "блокнот": AppSpec("Блокнот", "notepad", ("notepad",)),
    "notepad": AppSpec("Блокнот", "notepad", ("notepad",)),
    "калькулятор": AppSpec("Калькулятор", "calc", ("CalculatorApp", "Calculator")),
    "проводник": AppSpec("Проводник", "explorer", ()),  # explorer.exe не закрываем — критичный
    "explorer": AppSpec("Проводник", "explorer", ()),
    "telegram": AppSpec("Telegram", "telegram", ("Telegram",)),
    "телеграм": AppSpec("Telegram", "telegram", ("Telegram",)),
    "терминал": AppSpec("PowerShell", "powershell", ("powershell", "WindowsTerminal")),
    "powershell": AppSpec("PowerShell", "powershell", ("powershell", "WindowsTerminal")),
    "ножницы": AppSpec("Ножницы", "snippingtool", ("SnippingTool", "ScreenClippingHost")),
    "spotify": AppSpec("Spotify", "spotify", ("Spotify",)),
    "outlook": AppSpec("Outlook", "outlook", ("OUTLOOK", "olk")),
}


_OPEN_PATTERN = re.compile(
    r"\b(?:открой|запусти|включи|открыть|стартани)\s+(.+?)(?:[\s.!?]|$)",
    re.IGNORECASE,
)
_CLOSE_PATTERN = re.compile(
    r"\b(?:закрой|выключи|останови|закрыть|кильни|убей)\s+(.+?)(?:[\s.!?]|$)",
    re.IGNORECASE,
)


def _resolve_app(query: str) -> AppSpec | None:
    """Найти AppSpec по подстроке. Возвращает первое совпадение."""
    q = query.lower().strip()
    # exact match
    if q in APP_MAP:
        return APP_MAP[q]
    # substring match — берём самый длинный ключ который входит в q
    candidates = [(k, v) for k, v in APP_MAP.items() if k in q]
    if not candidates:
        return None
    candidates.sort(key=lambda kv: -len(kv[0]))
    return candidates[0][1]


async def _start_app(spec: AppSpec) -> bool:
    """Запуск через subprocess (без shell, чтобы не было инъекций)."""
    def _do() -> bool:
        try:
            subprocess.Popen(
                [spec.open_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
            )
            return True
        except FileNotFoundError:
            # Возможно команда в Start Menu, не в PATH. Пробуем через explorer.
            try:
                subprocess.Popen(["cmd", "/c", "start", "", spec.open_cmd], shell=False)
                return True
            except Exception:
                return False
        except Exception as e:
            logger.error("app_start_failed", app=spec.canonical, error=str(e))
            return False
    return await asyncio.to_thread(_do)


async def _stop_app(spec: AppSpec) -> int:
    """Прибить процессы. Возвращает сколько процессов было убито."""
    if not spec.process_names:
        return 0

    def _do() -> int:
        killed = 0
        for pname in spec.process_names:
            try:
                # taskkill /F /IM <name>.exe /T — гасит дерево процессов
                r = subprocess.run(
                    ["taskkill", "/F", "/IM", f"{pname}.exe", "/T"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    # parse "SUCCESS: Sent termination signal to the process ..." (одна на каждый PID)
                    killed += r.stdout.lower().count("success")
            except Exception as e:
                logger.warning("app_stop_failed", pname=pname, error=str(e))
        return killed
    return await asyncio.to_thread(_do)


class AppControlSkill(KeywordSkill):
    name = "app_control"
    keywords = [
        r"\b(?:открой|запусти|включи|открыть|стартани)\s+\S",
        r"\b(?:закрой|выключи|останови|закрыть|кильни|убей)\s+\S",
    ]

    def match(self, text: str) -> float:
        # Уступаем FileSkill: «открой документ X», «открой файл X», «открой папку X»
        # — это про файлы Босса, не про приложения.
        if re.search(
            r"\b(?:открой|открыть|закрой|закрыть)\s+(?:мне\s+)?"
            r"(?:файл|документ|папку|директорию|каталог)\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            return 0.0
        # Уступаем MusicSkill: «включи музыку», «поставь Imagine Dragons»,
        # «играй Арию», «следующий трек» — это музыка, не приложения.
        # Также подсветка Станции: «включи синий», «зажги вечеринку».
        if re.search(
            r"\b(?:музык\w*|музло|плейлист|трек|песн\w*|альбом|"
            r"подсветк\w*|вечеринк\w+|закат|рассвет|лава\s+лампа|"
            r"свеч\w*|стробоскоп|романтик\w+|"
            # цвета подсветки
            r"белый|красный|оранжевый|жёлтый|желтый|зелёный|зеленый|"
            r"голубой|синий|фиолетовый|пурпурный|розовый)\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            return 0.0
        # Если после «открой/закрой/включи» не следует ничего из APP_MAP —
        # уступаем (это не про приложения вообще). Проверим что в тексте
        # есть хоть один известный alias.
        match_verb = re.search(
            r"\b(?:открой|запусти|включи|открыть|стартани|"
            r"закрой|выключи|останови|закрыть|кильни|убей)\s+(.+?)(?:[\s.!?]|$)",
            text, flags=re.IGNORECASE | re.UNICODE,
        )
        if match_verb:
            query = match_verb.group(1)
            if _resolve_app(query) is None:
                return 0.0  # Это не про наши приложения — пусть другой skill ловит
        return super().match(text)

    async def run(self, text: str, request_id: str) -> SkillResult:
        # CLOSE
        m = _CLOSE_PATTERN.search(text)
        if m:
            spec = _resolve_app(m.group(1))
            if spec is None:
                return SkillResult(
                    text=f"Босс, не знаю такого приложения. Поддерживаю: {self._list_apps()}.",
                    speakable=True,
                )
            if not spec.process_names:
                return SkillResult(
                    text=f"Босс, {spec.canonical} не закрываю — это системное приложение.",
                    speakable=True,
                )
            killed = await _stop_app(spec)
            if killed == 0:
                return SkillResult(text=f"{spec.canonical} и не был запущен.", speakable=True)
            return SkillResult(text=f"Закрыл {spec.canonical}.", speakable=True)

        # OPEN
        m = _OPEN_PATTERN.search(text)
        if m:
            spec = _resolve_app(m.group(1))
            if spec is None:
                return SkillResult(
                    text=f"Босс, не знаю такого приложения. Поддерживаю: {self._list_apps()}.",
                    speakable=True,
                )
            ok = await _start_app(spec)
            if ok:
                return SkillResult(text=f"Открываю {spec.canonical}.", speakable=True)
            return SkillResult(
                text=f"Не получилось запустить {spec.canonical} — нет в PATH.",
                speakable=True,
            )

        return SkillResult(
            text=f"Босс, что открыть/закрыть? Поддерживаю: {self._list_apps()}.",
            speakable=True,
        )

    @staticmethod
    def _list_apps() -> str:
        seen = set()
        out = []
        for spec in APP_MAP.values():
            if spec.canonical in seen:
                continue
            seen.add(spec.canonical)
            out.append(spec.canonical)
        return ", ".join(out)

"""Smoke-тест 4 новых skills: read_aloud, app_control, system_info, clipboard.

Гоняет match + run на наборе фраз — без HUD, без сети.
app_control НЕ запускает реальные процессы (только match-check), чтобы не
дёргать VS Code и т.д. под тестом.
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.skills.read_aloud_skill import ReadAloudSkill
from core.skills.app_control_skill import AppControlSkill, _resolve_app
from core.skills.system_info_skill import SystemInfoSkill
from core.skills.clipboard_skill import ClipboardSkill


async def smoke(skill, cases):
    print(f"\n=== {skill.name} ===")
    for c in cases:
        score = skill.match(c)
        if score == 0:
            print(f"  [no-match] {c!r}")
            continue
        try:
            res = await skill.run(c, request_id="t")
            txt = res.text if len(res.text) <= 200 else res.text[:200] + "..."
            print(f"  [match] {c!r}")
            print(f"      → {txt}")
        except Exception as e:
            print(f"  [ERROR] {c!r}: {type(e).__name__}: {e}")


async def main() -> int:
    # read_aloud — безопасно гоняется (echo)
    await smoke(ReadAloudSkill(), [
        "прочитай вслух привет Босс",
        "озвучь это: тест системы озвучки",
        "произнеси одно два три",
        "скажи вслух: проверка",
        "забудь это",  # not match
    ])

    # app_control — только resolve, без реального запуска (чтобы тест не открыл VS Code)
    print("\n=== app_control resolver (без запуска процессов) ===")
    for q in ["vscode", "VS Code", "браузер", "блокнот", "телеграм", "несуществующая_приложуха"]:
        spec = _resolve_app(q)
        print(f"  {q!r} → {spec.canonical if spec else 'None'}")

    # system_info — реально читает данные
    await smoke(SystemInfoSkill(), [
        "загрузка процессора",
        "сколько свободно памяти",
        "сколько места на диске",
        "батарея",
        "статус компьютера",
        "что в задачах",  # not match
    ])

    # clipboard — реальное чтение/запись (на test-машине без проблем)
    await smoke(ClipboardSkill(), [
        "положи в буфер: test clipboard payload",
        "что в буфере",
        "очисти буфер",
        "что в буфере",  # после очистки
    ])

    return 0


sys.exit(asyncio.run(main()))

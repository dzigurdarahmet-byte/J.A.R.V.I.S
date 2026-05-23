"""Тест JARVIS tone polish: пропускаем сухой skill output через Claude."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import settings
from core.providers import build_smart_provider
from core.voice.jarvis_tone import polish_for_jarvis


async def main() -> int:
    claude = build_smart_provider(settings)

    cases = [
        # точный факт + список — должен сохранить #ID и порядок
        "В списке 4 задачи:\n1. купить хлеб\n2. позвонить маме\n3. подготовить отчёт по Q2\n4. забрать посылку с почты",
        # короткий факт
        "Сейчас 14:30.",
        # подтверждение действия
        "Добавил #5: проверить алису.",
        # системные данные с цифрами
        "Свободно памяти 5.0 ГБ из 15.7 ГБ (занято 68%).",
        # ошибка — должен передать суть в стиле
        "Босс, GitHub PAT не найден в .secrets/github_pat.",
        # очень короткий — должен пропустить как есть
        "Готово.",
    ]
    for raw in cases:
        print(f"\n--- RAW ({len(raw)}) ---")
        print(raw)
        polished = await polish_for_jarvis(raw, claude)
        marker = "POLISHED" if polished != raw else "PASS-THROUGH"
        print(f"--- {marker} ({len(polished)}) ---")
        print(polished)

    await claude.close()
    return 0


sys.exit(asyncio.run(main()))

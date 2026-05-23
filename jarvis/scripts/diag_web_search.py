"""Тест WebSearchSkill: парсинг фраз + реальный запрос к DDG (или Yandex если есть ключи)."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.skills.web_search_skill import WebSearchSkill
from core.skills.search_providers import get_default_provider


async def main() -> int:
    provider = get_default_provider()
    print(f"=== provider: {provider.name} ===\n")
    skill = WebSearchSkill(provider=provider)

    cases = [
        "Найди в интернете рецепт борща",
        "Погугли курс рубля сегодня",
        "Поиск: weather forecast Moscow",
        "Что в интернете про FastAPI 0.136",
        "Какая погода",  # not match
    ]
    for c in cases:
        score = skill.match(c)
        if score == 0:
            print(f"[no-match] '{c}'\n")
            continue
        print(f"[match] '{c}'")
        res = await skill.run(c, request_id="t")
        # Печатаем первые 400 символов чтобы не флудить
        body = res.text if len(res.text) <= 400 else res.text[:400] + "...[trunc]"
        print(body)
        print()

    return 0


sys.exit(asyncio.run(main()))

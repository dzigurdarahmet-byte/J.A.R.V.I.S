"""E2E для Tier 3 vector recall.

Засеивает 8 разных фраз (раздельные тематики), потом делает 4 семантических
запроса и проверяет что верхний результат — то что мы хотели.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import enum as _e  # noqa: E402

if not hasattr(_e, "StrEnum"):
    class _SE(str, _e.Enum):
        pass
    _e.StrEnum = _SE  # type: ignore[attr-defined]

from core.memory import MemoryManager  # noqa: E402

WORKSPACE = ROOT / "workspace"

SEEDS = [
    ("Купил подписку Spotify за 199 рублей в месяц", "user", "telegram"),
    ("Запомнил, Босс — подписка Spotify, 199 ₽/мес.", "assistant", "telegram"),
    ("Пароль от роутера дома: K3lly-2024", "user", "telegram"),
    ("Записал пароль от роутера, Босс.", "assistant", "telegram"),
    ("Завтра в 14:30 встреча с дизайнером в кафе на Никольской", "user", "telegram"),
    ("Зафиксировал встречу, Босс — завтра 14:30, Никольская.", "assistant", "telegram"),
    ("Размер обуви у меня 43", "user", "telegram"),
    ("Понял, Босс — размер 43.", "assistant", "telegram"),
]

QUERIES = [
    ("Сколько я плачу за музыку?", "spotify"),
    ("Какой у меня пароль от вайфая?", "пароль"),
    ("Когда у меня встреча?", "встреч"),
    ("Какой у меня размер ноги?", "размер"),
]


async def main() -> int:
    memory = MemoryManager(workspace_dir=WORKSPACE)
    print("Seeding 8 заметок...")
    for text, role, channel in SEEDS:
        await memory.add_to_vector(text, role=role, channel=channel)
    print("Done.\n")

    print(f"{'Q':50}  hit_score  top_text")
    print("-" * 130)
    for query, marker in QUERIES:
        hits = await memory.search_vector(query, limit=3)
        if not hits:
            print(f"{query:50}  ----   MISS")
            continue
        top = hits[0]
        score = top.get("score") or 0
        text = (top.get("text") or "").replace("\n", " ")[:60]
        ok = marker.lower() in text.lower()
        mark = "OK " if ok else "?  "
        print(f"{mark} {query:50}  {score:.3f}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

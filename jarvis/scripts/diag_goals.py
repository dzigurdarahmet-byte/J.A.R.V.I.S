"""Unit-тест GoalsSkill: parser + storage + сценарий жизненного цикла цели."""
from __future__ import annotations
import asyncio, sys, tempfile, shutil
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.skills.goals_skill import GoalsSkill, GoalsStore, _resolve_deadline


print("=== _resolve_deadline ===")
NOW = datetime(2026, 5, 22, 12, 0)
cases = [
    ("до конца месяца", "2026-05-31"),
    ("до конца недели", "2026-05-24"),  # 22 May 2026 is Friday, end of week = Sunday 24
    ("до конца года", "2026-12-31"),
    ("за месяц", "2026-06-21"),
    ("за неделю", "2026-05-29"),
    ("до 15.06", "2026-06-15"),
    ("до 31.12.2026", "2026-12-31"),
    ("без срока", None),
]
for text, exp in cases:
    got = _resolve_deadline(text, now=NOW)
    print(f"  [{'OK ' if got == exp else 'MISS'}] {text!r} → {got}")


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_goals_"))
    store = GoalsStore(tmp / "goals.json")
    sk = GoalsSkill(store)

    print("\n=== full lifecycle ===")
    cases = [
        "цель: написать 5 статей до конца месяца",
        "новая цель: прочитать 12 книг за год",
        "поставь цель: 30 тренировок до 31.07.2026",
        "мои цели",
        "+1 статья: про FastAPI",
        "+2 тренировки",
        "прочёл книгу",
        "мои цели",
        "цель #1 выполнена",
        "удали цель 2",
        "мои цели",
    ]
    for c in cases:
        score = sk.match(c)
        if score == 0:
            print(f"  [no-match] {c!r}")
            continue
        res = await sk.run(c, request_id="t")
        body = res.text if len(res.text) <= 200 else res.text[:200] + "...[trunc]"
        print(f"  [match] {c!r}")
        print(f"      → {body}")

    shutil.rmtree(tmp, ignore_errors=True)

asyncio.run(main())

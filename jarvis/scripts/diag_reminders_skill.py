"""Тест RemindersSkill: parse_when + storage + skill responses + scheduler."""
from __future__ import annotations
import asyncio, sys, tempfile, shutil
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.event_bus import bus, EventType
from core.skills.reminders_skill import (
    RemindersSkill, RemindersStore, ReminderScheduler, parse_when, _format_when,
)

NOW = datetime(2026, 5, 22, 14, 0, 0)  # fixed для теста парсера

print("=== unit: parse_when (NOW =", NOW.strftime("%Y-%m-%d %H:%M"), ") ===")
parse_cases = [
    ("через 30 минут", NOW + timedelta(minutes=30)),
    ("через час", NOW + timedelta(hours=1)),
    ("через 2 часа", NOW + timedelta(hours=2)),
    ("через 15 мин", NOW + timedelta(minutes=15)),
    ("через минуту", NOW + timedelta(minutes=1)),
    ("через пол часа", NOW + timedelta(minutes=30)),
    ("в 15:30", NOW.replace(hour=15, minute=30)),
    ("в 8 утра", (NOW + timedelta(days=1)).replace(hour=8, minute=0)),  # 8 утра уже прошло сегодня
    ("в 18:00", NOW.replace(hour=18, minute=0)),
    ("в 9 вечера", NOW.replace(hour=21, minute=0)),
    ("завтра в 10:00", (NOW + timedelta(days=1)).replace(hour=10, minute=0)),
    ("сегодня в 18", NOW.replace(hour=18, minute=0)),
    ("ерунда без времени", None),
]
parse_ok = True
for text, expected in parse_cases:
    got = parse_when(text, now=NOW)
    status = "OK " if got == expected else "MISS"
    if got != expected:
        parse_ok = False
    print(f"  [{status}] '{text}' → {got}  (expected {expected})")
print(f"parser: {'PASS' if parse_ok else 'FAIL'}\n")


async def main() -> int:
    print("=== storage + skill responses ===")
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_reminders_"))
    store = RemindersStore(tmp / "reminders.json")
    skill = RemindersSkill(store)

    cases = [
        "Напомни через 30 минут позвонить врачу",
        "Напомни в 15:30 что забрать посылку",
        "Напомни завтра в 9 утра встать пораньше",
        "Покажи напоминания",
        "Отмени напоминание #2",
        "Какие напоминания",
        "Напомни через 5 секунд тест",
    ]
    for c in cases:
        score = skill.match(c)
        if score == 0:
            print(f"  [no-match] '{c}'")
            continue
        res = await skill.run(c, request_id="t")
        print(f"  [match] '{c}'")
        print(f"          → {res.text}")

    # Scheduler — fire через 5 сек
    print("\n=== scheduler: ждём 5-секундный reminder ===")
    fired = []

    @bus.on(EventType.ASSISTANT_REPLY)
    async def _catch(event):
        if event.source == "reminder-scheduler":
            fired.append(event.data.get("text", ""))

    sched = ReminderScheduler(store)
    await sched.start()
    print("  scheduler стартовал, жду 18 сек (tick=10s)...")
    await asyncio.sleep(18)
    await sched.stop()

    print(f"  fired events: {len(fired)}")
    for t in fired:
        print(f"    - {t}")

    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if (parse_ok and len(fired) >= 1) else 1


sys.exit(asyncio.run(main()))

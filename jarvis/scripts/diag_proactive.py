"""Тест F3 proactive: dedup + морning brief trigger logic + quiet hours."""
from __future__ import annotations
import asyncio, sys, tempfile, shutil
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.proactive.dedup import FiredStore
from core.proactive.triggers import (
    TriggerContext, trigger_morning_brief, trigger_backup_health, MORNING_BRIEF_HOUR,
)
from core.proactive.watcher import _is_quiet_hours

print("=== quiet hours ===")
for h in [0, 4, 7, 8, 12, 18, 22, 23]:
    dt = datetime(2026, 5, 22, h, 0)
    print(f"  {h:02d}:00 → quiet={_is_quiet_hours(dt)}")

print("\n=== dedup store ===")
tmp = Path(tempfile.mkdtemp(prefix="jarvis_proactive_"))
s1 = FiredStore(tmp / "fired.jsonl")
print(f"  already_fired('x','2026-05-22') = {s1.already_fired('x','2026-05-22')}")
s1.mark_fired("x", "2026-05-22")
print(f"  after mark_fired → {s1.already_fired('x','2026-05-22')}")
# reload
s2 = FiredStore(tmp / "fired.jsonl")
print(f"  reload, fired? = {s2.already_fired('x','2026-05-22')}")
shutil.rmtree(tmp, ignore_errors=True)


async def test_morning_brief():
    print("\n=== morning_brief trigger logic (mocked, не дёргает Claude) ===")
    # Mock'аем live_morning_brief чтобы не тратить токены
    from core.proactive import triggers as T
    import core.briefings as B
    orig = B.live_morning_brief
    async def fake_brief(*a, **kw):
        return "[fake brief text]"
    B.live_morning_brief = fake_brief

    tmp = Path(tempfile.mkdtemp(prefix="jarvis_proactive_"))
    ded = FiredStore(tmp / "fired.jsonl")

    class FakeMem:
        workspace = tmp
    class FakeClaude: pass

    # Случай 1: 09:30 — должен fire (внутри окна 09:00-09:59)
    ctx = TriggerContext(now=datetime(2026,5,22,9,30), claude=FakeClaude(), memory=FakeMem(), dedup=ded)
    n = await trigger_morning_brief(ctx)
    print(f"  09:30 first call → {'FIRED' if n else 'no'}")

    # Случай 2: 09:35 — уже выстрелил сегодня → дедуп
    ctx2 = TriggerContext(now=datetime(2026,5,22,9,35), claude=FakeClaude(), memory=FakeMem(), dedup=ded)
    n2 = await trigger_morning_brief(ctx2)
    print(f"  09:35 second call (dedup) → {'FIRED' if n2 else 'no (correct)'}")

    # Случай 3: 14:00 — вне окна → не fire
    ctx3 = TriggerContext(now=datetime(2026,5,22,14,0), claude=FakeClaude(), memory=FakeMem(), dedup=ded)
    n3 = await trigger_morning_brief(ctx3)
    print(f"  14:00 outside window → {'FIRED' if n3 else 'no (correct)'}")

    # Случай 4: завтра 09:30 — новый день → должен fire
    ctx4 = TriggerContext(now=datetime(2026,5,23,9,30), claude=FakeClaude(), memory=FakeMem(), dedup=ded)
    n4 = await trigger_morning_brief(ctx4)
    print(f"  next day 09:30 → {'FIRED' if n4 else 'no'}")

    B.live_morning_brief = orig
    shutil.rmtree(tmp, ignore_errors=True)


async def test_backup_health():
    print("\n=== backup_health trigger ===")
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_proactive_bk_"))
    workspace = tmp
    log_path = workspace / "backup.log"
    # Случай: успешный backup в конце
    log_path.write_text(
        "[2026-05-22 09:00:00] [INFO] === START ===\n"
        "[2026-05-22 09:00:05] [ERROR] git push failed: foo\n"
        "[2026-05-22 09:01:00] [INFO] === START ===\n"
        "[2026-05-22 09:01:05] [OK] Public push DONE\n",
        encoding="utf-8",
    )
    class FakeMem: workspace = tmp
    class FakeClaude: pass
    ded = FiredStore(tmp / "fired.jsonl")
    ctx = TriggerContext(now=datetime(2026,5,22,12,0), claude=FakeClaude(), memory=FakeMem(), dedup=ded)
    n = await trigger_backup_health(ctx)
    print(f"  с успешным OK в конце → {'FIRED' if n else 'no (correct)'}")
    # Случай: только ERROR
    log_path.write_text(
        "[2026-05-22 09:00:00] [INFO] === START ===\n"
        "[2026-05-22 09:00:05] [ERROR] git push failed: GH013 secret scan\n",
        encoding="utf-8",
    )
    ded2 = FiredStore(tmp / "fired2.jsonl")
    ctx2 = TriggerContext(now=datetime(2026,5,22,12,0), claude=FakeClaude(), memory=FakeMem(), dedup=ded2)
    n2 = await trigger_backup_health(ctx2)
    print(f"  только ERROR → {'FIRED' if n2 else 'no'}: {n2.text[:80] if n2 else ''}")
    shutil.rmtree(tmp, ignore_errors=True)


async def main():
    await test_morning_brief()
    await test_backup_health()

asyncio.run(main())

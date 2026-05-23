"""Unit-тесты AwarenessSkill (match patterns + parse_ago + buffer CRUD).
Без реального скриншота — это медленный/непредсказуемый шаг.
"""
from __future__ import annotations
import sys, tempfile, shutil
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.awareness import ContextBuffer
from core.skills.awareness_skill import AwarenessSkill, _parse_ago

NOW = datetime(2026, 5, 22, 18, 0, 0)

print("=== parse_ago ===")
cases = [
    ("что я делал 30 минут назад", NOW - timedelta(minutes=30)),
    ("что я делал 2 часа назад", NOW - timedelta(hours=2)),
    ("чем я был занят полчаса назад", NOW - timedelta(minutes=30)),
    ("что я делал час назад", NOW - timedelta(hours=1)),
    ("какая погода", None),
]
for text, expected in cases:
    got = _parse_ago(text, now=NOW)
    flag = "OK " if got == expected else "MISS"
    print(f"  [{flag}] {text!r} → {got}")

print("\n=== buffer CRUD ===")
tmp = Path(tempfile.mkdtemp(prefix="jarvis_awareness_"))
buf = ContextBuffer(persist_path=tmp / "awareness.jsonl")
# add 3 entries with hand-crafted timestamps via direct injection
e1 = buf.add("Работаю над JARVIS в VS Code", trigger="manual")
e2 = buf.add("Открыл Telegram, переписка с Машей", trigger="manual")
e3 = buf.add("Смотрю YouTube видео про Rust", trigger="manual")
print(f"  added 3, len = {len(buf)}")
print(f"  recent(2) = {[e.description for e in buf.recent(2)]}")

# disk persistence
buf2 = ContextBuffer(persist_path=tmp / "awareness.jsonl")
print(f"  after reload, len = {len(buf2)} (expected 3)")
print(f"  recent(3) = {[e.description for e in buf2.recent(3)]}")

shutil.rmtree(tmp, ignore_errors=True)

print("\n=== match patterns ===")
class FakeClaude:
    async def chat_with_image(self, **kw): return "[fake]"
    async def chat(self, **kw): return "[fake]"
sk = AwarenessSkill(FakeClaude(), Path("."))
test_match = [
    "Запомни что я сейчас делаю",
    "Зафиксируй контекст",
    "Что я делал 20 минут назад",
    "Чем я был занят час назад",
    "Над чем я сейчас зависаю",
    "Чем я занят последнее время",
    "Какая погода",  # not match
]
for t in test_match:
    score = sk.match(t)
    print(f"  [{'match' if score > 0 else 'no-match'}] {t!r}")

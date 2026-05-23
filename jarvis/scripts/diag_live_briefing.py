"""Тест live JARVIS-стиль утреннего брифинга."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import settings
from core.providers import build_smart_provider
from core.memory import MemoryManager
from core.briefings import live_morning_brief
from core.skills.todo_skill import TodoStore
from core.skills.reminders_skill import RemindersStore


async def main() -> int:
    workspace = HERE / "workspace"
    memory = MemoryManager(workspace_dir=workspace)
    claude = build_smart_provider(settings)
    todo_store = TodoStore(workspace / "todo.json")
    reminders_store = RemindersStore(workspace / "reminders.json")

    print("=== LIVE JARVIS BRIEFING ===\n")
    text = await live_morning_brief(
        memory, claude,
        todo_store=todo_store,
        reminders_store=reminders_store,
    )
    print(text)
    print(f"\n--- {len(text)} chars ---")

    await claude.close()
    return 0


sys.exit(asyncio.run(main()))

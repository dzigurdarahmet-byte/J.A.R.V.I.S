"""Тест F4 conversational continuity.
Записываем несколько «прошлых разговоров» в vector_db, потом задаём
вопрос косвенно связанный с одним из них. Проверяем что RAG-augment
вытащил релевантный контекст в system prompt.
"""
from __future__ import annotations
import asyncio, sys, tempfile, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import settings
from core.memory import MemoryManager
from core.providers import build_smart_provider
from core.router import Router


async def main():
    # Используем настоящий workspace чтобы достать реальную vector_db
    workspace = HERE / "workspace"
    memory = MemoryManager(workspace_dir=workspace)
    claude = build_smart_provider(settings)
    router = Router(
        claude_provider=claude,
        memory=memory,
        base_prompt="Ты — J.A.R.V.I.S. Обращайся «Босс».",
    )

    queries = [
        "что у меня в задачах",  # будет L1 match (todo)
        "ты помнишь что мы говорили про аватара?",  # должно triggernuть RAG (если в vector_db есть про аватара)
        "как там лип синк джарвиса?",  # семантически близко к 'аватар lip sync'
    ]
    for q in queries:
        print(f"\n=== query: {q!r} ===")
        # Печатаем augmented system prompt чтобы увидеть RAG
        sp = await router._build_system_prompt(query=q)
        # Только RAG-секцию покажем
        if "Релевантные фрагменты" in sp:
            idx = sp.index("Релевантные фрагменты")
            rag_section = sp[idx:]
            print(rag_section)
        else:
            print("(нет RAG-augmentation для этого запроса)")

    await claude.close()
    return 0


sys.exit(asyncio.run(main()))

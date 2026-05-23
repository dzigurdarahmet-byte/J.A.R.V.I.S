"""Тест TodoSkill: TodoStore CRUD + match + run на разных фразах."""
from __future__ import annotations
import asyncio, sys, tempfile, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.skills.todo_skill import TodoSkill, TodoStore

# Изолированный workspace для теста
_tmp = Path(tempfile.mkdtemp(prefix="jarvis_todo_test_"))
store = TodoStore(_tmp / "todo.json")
skill = TodoSkill(store)


async def run(text: str) -> str:
    score = skill.match(text)
    if score == 0.0:
        return f"[no-match] '{text}'"
    res = await skill.run(text, request_id="test")
    return f"[match] '{text}' → {res.text}"


async def main() -> int:
    print(f"=== TodoSkill smoke test ===\nworkspace: {_tmp}\n")

    cases = [
        # ADD
        "Добавь задачу купить хлеб",
        "Запиши в список позвонить маме",
        "todo: подготовить отчёт по Q2",
        "Новая задача забрать посылку с почты",
        # LIST
        "Что в задачах?",
        "Покажи список",
        "Мои задачи",
        # DONE
        "Выполнил #1",
        "Сделал задачу 3",
        # LIST again — should show fewer
        "Покажи задачи",
        # REMOVE
        "Удали задачу #2",
        # LIST
        "Что в списке",
        # CLEAR
        "Очисти выполненные",
        # LIST — should show only active
        "Что в задачах",
        # negative
        "Какая погода в Москве",
    ]

    for c in cases:
        print(await run(c))
        print()

    # Финальный state
    print("=== final storage state ===")
    final = store.list_all()
    for it in final:
        mark = "✓" if it["done"] else " "
        print(f"  [{mark}] #{it['id']}: {it['text']}")

    shutil.rmtree(_tmp, ignore_errors=True)
    return 0


sys.exit(asyncio.run(main()))

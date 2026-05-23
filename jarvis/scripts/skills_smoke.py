"""Smoke-test для 20 builtin-скиллов: проверяем match() routing.

Реальные API не дёргаем — только classification по тексту.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# На Python < 3.11 (sandbox 3.10) — shim StrEnum
import enum as _enum  # noqa: E402

if not hasattr(_enum, "StrEnum"):
    class _StrEnum(str, _enum.Enum):
        pass

    _enum.StrEnum = _StrEnum  # type: ignore[attr-defined]

from core.memory import MemoryManager  # noqa: E402
from core.router import Router  # noqa: E402
from core.skills import register_all_builtin  # noqa: E402

WORKSPACE = ROOT / "workspace"

CASES = [
    ("Который час?", "time"),
    ("Сколько сейчас время?", "time"),
    ("Сколько времени?", "time"),
    ("сколькро время", "time"),       # STT/typo
    ("скока время", "time"),           # разговорный
    ("Какая сегодня дата?", "date"),
    ("Какой сегодня день недели?", "date"),
    ("Сколько времени в Нью-Йорке?", "timezone"),
    ("Который час в Токио?", "timezone"),
    ("Поставь таймер на 5 минут", "timer"),
    ("Поставь будильник на 7:30", "alarm"),
    ("Какая сегодня погода?", "weather"),
    ("Прогноз погоды на завтра", "weather_forecast"),
    ("Погода в Москве на выходные", "weather_forecast"),
    ("Курс доллара?", "currency"),
    ("Сколько стоит биткоин?", "crypto"),
    ("Запиши: купить молоко", "note"),
    ("Запомни: пароль от роутера 12345", "remember"),
    ("Покажи мои заметки", "notes_list"),
    ("Расскажи про Эйнштейна", "wiki"),
    ("Что такое квантовая запутанность?", "wiki"),
    ("Сколько будет 17 плюс 25?", "calc"),
    ("100 км в мили", "convert"),
    ("50 кг в фунты", "convert"),
    ("Подбрось монетку", "random"),
    ("Переведи на английский: добрый день", "translate"),
    ("Статус", "status"),
    ("Расскажи анекдот", None),
    ("Как тебя зовут?", None),
]


def main() -> int:
    class _FakeClaude:
        pass

    memory = MemoryManager(workspace_dir=WORKSPACE)
    router = Router(
        claude_provider=_FakeClaude(),  # type: ignore[arg-type]
        memory=memory,
        base_prompt="test",
    )
    register_all_builtin(router, memory)

    print("Registered skills:", len(router._skills))  # type: ignore[attr-defined]
    print()

    ok = 0
    fail = 0
    for text, expected in CASES:
        best, score = router._best_skill(text)  # type: ignore[attr-defined]
        if best is not None and score < 0.5:
            best = None
        actual = best.name if best else None
        mark = "OK  " if actual == expected else "FAIL"
        if actual == expected:
            ok += 1
        else:
            fail += 1
        print(f"[{mark}] {text!r:55s} -> {actual}  (exp {expected})")

    print()
    print(f"Matched: {ok}/{ok + fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

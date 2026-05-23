"""Smoke-тест LLMSwitcherSkill — проверяет regex-match и логику записи.

Запуск: python -m scripts.llm_switcher_smoke (из jarvis/).
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Чтобы импорты типа core.* работали:
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.skills.llm_switcher_skill import LLMSwitcherSkill, _detect_choice


class FakeSmart:
    """Минимальный SmartProvider-stub без сети — пишет/читает выбор в файл."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached = "auto"

    def get_choice(self) -> str:
        if self._path.exists():
            v = self._path.read_text(encoding="utf-8").strip().lower()
            if v:
                return v
        return "auto"

    def set_choice(self, choice: str) -> bool:
        if choice not in {"auto", "claude", "deepseek", "yandex", "ollama"}:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(choice, encoding="utf-8")
        return True


PHRASES = [
    # (text, expected_choice_or_None, expect_match)
    ("пользуйся клодом", "claude", True),
    ("Пользуйся Клодом, Джарвис", "claude", True),
    ("используй deepseek", "deepseek", True),
    ("Джарвис, пользуйся дипсиком", "deepseek", True),
    ("используй яндекс", "yandex", True),
    ("перейди на ollama", "ollama", True),
    ("переключись на оламу", "ollama", True),
    ("вернись на авто", "auto", True),
    ("какая сейчас llm?", None, True),         # status query
    ("какая модель сейчас работает", None, True),
    ("через что ты сейчас работаешь", None, True),
    # Не должно срабатывать:
    ("клод сказал, что погода нормальная", None, False),
    ("дипсик — это китайская модель", None, False),
]


async def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        choice_file = Path(td) / "llm_choice.txt"
        smart = FakeSmart(choice_file)
        skill = LLMSwitcherSkill(smart)

        failed = 0
        for text, expected, expect_match in PHRASES:
            m = skill.match(text)
            ok_match = (m >= 1.0) == expect_match
            ok_detect = True
            if expect_match and expected is not None:
                detected = _detect_choice(text)
                ok_detect = (detected == expected)
            status = "OK" if (ok_match and ok_detect) else "FAIL"
            if not (ok_match and ok_detect):
                failed += 1
            print(f"{status:5s} match={m:.1f} expected={expected!r:10s}  text={text!r}")

        # Сценарии записи:
        print("\n--- запись через run() ---")
        for phrase, want_choice in [
            ("пользуйся клодом", "claude"),
            ("используй дипсик", "deepseek"),
            ("вернись на авто", "auto"),
        ]:
            res = await skill.run(phrase, request_id="test")
            cur = smart.get_choice()
            ok = cur == want_choice
            mark = "OK" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"{mark:5s} after {phrase!r}: file={cur!r} expected={want_choice!r}  reply={res.text}")

        # Tool-use:
        print("\n--- tool-use (run_with_args) ---")
        for choice in ("claude", "deepseek", "yandex", "ollama", "auto"):
            res = await skill.run_with_args({"choice": choice}, request_id="t")
            cur = smart.get_choice()
            ok = cur == choice
            mark = "OK" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"{mark:5s} choice={choice}  file={cur}  reply={res.text}")

        print(f"\nFAILED: {failed}")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

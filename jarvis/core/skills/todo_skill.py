"""Skill: личный список задач (todo).

Persistence: workspace/todo.json. Простая структура, без БД.

Поддерживаемые фразы:
  add:     "добавь задачу X", "запиши в список X", "новая задача X", "todo: X"
  list:    "что в задачах", "покажи список", "мои задачи", "что у меня в todo"
  done:    "выполнил #3", "сделал задачу 3", "задача 3 готова", "отметь 3"
  remove:  "удали задачу 3", "забудь задачу #3"
  clear:   "очисти выполненные", "удали сделанные"

Хранение — JSON в workspace/todo.json:
  {"next_id": 5, "items": [{"id":..., "text":..., "created_at":..., "done":..., "done_at":...}]}
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

_LOCK = Lock()  # один файл — одно атомарное обновление


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TodoStore:
    """Минимальный JSON-storage для задач. Атомарная запись через tmp+rename."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"next_id": 1, "items": []}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("next_id", 1)
            data.setdefault("items", [])
            return data
        except Exception as e:
            logger.error("todo_load_failed", error=str(e))
            return {"next_id": 1, "items": []}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    # ── high-level operations (lock-protected) ──────────────────────
    def add(self, text: str) -> dict[str, Any]:
        with _LOCK:
            data = self.load()
            item = {
                "id": data["next_id"],
                "text": text,
                "created_at": _now_iso(),
                "done": False,
                "done_at": None,
            }
            data["next_id"] += 1
            data["items"].append(item)
            self.save(data)
            return item

    def list_active(self) -> list[dict[str, Any]]:
        return [i for i in self.load().get("items", []) if not i.get("done")]

    def list_all(self) -> list[dict[str, Any]]:
        return self.load().get("items", [])

    def mark_done(self, item_id: int) -> dict[str, Any] | None:
        with _LOCK:
            data = self.load()
            for it in data["items"]:
                if it["id"] == item_id and not it["done"]:
                    it["done"] = True
                    it["done_at"] = _now_iso()
                    self.save(data)
                    return it
            return None

    def remove(self, item_id: int) -> dict[str, Any] | None:
        with _LOCK:
            data = self.load()
            kept, removed = [], None
            for it in data["items"]:
                if it["id"] == item_id and removed is None:
                    removed = it
                else:
                    kept.append(it)
            if removed is not None:
                data["items"] = kept
                self.save(data)
            return removed

    def clear_done(self) -> int:
        with _LOCK:
            data = self.load()
            before = len(data["items"])
            data["items"] = [i for i in data["items"] if not i.get("done")]
            after = len(data["items"])
            self.save(data)
            return before - after


# ─── regex'ы для intents ────────────────────────────────────────────
_ADD_PATTERNS = [
    re.compile(r"\b(?:добавь|запиши|новая)\s+задач[ау]?[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:добавь|запиши|сохрани)\s+в\s+(?:задачи|список|todo|туду)[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*todo[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
]
_LIST_PATTERNS = [
    re.compile(r"\b(?:что|какие)\s+(?:у\s+меня\s+)?(?:в\s+)?(?:задач\w*|todo|туду|списке)\b", re.IGNORECASE),
    re.compile(r"\bпокажи\s+(?:мои\s+)?(?:задачи|список|todo|туду)\b", re.IGNORECASE),
    re.compile(r"\b(?:мои\s+)?(?:задачи|todo|туду)\b\s*\??$", re.IGNORECASE),
    re.compile(r"\bсписок\s+задач\b", re.IGNORECASE),
]
_DONE_PATTERNS = [
    re.compile(r"\b(?:выполнил|сделал|готов[оа]?|закрыл|done)\b\s*(?:задач[уа]?\s*)?#?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bотметь\s+#?(\d+)(?:\s+как\s+(?:сделано|готово|выполнено))?\b", re.IGNORECASE),
    re.compile(r"\bзадач[ау]?\s+(\d+)\s+(?:готова|сделана|выполнена)\b", re.IGNORECASE),
]
_REMOVE_PATTERNS = [
    re.compile(r"\b(?:удали|забудь|выкинь|delete)\s+задач[уа]?\s*#?\s*(\d+)\b", re.IGNORECASE),
]
_CLEAR_PATTERNS = [
    re.compile(r"\bочисти\s+(?:выполненные|сделанные|готовые)\b", re.IGNORECASE),
    re.compile(r"\bудали\s+(?:все\s+)?(?:выполненные|сделанные|готовые)\b", re.IGNORECASE),
]


class TodoSkill(KeywordSkill):
    """Единый skill для todo: add / list / done / remove / clear_done."""

    name = "todo"
    keywords = [
        # add
        r"\b(?:добавь|запиши|сохрани|новая)\s+(?:задач[ау]?|в\s+(?:задачи|список|todo|туду))",
        r"^\s*todo[:\s]",
        # list
        r"\b(?:что|какие)\s+(?:у\s+меня\s+)?(?:в\s+)?(?:задач|todo|туду|списке)",
        r"\bпокажи\s+(?:мои\s+)?(?:задачи|список|todo|туду)",
        r"\bсписок\s+задач\b",
        r"\bмои\s+(?:задачи|todo|туду)\b",
        # done
        r"\b(?:выполнил|сделал|готов[оа]?|закрыл|done)\b\s*(?:задач[уа]?\s*)?#?\s*\d+",
        r"\bотметь\s+#?\d+",
        r"\bзадач[ау]?\s+\d+\s+(?:готова|сделана|выполнена)",
        # remove
        r"\b(?:удали|забудь|выкинь|delete)\s+задач[уа]?\s*#?\s*\d+",
        # clear
        r"\bочисти\s+(?:выполненные|сделанные|готовые)",
        r"\bудали\s+(?:все\s+)?(?:выполненные|сделанные|готовые)",
    ]

    def __init__(self, store: TodoStore) -> None:
        super().__init__()
        self._store = store

    @staticmethod
    def _plural(n: int) -> str:
        """Русская морфология для 'задач'."""
        if 11 <= (n % 100) <= 14:
            return "задач"
        last = n % 10
        if last == 1:
            return "задача"
        if 2 <= last <= 4:
            return "задачи"
        return "задач"

    async def run(self, text: str, request_id: str) -> SkillResult:
        # ── ADD ─────────────────────────────────────────────────────
        for pat in _ADD_PATTERNS:
            m = pat.search(text)
            if m:
                item_text = m.group(1).strip().rstrip(".!?")
                if not item_text:
                    return SkillResult(text="Босс, что добавить в список?", speakable=True)
                item = self._store.add(item_text)
                return SkillResult(
                    text=f"Добавил #{item['id']}: {item['text']}.",
                    speakable=True,
                )

        # ── DONE ────────────────────────────────────────────────────
        for pat in _DONE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    idn = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                it = self._store.mark_done(idn)
                if it is None:
                    return SkillResult(
                        text=f"Босс, задачи #{idn} в активных не нашёл.",
                        speakable=True,
                    )
                return SkillResult(text=f"Отметил #{idn}: {it['text']}.", speakable=True)

        # ── REMOVE ──────────────────────────────────────────────────
        for pat in _REMOVE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    idn = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                it = self._store.remove(idn)
                if it is None:
                    return SkillResult(text=f"Босс, задачи #{idn} не нашёл.", speakable=True)
                return SkillResult(text=f"Удалил #{idn}: {it['text']}.", speakable=True)

        # ── CLEAR DONE ──────────────────────────────────────────────
        for pat in _CLEAR_PATTERNS:
            if pat.search(text):
                n = self._store.clear_done()
                if n == 0:
                    return SkillResult(text="Очищать нечего.", speakable=True)
                # 1 → "задачу" (винительный), 2-4 → "задачи", 5+ → "задач"
                last = n % 10
                if 11 <= (n % 100) <= 14:
                    form = "задач"
                elif last == 1:
                    form = "задачу"
                elif 2 <= last <= 4:
                    form = "задачи"
                else:
                    form = "задач"
                return SkillResult(text=f"Очистил {n} {form}.", speakable=True)

        # ── LIST ────────────────────────────────────────────────────
        # list по дефолту — если ничего другого не сматчилось
        for pat in _LIST_PATTERNS:
            if pat.search(text):
                items = self._store.list_active()
                if not items:
                    return SkillResult(text="Список пуст, Босс.", speakable=True)
                # Формат: "1. купить хлеб\n2. позвонить маме"
                lines = [f"{it['id']}. {it['text']}" for it in items]
                joined = "\n".join(lines)
                summary = f"В списке {len(items)} {self._plural(len(items))}:\n{joined}"
                return SkillResult(text=summary, speakable=True)

        # ничего не сматчилось из intent-patterns, но keyword сработал —
        # значит вероятно "todo" или похожее упоминание без операции
        return SkillResult(
            text="Босс, не понял что с задачами делать. Скажи 'покажи задачи', 'добавь задачу X' или 'выполнил #N'.",
            speakable=True,
        )

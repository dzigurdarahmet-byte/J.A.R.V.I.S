"""Skill: долгие измеримые цели с прогрессом.

Storage: workspace/goals.json.

Intents:
  add       — «новая цель: написать 5 статей до конца месяца»
              «цель: прочитать 12 книг за год»
              «поставь цель: 30 тренировок до конца лета»
  progress  — «+1 статья» / «прочёл 2 книги» / «отметь 3 тренировки»
              «выполнил тренировку» (delta=1)
  list      — «мои цели» / «какие у меня цели» / «прогресс по целям»
  status    — «как там X» / «прогресс по статьям»
  complete  — «цель X завершена» / «закрой цель N»
  remove    — «удали цель N»

Цель структура:
  {id, name, target, current, unit, deadline_iso?, created_iso,
   history:[{at_iso, delta, note}], completed_at?}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from threading import Lock
from typing import Any

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

_LOCK = Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── Parsing helpers ────────────────────────────────────────────────
# «5 статей» / «12 книг» / «30 тренировок» / «1.5 кг» / «10 часов»
_QTY_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s+([А-Яа-яA-Za-z]+)",
    re.UNICODE,
)
# дедлайн: «до конца месяца» / «до конца лета» / «за месяц» / «за год» /
# «до DD.MM» / «до DD.MM.YYYY» / «к понедельнику» (sub-set)
_DEADLINE_PATTERNS = [
    (re.compile(r"\bдо\s+конца\s+месяца\b", re.IGNORECASE), "end_of_month"),
    (re.compile(r"\bдо\s+конца\s+недели\b", re.IGNORECASE), "end_of_week"),
    (re.compile(r"\bдо\s+конца\s+года\b", re.IGNORECASE), "end_of_year"),
    (re.compile(r"\bдо\s+конца\s+лета\b", re.IGNORECASE), "end_of_summer"),
    (re.compile(r"\bза\s+месяц\b", re.IGNORECASE), "plus_month"),
    (re.compile(r"\bза\s+неделю\b", re.IGNORECASE), "plus_week"),
    (re.compile(r"\bза\s+год\b", re.IGNORECASE), "plus_year"),
    (re.compile(r"\bдо\s+(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b"), "date_dmy"),
]


def _resolve_deadline(text: str, now: datetime | None = None) -> str | None:
    """Извлечь deadline_iso (YYYY-MM-DD) из текста цели."""
    now = now or datetime.now()
    for pat, kind in _DEADLINE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if kind == "end_of_month":
            # last day of month
            if now.month == 12:
                last = date(now.year, 12, 31)
            else:
                last = date(now.year, now.month + 1, 1) - timedelta(days=1)
            return last.isoformat()
        if kind == "end_of_week":
            # nearest Sunday
            days_ahead = (6 - now.weekday()) % 7 or 7
            return (now + timedelta(days=days_ahead)).date().isoformat()
        if kind == "end_of_year":
            return date(now.year, 12, 31).isoformat()
        if kind == "end_of_summer":
            return date(now.year, 8, 31).isoformat()
        if kind == "plus_month":
            return (now + timedelta(days=30)).date().isoformat()
        if kind == "plus_week":
            return (now + timedelta(days=7)).date().isoformat()
        if kind == "plus_year":
            return (now + timedelta(days=365)).date().isoformat()
        if kind == "date_dmy":
            d, mo = int(m.group(1)), int(m.group(2))
            yr = int(m.group(3)) if m.group(3) else now.year
            try:
                return date(yr, mo, d).isoformat()
            except ValueError:
                continue
    return None


def _normalize_unit(unit: str) -> str:
    """Приведение слова единицы к канонической форме (для match при progress).

    «статью / статьи / статей» → «стат»
    «книгу / книг / книги» → «книг»
    """
    low = unit.lower().strip()
    # просто берём корень — первые 4-5 символов. Хватит для match.
    return low[:4] if len(low) > 4 else low


# ─── Storage ────────────────────────────────────────────────────────
class GoalsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"next_id": 1, "items": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("goals_load_failed", error=str(e))
            return {"next_id": 1, "items": []}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, name: str, target: float, unit: str, deadline_iso: str | None) -> dict:
        with _LOCK:
            data = self.load()
            item = {
                "id": data["next_id"],
                "name": name,
                "target": target,
                "current": 0,
                "unit": unit,
                "unit_key": _normalize_unit(unit),
                "deadline_iso": deadline_iso,
                "created_iso": _now_iso(),
                "history": [],
                "completed_at": None,
            }
            data["next_id"] += 1
            data["items"].append(item)
            self.save(data)
            return item

    def list_active(self) -> list[dict]:
        return [g for g in self.load().get("items", []) if not g.get("completed_at")]

    def find_by_unit(self, unit_query: str) -> dict | None:
        key = _normalize_unit(unit_query)
        for g in self.list_active():
            if g.get("unit_key") == key:
                return g
        return None

    def find_by_id(self, gid: int) -> dict | None:
        for g in self.load().get("items", []):
            if g["id"] == gid:
                return g
        return None

    def add_progress(self, goal_id: int, delta: float, note: str = "") -> dict | None:
        with _LOCK:
            data = self.load()
            for g in data["items"]:
                if g["id"] == goal_id and not g.get("completed_at"):
                    g["current"] = round(g.get("current", 0) + delta, 2)
                    g["history"].append({
                        "at_iso": _now_iso(),
                        "delta": delta,
                        "note": note,
                    })
                    # Авто-completion если достигли target
                    if g["current"] >= g["target"]:
                        g["completed_at"] = _now_iso()
                    self.save(data)
                    return g
            return None

    def complete(self, goal_id: int) -> dict | None:
        with _LOCK:
            data = self.load()
            for g in data["items"]:
                if g["id"] == goal_id and not g.get("completed_at"):
                    g["completed_at"] = _now_iso()
                    self.save(data)
                    return g
            return None

    def remove(self, goal_id: int) -> dict | None:
        with _LOCK:
            data = self.load()
            kept, removed = [], None
            for g in data["items"]:
                if g["id"] == goal_id and removed is None:
                    removed = g
                else:
                    kept.append(g)
            if removed is not None:
                data["items"] = kept
                self.save(data)
            return removed


# ─── Progress-форматирование ────────────────────────────────────────
def _format_pace(g: dict, now: datetime | None = None) -> str:
    """Сколько осталось vs дни до deadline. Темп: 'идёшь по графику' / 'опережаешь' / 'отстаёшь'."""
    now = now or datetime.now()
    if not g.get("deadline_iso"):
        return ""
    try:
        deadline = datetime.fromisoformat(g["deadline_iso"]).date()
    except ValueError:
        return ""
    created = datetime.fromisoformat(g["created_iso"]).date()
    today = now.date()
    total_days = max(1, (deadline - created).days)
    days_passed = max(0, (today - created).days)
    days_left = (deadline - today).days
    if days_left < 0:
        return " (дедлайн прошёл)"
    expected = g["target"] * (days_passed / total_days)
    actual = g["current"]
    diff = actual - expected
    if abs(diff) < 0.5:
        pace = "идёшь по графику"
    elif diff > 0:
        pace = f"опережаешь на {round(diff, 1)}"
    else:
        pace = f"отстаёшь на {round(-diff, 1)}"
    return f" ({pace}, {days_left} дн до дедлайна)"


def _format_goal(g: dict) -> str:
    bar_len = 12
    pct = min(1.0, g["current"] / max(g["target"], 1))
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    completed_mark = " ✓" if g.get("completed_at") else ""
    return (
        f"#{g['id']}{completed_mark} {g['name']}: "
        f"{g['current']:g}/{g['target']:g} {g['unit']} "
        f"[{bar}]"
        + _format_pace(g)
    )


# ─── Skill ──────────────────────────────────────────────────────────
_ADD_PATTERNS = [
    re.compile(r"\b(?:новая\s+цель|поставь\s+цель|цель)[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
]
_PROGRESS_PATTERNS = [
    # «+1 статья», «+2 тренировки»
    re.compile(r"^\s*\+\s*(\d+(?:[.,]\d+)?)\s+([А-Яа-яA-Za-z]+)\b", re.IGNORECASE),
    # «отметь 1 статью», «отметь 2 тренировки»
    re.compile(r"\bотметь\s+(\d+(?:[.,]\d+)?)\s+([А-Яа-яA-Za-z]+)\b", re.IGNORECASE),
    # «прочёл 2 книги», «выпил 2 литра», «написал 1 статью», «сделал 30 отжиманий»
    re.compile(r"\b(?:прочёл|прочитал|выпил|написал|сделал|выполнил)\s+(\d+(?:[.,]\d+)?)\s+([А-Яа-яA-Za-z]+)\b", re.IGNORECASE),
]
_PROGRESS_SINGLE = [
    # «прочёл книгу» / «выпил воды» / «сделал тренировку» (delta=1)
    re.compile(r"\b(?:прочёл|прочитал|выпил|написал|сделал|выполнил)\s+(?:одну\s+|один\s+)?([А-Яа-яA-Za-z]+)\b", re.IGNORECASE),
]
_LIST_PATTERNS = [
    re.compile(r"\b(?:мои\s+цели|какие\s+у\s+меня\s+цели|прогресс\s+по\s+целям|покажи\s+цели)\b", re.IGNORECASE),
    re.compile(r"^\s*цели\s*\??$", re.IGNORECASE),
]
_COMPLETE_PATTERNS = [
    re.compile(r"\b(?:цель|задач[ау])\s*#?\s*(\d+)\s+(?:выполнена|готова|закрыта|завершена)\b", re.IGNORECASE),
    re.compile(r"\b(?:закрой|заверши)\s+цель\s*#?\s*(\d+)\b", re.IGNORECASE),
]
_REMOVE_PATTERNS = [
    re.compile(r"\bудали\s+цель\s*#?\s*(\d+)\b", re.IGNORECASE),
]


class GoalsSkill(KeywordSkill):
    name = "goals"
    keywords = [
        r"\b(?:новая\s+цель|поставь\s+цель|цель)[:\s]",
        r"^\s*\+\s*\d+\s+[А-Яа-яA-Za-z]+",
        r"\bотметь\s+\d+\s+[А-Яа-яA-Za-z]+",
        r"\b(?:прочёл|прочитал|выпил|написал|сделал|выполнил)\s+(?:одну\s+|один\s+|\d+\s+)",
        r"\b(?:мои\s+цели|какие\s+у\s+меня\s+цели|прогресс\s+по\s+целям|покажи\s+цели)\b",
        r"^\s*цели\s*\??$",
        r"\b(?:цель|задач[ау])\s*#?\s*\d+\s+(?:выполнена|готова|закрыта|завершена)\b",
        r"\b(?:закрой|заверши)\s+цель\s*#?\s*\d+\b",
        r"\bудали\s+цель\s*#?\s*\d+\b",
    ]

    def __init__(self, store: GoalsStore) -> None:
        super().__init__()
        self._store = store

    async def run(self, text: str, request_id: str) -> SkillResult:
        # COMPLETE / REMOVE — самые специфичные первыми
        for pat in _COMPLETE_PATTERNS:
            m = pat.search(text)
            if m:
                gid = int(m.group(1))
                g = self._store.complete(gid)
                if not g:
                    return SkillResult(text=f"Цель #{gid} не нашёл или уже закрыта.", speakable=True)
                return SkillResult(text=f"Цель #{gid} «{g['name']}» закрыта.", speakable=True)
        for pat in _REMOVE_PATTERNS:
            m = pat.search(text)
            if m:
                gid = int(m.group(1))
                g = self._store.remove(gid)
                if not g:
                    return SkillResult(text=f"Цель #{gid} не нашёл.", speakable=True)
                return SkillResult(text=f"Удалил цель #{gid}: {g['name']}.", speakable=True)

        # ADD — «цель: ...»
        for pat in _ADD_PATTERNS:
            m = pat.search(text)
            if m:
                return self._add(m.group(1))

        # PROGRESS — с числом
        for pat in _PROGRESS_PATTERNS:
            m = pat.search(text)
            if m:
                qty_str, unit = m.group(1), m.group(2)
                qty = float(qty_str.replace(",", "."))
                return self._progress(qty, unit, text)

        # PROGRESS — единичный (delta=1)
        for pat in _PROGRESS_SINGLE:
            m = pat.search(text)
            if m:
                unit = m.group(1)
                return self._progress(1.0, unit, text)

        # LIST
        for pat in _LIST_PATTERNS:
            if pat.search(text):
                return self._list()

        return SkillResult(text="Не понял команду по целям.", speakable=True)

    def _add(self, body: str) -> SkillResult:
        body = body.strip().rstrip(".!?")
        m = _QTY_UNIT.search(body)
        if not m:
            return SkillResult(
                text="Босс, укажи количество и единицу. Например: 'цель: написать 5 статей до конца месяца'.",
                speakable=True,
            )
        target = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        deadline = _resolve_deadline(body)
        # Имя цели — text вокруг числа, очищенный
        name = body.replace(m.group(0), "").strip()
        name = re.sub(r"\bдо\s+конца\s+(?:месяца|недели|года|лета)\b", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\bза\s+(?:месяц|неделю|год)\b", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\bдо\s+\d{1,2}\.\d{1,2}(?:\.\d{4})?\b", "", name)
        name = re.sub(r"\s+", " ", name).strip(" :,")
        if not name:
            name = f"{m.group(1)} {unit}"
        g = self._store.add(name, target, unit, deadline)
        suffix = f" к {deadline}" if deadline else ""
        return SkillResult(
            text=f"Цель #{g['id']}: «{name}» — {target:g} {unit}{suffix}. Начинаем считать.",
            speakable=True,
        )

    def _progress(self, qty: float, unit: str, raw_text: str) -> SkillResult:
        g = self._store.find_by_unit(unit)
        if g is None:
            return SkillResult(
                text=f"Не нашёл активной цели с единицей «{unit}». "
                     "Заведи через 'цель: ...'.",
                speakable=True,
            )
        # extract note — то что после ":" если есть
        note = ""
        m = re.search(r":\s*(.+)$", raw_text)
        if m:
            note = m.group(1).strip()
        updated = self._store.add_progress(g["id"], qty, note)
        if updated is None:
            return SkillResult(text="Не получилось обновить.", speakable=True)
        if updated.get("completed_at"):
            return SkillResult(
                text=f"+{qty:g} {unit}. Цель #{g['id']} «{g['name']}» достигнута! "
                     f"({updated['current']:g}/{g['target']:g})",
                speakable=True,
            )
        return SkillResult(text=_format_goal(updated), speakable=True)

    def _list(self) -> SkillResult:
        goals = self._store.list_active()
        if not goals:
            return SkillResult(text="Активных целей нет, Босс.", speakable=True)
        lines = [_format_goal(g) for g in goals]
        return SkillResult(text="\n".join(lines), speakable=True)

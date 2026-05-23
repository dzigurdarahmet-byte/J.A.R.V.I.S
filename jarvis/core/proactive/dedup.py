"""Дедупликация сработавших триггеров.

JSONL append-only: {trigger_id, key, fired_at_iso}. trigger_id — имя триггера
(например 'morning_brief'), key — уникальный токен повторяемости (например
'2026-05-22' для daily-триггеров; 'evt-id-xxx' для конкретного события).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from core.logging import get_logger

logger = get_logger(__name__)
_LOCK = Lock()
KEEP_HOURS = 72  # держим записи за 3 суток, старее — чистим


class FiredStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fired: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        cutoff = datetime.now() - timedelta(hours=KEEP_HOURS)
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    fired_at = datetime.fromisoformat(d.get("fired_at_iso", ""))
                    if fired_at < cutoff:
                        continue
                    self._fired.add(f"{d['trigger_id']}::{d['key']}")
                except Exception:
                    continue
        except Exception as e:
            logger.warning("dedup_load_failed", error=str(e))

    def already_fired(self, trigger_id: str, key: str) -> bool:
        return f"{trigger_id}::{key}" in self._fired

    def mark_fired(self, trigger_id: str, key: str) -> None:
        full = f"{trigger_id}::{key}"
        with _LOCK:
            if full in self._fired:
                return
            self._fired.add(full)
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "trigger_id": trigger_id,
                        "key": key,
                        "fired_at_iso": datetime.now().isoformat(timespec="seconds"),
                    }) + "\n")
            except Exception as e:
                logger.warning("dedup_persist_failed", error=str(e))

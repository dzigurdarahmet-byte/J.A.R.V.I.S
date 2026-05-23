"""Rolling-буфер контекстов «чем Босс был занят».

Хранится:
  - в-памяти (deque, до 100 точек) — для быстрой агрегации
  - на диске в workspace/awareness.jsonl — append-only лог (для recall
    после restart HUD'а и для weekly-аналитики).

Не сохраняет сами скриншоты — только описания. Privacy by design.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Iterable

from core.logging import get_logger

logger = get_logger(__name__)

BUFFER_CAPACITY = 100
_LOCK = Lock()


@dataclass(slots=True)
class ContextEntry:
    at_iso: str
    description: str
    trigger: str = "manual"  # 'manual' / 'auto' / 'agentic' — кто инициировал

    def to_dict(self) -> dict:
        return asdict(self)


class ContextBuffer:
    """Append-only buffer контекстов. Persistent через JSONL на диске."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self._entries: deque[ContextEntry] = deque(maxlen=BUFFER_CAPACITY)
        self._persist = persist_path
        if persist_path is not None:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        if self._persist is None or not self._persist.exists():
            return
        try:
            lines = self._persist.read_text(encoding="utf-8").splitlines()
            # Берём последние BUFFER_CAPACITY из файла
            for line in lines[-BUFFER_CAPACITY:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    self._entries.append(ContextEntry(**d))
                except Exception:
                    continue
        except Exception as e:
            logger.warning("awareness_load_failed", error=str(e))

    def add(self, description: str, trigger: str = "manual") -> ContextEntry:
        entry = ContextEntry(
            at_iso=datetime.now().isoformat(timespec="seconds"),
            description=description.strip(),
            trigger=trigger,
        )
        with _LOCK:
            self._entries.append(entry)
            if self._persist is not None:
                try:
                    with self._persist.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
                except Exception as e:
                    logger.warning("awareness_persist_failed", error=str(e))
        return entry

    def recent(self, limit: int = 10) -> list[ContextEntry]:
        """Последние N entries (новые в конце)."""
        return list(self._entries)[-limit:]

    def near_time(self, target: datetime, tolerance: timedelta = timedelta(minutes=15)) -> ContextEntry | None:
        """Найти ближайший entry к target time. None если в пределах tolerance нет."""
        best: ContextEntry | None = None
        best_delta: timedelta | None = None
        for e in self._entries:
            try:
                t = datetime.fromisoformat(e.at_iso)
            except ValueError:
                continue
            d = abs(t - target)
            if d <= tolerance and (best_delta is None or d < best_delta):
                best = e
                best_delta = d
        return best

    def since(self, since: datetime) -> list[ContextEntry]:
        """Все entries не раньше since."""
        out = []
        for e in self._entries:
            try:
                t = datetime.fromisoformat(e.at_iso)
            except ValueError:
                continue
            if t >= since:
                out.append(e)
        return out

    def __len__(self) -> int:
        return len(self._entries)


# Singleton (initialized on first use)
_BUFFER: ContextBuffer | None = None


def get_buffer(workspace_dir: Path | None = None) -> ContextBuffer:
    """Singleton-аксессор. При первом вызове — инициализирует с persistence."""
    global _BUFFER
    if _BUFFER is None:
        persist = workspace_dir / "awareness.jsonl" if workspace_dir else None
        _BUFFER = ContextBuffer(persist_path=persist)
    return _BUFFER

"""SQLite-based MetricsCollector с auto-subscription на event_bus.

Метрики:
  * stt / llm / tts / e2e — latency events (duration_ms заполнен)
  * barge_in / speaker_reject / wake / fallback / error — counter events
  * skill — кто из skill-ов сработал (provider=skill_name)

Storage: workspace/metrics.db (SQLite).
Schema:
    metric_events(id, ts, event_type, channel, duration_ms, success, provider, meta_json)

Концепция timing:
  * USER_INPUT с request_id   → запоминаем started
  * ASSISTANT_REPLY с тем же  → пишем e2e latency = now - started
  * Для STT/LLM/TTS — explicit `metrics.timed(...)` context manager.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.event_bus import EventType, JarvisEvent
from core.logging import get_logger

logger = get_logger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    channel TEXT,
    duration_ms INTEGER,
    success INTEGER NOT NULL DEFAULT 1,
    provider TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_metric_ts ON metric_events(ts);
CREATE INDEX IF NOT EXISTS idx_metric_type_ts ON metric_events(event_type, ts);
"""


class MetricsCollector:
    """Singleton — пишет события в SQLite + поддерживает in-memory request_id tracking."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "workspace" / "metrics.db"
        self._db_path = db_path
        self._lock = threading.Lock()
        # request_id -> ts когда был USER_INPUT (для e2e timing)
        self._inflight: dict[str, float] = {}
        self._inflight_lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._enabled = True
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False — мы защищаем своим _lock
            self._conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False, timeout=5.0
            )
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            logger.info("metrics_db_ready", path=str(self._db_path))
        except Exception as e:
            logger.error("metrics_db_init_failed", error=str(e))
            self._enabled = False
            self._conn = None

    def attach_bus(self, bus) -> None:  # noqa: ANN001
        """Подписать collector на нужные типы событий шины."""

        async def _on_user_input(ev: JarvisEvent) -> None:
            with self._inflight_lock:
                self._inflight[ev.request_id] = time.time()
            self.record("user_input", channel=ev.channel)

        async def _on_assistant_reply(ev: JarvisEvent) -> None:
            with self._inflight_lock:
                started = self._inflight.pop(ev.request_id, None)
            duration_ms = int((time.time() - started) * 1000) if started else None
            self.record(
                "e2e",
                channel=ev.channel,
                duration_ms=duration_ms,
                meta={"request_id": ev.request_id},
            )

        async def _on_routed(ev: JarvisEvent) -> None:
            skill = ev.data.get("skill") or ev.data.get("skill_name")
            if skill:
                self.record("skill", channel=ev.channel, provider=str(skill))

        bus.subscribe(EventType.USER_INPUT, _on_user_input)
        bus.subscribe(EventType.ASSISTANT_REPLY, _on_assistant_reply)
        bus.subscribe(EventType.ROUTED, _on_routed)
        logger.info("metrics_attached_to_bus")

    def record(
        self,
        event_type: str,
        *,
        channel: str | None = None,
        duration_ms: int | None = None,
        success: bool = True,
        provider: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Записать одно событие. Безопасно к ошибкам — не валит вызывающий код."""
        if not self._enabled or self._conn is None:
            return
        try:
            meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
            with self._lock:
                self._conn.execute(
                    "INSERT INTO metric_events"
                    " (ts, event_type, channel, duration_ms, success, provider, meta_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(),
                        event_type,
                        channel,
                        duration_ms,
                        1 if success else 0,
                        provider,
                        meta_json,
                    ),
                )
                self._conn.commit()
        except Exception as e:
            logger.warning("metrics_record_failed", event_type=event_type, error=str(e))

    @contextlib.contextmanager
    def timed(
        self,
        event_type: str,
        *,
        channel: str | None = None,
        provider: str | None = None,
        meta: dict[str, Any] | None = None,
    ):
        """Context manager для latency измерения. Success=False если исключение."""
        t0 = time.time()
        success = True
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            self.record(
                event_type,
                channel=channel,
                duration_ms=duration_ms,
                success=success,
                provider=provider,
                meta=meta,
            )

    # ── Query API (используется backend'ом /api/metrics) ───────────

    def summary(self, window_sec: int) -> dict[str, Any]:
        """Aggregated summary за последний window_sec (для карточек)."""
        if not self._enabled or self._conn is None:
            return {"enabled": False}
        cutoff = time.time() - window_sec
        out: dict[str, Any] = {"enabled": True, "window_sec": window_sec}
        try:
            with self._lock:
                cur = self._conn.cursor()
                # Counters по типам
                rows = cur.execute(
                    "SELECT event_type, COUNT(*), COUNT(CASE WHEN success=0 THEN 1 END)"
                    " FROM metric_events WHERE ts > ? GROUP BY event_type",
                    (cutoff,),
                ).fetchall()
                out["counts"] = {r[0]: {"total": r[1], "failed": r[2]} for r in rows}
                # Latency: avg / p50 / p95 / max для stt/llm/tts/e2e
                out["latency_ms"] = {}
                for et in ("stt", "llm", "tts", "e2e"):
                    durs = [
                        r[0]
                        for r in cur.execute(
                            "SELECT duration_ms FROM metric_events"
                            " WHERE event_type=? AND duration_ms IS NOT NULL AND ts > ?"
                            " ORDER BY duration_ms",
                            (et, cutoff),
                        ).fetchall()
                    ]
                    if not durs:
                        continue
                    n = len(durs)
                    p50 = durs[n // 2]
                    p95 = durs[min(n - 1, int(n * 0.95))]
                    out["latency_ms"][et] = {
                        "n": n,
                        "avg": int(sum(durs) / n),
                        "p50": p50,
                        "p95": p95,
                        "max": durs[-1],
                    }
                # Provider hit-rate
                rows = cur.execute(
                    "SELECT event_type, provider, COUNT(*) FROM metric_events"
                    " WHERE ts > ? AND provider IS NOT NULL"
                    " GROUP BY event_type, provider",
                    (cutoff,),
                ).fetchall()
                providers: dict[str, dict[str, int]] = {}
                for et, prov, n in rows:
                    providers.setdefault(et, {})[prov] = n
                out["providers"] = providers
            return out
        except Exception as e:
            logger.warning("metrics_summary_failed", error=str(e))
            return {"enabled": False, "error": str(e)}

    def timeseries(
        self, event_type: str, window_sec: int, bucket_sec: int
    ) -> list[dict[str, Any]]:
        """Bucketed time-series для графиков.

        Для latency-событий (есть duration_ms): avg latency per bucket.
        Для counter-событий: count per bucket.
        """
        if not self._enabled or self._conn is None:
            return []
        cutoff = time.time() - window_sec
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT CAST(ts / ? AS INTEGER) * ? AS bucket,"
                    "       COUNT(*) AS n,"
                    "       AVG(duration_ms) AS avg_ms"
                    " FROM metric_events"
                    " WHERE event_type=? AND ts > ?"
                    " GROUP BY bucket ORDER BY bucket",
                    (bucket_sec, bucket_sec, event_type, cutoff),
                ).fetchall()
            return [
                {
                    "ts": int(r[0]),
                    "count": int(r[1]),
                    "avg_ms": int(r[2]) if r[2] is not None else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("metrics_timeseries_failed", event_type=event_type, error=str(e))
            return []

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Последние N событий — для таблицы в HUD."""
        if not self._enabled or self._conn is None:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT ts, event_type, channel, duration_ms, success, provider, meta_json"
                    " FROM metric_events ORDER BY ts DESC LIMIT ?",
                    (max(1, min(limit, 1000)),),
                ).fetchall()
            return [
                {
                    "ts": r[0],
                    "event_type": r[1],
                    "channel": r[2],
                    "duration_ms": r[3],
                    "success": bool(r[4]),
                    "provider": r[5],
                    "meta": json.loads(r[6]) if r[6] else None,
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("metrics_recent_failed", error=str(e))
            return []


# Singleton — импортируем как `from core.metrics import metrics`.
# Подписку на bus делаем lazy через attach_bus(), чтобы не было циркулярного импорта.
metrics = MetricsCollector()

"""Proactive nudges — JARVIS первый инициирует диалог.

Background watcher с тиком ~60 сек. На каждом тике проверяет набор
триггеров (calendar, brief, long focus и т.д.). Если триггер сработал —
публикует ASSISTANT_REPLY в bus с source='proactive', все каналы
(Telegram, HUD, voice, avatar) подхватывают и доставляют Боссу.

Дедупликация — JSONL-файл с уже сработавшими триггерами за день/час
(зависит от триггера). Гарантирует, что один nudge не повторится.
"""
from core.proactive.watcher import ProactiveWatcher

__all__ = ["ProactiveWatcher"]

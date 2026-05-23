"""Context awareness — Jarvis помнит чем занят Босс.

On-demand модель: Босс сам триггерит snapshot ('запомни что делаю' /
'опиши экран'), описание сохраняется в rolling-буфер. Не фоновый watcher.
"""
from core.awareness.state import ContextBuffer, ContextEntry, get_buffer

__all__ = ["ContextBuffer", "ContextEntry", "get_buffer"]

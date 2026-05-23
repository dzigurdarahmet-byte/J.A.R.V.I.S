"""Память Джарвиса.

Tier 1 (workspace/{SOUL,USER,MEMORY}.md) — критичные факты, всегда в system prompt.
Tier 2 (workspace/daily/YYYY-MM-DD.md) — дневной лог реплик, sliding window.
Tier 3 (vector store) — отложено на Фазу 2.
"""

from .manager import MemoryManager

__all__ = ["MemoryManager"]

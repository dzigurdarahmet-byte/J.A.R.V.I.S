"""MemoryManager: Tier 1 (SOUL/USER/MEMORY) + Tier 2 (daily logs).

Архитектурно по v4.4/v5.3:
- SOUL.md     — личность, правила (всегда в system prompt)
- USER.md     — профиль Босса (всегда в system prompt)
- MEMORY.md   — Tier 1 ключевые факты (всегда в system prompt, лимит ~30 строк)
- daily/YYYY-MM-DD.md — Tier 2 сырой лог разговоров (для resume контекста)

При старте провайдера канала вызывается load_recent_context(limit) — он читает
последние N exchanges из сегодняшнего daily-лога и возвращает их как
list[Message]. Это позволяет Джарвису помнить «утренний разговор» после
рестарта в обед.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from core.logging import get_logger
from core.providers.base import Message

logger = get_logger(__name__)

# Формат daily-лога: пары
#   ## 14:23  user  [telegram]
#   текст
#   ## 14:23  assistant
#   текст
# (channel-suffix после роли — опционален, поэтому в regex (?:...)? )
DAILY_ENTRY_RE: Final = re.compile(
    r"^##\s+(\d{2}:\d{2})\s+(user|assistant)(?:\s+\[[^\]]+\])?\s*$",
    flags=re.MULTILINE,
)


@dataclass(slots=True)
class MemorySnapshot:
    """То что попадает в system prompt."""

    soul: str
    user_profile: str
    tier1_facts: str

    def render_system_addendum(self) -> str:
        """Сформировать блок для добавления в SYSTEM_PROMPT."""
        parts: list[str] = []
        if self.tier1_facts.strip():
            parts.append(f"=== MEMORY (Tier 1) ===\n{self.tier1_facts.strip()}")
        if self.user_profile.strip():
            # USER.md уже структурирован — добавляем как есть, но обрезаем placeholder-секции
            user_clean = self._strip_placeholders(self.user_profile)
            parts.append(f"=== USER PROFILE ===\n{user_clean}")
        # SOUL не добавляем в каждый запрос — там основной prompt уже описывает поведение.
        # Используется только если SYSTEM_PROMPT не задан явно.
        return "\n\n".join(parts)

    @staticmethod
    def _strip_placeholders(text: str) -> str:
        """Убрать строки-плейсхолдеры вида '- _(пусто)_' и '_заполнится позже_'."""
        lines: list[str] = []
        for line in text.splitlines():
            low = line.strip().lower()
            if low.startswith("- _(пусто)_") or "_заполнится позже_" in low:
                continue
            if low.startswith("_") and low.endswith("_") and len(low) > 2:
                continue  # markdown italic placeholder
            lines.append(line)
        return "\n".join(lines)


class MemoryManager:
    """Файловая память Джарвиса.

    Thread-safe append через threading.Lock — daily logs пишутся синхронно из
    разных каналов (telegram + local_voice могут писать в один файл).
    """

    def __init__(self, workspace_dir: str | Path) -> None:
        self._workspace = Path(workspace_dir).resolve()
        self._daily_dir = self._workspace / "daily"
        self._lock = threading.Lock()
        # Ленивая загрузка SOUL/USER — они большие, читаем один раз и кэшируем.
        # MEMORY.md перечитываем каждый запрос (он часто меняется через remember_fact).
        self._soul_cache: str | None = None
        self._user_cache: str | None = None
        self._soul_mtime: float = 0.0
        self._user_mtime: float = 0.0
        # Tier 3 (vector). Лениво — модель грузится тяжело.
        self._vector = None  # type: ignore[var-annotated]
        self._vector_init_tried = False

    def _ensure_vector(self):
        """Ленивая инициализация Tier 3. Возвращает VectorMemory|None."""
        if self._vector is not None or self._vector_init_tried:
            return self._vector
        self._vector_init_tried = True
        try:
            from core.memory.vector import make_vector_memory
            self._vector = make_vector_memory(self._workspace)
        except Exception as e:
            logger.warning("vector_memory_disabled", error=str(e))
            self._vector = None
        return self._vector

    async def add_to_vector(
        self,
        text: str,
        role: str = "user",
        channel: str = "",
    ) -> None:
        """Записать текст в Tier 3 vector store. Не блокирует если vector ещё не готов."""
        vm = self._ensure_vector()
        if vm is None:
            return
        try:
            await vm.add(text, role=role, channel=channel)
        except Exception as e:
            logger.warning("vector_add_failed", error=str(e))

    async def search_vector(self, query: str, limit: int = 5) -> list[dict]:
        """Семантический поиск по Tier 3. Возвращает [] если vector не доступен."""
        vm = self._ensure_vector()
        if vm is None:
            return []
        try:
            return await vm.search(query, limit=limit)
        except Exception as e:
            logger.warning("vector_search_failed", error=str(e))
            return []

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def daily_dir(self) -> Path:
        return self._daily_dir

    def _read_file(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("memory_read_failed", path=str(path), error=str(e))
            return ""

    def _read_with_cache(self, path: Path, cache_key: str) -> str:
        if not path.exists():
            return ""
        mtime = path.stat().st_mtime
        attr_cache = f"_{cache_key}_cache"
        attr_mtime = f"_{cache_key}_mtime"
        if getattr(self, attr_cache) is not None and mtime == getattr(self, attr_mtime):
            return getattr(self, attr_cache)
        text = self._read_file(path)
        setattr(self, attr_cache, text)
        setattr(self, attr_mtime, mtime)
        return text

    def load_soul(self) -> str:
        return self._read_with_cache(self._workspace / "SOUL.md", "soul")

    def load_user_profile(self) -> str:
        return self._read_with_cache(self._workspace / "USER.md", "user")

    def load_memory_md(self) -> str:
        # Без кэша — этот файл часто меняется через remember_fact
        return self._read_file(self._workspace / "MEMORY.md")

    def snapshot(self) -> MemorySnapshot:
        """Снимок памяти для system prompt."""
        return MemorySnapshot(
            soul=self.load_soul(),
            user_profile=self.load_user_profile(),
            tier1_facts=self.load_memory_md(),
        )

    # ── Tier 2: daily logs ─────────────────────────────────────────────

    def _today_log_path(self, when: datetime | None = None) -> Path:
        when = when or datetime.now()
        return self._daily_dir / f"{when:%Y-%m-%d}.md"

    def append_exchange(
        self,
        user_text: str,
        assistant_text: str,
        channel: str,
        when: datetime | None = None,
    ) -> None:
        """Записать пару user→assistant в daily-лог сегодняшнего дня."""
        when = when or datetime.now()
        path = self._today_log_path(when)
        block = (
            f"## {when:%H:%M}  user  [{channel}]\n"
            f"{user_text.strip()}\n\n"
            f"## {when:%H:%M}  assistant\n"
            f"{assistant_text.strip()}\n\n"
        )
        with self._lock:
            self._daily_dir.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                header = f"# Daily log — {when:%Y-%m-%d}\n\n"
                path.write_text(header + block, encoding="utf-8")
            else:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
        logger.info(
            "memory_exchange_appended",
            path=str(path),
            channel=channel,
            user_chars=len(user_text),
            assistant_chars=len(assistant_text),
        )

    def load_recent_context(self, limit_messages: int = 12) -> list[Message]:
        """Загрузить последние N сообщений из сегодняшнего daily-лога.

        Возвращает list[Message] чередующиеся user/assistant. Если файла нет —
        возвращает пустой список. Если меньше N — возвращает всё что есть.
        """
        path = self._today_log_path()
        if not path.exists():
            return []
        text = self._read_file(path)
        if not text:
            return []

        # Парсинг: разбиваем на блоки по заголовкам '## HH:MM  role'
        entries: list[tuple[str, str]] = []  # (role, body)
        positions = [(m.start(), m.group(2).lower()) for m in DAILY_ENTRY_RE.finditer(text)]
        for i, (pos, role) in enumerate(positions):
            # тело — от конца ## строки до следующего ## или EOF
            start = text.find("\n", pos) + 1
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            body = text[start:end].strip()
            if body:
                entries.append((role, body))

        # Берём последние limit_messages
        recent = entries[-limit_messages:]
        messages: list[Message] = []
        for role, body in recent:
            if role not in {"user", "assistant"}:
                continue
            messages.append(Message(role=role, content=body))  # type: ignore[arg-type]
        logger.info(
            "memory_context_loaded",
            messages=len(messages),
            path=str(path),
            limit=limit_messages,
        )
        return messages

    # ── Tier 1: явное запоминание факта ────────────────────────────────

    def remember_fact(self, fact: str) -> None:
        """Добавить факт в MEMORY.md.

        Простая реализация на MVP: append в конец файла под heading '## Заметки'.
        Позже добавим intelligent merging через LLM (vector dedup, обновление
        существующих фактов и т.п.).
        """
        if not fact.strip():
            return
        memory_path = self._workspace / "MEMORY.md"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- ({timestamp}) {fact.strip()}\n"
        with self._lock:
            if memory_path.exists():
                text = memory_path.read_text(encoding="utf-8")
                if "## Заметки" in text:
                    text = text.rstrip() + "\n" + line
                else:
                    text = text.rstrip() + "\n\n## Заметки\n" + line
                memory_path.write_text(text, encoding="utf-8")
            else:
                memory_path.write_text(
                    f"# MEMORY.md\n\n## Заметки\n{line}", encoding="utf-8"
                )
        # сброс кэша memory_md не нужен — мы его и так не кэшируем
        logger.info("memory_fact_remembered", fact_preview=fact[:60])

    # ── Async-удобный wrapper ──────────────────────────────────────────

    async def append_exchange_async(
        self, user_text: str, assistant_text: str, channel: str
    ) -> None:
        await asyncio.to_thread(self.append_exchange, user_text, assistant_text, channel)

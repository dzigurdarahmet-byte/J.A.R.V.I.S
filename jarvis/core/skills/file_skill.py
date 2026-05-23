"""Skill: операции с файлами Босса голосом/текстом.

Поддерживает:
  - **find** — «найди файл отчёт», «где файл договор», «поищи документ X»
  - **open** — «открой документ X», «открой файл Y» (через os.startfile, Windows)
  - **rename** — «переименуй X в Y»
  - **delete** — «удали файл X» (двухшаговое: запрашиваем подтверждение)

Безопасность:
  - Whitelist папок (Documents, Downloads, Desktop, Pictures, Music, Videos).
  - Никаких C:\\Windows, C:\\Program Files, корня репо JARVIS — только пользовательские
    папки. См. ALLOWED_ROOTS.
  - Никаких relative-paths и symlink-escapes — проверяем resolve() против whitelist.
  - Delete без wildcards. Требует подтверждения в течение 60 секунд.
  - Никаких рекурсивных удалений папок — только одиночные файлы.

Triggers:
  - «найди/поищи файл/документ/папку X»
  - «открой файл/документ/папку X»
  - «переименуй X в Y»
  - «удали файл/документ X», «удали X.docx»
  - «подтверждаю удаление» / «да, удали»
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


# ── Whitelist: разрешённые корневые папки ────────────────────────────
def _default_allowed_roots() -> list[Path]:
    """Базовый whitelist под Windows-юзера."""
    home = Path.home()
    candidates = [
        home / "Documents",
        home / "Downloads",
        home / "Desktop",
        home / "Pictures",
        home / "Music",
        home / "Videos",
        # OneDrive-зеркала, если есть
        home / "OneDrive" / "Documents",
        home / "OneDrive" / "Рабочий стол",
        home / "OneDrive" / "Desktop",
    ]
    return [p for p in candidates if p.exists()]


# ── Triggers ─────────────────────────────────────────────────────────
_FIND_PATTERNS = [
    re.compile(
        r"\b(?:найди|поищи|ищи|где)\s+(?:мне\s+)?"
        r"(?:файл|документ|папку|директорию|каталог)\s+(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.UNICODE,
    ),
    re.compile(
        r"\b(?:найди|поищи)\s+(?:по\s+)?имени\s+(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.UNICODE,
    ),
]
_OPEN_PATTERNS = [
    re.compile(
        r"\b(?:открой|открыть)\s+(?:мне\s+)?"
        r"(?:файл|документ|папку|директорию|каталог)\s+(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.UNICODE,
    ),
]
_RENAME_PATTERNS = [
    re.compile(
        r"\b(?:переименуй|переименовать)\s+(?:файл\s+|документ\s+)?(.+?)\s+в\s+(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.UNICODE,
    ),
]
_DELETE_PATTERNS = [
    re.compile(
        r"\b(?:удали|удалить|снеси|сотри)\s+(?:файл\s+|документ\s+)?(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.UNICODE,
    ),
]
_CONFIRM_PATTERNS = [
    re.compile(
        r"\b(?:подтверждаю|подтверди|да[\s,]+удали|удаляй|сноси|давай удаляй|"
        r"yes|confirm|delete it|давай)\b",
        re.IGNORECASE | re.UNICODE,
    ),
]
_CANCEL_PATTERNS = [
    re.compile(
        r"\b(?:отмена|отмени|нет[\s,]+не\s+удаляй|стоп|cancel|abort|погоди)\b",
        re.IGNORECASE | re.UNICODE,
    ),
]


# ── Pending state (per process, in-memory) ───────────────────────────
@dataclass
class _PendingDelete:
    """Ожидающее подтверждения удаление. Истекает через TTL."""
    path: Path
    requested_at: float
    request_id: str

    def is_expired(self, ttl_sec: float = 60.0) -> bool:
        return (time.time() - self.requested_at) > ttl_sec


# ── Helpers ──────────────────────────────────────────────────────────
def _is_under_root(p: Path, roots: list[Path]) -> bool:
    """True если p внутри одной из разрешённых корневых папок."""
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError):
        return False
    for r in roots:
        try:
            resolved.relative_to(r.resolve())
            return True
        except ValueError:
            continue
    return False


def _clean_query(raw: str) -> str:
    """Убираем кавычки, лишние пробелы."""
    return raw.strip().strip('"\'«»“”').strip(".,;:!?")


# Служебные папки, которые НЕ должны попадать в результаты поиска.
# Босс ищет свои документы, не файлы из site-packages.
_SKIP_DIRS = frozenset({
    ".venv", "venv", "env", "node_modules", "__pycache__",
    ".git", ".idea", ".vscode", ".cache", "site-packages",
    "AppData", "$RECYCLE.BIN", "System Volume Information",
})


# ── Транслитерация ──────────────────────────────────────────────────
# Yandex STT всегда возвращает кириллицу — даже когда Босс произносит
# английское название. «MEMORY» → «мемори», «report» → «репорт».
# Прямой substring-поиск не находит латинские файлы по такому запросу,
# поэтому в качестве fallback пробуем транслитерацию.
#
# Стратегия: генерируем НЕСКОЛЬКО вариантов одного запроса и проверяем
# их все. Делаем «упрощённую» нормализацию (без удвоений, без мягкого
# знака), чтобы «мемори» матчился и в «memory», и в «memori».

# RU → LAT (GOST-R 52535.1-2006 lite + общеупотребительные правила)
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# LAT → RU (грубая обратка, для редкого случая «бот сказал на латинице»)
_LAT_TO_CYR = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з",
}


# Биграммы — некоторые сочетания на кириллице передают английские звуки
# нестандартно по моноалфавитному правилу. «дж» = английское «j».
_CYR_BIGRAM_TO_LAT = {
    "дж": "j",
    "кс": "x",   # «Алексей» = «Alexei», «эксель» = «excel»
    "ия": "ia",  # «Россия» = «Russia»
}


def _translit_cyr_to_lat(s: str) -> str:
    s_low = s.lower()
    # Сначала биграммы (длинные сочетания), потом моноалфавит.
    for bg, lat in _CYR_BIGRAM_TO_LAT.items():
        s_low = s_low.replace(bg, lat)
    out = []
    for ch in s_low:
        out.append(_CYR_TO_LAT.get(ch, ch))
    return "".join(out)


def _translit_lat_to_cyr(s: str) -> str:
    out = []
    for ch in s.lower():
        out.append(_LAT_TO_CYR.get(ch, ch))
    return "".join(out)


def _normalize_for_match(s: str) -> str:
    """Каноническая форма для нечёткого сравнения. Гасит расхождения,
    которые типично возникают между STT-транслитом и реальными именами:

      - lowercase
      - схлопывание удвоений (memmory → memory)
      - y → i (memory → memori, чтобы STT «мемори» сматчился)
      - удаление мягкого/твёрдого знака
      - удаление разделителей и пунктуации
    """
    s = s.lower()
    # схлопнуть удвоения букв (memmory → memory)
    out = []
    prev = ""
    for ch in s:
        if ch == prev and ch.isalpha():
            continue
        out.append(ch)
        prev = ch
    s = "".join(out)
    # y → i (фонетически равны на конце слова: memory ≈ memori)
    s = s.replace("y", "i")
    # мягкий/твёрдый знак — STT их не выдаёт, в именах файлов их обычно нет
    s = s.replace("ь", "").replace("ъ", "")
    # убрать разделители и пунктуацию между буквами
    s = re.sub(r"[\s_\-.]+", "", s)
    return s


def _build_query_variants(query: str) -> list[str]:
    """Сгенерировать варианты query для substring-поиска.

    Порядок: сначала прямой (точнее), потом транслитерации (грубее).
    Дедупликация — порядок сохраняется.
    """
    seen: set[str] = set()
    variants: list[str] = []

    def _add(v: str) -> None:
        if v and v not in seen:
            seen.add(v)
            variants.append(v)

    base = query.strip().lower()
    _add(base)
    _add(_normalize_for_match(base))
    # Если в запросе есть кириллица — добавим латинскую транслит-версию.
    if any("а" <= ch <= "я" or ch == "ё" for ch in base):
        lat = _translit_cyr_to_lat(base)
        _add(lat)
        _add(_normalize_for_match(lat))
    # Если в запросе латиница — добавим кириллическую (на случай когда
    # пользователь напечатал латиницей, а файл по-русски).
    if any("a" <= ch <= "z" for ch in base):
        cyr = _translit_lat_to_cyr(base)
        _add(cyr)
        _add(_normalize_for_match(cyr))
    return variants


def _search_files(roots: list[Path], query: str, max_results: int = 8) -> list[Path]:
    """Найти файлы по подстроке имени. Case-insensitive, с транслит-fallback.

    Используем os.walk (а не rglob) чтобы обрезать обход служебных папок
    in-place через dirnames[:]=... — иначе rglob лезет в .venv/site-packages
    и захламляет выдачу.

    Алгоритм:
      1. Один проход по файлам, нормализуем имя в lowercase + dedup-форму.
      2. Проверяем подстроку для всех вариантов query (кириллица + транслит).
      3. Совпадение хоть по одному варианту → файл попадает в результаты.
    """
    variants = _build_query_variants(query)
    if not variants:
        return []
    # Двухслойная проверка: «как есть» + нормализованная форма имени.
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                for fn in filenames:
                    fn_low = fn.lower()
                    fn_norm = _normalize_for_match(fn)
                    if any((v in fn_low) or (v in fn_norm) for v in variants):
                        matches.append(Path(dirpath) / fn)
                        if len(matches) >= max_results:
                            return matches
        except (PermissionError, OSError) as e:
            logger.debug("file_search_perm_error", root=str(root), error=str(e))
            continue
    return matches


def _format_path_for_speech(p: Path, home: Path) -> str:
    """Сократить путь для вывода: ~/Documents/foo.docx вместо абсолюта."""
    try:
        rel = p.relative_to(home)
        return f"~/{rel.as_posix()}"
    except ValueError:
        return str(p)


async def _open_path(p: Path) -> bool:
    """Открыть файл/папку через дефолтный обработчик ОС."""
    def _do() -> bool:
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606 — Windows API, не shell
            elif sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False, timeout=10)
            else:
                subprocess.run(["xdg-open", str(p)], check=False, timeout=10)
            return True
        except Exception as e:
            logger.error("file_open_failed", path=str(p), error=str(e))
            return False
    return await asyncio.to_thread(_do)


async def _delete_path(p: Path) -> bool:
    """Удалить файл (НЕ папку рекурсивно)."""
    def _do() -> bool:
        try:
            if p.is_dir():
                # Пустую папку — да; непустую — нет.
                p.rmdir()
            else:
                p.unlink()
            return True
        except OSError as e:
            logger.error("file_delete_failed", path=str(p), error=str(e))
            return False
    return await asyncio.to_thread(_do)


# ── Skill ────────────────────────────────────────────────────────────
class FileSkill(KeywordSkill):
    """L1+L2 skill: find/open/rename/delete файлов в whitelisted папках."""

    name = "file_ops"
    keywords = [
        # find
        r"\b(?:найди|поищи|ищи|где)\s+(?:мне\s+)?(?:файл|документ|папку|директорию|каталог)\b",
        r"\b(?:найди|поищи)\s+(?:по\s+)?имени\s+\S",
        # open
        r"\b(?:открой|открыть)\s+(?:мне\s+)?(?:файл|документ|папку|директорию|каталог)\b",
        # rename
        r"\bпереименуй\s+(?:файл\s+|документ\s+)?\S+\s+в\s+\S",
        # delete
        r"\b(?:удали|удалить|снеси|сотри)\s+(?:файл|документ)\b",
        # confirm/cancel — только когда есть pending
        # (через match() проверяем _pending; здесь keyword не нужен)
    ]

    def __init__(self, allowed_roots: list[Path] | None = None) -> None:
        super().__init__()
        self._roots = allowed_roots if allowed_roots is not None else _default_allowed_roots()
        self._home = Path.home()
        self._pending: dict[str, _PendingDelete] = {}  # ключ — owner ("default")

    def match(self, text: str) -> float:
        # Если есть pending delete — ловим confirm/cancel.
        if self._has_active_pending():
            if _CONFIRM_PATTERNS[0].search(text) or _CANCEL_PATTERNS[0].search(text):
                return 1.0
        return super().match(text)

    def _has_active_pending(self, owner: str = "default") -> bool:
        p = self._pending.get(owner)
        if p is None:
            return False
        if p.is_expired():
            del self._pending[owner]
            return False
        return True

    async def run(self, text: str, request_id: str) -> SkillResult:
        # 0) Pending confirm/cancel
        if self._has_active_pending():
            if _CONFIRM_PATTERNS[0].search(text):
                return await self._confirm_delete(request_id)
            if _CANCEL_PATTERNS[0].search(text):
                return self._cancel_delete()

        # 1) Delete intent
        for pat in _DELETE_PATTERNS:
            m = pat.search(text)
            if m:
                return await self._handle_delete(_clean_query(m.group(1)), request_id)

        # 2) Rename intent
        for pat in _RENAME_PATTERNS:
            m = pat.search(text)
            if m:
                return await self._handle_rename(
                    _clean_query(m.group(1)),
                    _clean_query(m.group(2)),
                )

        # 3) Open intent
        for pat in _OPEN_PATTERNS:
            m = pat.search(text)
            if m:
                return await self._handle_open(_clean_query(m.group(1)))

        # 4) Find intent (default)
        for pat in _FIND_PATTERNS:
            m = pat.search(text)
            if m:
                return await self._handle_find(_clean_query(m.group(1)))

        # Не должны сюда попасть если match() сработал, но на всякий
        return SkillResult(
            text="Босс, не понял что с файлом. Можно: «найди файл X», «открой документ X», «переименуй X в Y», «удали файл X».",
            speakable=True,
        )

    # ── Handlers ────────────────────────────────────────────────────
    async def _handle_find(self, query: str) -> SkillResult:
        if not query:
            return SkillResult(text="Босс, что искать?", speakable=True)
        matches = await asyncio.to_thread(_search_files, self._roots, query, 8)
        if not matches:
            return SkillResult(
                text=f"Не нашёл файлов с именем «{query}» в Documents/Downloads/Desktop и т.п.",
                speakable=True,
            )
        if len(matches) == 1:
            p = matches[0]
            return SkillResult(
                text=f"Нашёл: {_format_path_for_speech(p, self._home)}",
                speakable=True,
                data={"found": [str(p)]},
            )
        lines = [
            f"  {i + 1}. {_format_path_for_speech(p, self._home)}"
            for i, p in enumerate(matches)
        ]
        return SkillResult(
            text=f"Нашёл {len(matches)} совпадений по «{query}»:\n" + "\n".join(lines),
            speakable=True,
            data={"found": [str(p) for p in matches]},
        )

    async def _handle_open(self, query: str) -> SkillResult:
        if not query:
            return SkillResult(text="Босс, что открыть?", speakable=True)
        matches = await asyncio.to_thread(_search_files, self._roots, query, 4)
        if not matches:
            return SkillResult(
                text=f"Файл «{query}» не найден в разрешённых папках.",
                speakable=True,
            )
        if len(matches) > 1:
            lines = [
                f"  {i + 1}. {_format_path_for_speech(p, self._home)}"
                for i, p in enumerate(matches[:4])
            ]
            return SkillResult(
                text=(
                    f"Нашёл {len(matches)} совпадений — уточни какой:\n"
                    + "\n".join(lines)
                ),
                speakable=True,
                data={"candidates": [str(p) for p in matches]},
            )
        target = matches[0]
        ok = await _open_path(target)
        if not ok:
            return SkillResult(
                text=f"Не получилось открыть {target.name}.",
                speakable=True,
            )
        return SkillResult(
            text=f"Открываю {_format_path_for_speech(target, self._home)}.",
            speakable=True,
            data={"opened": str(target)},
        )

    async def _handle_rename(self, src_query: str, dst_name: str) -> SkillResult:
        if not src_query or not dst_name:
            return SkillResult(text="Босс, переименовать что в что?", speakable=True)
        # dst — простое имя без слешей (новое имя файла)
        if "/" in dst_name or "\\" in dst_name:
            return SkillResult(
                text="Босс, новое имя — только имя файла, без путей.",
                speakable=True,
            )
        matches = await asyncio.to_thread(_search_files, self._roots, src_query, 4)
        if not matches:
            return SkillResult(
                text=f"Файл «{src_query}» не найден.",
                speakable=True,
            )
        if len(matches) > 1:
            lines = [
                f"  {i + 1}. {_format_path_for_speech(p, self._home)}"
                for i, p in enumerate(matches[:4])
            ]
            return SkillResult(
                text=(
                    f"Нашёл {len(matches)} совпадений — уточни какой переименовать:\n"
                    + "\n".join(lines)
                ),
                speakable=True,
            )
        src = matches[0]
        dst = src.parent / dst_name
        if not _is_under_root(dst, self._roots):
            return SkillResult(
                text="Босс, целевое имя выводит за разрешённые папки. Отказ.",
                speakable=True,
            )
        if dst.exists():
            return SkillResult(
                text=f"Файл {dst.name} уже существует — не перезаписываю.",
                speakable=True,
            )

        def _do_rename() -> bool:
            try:
                src.rename(dst)
                return True
            except OSError as e:
                logger.error("file_rename_failed", src=str(src), dst=str(dst), error=str(e))
                return False

        ok = await asyncio.to_thread(_do_rename)
        if not ok:
            return SkillResult(text="Не получилось переименовать.", speakable=True)
        logger.info("file_renamed", src=str(src), dst=str(dst))
        return SkillResult(
            text=f"Переименовал в {dst.name}.",
            speakable=True,
            data={"renamed": [str(src), str(dst)]},
        )

    async def _handle_delete(self, query: str, request_id: str) -> SkillResult:
        if not query:
            return SkillResult(text="Босс, что удалить?", speakable=True)
        # Никаких wildcards
        if any(c in query for c in "*?["):
            return SkillResult(
                text="Босс, без масок — назови конкретное имя файла.",
                speakable=True,
            )
        matches = await asyncio.to_thread(_search_files, self._roots, query, 4)
        if not matches:
            return SkillResult(
                text=f"Файл «{query}» не найден в разрешённых папках.",
                speakable=True,
            )
        if len(matches) > 1:
            lines = [
                f"  {i + 1}. {_format_path_for_speech(p, self._home)}"
                for i, p in enumerate(matches[:4])
            ]
            return SkillResult(
                text=(
                    f"Нашёл {len(matches)} совпадений. Уточни какой именно удалить (полное имя):\n"
                    + "\n".join(lines)
                ),
                speakable=True,
            )
        target = matches[0]
        # Дополнительная проверка — точно ли под allowed_roots
        if not _is_under_root(target, self._roots):
            return SkillResult(
                text="Босс, этот файл не в разрешённых папках. Отказ.",
                speakable=True,
            )
        # Регистрируем pending — ждём подтверждения 60 сек
        self._pending["default"] = _PendingDelete(
            path=target, requested_at=time.time(), request_id=request_id,
        )
        return SkillResult(
            text=(
                f"Босс, удалить {_format_path_for_speech(target, self._home)}? "
                "Скажи «подтверждаю» в течение минуты, иначе отменю."
            ),
            speakable=True,
            data={"pending_delete": str(target)},
        )

    async def _confirm_delete(self, request_id: str) -> SkillResult:
        pending = self._pending.pop("default", None)
        if pending is None:
            return SkillResult(text="Нечего подтверждать.", speakable=True)
        if pending.is_expired():
            return SkillResult(
                text="Тайм-аут подтверждения — отмена.",
                speakable=True,
            )
        target = pending.path
        ok = await _delete_path(target)
        if not ok:
            return SkillResult(
                text=f"Не получилось удалить {target.name}.",
                speakable=True,
            )
        logger.info("file_deleted", path=str(target), request_id=request_id)
        return SkillResult(
            text=f"Удалил {target.name}, Босс.",
            speakable=True,
            data={"deleted": str(target)},
        )

    def _cancel_delete(self) -> SkillResult:
        pending = self._pending.pop("default", None)
        if pending is None:
            return SkillResult(text="Нечего отменять.", speakable=True)
        return SkillResult(
            text=f"Отменил удаление {pending.path.name}.",
            speakable=True,
        )

    # ── L2 Tool-use ─────────────────────────────────────────────────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "file_ops",
            "description": (
                "Operate on the Boss's files in whitelisted folders "
                "(Documents, Downloads, Desktop, Pictures, Music, Videos). "
                "Supports find, open, rename, delete. Delete REQUIRES "
                "a second call with action='confirm_delete' after the "
                "first call returned a pending_delete in data."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["find", "open", "rename", "delete", "confirm_delete", "cancel_delete"],
                    },
                    "query": {
                        "type": "string",
                        "description": "File name or substring (case-insensitive). For rename, the source name.",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Only for action='rename' — new file name (no slashes).",
                    },
                },
                "required": ["action"],
            },
        }

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        action = (args.get("action") or "").strip().lower()
        query = _clean_query(args.get("query") or "")
        new_name = _clean_query(args.get("new_name") or "")
        if action == "find":
            return await self._handle_find(query)
        if action == "open":
            return await self._handle_open(query)
        if action == "rename":
            return await self._handle_rename(query, new_name)
        if action == "delete":
            return await self._handle_delete(query, request_id)
        if action == "confirm_delete":
            return await self._confirm_delete(request_id)
        if action == "cancel_delete":
            return self._cancel_delete()
        return SkillResult(
            text=f"Неизвестное действие file_ops: {action!r}.",
            speakable=True,
        )

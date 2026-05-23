"""Skill: делегирование кодинг-задач в Claude Code CLI (claude.cmd).

Босс подписан на Anthropic Max 20× — `claude.cmd` использует его квоту,
а не наш API-токен. Этот skill ловит запросы вида «напиши код», «исправь
баг», «оптимизируй» и отправляет их в `claude -p "<task>"` как subprocess.

Два режима:

  **text** (default) — non-interactive `-p` без tools. Claude отвечает
  кодом в stdout, JARVIS возвращает в чат/TG. Босс копипастит сам.

  **edit** — Claude получает full tool-set (Read/Edit/Write/Bash/Grep/Glob)
  и `--add-dir <junction>` на JARVIS-репо. Может править файлы напрямую,
  запускать тесты, читать любые исходники. ОПАСНО — даём polный доступ
  к репо через `--dangerously-skip-permissions`. Триггерится явно по
  фразам типа «исправь в core/skills/X», «обнови файл», «отредактируй»
  и т.п.

Архитектура edit-mode:
  - Windows junction `C:\\jarvis-repo` → корень JARVIS-репо (ASCII path,
    не зависит от кириллицы оригинала). Создаётся `_ensure_repo_junction()`.
  - cwd = junction, `--add-dir <junction>` — Claude CLI auto-discovery
    видит CLAUDE.md, .git, etc.
  - `--dangerously-skip-permissions` — Claude применяет правки без
    подтверждения (в `-p` mode иначе зависает).

Тонкости:
- HTTPS_PROXY=http://127.0.0.1:10808 обязателен (v2rayN, иначе DNS).
- claude.cmd живёт в `%APPDATA%\\npm\\claude.cmd` (ASCII path — OK).
- text-mode cwd на `%TEMP%` (ASCII), чтобы кириллица не ломала
  CLAUDE.md auto-discovery в Claude CLI.
- Timeout 90 сек для text, 300 сек для edit (правка + тесты).
- Не блокируем event loop — `asyncio.create_subprocess_exec`.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


# Триггеры — KeywordSkill матчит по regex.
# Цель: не путать с обычными вопросами Боссу. «Напиши пост» / «напиши
# письмо» НЕ ловим — это другие задачи.
_TRIGGER_KEYWORDS: list[str] = [
    # «напиши/сгенерируй/сделай … код/функцию/класс/скрипт/тест/regex/sql»
    r"\b(?:напиши|сгенерируй|сделай|набросай|выдай)\s+"
    r"(?:мне\s+)?(?:код|функцию|класс|скрипт|метод|тест|regex|regexp|"
    r"sql[\s\-]?запрос|снипп?ет|сниппет|реализацию)\b",
    # «исправь баг», «отладь», «дебагни», «почини код»
    r"\b(?:исправь|отладь|дебагни|почини|пофикси)\s+(?:баг|ошибку|код|"
    r"скрипт|функцию|тест)\b",
    # «оптимизируй …» — допускаем 1-2 промежуточных слова («оптимизируй sql запрос»).
    r"\bоптимизируй\s+(?:\w+\s+){0,2}(?:код|функцию|запрос|скрипт|sql|"
    r"sql[\s\-]?запрос|алгоритм|производительность)\b",
    # «реализуй …», «давай реализуем», «нужна реализация»
    r"\b(?:реализуй|давай\s+реализуем|нужна\s+реализация|запили)\b",
    # «помоги с кодом», «помоги с функцией»
    r"\bпомоги\s+(?:с|написать)\s+(?:кодом?|функцией|классом|скриптом|"
    r"регуляркой|sql)\b",
    # «отрефактори»
    r"\b(?:отрефактори|рефактори|рефактор)\b",
    # «преобразуй … в Python/JS/...»
    r"\bпреобразуй\s+.+\s+в\s+(?:python|пайтон|javascript|js|"
    r"typescript|ts|sql|powershell|ps1|bash|sh)\b",
    # явный вызов CLI
    r"\b(?:claude\s+code|клод\s+код|кодовый\s+ассистент)\b",
    # === edit-mode keywords (правка JARVIS-репо напрямую) ===
    # «исправь в core/skills/X», «обнови файл», «отредактируй … в jarvis»
    r"\b(?:исправь|обнови|отредактируй|поправь|измени)\s+(?:в\s+)?"
    r"(?:файл|модуль|скилл|skill|core/|jarvis/|channels/)",
    # «добавь в core/skills новый skill», «допиши в registry»
    r"\b(?:добавь|допиши|внеси|вставь)\s+(?:в\s+)?"
    r"(?:файл|модуль|core/|jarvis/|registry|repo|репо)",
    # «запусти тесты», «прогони pytest»
    r"\b(?:запусти|прогони|выполни)\s+(?:тест\w*|pytest|линт\w*|mypy|ruff)\b",
]


# Edit-mode regex — если совпало, гоним через edit-режим (с tools + cwd
# на JARVIS-репо). Иначе text-mode (просто текстовый ответ).
_EDIT_MODE_TRIGGERS = re.compile(
    r"\b(?:исправь|обнови|отредактируй|поправь|измени|добавь|допиши|"
    r"внеси|вставь|запусти|прогони|выполни|прочитай|покажи|найди|посмотри|глянь)\s+"
    r"(?:в\s+)?(?:файл|модуль|скилл|skill|core/|jarvis/|channels/|"
    r"тест\w*|pytest|линт\w*|mypy|ruff|registry|repo|репо|"
    r"[A-Z][A-Za-z0-9_]*\.(?:md|py|json|yml|yaml|ps1|bat|html))|"
    r"(?:core/skills/|jarvis/core/|channels/web_hud/|docs/)",
    re.IGNORECASE | re.UNICODE,
)

# Default cwd для subprocess (text-mode) — ASCII-папка, не кириллический
# корень JARVIS. Claude CLI читает CLAUDE.md из cwd, и кириллица в пути
# иногда ломает его. Используем %TEMP% — там всегда ASCII.
_SAFE_CWD = Path(os.environ.get("TEMP") or os.environ.get("TMP") or r"C:\Windows\Temp")

# ASCII-junction на корень JARVIS-репо для edit-mode. Создаётся однократно
# через _ensure_repo_junction() — Windows mklink /J не требует admin прав.
# Указывает на: C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)
_REPO_JUNCTION = Path(r"C:\jarvis-repo")
_REPO_REAL_PATH = Path(
    r"C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)"
)


def _ensure_repo_junction() -> Path | None:
    """Создать junction если ещё нет. Возвращает путь или None если упало."""
    if _REPO_JUNCTION.exists():
        return _REPO_JUNCTION
    if not _REPO_REAL_PATH.exists():
        logger.warning("code_assist_no_repo_path", path=str(_REPO_REAL_PATH))
        return None
    try:
        import subprocess
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(_REPO_JUNCTION), str(_REPO_REAL_PATH)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and _REPO_JUNCTION.exists():
            logger.info("code_assist_junction_created", path=str(_REPO_JUNCTION))
            return _REPO_JUNCTION
        logger.warning(
            "code_assist_junction_failed",
            stderr=result.stderr[:200],
            stdout=result.stdout[:200],
        )
        return None
    except Exception as e:  # noqa: BLE001 — диагностика
        logger.error("code_assist_junction_error", error=str(e))
        return None

# Системный промпт — лаконично, по делу (text-mode).
_APPEND_SYSTEM_PROMPT = (
    "You are coding helper for JARVIS personal assistant. The Boss talks "
    "Russian, but write code comments in English unless asked otherwise. "
    "Be terse: return code wrapped in fenced blocks plus 1-2 line "
    "explanation. No long preambles. If task is ambiguous, pick the most "
    "common interpretation and note assumption in one line."
)

# Edit-mode prompt — Claude получает доступ к JARVIS-репо и tools.
_APPEND_SYSTEM_PROMPT_EDIT = (
    "You are coding helper inside the JARVIS personal-assistant repo. "
    "You have full tool access (Read, Edit, Write, Bash, Grep, Glob). "
    "The repo follows these conventions: skills go to core/skills/, "
    "register them in core/skills/registry.py. Tests via pytest. "
    "Code in English, comments in Russian when explaining the domain. "
    "When making changes: keep diff minimal, preserve existing style, "
    "always run a syntax check (python -c 'import ast; "
    "ast.parse(open(path).read())') on touched .py files. "
    "At the end, summarize what was changed in 3-5 bullet points in Russian."
)

# Сколько секунд ждём ответ.
_TIMEOUT_SEC = 90.0


def _resolve_claude_cmd() -> Path | None:
    """Найти claude.cmd. Сначала PATH, потом стандартное npm-global место."""
    found = shutil.which("claude.cmd") or shutil.which("claude")
    if found:
        return Path(found)
    fallback = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
    if fallback.exists():
        return fallback
    return None


def _extract_task(text: str) -> str:
    """Вытащить ядро задачи из реплики Босса.

    Просто отбрасываем самые типичные «преамбулы» и возвращаем хвост.
    Сильно не вычищаем — Claude разберётся.
    """
    cleaned = text.strip()
    # Срезаем обращение «джарвис, ...»
    cleaned = re.sub(
        r"^\s*джарвис[,\s]+", "", cleaned, flags=re.IGNORECASE | re.UNICODE
    )
    return cleaned


class CodeAssistSkill(KeywordSkill):
    """L1+L2 skill: делегирует кодинг-задачу в `claude.cmd -p`."""

    name = "code_assist"
    keywords = _TRIGGER_KEYWORDS

    def __init__(
        self,
        claude_cmd: Path | None = None,
        proxy_url: str | None = None,
        timeout_sec: float = _TIMEOUT_SEC,
    ) -> None:
        super().__init__()
        self._claude_cmd = claude_cmd or _resolve_claude_cmd()
        # Если proxy явно не передан — берём из env (HTTPS_PROXY) или дефолт v2rayN.
        self._proxy_url = (
            proxy_url
            or os.environ.get("HTTPS_PROXY")
            or "http://127.0.0.1:10808"
        )
        self._timeout = timeout_sec

    async def run(self, text: str, request_id: str) -> SkillResult:
        if self._claude_cmd is None:
            logger.warning("code_assist_no_cli")
            return SkillResult(
                text=(
                    "Босс, Claude Code CLI не найден в системе. "
                    "Установи через `npm i -g @anthropic-ai/claude-code`."
                ),
                speakable=True,
            )

        task = _extract_task(text)
        if not task:
            return SkillResult(
                text="Босс, опиши задачу — что писать или чинить.",
                speakable=True,
            )

        # Mode auto-detect: если триггеры edit-mode сработали → даём
        # Claude full tool-set + cwd на JARVIS-репо.
        mode = "edit" if _EDIT_MODE_TRIGGERS.search(text) else "text"

        logger.info(
            "code_assist_start", request_id=request_id, task_len=len(task), mode=mode,
        )
        try:
            stdout, stderr, rc = await self._invoke_claude_cli(task, mode=mode)
        except asyncio.TimeoutError:
            logger.warning("code_assist_timeout", request_id=request_id)
            return SkillResult(
                text=(
                    f"Claude Code CLI думал дольше {int(self._timeout)} секунд "
                    "и не успел. Попробуй задачу проще или подожди и повтори."
                ),
                speakable=True,
            )
        except FileNotFoundError:
            logger.error("code_assist_cli_missing", path=str(self._claude_cmd))
            return SkillResult(
                text="Claude Code CLI пропал из системы. Проверь установку.",
                speakable=True,
            )
        except Exception as exc:  # noqa: BLE001 — diagnostics
            logger.error("code_assist_subprocess_error", error=str(exc))
            return SkillResult(
                text=f"Не получилось вызвать Claude Code CLI: {exc}",
                speakable=True,
            )

        if rc != 0:
            tail = (stderr or stdout or "").strip().splitlines()
            tail_text = "\n".join(tail[-8:]) if tail else "(пусто)"
            logger.warning(
                "code_assist_nonzero",
                request_id=request_id,
                rc=rc,
                stderr_tail=tail_text,
            )
            return SkillResult(
                text=(
                    f"Claude Code CLI завершился с кодом {rc}. "
                    f"Хвост ошибки:\n{tail_text}\n\n"
                    "Если 401 — проверь логин: `claude.cmd /login` "
                    "(с HTTPS_PROXY=http://127.0.0.1:10808)."
                ),
                speakable=True,
            )

        answer = (stdout or "").strip()
        if not answer:
            return SkillResult(
                text="Claude вернул пустой ответ, Босс.",
                speakable=True,
            )

        logger.info(
            "code_assist_done",
            request_id=request_id,
            stdout_len=len(answer),
        )
        # Speakable=False — код произносить бессмысленно, лучше показать в чате.
        return SkillResult(
            text=answer,
            speakable=False,
            data={
                "source": "claude_code_cli",
                "task": task[:200],
                "task_len": len(task),
            },
        )

    async def _invoke_claude_cli(
        self, task: str, mode: str = "text"
    ) -> tuple[str, str, int]:
        """Запустить claude.cmd -p <task> и вернуть (stdout, stderr, rc).

        mode='text' (default):
          - cwd = %TEMP% (ASCII)
          - без tools, без CLAUDE.md auto-discovery
          - Claude возвращает только текстовый ответ
          - timeout self._timeout (90s)

        mode='edit':
          - cwd = junction на JARVIS-репо (ASCII через mklink /J)
          - --add-dir <junction> — Claude видит весь репо
          - --dangerously-skip-permissions — применяет правки без подтверждений
          - tools: Read, Edit, Write, Bash, Grep, Glob (всё что нужно)
          - timeout 300s (правка + тесты могут занять минуты)
        """
        env = os.environ.copy()
        env["HTTPS_PROXY"] = self._proxy_url
        env["HTTP_PROXY"] = self._proxy_url
        env["PYTHONIOENCODING"] = "utf-8"

        if mode == "edit":
            junction = _ensure_repo_junction()
            if junction is None:
                # Fallback на text-mode если junction не создался
                logger.warning("code_assist_edit_fallback_to_text")
                mode = "text"

        if mode == "edit":
            cwd = str(junction)
            args = [
                str(self._claude_cmd),
                "-p",
                "--append-system-prompt", _APPEND_SYSTEM_PROMPT_EDIT,
                "--add-dir", str(junction),
                "--dangerously-skip-permissions",
            ]
            effective_timeout = max(self._timeout, 300.0)
        else:
            cwd = str(_SAFE_CWD)
            args = [
                str(self._claude_cmd),
                "-p",
                "--append-system-prompt", _APPEND_SYSTEM_PROMPT,
                "--exclude-dynamic-system-prompt-sections",
            ]
            effective_timeout = self._timeout

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=task.encode("utf-8")),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return stdout, stderr, proc.returncode or 0

    # ── L2 Tool-use ────────────────────────────────────────────────────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "code_assist",
            "description": (
                "Delegate a coding task to Claude Code CLI (`claude -p`). "
                "Two modes: 'text' returns code as text in chat (Boss "
                "copy-pastes), 'edit' lets Claude modify JARVIS repo "
                "files directly via tools (Read/Edit/Write/Bash). Uses "
                "the Boss's Max 20× quota, not API tokens. Don't use "
                "for chat-only questions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Plain-text description of the coding task. "
                            "Russian or English is fine. For 'edit' mode, "
                            "be specific about which files to change."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["text", "edit"],
                        "description": (
                            "'text' (default): Claude answers as plain "
                            "code+explanation. 'edit': Claude has full "
                            "tool access to JARVIS repo, modifies files, "
                            "can run tests. Use 'edit' only when explicitly "
                            "asked to change repo code."
                        ),
                    },
                },
                "required": ["task"],
            },
        }

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        task = (args.get("task") or "").strip()
        if not task:
            return SkillResult(
                text="Босс, для code_assist нужен непустой `task`.",
                speakable=True,
            )
        explicit_mode = (args.get("mode") or "").strip().lower()
        if explicit_mode in ("text", "edit"):
            # Bypass auto-detect — Claude явно сказал что хочет
            if self._claude_cmd is None:
                return SkillResult(
                    text="Claude Code CLI не найден.", speakable=True,
                )
            logger.info(
                "code_assist_start", request_id=request_id, task_len=len(task),
                mode=explicit_mode, via="L2",
            )
            try:
                stdout, stderr, rc = await self._invoke_claude_cli(
                    task, mode=explicit_mode,
                )
            except asyncio.TimeoutError:
                return SkillResult(
                    text="Claude Code CLI not responding within timeout.",
                    speakable=True,
                )
            except Exception as exc:  # noqa: BLE001
                return SkillResult(text=f"CLI error: {exc}", speakable=True)
            if rc != 0:
                return SkillResult(
                    text=f"CLI exited with {rc}:\n{(stderr or stdout)[:500]}",
                    speakable=True,
                )
            return SkillResult(
                text=(stdout or "").strip() or "(empty)",
                speakable=False,
                data={"source": "claude_code_cli", "mode": explicit_mode},
            )
        # Иначе — auto-detect через .run()
        return await self.run(task, request_id)

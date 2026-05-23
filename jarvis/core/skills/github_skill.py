"""Skill: GitHub — pull requests, issues, последние коммиты.

«Какие у меня PR», «open issues», «последние коммиты в RepoX».

Использует GitHub REST API через PAT (jarvis/.secrets/github_pat).
Тот же PAT что для auto-backup → no extra setup нужен.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)

GH_API = "https://api.github.com"
DEFAULT_TIMEOUT = 8.0


def _load_pat() -> str | None:
    """Читаем PAT из .secrets/github_pat (тот же что для auto-backup)."""
    # Workspace_dir = jarvis/ → .secrets/github_pat
    secrets_dir = Path(__file__).resolve().parents[2] / ".secrets"
    pat_file = secrets_dir / "github_pat"
    if pat_file.exists():
        return pat_file.read_text(encoding="utf-8").strip()
    return None


def _ago(iso: str) -> str:
    """Человеческий формат «5 минут назад» по ISO 8601."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - dt
    sec = int(delta.total_seconds())
    if sec < 60:
        return f"{sec} сек назад"
    if sec < 3600:
        return f"{sec // 60} мин назад"
    if sec < 86400:
        return f"{sec // 3600} ч назад"
    return f"{sec // 86400} дн назад"


_PR_PATTERNS = [
    re.compile(r"\b(?:какие|мои|открытые)\b.*?\b(?:pr|pull\s*request\w*|пиар\w*)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bpr\s+(?:ждут|висят|на\s+меня)\b", re.IGNORECASE),
    re.compile(r"\bpull\s+request\w*\b", re.IGNORECASE),
]
_ISSUE_PATTERNS = [
    re.compile(r"\b(?:какие|мои|открытые)\b.*?\b(?:issues?|тикет\w*)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bopen\s+issues?\b", re.IGNORECASE),
    re.compile(r"\bissues?\s+(?:в|для|на)\s+\S", re.IGNORECASE),
]
_COMMITS_PATTERNS = [
    re.compile(r"\bпоследние\s+коммиты\s+в\s+(\S+)", re.IGNORECASE),
    re.compile(r"\bкоммиты\s+(?:в|по)\s+(\S+)", re.IGNORECASE),
]


async def _gh_get(url: str, token: str, params: dict | None = None) -> list | dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
        r = await c.get(url, headers=headers, params=params or {})
        r.raise_for_status()
        return r.json()


class GitHubSkill(KeywordSkill):
    name = "github"
    keywords = [
        # «какие у меня PR» / «мои PR» / «открытые pull request'ы»
        r"\b(?:какие|мои|открытые)\b[^\n]{0,30}\b(?:pr|pull\s*request\w*|пиар\w*)\b",
        r"\bpr\s+(?:ждут|висят|на\s+меня)\b",
        r"\bpull\s+request\w*\b",
        # «какие у меня issues»
        r"\b(?:какие|мои|открытые)\b[^\n]{0,30}\b(?:issues?|тикет\w*)\b",
        r"\bopen\s+issues?\b",
        r"\bissues?\s+(?:в|для|на)\s+\S",
        r"\bпоследние\s+коммиты\b",
        r"\bкоммиты\s+(?:в|по)\b",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._token = _load_pat()

    async def run(self, text: str, request_id: str) -> SkillResult:
        if not self._token:
            return SkillResult(
                text="Босс, GitHub PAT не найден в .secrets/github_pat.",
                speakable=True,
            )

        # PR — самое частое
        if any(p.search(text) for p in _PR_PATTERNS):
            return await self._my_open_prs()

        # Issues
        if any(p.search(text) for p in _ISSUE_PATTERNS):
            return await self._my_open_issues()

        # Commits в конкретном repo
        for p in _COMMITS_PATTERNS:
            m = p.search(text)
            if m:
                repo = m.group(1).strip().rstrip(".!?,")
                return await self._recent_commits(repo)

        return SkillResult(text="Босс, не понял GitHub-команду.", speakable=True)

    async def _my_open_prs(self) -> SkillResult:
        """Все открытые PR где я автор."""
        try:
            data = await _gh_get(
                f"{GH_API}/search/issues",
                self._token,
                params={"q": "is:pr is:open author:@me", "sort": "updated", "order": "desc", "per_page": 10},
            )
        except httpx.HTTPError as e:
            return SkillResult(text=f"GitHub не ответил: {e}", speakable=True)

        items = data.get("items", [])
        if not items:
            return SkillResult(text="Открытых PR на твоём аккаунте нет.", speakable=True)

        lines = [f"Открытых PR: {len(items)}"]
        for it in items[:10]:
            repo = it.get("repository_url", "").split("/repos/", 1)[-1]
            lines.append(f"• [{repo}] #{it['number']} {it['title']} — обновлено {_ago(it['updated_at'])}")
        return SkillResult(text="\n".join(lines), speakable=True)

    async def _my_open_issues(self) -> SkillResult:
        try:
            data = await _gh_get(
                f"{GH_API}/search/issues",
                self._token,
                params={"q": "is:issue is:open author:@me", "sort": "updated", "order": "desc", "per_page": 10},
            )
        except httpx.HTTPError as e:
            return SkillResult(text=f"GitHub не ответил: {e}", speakable=True)

        items = data.get("items", [])
        if not items:
            return SkillResult(text="Открытых issues на твоём аккаунте нет.", speakable=True)

        lines = [f"Открытых issues: {len(items)}"]
        for it in items[:10]:
            repo = it.get("repository_url", "").split("/repos/", 1)[-1]
            lines.append(f"• [{repo}] #{it['number']} {it['title']} — {_ago(it['updated_at'])}")
        return SkillResult(text="\n".join(lines), speakable=True)

    async def _recent_commits(self, repo: str) -> SkillResult:
        """Последние коммиты в repo. Repo указывается как owner/name либо просто name (тогда @me)."""
        if "/" not in repo:
            # Получаем имя пользователя для шорт-форм
            try:
                me = await _gh_get(f"{GH_API}/user", self._token)
                repo = f"{me['login']}/{repo}"
            except Exception:
                return SkillResult(text="Не смог понять чей это repo. Скажи как owner/name.", speakable=True)

        try:
            data = await _gh_get(f"{GH_API}/repos/{repo}/commits", self._token, params={"per_page": 5})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return SkillResult(text=f"Repo {repo} не найден.", speakable=True)
            return SkillResult(text=f"GitHub вернул {e.response.status_code}.", speakable=True)
        except httpx.HTTPError as e:
            return SkillResult(text=f"GitHub не ответил: {e}", speakable=True)

        if not data:
            return SkillResult(text=f"В {repo} коммитов не нашёл.", speakable=True)
        lines = [f"Последние коммиты в {repo}:"]
        for c in data[:5]:
            msg = (c.get("commit", {}).get("message") or "").split("\n", 1)[0]
            author = c.get("commit", {}).get("author", {}).get("name", "?")
            when = _ago(c.get("commit", {}).get("author", {}).get("date", ""))
            lines.append(f"• {msg[:80]} — {author}, {when}")
        return SkillResult(text="\n".join(lines), speakable=True)

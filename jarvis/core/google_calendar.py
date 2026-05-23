"""Google Calendar integration: OAuth Desktop flow + read/create events.

Файлы:
    jarvis/.secrets/google_credentials.json  — OAuth client (от тебя)
    jarvis/.secrets/google_token.json        — refresh-token (создаётся при первом auth)

Первый запуск: вызывается ensure_authorized() — открывает браузер, ты подтверждаешь.
Дальше работает молча — токен авто-обновляется.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.logging import get_logger

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

CRED_PATH = Path(__file__).resolve().parents[1] / ".secrets" / "google_credentials.json"
TOKEN_PATH = Path(__file__).resolve().parents[1] / ".secrets" / "google_token.json"


class GoogleCalendar:
    """Тонкая обёртка над googleapiclient + OAuth.

    Использование:
        gc = GoogleCalendar()
        await gc.ensure_authorized()   # один раз, открывает браузер
        events = await gc.list_today()
    """

    def __init__(self, default_calendar: str = "primary") -> None:
        self._cal_id = default_calendar
        self._service = None

    def _load_credentials(self):
        """Загружает saved token или None."""
        if not TOKEN_PATH.exists():
            return None
        try:
            from google.oauth2.credentials import Credentials
            return Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            logger.warning("gcal_token_load_failed", error=str(e))
            return None

    def _save_credentials(self, creds) -> None:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        logger.info("gcal_token_saved", path=str(TOKEN_PATH))

    def _ensure_service_sync(self):
        """Авторизуется (interactive первый раз) и возвращает googleapiclient.discovery service."""
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as e:
            raise RuntimeError("google libs не установлены: uv pip install google-api-python-client google-auth-oauthlib") from e

        creds = self._load_credentials()
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_credentials(creds)
                logger.info("gcal_token_refreshed")
            except Exception as e:
                logger.warning("gcal_refresh_failed", error=str(e))
                creds = None

        if not creds or not creds.valid:
            if not CRED_PATH.exists():
                raise RuntimeError(
                    f"Нет {CRED_PATH}. Скачай OAuth client.json из Google Cloud Console и положи туда."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CRED_PATH), SCOPES)
            # InstalledAppFlow поднимает localhost-сервер на случайном порту,
            # открывает браузер с consent screen, ловит callback.
            creds = flow.run_local_server(port=0)
            self._save_credentials(creds)
            logger.info("gcal_first_auth_done")

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def ensure_authorized(self) -> None:
        """Async wrapper. Вызвать ОДИН раз в setup — после этого работает молча."""
        await asyncio.to_thread(self._ensure_service_sync)

    # ── Read API ───────────────────────────────────────────────────────

    async def list_today(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._list_range(datetime.now(), days=1, limit=limit)

    async def list_tomorrow(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._list_range(datetime.now() + timedelta(days=1), days=1, limit=limit)

    async def list_week(self, limit: int = 30) -> list[dict[str, Any]]:
        return await self._list_range(datetime.now(), days=7, limit=limit)

    async def _list_range(self, start: datetime, days: int, limit: int) -> list[dict[str, Any]]:
        def _do():
            svc = self._ensure_service_sync()
            start_dt = datetime(start.year, start.month, start.day, 0, 0, 0)
            end_dt = start_dt + timedelta(days=days)
            res = svc.events().list(
                calendarId=self._cal_id,
                timeMin=start_dt.astimezone().isoformat(),
                timeMax=end_dt.astimezone().isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=limit,
            ).execute()
            return res.get("items", [])
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.error("gcal_list_failed", error=str(e))
            return []

    # ── Write API ──────────────────────────────────────────────────────

    async def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any] | None:
        if end is None:
            end = start + timedelta(hours=1)

        def _do():
            svc = self._ensure_service_sync()
            body = {
                "summary": summary,
                "description": description,
                "location": location,
                "start": {"dateTime": start.astimezone().isoformat(), "timeZone": "Europe/Moscow"},
                "end": {"dateTime": end.astimezone().isoformat(), "timeZone": "Europe/Moscow"},
            }
            return svc.events().insert(calendarId=self._cal_id, body=body).execute()
        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.error("gcal_create_failed", error=str(e))
            return None


# ── Helpers ───────────────────────────────────────────────────────────


def format_events_human(events: list[dict[str, Any]]) -> str:
    """Превратить список событий в читаемый русский текст."""
    if not events:
        return "Событий нет."
    lines: list[str] = []
    for ev in events:
        summary = ev.get("summary", "(без названия)")
        start = ev.get("start", {})
        when = start.get("dateTime") or start.get("date") or ""
        try:
            if "T" in when:
                dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
                when_human = dt.astimezone().strftime("%H:%M")
            else:
                when_human = "весь день"
        except Exception:
            when_human = when
        loc = ev.get("location", "")
        if loc:
            lines.append(f"• {when_human} — {summary} ({loc})")
        else:
            lines.append(f"• {when_human} — {summary}")
    return "\n".join(lines)

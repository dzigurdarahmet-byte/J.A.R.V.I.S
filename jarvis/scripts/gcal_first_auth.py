"""Первая авторизация Google Calendar — открывает браузер для consent.

После первого запуска появляется jarvis/.secrets/google_token.json (refresh token),
дальше всё работает молча.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.google_calendar import GoogleCalendar, format_events_human  # noqa: E402


async def main() -> int:
    cal = GoogleCalendar()
    print("Запускаю OAuth flow… Откроется браузер — подтверди доступ.")
    await cal.ensure_authorized()
    print("OAuth OK. Токен сохранён.")

    print("\n=== События на сегодня ===")
    today = await cal.list_today()
    print(format_events_human(today))

    print("\n=== На завтра ===")
    tomorrow = await cal.list_tomorrow()
    print(format_events_human(tomorrow))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Точка запуска Telegram-канала.

Запуск из jarvis/:
    .venv\\Scripts\\python.exe run_telegram.py
"""

import asyncio

from channels.telegram import run_bot

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass

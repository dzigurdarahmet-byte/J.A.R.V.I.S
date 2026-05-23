"""Точка запуска Web HUD.

Запуск из jarvis/:
    .venv\\Scripts\\python.exe run_web_hud.py

Открыть в Edge:
    http://localhost:8000
"""

import asyncio

from channels.web_hud import run_web_hud

if __name__ == "__main__":
    try:
        asyncio.run(run_web_hud())
    except (KeyboardInterrupt, SystemExit):
        pass

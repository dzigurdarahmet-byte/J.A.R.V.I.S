"""Точка запуска local_voice канала.

Запуск из jarvis/:
    .venv\\Scripts\\python.exe run_voice.py            # VAD-mode по умолчанию
    .venv\\Scripts\\python.exe run_voice.py ptt        # PTT-mode
    .venv\\Scripts\\python.exe run_voice.py wake       # Wake-word «Hey Jarvis»

Hotkeys (глобальные, работают из любого приложения):
    F12         — пауза/возобновление слушания
    F11         — переключение VAD → PTT → WAKE → VAD
    Ctrl+Space  — push-to-talk (только в PTT-режиме): зажать, говорить, отпустить
"""

import asyncio
import sys

from channels.local_voice import run_local_voice

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "vad"
    try:
        asyncio.run(run_local_voice(mode=mode))
    except (KeyboardInterrupt, SystemExit):
        pass

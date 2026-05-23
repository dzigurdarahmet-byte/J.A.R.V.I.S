"""E2E: проверяем что новые skills работают через router в HUD."""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import httpx, websockets

WS_URL = "ws://127.0.0.1:8000/ws"
STATUS_URL = "http://127.0.0.1:8000/api/status"


async def ask(text: str, timeout: float = 25.0) -> str:
    """Послать text в /ws и вернуть первый assistant_reply."""
    async with websockets.connect(WS_URL) as ws:
        await asyncio.sleep(0.3)  # дать snapshot прийти
        await ws.send(json.dumps({"text": text}))
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            event = payload.get("event")
            if not event:
                continue
            if event.get("type") == "assistant_reply" and event.get("channel") == "web_hud":
                return (event.get("data") or {}).get("text", "")
        return "[TIMEOUT]"


async def main() -> int:
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            r = await c.get(STATUS_URL)
            r.raise_for_status()
        except Exception as e:
            print(f"HUD down: {e}")
            return 2

    cases = [
        # TODO
        "Добавь задачу проверить аватара",
        "Добавь задачу позвонить врачу",
        "Покажи задачи",
        "Выполнил #1",
        "Покажи задачи",
        # REMINDERS
        "Напомни через 1 минуту тест напоминания",
        "Покажи напоминания",
        # WEB SEARCH
        "Найди в интернете рецепт борща",
    ]
    for q in cases:
        print(f"\n--- {q!r} ---")
        reply = await ask(q)
        # Если ответ длинный — обрезаем
        if len(reply) > 350:
            reply = reply[:350] + "...[trunc]"
        print(reply)

    return 0


sys.exit(asyncio.run(main()))

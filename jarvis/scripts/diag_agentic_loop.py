"""Тест F1 agentic loop: Claude должен сделать chain из >1 tool_use в одном вызове.

Сценарий: «Найди в интернете кратко про последний релиз FastAPI и положи название в буфер»
— Claude должен:
  1. web_search(query="FastAPI latest release")
  2. clipboard_write(text="FastAPI X.Y.Z")
  3. Финальный текстовый ответ.
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import httpx, websockets

WS_URL = "ws://127.0.0.1:8000/ws"


async def ask(text: str, timeout: float = 60.0) -> str:
    """Послать text в /ws, дождаться assistant_reply (через L2 цепочку)."""
    async with websockets.connect(WS_URL) as ws:
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({"text": text}))
        deadline = asyncio.get_event_loop().time() + timeout
        seen_levels: list[str] = []
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                continue
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            event = payload.get("event")
            if not event:
                continue
            kind = event.get("type")
            data = event.get("data") or {}
            if kind == "routed":
                seen_levels.append(data.get("level", "?") + ":" + str(data.get("intent")))
            if kind == "skill_result" and data.get("tool_calls"):
                # log tool chain
                for tc in data["tool_calls"]:
                    print(f"   ⚙ tool: {tc.get('tool')} args={tc.get('input')}")
            if kind == "assistant_reply" and event.get("channel") == "web_hud":
                final = data.get("text", "")
                print(f"   routed levels: {seen_levels}")
                return final
        return "[TIMEOUT]"


async def main() -> int:
    # 0) Health
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get("http://127.0.0.1:8000/api/status")
        r.raise_for_status()
        print(f"HUD ok: {r.json().get('ok')}\n")

    cases = [
        # Простой chain — 2 tool calls
        "Какая сейчас погода в Москве и в Лондоне?",
        # Реальный agentic chain — search + clipboard
        "Найди в интернете кратко про последний релиз Python и положи название в буфер.",
        # GitHub + todo
        "Покажи мои открытые PR на GitHub и если есть — самый верхний добавь в задачи как 'смержить'.",
    ]
    for q in cases:
        print(f"\n=== {q!r} ===")
        reply = await ask(q)
        print(f"\n   FINAL → {reply[:400]}")
    return 0


sys.exit(asyncio.run(main()))

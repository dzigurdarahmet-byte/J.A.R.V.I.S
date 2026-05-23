"""Тест явных multi-skill chains через L2."""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import websockets

async def ask(text: str, timeout: float = 60.0) -> tuple[str, list[str], list[dict]]:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({"text": text}))
        deadline = asyncio.get_event_loop().time() + timeout
        levels, tools = [], []
        while asyncio.get_event_loop().time() < deadline:
            try: msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError: continue
            try: payload = json.loads(msg)
            except json.JSONDecodeError: continue
            event = payload.get("event")
            if not event: continue
            kind = event.get("type"); data = event.get("data") or {}
            if kind == "routed":
                levels.append(f"{data.get('level','?')}:{data.get('intent')}")
            if kind == "skill_result" and data.get("tool_calls"):
                for tc in data["tool_calls"]:
                    tools.append({"tool": tc.get('tool'), "input": tc.get('input')})
            if kind == "assistant_reply" and event.get("channel") == "web_hud":
                return data.get("text",""), levels, tools
        return "[TIMEOUT]", levels, tools

async def main():
    cases = [
        "Покажи мои задачи и какая сейчас погода в Москве?",
        "Сколько свободно памяти и какие у меня PR на GitHub?",
        "Запомни задачу позвонить Маше и напомни через 30 минут об этом",
    ]
    for q in cases:
        print(f"\n=== {q!r} ===")
        reply, levels, tools = await ask(q)
        print(f"   levels: {levels}")
        for t in tools:
            print(f"   ⚙ {t['tool']}({t['input']})")
        print(f"   → {reply[:300]}")

asyncio.run(main())

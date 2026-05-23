"""Triggers briefing via WS, prints result."""
from __future__ import annotations

import asyncio
import json
import sys

import websockets


async def run(prompt: str) -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws", ping_interval=None) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)
        await ws.send(json.dumps({"text": prompt}))
        deadline = asyncio.get_event_loop().time() + 30.0
        while True:
            rem = deadline - asyncio.get_event_loop().time()
            if rem <= 0:
                break
            msg = await asyncio.wait_for(ws.recv(), timeout=rem)
            ev = json.loads(msg).get("event") or {}
            kind = ev.get("type")
            data = ev.get("data") or {}
            if kind == "routed":
                print(f"=== ROUTED ===  intent={data.get('intent')} level={data.get('level')}")
            elif kind == "assistant_reply":
                print("=== ANSWER ===")
                print(data.get("text"))
                return


async def main() -> int:
    await run("Дай брифинг")
    print()
    print("---" * 25)
    print()
    await run("Дай мне вечерний брифинг")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

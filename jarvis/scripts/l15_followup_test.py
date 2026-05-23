"""L1.5 follow-up тест: цепочка реплик через ОДИН WS-соединение.

Проверяем что:
- «Какая погода в Москве?» → L1 weather (моментально)
- «А в Сыктывкаре?» → L1.5 weather (моментально, БЕЗ Claude)
- «А завтра?» → L1.5 weather_forecast (моментально)
- «А курс доллара?» → L1 currency (новый topic)
- «А юань?» → L1.5 currency (короткое продолжение)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

URL = "ws://127.0.0.1:8000/ws"


async def ask(ws, prompt: str) -> dict:
    """Шлёт реплику, ждёт routed + assistant_reply. Возвращает {level, intent, reply, ms}."""
    t0 = time.monotonic()
    await ws.send(json.dumps({"text": prompt}))
    level = intent = reply = None
    deadline = asyncio.get_event_loop().time() + 30.0
    while True:
        rem = deadline - asyncio.get_event_loop().time()
        if rem <= 0:
            break
        msg = await asyncio.wait_for(ws.recv(), timeout=rem)
        ev = json.loads(msg).get("event") or {}
        kind = ev.get("type")
        data = ev.get("data") or {}
        if kind == "routed" and data.get("intent") != "user_input":
            level = data.get("level")
            intent = data.get("intent")
        elif kind == "assistant_reply":
            reply = data.get("text")
            break
    return {"level": level, "intent": intent, "reply": reply, "ms": int((time.monotonic() - t0) * 1000)}


async def main() -> int:
    async with websockets.connect(URL, ping_interval=None) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)  # snapshot

        dialog = [
            "Какая погода в Москве?",
            "А в Сыктывкаре?",
            "А во Владивостоке?",
            "А завтра?",
            "Курс доллара?",
            "А юань?",
            "А биткоин?",
        ]
        expected = [
            "L1",     # weather
            "L1.5",   # weather, follow-up city
            "L1.5",   # weather, ещё город
            "L1.5",   # weather_forecast (time marker)
            "L1",     # currency (новый topic)
            "L1.5",   # currency (валюта-продолжение)
            "L1",     # crypto (CryptoSkill сам ловит «биткоин» через L1)
        ]

        print(f"{'St':4} {'Prompt':30} {'Lvl':6} {'Intent':18} {'ms':5} Reply")
        print("-" * 130)
        ok = 0
        for prompt, exp in zip(dialog, expected, strict=True):
            r = await ask(ws, prompt)
            mark = "OK" if r["level"] == exp else "FAIL"
            if mark == "OK":
                ok += 1
            print(f"{mark:4} {prompt:30.30} {str(r['level']):6} {str(r['intent']):18} {r['ms']:>4}  {(r['reply'] or '')[:70]}")
        print(f"\nMatched: {ok}/{len(dialog)}")
        return 0 if ok == len(dialog) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)

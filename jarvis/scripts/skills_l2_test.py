"""L2 tool-use тест: контекстный follow-up «А в Сыктывкаре?».

Шлёт две реплики подряд через WebSocket. Ожидаем что Claude поймёт контекст
и для второго вопроса позовёт get_weather(city='Сыктывкар') через tool_use,
вместо галлюцинации «у меня нет инструмента».
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

URL = "ws://127.0.0.1:8000/ws"


async def run_dialog(prompts: list[str]) -> None:
    async with websockets.connect(URL, ping_interval=None) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)  # snapshot
        for prompt in prompts:
            print(f"\n>>> Босс: {prompt}")
            await ws.send(json.dumps({"text": prompt}))
            level = intent = None
            tool_calls = None
            reply = None
            deadline = asyncio.get_event_loop().time() + 30.0
            while True:
                rem = deadline - asyncio.get_event_loop().time()
                if rem <= 0:
                    print("    [timeout]")
                    break
                msg = await asyncio.wait_for(ws.recv(), timeout=rem)
                ev = json.loads(msg).get("event") or {}
                kind = ev.get("type")
                data = ev.get("data") or {}
                if kind == "routed":
                    intent = data.get("intent")
                    level = data.get("level")
                elif kind == "skill_result" and "tool_calls" in data:
                    tool_calls = data["tool_calls"]
                elif kind == "assistant_reply":
                    reply = data.get("text")
                    break
            print(f"    routed: intent={intent} level={level}")
            if tool_calls:
                for tc in tool_calls:
                    print(f"    tool: {tc.get('tool')}({tc.get('input')}) -> {(tc.get('result') or '')[:80]}")
            print(f"    Джарвис: {reply}")


async def main() -> None:
    await run_dialog([
        "Какая погода в Москве?",
        "А в Сыктывкаре?",
        "Сколько будет 17 умножить на 31?",
        "Запомни: код от подъезда 7634",
        "Что ещё ты умеешь?",
    ])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)

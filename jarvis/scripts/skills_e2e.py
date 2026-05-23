"""End-to-end проверка всех 20 скиллов через работающий Web HUD WebSocket.

Запускать ПОСЛЕ старта `python run_web_hud.py`.
Шлёт по фразе на каждый скилл, ждёт assistant_reply, собирает intent + level + ответ.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

CASES: list[tuple[str, str | None]] = [
    # (text, expected_intent or None для llm-fallback)
    ("Который час?",                                "time"),
    ("Какая сегодня дата?",                          "date"),
    ("Который час в Токио?",                         "timezone"),
    ("Поставь таймер на 1 минуту",                   "timer"),
    ("Поставь будильник на 7:30",                    "alarm"),
    ("Какая погода в Москве?",                       "weather"),
    ("Прогноз погоды в Москве на завтра",            "weather_forecast"),
    ("Курс доллара?",                                "currency"),
    ("Сколько стоит биткоин?",                       "crypto"),
    ("Запиши: купить молоко",                        "note"),
    ("Запомни: ИНН для подписки 7707083893",         "remember"),
    ("Забудь заметку про молоко",                    "forget"),
    ("Покажи мои заметки",                           "notes_list"),
    ("Расскажи про Эйнштейна",                       "wiki"),
    ("Какие новости?",                               "news"),
    ("Сколько будет 17 плюс 25?",                    "calc"),
    ("100 км в мили",                                "convert"),
    ("Подбрось монетку",                             "random"),
    ("Переведи на английский: добрый день",          "translate"),
    ("Статус",                                       "status"),
]

URL = "ws://127.0.0.1:8000/ws"
TIMEOUT_PER_CASE = 12.0  # сек — Wiki/Translate API могут тормозить


async def run_case(ws, text: str) -> tuple[str | None, str | None, str | None]:
    """Return (intent, level, reply)."""
    await ws.send(json.dumps({"text": text}))
    intent: str | None = None
    level: str | None = None
    reply: str | None = None
    deadline = asyncio.get_event_loop().time() + TIMEOUT_PER_CASE
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        ev = msg.get("event") or {}
        kind = ev.get("type")
        data = ev.get("data") or {}
        if kind == "routed":
            intent = data.get("intent")
            level = data.get("level")
        elif kind == "assistant_reply":
            reply = data.get("text")
            break
    return intent, level, reply


async def main() -> int:
    print(f"Connecting to {URL}")
    try:
        async with websockets.connect(URL, ping_interval=None) as ws:
            # snapshot
            await asyncio.wait_for(ws.recv(), timeout=5.0)
            print("Connected. Running 20 cases...\n")
            rows: list[tuple[str, str, str, str, str]] = []
            ok = 0
            for text, expected in CASES:
                intent, level, reply = await run_case(ws, text)
                actual = intent or "llm"
                status = "OK" if actual == expected else ("OK*" if expected is None else "FAIL")
                if status.startswith("OK"):
                    ok += 1
                rows.append((status, text, expected or "(llm)", f"{actual}/{level or '-'}", (reply or "<TIMEOUT>")[:90]))
            # print table
            print(f"{'St':4} {'Phrase':45} {'Exp':18} {'Got':14} Reply")
            print("-" * 140)
            for r in rows:
                print(f"{r[0]:4} {r[1]:45.45} {r[2]:18} {r[3]:14} {r[4]}")
            print(f"\nPassed: {ok}/{len(CASES)}")
            return 0 if ok == len(CASES) else 1
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

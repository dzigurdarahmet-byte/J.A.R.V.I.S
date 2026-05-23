"""E2E тест авто-говорения D17.

Поднимает два WS-клиента к http://127.0.0.1:8000:
  A — Chat-канал (пишет 'сколько будет 2 плюс 2')
  B — Avatar (слушает ASSISTANT_REPLY, вызывает /api/avatar/speak)

Если pipeline работает: B получает assistant_reply от router → дёргает
speak → получает 200 с visemes — тест зелёный.

Запуск:
    .\\.venv\\Scripts\\python.exe scripts\\diag_avatar_e2e.py
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

HOST = "127.0.0.1:8000"
WS_URL = f"ws://{HOST}/ws"
SPEAK_URL = f"http://{HOST}/api/avatar/speak"

# Сюда складываем что выловили
results = {
    "user_input_sent": False,
    "assistant_reply_received": False,
    "assistant_text": None,
    "speak_status": None,
    "speak_visemes": None,
    "errors": [],
}


async def chat_client():
    """Имитирует браузер на главной вкладке HUD — шлёт текст."""
    async with websockets.connect(WS_URL) as ws:
        # Подождём snapshot
        await asyncio.sleep(0.5)
        # calc skill отвечает локально, без Claude — быстрее и стабильнее
        msg = json.dumps({"text": "посчитай 2 + 2"})
        await ws.send(msg)
        results["user_input_sent"] = True
        print(f"[chat ] sent: {msg}", flush=True)
        # Слушаем чуть-чуть, чтобы не закрыть до broadcast
        try:
            async for raw in ws:
                # ничего не делаем, просто держим соединение
                pass
        except websockets.exceptions.ConnectionClosed:
            pass


async def avatar_client():
    """Имитирует avatar.html — слушает ASSISTANT_REPLY → speak."""
    async with websockets.connect(WS_URL) as ws:
        print("[avatar] WS connected", flush=True)
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = payload.get("event")
            if not event:
                continue
            if event.get("type") != "assistant_reply":
                continue
            if event.get("channel") != "web_hud":
                continue
            text = (event.get("data") or {}).get("text", "").strip()
            if not text:
                continue
            print(f"[avatar] got assistant_reply ({len(text)} chars): {text[:80]}", flush=True)
            results["assistant_reply_received"] = True
            results["assistant_text"] = text

            # Дёргаем speak — ровно то же что делает avatar.html
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(SPEAK_URL, json={"text": text, "emotion": "neutral"})
            results["speak_status"] = r.status_code
            print(f"[avatar] /api/avatar/speak → {r.status_code}", flush=True)
            if r.status_code == 200:
                d = r.json()
                results["speak_visemes"] = len(d.get("visemes", []))
                print(f"[avatar] OK — {d.get('duration_ms')}ms, {results['speak_visemes']} visemes", flush=True)
            else:
                print(f"[avatar] FAIL body: {r.text[:200]}", flush=True)
                results["errors"].append(f"speak status {r.status_code}: {r.text[:200]}")
            return


async def main() -> int:
    print(f"=== E2E D17 auto-speak ({time.strftime('%H:%M:%S')}) ===", flush=True)
    # Проверка что HUD живой
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"http://{HOST}/api/status")
            r.raise_for_status()
            print(f"[bootstrap] HUD status: {r.json().get('ok')}", flush=True)
    except Exception as e:
        print(f"[bootstrap] HUD недоступен: {type(e).__name__}: {e!r}", flush=True)
        return 2

    avatar_task = asyncio.create_task(avatar_client())
    chat_task = asyncio.create_task(chat_client())
    # Avatar должен поймать reply и сделать speak — даём до 60 сек
    # (Claude API может быть overloaded и долго отвечать)
    try:
        await asyncio.wait_for(avatar_task, timeout=60.0)
    except asyncio.TimeoutError:
        print("[main] timeout — assistant_reply не пришёл за 20 сек", flush=True)
        results["errors"].append("timeout waiting for assistant_reply")
    finally:
        chat_task.cancel()
        try: await chat_task
        except (asyncio.CancelledError, Exception): pass

    print("\n=== RESULTS ===", flush=True)
    for k, v in results.items():
        if k == "assistant_text" and v:
            v = v[:100] + ("..." if len(v) > 100 else "")
        print(f"  {k}: {v}")

    ok = (results["user_input_sent"]
          and results["assistant_reply_received"]
          and results["speak_status"] == 200
          and (results["speak_visemes"] or 0) > 0)
    print(f"\n{'[OK]' if ok else '[FAIL]'} E2E {'passed' if ok else 'failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

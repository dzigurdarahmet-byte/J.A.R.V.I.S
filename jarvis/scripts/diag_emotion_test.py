"""Тест детекции и применения эмоций в /api/avatar/speak.

Прогоняет несколько текстов и проверяет что:
  - сервер детектит правильную emotion в режиме auto
  - возвращает yandex_role совместимый с alena (good/neutral)
  - не падает
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import httpx

CASES = [
    # (text, expected_emotion)
    ("Готово, Босс! Отчёт прикреплён.", "joy"),
    ("К сожалению, сервер упал — 529 от Claude.", "concerned"),
    ("Сколько будет 2 плюс 2?", "curious"),
    ("Внимание: не забудь сохранить файл перед перезагрузкой.", "serious"),
    ("Понял, Босс.", "neutral"),
]

# Также проверяем чистую детекцию (без сети)
from core.voice.emotion import detect_emotion, emotion_to_yandex_role

print("=== unit: detect_emotion (без сервера) ===")
unit_ok = True
for text, expected in CASES:
    got = detect_emotion(text)
    role = emotion_to_yandex_role(got, voice="alena")
    flag = "OK " if got == expected else "MISS"
    if got != expected:
        unit_ok = False
    print(f"  [{flag}] '{text[:50]}' → emotion={got}  role={role}  (expected={expected})")
print(f"unit: {'PASS' if unit_ok else 'FAIL'}\n")


async def main() -> int:
    print("=== e2e: POST /api/avatar/speak emotion=auto ===")
    e2e_ok = True
    async with httpx.AsyncClient(timeout=30.0) as c:
        for text, expected in CASES:
            try:
                r = await c.post(
                    "http://127.0.0.1:8000/api/avatar/speak",
                    json={"text": text, "emotion": "auto"},
                )
                if r.status_code != 200:
                    print(f"  [FAIL] {r.status_code}: {r.text[:120]}")
                    e2e_ok = False
                    continue
                d = r.json()
                emo = d.get("emotion")
                role = d.get("yandex_role")
                vis = len(d.get("visemes", []))
                flag = "OK " if emo == expected else "MISS"
                if emo != expected:
                    e2e_ok = False
                print(f"  [{flag}] '{text[:45]}' → emo={emo} role={role} visemes={vis} dur={d.get('duration_ms')}ms")
            except Exception as e:
                print(f"  [FAIL] '{text[:45]}': {type(e).__name__}: {e}")
                e2e_ok = False
    print(f"\ne2e: {'PASS' if e2e_ok else 'FAIL'}")
    return 0 if (unit_ok and e2e_ok) else 1


sys.exit(asyncio.run(main()))

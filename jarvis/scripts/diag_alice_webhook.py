"""Unit-тест webhook /api/alice/webhook через FastAPI TestClient.

Сценарии:
  1. session.new → приветствие
  2. "выход" → end_session=true
  3. "сколько времени" → отвечает текст от TimeSkill
  4. "добавь задачу X" → отвечает от TodoSkill
  5. чужой skill_id (если ALICE_SKILL_ID настроен) → отбой
"""
from __future__ import annotations
import sys, json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from fastapi.testclient import TestClient

from channels.web_hud.server import build_app
from core.config import settings
from core.providers import build_smart_provider
from core.memory import MemoryManager

WORKSPACE_DIR = HERE / "workspace"

claude = build_smart_provider(settings)
memory = MemoryManager(workspace_dir=WORKSPACE_DIR)
app = build_app(claude, memory)
client = TestClient(app)

EXPECTED_ID = settings.alice_skill_id or ""


def mk_payload(text: str, *, new: bool = False, skill_id: str = "") -> dict:
    return {
        "meta": {"locale": "ru-RU", "timezone": "Europe/Moscow"},
        "session": {
            "new": new,
            "message_id": 0 if new else 1,
            "session_id": "test-session-abc",
            "skill_id": skill_id or "test-skill-id",
            "user_id": "test-user",
        },
        "request": {
            "command": text,
            "original_utterance": text,
            "type": "SimpleUtterance",
        },
        "state": {},
        "version": "1.0",
    }


def call(payload: dict) -> dict:
    r = client.post("/api/alice/webhook", json=payload)
    return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}


def show(name: str, result: dict) -> None:
    print(f"\n[{name}] status={result['status']}")
    if isinstance(result["body"], dict):
        resp = result["body"].get("response", {})
        print(f"  text: {resp.get('text')!r}")
        print(f"  end_session: {resp.get('end_session')}")
    else:
        print(f"  body: {result['body'][:200]}")


print("=== Alice webhook smoke ===\n")

show("session.new (приветствие)", call(mk_payload("", new=True)))
show("выход", call(mk_payload("выход")))
show("какой час", call(mk_payload("какой сейчас час")))
show("добавь задачу", call(mk_payload("добавь задачу проверить алису")))
show("покажи задачи", call(mk_payload("покажи задачи")))
show("пустая команда", call(mk_payload("")))
if EXPECTED_ID:
    show("чужой skill_id", call(mk_payload("привет", skill_id="WRONG-ID")))
else:
    print("\n[skill_id mismatch] пропущен — ALICE_SKILL_ID не задан в .env")

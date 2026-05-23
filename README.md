# J.A.R.V.I.S.

Персональный голосовой ИИ-ассистент. Один пользователь — Босс.

## Состояние

🟡 Phase 1 MVP — в разработке (старт 21 мая 2026).

См. `JARVIS_Phase1_Plan.md` для детального плана.

## Стек

- **Brain (primary):** Claude Sonnet 4.6 через Anthropic API
- **Brain (on-demand):** Ollama @ Selectel GPU (когда понадобится локальная LLM)
- **STT:** Faster-Whisper Small (CPU)
- **TTS:** Silero v3 RU (CPU, мужской голос)
- **Event Bus:** Redis Streams (по v5.3)
- **Storage:** SQLite (Tier 1+2), ChromaDB (Tier 3, отложено)
- **API:** FastAPI + WebSocket
- **Каналы MVP:** Web HUD, Telegram (@jarvis_boss_sergey_bot), CLI

## Структура

```
jarvis/
├── core/                  # ядро
│   ├── event_bus/         # Redis Streams (v5.3)
│   ├── router/            # 4-уровневый роутер
│   ├── memory/            # MEMORY.md + daily logs
│   ├── security/          # PromptGuard, sanitizers
│   ├── offline/           # OfflineDetector + Queue
│   ├── providers/         # Provider Registry
│   ├── voice/             # Whisper + Silero
│   └── logging/           # structlog
├── skills/                # 10 базовых
├── channels/              # web_hud, telegram, cli
├── workspace/             # SOUL.md, USER.md, MEMORY.md, daily/
├── tests/
├── docs/
├── scripts/
└── pyproject.toml
```

## Запуск (когда дойдём)

```powershell
cd jarvis
uv sync                      # установка зависимостей
uv run uvicorn core.app:app --reload --port 8000
```

## Безопасность

- `.env` НЕ коммитится (см. `.gitignore`)
- `.env.example` — шаблон без секретов
- SOUL.md имеет SHA-256 чексумму, проверяется при старте
- PromptGuard на входе, Output filter на выходе

## Лицензия

Proprietary. Один Босс — один Джарвис.

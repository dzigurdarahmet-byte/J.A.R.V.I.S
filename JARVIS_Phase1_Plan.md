# J.A.R.V.I.S. — Phase 1 MVP Plan

**Дата:** 21.05.2026
**Хост:** Windows 11 Home, Intel i5-12400 (6C/12T), 16GB RAM, 390GB free
**GPU:** нет (Intel UHD 730 only) → стратегия Hybrid Claude + Selectel on-demand
**Срок:** ~4 недели до первого рабочего голоса

---

## 1. Стратегические решения (зафиксировано)

| Решение | Значение | Обоснование |
|---|---|---|
| Архитектура | Full Stack (Docker + WSL2) | Boss: production-style с первого дня |
| Brain (primary) | Claude Sonnet 4.6 через API | Нет локального GPU |
| Brain (slot для on-demand) | Ollama@Selectel GPU | Поднимаем по часам когда нужно |
| STT | Faster-Whisper Small на CPU | Достаточно для 1 пользователя |
| TTS | Silero v3 (ru, мужской) на CPU | Оффлайн, бесплатно, хорошее качество RU |
| Wake-word | НЕТ в MVP (push-to-talk кнопка) | Porcupine на Фазе 4 |
| Event Bus | Redis Streams (по v5.3) | Архитектурно с первого дня |
| Memory | MEMORY.md + SQLite + ChromaDB (отложим) | Tier1+Tier2, Tier3 на Фазе 2 |
| Channels MVP | Web HUD + Telegram + CLI | Yandex Station/HA — Фаза 2 |
| Skills MVP | ~10 базовых | Погода, курсы, время, новости, задачи, таймеры, заметки, поиск, перевод, калькулятор |

---

## 2. Стек установки (что ставим на ПК)

### Уже установлено (проверено)
- Windows 11 Home 22631
- Git 2.54.0
- Python 3.13.13
- uv 0.11.15 (в Python313\Scripts)
- winget 1.6.2771
- WSL (но без дистрибутивов)
- Desktop Commander MCP + Windows MCP (для меня — управление ПК)

### Ставим сейчас
1. **FFmpeg** (Gyan.FFmpeg) — обязательно для STT/TTS audio pipeline
2. **Node.js LTS** (OpenJS.NodeJS.LTS) — для будущего frontend и MCP-серверов
3. **Docker Desktop** (Docker.DockerDesktop) — Redis, PostgreSQL, изоляция
4. **WSL2 + Ubuntu** — для Docker backend и Linux-плагинов

### Ставим в первую неделю (по мере необходимости)
- **Redis** (через Docker) — Event Bus + кэш
- **PostgreSQL** (через Docker) — постоянное хранение (опционально на MVP, можно SQLite)
- **ChromaDB** (через Docker) — vector store (можно отложить на Фазу 2)

### Откладываем до GPU
- Ollama локально
- Whisper large-v3
- Fish Speech V1.5 / XTTS-v2 (voice cloning)
- Kandinsky локально

---

## 3. Структура проекта (jarvis/)

```
C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\
├── jarvis/                          # основной репо
│   ├── core/
│   │   ├── event_bus/               # Redis Streams (v5.3)
│   │   ├── router/                  # 4-уровневый: TF-IDF → Fuzzy → MCP → LLM
│   │   ├── memory/                  # MEMORY.md manager + daily logs
│   │   ├── security/                # PromptGuard, sanitizer (v5.3)
│   │   ├── offline/                 # OfflineDetector, OfflineQueue (v5.3)
│   │   ├── providers/               # Provider Registry (Claude / Selectel-Ollama / Groq)
│   │   ├── voice/                   # Whisper + Silero
│   │   └── logging/                 # structlog + request_id
│   │
│   ├── skills/                      # 10 базовых
│   │   ├── weather_ru/
│   │   ├── time/
│   │   ├── currency/
│   │   ├── news/
│   │   ├── tasks/
│   │   ├── timers/
│   │   ├── notes/
│   │   ├── search/
│   │   ├── translate/
│   │   └── calculator/
│   │
│   ├── channels/                    # каналы общения
│   │   ├── web_hud/                 # FastAPI + WebSocket
│   │   ├── telegram/                # aiogram bot
│   │   └── cli/                     # CLI клиент
│   │
│   ├── workspace/                   # файлы под Git (SOUL/USER/MEMORY)
│   │   ├── SOUL.md                  # личность, правила
│   │   ├── USER.md                  # профиль Босса
│   │   ├── PREFERENCES.md
│   │   ├── MEMORY.md                # Tier 1 память
│   │   └── daily/                   # Tier 2 логи
│   │
│   ├── docker-compose.yml           # Redis, Postgres (опционально), JARVIS
│   ├── Dockerfile                   # для production deploy
│   ├── pyproject.toml               # uv-based
│   ├── uv.lock
│   ├── .env.example
│   └── README.md
│
└── docs/
    └── (текущие архитектурные доки)
```

---

## 4. Roadmap по неделям

### Неделя 1 — Каркас и каналы
- [ ] Установка инструментов (FFmpeg, Node.js, Docker, WSL2)
- [ ] Telegram Bot Token + OpenWeather API key
- [ ] Структура `jarvis/` под Git
- [ ] FastAPI ядро + Redis Streams Event Bus
- [ ] Provider Registry (Claude только)
- [ ] Workspace-файлы (SOUL/USER/MEMORY.md) + SHA-256 чексумма
- [ ] PromptGuard input sanitizer
- [ ] Telegram-бот (echo → подключение к router)
- [ ] structlog с request_id, OpenAPI-контракт
- [ ] Docker-compose с Redis
- **Цель:** Telegram-бот отвечает через Claude API

### Неделя 2 — Голос
- [ ] Faster-Whisper Small (CPU) — STT через FastAPI endpoint
- [ ] Silero v3 RU TTS — генерация .wav
- [ ] Web HUD (jarvis.jsx с Arc Reactor) + WebSocket
- [ ] Push-to-talk кнопка в Web HUD
- [ ] Аудио pipeline через Event Bus (skill_result → TTS → channel)
- **Цель:** в Web HUD скажешь голосом — JARVIS ответит голосом

### Неделя 3 — Скиллы и память
- [ ] Router L1 (TF-IDF + Fuzzy)
- [ ] 10 базовых скиллов
- [ ] MEMORY.md manager + daily logs (Tier 1 + Tier 2)
- [ ] OfflineDetector + OfflineQueue (по v5.3)
- [ ] Output filter для утечек API-ключей
- **Цель:** «Джарвис, какая погода?» работает в офлайн (по кэшу) и онлайн

### Неделя 4 — Полировка и интеграции
- [ ] Heartbeat (cron jobs внутри JARVIS)
- [ ] Yandex SpeechKit как fallback STT/TTS
- [ ] Groq как fallback LLM
- [ ] Базовые автоматизации: morning briefing, evening summary
- [ ] Бэкапы workspace (Git push в приватный GitHub repo)
- [ ] (Опционально) первый запуск Selectel GPU on-demand: рецепт + Ollama Qwen3 8B
- **Цель:** Фаза 1 MVP готов, можно демонстрировать

---

## 5. Бюджет Фазы 1

| Статья | Сумма/мес |
|---|---|
| Claude API (Sonnet 4.6, 10-30 запросов в день) | $10-30 |
| Selectel GPU on-demand (2-5 часов в неделю) | ~$15-30 (1500-3000₽) |
| OpenWeather, NewsAPI, прочие — бесплатные tier'ы | $0 |
| GitHub приватный repo | $0 (free tier) |
| **Итого** | **$25-60/мес** |

---

## 6. Что НЕ делаем в Фазе 1 (важно для скоупа)

- ❌ Yandex Station / Home Assistant интеграция (на Raspberry Pi когда поднимется)
- ❌ Huawei Health Kit (нужен API-доступ, отдельная задача)
- ❌ Локальная Ollama 24/7 (нет GPU)
- ❌ Voice cloning (Fish Speech / XTTS) — GPU
- ❌ TalkingHead 3D — Фаза 3
- ❌ Android-приложение — Фаза 3+
- ❌ MCP-server для Claude Desktop — Фаза 2
- ❌ Skill marketplace и sandbox — Фаза 3
- ❌ Privacy router PUBLIC/PRIVATE/SECRET — добавим к Фазе 2 (на MVP всё через Claude)

---

## 7. Что от тебя нужно в начале

1. **Anthropic API key** — у тебя есть, положу в `.env` когда дойдём
2. **Telegram Bot Token** — @BotFather → /newbot (5 минут)
3. **OpenWeather API key** — openweathermap.org/api → free tier (5 минут)
4. **Согласие на установку Docker Desktop** — он требует Hyper-V, после установки понадобится перезагрузка ПК
5. **(опционально, в конце Недели 4)** аккаунт Selectel — когда дойдём до экспериментов с локальной LLM

Остальное делаю я через Desktop Commander и Windows-MCP без твоего вмешательства.

---

*«План зафиксирован. Двигаемся, Босс.»*

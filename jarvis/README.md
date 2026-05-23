# J.A.R.V.I.S. — персональный ассистент Босса

Голос + Telegram + Web HUD на базе Claude Sonnet 4.6. 20 встроенных скиллов, tool-use, память между сессиями.

## Быстрый старт

```powershell
# Из jarvis/
.\.venv\Scripts\python.exe run_web_hud.py     # http://localhost:8000
.\.venv\Scripts\python.exe run_telegram.py    # @jarvis_boss_sergey_bot
.\.venv\Scripts\python.exe run_voice.py       # VAD-режим
.\.venv\Scripts\python.exe run_voice.py ptt   # push-to-talk
```

Hotkeys voice-режима (глобальные):
- `F12` — пауза/возобновление слушания
- `F11` — переключение VAD ↔ PTT
- `Ctrl+Space` — push-to-talk: зажать, говорить, отпустить

## Архитектура

```
Входящий запрос → Router → ответ
                    │
                    ├── L1 keyword-match (20 скиллов)            ← быстро, бесплатно
                    │   погода/время/курсы/таймер/калькулятор/…
                    │
                    ├── L2 Claude с tool-use (16 функций)        ← для follow-up
                    │   «А в Сыктывкаре?» → Claude сам зовёт
                    │   get_weather(city='Сыктывкар')
                    │
                    └── L4 Claude чистым текстом                 ← общие вопросы
                        «Объясни квантовую механику»
```

## Структура

```
jarvis/
├── core/
│   ├── event_bus/        # in-process pub/sub
│   ├── router/           # L1/L2/L4 маршрутизатор
│   ├── memory/           # MEMORY.md + daily logs
│   ├── providers/        # ClaudeProvider (+ chat_with_tools)
│   ├── security/         # PromptGuard sanitizer
│   ├── voice/            # Faster-Whisper STT + Silero TTS
│   ├── skills/
│   │   ├── base.py                # BaseSkill / KeywordSkill
│   │   ├── registry.py            # 20 встроенных скиллов
│   │   ├── tool_registry.py       # 16 Anthropic tool-схем для L2
│   │   └── weather_providers.py   # Open-Meteo + OpenWeather fallback
│   └── logging/          # structlog с redaction секретов
│
├── channels/
│   ├── web_hud/          # FastAPI + WebSocket + Arc Reactor SVG
│   ├── telegram/         # aiogram + proxy для РКН-обхода
│   └── local_voice/      # Bluetooth наушники, VAD, hotkeys
│
├── workspace/            # личность + память (под git)
│   ├── SOUL.md           # личность Джарвиса
│   ├── USER.md           # профиль Босса
│   ├── MEMORY.md         # Tier 1 факты
│   └── daily/            # Tier 2 дневные логи
│
├── scripts/              # smoke / e2e / отладочные
└── run_*.py              # точки входа
```

## Скиллы (L1)

| Имя | Триггеры | Источник данных |
|---|---|---|
| `time` / `date` / `timezone` | «который час», «дата», «время в Токио» | локально |
| `timer` / `alarm` | «таймер на 5 минут», «будильник на 7:30» | локально |
| `weather` / `weather_forecast` | «погода в …», «прогноз на завтра» | Open-Meteo → OpenWeather |
| `currency` | «курс доллара» | ЦБ РФ |
| `crypto` | «курс биткоина» | CoinGecko |
| `note` / `remember` / `forget` / `notes_list` | «запиши», «запомни», «покажи заметки» | MEMORY.md |
| `wiki` | «расскажи про …», «что такое …» | RU Wikipedia |
| `news` | «новости» | NewsAPI (нужен ключ) |
| `calc` | «сколько будет 17 плюс 25» | local AST eval (безопасно) |
| `convert` | «100 км в мили» | local |
| `random` | «подбрось монетку», «случайное число» | local |
| `translate` | «переведи на английский …» | MyMemory |
| `status` | «статус» | local |

## .env (секреты — НЕ коммитим)

Лежит в **корне проекта** (`ДЖАРВИС (2)/.env`), не в `jarvis/.env`. Требуется:

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OWNER_CHAT_ID=...
TELEGRAM_PROXY_URL=http://127.0.0.1:10808       # v2rayN для обхода РКН
OPENWEATHER_API_KEY=...                          # fallback weather
NEWSAPI_KEY=...                                  # опционально
TTS_SPEAKER=xenia
```

## Известные особенности

- **Кириллический путь проекта** — torch на Windows падает с WinError 1114, если cwd содержит кириллицу. Запуск через `.venv\Scripts\python.exe` решает; модели Silero copy в `C:\jarvis_data\models\` перед загрузкой.
- **Bluetooth + sounddevice** — перед стартом voice-канала убедиться что в Windows Settings → Sound default input/output = BT гарнитура.
- **Telegram + РКН** — `aiogram` через proxy URL из `.env`. Без proxy `api.telegram.org` блокируется.
- **Whisper hallucinations** — фильтр `core/voice/anti_hallucination.py` режет «Субтитры от Н.Новикова», «Subtitles by Amara.org» и пр.

## Тесты

```powershell
.\.venv\Scripts\python.exe scripts\skills_smoke.py        # L1 keyword match (29/29)
.\.venv\Scripts\python.exe scripts\skills_e2e.py          # L1 через WebSocket (20/20)
.\.venv\Scripts\python.exe scripts\skills_l2_test.py      # L2 tool-use + follow-up
.\.venv\Scripts\python.exe scripts\weather_cities_test.py # Open-Meteo по городам РФ
```

## Daily restart cheat-sheet

```powershell
# Убить всех jarvis-питонов (ОСТАВИТЬ windows-mcp!):
Get-CimInstance Win32_Process -Filter "Name='python.exe'" `
  | Where-Object { $_.CommandLine -like "*run_*.py*" } `
  | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# Поднять заново:
$wd = "C:\Users\Staho\DOCUME~1\Claude\Projects\(2)~1\jarvis"  # short path для torch
$py = "$wd\.venv\Scripts\python.exe"
Start-Process $py "run_web_hud.py"  -WorkingDirectory $wd
Start-Process $py "run_telegram.py" -WorkingDirectory $wd
Start-Process $py "run_voice.py"    -WorkingDirectory $wd
```

## Roadmap

Pending:
- **#13/14** Selectel GPU on-demand recipe — для оффлайн-LLM fallback
- **#32** TTSProvider Protocol — добавить Yandex SpeechKit как второй движок
- **#35** Расширять `ACCENT_DICT` в `core/voice/tts.py` по мере обнаружения ошибок ударения

Идеи:
- L1.5 context-tracker — короткие follow-up без захода в Claude
- Heartbeat — утренний/вечерний брифинг в Telegram
- Wake-word «Джарвис» вместо VAD/PTT

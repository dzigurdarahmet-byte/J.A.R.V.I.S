# C16 — Offline Mode

Что Джарвис умеет делать без интернета — и как это включить.

## Архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│  NetworkWatchdog (фон, каждые 30 сек)                            │
│    HEAD к api.anthropic.com + llm.api.cloud.yandex.net           │
│    State: ONLINE / PARTIAL / OFFLINE                             │
│    Публикует SYSTEM event в bus                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  SmartProvider (LLM chain)                                       │
│    1. Claude (primary)  — best quality, online                   │
│    2. YandexGPT  (fallback) — online, RU-tuned                   │
│    3. Ollama qwen2.5:7b (offline) — локальная LLM                │
│  Авто-skip падающих уровней через try/except + healthcheck       │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  TTS chain                                                       │
│    1. Yandex Alena (primary) — premium, online                   │
│    2. Silero v3 xenia (offline fallback)                         │
│  Автопереход на Silero при network err                           │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  STT chain                                                       │
│    1. Yandex SpeechKit (primary) — online                        │
│    2. Whisper small (offline fallback)                           │
│  Автопереход на Whisper при network err                          │
└──────────────────────────────────────────────────────────────────┘
```

## Что работает offline

| Возможность | Online | Offline | Качество offline |
|---|---|---|---|
| LLM ответы | Claude Sonnet 4.6 | Ollama qwen2.5:7b | Заметно хуже, но осмысленно |
| STT (распознавание речи) | Yandex SpeechKit | faster-whisper small | На 2-3 сек медленнее, точность ~85% |
| TTS (озвучка) | Yandex Alena | Silero v3 xenia | Робот-стиль, но понятно |
| Speaker verification | Resemblyzer (локально) | то же | без изменений |
| Wake word | openWakeWord (локально) | то же | без изменений |
| Memory (MEMORY.md, daily/) | локально | локально | без изменений |
| Skills time/date/calc/convert/random | локально | локально | без изменений |
| Skills weather/news/currency/crypto/wiki | online API | **недоступны** | — |
| Skills maps/geo | 2GIS API | **недоступны** | — |
| Skills calendar | Google API | **недоступны** | — |

## Установка Ollama

### 1. Скачать и установить

Windows: https://ollama.com/download/windows → запусти `OllamaSetup.exe`.
Установка ставит Ollama как Windows service (стартует автоматически при загрузке).

После установки `ollama` доступен в PowerShell:
```powershell
ollama --version
```

### 2. Скачать модель

Рекомендация: **qwen2.5:7b** — 5 GB, отлично знает русский, генерирует осмысленные ответы. Требует ~6 GB RAM.

```powershell
ollama pull qwen2.5:7b
```

Альтернативы (выбирай по железу):

| Модель | Размер | RAM | Качество русского | Скорость на CPU |
|---|---|---|---|---|
| `qwen2.5:7b` | 5 GB | 6 GB | ⭐⭐⭐⭐ | ~5 tok/s |
| `qwen2.5:14b` | 9 GB | 11 GB | ⭐⭐⭐⭐⭐ | ~2 tok/s |
| `llama3.1:8b` | 5 GB | 6 GB | ⭐⭐⭐ | ~5 tok/s |
| `gemma2:9b` | 5.4 GB | 7 GB | ⭐⭐⭐ | ~4 tok/s |
| `mistral:7b` | 4 GB | 5 GB | ⭐⭐ (плохо) | ~6 tok/s |

Если хочешь другую модель — поменяй `DEFAULT_MODEL` в `core/providers/ollama.py:24`.

### 3. Проверить

```powershell
# Запущен ли runtime
curl http://localhost:11434/api/tags

# Тест inference
ollama run qwen2.5:7b "Привет, как тебя зовут?"
```

### 4. Перезапустить JARVIS

После установки модели — рестарт HUD / voice loop. SmartProvider при следующем запросе попробует Claude → если упадёт → YandexGPT → если упадёт → Ollama.

## Тестирование offline

### Имитировать падение Claude+Yandex

Самый простой способ — отключить Wi-Fi на 30 сек. NetworkWatchdog заметит, в HUD статус сменится на `offline`, при следующем вопросе SmartProvider пройдёт chain и докатится до Ollama.

Если включишь сеть обратно — следующий вопрос уйдёт сразу на Claude.

### Проверить что Ollama подцепился

В HUD → sidebar:
```
network    ● offline
providers  ✗ claude-sonnet-4-5
           ✗ yandex-llm
           ✓ ollama:qwen2.5:7b
```

В логах после успешного fallback-вызова:
```
smart_fallback_succeeded provider=ollama:qwen2.5:7b position=2
```

В Metrics tab:
- Card "Fallback" увеличится
- В Provider hit-rate под `llm` появится `ollama:qwen2.5:7b`

## Troubleshooting

**Ollama healthcheck FAIL: connection refused**
- Запусти руками: `ollama serve` (если установка не зарегистрировала service)
- Проверь брандмауэр: `netsh advfirewall firewall add rule name="Ollama" dir=in action=allow protocol=TCP localport=11434`

**Модель не найдена**
- `ollama list` — что скачано
- `ollama pull qwen2.5:7b` — скачать

**Очень медленно (>30 сек на ответ)**
- Возможно модель крутится на CPU, а у тебя есть NVIDIA GPU — Ollama должна сама подхватить CUDA. Проверь: `nvidia-smi` при активной генерации, должен видеть `ollama.exe` среди процессов.
- Если GPU нет — уменьши модель до `qwen2.5:3b` (~2 GB, ~10 tok/s на CPU).
- Можно повысить `REQUEST_TIMEOUT_SEC` в `core/providers/ollama.py:25` (по умолчанию 60 сек).

**Silero TTS не загружается**
- Первый запуск качает модель ~50 MB из HuggingFace. Если нет сети — взять заранее, положить в `~/.cache/torch/hub/snakers4_silero-models_master/`.
- Без интернета и без кэшированной Silero — TTS не работает совсем, но текст всё равно отображается в HUD Chat.

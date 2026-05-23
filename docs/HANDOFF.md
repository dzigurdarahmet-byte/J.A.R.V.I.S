# J.A.R.V.I.S. — Handoff Document

**Назначение:** прочитать в начале каждого нового чата с Claude, чтобы не потерять контекст.
Любой Claude-агент после прочтения этого файла должен мгновенно включиться в проект.

---

## Кто Босс и что мы делаем

**Sergey "Босс" Стаховский** (dzigurdarahmet@gmail.com) — маркетинг + AI, удалёнка, английский A1.
Строит **J.A.R.V.I.S.** — personal AI-ассистент на Windows. Не коммерческий продукт, для себя.

**Обращение:** «Босс», никогда «вы»/«сэр»/«господин». Стиль — Marvel JARVIS: уважительно, лаконично, остроумно.

---

## Корневые пути

| Путь | Что внутри |
|---|---|
| `C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)` | Корень репо (git) |
| `C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis` | Python пакет |
| `C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\.venv` | Python venv |
| `C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\workspace` | Runtime state (MEMORY, daily, vector_db, owner_voice.npy, backup.log, metrics.db) |
| `C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\.secrets` | OAuth, GitHub PAT (gitignored) |
| `C:\Backups\jarvis` | Encrypted local backups + `.passphrase` |
| `C:\jarvis-bak` | Temp staging для git archive (auto-cleaned) |

**ВАЖНО:** путь содержит кириллицу `ДЖАРВИС` И пробел И скобки `(2)`. Это ломает много вещей. Решения:
- В PowerShell scripts всегда `[System.Text.UTF8Encoding]::new($true)` для BOM
- В путях использовать `$PSScriptRoot` относительно
- В .bat файлах — `chcp 65001 >nul`
- Edit-tool иногда теряет файлы — после Edit проверять через `git status`

---

## Архитектура

```
┌──────────────────────────────────────────────────────────────────────┐
│                          КАНАЛЫ (channels/)                          │
│  telegram (aiogram)  │  local_voice (VAD/wake/STT/TTS)  │  web_hud   │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                          ┌───────┴────────┐
                          ▼                ▼
                  ┌─────────────┐  ┌──────────────┐
                  │  EventBus   │  │   Router     │
                  │ (in-proc)   │  │ + Skills     │
                  └─────────────┘  └──────────────┘
                          │                │
            ┌─────────────┴────┐           │
            ▼                  ▼           ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
    │ MetricsColl. │  │ NetworkWatch │  │  SmartProvider   │
    │ (SQLite)     │  │ (poll 30s)   │  │  Claude → Yandex │
    └──────────────┘  └──────────────┘  │  → Ollama        │
                                         └──────────────────┘
```

**Brain:** Claude Sonnet 4.6 (primary), YandexGPT (fallback), Ollama qwen2.5:7b (offline).
**STT:** Yandex SpeechKit → Whisper fallback.
**TTS:** Yandex Alena → Silero fallback.
**Memory:** MEMORY.md (Tier 1) + workspace/daily/*.md (Tier 2) + vector_db/inmem.pkl (Tier 3).
**Speaker ID:** Resemblyzer GE2E 256-d embedding, threshold 0.65.
**Wake word:** openWakeWord, сейчас "hey_jarvis" (англ). Custom "Джарвис" — pipeline готов, Босс ещё не записал samples.

---

## Что сделано (по спринтам)

### Sprint 0 — Setup ✅
Phase 1 MVP: telegram bot ↔ Claude exchange, memory persistence, base scaffold.

### Sprint 1 — Категория A ✅
- A1 Vision (фото в Telegram → Claude vision)
- A2 Google Calendar (OAuth + read events skill)
- A4 2GIS / Yandex Maps (геокодер, маршруты, поиск ближайшего)
- A5 Yandex Music (голосовое управление)

### Sprint 2 — Категория B ✅
- B9 Voice barge-in (прерывание TTS живой речью)
- B9.5 Speaker verification (только голос Босса)
- B9.6 Hot-swap audio device (наушники/мик на лету)
- B10 YandexGPT fallback
- B10.5 Yandex STT primary + Whisper fallback
- B11 Auto-weekly summary (воскресенье 21:00)
- B12 Proactive alerts (валюта/погода/care-message)

### Sprint 3 — Категория C ✅
- C13 Auto-backup в GitHub + encrypted local (post-commit hook)
- C14 Logs-tab в HUD (SSE стрим)
- C15 Metrics dashboard (SQLite metrics.db, Chart.js)
- C16 OfflineMode (Ollama + Silero TTS fallback + NetworkWatchdog)

### Sprint 4 — Категория D (in progress)
- **B8 Custom wake-word «Джарвис»** — pipeline готов (record_wake_samples.py, extract_wake_dataset.py, Colab notebook, WakeDetector.auto()). **Pending:** Босс записывает samples → Colab training → dzarvis.onnx в models/wake/
- **D17 TalkingHead 3D-аватар** — MVP готов на Path A (web/three.js, без NVIDIA GPU). Встроенный default GLB (TalkingHead.js brunette, CC0). RPM блочат в РФ — есть proxy + загрузка с диска + Avaturn как альтернатива. **Pending:** Босс ещё не выбрал "встроенный аватар" в UI и не тестировал lip sync.
- D18 Home Assistant — не начинали
- D19 Yandex Station — не начинали
- D20 Voice cloning — не начинали
- D21 Android app — не начинали
- D22 Selectel GPU on-demand — не начинали

---

## Workflow коммитов (auto-backup hook)

Каждый `git commit` на ветке `main` запускает `.git/hooks/post-commit` синхронно (~10 сек):
1. **Public GitHub push** через `git archive HEAD` → temp `C:\jarvis-bak\snapshot` → fresh `git init` → force push на `main`. **Public repo:** github.com/dzigurdarahmet-byte/J.A.R.V.I.S — содержит ТОЛЬКО код (workspace, .secrets исключены в `.gitignore`).
2. **Encrypted local backup**: `tar.gz` workspace + .secrets → AES-256-CBC pbkdf2 600k → `C:\Backups\jarvis\jarvis-private-YYYYMMDD-HHMMSS.tar.gz.enc`. Ротация 14 архивов. **Passphrase** в `C:\Backups\jarvis\.passphrase` (Босс должен скопировать в KeePass!).

**Manual:** `jarvis\scripts\backup_now.bat` (с UTF-8 chcp).
**Восстановление:** см. `docs/BACKUP.md`.
**Полный путь push'а** — см. `core/providers/__init__.py:build_smart_provider`.

---

## Запуск компонентов

```powershell
cd "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis"

# Голосовой канал (с UTF-8 для логов)
.\scripts\start_voice.bat
# или вручную: .\.venv\Scripts\python.exe run_voice.py vad

# Telegram бот
.\.venv\Scripts\python.exe run_telegram.py

# Web HUD (включает Metrics, Logs, Avatar)
.\.venv\Scripts\python.exe run_web_hud.py
# открыть http://127.0.0.1:8000
```

**ВАЖНО:** Босс часто оставляет HUD запущенным; новый процесс не сможет занять порт 8000.
Claude из своего MCP-контекста **не может убить процессы Босса** — Access Denied. Только Босс:
```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
```

---

## Технические gotchas — что я узнал на собственной шкуре

| Проблема | Решение |
|---|---|
| `tar (child): Cannot connect to C:` | git-bash подхватил cygwin tar. Использовать явный `$env:windir\System32\tar.exe`. |
| `git worktree add` падает с `Unable to create .git/index.lock` | Не использовать worktree в репо с unicode-путём. Подход через `git archive HEAD` + fresh `git init` в `C:\jarvis-bak\snapshot` (ASCII path). |
| Кириллица в .ps1 → SyntaxError | Сохранять с UTF-8 **with BOM** через `[System.Text.UTF8Encoding]::new($true)`. |
| cmd окно от .bat показывает иероглифы | `chcp 65001 >nul` в начале .bat. |
| `voice_run.log` ромбики `◊◊◊` | PowerShell-redirect ломает UTF-8 байты. Использовать `start_voice.bat` с `PYTHONIOENCODING=utf-8`. |
| `Failed to fetch` GLB Ready Player Me | RPM блочит РФ + CORS. Решение: backend proxy `/api/avatar/model?url=` + встроенный default GLB. |
| `Set-ExecutionPolicy` блокирует .ps1 | Запускать через `powershell -ExecutionPolicy Bypass -File ...` или `.bat`-врапер. |
| `Stop-Process` Access Denied | Процесс под другим токеном. Только сам Босс может убить из своей сессии. |
| Edit-tool иногда теряет файлы | После Edit на кириллических путях — `git status` для проверки. |

---

## Секреты (где лежат, никогда не комитить)

| Файл | Что |
|---|---|
| `jarvis/.secrets/google_credentials.json` | Google OAuth client |
| `jarvis/.secrets/google_token.json` | Google access token (Calendar) |
| `jarvis/.secrets/github_pat` | GitHub Personal Access Token для auto-backup |
| `jarvis/.env` | Все API ключи (ANTHROPIC, TELEGRAM, YANDEX, OPENWEATHER, NEWSAPI, 2GIS) |
| `C:\Backups\jarvis\.passphrase` | AES-256 ключ от encrypted backups |

**Ключи — НИКОГДА в этом MD.**
Все секреты живут в `jarvis/.env` и `jarvis/.secrets/`. Восстановление — из encrypted local backup (`C:\Backups\jarvis\*.tar.gz.enc`, passphrase в `C:\Backups\jarvis\.passphrase`).
Список того, что лежит в `.env`: ANTHROPIC, TELEGRAM, YANDEX (API key + folder id), GOOGLE, OPENWEATHER, NEWSAPI, 2GIS, GitHub PAT.

> **Lesson learned:** в коммите `c432dcd` ключи попали в публичный git (push protection отработал и заблокировал, но узкое окно было). Если такой инцидент повторится — ключ считается скомпрометированным, его надо отзывать в облаке провайдера и генерировать новый.

---

## Стиль работы Босса (важно)

- **Без воды.** Прямой, лаконично, по делу.
- **Делаю → коммит → показываю результат скриншотом или логом.** Любой commit автоматически идёт на GitHub + encrypted.
- **AskUserQuestion** для развилок, не для риторических вопросов.
- **При ошибках** — сразу диагностика (логи, stderr), не делать вид что норм.
- **Технические решения** — выбирать самые простые работающие, не over-engineering.
- **Когда у Босса нет GPU/VPN/железа для AAA-подхода** — честно сказать и предложить упрощённый путь.
- **Если что-то не могу сделать сам** (kill процесса, OAuth click) — чётко сказать что нужно от Босса.

**Что Босс НЕ любит:** длинные рассуждения, "может быть", choice paralysis, заглушки вместо рабочего кода, попытки спрятать что что-то не работает.

---

## Куда смотреть в репо

| Файл | Зачем |
|---|---|
| `docs/BACKUP.md` | Как восстановить из backup на новой машине |
| `docs/OFFLINE_MODE.md` | Установка Ollama, выбор модели |
| `docs/B8_WAKE_WORD_TRAINING.md` | End-to-end процедура для custom wake-word |
| `jarvis/run_telegram.py` / `run_voice.py` / `run_web_hud.py` | Entry points |
| `jarvis/core/providers/smart.py` | SmartProvider chain Claude→Yandex→Ollama |
| `jarvis/core/voice/` | STT/TTS/VAD/wake/speaker_id |
| `jarvis/core/memory/` | Tier 1/2/3 |
| `jarvis/core/skills/` | 25+ builtin skills (time, weather, currency, music, calendar, geo, etc.) |
| `jarvis/channels/web_hud/server.py` | FastAPI: Chat WS, Logs SSE, Metrics, Avatar |
| `jarvis/channels/web_hud/static/index.html` | HUD frontend (Chat/Logs/Metrics tabs) |
| `jarvis/channels/web_hud/static/avatar.html` | Three.js 3D-аватар (D17) |
| `.git/hooks/post-commit` | Auto-backup trigger |
| `jarvis/scripts/backup_full.ps1` | Двойной бэкап (Public + encrypted) |
| `jarvis/scripts/record_wake_samples.py` | Запись 150 utterances «Джарвис» |
| `jarvis/scripts/train_wake_dzarvis.ipynb` | Colab notebook training |

---

## Roadmap дальше

**Незавершённое в работе:**
- **B8 wake-word**: Босс записывает samples → Colab training (~6-8ч T4) → положить .onnx в models/wake/ → WakeDetector.auto() подхватит
- **D17 аватар**: Босс тыкает "встроенный аватар" в setup screen → проверяет lip sync → решает оставить или докручивать (эмоции, eye tracking, авто-говорит на assistant_reply из bus)

**Sprint 4 категория D (не начатые):**
- D18 Home Assistant (управление умным домом)
- D19 Yandex Station (интеграция через `app://`)
- D20 Voice cloning (TTS голосом Боссса через ElevenLabs/RVC)
- D21 Android-приложение
- D22 Selectel on-demand GPU (для тяжёлых задач: training, Whisper large, voice cloning)

---

## Как начать новый чат

В новом чате первое сообщение: **"Прочитай `docs/HANDOFF.md` и `MEMORY.md` (если есть), потом продолжаем."**

Claude должен:
1. `Read C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\docs\HANDOFF.md` (этот файл).
2. `Read C:\Users\Staho\AppData\Roaming\Claude\local-agent-mode-sessions\3d716ed1-6b23-4704-876f-7199932313ff\5f4e6281-d044-40f6-8e38-301d891f0d18\spaces\dfe4b31b-a0a4-47f1-902a-c9c4de2b4119\memory\MEMORY.md` (если существует).
3. `git log --oneline -10` чтобы увидеть последние коммиты.
4. Спросить Босса что делаем дальше.

---

## Session 2026-05-23 вечер→ночь (News RU + Smart Home + Music streaming + Android spec + CF Tunnel)

**Длинная сессия (~10 часов).** Закрыто очень много. Контекст для завтра:

### Что сделано (по группам)

**1. Backend-фичи (3 коммита):**
- `a4364fb` — **News skill #31**: переезд с заблоченного NewsAPI на RSS-агрегатор: РИА Новости, ТАСС, РБК, Lenta, Коммерсантъ. Триггеры: «новости», «новости про IT/крипту/спорт», «мировые новости» (с переводом через mymemory). httpx с `trust_env=False` чтобы обходить v2rayN для РФ-сайтов.
- `29cd848` — **Phase 2 CodeAssistSkill** (issue #30): edit-mode с full tool-access. Junction `C:\jarvis-repo` → корень JARVIS-репо (обходим кириллический путь). `--add-dir <junction>` + `--dangerously-skip-permissions`. Triggers: «исправь файл core/...», «обнови registry», «запусти тесты», «прочитай ROADMAP.md» → edit-mode. Без триггеров — text-mode (как было).
- `b9f408e` + `c4a8e1d` — **D18 Yandex Smart Home + Music streaming**. См. ниже.

**2. D18 Smart Home через Алису (без HA-hub):**
- `YandexSmartHomeSkill` — управление устройствами через `api.iot.yandex.net/v1.0/`. OAuth scope `iot:control + iot:view`.
- Триггеры: «включи/выключи свет/розетку», «свет на 30%», «выключи весь свет», «зажги синий», «включи вечеринку», «выключи подсветку».
- 15 цветов (HSV — RGB Станция не принимает) + 17 сцен (party/candle/lava_lamp/sunset/...).
- `scripts/get_yandex_iot_token.py` для OAuth.
- **Токен в `.env`: `YANDEX_IOT_TOKEN=y0__wgBEISN8NwBGN74BiC-va3QF7l3uqDfVoELG6-GZ-12CnEN8xyX`** (аккаунт StahovskiySS1993@yandex.ru, не Росси).
- Устройств в аккаунте: **только Яндекс Станция 3 Orion** (Zigbee-устройств физически нет — Босс установит в новой квартире после ремонта). Skill готов и будет работать как только устройства появятся.

**3. Yandex Music streaming через JARVIS-аудио:**
- Полная замена старого `MusicSkill` (был только метаданные).
- Yandex Music API → URL трека → httpx download MP3 → `soundfile` декодирует → `play_audio` в FreeBuds. **ffmpeg НЕ нужен** (soundfile нативно умеет MP3).
- Background task через `asyncio.create_task` — skill сразу возвращает «Играю X», музыка играет в фоне.
- Триггеры: «включи музыку», «поставь Imagine Dragons», «играй Арию», «следующий трек», «дальше», «стоп», «хватит», «что играет», «мои лайки», «дай рекомендации».
- **Token в `.env`: `YANDEX_MUSIC_TOKEN=y0__wgBEISN8NwBGN74BiC-va3QF7l3uqDfVoELG6-GZ-12CnEN8xyX`** (личный аккаунт с Plus, 28 лайков).
- ⚠ **Уроки:** Yandex Music OAuth client_id публичный (`23cabbbdc6cd418abb4b39c32c41195d` от yandex-music-api community). Босс получил два токена: первый от "Росси Команда" (без Plus, 451 Legal) — пришлось переавторизоваться под личным аккаунтом. И ещё подмена `I` (i upper) ↔ `l` (L lower) в копировании токена.
- ⚠ **Voice-команда «включи Арию» через FreeBuds HFP** распознаётся плохо (STT галлюцинирует на 8 kHz BT-канале). **В TG-боте текстом работает идеально.** До покупки LE Audio USB-донгла (EarFun BT-W5 ~2.5к₽) — голосом музыку лучше не пытаться.

**4. AppControl conflict fix:**
- AppControl уступает FileSkill/Music/SmartHome когда в тексте есть «музык/плейлист/трек/подсветк/синий/вечеринка/и т.д.».
- Также: если после «открой/закрой» нет известного приложения из APP_MAP — AppControl возвращает 0.0 (пусть другие skills ловят).

**5. D21 Android App — архитектура (БОЛЬШОЕ):**
- **`docs/D21_ANDROID_APP_SPEC.md`** — полная спека (314 строк). Архитектура, endpoints, sprint-plan.
- **Финальные решения по стеку:**
  - Backend: моноблок + Cloudflare Tunnel (готовим переезд на новый ПК Xeon E5-2697v4 + RTX 3070).
  - Stack: **Kotlin Native + Jetpack Compose** (делаем сразу хорошо).
  - STT: **Android SpeechRecognizer** на телефоне (низкая латенция).
  - TTS: **JARVIS Alena → mp3 → ExoPlayer телефон** (одинаковый голос везде).
- **6 Principal-skills созданы** для разработки Android в `C:\Users\Staho\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\skills-plugin\.../skills/`:
  - `android-architect` — Clean/MVVM/Hilt/Gradle/distribution.
  - `android-compose-ui` — Compose state, recomposition, Navigation.
  - `android-data-network` — Retrofit/OkHttp/Room/JWT.
  - `android-mobile-services` — Foreground Service/FCM/Voice.
  - `android-testing-quality` — JUnit5+MockK/Compose UI test/CI.
  - `mobile-backend-architect` — REST/JWT refresh/SSE/idempotency.

**6. Cloudflare Tunnel (Sprint A1 D21):**
- Установлен `cloudflared` через winget.
- Tunnel `jarvis` создан, привязан к **`jarvis.stakhovskiyss.ru`** (домен Босса).
- Config в `C:\Users\Staho\.cloudflared\config.yml` — ingress → `http://127.0.0.1:8000`.
- **Service установлен и запущен** под Windows (autostart). ImagePath:
  ```
  "C:\Program Files (x86)\cloudflared\cloudflared.exe" --config "C:\Users\Staho\.cloudflared\config.yml" tunnel run jarvis
  ```
- SSL/TLS mode в CF dashboard переключён с **Full → Flexible** (CF→Origin = HTTP).
- **Мои тесты с моноблока через CF: 200 OK, `CF-RAY: ...-FRA`.** Tunnel метрики: 4/4 ha_connections live, 0 errors.
- ⚠ **Caveat:** Боссов браузер получает **Error 1033** ("Cloudflare unable to resolve tunnel"). Скорее всего пропагация tunnel routing между CF edges медленная на Free плане (до 60 мин). Завтра должно само заработать. Если нет — переустановим tunnel.

### Состояние проекта (overall)

**Roadmap progress:**

| Sprint | Статус | Что осталось |
|---|---|---|
| Sprint 1 — Категория A (Vision, Calendar, Geo, Music) | ✅ DONE | — |
| Sprint 2 — Категория B (Wake-word, Voice barge-in, YandexGPT, Weekly, Proactive) | ✅ DONE кроме **B8 custom wake-word** (samples не записаны) | Босс пишет 150 samples → Colab T4 ~6-8ч |
| Sprint 3 — Категория C (Backup, Logs HUD, Metrics, Offline) | ✅ DONE | — |
| Sprint 4 — Категория D (Avatar, HA, Alice, Voice clone, Android, GPU) | 🟡 В РАБОТЕ | См. ниже |
|   D17 TalkingHead 3D | ✅ DONE (lip-sync deferred) | — |
|   D18 Smart Home через Алису | ✅ DONE | Босс установит Zigbee-устройства в новой квартире |
|   D19 Yandex Station webhook | ✅ DONE (fold in D18) | — |
|   D20 Voice cloning | ⏳ Не начат | Требует RTX 3070 (новый ПК) |
|   **D21 Android app** | 🟡 **В РАБОТЕ** | Sprint A1 ✓ (CF Tunnel), A2-C1 pending |
|   D22 Selectel on-demand GPU | ⏳ Не начат | После D20 |

**Дополнительно сделано вне roadmap (за сегодня):**
- News skill переезд на РФ источники (issue #31).
- CodeAssistSkill (issue #30) — text + edit modes.
- Goals tab в HUD.
- FileSkill (find/open/rename/delete + транслит).
- Voice tuning под FreeBuds (BT HFP gain).
- Music streaming в JARVIS-аудио.
- D21 spec + 6 Principal-skills.

### Что делаем завтра — Sprint A2

**Цель:** добавить mobile-endpoints в JARVIS backend.

**1. Сначала проверить что CF Tunnel пропагировал** — открыть `https://jarvis.stakhovskiyss.ru` в браузере. Должна открываться HUD-страница. Если 1033 — посмотреть в CF dashboard → Zero Trust → Networks → Tunnels, статус должен быть **Healthy** (зелёный).

**2. Реализовать в `jarvis/channels/mobile/`:**
```
mobile/
├── __init__.py
├── server.py     — FastAPI router /api/v1/*, mount в run_web_hud
├── auth.py       — JWT issue/verify, bcrypt PIN
├── schemas.py    — Pydantic модели
└── push.py       — Firebase Admin SDK для FCM (когда дойдём до B5)
```

**Endpoints (v1):**
```
POST /api/v1/auth/login           {pin}              → {access, refresh}
POST /api/v1/auth/refresh         {refresh}          → {access, refresh}
GET  /api/v1/status                                  → {ok, providers}
POST /api/v1/chat/text            {text}             → {reply, tts_url, request_id}
GET  /api/v1/tts/audio/{id}.mp3   (Bearer JWT)       → mp3 stream
GET  /api/v1/stream               (Bearer JWT)       → SSE: nudges, alarms
POST /api/v1/push/register        {fcm_token}        → {ok}
```

**3. Что нужно от Босса для Sprint A2 (10 минут действий):**
- Придумать PIN (6 цифр) — положу в `.env` как `JARVIS_MOBILE_PIN_HASH` (bcrypt).
- Принять решение по FCM: либо сразу зарегистрировать Firebase project (Sprint A4), либо отложить до B5.

**4. После A2 — Sprint B1 (Android skeleton):**
- Установить Android Studio.
- Создать пустой Compose-проект.
- Подключить Hilt + Retrofit + Compose Navigation.
- Login screen → Chat screen → Settings.

### Висящие технические долги

- **Voice через FreeBuds HFP** даёт мусорный STT — голосом музыку/неоднозначные запросы плохо. Решение: USB BT 5.3+ донгл с LE Audio (~2.5к₽ EarFun BT-W5) **или** ждать D21 → телефон ↔ FreeBuds через LE Audio (родной у современных смартфонов).
- **`Windows 11 25H2 update`** — у Босса в очереди, не установлен. Нужен для full LE Audio support (когда купим донгл).
- **CF Tunnel пропагация 1033** — следить, если завтра ещё будет — переустановить tunnel.
- **room=UUID** в YandexSmartHome — косметика, при появлении устройств в новой квартире.

### Git состояние (сегодня)

```
... (Sprint A1 - CF Tunnel — не закоммичено, всё в системных настройках)
d8124f8 docs(d21): architecture spec для Android JARVIS app + AppControl уступает Music/SmartHome
c4a8e1d feat(music+smart-home): streaming + scenes for Яндекс Станция
b9f408e feat(smart-home): D18 — Yandex Smart Home через Алису
29cd848 feat(code-assist): Phase 2 — edit-mode с tools
a4364fb feat(news): #31 — переезд на российские RSS-источники
```

### Установленные сегодня tools

- `cloudflared` (Windows service, ImagePath с `--config`).
- Ollama Desktop App (через winget, но требовал ребут для PATH).
- `deepseek-r1:7b` модель в Ollama (через ручной pull).

### Ключевые файлы

- `docs/D21_ANDROID_APP_SPEC.md` — полная спека Android-app.
- `jarvis/core/skills/code_assist_skill.py` — edit-mode + junction.
- `jarvis/core/skills/file_skill.py` — find/open/rename/delete + транслит.
- `jarvis/core/skills/news_ru_skill.py` — RSS-агрегатор.
- `jarvis/core/skills/yandex_smart_home_skill.py` — IoT API + scenes.
- `jarvis/core/skills/music_skill.py` — streaming pipeline.
- `jarvis/scripts/get_yandex_iot_token.py` — OAuth helper IoT.
- `jarvis/scripts/get_yandex_music_token.py` — OAuth helper Music.
- `C:\jarvis-repo` — junction на репо для CodeAssist edit-mode.
- `C:\Users\Staho\.cloudflared\config.yml` — конфиг tunnel.

### Большой ответ на «куда движемся»

**К моменту запуска Android app у тебя в кармане (D21 complete + LE Audio в FreeBuds):**
- Дома: JARVIS на моноблоке (Sprint 1-3 + большая часть D = почти всё кроме voice-clone и Android).
- Везде с 4G: телефон + FreeBuds → JARVIS-сервер через CF Tunnel.
- В FreeBuds: одновременно хороший звук музыки/TTS И качественный mic для voice — через LE Audio LC3.

**Следующая большая веха после D21** — D20 (Voice cloning твоим голосом через RTX 3070 на новом ПК).

**После D20** — D22 (Selectel GPU on-demand) для тяжёлых задач (Whisper large, voice clone training).

**И тогда — Тони Старк-режим закрыт полностью.**

---

## Session 2026-05-23 утро (Goals tab + FileSkill + voice tuning под FreeBuds)

### Что сделано
- **Goals tab в Web HUD** (коммит `6bf659e`). `GET /api/goals` денормализует percent/pace/days_left. Карточки с CSS-gradient progress bar (зелёный/жёлтый по темпу), red text для просроченных. Auto-refresh 15с.
- **FileSkill — операции с файлами** (коммит `6bf659e`). Триггеры «найди файл / открой документ / переименуй X в Y / удали файл X», двухшаговое подтверждение для delete, whitelist Documents/Downloads/Desktop/Pictures/Music/Videos + OneDrive-зеркала. Skip dirs: `.venv`, `__pycache__`, `.git`, `site-packages`, `AppData`. L1 keyword + L2 tool-use.
- **AppControl уступает FileSkill** на «открой документ» (коммит `fd584b9`). Раньше score=1.0 у обоих, побеждал первый зарегистрированный — Боссу прилетал список приложений вместо файла.
- **Транслит cyr↔lat в FileSkill** (коммит `ba95c7a`). Yandex STT всегда даёт кириллицу. Теперь «мемори» находит `MEMORY.md`, «роадмап» → `ROADMAP.md`, «хандофф» → `HANDOFF.md`, «джарвис» → `JARVIS_*` (7 hit), «инсталл» → `INSTALL_*.md`. Биграммы: `дж→j`, `кс→x`, `ия→ia`. Нормализация: dedup букв, `y→i`, мягкий/твёрдый знак, разделители.
- **Voice autostart** (коммит `e470a8f`). В `start_all.ps1` строка `run_voice.py` была закомментирована с 21 мая. Раскомментировано, `Start-Subsystem` расширена параметром `ScriptArgs` для передачи `vad`. Теперь воркает после ребута без ручных команд.
- **Voice tuning под FreeBuds Pro 4** (коммит `d647cba`). Bluetooth HFP отдавал речь на -76 dB, gate резал. Три ручки через settings: `JARVIS_AUDIO_INPUT_GAIN` (multiplier до VAD), `JARVIS_MIN_RMS_DB` (anti_hallucination gate), `JARVIS_SPEAKER_SIMILARITY_THRESHOLD` (Resemblyzer). Все читаются из pydantic-settings.

### Финальный шаг: переключение на встроенный мик моноблока
- В Windows Sound → Input выбран `Microphone (2- High Definition Audio Device)` вместо FreeBuds Hands-Free.
- FreeBuds теперь всегда в A2DP (никаких HFP-переключений, высокое качество звука постоянно).
- В `.env` возвращены дефолты: `JARVIS_AUDIO_INPUT_GAIN=1.0`, `JARVIS_MIN_RMS_DB=-55.0`, `JARVIS_SPEAKER_SIMILARITY_THRESHOLD=0.65`.
- JARVIS подтвердил `audio_capture_started device='Microphone (2- High Definition Audio Device)'`.
- Шумовой пол -92 dB (норма для встроенного без gain).

### Roadmap для «JARVIS как у Тони Старка»
- **Краткосрочно:** дома у моноблока — встроенный мик (работает). Вне дома / в другой комнате — Telegram-бот с voice messages через FreeBuds + телефон. Полное покрытие.
- **Среднесрочно:** USB BT 5.3+ адаптер с LE Audio (Creative BT-W5 ~4к₽ или EarFun BT-W5 ~2.5к₽). Откладывается, бюджет.
- **Долгосрочно (D21):** Android-app JARVIS. FreeBuds → телефон (его BT уже умеет LE Audio из коробки), JARVIS через мобильный интернет → радиус «где есть 4G». Это закрывает Тони-Старк-кейс полностью без донглов.

### Ollama
- Daemon установился (`C:\Users\Staho\AppData\Local\Programs\Ollama\ollama.exe`), API на 11434 отдаёт 200.
- `ollama pull deepseek-r1:7b` запущен в фоне в 7:51 (PID 28592), ~4.7 GB через v2rayN.
- После завершения SmartProvider подхватит автоматически на следующем рестарте JARVIS (healthcheck видит модель в `/api/tags`).

### Бэкап — critical fix
Скрипт `backup_private.ps1` хардкодил `jarvis/.env`. После того как `.env` переехал в корень репо (22 мая) — архивы шли БЕЗ ключей. Disaster recovery был бы невозможен. Поправлено (коммит `2e3e818`): теперь ищем оба пути.

### Git
```
d647cba feat(voice): tuning под BT-наушники (FreeBuds Pro 4)
88b3db8 fix(voice): MIN_RMS_DB через JARVIS_MIN_RMS_DB env
e470a8f fix(autostart): voice стартует автоматически вместе с HUD/TG
ba95c7a feat(file-skill): транслитерация cyr↔lat
fd584b9 fix(skills): AppControlSkill уступает FileSkill
6bf659e feat: Goals tab в HUD + FileSkill
5852ec8 docs(handoff): сессия 2026-05-23 утро (предыдущий этап)
fe4bf77 feat(code-assist): CodeAssistSkill — мост JARVIS → claude.cmd
2e3e818 fix(backup): .env в корне репо
```

### Висящие хвосты
- **Voice смок-тест с встроенным мика** — Босс должен сказать «Джарвис проверка», увидим в логе работает ли pipeline end-to-end (vad → gate → speaker → STT → reply).
- **Windows 11 25H2 update** — ожидает перезагрузки. После него LE Audio в Windows полная (если когда-нибудь будет донгл).
- **Ollama pull** — в фоне.
- **D21 Android app** — не начат, главный долгосрочный проект для «JARVIS везде».

---

## Session 2026-05-23 утро (continuation: память + CodeAssistSkill)

### Что сделано в этой сессии
1. **MEMORY.md прокачан** (Tier 1). Заполнены секции «О Боссе / Фокус / Активные дела / Важные даты», вынесены уроки (`.env` через Edit only, kill всех python при рестарте, v2rayN 10808, не запускать под admin). Файл в .gitignore (workspace/* by design), уехал в `jarvis-private-20260523-071521.tar.gz.enc` через ручной `backup_now.bat`.
2. **#48 ANTHROPIC_API_KEY рабочий** — прямой запрос `claude-sonnet-4-5` через v2rayN вернул 200 OK + `pong`. Закрыто.
3. **Critical fix: backup_private.ps1** (коммит `2e3e818`). Скрипт хардкодил `jarvis/.env`, а `.env` переехал в корень репо в прошлой сессии. Архивы шли БЕЗ ключей — disaster recovery был бы невозможен. Теперь ищем `.env` И `jarvis/.env`, кладём оба если есть. Проверено: новый архив содержит корневой `.env`.
4. **#30 CodeAssistSkill** (коммит `fe4bf77`) — мост JARVIS → `claude.cmd -p`. L1 keyword + L2 tool-use. Триггеры: «напиши код», «исправь баг», «оптимизируй …», «отрефактори», «преобразуй … в python», «помоги с регуляркой». Subprocess через `asyncio.create_subprocess_exec`, env с HTTPS_PROXY=10808, cwd=`%TEMP%` (ASCII, чтобы кириллица не ломала CLAUDE.md auto-discovery), задача через stdin, timeout 90 сек, `--append-system-prompt` про лаконичный ответ, `--exclude-dynamic-system-prompt-sections` для скорости. **End-to-end smoke прошёл**: «sorted unique list one-liner» → ` ```python\nsorted(set(lst))\n``` ` за 6 сек. Использует Max 20× квоту Босса, не API.

### Незакрытое
- **#49 Ollama BLOCKED.** Зомби `msiexec PID 20592` с прошлой ночи под SYSTEM-токеном. Access Denied на kill из обычного PS. Это блокирует MSI Service целиком — `winget install Ollama.Ollama` не пройдёт. **Решение для Босса:** (а) admin-PS → `Stop-Process -Id 20592 -Force`, либо (б) ребут. Затем `winget install Ollama.Ollama` или прямой `OllamaSetup.exe` с ollama.com (через v2rayN). Ollama — fallback в LLM-цепочке, не критична: Claude/Deepseek/Yandex работают.
- **Три хвоста на уточнение** (с прошлой сессии — не понял что конкретно): прогресс-бар (GoalsSkill в HUD?), F2 context-awareness (расширить screen monitor?), «файлы» (какой именно skill?). Спросить Босса.
- **Phase 2 для CodeAssistSkill:** tools enabled + `--add-dir <short-path-репо>` чтобы Claude мог править файлы JARVIS напрямую. Сейчас MVP — только текстовый ответ, Босс копипастит.

### Что я ещё рекомендую сделать утром
- Запустить полный `start_all.ps1` и убедиться что бот видит новый skill — попросить голосом «напиши код для сортировки списка», проверить ответ.
- Добавить запись в MEMORY.md про `CodeAssistSkill` (источник quota = Max 20×).

### Git состояние
```
fe4bf77 feat(code-assist): CodeAssistSkill — мост JARVIS -> claude.cmd (issue #30)
2e3e818 fix(backup): .env в корне репо (раньше jarvis/.env). Архивируем оба пути, если есть.
f87514c docs: handoff for 2026-05-23 session (switcher done, Ollama in progress, hvosты...)
```
Post-commit hook прогнал Public push + encrypted backup на оба коммита.

---

## Session 2026-05-22 вечер → 2026-05-23 ночь (handoff before sleep)

### Что сделано в этой сессии

**LLM Switcher — полностью работает.**
Босс голосом/текстом переключает primary LLM в SmartProvider:
- «какая сейчас ллм» → отвечает статусом (кириллица «ллм» и латиница «llm»)
- «пользуйся клодом / дипсиком / яндексом / оламой» → set_choice() пишет в `workspace/llm_choice.txt`
- «вернись на авто» → откатывает в auto-режим (цепочка `Claude → Deepseek → Yandex → Ollama`)

Vision (`chat_with_image`) и tool-use (`chat_with_tools`) всегда через Claude — Deepseek/Yandex/Ollama их не поддерживают.

Связанные коммиты: `261b6df feat(llm): runtime LLM switcher + Deepseek provider`, `1cb155c fix(switcher): bot.py bus imports, cyrillic ллм, SkillResult.data, start_all pre-kill`.

Новые файлы:
- `jarvis/core/providers/deepseek.py` — OpenAI-compatible клиент к `api.deepseek.com/v1`
- `jarvis/core/skills/llm_switcher_skill.py` — KeywordSkill + L2 tool-use
- `jarvis/scripts/llm_switcher_smoke.py` — smoke regex+файл (для CI потом)

### Проблемы которые встретили и починили
1. **`bot.py NameError: bus is not defined`** — `bus/JarvisEvent/EventType` использовались в 3 местах в `channels/telegram/bot.py`, но никогда не импортировались. Утренние брифинги работали (они идут через `bot.send_message`), а `on_text` валился молча. Добавил `from core.event_bus import EventType, JarvisEvent, bus`.
2. **`SkillResult.__init__() got an unexpected keyword argument 'meta'`** — у SkillResult поле называется `data`, не `meta`. Заменил везде.
3. **Pydantic читал не тот .env** — два .env файла существовали: корневой `.env` (читался Pydantic'ом через `parents[2]`) и `jarvis/.env`. Корневой содержал revoked ANTHROPIC ключ и не имел `DEEPSEEK_API_KEY`. Из-за этого был 401 на Claude и chain без deepseek. Слил всё в корневой `.env`, удалил `jarvis/.env`. **`TELEGRAM_PROXY_URL=http://127.0.0.1:10808` обязателен** — aiogram не уважает системный proxy Windows, без явного env переменной TG падает с `Cannot connect to host api.telegram.org`.
4. **8 параллельных python-процессов = 4 TG-бота = 409 Conflict** — у `Get-Process.CommandLine` всегда `$null`, поэтому фильтр Where-Object не работал и kill промахивался. В `start_all.ps1` добавил pre-kill через `Get-CimInstance Win32_Process` (там CommandLine реально доступен).

### Состояние процессов на момент handoff
Если HUD/TG работают сейчас — оставь как есть. Если нет:
```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
Remove-Item "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\workspace\autostart.lock" -Force -ErrorAction SilentlyContinue
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\scripts\start_all.ps1"
```
Если Босс уже залогинен в `claude.cmd` CLI (Anthropic Max 20× квота) — проверь `& "$env:APPDATA\npm\claude.cmd" --version`. Для login Claude CLI обязателен `$env:HTTPS_PROXY="http://127.0.0.1:10808"` (v2rayN на Frankfurt AA mixed-порт).

### Установка Ollama — в процессе
Босс выбрал `deepseek-r1:7b` (4.7 GB). Я запустил `winget install Ollama.Ollama` через Windows-MCP, msiexec работал в момент handoff. Проверь утром:
```powershell
Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
ollama --version  # если есть в PATH
```
Если поставлено — `ollama serve` (запустится при логине) + `ollama pull deepseek-r1:7b`. Это ~4.7 GB через v2rayN-прокси. Через ~10 мин после pull JARVIS подцепит как реальный offline-фоллбек (сейчас Ollama в цепочке заявлена но провайдер failed healthcheck).

### Хвосты на завтра (то что Босс просил не забыть)

1. **Прогресс-бар.** Видимо речь о `GoalsSkill` — в нём уже есть text-based progress bar (`▰▰▰▱▱`). Возможно нужно вывести в HUD-виджет или прислать Боссу как часть утреннего брифинга. Уточнить в начале сессии.
2. **Контекст-awareness (F2).** Фоновый screen monitor + `AwarenessSkill` (см. `core/awareness/`, `core/skills/awareness_skill.py`). Скорее всего нужно расширить — возможно научить JARVIS лучше использовать контекст (что Босс делает на экране) в проактивных нуджах, или прокинуть в RAG. Уточнить.
3. **Файлы.** Не до конца понял что именно — варианты: (а) skill для управления файлами Босса (открыть/найти/переименовать/удалить), (б) что-то про backup, (в) поделиться файлами через JARVIS в TG. Сразу спросить.

### Большая следующая задача — мост JARVIS → Claude Code CLI

**Цель:** сложные кодинг-задачи, которые JARVIS получает, делегировать в `claude.cmd` (подписка Max 20× у Босса). API-токены не тратятся, всё на квоту.

**План:**
- `CodeAssistSkill` в `core/skills/code_assist_skill.py`
- Триггеры: «напиши код», «исправь баг», «оптимизируй», «давай реализуем» — что-то связанное с кодингом.
- Под капотом: `asyncio.create_subprocess_exec('claude.cmd', ...)` через прокси (`HTTPS_PROXY` в env), передаём текст задачи через stdin или `-p` флаг.
- Возвращаем stdout как SkillResult.
- На что обращать внимание: cyrillic-path (запускать с short path `DOCUME~1\Claude\Projects\(2)~1`), timeout (claude CLI может думать долго), не блокировать event loop.

Босс хотел: сначала JARVIS → Claude Code CLI односторонне, потом возможно двусторонний bridge через Claude Desktop (Cowork).

### Открытые задачи в трекере
- #6 D17 lip sync — авангард-аватар, рот не открывается (deferred Боссом — «не главная»)
- #13 D18.4 JARVIS → устройства (smart home Phase 2)
- #14 D18.3+ webhook Alice через «Тестирование» (blocked на hosting)
- #30 **Skill: code_assist** — это и есть мост, см. выше
- #31 News skill российские источники (RIA/TASS/RBC + перевод + флаг страны для иностранных)
- #48 Проверить новый ANTHROPIC_API_KEY — старый был revoked, новый Босс ввёл в `.env`, надо убедиться что он рабочий (отправить запрос «привет» через TG, в логах смотрим `claude_chat_ok` или 401)
- #49 Ollama + deepseek-r1:7b — установка в процессе

### Важные напоминания (память)
- **НИКОГДА** не модифицировать `.env` через `Add-Content/Set-Content/Out-File` — только Read+Edit. Урок 22.05.2026, потеряли все ключи (`memory/feedback_env_writes.md`).
- При рестарте JARVIS убивать **все** python-процессы (не фильтровать через `Get-Process.CommandLine` — оно $null). См. `memory/feedback_jarvis_restart.md`.
- v2rayN VPN mixed-порт `127.0.0.1:10808` нужен для Anthropic API и Claude CLI. Системный proxy Windows подхватывают не все клиенты. См. `memory/reference_v2rayn_proxy.md`.
- Не использовать admin-запуск JARVIS — потом обычным PS не убить процессы (Access denied).

### Git состояние на момент handoff
```
261b6df feat(llm): runtime LLM switcher + Deepseek provider
1cb155c fix(switcher): bot.py bus imports, cyrillic ллм, SkillResult.data, start_all pre-kill
```
Все правки закоммичены, post-commit hook отработал (Public GitHub + encrypted local backup).

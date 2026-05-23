# D21 — Android JARVIS App. Архитектура и план

**Дата:** 23.05.2026
**Статус:** specification (код ещё не написан)
**Цель:** «Тони Старк-режим» — JARVIS в твоём кармане, FreeBuds в ушах, доступ везде где есть 4G.

---

## Финальные решения по архитектуре

| Развилка | Выбор | Обоснование |
|---|---|---|
| Где backend | **Моноблок + Cloudflare Tunnel** | Данные локально. Tunnel = бесплатный HTTPS-публичный URL. Готовим миграцию на новый ПК (Xeon+RTX 3070). |
| Stack Android | **Kotlin Native + Jetpack Compose** | Полный доступ к BT/voice/notifications. Долгосрочно надёжный путь. |
| STT (Voice) | **Android SpeechRecognizer / Yandex SpeechKit Android SDK** | Низкая латенция (~1-2 сек), минимум трафика, оффлайн-режим Google. |
| TTS | **JARVIS синтез (Yandex Alena) → mp3 → телефон играет** | Голос одинаковый везде (как Алиса), привычный. |

---

## Системная диаграмма

```
┌─────────────────────────────────────┐         ┌──────────────────────────┐
│  ANDROID PHONE                       │         │  МОНОБЛОК (JARVIS)       │
│  ┌──────────────────────────────┐    │         │                          │
│  │ Jetpack Compose UI            │    │         │  ┌────────────────────┐ │
│  │ ┌──────┐ ┌──────┐ ┌────────┐ │    │         │  │  FastAPI (порт     │ │
│  │ │ Chat │ │ Mic  │ │ Push   │ │    │         │  │  8000) — расширим  │ │
│  │ └───┬──┘ └──┬───┘ └────┬───┘ │    │         │  │  run_web_hud.py    │ │
│  │     │       │          │     │    │         │  │                    │ │
│  │ ┌───▼───────▼──────────▼──┐  │    │         │  │  Новые endpoints:  │ │
│  │ │  JARVIS Repository      │  │    │         │  │  /api/v1/auth/...  │ │
│  │ │  (Retrofit + OkHttp)    │◄─┼────┼─────────┼──┤  /api/v1/chat/...  │ │
│  │ └─────────────────────────┘  │    │ HTTPS   │  │  /api/v1/voice/... │ │
│  │                              │    │ + SSE   │  │  /api/v1/tts/...   │ │
│  │ ┌─────────────────────────┐ │    │         │  │  /api/v1/stream    │ │
│  │ │ SpeechRecognizer (STT)  │ │    │         │  │  (SSE для push)    │ │
│  │ └─────────────────────────┘ │    │         │  └──────────┬─────────┘ │
│  │ ┌─────────────────────────┐ │    │         │             │           │
│  │ │ ExoPlayer (TTS play mp3)│ │    │         │  ┌──────────▼─────────┐ │
│  │ └─────────────────────────┘ │    │         │  │  Router + Skills   │ │
│  │ ┌─────────────────────────┐ │    │         │  │  (как сейчас)      │ │
│  │ │ FCM (push в фоне)       │◄┼────┼─────────┤  │                    │ │
│  │ └─────────────────────────┘ │    │         │  └────────────────────┘ │
│  └──────────────────────────────┘    │         └──────────────────────────┘
│                                       │                  ▲
│  ┌──────────────────────────────┐    │                  │
│  │ FreeBuds Pro 4 ←──BT LE──────┼────┼──────────────────┘
│  └──────────────────────────────┘    │      Cloudflare Tunnel
└──────────────────────────────────────┘  (HTTPS-публичный URL без
                                          открытия портов роутера)
```

**Поток типичного запроса:**

```
1. Босс: "Джарвис, какая погода?"
2. SpeechRecognizer → text="какая сейчас погода"
3. Android → POST https://jarvis.yourtunnel.com/api/v1/chat/text
              Body: {"text": "какая сейчас погода"}
              Auth: Bearer <JWT>
4. Backend: Router → WeatherSkill → SkillResult
5. TTS-Service (Yandex Alena) → mp3-файл
6. Backend → JSON response: {
       "reply": "В Сыктывкаре +18°...",
       "tts_url": "/api/v1/tts/audio/abc123.mp3"
   }
7. Android: показывает текст + ExoPlayer streams mp3 → FreeBuds.
```

---

## Backend изменения

### Новые модули

```
jarvis/channels/mobile/
  ├── __init__.py
  ├── server.py            # FastAPI router /api/v1/* (mount в run_web_hud)
  ├── auth.py              # JWT issue/verify + PIN
  ├── schemas.py           # Pydantic модели request/response
  ├── push.py              # FCM client + queue
  └── README.md
```

### Endpoints (v1 API)

| Endpoint | Метод | Что |
|---|---|---|
| `POST /api/v1/auth/login` | — | PIN → JWT (срок 30 дней) |
| `POST /api/v1/auth/refresh` | JWT | Обновить JWT |
| `POST /api/v1/chat/text` | JWT | Text-message → reply + tts_url |
| `POST /api/v1/chat/voice-result` | JWT | Готовый text от STT → reply + tts_url (= chat/text по сути) |
| `GET /api/v1/tts/audio/{id}.mp3` | JWT (через ?token=) | Стрим mp3 (Range support) |
| `GET /api/v1/stream` | JWT (через query) | SSE: proactive nudges, alarm fires, scheduled tasks |
| `POST /api/v1/push/register` | JWT | Регистрация FCM device token |
| `GET /api/v1/status` | JWT | Health check + LLM provider status |

### Auth (PIN + JWT + Biometric)

- **PIN при первом запуске** (6 цифр), хранится в `.env` как `JARVIS_MOBILE_PIN=hash(...)`.
- JWT issuance с `jti` для возможности revoke.
- Срок access-token: 30 дней.
- Refresh-token: 90 дней.
- На Android: PIN → биометрия после первого ввода (FingerprintManager / BiometricPrompt). JWT в `EncryptedSharedPreferences`.

### TTS-сервис

- Существующий `jarvis/channels/web_hud/server.py` уже имеет `/api/avatar/speak` который генерирует WAV через Yandex Alena. Переиспользуем + конвертация WAV → MP3 для трафика.
- Кэш `/api/v1/tts/audio/{id}.mp3` с TTL 1 час (как `/api/avatar/audio/{id}`).
- Mp3 ~24 kbps mono = 6 KB/сек. На минуту речи = 350 KB. Норм для 4G.

### Cloudflare Tunnel

- Бесплатный, без открытия портов на роутере.
- `cloudflared.exe` на моноблоке → постоянное outbound-соединение к Cloudflare → публичный HTTPS URL `https://jarvis-<random>.trycloudflare.com`.
- Можно привязать собственный домен (если есть).
- Конфигурация через `cloudflared tunnel create jarvis-mobile`.

---

## Android-клиент структура

### Stack

| Слой | Технология |
|---|---|
| Язык | Kotlin 1.9+ |
| UI | Jetpack Compose + Material 3 |
| Архитектура | MVVM + Repository + UseCase |
| DI | Hilt (Dagger) |
| Network | Retrofit + OkHttp + Moshi |
| Streaming | OkHttp SSE + OkHttp WebSocket (зарезервировано) |
| Storage | DataStore (prefs) + EncryptedSharedPreferences (secrets) |
| Voice | `SpeechRecognizer` + опц. Yandex SpeechKit SDK |
| Audio | ExoPlayer (Media3) для TTS playback |
| Push | Firebase Cloud Messaging (FCM) |
| Auth | AndroidX Biometric |
| Min SDK | 26 (Android 8.0) |
| Target SDK | 35 (Android 15) |

### Структура модулей

```
android/
├── app/
│   ├── src/main/
│   │   ├── java/com/jarvis/mobile/
│   │   │   ├── MainActivity.kt
│   │   │   ├── JarvisApp.kt
│   │   │   ├── ui/
│   │   │   │   ├── chat/
│   │   │   │   │   ├── ChatScreen.kt
│   │   │   │   │   ├── ChatViewModel.kt
│   │   │   │   │   └── MessageBubble.kt
│   │   │   │   ├── login/
│   │   │   │   │   ├── LoginScreen.kt
│   │   │   │   │   └── LoginViewModel.kt
│   │   │   │   └── theme/  (Compose theme)
│   │   │   ├── data/
│   │   │   │   ├── api/   (Retrofit interfaces)
│   │   │   │   ├── repo/  (Repository pattern)
│   │   │   │   └── storage/  (DataStore + Encrypted)
│   │   │   ├── voice/
│   │   │   │   ├── SttManager.kt   (SpeechRecognizer wrapper)
│   │   │   │   └── TtsPlayer.kt    (ExoPlayer wrapper)
│   │   │   ├── push/
│   │   │   │   └── FcmService.kt
│   │   │   └── auth/
│   │   │       ├── BiometricGate.kt
│   │   │       └── TokenStore.kt
│   │   ├── res/  (icons, themes, strings)
│   │   └── AndroidManifest.xml
│   └── build.gradle.kts
└── settings.gradle.kts
```

### Минимальный UI (Phase 1)

**Login screen:**
- Текстовое поле PIN (6 цифр).
- Кнопка «Войти».
- После первого успешного логина — toggle «Использовать биометрию».

**Chat screen:**
- Список сообщений (Compose LazyColumn): user — справа, JARVIS — слева.
- Низ: текстовое поле + 2 кнопки: «Mic» (press-to-talk) и «Send».
- При зажатии Mic — анимация записи + текст результата SpeechRecognizer наверху.
- При получении ответа: показ текста + автоматический start ExoPlayer на TTS-url.

**Settings screen:**
- Server URL (tunnel address).
- Сменить PIN.
- Toggle: «JARVIS говорит вслух» (включить/выключить TTS-play).
- Toggle: «Push-уведомления».
- Кнопка «Logout».

### Permissions (AndroidManifest)

```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.RECORD_AUDIO"/>
<uses-permission android:name="android.permission.USE_BIOMETRIC"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/> <!-- API 33+ -->
<uses-permission android:name="android.permission.BLUETOOTH_CONNECT"/>
```

---

## Sprint-план (~11 часов реализации)

| Sprint | Задача | Время | Зависит от |
|---|---|---|---|
| **A1** | Backend: Cloudflare Tunnel setup на моноблоке | 30 мин | Босс регистрирует CF account |
| **A2** | Backend: новые endpoints /api/v1/auth + /chat/text | 1.5 ч | — |
| **A3** | Backend: TTS-сервис + mp3 кэш | 1 ч | A2 |
| **A4** | Backend: SSE-stream + FCM-push | 1.5 ч | Босс создаёт Firebase project |
| **B1** | Android skeleton (Gradle, Manifest, Hilt) | 1 ч | Android Studio установлен |
| **B2** | Login screen + JWT storage | 1 ч | A2 |
| **B3** | Chat screen (UI + Retrofit) | 2 ч | B2, A2 |
| **B4** | Voice: SpeechRecognizer + ExoPlayer | 1.5 ч | B3 |
| **B5** | Push: FCM service + notification | 1 ч | A4 |
| **C1** | Smoke-тест end-to-end | 1 ч | всё выше |

**Итого:** 11.5 часов чистой работы.

---

## Что нужно от Босса (последовательно)

### Шаг 1 (15 минут) — Аккаунты

1. **Cloudflare Tunnel** — зарегистрироваться на cloudflare.com, добавить домен (если есть свой) или использовать `*.trycloudflare.com`. Бесплатно.
2. **Firebase project** — console.firebase.google.com → New project → название «JARVIS Mobile». Достать `google-services.json` для Android.

### Шаг 2 (40 минут) — Установить tooling

3. **Android Studio** (Hedgehog или новее) — 1.5 GB загрузка, 8 GB места. Установить с дефолтными settings.
4. Подключить **физический Android телефон** через USB → включить Developer Options → USB debugging.

### Шаг 3 (10 минут) — Конфигурация

5. Дать мне публичный URL Cloudflare Tunnel.
6. Дать `google-services.json` от Firebase (можно прислать в чате — секрет, но не критичный).
7. Придумать PIN (6 цифр).

После этого я начинаю реализацию по Sprint-плану.

---

## Известные риски и решения

| Риск | Решение |
|---|---|
| Anthropic API блочит РФ-IP — JARVIS вызывает через v2rayN. Из туннеля будет ходить через тот же v2rayN на моноблоке — OK. | Уже работает. |
| Telegram-бот может конфликтовать с mobile-app (двойные ответы). | Не будет — TG и mobile разные channel в JARVIS, не пересекаются. |
| FCM требует SHA-1 fingerprint от Android key для регистрации в Firebase. | Я генерирую debug keystore при создании проекта, даю Боссу SHA-1 для Firebase. |
| Cloudflare Tunnel перезапускается → URL может смениться. | Платный план (5$/мес) даёт стабильный URL, или используем named tunnel. |
| Battery drain от фонового voice. | Phase 1 — только press-to-talk (нет фонового voice). Phase 2 — wake-word (когда B8 готов). |
| Android воспроизведение TTS через FreeBuds — autoroute. | OS сама направит на default output (BT-наушники если подключены). |

---

## Что НЕ делаем в Phase 1

- Wake-word «Джарвис» на телефоне (Phase 2, после B8).
- Foreground service для фонового voice (Phase 2).
- iOS-версия (через год если будет смысл).
- Виджет на главном экране Android (Phase 2).
- Тёмная/светлая темы (Phase 2 — пока Material You default).
- Многопользовательский режим (только Босс).
- Передача звонков / SMS через JARVIS (не цель).

---

## Phase 2 (после MVP)

- **Wake-word на телефоне** — после B8 («Джарвис» по-русски).
- **Foreground service** — фоновый voice даже при заблокированном экране.
- **Виджет** — быстрый чат прямо с домашнего экрана.
- **Tasker integration** — JARVIS в твоих Android-автоматизациях.
- **Apple Watch / Wear OS** — для удалённого триггера.
- **CarPlay-style mode** — большие кнопки в машине.

---

*Спец зафиксирован: 23.05.2026.*

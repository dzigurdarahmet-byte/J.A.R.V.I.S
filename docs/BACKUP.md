# J.A.R.V.I.S — Backup & Restore

## Архитектура

JARVIS использует **два независимых backup-канала**:

1. **Public GitHub** — `github.com/dzigurdarahmet-byte/J.A.R.V.I.S`
   - Содержит: код, скрипты, документация, конфиги без секретов
   - НЕ содержит: `workspace/` (память, биометрия), `.secrets/` (токены)
   - Реализация: `git archive HEAD` -> распаковка в `C:\jarvis-bak\snapshot\` -> fresh `git init` -> force push на main. Изолированно от основного репо, snapshot без истории.
   - Зачем Public: пока проект маленький, потом будет переведён в Private

2. **Local encrypted** — `C:\Backups\jarvis\`
   - Содержит: `jarvis/workspace/` + `jarvis/.secrets/`
   - Формат: AES-256-CBC шифрованный tar.gz
   - Имя: `jarvis-private-YYYYMMDD-HHMMSS.tar.gz.enc`
   - Passphrase: `C:\Backups\jarvis\.passphrase` (одна на все архивы)
   - Ротация: 14 последних архивов

## Триггеры backup

| Событие | Что бэкапится |
|---------|---------------|
| `git commit` на main | Оба канала (через `.git/hooks/post-commit`) |
| Ручной запуск `backup_full.ps1` | Оба канала |
| `backup_full.ps1 -SkipPublic` | Только encrypted local |
| `backup_full.ps1 -SkipPrivate` | Только Public push |
| `backup_private.ps1` напрямую | Только encrypted local |

Backup занимает ~5 секунд и блокирует завершение `git commit`. Это сознательное решение — асинхронный фон через `cmd /c start` ломается на кириллице в пути `ДЖАРВИС (2)`.

## Куда уходит passphrase

При первом запуске `backup_private.ps1` создаёт случайную base64-passphrase (48 байт энтропии) в `C:\Backups\jarvis\.passphrase` с ACL `только текущий пользователь`. **Этот файл — единственный ключ к расшифровке.** Если потеряешь — архивы превращаются в мусор.

**Действие Босса при первом запуске:**
1. Открой `C:\Backups\jarvis\.passphrase` в Notepad
2. Скопируй содержимое (одна строка base64)
3. Сохрани в KeePass / 1Password / Bitwarden / на бумаге в сейф

## Что лежит в Public GitHub

```
J.A.R.V.I.S/
├── .gitignore
├── .env.example          # шаблон env без значений
├── README.md
├── ROADMAP.md
├── docs/
│   └── BACKUP.md         # этот файл
├── jarvis/
│   ├── channels/         # local_voice, telegram, web_hud
│   ├── core/             # alerts, briefings, memory, voice, providers...
│   ├── skills/           # weather, news, music, maps, finance, ...
│   ├── scripts/          # enroll_voice, backup_*, smoke_test, ...
│   └── tests/
├── run_telegram.py
├── run_voice.py
└── run_web_hud.py
```

Один коммит — orphan snapshot на момент последнего `git commit` локально. История перепишется при следующем push.

## Что лежит в encrypted local

```
jarvis/
├── workspace/
│   ├── MEMORY.md          # факты о Боссе
│   ├── SOUL.md            # личность Джарвиса
│   ├── USER.md            # профиль владельца
│   ├── alerts_state.json  # курсы валют, последние alerts
│   ├── daily/YYYY-MM-DD.md  # ежедневный дневник разговоров
│   ├── vector_db/         # RAG-индекс памяти
│   ├── owner_voice.npy    # биометрия Босса (256-d embedding)
│   ├── voice_run.log/err  # runtime логи (не критично, для дебага)
│   └── audio_tmp/         # временные WAV (не критично)
└── .secrets/
    ├── google_credentials.json
    ├── google_token.json
    └── github_pat
```

## Восстановление

### Вариант A: Только код (на новой машине)

```powershell
# 1. Клонируем Public repo
git clone https://github.com/dzigurdarahmet-byte/J.A.R.V.I.S.git "ДЖАРВИС (2)"
cd "ДЖАРВИС (2)"

# 2. Создаём venv и ставим зависимости
cd jarvis
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Копируем шаблон env и заполняем
copy .env.example .env
notepad .env  # вставь ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, ...

# 4. Запускаем — workspace/ создастся пустым
python run_telegram.py
```

Без приватного backup'а Джарвис запустится, но без памяти, без биометрии Босса и без Google OAuth.

### Вариант B: Полное восстановление с приватной памятью

```powershell
# 1-3. Как в варианте A — клонируем код, делаем venv.

# 4. Расшифровываем последний приватный архив
$archive = (Get-ChildItem 'C:\Backups\jarvis\jarvis-private-*.tar.gz.enc' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$passFile = 'C:\Backups\jarvis\.passphrase'  # ИЛИ passphrase из KeePass в свежий файл
$tempTar = "$env:TEMP\jarvis-restore.tar.gz"

& 'C:\Program Files\Git\mingw64\bin\openssl.exe' enc -d -aes-256-cbc -pbkdf2 -iter 600000 `
    -in $archive -out $tempTar -pass "file:$passFile"

# 5. Распаковываем поверх репо
cd "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)"
& "$env:windir\System32\tar.exe" -xzf $tempTar
Remove-Item $tempTar -Force

# 6. Готово — workspace/ + .secrets/ восстановлены
```

### Вариант C: Восстановление БЕЗ файла passphrase (только passphrase из KeePass)

```powershell
# Создай $passFile с passphrase из KeePass
$passphrase = "ТВОЯ_PASSPHRASE_ИЗ_KEEPASS"
$passFile = "$env:TEMP\restore.pass"
Set-Content -Path $passFile -Value $passphrase -NoNewline

# Дальше как в варианте B шаг 4
```

## Проверка целостности backup

```powershell
# Запусти backup_private.ps1 и проверь что archive расшифровывается round-trip
.\jarvis\scripts\backup_private.ps1

# Расшифруй последний и посмотри содержимое (без распаковки)
$archive = (Get-ChildItem 'C:\Backups\jarvis\jarvis-private-*.tar.gz.enc' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$test = "$env:TEMP\test.tar.gz"
& 'C:\Program Files\Git\mingw64\bin\openssl.exe' enc -d -aes-256-cbc -pbkdf2 -iter 600000 `
    -in $archive -out $test -pass "file:C:\Backups\jarvis\.passphrase"
& "$env:windir\System32\tar.exe" -tzf $test | Select-Object -First 20
Remove-Item $test -Force
```

Должно показать `jarvis/workspace/MEMORY.md`, `jarvis/workspace/owner_voice.npy`, `.secrets/google_token.json` и т.д.

## Что НЕЛЬЗЯ делать

- Не коммитить `jarvis/workspace/owner_voice.npy` в Public — это голосовая биометрия Босса. С ней можно натренировать voice clone и звонить от его имени.
- Не коммитить `jarvis/.secrets/` — там Google OAuth refresh token (доступ к Calendar) и GitHub PAT.
- Не публиковать passphrase в чате, Telegram, email — она ключ ко ВСЕМУ приватному backup'у.
- Не делать `git push origin main` руками — это уведёт ВСЮ локальную историю с приваткой в Public. Используй только `backup_full.ps1` (он делает orphan-snapshot без истории).

## Перевод репозитория в Private

Когда проект станет большим — Settings → General → Danger Zone → **Change repository visibility** → **Change visibility to private**. Дальше ничего менять не нужно — orphan-snapshot push продолжит работать через тот же PAT.

После перевода в Private можно ослабить .gitignore и пушить `workspace/` тоже — но это решение Босса, не моё.

## Troubleshooting

**`tar (child): Cannot connect to C: resolve failed`**
git-bash подхватил свой cygwin tar, который видит `C:\` как `host=C`. В backup_private.ps1 используется явный путь `$env:windir\System32\tar.exe` — это уже исправлено.

**`Public push FAILED: 401 Unauthorized`**
Истёк PAT. Создай новый на GitHub Settings → Developer settings → PAT (classic) с scope `repo`, и положи в `jarvis/.secrets/github_pat` (без переноса строки в конце).

**`openssl не найден`**
Установи Git for Windows — он включает openssl в `C:\Program Files\Git\mingw64\bin\`.

**Hook не сработал на commit**
Проверь что `.git/hooks/post-commit` существует и имеет права на исполнение: `chmod +x .git/hooks/post-commit` из git-bash. Hook работает только на ветке main.

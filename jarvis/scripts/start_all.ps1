# start_all.ps1 — autostart for J.A.R.V.I.S. (HUD + Telegram).
# Запускается из shell:startup через ярлык JARVIS_autostart.lnk.
# Mutex-lock защищает от слишком частого ручного перезапуска.
# Pre-kill убивает старые JARVIS python-процессы (фильтр по .venv-пути)
# — без этого несколько TG-ботов с одним токеном дерутся за getUpdates
# и Telegram возвращает 409 Conflict (lesson 22 мая 2026).

$jarvis = "C:\Users\Staho\DOCUME~1\Claude\Projects\(2)~1\jarvis"
$py     = "$jarvis\.venv\Scripts\python.exe"
$logs   = "$jarvis\workspace"
$lock   = "$logs\autostart.lock"

# Mutex: если уже запускались <60 сек назад — выходим
# (защита от тройного клика по ярлыку, не от логичного рестарта).
if (Test-Path $lock) {
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    if ($age.TotalSeconds -lt 60) { exit 0 }
}
"started at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $lock -Encoding utf8

# ── Pre-kill старых JARVIS-процессов ────────────────────────────────
# CommandLine у Win32_Process всегда доступен (в отличие от
# Get-Process). Фильтр по .venv-пути убивает только наши процессы
# и не заденет посторонние python.exe.
# Также матчим "DOCUME~1" (short path) и "ДЖАРВИС" (long path) —
# autostart использует short, ручной запуск может быть long.
$killed = @()
try {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like "*\.venv\Scripts\python.exe*" -and
                ($_.CommandLine -like "*run_web_hud.py*" -or
                 $_.CommandLine -like "*run_telegram.py*" -or
                 $_.CommandLine -like "*run_voice.py*")
            )
        } |
        ForEach-Object {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                $script:killed += $_.ProcessId
            } catch {}
        }
} catch {}
if ($killed.Count -gt 0) {
    "$(Get-Date -Format 'HH:mm:ss') pre-kill old JARVIS pids: $($killed -join ',')" |
        Out-File "$logs\autostart_main.log" -Append -Encoding utf8
    Start-Sleep -Seconds 2  # дать ОС освободить порт 8000 и TG-сокеты
}

function Start-Subsystem {
    param(
        [string] $Script,
        [string] $LogName,
        [string[]] $ScriptArgs = @()
    )
    $logFile = "$logs\autostart_$LogName.log"
    $errFile = "$logs\autostart_$LogName.err"
    $argList = @("$jarvis\$Script") + $ScriptArgs
    Start-Process -WindowStyle Hidden `
        -FilePath $py `
        -ArgumentList $argList `
        -WorkingDirectory $jarvis `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError $errFile
}

# После pre-kill оба подсистемы должны быть мёртвы — поднимаем чисто.
# Skip-check по порту оставлен на случай если pre-kill не сработал
# (например, права не позволили убить процесс).
$hud_busy = $false
try {
    $hud_busy = $null -ne (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
} catch {}
if (-not $hud_busy) { Start-Subsystem -Script "run_web_hud.py" -LogName "hud" }

# Skip TG если каким-то чудом всё ещё жив (pre-kill промазал).
$tg_running = $false
try {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object {
            if ($_.CommandLine -like "*run_telegram.py*") { $script:tg_running = $true }
        }
} catch {}
if (-not $tg_running) { Start-Subsystem -Script "run_telegram.py" -LogName "telegram" }

# Voice — включён. Стартуем только если ещё не работает (run_voice.py
# держит .venv-процесс с одной из подкоманд: vad / wake / push-to-talk).
$voice_running = $false
try {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object {
            if ($_.CommandLine -like "*run_voice.py*") { $script:voice_running = $true }
        }
} catch {}
if (-not $voice_running) { Start-Subsystem -Script "run_voice.py" -LogName "voice" -ScriptArgs @("vad") }
# ============================================================
# backup_private.ps1 — encrypted local backup of workspace/ + .secrets/
# ============================================================
# Что бэкапим:
#   - jarvis/workspace/  (MEMORY, SOUL, USER, daily/, alerts_state,
#                         vector_db, owner_voice.npy, .gitkeep)
#   - jarvis/.secrets/   (Google OAuth, GitHub PAT)
# Куда:
#   - C:\Backups\jarvis\jarvis-private-YYYYMMDD-HHMMSS.tar.gz.enc
# Шифрование:
#   - AES-256-CBC через openssl, pbkdf2 600000 iterations
#   - Passphrase в C:\Backups\jarvis\.passphrase (создаётся один раз)
# Ротация: 14 последних архивов.
# ============================================================

$ErrorActionPreference = "Stop"

# RepoRoot — две директории вверх от scripts/: jarvis/scripts/ -> jarvis/ -> repo
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$BackupRoot = "C:\Backups\jarvis"
$PassFile = Join-Path $BackupRoot ".passphrase"
$KeepLast = 14

# === Подготовка ===
if (-not (Test-Path $BackupRoot)) {
    New-Item -Path $BackupRoot -ItemType Directory -Force | Out-Null
    Write-Host "Создал директорию $BackupRoot"
}

# === Passphrase: одноразовая генерация ===
if (-not (Test-Path $PassFile)) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $passphrase = [Convert]::ToBase64String($bytes)
    Set-Content -Path $PassFile -Value $passphrase -Encoding ascii -NoNewline
    icacls $PassFile /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null
    Write-Host "=== ПЕРВЫЙ ЗАПУСК: создан passphrase ===" -ForegroundColor Yellow
    Write-Host "  Файл: $PassFile"
    Write-Host "  Скопируй содержимое в KeePass / на бумагу — без него восстановления НЕТ."
}

# === Зависимости ===
# ВАЖНО: используем именно Windows-native tar.exe из System32.
# git-bash / cygwin tar интерпретирует "C:\..." как remote host (host:path).
$tarPath = Join-Path $env:windir 'System32\tar.exe'
if (-not (Test-Path -LiteralPath $tarPath)) {
    # Fallback на PATH (Windows 10+ должен иметь tar в System32)
    if (Get-Command tar -ErrorAction SilentlyContinue) {
        $tarPath = (Get-Command tar).Source
    } else {
        Write-Error "tar.exe не найден ни в $env:windir\System32\, ни в PATH."
        exit 1
    }
}
$opensslPath = $null
if (Get-Command openssl -ErrorAction SilentlyContinue) {
    $opensslPath = (Get-Command openssl).Source
} else {
    $candidates = @()
    $candidates += 'C:\Program Files\Git\mingw64\bin\openssl.exe'
    $candidates += 'C:\Program Files\Git\usr\bin\openssl.exe'
    $candidates += ('C:\Program Files ' + '(x86)\Git\mingw64\bin\openssl.exe')
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $opensslPath = $candidate; break }
    }
    if (-not $opensslPath) {
        Write-Error "openssl не найден. Установи Git for Windows."
        exit 1
    }
}

# === Архив ===
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archiveName = "jarvis-private-$timestamp.tar.gz.enc"
$archivePath = Join-Path $BackupRoot $archiveName
$tempTar = Join-Path $env:TEMP "jarvis-private-$timestamp.tar.gz"

try {
    Push-Location $RepoRoot
    Write-Host "Архивирую workspace/ + .secrets/ + .env..."
    # ВАЖНО: .env содержит ВСЕ API ключи (ANTHROPIC, YANDEX, TELEGRAM и т.д.)
    # — обязательно в backup, иначе восстановление невозможно (lesson 22.05.26).
    # С 23.05.26 .env живёт в корне репо (раньше был в jarvis/.env). Проверяем оба.
    $envCandidates = @(".env", "jarvis/.env")
    $envFound = @()
    foreach ($p in $envCandidates) {
        if (Test-Path -LiteralPath (Join-Path $RepoRoot $p)) { $envFound += $p }
    }
    if ($envFound.Count -gt 0) {
        Write-Host "  .env: $($envFound -join ', ')"
        $tarArgs = @("-czf", $tempTar, "jarvis/workspace", "jarvis/.secrets") + $envFound
        & $tarPath @tarArgs 2>&1 | Out-Null
    } else {
        Write-Warning ".env не найден ни в корне, ни в jarvis/ — backup без ключей."
        & $tarPath -czf $tempTar "jarvis/workspace" "jarvis/.secrets" 2>&1 | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw "tar exit $LASTEXITCODE" }
    $tarSize = (Get-Item $tempTar).Length
    Write-Host "tar.gz: $([math]::Round($tarSize/1KB, 1)) KB"

    Write-Host "Шифрую AES-256-CBC..."
    & $opensslPath enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -in $tempTar -out $archivePath -pass "file:$PassFile"
    if ($LASTEXITCODE -ne 0) { throw "openssl exit $LASTEXITCODE" }
    $encSize = (Get-Item $archivePath).Length
    Write-Host "Готово: $archivePath ($([math]::Round($encSize/1KB, 1)) KB)" -ForegroundColor Green
} finally {
    if (Test-Path $tempTar) { Remove-Item $tempTar -Force }
    Pop-Location
}

# === Ротация ===
$old = Get-ChildItem $BackupRoot -Filter "jarvis-private-*.tar.gz.enc" |
    Sort-Object Name -Descending | Select-Object -Skip $KeepLast
foreach ($f in $old) {
    Remove-Item $f.FullName -Force
    Write-Host "Удалил старый: $($f.Name)"
}

Write-Host "=== BACKUP DONE ===" -ForegroundColor Green

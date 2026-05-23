# ============================================================
#  J.A.R.V.I.S. — Авто-установка Python 3.13 + подготовка к Windows MCP
#  Запуск: PowerShell ОТ ИМЕНИ АДМИНИСТРАТОРА:
#      powershell -ExecutionPolicy Bypass -File .\install_python_and_mcp.ps1
# ============================================================

$ErrorActionPreference = "Stop"

function Section($t) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

# -------- 0. Проверка winget --------
Section "0. ПРОВЕРКА WINGET"
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Host "[FAIL] winget не найден." -ForegroundColor Red
    Write-Host "Поставь его из Microsoft Store: ищи 'App Installer'" -ForegroundColor Yellow
    Write-Host "Или скачай: https://github.com/microsoft/winget-cli/releases" -ForegroundColor Yellow
    exit 1
}
$wingetVer = (& winget --version) 2>&1
Write-Host "[ OK ] winget $wingetVer"

# -------- 1. Python 3.13 --------
Section "1. УСТАНОВКА PYTHON 3.13"
Write-Host "Ставлю Python 3.13 (тихо, с добавлением в PATH)..." -ForegroundColor Yellow
& winget install -e --id Python.Python.3.13 `
    --source winget `
    --accept-source-agreements `
    --accept-package-agreements `
    --silent `
    --override "/quiet PrependPath=1 Include_pip=1 Include_launcher=1"

if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
    # -1978335189 = уже установлен (это норма)
    Write-Host "[WARN] winget вернул код $LASTEXITCODE — проверь вывод выше" -ForegroundColor Yellow
}

# -------- 2. Перечитать PATH --------
Section "2. ОБНОВЛЕНИЕ PATH В ТЕКУЩЕЙ СЕССИИ"
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
Write-Host "[ OK ] PATH обновлён"

# -------- 3. Проверка --------
Section "3. ПРОВЕРКА УСТАНОВКИ"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[FAIL] python не появился в PATH. Перезапусти PowerShell и проверь: python --version" -ForegroundColor Red
    exit 1
}
$pyVer = (& python --version) 2>&1
Write-Host "[ OK ] $pyVer ($($py.Source))"

$pipVer = (& python -m pip --version) 2>&1
Write-Host "[ OK ] $pipVer"

# -------- 4. Обновить pip и базу --------
Section "4. ОБНОВЛЕНИЕ PIP И БАЗОВЫХ ПАКЕТОВ"
& python -m pip install --upgrade pip setuptools wheel --quiet
Write-Host "[ OK ] pip / setuptools / wheel обновлены"

# -------- 5. Попытка поставить uv (быстрый менеджер пакетов) --------
Section "5. УСТАНОВКА UV (быстрый менеджер пакетов от Astral)"
Write-Host "Ставлю uv — он понадобится для Windows MCP и других MCP-серверов..." -ForegroundColor Yellow
& python -m pip install --upgrade uv --quiet
$uvOk = $?
if ($uvOk) {
    Write-Host "[ OK ] uv установлен: $(uv --version 2>&1)"
} else {
    Write-Host "[WARN] uv не поставился, не критично" -ForegroundColor Yellow
}

# -------- ИТОГ --------
Section "ГОТОВО"
Write-Host ""
Write-Host "Python 3.13 установлен и работает." -ForegroundColor Green
Write-Host ""
Write-Host "ТЕПЕРЬ СДЕЛАЙ:" -ForegroundColor Cyan
Write-Host "  1. Открой Claude Desktop" -ForegroundColor White
Write-Host "  2. Перейди в Directory -> Connectors -> 'Windows' (от CursorTouch)" -ForegroundColor White
Write-Host "  3. Нажми кнопку Install" -ForegroundColor White
Write-Host "  4. ПОЛНОСТЬЮ закрой Claude через трей (правый клик -> Quit)" -ForegroundColor White
Write-Host "  5. Запусти Claude снова, открой чат с Джарвисом" -ForegroundColor White
Write-Host "  6. Скажи 'Поехали'" -ForegroundColor White
Write-Host ""

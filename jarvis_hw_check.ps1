# ============================================================
#  J.A.R.V.I.S. — Диагностика железа (Фаза 0 → Фаза 1)
#  Запуск: открой PowerShell ОТ ИМЕНИ АДМИНИСТРАТОРА,
#          перейди в папку с этим файлом и выполни:
#              powershell -ExecutionPolicy Bypass -File .\jarvis_hw_check.ps1
#  Затем скопируй ВЕСЬ вывод и скинь в чат Боссу-Джарвису.
# ============================================================

$ErrorActionPreference = "SilentlyContinue"
$report = New-Object System.Collections.ArrayList

function Write-Section($title) {
    $line = "=" * 60
    Write-Host ""
    Write-Host $line -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host $line -ForegroundColor Cyan
    [void]$report.Add("")
    [void]$report.Add($line)
    [void]$report.Add("  $title")
    [void]$report.Add($line)
}

function Write-Item($label, $value, $ok) {
    $mark = if ($ok -eq $true) { "[ OK ]" } elseif ($ok -eq $false) { "[FAIL]" } else { "[INFO]" }
    $color = if ($ok -eq $true) { "Green" } elseif ($ok -eq $false) { "Red" } else { "Yellow" }
    $line = "{0,-7} {1,-28} {2}" -f $mark, $label, $value
    Write-Host $line -ForegroundColor $color
    [void]$report.Add($line)
}

# -------------------- 1. ОС и железо --------------------
Write-Section "1. ОС И БАЗОВОЕ ЖЕЛЕЗО"

$os = Get-CimInstance Win32_OperatingSystem
Write-Item "OS" "$($os.Caption) build $($os.BuildNumber)" $null
Write-Item "Архитектура" $os.OSArchitecture $null

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Item "CPU" "$($cpu.Name)" $null
Write-Item "Ядер / потоков" "$($cpu.NumberOfCores) / $($cpu.NumberOfLogicalProcessors)" $null

$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$ramOK = $ramGB -ge 16
Write-Item "RAM" "$ramGB GB (нужно >=16, рекомендуется 32)" $ramOK

$disk = Get-PSDrive C
$freeGB = [math]::Round($disk.Free / 1GB, 1)
$diskOK = $freeGB -ge 50
Write-Item "Свободно на C:" "$freeGB GB (нужно >=50)" $diskOK

# -------------------- 2. GPU --------------------
Write-Section "2. GPU (NVIDIA)"

$gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
if ($gpu) {
    Write-Item "Видеокарта" $gpu.Name $true
    $vramGB = [math]::Round($gpu.AdapterRAM / 1GB, 1)
    Write-Item "VRAM (по WMI)" "$vramGB GB (WMI может врать для >4GB — смотри nvidia-smi)" $null
    Write-Item "Драйвер" $gpu.DriverVersion $null
} else {
    Write-Item "Видеокарта NVIDIA" "не обнаружена!" $false
}

$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    Write-Host ""
    Write-Host "--- nvidia-smi ---" -ForegroundColor Gray
    [void]$report.Add("--- nvidia-smi ---")
    $smiOut = & nvidia-smi 2>&1 | Out-String
    Write-Host $smiOut
    [void]$report.Add($smiOut)
} else {
    Write-Item "nvidia-smi" "не найден в PATH" $false
}

# -------------------- 3. Docker + WSL --------------------
Write-Section "3. DOCKER DESKTOP + WSL2"

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    $dockerVer = (& docker --version) 2>&1
    Write-Item "Docker CLI" $dockerVer $true
    $dockerInfo = (& docker info --format "{{.ServerVersion}} | {{.OSType}}") 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Item "Docker daemon" $dockerInfo $true
    } else {
        Write-Item "Docker daemon" "НЕ ЗАПУЩЕН (открой Docker Desktop)" $false
    }
} else {
    Write-Item "Docker CLI" "не установлен" $false
}

$wsl = Get-Command wsl -ErrorAction SilentlyContinue
if ($wsl) {
    $wslOut = (& wsl --status) 2>&1 | Out-String
    Write-Item "WSL" "установлен" $true
    [void]$report.Add($wslOut.Trim())
    Write-Host $wslOut -ForegroundColor Gray
    $distros = (& wsl --list --verbose) 2>&1 | Out-String
    [void]$report.Add("--- WSL distros ---")
    [void]$report.Add($distros.Trim())
    Write-Host "--- WSL distros ---" -ForegroundColor Gray
    Write-Host $distros -ForegroundColor Gray
} else {
    Write-Item "WSL" "не установлен" $false
}

# Проверим GPU passthrough в Docker
if ($docker) {
    Write-Host ""
    Write-Host "Тестирую GPU passthrough в Docker (это может занять минуту)..." -ForegroundColor Yellow
    $gpuTest = (& docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi 2>&1) | Out-String
    if ($LASTEXITCODE -eq 0 -and $gpuTest -match "NVIDIA-SMI") {
        Write-Item "Docker --gpus all" "РАБОТАЕТ" $true
    } else {
        Write-Item "Docker --gpus all" "не работает (нужен NVIDIA Container Toolkit)" $false
        [void]$report.Add("--- docker gpu test output ---")
        [void]$report.Add($gpuTest.Trim())
    }
}

# -------------------- 4. Dev-стек --------------------
Write-Section "4. DEV-СТЕК"

function Check-Tool($name, $cmd, $argList, $minVer) {
    $bin = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $bin) {
        Write-Item $name "не установлен" $false
        return
    }
    $ver = (& $cmd $argList) 2>&1 | Out-String
    Write-Item $name $ver.Trim() $true
}

Check-Tool "Git"            "git"     @("--version")    $null
Check-Tool "Python"         "python"  @("--version")    "3.10"
Check-Tool "pip"            "pip"     @("--version")    $null
Check-Tool "Node.js"        "node"    @("--version")    "18"
Check-Tool "npm"            "npm"     @("--version")    $null
Check-Tool "FFmpeg"         "ffmpeg"  @("-version")     $null

# -------------------- 5. Папки и сеть --------------------
Write-Section "5. ПАПКИ ПРОЕКТА И СЕТЬ"

$jarvisDir = "C:\jarvis"
if (Test-Path $jarvisDir) {
    Write-Item "C:\jarvis" "существует" $true
    $contents = Get-ChildItem $jarvisDir -ErrorAction SilentlyContinue | Select-Object -First 10
    if ($contents) {
        [void]$report.Add("Содержимое C:\jarvis:")
        $contents | ForEach-Object { [void]$report.Add("  " + $_.Name) }
    }
} else {
    Write-Item "C:\jarvis" "нет — создадим" $null
}

# Локальный IP
$ips = Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp,Manual |
        Where-Object { $_.IPAddress -notmatch "^127\." -and $_.IPAddress -notmatch "^169\." } |
        Select-Object -ExpandProperty IPAddress
Write-Item "Локальные IP" ($ips -join ", ") $null

# Порты, которые понадобятся Джарвису
$ports = @(8123, 8080, 5000, 5005, 8888, 11434, 8000)
$portsBusy = @()
foreach ($p in $ports) {
    $busy = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue
    if ($busy) { $portsBusy += $p }
}
if ($portsBusy.Count -gt 0) {
    Write-Item "Занятые порты" ($portsBusy -join ", ") $false
} else {
    Write-Item "Порты Джарвиса" "8123/8080/5000/5005/8888/11434/8000 свободны" $true
}

# -------------------- 6. Интернет --------------------
Write-Section "6. ИНТЕРНЕТ И ВНЕШНИЕ СЕРВИСЫ"

function Test-Url($name, $url) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 5 -UseBasicParsing
        Write-Item $name "доступен ($($r.StatusCode))" $true
    } catch {
        Write-Item $name "недоступен — $($_.Exception.Message.Split([Environment]::NewLine)[0])" $false
    }
}

Test-Url "api.anthropic.com"  "https://api.anthropic.com"
Test-Url "github.com"         "https://github.com"
Test-Url "huggingface.co"     "https://huggingface.co"
Test-Url "ollama.ai"          "https://ollama.ai"
Test-Url "openweathermap.org" "https://api.openweathermap.org"

# -------------------- ИТОГ --------------------
Write-Section "ОТЧЁТ ГОТОВ"
Write-Host ""
Write-Host "Сохраняю полный отчёт в jarvis_hw_report.txt — рядом с этим скриптом." -ForegroundColor Yellow
$reportPath = Join-Path $PSScriptRoot "jarvis_hw_report.txt"
$report -join [Environment]::NewLine | Out-File -FilePath $reportPath -Encoding UTF8
Write-Host "Файл: $reportPath" -ForegroundColor Green
Write-Host ""
Write-Host "Теперь сделай одно из двух:" -ForegroundColor Cyan
Write-Host "  1) Скопируй ВЕСЬ вывод выше и вставь в чат Джарвису" -ForegroundColor White
Write-Host "  2) Или открой jarvis_hw_report.txt и пришли его содержимое" -ForegroundColor White
Write-Host ""

# ============================================================
# backup_full.ps1 — двойной бэкап (Public GitHub + encrypted local)
# ============================================================
# 1) Public GitHub: создаём orphan-snapshot через git worktree,
#    коммитим, force-push на main, удаляем worktree.
#    Основной рабочий tree НЕ трогается.
# 2) Local encrypted: AES-256 архив workspace/ + .secrets/.
#
# Параметры:
#   -SkipPublic — пропустить GitHub push
#   -SkipPrivate — пропустить encrypted local
# ============================================================

param(
    [switch]$SkipPublic,
    [switch]$SkipPrivate
)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$LogFile = Join-Path $RepoRoot 'jarvis\workspace\backup.log'

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 -ErrorAction SilentlyContinue } catch {}
}

Write-Log "=== BACKUP START ==="

# ============================================================
# 1. Public GitHub через git worktree
# ============================================================
if (-not $SkipPublic) {
    $patFile = Join-Path $RepoRoot 'jarvis\.secrets\github_pat'
    if (-not (Test-Path $patFile)) {
        Write-Log "PAT не найден ($patFile) — Public push пропущен" "WARN"
    } else {
        # Подход без worktree: git archive HEAD -> распаковываем в чистую temp dir,
        # делаем там git init + commit + push. Полностью изолировано от основного репо,
        # не зависит от unicode/parens в пути основного репо.
        $snapshotDir = "C:\jarvis-bak\snapshot"
        $tarPath = Join-Path $env:windir 'System32\tar.exe'
        try {
            Push-Location $RepoRoot

            # Полная очистка staging директории
            if (Test-Path "C:\jarvis-bak") {
                Remove-Item "C:\jarvis-bak" -Recurse -Force -ErrorAction SilentlyContinue
            }
            New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null

            # 1. git archive HEAD: tar со снимком текущего HEAD (учитывает .gitignore)
            Write-Log "git archive HEAD -> $snapshotDir..."
            $archiveTar = "C:\jarvis-bak\head.tar"
            $archiveOut = git archive --format=tar -o $archiveTar HEAD 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "git archive failed: " + (($archiveOut | ForEach-Object { $_.ToString() }) -join ' | ')
            }

            # 2. Распаковываем в snapshotDir (ASCII путь — никаких unicode проблем)
            & $tarPath -xf $archiveTar -C $snapshotDir 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "tar extract failed ($LASTEXITCODE)" }
            Remove-Item $archiveTar -Force -ErrorAction SilentlyContinue

            # 3. В snapshotDir — отдельный fresh git repo
            Push-Location $snapshotDir
            try {
                git init -b main --quiet 2>&1 | Out-Null
                git config user.email "jarvis-backup@localhost" 2>&1 | Out-Null
                git config user.name "JARVIS Backup" 2>&1 | Out-Null
                git add -A 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "git add failed ($LASTEXITCODE)" }

                $msg = "chore(public-snapshot): auto-backup at $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
                $tmpMsg = "C:\jarvis-bak\msg.txt"
                $msg | Out-File -FilePath $tmpMsg -Encoding utf8 -NoNewline
                git commit -F $tmpMsg --quiet 2>&1 | Out-Null
                Remove-Item $tmpMsg -Force -ErrorAction SilentlyContinue
                if ($LASTEXITCODE -ne 0) { throw "git commit failed ($LASTEXITCODE)" }

                # 4. Force-push на main (orphan effect — никакая история не уезжает)
                $pat = (Get-Content $patFile -Raw).Trim()
                $tempUrl = "https://x-access-token:${pat}@github.com/dzigurdarahmet-byte/J.A.R.V.I.S.git"
                Write-Log "Force-push main..."
                $pushOut = git push $tempUrl 'main:main' --force 2>&1
                $pushExit = $LASTEXITCODE
                $pushSafe = ($pushOut | ForEach-Object { $_.ToString() }) -replace [regex]::Escape($pat), '***PAT***'
                Write-Log ("git push (exit $pushExit): " + ($pushSafe -join ' | '))
                if ($pushExit -ne 0) { throw "git push failed ($pushExit)" }

                Write-Log "Public push DONE" "OK"
            } finally {
                Pop-Location
            }
        } catch {
            Write-Log "Public push FAILED: $($_.Exception.Message)" "ERROR"
        } finally {
            # Cleanup
            try {
                if (Test-Path "C:\jarvis-bak") {
                    Remove-Item "C:\jarvis-bak" -Recurse -Force -ErrorAction SilentlyContinue
                }
            } catch {}
            Pop-Location
        }
    }
} else {
    Write-Log "Public push SKIPPED (-SkipPublic)"
}

# ============================================================
# 2. Local encrypted backup
# ============================================================
if (-not $SkipPrivate) {
    try {
        $privateScript = Join-Path $PSScriptRoot 'backup_private.ps1'
        Write-Log "Запускаю backup_private.ps1..."
        # Скобки и кириллица в пути ломают прямой '& $var'. Через scriptblock работает.
        $sb = [scriptblock]::Create(". '$privateScript'")
        & $sb 2>&1 | ForEach-Object { Write-Log ("private: " + $_) }
        Write-Log "Private encrypted backup DONE" "OK"
    } catch {
        Write-Log "Private backup FAILED: $($_.Exception.Message)" "ERROR"
    }
} else {
    Write-Log "Private backup SKIPPED (-SkipPrivate)"
}

Write-Log "=== BACKUP END ==="

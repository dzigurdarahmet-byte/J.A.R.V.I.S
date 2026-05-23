@echo off
REM Переключаем cmd codepage на UTF-8, иначе кириллица из PowerShell -> иероглифы
chcp 65001 >nul 2>&1
REM ============================================================
REM backup_now.bat — ручной запуск двойного backup'а
REM ============================================================
REM Двойной клик в проводнике ИЛИ из любой консоли:
REM     "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\scripts\backup_now.bat"
REM
REM Делает то же что post-commit hook:
REM   1. Public push на github.com/dzigurdarahmet-byte/J.A.R.V.I.S
REM   2. Encrypted local backup в C:\Backups\jarvis\
REM
REM ExecutionPolicy Bypass для этого процесса — не трогает системные настройки.
REM ============================================================

setlocal
cd /d "%~dp0..\..\"
echo === JARVIS Manual Backup ===
echo Repo: %CD%
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0backup_full.ps1" %*
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo === DONE (exit 0) ===
) else (
    echo === FAILED (exit %EXITCODE%) ===
)
echo Logs: jarvis\workspace\backup.log
echo Archives: C:\Backups\jarvis\
echo.
pause
exit /b %EXITCODE%

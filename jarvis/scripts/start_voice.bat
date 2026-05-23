@echo off
REM ============================================================
REM start_voice.bat — запуск голосового канала с UTF-8 логами
REM ============================================================
REM Решает проблему ромбиков в HUD Logs:
REM   - PYTHONIOENCODING=utf-8 заставляет Python писать stdout в UTF-8
REM   - chcp 65001 ставит cmd codepage в UTF-8
REM   - Redirect через CMD внутри (а не PowerShell) сохраняет UTF-8 байты
REM
REM Двойной клик ИЛИ:
REM   "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\scripts\start_voice.bat"
REM
REM Логи: jarvis\workspace\voice_run.log + voice_run.err (live в HUD Logs tab)
REM Остановка: Ctrl+C в этом окне или закрыть окно
REM ============================================================

chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d "%~dp0..\..\"
echo === JARVIS Voice Loop ===
echo Repo: %CD%
echo Logs: jarvis\workspace\voice_run.log + .err
echo.

REM Перенаправление stdout/stderr в файлы — оба в UTF-8 благодаря PYTHONIOENCODING
"%CD%\jarvis\.venv\Scripts\python.exe" "%CD%\jarvis\run_voice.py" vad > "%CD%\jarvis\workspace\voice_run.log" 2> "%CD%\jarvis\workspace\voice_run.err"

set EXITCODE=%ERRORLEVEL%
echo.
echo === Voice loop exited (code %EXITCODE%) ===
pause
exit /b %EXITCODE%

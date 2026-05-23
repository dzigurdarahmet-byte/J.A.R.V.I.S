@echo off
REM start_all.bat — autostart for J.A.R.V.I.S. (HUD + Telegram + Voice)
REM Put a shortcut to this file in shell:startup to launch on Windows login.

set JARVIS_DIR=C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis
set PY=%JARVIS_DIR%\.venv\Scripts\python.exe

REM Hidden background launch for each subsystem.
start "" /B /D "%JARVIS_DIR%" "%PY%" run_web_hud.py  >> "%JARVIS_DIR%\workspace\autostart_hud.log"      2>&1
start "" /B /D "%JARVIS_DIR%" "%PY%" run_telegram.py >> "%JARVIS_DIR%\workspace\autostart_telegram.log" 2>&1
REM Voice channel: pulls microphone, needs proper audio device. Uncomment when ready.
REM start "" /B /D "%JARVIS_DIR%" "%PY%" run_voice.py vad >> "%JARVIS_DIR%\workspace\autostart_voice.log" 2>&1

exit
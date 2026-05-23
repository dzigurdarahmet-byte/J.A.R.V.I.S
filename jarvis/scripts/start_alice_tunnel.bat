@echo off
REM start_alice_tunnel.bat -- start localtunnel for Alice webhook
REM Public URL: https://jarvis-boss.loca.lt/api/alice/webhook
REM Tunnels localhost:8000 to public URL via localtunnel (works in RU).
REM
REM Requirements (one-time):
REM   npm install -g localtunnel

echo === JARVIS Alice tunnel (localtunnel) ===
echo Tunneling localhost:8000 -^> https://jarvis-boss.loca.lt
echo.
echo Webhook URL: https://jarvis-boss.loca.lt/api/alice/webhook
echo.
echo Ctrl+C to stop.
echo.

lt --port 8000 --subdomain jarvis-boss --print-requests
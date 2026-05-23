"""Web HUD — третий канал общения через браузер.

http://localhost:8000 — открыть в Edge/Chrome.
- WebSocket стрим live-событий из bus
- Поле ввода → текстовое сообщение → ROUTED → Claude → ответ
- Arc Reactor SVG, статус, лента диалога
"""

from .server import build_app, run_web_hud

__all__ = ["build_app", "run_web_hud"]

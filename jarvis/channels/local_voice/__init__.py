"""Local voice channel — hands-free через микрофон ПК и динамики/наушники.

VAD-mode (default): слушает постоянно, ловит начало/конец речи через silero-vad.
PTT-mode: захват по hotkey Ctrl+Space.
Toggle: F12 пауза/возобновление, F11 переключение режима.
"""

from .loop import run_local_voice

__all__ = ["run_local_voice"]

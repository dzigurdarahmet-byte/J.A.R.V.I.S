"""Глобальные горячие клавиши через pynput.

Работают даже когда фокус не на нашем приложении (Boss в браузере / IDE).

Hotkeys:
    F12         — toggle pause/resume listening
    F11         — switch VAD-mode ↔ PTT-mode
    Ctrl+Space  — push-to-talk: зажал → говоришь → отпустил → процессинг

pynput Listener работает в отдельном thread'е. Прокидываем события в asyncio
через call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Final

from pynput import keyboard

from core.logging import get_logger

logger = get_logger(__name__)


class HotkeyEvent(Enum):
    TOGGLE_PAUSE = "toggle_pause"
    SWITCH_MODE = "switch_mode"
    PTT_DOWN = "ptt_down"
    PTT_UP = "ptt_up"


@dataclass(frozen=True)
class HotkeyConfig:
    pause_key: keyboard.Key = keyboard.Key.f12
    mode_key: keyboard.Key = keyboard.Key.f11
    ptt_combo: tuple[keyboard.Key, ...] = (keyboard.Key.ctrl, keyboard.Key.space)


class HotkeyListener:
    """Глобальный keyboard listener → asyncio.Queue[HotkeyEvent]."""

    def __init__(self, config: HotkeyConfig | None = None) -> None:
        self._cfg = config or HotkeyConfig()
        self.queue: asyncio.Queue[HotkeyEvent] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listener: keyboard.Listener | None = None
        self._pressed_keys: set[object] = set()
        self._ptt_active = False

    def _on_press(self, key: object) -> None:
        self._pressed_keys.add(key)

        # F12 — toggle pause
        if key == self._cfg.pause_key:
            self._emit(HotkeyEvent.TOGGLE_PAUSE)
            return

        # F11 — switch mode
        if key == self._cfg.mode_key:
            self._emit(HotkeyEvent.SWITCH_MODE)
            return

        # Ctrl+Space — PTT down
        if all(k in self._pressed_keys for k in self._cfg.ptt_combo) and not self._ptt_active:
            self._ptt_active = True
            self._emit(HotkeyEvent.PTT_DOWN)

    def _on_release(self, key: object) -> None:
        self._pressed_keys.discard(key)

        # Любая из клавиш PTT-комбо отпущена → PTT_UP
        if self._ptt_active:
            ptt_keys = set(self._cfg.ptt_combo)
            if key in ptt_keys:
                self._ptt_active = False
                self._emit(HotkeyEvent.PTT_UP)

    def _emit(self, event: HotkeyEvent) -> None:
        if self._loop is None:
            return
        logger.info("hotkey", event=event.value)
        try:
            self._loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except RuntimeError:
            pass

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        logger.info("hotkey_listener_started",
                    pause="F12", mode="F11", ptt="Ctrl+Space")

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("hotkey_listener_stopped")

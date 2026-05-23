"""TTSWithFallback — Yandex TTS (primary) → Silero (offline fallback).

Если Yandex TTS падает на network/timeout — переключаемся на локальную
Silero TTS прозрачно. Sample rate берётся из primary, поэтому при переходе
на Silero первый ответ может прийти на другом SR (звучать будет нормально,
play_audio принимает SR из параметра).

Совместимо с интерфейсом YandexSpeechKitTTS:
    .preload() / .synthesize(text) -> np.ndarray / .sample_rate
"""

from __future__ import annotations

import asyncio

import httpx
import numpy as np

from core.logging import get_logger
from core.metrics import metrics

logger = get_logger(__name__)


class TTSWithFallback:
    """Primary Yandex TTS, fallback Silero при сетевой ошибке."""

    name = "tts-yandex+silero-fallback"

    def __init__(self, primary, fallback) -> None:
        self._primary = primary
        self._fallback = fallback
        # SR берём от primary (Yandex отдаёт 48k, Silero — 48k тоже обычно)
        self._fell_back_once = False

    @property
    def sample_rate(self) -> int:
        if self._fell_back_once:
            return self._fallback.sample_rate
        return self._primary.sample_rate

    async def preload(self) -> None:
        await asyncio.gather(
            self._primary.preload(),
            self._fallback.preload(),
            return_exceptions=True,
        )

    async def synthesize(self, text: str, **kwargs) -> np.ndarray:
        try:
            return await self._primary.synthesize(text, **kwargs)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
            logger.warning(
                "tts_falling_back_to_silero",
                primary=getattr(self._primary, "name", "yandex"),
                error=str(e)[:200],
            )
            metrics.record(
                "fallback",
                provider="silero",
                meta={"from": "yandex", "stage": "tts", "reason": "network"},
            )
            self._fell_back_once = True
            with metrics.timed("tts", provider="silero"):
                # Silero не принимает kwargs Yandex'а — фильтруем
                return await self._fallback.synthesize(text)
        except Exception as e:
            logger.warning(
                "tts_unexpected_primary_error_fallback",
                primary=getattr(self._primary, "name", "yandex"),
                error=str(e)[:200],
            )
            metrics.record(
                "fallback",
                provider="silero",
                meta={"from": "yandex", "stage": "tts", "reason": "unknown"},
            )
            self._fell_back_once = True
            with metrics.timed("tts", provider="silero"):
                return await self._fallback.synthesize(text)

    async def close(self) -> None:
        close = getattr(self._primary, "close", None)
        if close:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass

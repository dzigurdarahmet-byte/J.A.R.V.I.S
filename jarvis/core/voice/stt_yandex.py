"""Yandex SpeechKit STT — primary распознаватель для local_voice.

REST v1 синхронный endpoint: до 30 сек, до 1 МБ.
Для наших VAD-сегментов (3-10 сек) идеально.

API: https://yandex.cloud/ru/docs/speechkit/stt/api/request-api
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
import numpy as np

from core.logging import get_logger
from core.metrics import metrics

logger = get_logger(__name__)

API_URL: Final = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
REQUEST_TIMEOUT_SEC: Final = 15.0
DEFAULT_LANG: Final = "ru-RU"
DEFAULT_TOPIC: Final = "general:rc"  # последняя generic-модель


class YandexSpeechKitSTT:
    """Распознавание речи через Yandex SpeechKit REST v1."""

    name = "yandex-speechkit-stt"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        language: str = DEFAULT_LANG,
        topic: str = DEFAULT_TOPIC,
    ) -> None:
        if not api_key:
            raise ValueError("YandexSpeechKitSTT requires non-empty api_key")
        if not folder_id:
            raise ValueError("YandexSpeechKitSTT requires non-empty folder_id")
        self._api_key = api_key
        self._folder_id = folder_id
        self._language = language
        self._topic = topic
        # Keep-alive HTTP-клиент (экономит TLS handshake на каждом запросе).
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SEC,
            headers={"Authorization": f"Api-Key {api_key}"},
        )

    async def preload(self) -> None:
        """Совместимость с интерфейсом WhisperSTT (модель не нужна)."""
        return None

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> str:
        text, _ = await self.transcribe_with_meta(audio, sample_rate, language)
        return text

    async def transcribe_with_meta(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> tuple[str, float]:
        """Вернуть (text, no_speech_prob). У Yandex нет no_speech_prob,
        ставим 0.0 (=уверены что речь), либо 0.9 при пустом результате."""
        if not isinstance(audio, np.ndarray):
            raise TypeError("YandexSpeechKitSTT.transcribe принимает только np.ndarray")
        # Конвертим float32 → int16 PCM
        if audio.dtype == np.float32:
            pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        elif audio.dtype == np.int16:
            pcm = audio
        else:
            pcm = audio.astype(np.int16)
        if pcm.ndim > 1:
            pcm = pcm[:, 0]
        pcm_bytes = pcm.tobytes()

        params = {
            "folderId": self._folder_id,
            "lang": language or self._language,
            "topic": self._topic,
            "format": "lpcm",
            "sampleRateHertz": str(sample_rate),
        }
        headers = {"Content-Type": f"audio/x-pcm;bit=16;rate={sample_rate}"}

        try:
            with metrics.timed("stt", provider="yandex"):
                r = await self._client.post(
                    API_URL, params=params, content=pcm_bytes, headers=headers,
                )
                r.raise_for_status()
                data = r.json()
            text = (data.get("result") or "").strip()
            no_speech_prob = 0.0 if text else 0.9
            logger.info(
                "yandex_stt_ok",
                chars=len(text),
                duration_sec=round(audio.shape[0] / sample_rate, 2),
            )
            return text, no_speech_prob
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            logger.warning("yandex_stt_network_error", error=str(e)[:200])
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "yandex_stt_http_error",
                status=e.response.status_code,
                body=e.response.text[:200],
            )
            raise

    async def close(self) -> None:
        await self._client.aclose()


class STTWithFallback:
    """Primary Yandex STT, fallback Whisper при сетевой ошибке.

    Сохраняет интерфейс WhisperSTT (transcribe / transcribe_with_meta / preload).
    """

    name = "stt-yandex+whisper-fallback"

    def __init__(self, primary, fallback) -> None:
        self._primary = primary
        self._fallback = fallback

    async def preload(self) -> None:
        await asyncio.gather(
            self._primary.preload(),
            self._fallback.preload(),
            return_exceptions=True,
        )

    async def transcribe(self, audio, sample_rate=16000, language=None):
        text, _ = await self.transcribe_with_meta(audio, sample_rate, language)
        return text

    async def transcribe_with_meta(self, audio, sample_rate=16000, language=None):
        try:
            return await self._primary.transcribe_with_meta(audio, sample_rate, language)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
            logger.warning(
                "stt_falling_back_to_whisper",
                primary=self._primary.name,
                error=str(e)[:200],
            )
            metrics.record("fallback", provider="whisper", meta={"from": "yandex", "reason": "network"})
            with metrics.timed("stt", provider="whisper"):
                return await self._fallback.transcribe_with_meta(audio, sample_rate, language)
        except Exception as e:
            logger.warning(
                "stt_unexpected_primary_error_fallback",
                primary=self._primary.name,
                error=str(e)[:200],
            )
            metrics.record("fallback", provider="whisper", meta={"from": "yandex", "reason": "unknown"})
            with metrics.timed("stt", provider="whisper"):
                return await self._fallback.transcribe_with_meta(audio, sample_rate, language)

    async def close(self) -> None:
        close = getattr(self._primary, "close", None)
        if close:
            await close()

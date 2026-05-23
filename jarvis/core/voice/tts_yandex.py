"""Text-to-Speech через Yandex SpeechKit v3 (gRPC) — официальный SDK.

Премиум-голоса (alena/jane/omazh/zahar/ermil/filipp), эмоции
(neutral/good/evil/whisper), 48kHz mono PCM.

Тариф: ~400 ₽ за 1 млн символов (alena/jane premium).

Совместим с интерфейсом SileroTTS:
    tts = YandexSpeechKitTTS(api_key=..., folder_id=..., voice="alena")
    await tts.preload()
    audio = await tts.synthesize("Привет, Босс.")  # np.float32 mono
    tts.sample_rate  # 48000
"""

from __future__ import annotations

import asyncio
from typing import Final

import numpy as np

from core.logging import get_logger
from core.metrics import metrics

logger = get_logger(__name__)

DEFAULT_VOICE: Final = "alena"
DEFAULT_EMOTION: Final = "neutral"  # neutral / good / evil / whisper
DEFAULT_SAMPLE_RATE: Final = 48000
DEFAULT_SPEED: Final = 1.0


class YandexSpeechKitTTS:
    """Async wrapper над SDK `yandex-speechkit`. Совместим с SileroTTS API."""

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        voice: str = DEFAULT_VOICE,
        emotion: str = DEFAULT_EMOTION,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        speed: float = DEFAULT_SPEED,
    ) -> None:
        if not api_key or not folder_id:
            raise ValueError("YandexSpeechKitTTS: нужны api_key + folder_id")
        self._api_key = api_key
        self._folder_id = folder_id
        self._voice = voice
        self._emotion = emotion
        self._sample_rate = sample_rate
        self._speed = speed
        self._synthesizer = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _ensure_synth(self):
        if self._synthesizer is not None:
            return self._synthesizer
        from speechkit import configure_credentials, creds, model_repository

        configure_credentials(yandex_credentials=creds.YandexCredentials(api_key=self._api_key))
        synth = model_repository.synthesis_model()
        synth.voice = self._voice
        synth.role = self._emotion
        synth.speed = self._speed
        # SDK сам выставит правильный sample rate (по умолчанию 22050 в SDK,
        # но AudioEncoding.LINEAR16_PCM поддерживает любой; используем default).
        self._synthesizer = synth
        logger.info(
            "yandex_tts_ready",
            voice=self._voice,
            emotion=self._emotion,
        )
        return synth

    async def preload(self) -> None:
        await asyncio.to_thread(self._ensure_synth)

    async def synthesize(
        self,
        text: str,
        speaker: str | None = None,
        sample_rate: int | None = None,
        role: str | None = None,
    ) -> np.ndarray:
        """Текст → PCM float32 mono numpy. Длинные тексты автосплиттер SDK.

        Параметр `role` (если задан) подменяет эмоцию голоса на одну
        синтез-операцию: good / neutral / evil / whisper — по тому, что
        поддерживает выбранный голос. Если SDK не примет — деградируем
        обратно к init-значению.
        """

        def _do() -> np.ndarray:
            synth = self._ensure_synth()
            if speaker:
                synth.voice = speaker
            # Per-call role override (для аватара с эмоциями).
            prev_role = getattr(synth, "role", self._emotion)
            if role and role != prev_role:
                try:
                    synth.role = role
                except Exception:
                    pass  # SDK не дал — игнорируем, останется prev
            try:
                result = synth.synthesize(text, raw_format=False)
            finally:
                # Восстанавливаем init-роль чтобы не протекать в другие вызовы
                if role and role != prev_role:
                    try:
                        synth.role = prev_role
                    except Exception:
                        pass
            # SDK возвращает pydub.AudioSegment.
            try:
                from pydub import AudioSegment

                if isinstance(result, AudioSegment):
                    seg: AudioSegment = result.set_channels(1)
                    src_sr = seg.frame_rate
                    # Берём raw int16 → float32 [-1, 1]
                    pcm_i16 = np.frombuffer(seg.raw_data, dtype=np.int16)
                    pcm_f = pcm_i16.astype(np.float32) / 32768.0
                    target_sr = self._sample_rate
                    if src_sr == target_sr:
                        return pcm_f
                    # Качественный ресемплинг через scipy.signal.resample_poly
                    try:
                        import math
                        import scipy.signal as sps
                        g = math.gcd(src_sr, target_sr)
                        up = target_sr // g
                        down = src_sr // g
                        return sps.resample_poly(pcm_f, up, down).astype(np.float32)
                    except ImportError:
                        # без scipy: оставляем оригинальный sample_rate,
                        # пусть audio-module sounddevice сам решит
                        self._sample_rate = src_sr
                        return pcm_f
            except Exception as e:
                logger.warning("yandex_pydub_failed", error=str(e))
            # Fallback: assume bytes raw int16 @ self._sample_rate
            if isinstance(result, (bytes, bytearray)):
                pcm = np.frombuffer(result, dtype=np.int16).astype(np.float32) / 32768.0
                return pcm
            return np.zeros(0, dtype=np.float32)

        try:
            with metrics.timed("tts", provider="yandex", meta={"chars": len(text)}):
                return await asyncio.to_thread(_do)
        except Exception as e:
            logger.error("yandex_tts_error", error=str(e))
            raise

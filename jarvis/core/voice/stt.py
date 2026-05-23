"""Speech-to-Text через faster-whisper.

CPU mode (int8 quantization) — на i5-12400 модель small даёт ~0.3x realtime,
то есть 3-секундная реплика обрабатывается ~1 сек. Для MVP достаточно.

Lazy-load: модель грузится при первом transcribe(). Можно forсить через preload().
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

import numpy as np
from faster_whisper import WhisperModel

from core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL_SIZE: Final = "small"
DEFAULT_LANGUAGE: Final = "ru"
DEFAULT_BEAM_SIZE: Final = 1  # greedy на CPU быстрее в 3-5 раз


class WhisperSTT:
    """Async wrapper над faster-whisper. Singleton — модель загружается один раз."""

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model: WhisperModel | None = None

    def _load(self) -> WhisperModel:
        if self._model is None:
            logger.info(
                "whisper_loading",
                model=self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("whisper_loaded", model=self._model_size)
        return self._model

    async def preload(self) -> None:
        """Принудительная загрузка модели (в фоне) до первого transcribe."""
        await asyncio.to_thread(self._load)

    async def transcribe(
        self,
        audio: np.ndarray | str | Path,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> str:
        """Превратить аудио в текст. Возвращает только text для обратной
        совместимости. Для метрик no_speech_prob используй transcribe_with_meta."""
        result = await self.transcribe_with_meta(audio, sample_rate, language)
        return result[0]

    async def transcribe_with_meta(
        self,
        audio: np.ndarray | str | Path,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> tuple[str, float]:
        """Расширенная версия: вернуть (text, no_speech_prob).

        no_speech_prob — средняя вероятность что в сегментах НЕТ речи.
        Чем выше, тем менее достоверный транскрипт. Используется
        anti-hallucination фильтром.
        """
        model = self._load()
        lang = language or self._language

        # numpy int16 → float32 нормализация (faster-whisper требует float32 в [-1, 1])
        if isinstance(audio, np.ndarray):
            if audio.dtype == np.int16:
                audio = audio.astype(np.float32) / 32768.0
            elif audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            if audio.ndim > 1:
                audio = audio[:, 0]
        elif isinstance(audio, Path):
            audio = str(audio)

        def _do_transcribe() -> tuple[str, float]:
            assert self._model is not None
            segments, info = self._model.transcribe(
                audio,
                language=lang,
                beam_size=DEFAULT_BEAM_SIZE,
                vad_filter=False,
                without_timestamps=True,
            )
            text_parts: list[str] = []
            no_speech_probs: list[float] = []
            for seg in segments:
                text_parts.append(seg.text)
                # faster-whisper Segment.no_speech_prob — float [0, 1]
                if seg.no_speech_prob is not None:
                    no_speech_probs.append(float(seg.no_speech_prob))
            text = " ".join(part.strip() for part in text_parts).strip()
            avg_no_speech = (
                sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else 0.0
            )
            return text, avg_no_speech

        text, no_speech_prob = await asyncio.to_thread(_do_transcribe)
        logger.info(
            "whisper_transcribed",
            chars=len(text),
            lang=lang,
            no_speech_prob=round(no_speech_prob, 3),
        )
        return text, no_speech_prob

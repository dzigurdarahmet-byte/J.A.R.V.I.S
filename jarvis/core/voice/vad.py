"""Voice Activity Detection через silero-vad.

Используется в local_voice loop:
- Audio capture даёт чанки по 32ms (512 samples @ 16kHz)
- VAD проверяет каждый чанк (speech/silence)
- Когда речь началась — буферизуем до 1500ms тишины
- Возвращаем segment для отправки в Whisper
"""

from __future__ import annotations

import shutil
from collections import deque
from pathlib import Path
from typing import Final

import numpy as np
import torch
from silero_vad import VADIterator

from core.logging import get_logger

logger = get_logger(__name__)

VAD_SAMPLE_RATE: Final = 16000
VAD_CHUNK_SIZE: Final = 512  # silero-vad fixed размер для 16kHz
VAD_THRESHOLD: Final = 0.5
DEFAULT_MIN_SILENCE_MS: Final = 1500
SPEECH_PAD_MS: Final = 200  # сколько мс тишины оставляем по краям

# torch.jit.load на Windows не открывает файлы с не-ASCII символами в пути
# (наш venv в "C:\Users\Staho\...\ДЖАРВИС (2)\..."). Копируем .jit в ASCII-кэш.
ASCII_MODELS_DIR: Final = Path("C:/jarvis_data/models")


def _load_silero_vad_ascii_safe() -> torch.nn.Module:
    """Загрузка silero-vad модели через ASCII-путь.

    Стандартный load_silero_vad() ищет файл в site-packages внутри venv,
    который у нас лежит по пути с кириллицей и torch.jit.load на это ругается.
    """
    import silero_vad as _svad

    src = Path(_svad.__file__).parent / "data" / "silero_vad.jit"
    if not src.exists():
        # fallback на onnx если jit отсутствует в установке
        src = Path(_svad.__file__).parent / "data" / "silero_vad.onnx"
        if not src.exists():
            raise FileNotFoundError(f"silero-vad model not found near {_svad.__file__}")

    ASCII_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = ASCII_MODELS_DIR / src.name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        logger.info("copying_silero_vad", src=str(src), dst=str(dst))
        shutil.copy2(src, dst)

    return torch.jit.load(str(dst), map_location="cpu")


class VADStream:
    """Stateful streaming VAD.

    Использование:
        vad = VADStream()
        vad.start()
        for chunk in audio_stream:  # np.ndarray shape (512,) float32 16kHz
            segment = vad.feed(chunk)
            if segment is not None:
                # segment — np.ndarray весь буфер речи с padding'ом
                text = await whisper.transcribe(segment)
    """

    def __init__(
        self,
        min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
        threshold: float = VAD_THRESHOLD,
        sample_rate: int = VAD_SAMPLE_RATE,
    ) -> None:
        if sample_rate != VAD_SAMPLE_RATE:
            raise ValueError(f"silero-vad supports only {VAD_SAMPLE_RATE}Hz, got {sample_rate}")

        self._model = _load_silero_vad_ascii_safe()
        self._iter = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=sample_rate,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=SPEECH_PAD_MS,
        )
        self._sample_rate = sample_rate
        self._buffer: deque[np.ndarray] = deque()
        self._in_speech = False

    def start(self) -> None:
        """Сброс состояния."""
        self._iter.reset_states()
        self._buffer.clear()
        self._in_speech = False

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        """Подать аудиочанк (512 samples float32). Вернуть законченный сегмент или None.

        Логика:
        - VADIterator возвращает {'start': ...} когда речь начинается
        - Возвращает {'end': ...} когда после паузы решает что речь кончилась
        - Между этими событиями — копим chunks в буфер
        """
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)
        if chunk.shape[0] != VAD_CHUNK_SIZE:
            raise ValueError(
                f"VAD expects chunk of {VAD_CHUNK_SIZE} samples, got {chunk.shape[0]}"
            )

        tensor = torch.from_numpy(chunk)
        event = self._iter(tensor, return_seconds=False)

        if event is not None:
            if "start" in event:
                self._in_speech = True
                self._buffer.clear()
                self._buffer.append(chunk.copy())
                return None
            if "end" in event:
                # завершение речи — собираем accumulated buffer
                self._buffer.append(chunk.copy())
                segment = np.concatenate(list(self._buffer))
                self._buffer.clear()
                self._in_speech = False
                logger.info(
                    "vad_segment_ready",
                    samples=segment.shape[0],
                    duration_sec=round(segment.shape[0] / self._sample_rate, 2),
                )
                return segment
        elif self._in_speech:
            # внутри речи — продолжаем накапливать
            self._buffer.append(chunk.copy())

        return None

    def is_in_speech(self) -> bool:
        return self._in_speech

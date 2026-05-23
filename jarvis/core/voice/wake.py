"""Wake-word predictor через openWakeWord.

Поддерживает:
  - встроенные модели openWakeWord (`hey_jarvis`, `alexa`, `hey_mycroft`, ...)
  - кастомные `.onnx` модели (например, `models/wake/dzarvis.onnx` после
    тренировки через scripts/train_wake_dzarvis.ipynb).

Использование в loop:

    # Встроенная модель
    wd = WakeDetector("hey_jarvis")

    # Кастомная (путь к .onnx)
    wd = WakeDetector("models/wake/dzarvis.onnx")

    # Автодетект — если models/wake/dzarvis.onnx существует, использует её;
    # иначе fallback на hey_jarvis
    wd = WakeDetector.auto()

    wd.preload()  # один раз
    for chunk in audio:  # int16 mono 16kHz, любого размера
        if wd.feed(chunk):
            # wake-слово услышано
            ...
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from core.logging import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80ms @ 16kHz — стандартный шаг openWakeWord
DEFAULT_THRESHOLD = 0.5
COOLDOWN_SEC = 2.0  # после wake не реагируем N сек чтобы не зажить cycles

# Конвенция для кастомной модели — лежит в models/wake/ относительно репо.
_JARVIS_ROOT = Path(__file__).resolve().parents[2]  # jarvis/
DEFAULT_CUSTOM_MODEL_PATH = _JARVIS_ROOT / "models" / "wake" / "dzarvis.onnx"


class WakeDetector:
    """Накапливает int16 PCM и спрашивает openWakeWord по 1280 samples за шаг.

    Вызывающий код кормит произвольными чанками через feed(). Когда внутри
    накопилось ≥ 1280 sample'ов — запускается инференс. Возвращает True
    при срабатывании wake-слова с учётом cooldown.
    """

    def __init__(
        self,
        wakeword: str = "hey_jarvis",
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._wakeword = wakeword
        self._threshold = threshold
        # Если wakeword — путь к существующему .onnx файлу, считаем custom-моделью.
        # Иначе — имя встроенной модели openWakeWord.
        self._is_custom = wakeword.endswith(".onnx") and Path(wakeword).exists()
        self._custom_path = Path(wakeword).resolve() if self._is_custom else None
        # Имя по которому будем читать score из predict() result
        self._score_key = self._custom_path.stem if self._is_custom else wakeword
        self._model = None
        self._buf: list[np.ndarray] = []
        self._buf_total = 0
        self._last_trigger_ts = 0.0
        self._last_score = 0.0

    @classmethod
    def auto(cls, threshold: float = DEFAULT_THRESHOLD) -> "WakeDetector":
        """Автоматически выбирает custom dzarvis.onnx если есть, иначе hey_jarvis."""
        if DEFAULT_CUSTOM_MODEL_PATH.exists():
            logger.info("wake_auto_selected_custom", path=str(DEFAULT_CUSTOM_MODEL_PATH))
            return cls(str(DEFAULT_CUSTOM_MODEL_PATH), threshold=threshold)
        logger.info("wake_auto_selected_builtin", model="hey_jarvis",
                    hint="трен. кастомную через docs/B8_WAKE_WORD_TRAINING.md")
        return cls("hey_jarvis", threshold=threshold)

    def preload(self) -> None:
        """Загрузить модель. Долго (~1с) — желательно один раз при старте."""
        if self._model is not None:
            return
        try:
            from openwakeword.model import Model
            if self._is_custom:
                self._model = Model(
                    wakeword_models=[str(self._custom_path)],
                    inference_framework="onnx",
                )
                logger.info(
                    "wake_model_loaded",
                    model_type="custom",
                    path=str(self._custom_path),
                    score_key=self._score_key,
                    threshold=self._threshold,
                )
            else:
                self._model = Model(
                    wakeword_models=[self._wakeword],
                    inference_framework="onnx",
                )
                logger.info(
                    "wake_model_loaded",
                    model_type="builtin",
                    wakeword=self._wakeword,
                    threshold=self._threshold,
                )
        except Exception as e:
            logger.error("wake_model_load_failed", error=str(e))
            self._model = None

    @property
    def last_score(self) -> float:
        return self._last_score

    @property
    def ready(self) -> bool:
        return self._model is not None

    def feed(self, pcm_int16: np.ndarray) -> bool:
        """Скормить чанк (любой длины, int16, 16kHz mono).

        Returns True если wake-слово сработало в этом окне.
        """
        if self._model is None:
            return False

        pcm = np.asarray(pcm_int16, dtype=np.int16).ravel()
        self._buf.append(pcm)
        self._buf_total += pcm.shape[0]

        # Скользящие окна по 1280. Берём блоки целиком, остаток оставляем.
        triggered = False
        while self._buf_total >= CHUNK_SAMPLES:
            window = self._take_window(CHUNK_SAMPLES)
            try:
                scores = self._model.predict(window)
            except Exception as e:
                logger.warning("wake_predict_error", error=str(e))
                continue
            # openWakeWord возвращает dict {model_name: score}. Для custom модели
            # ключ = имя ONNX-файла без расширения (stem).
            score = float(scores.get(self._score_key, 0.0))
            if score == 0.0 and scores:
                # Fallback — берём максимум из всех (на случай если score_key угадан неверно)
                score = max(scores.values())
            self._last_score = score
            if score >= self._threshold:
                now = time.time()
                if now - self._last_trigger_ts >= COOLDOWN_SEC:
                    self._last_trigger_ts = now
                    triggered = True
                    logger.info("wake_word_detected", word=self._score_key, score=round(score, 3))
                    # Очищаем буфер чтобы повторно не сработать на хвосте
                    self._buf.clear()
                    self._buf_total = 0
                    break
        return triggered

    def reset(self) -> None:
        """Сброс буфера и cooldown — после выхода из active state."""
        self._buf.clear()
        self._buf_total = 0
        self._last_trigger_ts = time.time()

    def _take_window(self, n: int) -> np.ndarray:
        """Снять первые n samples из буфера."""
        out = np.empty(n, dtype=np.int16)
        filled = 0
        while filled < n and self._buf:
            head = self._buf[0]
            take = min(n - filled, head.shape[0])
            out[filled : filled + take] = head[:take]
            filled += take
            if take == head.shape[0]:
                self._buf.pop(0)
            else:
                self._buf[0] = head[take:]
        self._buf_total -= n
        return out

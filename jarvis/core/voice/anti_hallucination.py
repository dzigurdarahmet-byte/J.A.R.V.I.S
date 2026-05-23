"""Anti-hallucination фильтр для Whisper STT.

Whisper тренировали на ~680 тыс часов YouTube — поэтому на тихих/шумовых
сегментах он часто галлюцинирует YouTube-служебные фразы:
    «Субтитры от Н.Новикова», «Subtitles by Amara.org»,
    «Спасибо за просмотр», «Подпишитесь на канал» и т.п.

3 уровня защиты:
    1. SEGMENT GATE — отсекаем сегменты слишком короткие или слишком тихие
       (короткий шум вентилятора не должен идти в Whisper вообще).
    2. WHISPER METRICS — если faster-whisper вернул высокий no_speech_prob —
       не верим транскрипту.
    3. PATTERN FILTER — если транскрипт похож на YouTube-служебную фразу,
       выбрасываем (или вычищаем из частичного матча).
"""

from __future__ import annotations

import re
from typing import Final

import numpy as np

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

# ── 1. Пороги для гейта сегмента ───────────────────────────────────────

MIN_SEGMENT_SEC: Final = 0.5     # короче — выкидываем
# Default -55 dB — для нормальных микрофонов (USB, встроенный ноутбука).
# Bluetooth HFP (FreeBuds Hands-Free) даёт сильно тише; при INPUT_GAIN 8x
# в audio.py речь поднимается до нормального уровня. Управление через
# settings.jarvis_min_rms_db (.env: JARVIS_MIN_RMS_DB).
MIN_RMS_DB: Final = float(settings.jarvis_min_rms_db)

# ── 2. Порог уверенности Whisper в речи ────────────────────────────────

MAX_NO_SPEECH_PROB: Final = 0.7  # выше — Whisper сам не верит что речь

# ── 3. Чёрный список галлюцинаций ──────────────────────────────────────
# Regex case-insensitive. Цель — точно поймать паттерн но не зацепить
# легитимные фразы (например слово "переводчик" в разговоре).

HALLUCINATION_PATTERNS: Final[list[re.Pattern[str]]] = [
    # === Русские YouTube-служебные ===
    re.compile(r"субтитры", re.IGNORECASE),
    re.compile(r"корректор\s*[:.]", re.IGNORECASE),
    re.compile(r"редактор\s*[:.]", re.IGNORECASE),
    re.compile(r"перевод(чик)?\s*[:.]", re.IGNORECASE),
    re.compile(r"озвуч(ива)?л\s*[:.]", re.IGNORECASE),
    re.compile(r"спасибо\s+за\s+просмотр", re.IGNORECASE),
    re.compile(r"подпиш(итесь|ись)\s+на\s+канал", re.IGNORECASE),
    re.compile(r"поставьте\s+(лайк|колокольчик)", re.IGNORECASE),
    re.compile(r"увидимся\s+(в\s+)?(следующем|новом)\s+(видео|выпуске)", re.IGNORECASE),
    re.compile(r"продолжение\s+следует", re.IGNORECASE),
    re.compile(r"конец\s+(видео|серии|выпуска)", re.IGNORECASE),
    re.compile(r"всем\s+пока", re.IGNORECASE),
    re.compile(r"до\s+(встречи|свидания)\s+в\s+следующ", re.IGNORECASE),
    # Имена-маркеры от пиратских субтитров (часто всплывают)
    re.compile(r"н\.?\s*новиков", re.IGNORECASE),
    re.compile(r"добби\s*\(", re.IGNORECASE),
    # === English YouTube-служебные ===
    re.compile(r"subtitles?\s+(by|from)\s+", re.IGNORECASE),
    re.compile(r"thanks?\s+(for\s+)?watching", re.IGNORECASE),
    re.compile(r"like\s+and\s+subscribe", re.IGNORECASE),
    re.compile(r"smash\s+that\s+like", re.IGNORECASE),
    re.compile(r"see\s+you\s+(in\s+the\s+)?next\s+(video|episode)", re.IGNORECASE),
    # Сервисы транскрипции (часто упоминаются в субтитрах)
    re.compile(r"amara\.org", re.IGNORECASE),
    re.compile(r"dotsub\.com", re.IGNORECASE),
    re.compile(r"transcriber\s*[:.]", re.IGNORECASE),
]

# Доля символов от исходного текста, при которой матч считается «доминирующим»
# и весь сегмент бракуется. Если меньше — пытаемся вырезать кусок.
HALLUCINATION_DOMINANCE_RATIO: Final = 0.5


def gate_audio_segment(audio: np.ndarray, sample_rate: int = 16000) -> tuple[bool, str]:
    """Решить, стоит ли вообще прогонять сегмент через Whisper.

    Возвращает (pass, reason). Если pass=False, segment не передаётся дальше.
    """
    duration = audio.shape[0] / sample_rate
    if duration < MIN_SEGMENT_SEC:
        return False, f"too_short_{duration:.2f}s"

    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    if rms <= 0:
        return False, "silent"
    db = 20.0 * float(np.log10(rms))
    if db < MIN_RMS_DB:
        return False, f"too_quiet_{db:.1f}dB"

    return True, "ok"


def is_hallucination(
    text: str,
    no_speech_prob: float | None = None,
    dominance_ratio: float = HALLUCINATION_DOMINANCE_RATIO,
) -> tuple[bool, str]:
    """Проверить транскрипт на YouTube-галлюцинации.

    Возвращает (is_hallucination, reason).
    """
    text = text.strip()
    if not text:
        return True, "empty"

    # Whisper сам сомневается в речи
    if no_speech_prob is not None and no_speech_prob >= MAX_NO_SPEECH_PROB:
        return True, f"no_speech_prob_{no_speech_prob:.2f}"

    # Считаем сколько символов покрывают match'и галлюцинаций
    total_chars = len(text)
    matched_chars = 0
    matched_patterns: list[str] = []
    for pattern in HALLUCINATION_PATTERNS:
        for m in pattern.finditer(text):
            matched_chars += m.end() - m.start()
            matched_patterns.append(pattern.pattern[:30])
    if matched_chars == 0:
        return False, "clean"

    ratio = matched_chars / total_chars
    if ratio >= dominance_ratio:
        return True, f"hallucination_{ratio:.0%}_{matched_patterns[0]}"

    # частичный матч — не считаем галлюцинацией, но логируем
    logger.warning(
        "partial_hallucination_match",
        ratio=round(ratio, 2),
        patterns=matched_patterns,
        text_preview=text[:80],
    )
    return False, f"partial_{ratio:.0%}"


def clean_or_reject(text: str, no_speech_prob: float | None = None) -> str | None:
    """Объединённая проверка: вернуть None если галлюцинация целиком,
    иначе вычистить матчи из текста."""
    is_hall, reason = is_hallucination(text, no_speech_prob=no_speech_prob)
    if is_hall:
        logger.info("hallucination_rejected", reason=reason, text_preview=text[:80])
        return None
    # частичный матч — вычистим засветившиеся куски
    cleaned = text
    for pattern in HALLUCINATION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:—-")
    if not cleaned:
        return None
    return cleaned

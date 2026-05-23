"""Грубый Russian text → viseme timeline для lip-sync аватара.

НЕ полноценный g2p — упрощённое правило: считаем каждую гласную как опорную
точку рта, согласные между ними — как короткие переходы. Этого достаточно
для веб-аватара (TalkingHead.js), где визуально важно «открыл/закрыл рот в
правильном ритме», а не точный фонетический портрет.

Маппинг русских звуков → ARKit-совместимые visemes:
    а → "aa"       (open)
    о, у → "O"     (rounded)
    и, ы, э, е → "I" (smile-ish, узкая щель)
    я, ю → переход через "I" + "aa" / "O"
    м, п, б → "PP" (губы вместе)
    ф, в → "FF"    (нижняя губа на верхние зубы)
    т, д, с, з, н → "DD" (язык за зубами)
    к, г, х → "kk" (задняя стенка)
    ш, ж, щ → "CH"
    р, л → "nn"
    пауза → "sil"

Timeline: [{"t": float_sec, "viseme": str, "weight": 0..1}]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

VOWEL_MAP: Final[dict[str, str]] = {
    "а": "aa", "я": "aa",
    "о": "O", "ё": "O", "у": "O", "ю": "O",
    "и": "I", "ы": "I", "э": "I", "е": "I",
}
CONSONANT_MAP: Final[dict[str, str]] = {
    "м": "PP", "п": "PP", "б": "PP",
    "ф": "FF", "в": "FF",
    "т": "DD", "д": "DD", "с": "DD", "з": "DD", "н": "DD", "ц": "DD",
    "к": "kk", "г": "kk", "х": "kk",
    "ш": "CH", "ж": "CH", "щ": "CH", "ч": "CH",
    "р": "nn", "л": "nn",
    "й": "I",
}
RU_LOWER: Final = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


@dataclass(frozen=True, slots=True)
class VisemeKey:
    t: float          # секунды от начала
    viseme: str       # код виземы
    weight: float     # 0..1


def text_to_visemes(text: str, duration_sec: float) -> list[dict]:
    """Построить timeline visemes равномерно по тексту.

    Простая стратегия: считаем буквы → каждой букве выдаём временной слот,
    добавляем тишину в начале (50 ms) и в конце (100 ms). Гласным даём
    weight=0.85, согласным weight=0.6, паузам weight=0.0.
    """
    # Чистим — оставляем только русские буквы, цифры в текст, пробелы и пунктуацию
    chars: list[str] = []
    for c in text.lower():
        if c in RU_LOWER:
            chars.append(c)
        elif c.isdigit():
            chars.append(c)  # цифры => нейтральный звук (произнесём как "a")
        elif c in " ,.!?;:—-\n":
            chars.append(" ")
    if not chars:
        return []

    # Сжимаем подряд идущие пробелы
    compacted: list[str] = []
    prev_space = False
    for c in chars:
        if c == " ":
            if not prev_space:
                compacted.append(" ")
            prev_space = True
        else:
            compacted.append(c)
            prev_space = False
    chars = compacted

    n = len(chars)
    if n == 0:
        return []

    # Тайминг: 50 ms тишины в начале, 100 ms в конце, остальное равномерно
    lead = 0.05
    tail = 0.10
    inner = max(0.1, duration_sec - lead - tail)
    per_char = inner / n

    out: list[dict] = []
    # Начальная тишина
    out.append({"t": 0.0, "viseme": "sil", "weight": 0.0})

    for i, c in enumerate(chars):
        t = lead + i * per_char
        if c == " ":
            out.append({"t": round(t, 3), "viseme": "sil", "weight": 0.0})
            continue
        if c.isdigit():
            out.append({"t": round(t, 3), "viseme": "aa", "weight": 0.7})
            continue
        if c in VOWEL_MAP:
            out.append({"t": round(t, 3), "viseme": VOWEL_MAP[c], "weight": 0.85})
        elif c in CONSONANT_MAP:
            out.append({"t": round(t, 3), "viseme": CONSONANT_MAP[c], "weight": 0.6})
        else:
            # ъ, ь — артикуляция мягкого знака, тихая
            out.append({"t": round(t, 3), "viseme": "sil", "weight": 0.0})

    # Финальная тишина
    out.append({"t": round(duration_sec - tail, 3), "viseme": "sil", "weight": 0.0})
    out.append({"t": round(duration_sec, 3), "viseme": "sil", "weight": 0.0})

    return out

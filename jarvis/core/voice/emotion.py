"""Детектор эмоции для аватара по тексту ответа.

Эвристика — без LLM, локально и быстро. На выходе одна из меток:
joy / curious / concerned / serious / neutral.

Также экспортирует mapping в Yandex SpeechKit `synth.role`:
у голоса alena доступны только good / neutral, поэтому evil/sad
маппим в neutral, а joy → good.

Использование:
    from core.voice.emotion import detect_emotion, emotion_to_yandex_role
    label = detect_emotion("Босс, готово! Отчёт прикреплён.")  # → "joy"
    role  = emotion_to_yandex_role(label, voice="alena")        # → "good"
"""
from __future__ import annotations

import re
from typing import Final, Literal

EmotionLabel = Literal["joy", "curious", "concerned", "serious", "neutral"]

ALL_EMOTIONS: Final[tuple[EmotionLabel, ...]] = (
    "joy", "curious", "concerned", "serious", "neutral",
)


# Лексические маркеры — нижний регистр, в основном леммы.
# Намеренно компактно — точность ловить по знакам препинания, не по словарю.
_JOY_WORDS = (
    "отлично", "круто", "супер", "ура", "поздравля", "победа", "успех",
    "готово", "сделано", "прекрасно", "замечательно", "класс",
)
_CONCERN_WORDS = (
    "к сожалению", "проблема", "ошибка", "не получилось", "сбой",
    "увы", "не удалось", "критично", "опасно", "беда", "падает",
    "529", "timeout", "fail",
)
_SERIOUS_WORDS = (
    "внимание", "важно", "осторожно", "учти", "имей в виду",
    "напоминаю", "не забудь", "критически", "обязательно",
)


def _has_any(text_lower: str, words: tuple[str, ...]) -> bool:
    return any(w in text_lower for w in words)


def detect_emotion(text: str) -> EmotionLabel:
    """Эвристически определить эмоцию ответа.

    Порядок проверок — от более специфичных к общим:
      1. concerned — негативные ключевые слова
      2. joy — позитив + восклицания
      3. curious — вопрос
      4. serious — маркеры внимания/важности
      5. neutral — всё остальное
    """
    if not text or not text.strip():
        return "neutral"

    low = text.lower().strip()

    # concerned бьёт первым — негатив важнее радости даже если есть "!"
    if _has_any(low, _CONCERN_WORDS):
        return "concerned"

    # joy — позитивные слова, либо "!" в конце короткой положительной фразы
    if _has_any(low, _JOY_WORDS):
        return "joy"
    # "Готово!" / "Сделал!" — короткое восклицание без негатива
    if low.endswith("!") and len(low) <= 60 and not _has_any(low, _CONCERN_WORDS):
        return "joy"

    # curious — вопрос
    if "?" in low:
        return "curious"

    # serious — важные предупреждения
    if _has_any(low, _SERIOUS_WORDS):
        return "serious"

    return "neutral"


# Yandex SpeechKit `role` доступны не для всех голосов.
# alena: good, neutral
# jane:  evil, good, neutral
# omazh: evil, neutral
# zahar: good, neutral
# ermil: good, neutral
# filipp: только neutral (роли не поддерживает)
_YANDEX_ROLES_BY_VOICE: Final[dict[str, frozenset[str]]] = {
    "alena":  frozenset({"good", "neutral"}),
    "jane":   frozenset({"evil", "good", "neutral"}),
    "omazh":  frozenset({"evil", "neutral"}),
    "zahar":  frozenset({"good", "neutral"}),
    "ermil":  frozenset({"good", "neutral"}),
    "filipp": frozenset({"neutral"}),
}


def emotion_to_yandex_role(emotion: EmotionLabel, voice: str = "alena") -> str:
    """Перевести нашу эмоцию в Yandex `synth.role` под выбранный голос.

    Если для голоса роль не поддерживается — деградируем в neutral.
    """
    allowed = _YANDEX_ROLES_BY_VOICE.get(voice, frozenset({"neutral"}))
    candidates: tuple[str, ...]
    if emotion == "joy":
        candidates = ("good", "neutral")
    elif emotion == "concerned":
        # evil звучит слишком зловеще — деградируем в neutral для concerned
        candidates = ("neutral",)
    elif emotion == "curious":
        candidates = ("good", "neutral")  # лёгкое оживление в голосе
    elif emotion == "serious":
        candidates = ("neutral",)
    else:
        candidates = ("neutral",)
    for c in candidates:
        if c in allowed:
            return c
    return "neutral"

"""Лингвистическая нормализация текста перед TTS.

Pipeline (для Silero v3 ru):
    raw text
        → RUAccent (ставит U+0301 во все слова: 1.5M словарь + нейросеть)
        → конверсия U+0301 → '+' (синтаксис Silero)
        → ACCENT_DICT override (имена собственные + устойчивые фразы)
        → готовый текст с '+' маркерами

Silero вызывается с put_accent=False, чтобы он не запускал свой accentizer
поверх и не сбивал расстановку.
"""

from __future__ import annotations

import asyncio
import re
from typing import Final

from core.logging import get_logger

logger = get_logger(__name__)

COMBINING_ACUTE: Final = "́"  # U+0301


class _RUAccentSingleton:
    """Singleton RUAccent — модель тяжёлая, грузим один раз."""

    _instance: object | None = None
    _lock = asyncio.Lock()

    @classmethod
    def get(cls) -> object:
        if cls._instance is None:
            logger.info("ruaccent_loading")
            # импорт здесь — чтобы не платить латенси если text_norm не используется
            from ruaccent import RUAccent

            inst = RUAccent()
            # tiny_mode=True — лёгкая модель (~50MB вместо 500MB), грузится за секунды.
            # use_dictionary=True — словарь >1.5M ударений (главный источник правильности).
            # omograph_model_size='turbo' — компактный resolver омографов.
            # При появлении GPU можно переключить на turbo3.1 / big_poetry для большей точности.
            inst.load(
                omograph_model_size="big_poetry",
                use_dictionary=True,
                tiny_mode=True,
            )
            cls._instance = inst
            logger.info("ruaccent_loaded")
        return cls._instance


async def preload_ruaccent() -> None:
    """Принудительная загрузка модели (в фоне)."""
    await asyncio.to_thread(_RUAccentSingleton.get)


def _u0301_to_silero_plus(text: str) -> str:
    """Конверсия Unicode combining acute U+0301 → синтаксис Silero '+гласная'.

    "Те́кст" (т, е, U+0301, к, с, т) → "Т+екст" (т, +, е, к, с, т).
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Если СЛЕДУЮЩИЙ символ — U+0301, вставляем '+' ПЕРЕД текущей буквой
        if i + 1 < n and text[i + 1] == COMBINING_ACUTE:
            out.append("+")
            out.append(ch)
            i += 2  # пропускаем и букву, и combining accent
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _strip_existing_accents(text: str) -> str:
    """Убрать любые существующие '+' и U+0301 из текста перед прогоном RUAccent.

    Если пользователь напишет в коде/конфиге "Дж+арвис", и мы прогоним через RUAccent,
    тот не поймёт что это. Сначала убираем разметку, RUAccent ставит свою, потом
    override DICT восстанавливает наши форсированные ударения.
    """
    return text.replace("+", "").replace(COMBINING_ACUTE, "")


def normalize_for_silero(text: str, override_dict: dict[str, str] | None = None) -> str:
    """Главная функция: текст → текст с '+' ударениями.

    override_dict — словарь форсированных ударений (наши имена собственные,
    устойчивые фразы). Применяется ПОСЛЕ RUAccent, поэтому всегда выигрывает.
    """
    # 1. Сброс — на случай если в тексте уже есть наши '+'
    raw = _strip_existing_accents(text)

    # 2. RUAccent ставит ударения через U+0301
    accentizer = _RUAccentSingleton.get()
    accented = accentizer.process_all(raw)  # type: ignore[attr-defined]

    # 3. Конверсия U+0301 → '+'
    silero_text = _u0301_to_silero_plus(accented)

    # 4. Override DICT — самое последнее слово
    if override_dict:
        silero_text = _apply_overrides(silero_text, override_dict)

    return silero_text


def _apply_overrides(text: str, override_dict: dict[str, str]) -> str:
    """Применить override-словарь поверх уже-разметенного RUAccent текста.

    Перед matching убираем '+' из самих слов в тексте, чтобы regex видел чистое слово.
    Если совпадение нашлось — заменяем на версию из override (с её '+').
    """
    # Сначала индексируем где в исходном тексте стоят '+' (мы их потом можем не вернуть
    # если override перекрыл слово). Это нормально — override полностью заменяет.

    def _replace_one(word: str, accented_with_plus: str, s: str) -> str:
        # Очищаем word_clean для matching (без '+')
        word_clean = word.replace("+", "")
        # Pattern должен матчить слово в тексте, который МОЖЕТ содержать '+' внутри.
        # Делаем pattern: \w*\+?\w*\+?\w*...  Hmm — проще удалить '+' из текста для поиска,
        # а на матче подставить override. Использую stripper-проход через re.sub callback.
        # Но re.sub не умеет искать через "невидимые" '+' — нужно его сначала убрать,
        # запомнить позицию '+', потом подставить.

        # Простой подход: убираем все '+' из s перед matching, делаем replace, затем
        # возвращаем. Минус: теряем '+' от RUAccent в словах НЕ из override. Принимаем это:
        # после override-passа Silero все равно получит правильно акцентированные override-слова,
        # а слова из ruaccent не пострадают если их не нашли в override.
        # Решение: matching без снятия '+'. Pattern: вставляем '\+?' между каждой буквой
        # word_clean. Это сматчит "Дж+арвис" и "Джарвис" одинаково.
        chars = list(word_clean)
        pattern_str = r"(?<!\w)\+?" + r"\+?".join(re.escape(c) for c in chars) + r"\+?(?!\w)"
        pattern = re.compile(pattern_str, flags=re.IGNORECASE)

        def _keep_case(match: re.Match[str]) -> str:
            orig = match.group()
            # Слово в верхнем регистре → возвращаем accented в upper (сохраняем '+')
            orig_alpha = orig.replace("+", "")
            if orig_alpha.isupper():
                return accented_with_plus.upper()
            if orig_alpha[:1].isupper():
                chars_out = list(accented_with_plus)
                for i, c in enumerate(chars_out):
                    if c.isalpha():
                        chars_out[i] = c.upper()
                        break
                return "".join(chars_out)
            return accented_with_plus

        return pattern.sub(_keep_case, s)

    result = text
    # Длинные ключи сначала — чтобы "j.a.r.v.i.s" не перетёрся "jarvis"
    for word in sorted(override_dict.keys(), key=len, reverse=True):
        result = _replace_one(word, override_dict[word], result)
    return result

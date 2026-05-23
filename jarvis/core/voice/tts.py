"""Text-to-Speech через Silero v3 ru.

Speakers (мужские/женские голоса):
- aidar    — взрослый мужской (рекомендую для Джарвиса)
- baya     — женский
- kseniya  — женский (нейтральный)
- xenia    — женский
- eugene   — мужской (молодой)
- random   — случайный голос

Sample rate: 48000 (HD), 24000 (medium), 8000 (low).
Для Telegram voice и для динамиков ПК — 48000 / 24000.

Первый запуск тащит ~50MB модели из torch.hub (snakers4/silero-models).
Кэшируется в ~/.cache/torch/hub/snakers4_silero-models_master/
"""

from __future__ import annotations

import asyncio
from typing import Final

import numpy as np
import torch

from core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_LANGUAGE: Final = "ru"
DEFAULT_MODEL_ID: Final = "v3_1_ru"
DEFAULT_SPEAKER: Final = "xenia"
DEFAULT_SAMPLE_RATE: Final = 48000

# Словарь форсированных ударений для имён собственных и редких слов.
# Silero v3 use синтаксис "+гласная" ПЕРЕД ударной гласной (НЕ U+0301 после).
# Регексы регистронезависимые, сохраняют исходный регистр через .group().
ACCENT_DICT: Final[dict[str, str]] = {
    # Джарвис — основное обращение (ударение на А: Дж+арвис)
    "джарвис":   "дж+арвис",
    "джарвиса":  "дж+арвиса",
    "джарвису":  "дж+арвису",
    "джарвисом": "дж+арвисом",
    "джарвисе":  "дж+арвисе",
    "j.a.r.v.i.s": "дж+арвис",
    "jarvis":    "дж+арвис",
    # Имя Босса (ударение на Е: Серг+ей)
    "сергей":    "серг+ей",
    "сергея":    "серг+ея",
    "сергею":    "серг+ею",
    "сергеем":   "серг+еем",
    # Устойчивые фразы где accentizer Silero путает падеж
    "на связи":   "на св+язи",     # идиома "доступен/онлайн" — ударение на Я
    "на проводе": "на пр+оводе",
    # Часто встречающиеся слова где Silero ошибается
    "звонит":     "звон+ит",       # не зв+онит
    "звонят":     "звон+ят",
    "включить":   "включ+ить",
    "включит":    "включ+ит",
    "включена":   "включен+а",
    "торты":      "т+орты",        # классическая ловушка — не торт+ы
    "договор":    "догов+ор",      # не д+оговор
    "договоры":   "догов+оры",
    "красивее":   "крас+ивее",     # не красив+ее
    "обеспечение": "обесп+ечение", # не обеспеч+ение
    "позвонит":   "позвон+ит",
    "позвонишь":  "позвон+ишь",
    "позвонят":   "позвон+ят",
    "хочешь":     "х+очешь",
    "облегчить":  "облегч+ить",
    "одновременно": "одноврем+енно",
    "феномен":    "фен+омен",
    "квартал":    "кварт+ал",
    "столяр":     "стол+яр",
    "августовский": "+августовский",
    "ходатайство": "ход+атайство",
    "форзац":     "ф+орзац",
    "красивейший": "крас+ивейший",
}


class SileroTTS:
    """Async wrapper над Silero v3 TTS. Singleton."""

    def __init__(
        self,
        speaker: str = DEFAULT_SPEAKER,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        model_id: str = DEFAULT_MODEL_ID,
        language: str = DEFAULT_LANGUAGE,
        device: str = "cpu",
    ) -> None:
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._model_id = model_id
        self._language = language
        self._device = torch.device(device)
        self._model: torch.nn.Module | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _load(self) -> torch.nn.Module:
        if self._model is None:
            logger.info(
                "silero_loading", model_id=self._model_id, language=self._language
            )
            # torch.hub возвращает (model, example_text)
            result = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language=self._language,
                speaker=self._model_id,
                trust_repo=True,
            )
            # API меняется: иногда (model, _), иногда просто model — обрабатываем оба варианта
            model = result[0] if isinstance(result, tuple) else result
            model.to(self._device)
            self._model = model
            logger.info("silero_loaded", speaker=self._speaker, sample_rate=self._sample_rate)
        return self._model

    async def preload(self) -> None:
        """Принудительная загрузка модели (в фоне)."""
        await asyncio.to_thread(self._load)

    async def synthesize(
        self,
        text: str,
        speaker: str | None = None,
        sample_rate: int | None = None,
    ) -> np.ndarray:
        """Превратить текст в PCM float32 mono numpy array.

        Возвращает np.ndarray shape (samples,), dtype float32, диапазон [-1, 1].
        """
        model = self._load()
        spk = speaker or self._speaker
        sr = sample_rate or self._sample_rate

        # Override-словарь имён собственных и устойчивых фраз.
        # Silero сам ставит остальные ударения (put_accent=True).
        text = _apply_accents(text)

        # Silero ограничивает длину текста ~1000 символов на один вызов.
        # Длинные тексты разбиваем на предложения и склеиваем результаты.
        chunks = _split_text(text, max_chars=900)

        def _synth_chunk(t: str) -> np.ndarray:
            audio_tensor = model.apply_tts(  # type: ignore[attr-defined]
                text=t,
                speaker=spk,
                sample_rate=sr,
                put_accent=True,
                put_yo=True,
            )
            return audio_tensor.cpu().numpy().astype(np.float32)

        def _do() -> np.ndarray:
            audio_chunks = [_synth_chunk(c) for c in chunks if c.strip()]
            if not audio_chunks:
                return np.zeros(0, dtype=np.float32)
            # вставляем 200ms тишины между чанками
            gap = np.zeros(int(sr * 0.2), dtype=np.float32)
            joined: list[np.ndarray] = []
            for i, ch in enumerate(audio_chunks):
                joined.append(ch)
                if i < len(audio_chunks) - 1:
                    joined.append(gap)
            return np.concatenate(joined)

        audio = await asyncio.to_thread(_do)
        logger.info(
            "silero_synthesized",
            chars=len(text),
            samples=audio.shape[0],
            duration_sec=round(audio.shape[0] / sr, 2),
            speaker=spk,
        )
        return audio


def _apply_accents(text: str) -> str:
    """Расставить ударения по ACCENT_DICT (регистронезависимо)."""
    import re

    def _replace_one(word: str, accented: str, s: str) -> str:
        # Используем lookbehind/lookahead вместо \b, чтобы работали и многословные фразы.
        # \b ломается на пробелах (word→non-word boundary), а (?<!\w)/(?!\w) — нет.
        pattern = re.compile(
            r"(?<!\w)" + re.escape(word) + r"(?!\w)", flags=re.IGNORECASE
        )

        def _keep_case(match: re.Match[str]) -> str:
            orig = match.group()
            # Слово целиком в верхнем регистре → возвращаем accented в upper
            if orig.isupper():
                return accented.upper()
            # Первая буква заглавная → capitalize первую БУКВУ (не знак '+')
            if orig[:1].isupper():
                # Найдём первый алфавитный символ и поднимем регистр у него
                chars = list(accented)
                for i, ch in enumerate(chars):
                    if ch.isalpha():
                        chars[i] = ch.upper()
                        break
                return "".join(chars)
            return accented

        return pattern.sub(_keep_case, s)

    result = text
    # Длинные ключи (например "j.a.r.v.i.s") применяем первыми, чтобы они не
    # перетёрлись более короткими ("jarvis").
    for word in sorted(ACCENT_DICT.keys(), key=len, reverse=True):
        result = _replace_one(word, ACCENT_DICT[word], result)
    return result


def _split_text(text: str, max_chars: int = 900) -> list[str]:
    """Разбить длинный текст на куски по предложениям, чтоб каждый ≤ max_chars."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    # грубое разбиение по концам предложений
    import re

    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 <= max_chars:
            current = f"{current} {s}".strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks

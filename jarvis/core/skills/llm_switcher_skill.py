"""Skill: переключение primary LLM-провайдера голосом.

Босс может сказать «пользуйся Клодом», «пользуйся Дипсиком», «используй Яндекс»,
«перейди на Оламу» или «вернись на авто» — JARVIS зафиксирует выбор в
`workspace/llm_choice.txt`, и следующий `chat()` через SmartProvider пойдёт
через выбранного провайдера первым (а остальные останутся fallback'ами в
default-порядке).

Vision (`chat_with_image`) и tool-use (`chat_with_tools`) всегда идут через
Claude, потому что Deepseek/Yandex/Ollama их не поддерживают — переключение
влияет только на обычный `chat()`.

Skill **не использует** Claude/Deepseek/etc — он просто пишет в файл, поэтому
работает без сети и без ключей.
"""
from __future__ import annotations

import re
from typing import Any

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


# Известные варианты → канонический ключ для SmartProvider.set_choice().
# Регистронезависимо. Покрывает падежи и STT-варианты ("дипсик/дипсиком/deepseek").
_ALIASES: dict[str, list[str]] = {
    "claude": [
        r"клод\w*",        # клод, клода, клодом, клоду
        r"claude",
        r"антропик\w*",    # антропик, антропиком
        r"anthropic",
    ],
    "deepseek": [
        r"дипсик\w*",      # дипсик, дипсиком, дипсика
        r"deep\s*seek",
        r"дип\s*сик\w*",
    ],
    "yandex": [
        r"яндекс\w*",      # яндекс, яндексом
        r"yandex\w*",
        r"yagpt",
        r"яндекс[\s\-]?гпт",
    ],
    "ollama": [
        r"олам\w*",        # олама, оламой
        r"оллам\w*",       # оллама
        r"ollama",
        r"локальн\w+\s+(?:модел|llm|нейросет)",  # «локальной моделью»
        r"оффлайн\w*",     # «оффлайн режимом»
    ],
    "auto": [
        r"авто\b",
        r"автоматическ\w*",
        r"по\s+умолчанию",
        r"стандартн\w+\s+режим",
        r"default",
    ],
}

# Триггер-глаголы: «пользуйся / используй / перейди на / переключись на /
# давай через / вернись на / на … переключись».
_ACTION_VERBS = (
    r"(?:пользуй(?:ся|тесь)?|используй(?:те)?|"
    r"переключ\w+(?:\s+на)?|перейди\s+на|перейдём?\s+на|"
    r"давай\s+(?:через|на)|"
    r"вернись\s+(?:на|к)|верни(?:сь)?\s+на|"
    r"работай\s+(?:через|с|на))"
)

# Прицельные паттерны: глагол + псевдоним. Используются для match() — чтобы
# не ловить случайное «клод сказал что...».
_TRIGGER_RE = re.compile(
    rf"\b{_ACTION_VERBS}\b[\s,]*"
    rf"(?:"
    rf"клод\w*|claude|антропик\w*|anthropic|"
    rf"дипсик\w*|deep\s*seek|дип\s*сик\w*|"
    rf"яндекс\w*|yandex\w*|yagpt|"
    rf"олам\w*|оллам\w*|ollama|оффлайн\w*|локальн\w+\s+(?:модел|llm|нейросет)|"
    rf"авто|автоматическ\w*|по\s+умолчанию|default"
    rf")",
    flags=re.IGNORECASE | re.UNICODE,
)

# Спецслучай: «вернись на авто», «верни как было», «авто-режим LLM».
_AUTO_FALLBACK_RE = re.compile(
    r"\b(?:верни(?:сь)?|сбрось|сброс)\b.{0,40}\b(?:авто|default|умолчани\w+|как\s+было)\b",
    flags=re.IGNORECASE | re.UNICODE,
)


def _detect_choice(text: str) -> str | None:
    """Извлечь канонический ключ выбора из текста ('claude'/'deepseek'/...).

    Возвращает None если ни один alias не совпал.
    """
    if _AUTO_FALLBACK_RE.search(text):
        return "auto"
    for canonical, patterns in _ALIASES.items():
        for pat in patterns:
            if re.search(rf"\b{pat}\b", text, flags=re.IGNORECASE | re.UNICODE):
                return canonical
    return None


# Человекочитаемые названия для подтверждения.
_HUMAN_NAMES: dict[str, str] = {
    "claude": "Claude (Anthropic)",
    "deepseek": "Deepseek",
    "yandex": "YandexGPT",
    "ollama": "Ollama (локальная)",
    "auto": "авто-режим",
}


class LLMSwitcherSkill(KeywordSkill):
    """L1-skill: переключает primary LLM в SmartProvider."""

    name = "llm_switcher"
    keywords = [
        # Только связки глагол+псевдоним — чтобы не сработать на «клод сказал».
        r"\b(?:пользуй(?:ся|тесь)?|используй(?:те)?)\b[\s,]*"
        r"(?:клод\w*|claude|дипсик\w*|deep\s*seek|дип\s*сик\w*|"
        r"яндекс\w*|yandex\w*|yagpt|олам\w*|оллам\w*|ollama|"
        r"оффлайн\w*|локальн\w+\s+(?:модел|llm|нейросет)|"
        r"авто|автоматическ\w*|по\s+умолчанию|default)\b",
        r"\bпереключ\w+\s+(?:на\s+)?(?:клод\w*|claude|дипсик\w*|deep\s*seek|"
        r"яндекс\w*|yandex\w*|олам\w*|оллам\w*|ollama|авто)\b",
        r"\bперейди\s+на\s+(?:клод\w*|claude|дипсик\w*|deep\s*seek|"
        r"яндекс\w*|yandex\w*|олам\w*|оллам\w*|ollama|авто)\b",
        r"\bвернись?\s+(?:на|к)\s+(?:авто|default|клод\w*|claude)\b",
        # Status queries — кириллица «ллм» и латиница «llm», + «модель/нейросеть/ии».
        r"\b(?:какая|какую|какой)\s+(?:сейчас\s+)?(?:ллм|llm|модель|нейросеть|ии)\b",
        r"\b(?:ллм|llm|модель|нейросеть)\s+(?:сейчас|сегодня|у\s+тебя|активн\w+)\b",
        r"\bчерез\s+(?:что|какую\s+модель|какую\s+ллм|какую\s+llm)\s+ты\s+(?:сейчас\s+)?работаешь\b",
        r"\bкто\s+сейчас\s+отвечает\b",
    ]

    def __init__(self, smart_provider) -> None:  # noqa: ANN001 — circular import
        super().__init__()
        self._smart = smart_provider

    def match(self, text: str) -> float:
        # Status-query — отдельная ветка. Принимаем кириллицу «ллм» и латиницу «llm».
        if re.search(
            r"\b(?:какая|какую|какой)\s+(?:сейчас\s+)?(?:ллм|llm|модель|нейросеть|ии)\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            return 1.0
        if re.search(
            r"\b(?:ллм|llm|модель|нейросеть)\s+(?:сейчас|сегодня|у\s+тебя|активн\w+)\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            return 1.0
        if re.search(
            r"\bчерез\s+(?:что|какую\s+модель|какую\s+ллм|какую\s+llm)\s+ты\s+(?:сейчас\s+)?работаешь\b",
            text,
            flags=re.IGNORECASE | re.UNICODE,
        ):
            return 1.0
        if re.search(r"\bкто\s+сейчас\s+отвечает\b", text, flags=re.IGNORECASE | re.UNICODE):
            return 1.0
        if _AUTO_FALLBACK_RE.search(text):
            return 1.0
        if _TRIGGER_RE.search(text):
            return 1.0
        return super().match(text)

    async def run(self, text: str, request_id: str) -> SkillResult:
        # 1) Статус-запрос — без записи. Принимаем кириллицу «ллм» и латиницу «llm».
        status_patterns = (
            r"\b(?:какая|какую|какой)\s+(?:сейчас\s+)?(?:ллм|llm|модель|нейросеть|ии)\b",
            r"\b(?:ллм|llm|модель|нейросеть)\s+(?:сейчас|сегодня|у\s+тебя|активн\w+)\b",
            r"\bчерез\s+(?:что|какую\s+модель|какую\s+ллм|какую\s+llm)\s+ты\s+(?:сейчас\s+)?работаешь\b",
            r"\bкто\s+сейчас\s+отвечает\b",
        )
        if any(re.search(p, text, flags=re.IGNORECASE | re.UNICODE) for p in status_patterns):
            current = self._smart.get_choice()
            human = _HUMAN_NAMES.get(current, current)
            return SkillResult(
                text=f"Сейчас работаю через {human}, Босс.",
                speakable=True,
                data={"llm_choice": current},
            )

        # 2) Переключение.
        choice = _detect_choice(text)
        if choice is None:
            return SkillResult(
                text=(
                    "Не понял, на что переключиться. Скажи «пользуйся Клодом», "
                    "«пользуйся Дипсиком», «используй Яндекс», «через Оламу» или "
                    "«вернись на авто»."
                ),
                speakable=True,
            )

        ok = self._smart.set_choice(choice)
        if not ok:
            logger.warning("llm_switch_failed", choice=choice)
            return SkillResult(
                text=(
                    f"Не получилось переключить на {_HUMAN_NAMES.get(choice, choice)}. "
                    "Возможно, провайдер не сконфигурирован — проверь .env."
                ),
                speakable=True,
            )

        human = _HUMAN_NAMES.get(choice, choice)
        logger.info("llm_switched", choice=choice, by="voice")

        # Особые подтверждения для Босса в JARVIS-тоне.
        if choice == "auto":
            text_out = "Возвращаюсь в авто-режим, Босс. Цепочка: Клод → Дипсик → Яндекс → Ollama."
        else:
            text_out = f"Принято, Босс. Перехожу на {human}."
        return SkillResult(
            text=text_out,
            speakable=True,
            data={"llm_choice": choice},
        )

    # ── L2 Tool-use (Claude может вызвать сам, если в чате попросят) ────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "llm_switch",
            "description": (
                "Switch which LLM JARVIS uses as the primary provider. Use when "
                "the Boss explicitly asks to switch models (e.g. 'use Claude', "
                "'use Deepseek', 'switch to Yandex', 'back to auto'). For "
                "vision and tool-use Claude is always used regardless."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "choice": {
                        "type": "string",
                        "enum": ["auto", "claude", "deepseek", "yandex", "ollama"],
                        "description": "Which provider to make primary.",
                    },
                },
                "required": ["choice"],
            },
        }

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        choice = (args.get("choice") or "").strip().lower()
        if choice not in {"auto", "claude", "deepseek", "yandex", "ollama"}:
            return SkillResult(
                text=f"Неизвестный провайдер {choice!r}.",
                speakable=True,
            )
        ok = self._smart.set_choice(choice)
        if not ok:
            return SkillResult(
                text=f"Не получилось переключить на {choice}.",
                speakable=True,
            )
        human = _HUMAN_NAMES.get(choice, choice)
        return SkillResult(
            text=f"Готово, основная LLM теперь {human}.",
            speakable=True,
            data={"llm_choice": choice},
        )

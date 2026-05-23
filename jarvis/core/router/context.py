"""L1.5 context-tracker: запоминаем последний intent per channel
и распознаём короткие follow-up'ы вида «А в Сыктывкаре?» / «А завтра?» / «А юань?».

Зачем: чтобы не звать Claude+tools на простых продолжениях ранее заданного intent.
Экономит время (1.2с → 50мс) и API-расход.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Final

# Сколько секунд интент считается «свежим» для follow-up
CONTEXT_TTL_SEC: Final = 60.0

# Какие intents поддерживают follow-up через L1.5
FOLLOWUP_INTENTS: Final[set[str]] = {
    "weather",
    "weather_forecast",
    "timezone",
    "currency",
    "crypto",
}


@dataclass(slots=True)
class ContextSnapshot:
    """Состояние последнего intent в канале."""

    intent: str
    args: dict[str, str] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class ContextStore:
    """Per-channel storage. In-memory, не персистентен между рестартами."""

    def __init__(self, ttl_sec: float = CONTEXT_TTL_SEC) -> None:
        self._ctx: dict[str, ContextSnapshot] = {}
        self._ttl = ttl_sec

    def set(self, channel: str, intent: str, args: dict[str, str] | None = None) -> None:
        self._ctx[channel] = ContextSnapshot(intent=intent, args=dict(args or {}))

    def get_fresh(self, channel: str) -> ContextSnapshot | None:
        snap = self._ctx.get(channel)
        if not snap:
            return None
        if time.time() - snap.ts > self._ttl:
            self._ctx.pop(channel, None)
            return None
        return snap

    def clear(self, channel: str) -> None:
        self._ctx.pop(channel, None)


# ──────────────────────────────────────────────────────────────────────
# Детектор «короткого продолжения»
# ──────────────────────────────────────────────────────────────────────

# Короткий follow-up — это:
# - ≤ 6 слов
# - либо начинается с подхватчика ('а', 'и', 'ну а'),
# - либо короткий вопрос с городом/временем/валютой,
# - либо просто «ещё».
_FOLLOWUP_PREFIX_RE = re.compile(
    r"^\s*(а|и|ну\s+а|ну|так\s+а|ок\s+а)\b",
    re.IGNORECASE | re.UNICODE,
)


def is_short_followup(text: str) -> bool:
    """True если фраза похожа на короткое продолжение предыдущего intent."""
    t = text.strip()
    if not t:
        return False
    words = re.findall(r"\w+", t, flags=re.UNICODE)
    if len(words) > 6:
        return False
    if _FOLLOWUP_PREFIX_RE.match(t):
        return True
    # Короткое одно-двухсловное продолжение без подхватчика тоже принимаем,
    # но только если есть «временной маркер» или явный город ниже
    return len(words) <= 3


# ──────────────────────────────────────────────────────────────────────
# Извлечение аргументов для каждого intent
# ──────────────────────────────────────────────────────────────────────

# Временные маркеры → запрос forecast
_TIME_MARKERS: Final[dict[str, str]] = {
    "завтра": "завтра",
    "послезавтра": "послезавтра",
    "выходные": "выходные",
    "выходных": "выходные",
    "неделю": "неделю",
    "сегодня": "сегодня",
    "вечером": "вечером",
    "утром": "утром",
}

# Валюты (для currency, crypto)
_CURRENCY_HINTS: Final[set[str]] = {
    "доллар", "доллара", "усд", "usd",
    "евро", "евр", "eur",
    "юань", "юаня", "cny",
    "фунт", "фунта", "gbp",
}
_CRYPTO_HINTS: Final[set[str]] = {
    "биткоин", "битка", "битки", "битк", "btc",
    "эфир", "эфира", "эфиром", "eth", "ethereum",
}


def _extract_city(text: str) -> str | None:
    """Из «А в Сыктывкаре?» или «в Москве?» вытащить 'Сыктывкаре' / 'Москве'."""
    # «в X», «в X-Y» — кириллица + дефисы
    m = re.search(r"\bв[оо]?\s+([А-ЯЁа-яё][А-ЯЁа-яё\-]+)", text, flags=re.IGNORECASE | re.UNICODE)
    if m:
        return m.group(1)
    return None


def _extract_time_marker(text: str) -> str | None:
    low = text.lower()
    for w, canonical in _TIME_MARKERS.items():
        if re.search(rf"\b{w}\b", low):
            return canonical
    return None


def _extract_currency(text: str) -> str | None:
    low = text.lower()
    for w in _CURRENCY_HINTS:
        if re.search(rf"\b{w}\b", low):
            return w
    return None


def _extract_crypto(text: str) -> str | None:
    low = text.lower()
    for w in _CRYPTO_HINTS:
        if re.search(rf"\b{w}\b", low):
            return w
    return None


def extract_followup_args(text: str, last_intent: str) -> dict[str, str] | None:
    """Извлечь новые аргументы из follow-up под уже известный intent.

    Возвращает dict с новыми args либо None если фраза не подходит под этот intent.
    """
    if last_intent not in FOLLOWUP_INTENTS:
        return None

    if last_intent in ("weather", "weather_forecast", "timezone"):
        city = _extract_city(text)
        time_marker = _extract_time_marker(text)
        # Для weather/forecast: если есть city → продолжаем в weather с city,
        # если есть time_marker → переключаем weather → forecast (или forecast → forecast)
        if last_intent == "weather":
            if city:
                return {"city": city, "intent": "weather"}
            if time_marker:
                return {"time": time_marker, "intent": "weather_forecast"}
        elif last_intent == "weather_forecast":
            if city:
                return {"city": city, "intent": "weather_forecast"}
            if time_marker:
                return {"time": time_marker, "intent": "weather_forecast"}
        elif last_intent == "timezone":
            if city:
                return {"city": city, "intent": "timezone"}

    elif last_intent == "currency":
        cur = _extract_currency(text)
        crypto = _extract_crypto(text)
        if crypto:
            return {"asset": crypto, "intent": "crypto"}
        if cur:
            # CurrencySkill уже отдаёт все три валюты — повторный вызов даст актуальные данные
            return {"intent": "currency"}

    elif last_intent == "crypto":
        crypto = _extract_crypto(text)
        cur = _extract_currency(text)
        if cur:
            return {"intent": "currency"}
        if crypto:
            return {"intent": "crypto"}

    return None

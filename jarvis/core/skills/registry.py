"""Реестр встроенных скиллов: 20 готовых функций.

Каждый скилл — небольшой класс прямо здесь. Если разрастётся — выносим
в core/skills/builtin/<имя>.py.
"""

from __future__ import annotations

import asyncio
import json
import operator
import random as _random
import re
from datetime import datetime, timedelta, timezone
from typing import Final
from urllib.parse import quote

import httpx

from core.config import settings
from core.logging import get_logger
from core.memory import MemoryManager
from core.router import Router, SkillResult
from core.skills.base import KeywordSkill
from core.skills.briefing_skill import BriefingSkill
from core.skills.calendar_skill import CalendarSkill
from core.skills.geo_skill import GeoSkill
from core.skills.music_skill import MusicSkill
from core.skills.recall_skill import RecallSkill
from core.skills.app_control_skill import AppControlSkill
from core.skills.clipboard_skill import ClipboardSkill
from core.skills.code_assist_skill import CodeAssistSkill
from core.skills.file_skill import FileSkill
from core.skills.github_skill import GitHubSkill
from core.skills.goals_skill import GoalsSkill, GoalsStore
from core.skills.llm_switcher_skill import LLMSwitcherSkill
from core.skills.news_ru_skill import NewsRuSkill
from core.skills.read_aloud_skill import ReadAloudSkill
from core.skills.reminders_skill import RemindersSkill, RemindersStore
from core.skills.screenshot_skill import ScreenshotDescribeSkill
from core.skills.system_info_skill import SystemInfoSkill
from core.skills.todo_skill import TodoSkill, TodoStore
from core.skills.volume_skill import VolumeSkill
from core.skills.web_search_skill import WebSearchSkill
from core.skills.yandex_smart_home_skill import YandexSmartHomeSkill
from core.skills.weather_providers import fetch_current, fetch_forecast

logger = get_logger(__name__)

# ───────────────────────────────────────────────────────────────────────
# 1. TIME — сколько времени
# ───────────────────────────────────────────────────────────────────────

class TimeSkill(KeywordSkill):
    name = "time"
    keywords = [
        r"\bкот[оа]рый\s+час\b",
        # терпим STT/typo
        r"\bскольк\w*\s+(сейчас\s+)?врем\w*\b",
        r"\bскока\s+врем\w*\b",
        r"\bврем[ея]\s+сейчас\b",
        r"\bсейчас\s+врем[ея]\b",
        r"\bкоторый\s+ча?с\b",
        r"\bтекущее\s+врем[ея]\b",
        r"\bчас\s+сейчас\b",
    ]

    def match(self, text: str) -> float:
        # Уступаем TimezoneSkill: если в тексте "в <город>" — это запрос по таймзоне
        if re.search(r"\bв\s+[А-ЯA-Z][А-Яа-яA-Za-z\-]+", text):
            return 0.0
        # Уступаем если упоминается известный город из CITY_TZ
        low = text.lower()
        for city in CITY_TZ:
            if city in low:
                return 0.0
        return super().match(text)

    async def run(self, text: str, request_id: str) -> SkillResult:
        now = datetime.now()
        hh, mm = now.hour, now.minute
        return SkillResult(text=f"Сейчас {hh:02d}:{mm:02d}.", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 2. DATE — какая дата / день недели
# ───────────────────────────────────────────────────────────────────────

DAYS_RU: Final = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU: Final = ["января", "февраля", "марта", "апреля", "мая", "июня",
                    "июля", "августа", "сентября", "октября", "ноября", "декабря"]


class DateSkill(KeywordSkill):
    name = "date"
    keywords = [
        r"\bкакая?\s+сегодня\s+дата\b",
        r"\bкакое\s+(сегодня\s+)?число\b",
        r"\bкакой\s+(сегодня\s+)?день\s+недели\b",
        r"\bсегодняшняя\s+дата\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        now = datetime.now()
        day_name = DAYS_RU[now.weekday()]
        return SkillResult(
            text=f"Сегодня {day_name}, {now.day} {MONTHS_RU[now.month - 1]}.",
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# 3. TIMEZONE — время в другом городе
# ───────────────────────────────────────────────────────────────────────

# Склонения городов: любой падеж -> именительный (для API) + предложный (для вывода)
CITY_NORM: Final[dict[str, str]] = {
    "москва": "москва", "москве": "москва", "москвы": "москва", "москву": "москва",
    "питер": "санкт-петербург", "питере": "санкт-петербург", "спб": "санкт-петербург",
    "санкт-петербург": "санкт-петербург", "санкт-петербурге": "санкт-петербург",
    "нью-йорк": "нью-йорк", "нью-йорке": "нью-йорк",
    "лондон": "лондон", "лондоне": "лондон",
    "париж": "париж", "париже": "париж",
    "берлин": "берлин", "берлине": "берлин",
    "токио": "токио",
    "сыктывкар": "сыктывкар", "сыктывкаре": "сыктывкар",
    "екатеринбург": "екатеринбург", "екатеринбурге": "екатеринбург",
    "новосибирск": "новосибирск", "новосибирске": "новосибирск",
    "владивосток": "владивосток", "владивостоке": "владивосток",
    "хабаровск": "хабаровск", "хабаровске": "хабаровск",
    "дубай": "дубай", "дубае": "дубай",
    "стамбул": "стамбул", "стамбуле": "стамбул",
}

CITY_PREP: Final[dict[str, str]] = {
    # именительный -> предложный («в ...»)
    "москва": "Москве", "санкт-петербург": "Санкт-Петербурге",
    "нью-йорк": "Нью-Йорке", "лондон": "Лондоне", "париж": "Париже",
    "берлин": "Берлине", "токио": "Токио",
    "сыктывкар": "Сыктывкаре", "екатеринбург": "Екатеринбурге",
    "новосибирск": "Новосибирске", "владивосток": "Владивостоке",
    "хабаровск": "Хабаровске", "дубай": "Дубае", "стамбул": "Стамбуле",
}


def normalize_city(raw: str) -> str:
    """Любая форма города -> nominative lowercase. Если неизвестно — возвращаем как есть."""
    low = raw.lower().strip(".,!?")
    return CITY_NORM.get(low, low)


def city_prep(name_nom: str) -> str:
    """Nominative lowercase -> предложный с большой буквы. Fallback: .title()."""
    return CITY_PREP.get(name_nom, name_nom.title())

CITY_TZ: Final[dict[str, int]] = {
    # offsets from UTC, hours
    "москва": 3, "питер": 3, "санкт-петербург": 3, "спб": 3,
    "лондон": 0, "париж": 1, "берлин": 1, "рим": 1, "мадрид": 1,
    "нью-йорк": -5, "вашингтон": -5, "лос-анджелес": -8, "сан-франциско": -8,
    "токио": 9, "сеул": 9, "пекин": 8, "шанхай": 8, "сингапур": 8,
    "дубай": 4, "стамбул": 3,
    "сидней": 11, "мельбурн": 11,
    "новосибирск": 7, "екатеринбург": 5, "владивосток": 10, "хабаровск": 10,
    "франкфурт": 1, "цюрих": 1, "вена": 1,
}


class TimezoneSkill(KeywordSkill):
    name = "timezone"
    keywords = [
        # STT-опечатки в "сколько"
        r"\bскольк\w*\s+(сейчас\s+)?врем\w*\s+в\s+[А-Яа-я\-]+",
        r"\bврем[ея]\s+в\s+[А-Яа-я\-]+",
        r"\bкот[оа]рый\s+час\s+в\s+[А-Яа-я\-]+",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        # ищем город после "в "
        m = re.search(r"\bв\s+([А-Яа-я\-]+)", text, flags=re.IGNORECASE)
        if not m:
            return SkillResult(text="Босс, в каком городе?", speakable=True)
        city = normalize_city(m.group(1))
        offset = CITY_TZ.get(city)
        if offset is None:
            return SkillResult(
                text=f"Города {city.title()} нет в моём базовом списке. Уточни или назови столицу.",
                speakable=True,
            )
        now_utc = datetime.now(timezone.utc)
        city_time = now_utc.astimezone(timezone(timedelta(hours=offset)))
        return SkillResult(
            text=f"В {city_prep(city)} сейчас {city_time:%H:%M}.",
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# 4. TIMER — таймер на N минут/секунд
# ───────────────────────────────────────────────────────────────────────

_active_timers: dict[str, asyncio.Task] = {}


class TimerSkill(KeywordSkill):
    name = "timer"
    keywords = [
        r"\bтаймер\s+на\s+\d+",
        r"\bпоставь\s+таймер\b",
        r"\bпоставь\s+на\s+\d+",
        r"\bразбуди\s+через\b",
        # «напомни через …» — это RemindersSkill (с persistent storage,
        # пережёвывает HUD-restart). Timer — только для эфемерных таймеров
        # типа кулинарии "поставь на 5 минут".
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        secs = self._parse_duration_seconds(text)
        if secs is None:
            return SkillResult(text="Босс, не понял на сколько ставить таймер.", speakable=True)
        # отдельная корутина дождётся и опубликует SYSTEM-event с reminder
        async def _fire() -> None:
            await asyncio.sleep(secs)
            from core.event_bus import EventType, JarvisEvent, bus
            await bus.publish(JarvisEvent(
                type=EventType.SYSTEM,
                source="skill:timer",
                priority="high",
                data={"text": f"Босс, таймер на {secs // 60} минут сработал."},
            ))
            _active_timers.pop(request_id, None)

        _active_timers[request_id] = asyncio.create_task(_fire())
        mins, sec = divmod(secs, 60)
        if mins and not sec:
            human = f"{mins} мин."
        elif mins:
            human = f"{mins} мин. {sec} сек."
        else:
            human = f"{sec} сек."
        return SkillResult(text=f"Таймер на {human} запущен.", speakable=True)

    @staticmethod
    def _parse_duration_seconds(text: str) -> int | None:
        m = re.search(r"(\d+)\s*(сек|секунд|мин|минут|час|часов|часа)", text, flags=re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+)", text)
            if not m:
                return None
            return int(m.group(1)) * 60  # дефолт минуты
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("сек"):
            return n
        if unit.startswith("мин"):
            return n * 60
        if unit.startswith("час"):
            return n * 3600
        return n * 60


# ───────────────────────────────────────────────────────────────────────
# 5. ALARM — будильник на HH:MM (упрощённо, in-memory)
# ───────────────────────────────────────────────────────────────────────

class AlarmSkill(KeywordSkill):
    name = "alarm"
    keywords = [
        r"\bбудильник\s+на\s+\d",
        r"\bпоставь\s+будильник\b",
        r"\bразбуди\s+в\s+\d",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        m = re.search(r"(\d{1,2})[:\.](\d{2})", text)
        if not m:
            m = re.search(r"в\s+(\d{1,2})\s*(?:час|часов|ч)\b", text, flags=re.IGNORECASE)
            if not m:
                return SkillResult(text="Босс, на какое время будильник? Скажи в формате 7:30.", speakable=True)
            hh, mm = int(m.group(1)), 0
        else:
            hh, mm = int(m.group(1)), int(m.group(2))
        now = datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()

        async def _fire() -> None:
            await asyncio.sleep(delay)
            from core.event_bus import EventType, JarvisEvent, bus
            await bus.publish(JarvisEvent(
                type=EventType.SYSTEM,
                source="skill:alarm",
                priority="high",
                data={"text": f"Босс, будильник на {hh:02d}:{mm:02d}."},
            ))
            _active_timers.pop(f"alarm-{request_id}", None)

        _active_timers[f"alarm-{request_id}"] = asyncio.create_task(_fire())
        return SkillResult(text=f"Будильник на {hh:02d}:{mm:02d} поставлен.", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 6. WEATHER — погода сейчас (OpenWeather)
# ───────────────────────────────────────────────────────────────────────

class WeatherSkill(KeywordSkill):
    name = "weather"
    keywords = [
        r"\bпогод[ауы]\b",
        r"\bкак(ая|ой)\s+(на\s+улице|за\s+окном)\b",
        r"\bсколько\s+градус",
        r"\bжарко\b|\bхолодно\b",
    ]

    def match(self, text: str) -> float:
        # Уступаем WeatherForecastSkill: прогноз/завтра/выходные/неделю → не я
        if re.search(
            r"\bпрогноз\b|\bзавтра\b|\bпослезавтра\b|\bвыходны[ехм]\b|\bна\s+неделю\b",
            text,
            flags=re.IGNORECASE,
        ):
            return 0.0
        return super().match(text)

    async def run(self, text: str, request_id: str) -> SkillResult:
        raw_city = self._extract_city(text) or settings.jarvis_default_city
        city = normalize_city(raw_city)
        snap = await fetch_current(city)
        if snap is None:
            return SkillResult(
                text=f"Босс, погода для {city.title()} недоступна — провайдеры молчат.",
                speakable=True,
            )
        nice = city_prep(city) if city in CITY_PREP else snap.city
        return SkillResult(
            text=f"В {nice}: {snap.temp_c:+d}°, ощущается как {snap.feels_like_c:+d}°, {snap.description}, ветер {snap.wind_ms} м/с.",
            speakable=True,
        )

    @staticmethod
    def _extract_city(text: str) -> str | None:
        m = re.search(r"\bв\s+([А-Яа-я\-]+)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip(".,!?").capitalize()
        return None


# ───────────────────────────────────────────────────────────────────────
# 7. WEATHER FORECAST — прогноз на 3 дня
# ───────────────────────────────────────────────────────────────────────

class WeatherForecastSkill(KeywordSkill):
    name = "weather_forecast"
    keywords = [
        r"\bпрогноз\s+погоды\b",
        r"\bпрогноз\b.*\bпогод",
        r"\bпогод[ауы]\b.*\bна\s+(завтра|послезавтра|выходны[ехм]|неделю)",
        r"\bпогод[ауы]\b.*\bзавтра\b",
        r"\bчто\s+с\s+погод",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        raw_city = WeatherSkill._extract_city(text) or settings.jarvis_default_city
        city = normalize_city(raw_city)
        res = await fetch_forecast(city, days=3)
        if res is None:
            return SkillResult(text=f"Прогноз погоды в {city.title()} недоступен.", speakable=True)
        found_name, days = res
        nice = city_prep(city) if city in CITY_PREP else found_name
        parts: list[str] = []
        for fd in days:
            dt = datetime.fromisoformat(fd.date_iso)
            parts.append(f"{DAYS_RU[dt.weekday()].capitalize()}: {fd.temp_c:+d}°, {fd.description}")
        return SkillResult(text=f"Прогноз погоды в {nice}. " + ". ".join(parts) + ".", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 8. CURRENCY — курс USD/EUR через ЦБ РФ
# ───────────────────────────────────────────────────────────────────────

class CurrencySkill(KeywordSkill):
    name = "currency"
    keywords = [
        r"\bкурс\s+(доллара|евро|usd|eur|юаня|фунта)\b",
        r"\bсколько\s+стоит\s+(доллар|евро|usd|eur)\b",
        r"\b(usd|eur|доллар|евро)\s+к\s+(рублю|руб)",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get("https://www.cbr-xml-daily.ru/daily_json.js")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.error("cbr_api_error", error=str(e))
            return SkillResult(text="Босс, курсы ЦБ сегодня недоступны.", speakable=True)
        rates = data["Valute"]
        usd = rates["USD"]["Value"]
        eur = rates["EUR"]["Value"]
        cny = rates["CNY"]["Value"]
        return SkillResult(
            text=f"По ЦБ: доллар {usd:.2f}, евро {eur:.2f}, юань {cny:.2f} рубля.",
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# 9. CRYPTO — курс BTC/ETH через CoinGecko (без ключа)
# ───────────────────────────────────────────────────────────────────────

class CryptoSkill(KeywordSkill):
    name = "crypto"
    keywords = [
        r"\bкурс\s+(битк|btc|eth|эфир)",
        r"\b(биткоин|bitcoin|эфир|ethereum)\b",
        r"\bсколько\s+стоит\s+(биткоин|эфир|btc|eth)\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "bitcoin,ethereum", "vs_currencies": "usd"}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.error("coingecko_error", error=str(e))
            return SkillResult(text="Босс, CoinGecko сейчас недоступен.", speakable=True)
        btc = data.get("bitcoin", {}).get("usd")
        eth = data.get("ethereum", {}).get("usd")
        if btc is None or eth is None:
            return SkillResult(text="Котировки крипты не пришли.", speakable=True)
        return SkillResult(
            text=(f"Биткоин {btc:,.0f} долларов, эфир {eth:,.0f} долларов.".replace(",", " ").replace("  ", " ")),
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# 10-13. NOTES — note / remember / forget / notes_list
# ───────────────────────────────────────────────────────────────────────

class NoteSkill(KeywordSkill):
    name = "note"
    keywords = [
        r"\bзапиши\b",
        r"\bдобавь\s+заметку\b",
        r"\bсделай\s+заметку\b",
    ]

    def __init__(self, memory: MemoryManager) -> None:
        super().__init__()
        self._memory = memory

    async def run(self, text: str, request_id: str) -> SkillResult:
        # вычленяем содержимое после "запиши:" или "запиши"
        m = re.search(r"\b(?:запиши|заметк[ау])[:\s]+(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return SkillResult(text="Босс, что записать?", speakable=True)
        note_text = m.group(1).strip().rstrip(".")
        self._memory.remember_fact(f"Заметка: {note_text}")
        return SkillResult(text="Записал.", speakable=True)


class RememberSkill(KeywordSkill):
    name = "remember"
    keywords = [
        r"\bзапомни[:\s]+\S",
        r"\bне\s+забудь[:\s]+\S",
    ]

    def __init__(self, memory: MemoryManager) -> None:
        super().__init__()
        self._memory = memory

    async def run(self, text: str, request_id: str) -> SkillResult:
        m = re.search(r"\b(?:запомни|не\s+забудь)[:\s]+(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return SkillResult(text="Босс, что запомнить?", speakable=True)
        fact = m.group(1).strip().rstrip(".")
        self._memory.remember_fact(fact)
        return SkillResult(text="Запомнил.", speakable=True)


class ForgetSkill(KeywordSkill):
    name = "forget"
    keywords = [r"\bзабудь\s+(заметку|про|что)\b", r"\bудали\s+заметку\b"]

    async def run(self, text: str, request_id: str) -> SkillResult:
        # На MVP — заглушка. Удаление через ручную правку MEMORY.md.
        return SkillResult(
            text="Босс, удаление заметок через UI пока не сделано — поправь руками в MEMORY.md.",
            speakable=True,
        )


class NotesListSkill(KeywordSkill):
    name = "notes_list"
    keywords = [
        r"\bпокажи\s+(мои\s+)?заметки\b",
        r"\bкакие\s+(у\s+меня\s+)?заметки\b",
        r"\bсписок\s+заметок\b",
    ]

    def __init__(self, memory: MemoryManager) -> None:
        super().__init__()
        self._memory = memory

    async def run(self, text: str, request_id: str) -> SkillResult:
        mem = self._memory.load_memory_md()
        # извлекаем секцию "## Заметки"
        m = re.search(r"##\s+Заметки\s*\n([\s\S]+?)(?=\n##\s+|\Z)", mem, flags=re.IGNORECASE)
        if not m:
            return SkillResult(text="Заметок пока нет, Босс.", speakable=True)
        notes_raw = m.group(1).strip()
        if not notes_raw:
            return SkillResult(text="Заметок пока нет.", speakable=True)
        # берём последние 5 строк
        lines = [ln for ln in notes_raw.splitlines() if ln.strip().startswith("-")]
        recent = lines[-5:]
        if not recent:
            return SkillResult(text="Заметок пока нет.", speakable=True)
        text_out = "Последние заметки. " + " ".join(
            re.sub(r"^\-\s*(\(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\)\s*)?", "", ln).strip()
            for ln in recent
        )
        return SkillResult(text=text_out, speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 14. WIKI — короткое описание из RU Wikipedia
# ───────────────────────────────────────────────────────────────────────

class WikiSkill(KeywordSkill):
    name = "wiki"
    keywords = [
        r"\bчто\s+так(ое|ая|ой)\s+\S",
        r"\bкто\s+так(ой|ая)\s+\S",
        r"\bвикипедия\b",
        r"\bрасскажи\s+(про|о)\s+\S",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        m = re.search(r"\b(?:такое|такой|такая|про|о)\s+(.+)$", text, flags=re.IGNORECASE)
        if not m:
            return SkillResult(text="Босс, о чём рассказать?", speakable=True)
        query = m.group(1).strip().rstrip("?.!")
        url = f"https://ru.wikipedia.org/api/rest_v1/page/summary/{quote(query)}"
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code == 404:
                    return SkillResult(text=f"В Википедии нет статьи про {query}.", speakable=True)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.error("wiki_error", error=str(e))
            return SkillResult(text="Википедия сейчас недоступна.", speakable=True)
        extract = (data.get("extract") or "").strip()
        if not extract:
            return SkillResult(text="Не нашёл описания.", speakable=True)
        # обрезаем до 2 предложений
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        short = " ".join(sentences[:2])
        return SkillResult(text=short, speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 15. NEWS — топ-3 новости через NewsAPI (если есть ключ)
# ───────────────────────────────────────────────────────────────────────

class NewsSkill(KeywordSkill):
    name = "news"
    keywords = [
        r"\bновости\b",
        r"\bчто\s+нового\b",
        r"\bпоследние\s+(новости|события)\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        key = settings.newsapi_key
        if not key or not key.get_secret_value():
            return SkillResult(
                text="Босс, ключ NewsAPI не настроен — добавь в .env NEWSAPI_KEY.",
                speakable=True,
            )
        url = "https://newsapi.org/v2/top-headlines"
        params = {
            "country": "ru",
            "pageSize": 3,
            "apiKey": key.get_secret_value(),
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.error("news_error", error=str(e))
            return SkillResult(text="Босс, новости сегодня недоступны.", speakable=True)
        articles = data.get("articles", [])
        if not articles:
            return SkillResult(text="Свежих заголовков не нашлось.", speakable=True)
        titles = [a.get("title", "").split(" - ")[0] for a in articles[:3]]
        return SkillResult(
            text="Топ-новости. " + ". ".join(t.rstrip(".") for t in titles) + ".",
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# 16. CALC — простой калькулятор
# ───────────────────────────────────────────────────────────────────────

_SAFE_OPS = {
    ast_op: fn for ast_op, fn in [
        ("Add", operator.add), ("Sub", operator.sub),
        ("Mult", operator.mul), ("Div", operator.truediv),
        ("Mod", operator.mod), ("Pow", operator.pow),
        ("USub", operator.neg), ("FloorDiv", operator.floordiv),
    ]
}


def _safe_eval(expr: str) -> float:
    """Безопасное вычисление арифм. выражения без eval()."""
    import ast as _ast
    node = _ast.parse(expr, mode="eval").body
    return _walk(node)


def _walk(node) -> float:
    import ast as _ast
    if isinstance(node, _ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("not a number")
    if isinstance(node, _ast.BinOp):
        op_name = type(node.op).__name__
        if op_name not in _SAFE_OPS:
            raise ValueError(f"op {op_name} not allowed")
        return _SAFE_OPS[op_name](_walk(node.left), _walk(node.right))
    if isinstance(node, _ast.UnaryOp):
        op_name = type(node.op).__name__
        if op_name not in _SAFE_OPS:
            raise ValueError(f"op {op_name} not allowed")
        return _SAFE_OPS[op_name](_walk(node.operand))
    raise ValueError(f"node {type(node).__name__} not allowed")


class CalcSkill(KeywordSkill):
    name = "calc"
    keywords = [
        r"\bсколько\s+будет\s+\d",
        r"\bпосчитай\b",
        r"\bвычисли\b",
        r"\d+\s*[\+\-\*\/]\s*\d+",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        # переводим слова в операторы
        t = text.lower()
        t = re.sub(r"\bплюс\b", "+", t)
        t = re.sub(r"\bминус\b", "-", t)
        # умножение: ловим целые фразы 'умножить на', 'умножь на', чтобы 'на' не превратился в лишний *
        t = re.sub(r"\b(?:умножить|умножь|умноженное)\s+на\b", " * ", t)
        t = re.sub(r"\b(?:умножить|умножь)\b", " * ", t)
        # деление: 'разделить на', 'делить на', 'поделить на', 'поделённое на'
        t = re.sub(r"\b(?:разделить|поделить|поделённое|делить)\s+на\b", " / ", t)
        # запятая → точка
        t = t.replace(",", ".")
        # вычленяем "число оп число оп число..."
        m = re.search(r"([\d\.]+\s*[\+\-\*\/]\s*[\d\.\s\+\-\*\/]+)", t)
        if not m:
            return SkillResult(text="Босс, не разобрал выражение.", speakable=True)
        expr = re.sub(r"\s+", "", m.group(1)).rstrip(".+-*/")
        try:
            result = _safe_eval(expr)
        except Exception:
            return SkillResult(text="Не смог посчитать.", speakable=True)
        if result == int(result):
            return SkillResult(text=f"Будет {int(result)}.", speakable=True)
        return SkillResult(text=f"Будет {result:.4g}.", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 17. CONVERT — конвертация единиц
# ───────────────────────────────────────────────────────────────────────

_CONVERSIONS: Final = [
    # (pattern, factor, from_unit, to_unit, formatter)
    (r"(\d+(?:[\.\,]\d+)?)\s*(?:км|километр(?:ов|а|)?)\s+в\s+мил", 0.621371, "км", "миль"),
    (r"(\d+(?:[\.\,]\d+)?)\s*мил(?:ь|и|ей|я)\s+в\s+(?:км|километр)", 1.60934, "миль", "км"),
    (r"(\d+(?:[\.\,]\d+)?)\s*(?:кг|килограм(?:м|ма|мов)?)\s+в\s+фунт", 2.20462, "кг", "фунтов"),
    (r"(\d+(?:[\.\,]\d+)?)\s*фунт(?:ов|а|)?\s+в\s+(?:кг|килограм)", 0.453592, "фунтов", "кг"),
    (r"(\-?\d+(?:[\.\,]\d+)?)\s*(?:°c|цельси)\s+в\s+(?:фаренгейт|°f)", None, "°C", "°F"),
    (r"(\-?\d+(?:[\.\,]\d+)?)\s*(?:°f|фаренгейт)\s+в\s+(?:°c|цельси)", None, "°F", "°C"),
]


class ConvertSkill(KeywordSkill):
    name = "convert"
    keywords = [
        # Строгие unit-патерны: число + единица + "в" + единица
        r"\b\d+(?:[\.\,]\d+)?\s*(?:км|километр|мил|миль|кг|килограм|фунт|°c|°f|цельси|фаренгейт)\b",
        r"\bконвертируй\b",
        r"\bпереведи\s+\d",  # "переведи 100 км в мили"
    ]

    def match(self, text: str) -> float:
        # Уступаем TranslateSkill: если есть "на <язык>" — это перевод
        for lang_word in (
            "английский", "русский", "французский", "немецкий", "испанский",
            "итальянский", "китайский", "японский", "турецкий", "арабский",
            "англ", "русс",
        ):
            if re.search(rf"\bна\s+{lang_word}\b", text, flags=re.IGNORECASE):
                return 0.0
        return super().match(text)

    async def run(self, text: str, request_id: str) -> SkillResult:
        t = text.lower()
        for pat, factor, from_u, to_u in _CONVERSIONS:
            m = re.search(pat, t, flags=re.IGNORECASE)
            if not m:
                continue
            value = float(m.group(1).replace(",", "."))
            if factor is None:
                # температура
                if from_u == "°C":
                    out = value * 9 / 5 + 32
                else:
                    out = (value - 32) * 5 / 9
            else:
                out = value * factor
            return SkillResult(
                text=f"{value:g} {from_u} = {out:.2f} {to_u}.",
                speakable=True,
            )
        return SkillResult(text="Босс, не понял конвертацию. Скажи 'X км в мили' или 'X кг в фунты'.", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 18. RANDOM — случайное число / монетка / выбор
# ───────────────────────────────────────────────────────────────────────

class RandomSkill(KeywordSkill):
    name = "random"
    keywords = [
        r"\bподбрось\s+монет",
        r"\bорёл\s+или\s+решк",
        r"\bслучайн(ое|ая|ый)\s+(число|выбор)",
        r"\bкинь\s+(куби|монет)",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        t = text.lower()
        if "монет" in t or "орёл" in t or "решк" in t:
            return SkillResult(text=_random.choice(["Орёл.", "Решка."]), speakable=True)
        if "куби" in t:
            return SkillResult(text=f"Выпало {_random.randint(1, 6)}.", speakable=True)
        # случайное число от 1 до 100 по умолчанию
        m = re.search(r"от\s+(\d+)\s+до\s+(\d+)", t)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        else:
            lo, hi = 1, 100
        return SkillResult(text=f"{_random.randint(lo, hi)}.", speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 19. TRANSLATE — перевод через бесплатный MyMemory API
# ───────────────────────────────────────────────────────────────────────

class TranslateSkill(KeywordSkill):
    name = "translate"
    keywords = [
        r"\bпереведи\s+на\s+(английский|русский|французский|немецкий|испанский|итальянский|китайский|японский|турецкий|арабский|англ|русс)",
        r"\bпереведи\s+с\s+\S+",
        r"\bкак\s+(будет|сказать|по|на)\s+.*\s+на\s+(английский|русский|французский|немецкий|испанский|итальянский|китайский|японский|турецкий|арабский)",
        r"\bна\s+(английский|русский|французский|немецкий|испанский|итальянский|китайский|японский|турецкий|арабский)\b.*\bпереведи\b",
    ]

    LANG_MAP = {
        "английский": "en", "англ": "en", "инглиш": "en",
        "русский": "ru", "русс": "ru",
        "французский": "fr", "немецкий": "de", "испанский": "es",
        "итальянский": "it", "китайский": "zh", "японский": "ja",
        "турецкий": "tr", "арабский": "ar",
    }

    async def run(self, text: str, request_id: str) -> SkillResult:
        # «переведи на английский <текст>» / «переведи <текст> на английский»
        t = text
        target = "en"
        for ru, code in self.LANG_MAP.items():
            if re.search(rf"\bна\s+{ru}\b", t, flags=re.IGNORECASE):
                target = code
                t = re.sub(rf"\bна\s+{ru}\b", "", t, flags=re.IGNORECASE)
                break
        # вычленяем сам текст для перевода (после "переведи")
        m = re.search(r"\bпереведи\b\s*(.+)$", t, flags=re.IGNORECASE)
        phrase = (m.group(1).strip() if m else t).strip(' "“”«»')
        if not phrase:
            return SkillResult(text="Босс, что переводить?", speakable=True)
        source = "ru" if target != "ru" else "en"
        url = "https://api.mymemory.translated.net/get"
        params = {"q": phrase, "langpair": f"{source}|{target}"}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            translated = data["responseData"]["translatedText"]
        except Exception as e:
            logger.error("translate_error", error=str(e))
            return SkillResult(text="Сервис перевода недоступен.", speakable=True)
        return SkillResult(text=translated, speakable=True)


# ───────────────────────────────────────────────────────────────────────
# 20. STATUS — статус Джарвиса
# ───────────────────────────────────────────────────────────────────────

class StatusSkill(KeywordSkill):
    name = "status"
    keywords = [
        r"\bстатус\b",
        r"\bкак\s+дела\s+джарвис",
        r"\bвсё\s+работает\b",
        r"\bдоложи\s+(статус|состояние)\b",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        active_timers = len(_active_timers)
        try:
            from core.event_bus import bus
            handlers = sum(len(v) for v in bus._handlers.values())  # type: ignore[attr-defined]
        except Exception:
            handlers = 0
        return SkillResult(
            text=(
                f"Все системы в норме. Активных таймеров: {active_timers}. "
                f"Подписчиков на шине: {handlers}. Память тёплая, каналы на связи."
            ),
            speakable=True,
        )


# ───────────────────────────────────────────────────────────────────────
# Регистрация всех 20 в Router
# ───────────────────────────────────────────────────────────────────────

def register_all_builtin(
    router: Router,
    memory: MemoryManager,
    claude=None,  # ClaudeProvider — для live brief, opt
    smart_provider=None,  # SmartProvider — для LLMSwitcherSkill, opt
) -> None:
    """Подключить все встроенные скиллы в router.

    `smart_provider` нужен LLMSwitcherSkill, чтобы Босс мог голосом переключать
    primary LLM ("пользуйся Дипсиком"). Если не передан — skill регистрируется
    только когда provider есть, иначе пропускается (status-query вернёт ошибку).
    """
    # Workspace для skill-storage (todo.json, reminders.json и т.п.).
    # MemoryManager уже знает workspace dir; используем его.
    workspace_dir = memory.workspace
    todo_store = TodoStore(workspace_dir / "todo.json")
    reminders_store = RemindersStore(workspace_dir / "reminders.json")
    goals_store = GoalsStore(workspace_dir / "goals.json")

    skills = [
        TimeSkill(),
        DateSkill(),
        TimezoneSkill(),
        TimerSkill(),
        AlarmSkill(),
        WeatherSkill(),
        WeatherForecastSkill(),
        CurrencySkill(),
        CryptoSkill(),
        NoteSkill(memory),
        RememberSkill(memory),
        ForgetSkill(),
        NotesListSkill(memory),
        WikiSkill(),
        NewsRuSkill(),  # старый NewsSkill (NewsAPI) заменён — NewsAPI заблочен в РФ
        CalcSkill(),
        ConvertSkill(),
        RandomSkill(),
        TranslateSkill(),
        StatusSkill(),
        BriefingSkill(memory, claude=claude, todo_store=todo_store, reminders_store=reminders_store),
        RecallSkill(memory),
        CalendarSkill(),
        GeoSkill(),
        MusicSkill(),
        TodoSkill(todo_store),
        RemindersSkill(reminders_store),
        WebSearchSkill(),
        ReadAloudSkill(),
        AppControlSkill(),
        SystemInfoSkill(),
        ClipboardSkill(),
        VolumeSkill(),
        GitHubSkill(),
        GoalsSkill(goals_store),
        CodeAssistSkill(),
        FileSkill(),
        YandexSmartHomeSkill(),
    ]
    # LLM switcher — только если у нас есть ссылка на SmartProvider.
    # Иначе skill бесполезен (некому set_choice() звать).
    if smart_provider is not None:
        skills.append(LLMSwitcherSkill(smart_provider))
    for s in skills:
        router.register_skill(s)
    logger.info("builtin_skills_registered", count=len(skills))

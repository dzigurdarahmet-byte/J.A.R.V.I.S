"""Генераторы утренней и вечерней сводки.

Используют существующие скиллы напрямую (WeatherSkill, CurrencySkill, CryptoSkill)
плюс MemoryManager для заметок. Не зависят от Router — это автономный модуль.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.logging import get_logger
from core.memory import MemoryManager

logger = get_logger(__name__)


def _R():
    """Lazy access to skills registry — избегаем circular import при
    загрузке core.briefings из briefing_skill (который сам из registry)."""
    from core.skills import registry as R
    return R


DAYS_RU_FULL = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
MONTHS_RU_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _today_human() -> str:
    now = datetime.now()
    return f"{DAYS_RU_FULL[now.weekday()].capitalize()}, {now.day} {MONTHS_RU_GEN[now.month - 1]}"


async def _safe(coro) -> str | None:
    """Запустить coroutine скилла, вернуть .text или None при ошибке."""
    try:
        res = await coro
        return res.text if res else None
    except Exception as e:
        logger.warning("briefing_skill_failed", error=str(e))
        return None


def _list_recent_notes(memory: MemoryManager, limit: int = 3) -> list[str]:
    """Достать последние N заметок из MEMORY.md."""
    try:
        mem = memory.load_memory_md()
    except Exception:
        return []
    m = re.search(r"##\s+Заметки\s*\n([\s\S]+?)(?=\n##\s+|\Z)", mem, flags=re.IGNORECASE)
    if not m:
        return []
    raw = m.group(1).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("-")]
    # убираем timestamp '(YYYY-MM-DD HH:MM)' в начале
    clean: list[str] = []
    for ln in lines[-limit:]:
        ln = re.sub(r"^-\s*(?:\(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\)\s*)?", "", ln).strip()
        if ln:
            clean.append(ln)
    return clean


def _default_city() -> str:
    """Город из settings.jarvis_default_city (Сыктывкар по дефолту)."""
    try:
        from core.config import settings
        return settings.jarvis_default_city or "Сыктывкар"
    except Exception:
        return "Сыктывкар"


async def morning_brief(
    memory: MemoryManager,
    city: str | None = None,
    name: str = "Босс",
) -> str:
    city = city or _default_city()
    """Утренняя сводка — формат для Telegram (markdown допустим)."""
    parts: list[str] = [f"☀️ *Доброе утро, {name}.*  ", f"_{_today_human()}_  ", ""]

    # Погода сейчас + прогноз
    cur_text = await _safe(_R().WeatherSkill().run(f"погода в {city}", request_id="brief"))
    if cur_text:
        parts.append(f"🌤 {cur_text}")
    fc_text = await _safe(_R().WeatherForecastSkill().run(f"прогноз погоды в {city}", request_id="brief"))
    if fc_text:
        # обрежем «Прогноз погоды в …» — оставим основную часть
        fc_text = re.sub(r"^Прогноз\s+погоды\s+в\s+\S+\.\s*", "", fc_text)
        parts.append(f"📅 {fc_text}")

    # Курсы валют
    cur = await _safe(_R().CurrencySkill().run("курс", request_id="brief"))
    if cur:
        parts.append(f"💱 {cur}")

    # Крипта
    cr = await _safe(_R().CryptoSkill().run("курс биткоина", request_id="brief"))
    if cr:
        parts.append(f"₿ {cr}")

    # Заметки
    notes = _list_recent_notes(memory, limit=3)
    if notes:
        parts.append("")
        parts.append("📝 *Открытые заметки:*")
        for n in notes:
            parts.append(f"  • {n}")

    parts.append("")
    parts.append("_Хорошего дня._")
    return "\n".join(parts)


async def evening_brief(
    memory: MemoryManager,
    city: str | None = None,
    name: str = "Босс",
) -> str:
    city = city or _default_city()
    """Вечерняя сводка — короткий итог дня."""
    parts: list[str] = [f"🌙 *Подвожу день, {name}.*  ", f"_{_today_human()}_  ", ""]

    # Погода на завтра
    fc_text = await _safe(_R().WeatherForecastSkill().run(f"прогноз погоды в {city}", request_id="brief"))
    if fc_text:
        # forecast возвращает 3 дня (сегодня, завтра, послезавтра). Берём индекс 1 — завтра.
        fc_text = re.sub(r"^Прогноз\s+погоды\s+в\s+\S+\.\s*", "", fc_text)
        days = [d.strip() for d in fc_text.split(". ") if d.strip()]
        tomorrow = days[1] if len(days) > 1 else (days[0] if days else None)
        if tomorrow:
            parts.append(f"🌤 На завтра: {tomorrow.rstrip('.')}.")

    # Финансы — закрытие
    cur = await _safe(_R().CurrencySkill().run("курс", request_id="brief"))
    if cur:
        parts.append(f"💱 {cur}")

    # Метрики дня (минимум — количество сообщений из daily-лога)
    try:
        recent = memory.load_recent_context(limit_messages=200)
        user_msgs = sum(1 for m in recent if m.role == "user")
        if user_msgs > 0:
            n = user_msgs
            last = n % 100
            if 11 <= last <= 14:
                word = "запросов"
            else:
                last1 = n % 10
                if last1 == 1:
                    word = "запрос"
                elif 2 <= last1 <= 4:
                    word = "запроса"
                else:
                    word = "запросов"
            parts.append(f"💬 За день — {n} {word} к Джарвису")
    except Exception:
        pass

    parts.append("")
    parts.append("_Спокойной ночи._")
    return "\n".join(parts)


# ── Helpers для use as L1 skill ───────────────────────────────────────


async def brief_now(memory: MemoryManager, kind: str = "auto", city: str | None = None) -> str:
    city = city or _default_city()
    """On-demand брифинг — вызывается через скилл «дай брифинг»."""
    if kind == "morning":
        return await morning_brief(memory, city=city)
    if kind == "evening":
        return await evening_brief(memory, city=city)
    # auto: до 16:00 утренний, после — вечерний
    hour = datetime.now().hour
    if hour < 16:
        return await morning_brief(memory, city=city)
    return await evening_brief(memory, city=city)


# ─── Live JARVIS-style брифинг (E1) ───────────────────────────────────
async def live_morning_brief(
    memory: MemoryManager,
    claude,  # ClaudeProvider — типизация через core.providers
    *,
    city: str | None = None,
    todo_store: Any = None,
    reminders_store: Any = None,
) -> str:
    city = city or _default_city()
    """Утренний брифинг, собранный из source'ов и переданный Claude
    для свободной формулировки в стиле JARVIS.

    Собирает: погода (сейчас + прогноз) · валюта · крипта · ближайшие события
    в календаре · активные задачи · активные напоминания на сегодня · открытые
    PRs/issues в GitHub. Затем просит Claude собрать связный брифинг.
    Все факты сохраняются дословно (числа, имена).
    """
    sources: dict[str, str] = {}

    # 1. Погода
    cur = await _safe(_R().WeatherSkill().run(f"погода в {city}", request_id="brief"))
    if cur:
        sources["weather_now"] = cur
    fc = await _safe(_R().WeatherForecastSkill().run(f"прогноз погоды в {city}", request_id="brief"))
    if fc:
        sources["weather_forecast"] = re.sub(r"^Прогноз\s+погоды\s+в\s+\S+\.\s*", "", fc)

    # 2. Финансы
    cur_text = await _safe(_R().CurrencySkill().run("курс", request_id="brief"))
    if cur_text:
        sources["currency"] = cur_text
    crypto = await _safe(_R().CryptoSkill().run("курс биткоина", request_id="brief"))
    if crypto:
        sources["crypto"] = crypto

    # 3. Календарь
    try:
        cal = await _R().CalendarSkill().run("какие у меня события сегодня", request_id="brief")
        if cal and cal.text:
            sources["calendar"] = cal.text
    except Exception as e:
        logger.warning("briefing_calendar_failed", error=str(e))

    # 4. Todo
    if todo_store is not None:
        try:
            items = todo_store.list_active()
            if items:
                sources["todo"] = "\n".join(f"{i['id']}. {i['text']}" for i in items[:10])
        except Exception as e:
            logger.warning("briefing_todo_failed", error=str(e))

    # 5. Reminders на сегодня
    if reminders_store is not None:
        try:
            today_end = datetime.now().replace(hour=23, minute=59, second=59)
            items = reminders_store.list_active()
            todays = []
            for it in items:
                try:
                    at = datetime.fromisoformat(it["at_iso"])
                    if at <= today_end:
                        todays.append(f"{at.strftime('%H:%M')} — {it['text']}")
                except Exception:
                    pass
            if todays:
                sources["reminders_today"] = "\n".join(todays)
        except Exception as e:
            logger.warning("briefing_reminders_failed", error=str(e))

    # 6. GitHub — открытые PRs
    try:
        from core.skills.github_skill import GitHubSkill
        gh = GitHubSkill()
        if gh._token:  # type: ignore[attr-defined]
            pr = await gh.run("какие у меня PR", request_id="brief")
            if pr and pr.text and "нет" not in pr.text.lower()[:50]:
                sources["github_prs"] = pr.text
    except Exception as e:
        logger.warning("briefing_github_failed", error=str(e))

    # 7. Заметки за последнюю неделю
    notes = _list_recent_notes(memory, limit=3)
    if notes:
        sources["notes"] = "\n".join(f"- {n}" for n in notes)

    # ── Сборка через Claude в стиле JARVIS ─────────────────────────
    if not sources:
        return f"Доброе утро, Босс. {_today_human()}. Источники сейчас молчат — день начнём с чистого листа."

    source_block = "\n\n".join(f"[{k.upper()}]\n{v}" for k, v in sources.items())

    system = (
        "Ты — J.A._R().V.I.S., персональный ассистент Босса (Marvel-стиль: "
        "уважительный, лаконичный, остроумный). Обращайся «Босс». "
        "Никогда «вы»/«сэр»/«господин»."
    )
    instruction = f"""Утро Босса. Собери для него связный брифинг на основе данных ниже.

Правила:
1. Начни с приветствия в твоём стиле + сегодняшняя дата ({_today_human()}).
2. Структура — 3–5 коротких параграфов, каждый с маленьким акцентом ("Погода так-то", "Финансы:", "По плану:").
3. ВСЕ цифры, имена, ID, проценты, температуры — оставь дословно из источников.
4. Сначала важное (события сегодня, напоминания), потом фоновое (погода, валюта).
5. Если есть PR'ы / открытые задачи — обозначь, не нагнетая. Можно лёгкая ирония если уместно.
6. Закончи короткой строкой-настроением (без банальностей типа «хорошего дня»).
7. Без markdown, без эмодзи, без bullet-маркеров — только живая речь параграфами.
8. Длина: до 200 слов.

Данные:
{source_block}

Брифинг:"""

    try:
        from core.providers import Message
        text = await claude.chat(
            messages=[Message(role="user", content=instruction)],
            system=system,
        )
        return (text or "").strip() or "Брифинг собрать не получилось, Босс."
    except Exception as e:
        logger.error("briefing_claude_failed", error=str(e))
        # fallback — обычный шаблонный брифинг
        return await morning_brief(memory, city=city)

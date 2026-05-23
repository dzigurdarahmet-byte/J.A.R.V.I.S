"""Tool-registry: схемы Anthropic + runner-функции для L2 (tool-use в Claude).

Отдельно от registry.py чтобы не разрывать большой файл. Schemas описывают
функции в формате Anthropic tool-use, runner'ы получают args dict и
возвращают str — текст результата, который пойдёт обратно Claude'у.

Зачем не методы класса BaseSkill: правка большого registry.py через
кириллический путь часто ломает кодировку — выносим вспомогательное
в чистый ASCII-friendly файл.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from core.logging import get_logger
from core.memory import MemoryManager
from core.skills import registry as R

logger = get_logger(__name__)

# Тип: (args_dict, memory) -> awaitable[str]
ToolFn = Callable[[dict[str, Any], MemoryManager], Awaitable[str]]


# ──────────────────────────────────────────────────────────────────────
# Tool schemas (Anthropic format)
# ──────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_time",
        "description": "Получить текущее локальное время (Москва, UTC+3). Используй когда Босс спрашивает «который час», «сколько время», «текущее время».",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_date",
        "description": "Получить текущую дату и день недели по локальному календарю.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_time_in_city",
        "description": "Получить текущее время в указанном городе.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Город (Москва, Нью-Йорк, Токио, Лондон, Сыктывкар и др.) в именительном падеже.",
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_weather",
        "description": "Текущая погода в указанном городе: температура, ощущается как, описание, ветер. Используй когда Босс спрашивает про погоду, как одеться, тепло/холодно.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Город в именительном падеже (Москва, Сыктывкар, Лондон…). По умолчанию Москва.",
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_weather_forecast",
        "description": "Прогноз погоды на ближайшие 3 дня в указанном городе.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Город в именительном падеже."},
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_currency_rates",
        "description": "Курсы валют по ЦБ РФ: USD, EUR, CNY к рублю.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_crypto_rates",
        "description": "Курсы Bitcoin и Ethereum к доллару США (через CoinGecko).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calculate",
        "description": "Безопасный калькулятор: + - * / ** %. Принимает математическое выражение строкой.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Например '17 + 25', '100 * 1.05', '2 ** 10'."},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "convert_units",
        "description": "Конвертация единиц: км↔мили, кг↔фунты, °C↔°F.",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {
                    "type": "string",
                    "enum": ["km", "mi", "kg", "lb", "C", "F"],
                },
                "to_unit": {
                    "type": "string",
                    "enum": ["km", "mi", "kg", "lb", "C", "F"],
                },
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
    {
        "name": "wiki_lookup",
        "description": "Краткая выжимка по теме из русской Википедии (2 предложения).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Запрос для поиска (имя, термин, событие)."}},
            "required": ["query"],
        },
    },
    {
        "name": "translate",
        "description": "Перевод текста на указанный язык через MyMemory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target_lang": {
                    "type": "string",
                    "description": "ISO-код языка: en, ru, fr, de, es, it, zh, ja, tr, ar.",
                },
            },
            "required": ["text", "target_lang"],
        },
    },
    {
        "name": "set_timer",
        "description": "Поставить таймер на N секунд/минут. Когда сработает — Босс получит уведомление.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "Длительность в секундах."},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "set_alarm",
        "description": "Поставить будильник на HH:MM. Если время уже прошло сегодня — на завтра.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
            },
            "required": ["hour", "minute"],
        },
    },
    {
        "name": "remember_fact",
        "description": "Сохранить факт/заметку в долговременной памяти Босса (MEMORY.md). Используй когда Босс просит «запомни», «запиши», «не забудь».",
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "Текст для сохранения."},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "list_notes",
        "description": "Показать последние заметки Босса из MEMORY.md.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "random_choice",
        "description": "Случайный выбор: монетка (орёл/решка), кубик 1-6, случайное число от lo до hi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["coin", "die", "number"]},
                "lo": {"type": "integer", "default": 1},
                "hi": {"type": "integer", "default": 100},
            },
            "required": ["kind"],
        },
    },
    # ── NEW: todo / reminders / web / github / cal / system / clipboard ──
    {
        "name": "todo_add",
        "description": "Добавить задачу в личный список Босса. Используй когда Босс говорит «запомни задачу», «не забудь сделать», «нужно сделать».",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст задачи в инфинитиве."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "todo_list",
        "description": "Показать активные (не выполненные) задачи Босса.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "todo_done",
        "description": "Отметить задачу как выполненную по её ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "ID задачи."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "reminder_add",
        "description": "Создать напоминание на будущее. Сработает через bus и придёт Боссу как сообщение. Используй для «напомни через X», «напомни в HH:MM», «завтра в HH разбуди».",
        "input_schema": {
            "type": "object",
            "properties": {
                "when_text": {
                    "type": "string",
                    "description": "Когда напомнить, в естественном виде: 'через 30 минут', 'в 15:30', 'завтра в 9 утра'.",
                },
                "text": {
                    "type": "string",
                    "description": "О чём напомнить (короткое описание).",
                },
            },
            "required": ["when_text", "text"],
        },
    },
    {
        "name": "reminder_list",
        "description": "Показать все активные (несработавшие) напоминания.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "web_search",
        "description": "Поиск в интернете (Yandex.XML если есть ключ, иначе DuckDuckGo). Возвращает топ-3 результата с заголовком, URL и snippet. Используй когда нужна свежая информация которой Claude не знает (новости, текущие события, цены).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "github_my_prs",
        "description": "Список открытых pull request'ов Босса на GitHub во всех репозиториях.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "github_repo_commits",
        "description": "Последние 5 коммитов в указанном репозитории Босса.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Имя репо (короткое — пред подставится логин Босса; либо полное owner/name).",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "calendar_today",
        "description": "События в календаре Босса на сегодня. Google Calendar.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "system_info",
        "description": "Состояние компьютера Босса: cpu загрузка, ram свободно, disk место, battery (если ноутбук).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["cpu", "ram", "disk", "battery", "all"],
                    "description": "Что показать.",
                },
            },
            "required": ["kind"],
        },
    },
    {
        "name": "clipboard_read",
        "description": "Прочитать что сейчас в буфере обмена Windows.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "clipboard_write",
        "description": "Положить текст в буфер обмена Windows (Босс потом вставит куда нужно).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Что положить в буфер."},
            },
            "required": ["text"],
        },
    },
]


# ──────────────────────────────────────────────────────────────────────
# Tool runners
# ──────────────────────────────────────────────────────────────────────

async def _run_time(args: dict, memory: MemoryManager) -> str:
    skill = R.TimeSkill()
    res = await skill.run("который час", request_id="tool")
    return res.text


async def _run_date(args: dict, memory: MemoryManager) -> str:
    skill = R.DateSkill()
    res = await skill.run("какая дата", request_id="tool")
    return res.text


async def _run_time_in_city(args: dict, memory: MemoryManager) -> str:
    city = (args.get("city") or "москва").strip()
    skill = R.TimezoneSkill()
    # передаём через `в <city>` чтобы попасть в существующий парсер
    res = await skill.run(f"который час в {city}", request_id="tool")
    return res.text


async def _run_weather(args: dict, memory: MemoryManager) -> str:
    from core.config import settings as _settings
    city = (args.get("city") or _settings.jarvis_default_city).strip()
    skill = R.WeatherSkill()
    res = await skill.run(f"погода в {city}", request_id="tool")
    return res.text


async def _run_forecast(args: dict, memory: MemoryManager) -> str:
    from core.config import settings as _settings
    city = (args.get("city") or _settings.jarvis_default_city).strip()
    skill = R.WeatherForecastSkill()
    res = await skill.run(f"прогноз погоды в {city}", request_id="tool")
    return res.text


async def _run_currency(args: dict, memory: MemoryManager) -> str:
    skill = R.CurrencySkill()
    res = await skill.run("курс доллара", request_id="tool")
    return res.text


async def _run_crypto(args: dict, memory: MemoryManager) -> str:
    skill = R.CryptoSkill()
    res = await skill.run("курс биткоина", request_id="tool")
    return res.text


async def _run_calc(args: dict, memory: MemoryManager) -> str:
    expr = str(args.get("expression") or "")
    skill = R.CalcSkill()
    # CalcSkill парсит текст; даём ему явное выражение
    res = await skill.run(f"посчитай {expr}", request_id="tool")
    return res.text


async def _run_convert(args: dict, memory: MemoryManager) -> str:
    value = args.get("value")
    fu = (args.get("from_unit") or "").lower()
    tu = (args.get("to_unit") or "").lower()
    # маппинг кодов в русские триггеры существующего скилла
    map_unit = {"km": "км", "mi": "мил", "kg": "кг", "lb": "фунт", "c": "°c", "f": "°f"}
    f_ru = map_unit.get(fu, fu)
    t_ru = map_unit.get(tu, tu)
    skill = R.ConvertSkill()
    res = await skill.run(f"{value} {f_ru} в {t_ru}", request_id="tool")
    return res.text


async def _run_wiki(args: dict, memory: MemoryManager) -> str:
    query = (args.get("query") or "").strip()
    skill = R.WikiSkill()
    res = await skill.run(f"расскажи про {query}", request_id="tool")
    return res.text


async def _run_translate(args: dict, memory: MemoryManager) -> str:
    text = args.get("text") or ""
    target = (args.get("target_lang") or "en").lower()
    # обратное соответствие LANG_MAP в TranslateSkill — даём фразу с языком
    lang_ru = {
        "en": "английский", "ru": "русский", "fr": "французский",
        "de": "немецкий", "es": "испанский", "it": "итальянский",
        "zh": "китайский", "ja": "японский", "tr": "турецкий", "ar": "арабский",
    }.get(target, "английский")
    skill = R.TranslateSkill()
    res = await skill.run(f"переведи на {lang_ru}: {text}", request_id="tool")
    return res.text


async def _run_timer(args: dict, memory: MemoryManager) -> str:
    secs = int(args.get("seconds") or 60)
    skill = R.TimerSkill()
    res = await skill.run(f"поставь таймер на {secs} секунд", request_id="tool")
    return res.text


async def _run_alarm(args: dict, memory: MemoryManager) -> str:
    h = int(args.get("hour") or 7)
    m = int(args.get("minute") or 0)
    skill = R.AlarmSkill()
    res = await skill.run(f"будильник на {h}:{m:02d}", request_id="tool")
    return res.text


async def _run_remember(args: dict, memory: MemoryManager) -> str:
    fact = (args.get("fact") or "").strip()
    if not fact:
        return "Босс, нечего запоминать."
    memory.remember_fact(fact)
    return "Запомнил."


async def _run_list_notes(args: dict, memory: MemoryManager) -> str:
    skill = R.NotesListSkill(memory)
    res = await skill.run("покажи заметки", request_id="tool")
    return res.text


async def _run_random(args: dict, memory: MemoryManager) -> str:
    kind = (args.get("kind") or "coin").lower()
    if kind == "coin":
        prompt = "подбрось монетку"
    elif kind == "die":
        prompt = "кинь кубик"
    else:
        lo = int(args.get("lo") or 1)
        hi = int(args.get("hi") or 100)
        prompt = f"случайное число от {lo} до {hi}"
    skill = R.RandomSkill()
    res = await skill.run(prompt, request_id="tool")
    return res.text


# ── NEW runners (closures over stores передаются через make_tool_runner) ──

async def _run_web_search(args: dict, memory: MemoryManager) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "Не указан query."
    from core.skills.web_search_skill import WebSearchSkill
    skill = WebSearchSkill()
    res = await skill.run(f"найди в интернете {query}", request_id="tool")
    return res.text


async def _run_github_my_prs(args: dict, memory: MemoryManager) -> str:
    from core.skills.github_skill import GitHubSkill
    skill = GitHubSkill()
    res = await skill.run("какие у меня PR", request_id="tool")
    return res.text


async def _run_github_repo_commits(args: dict, memory: MemoryManager) -> str:
    repo = (args.get("repo") or "").strip()
    if not repo:
        return "Не указан repo."
    from core.skills.github_skill import GitHubSkill
    skill = GitHubSkill()
    res = await skill.run(f"последние коммиты в {repo}", request_id="tool")
    return res.text


async def _run_calendar_today(args: dict, memory: MemoryManager) -> str:
    skill = R.CalendarSkill()
    res = await skill.run("какие у меня события сегодня", request_id="tool")
    return res.text


async def _run_system_info(args: dict, memory: MemoryManager) -> str:
    kind = (args.get("kind") or "all").lower()
    prompts = {
        "cpu": "загрузка процессора",
        "ram": "сколько свободно памяти",
        "disk": "сколько места на диске",
        "battery": "батарея",
        "all": "статус компьютера",
    }
    from core.skills.system_info_skill import SystemInfoSkill
    skill = SystemInfoSkill()
    res = await skill.run(prompts.get(kind, prompts["all"]), request_id="tool")
    return res.text


async def _run_clipboard_read(args: dict, memory: MemoryManager) -> str:
    from core.skills.clipboard_skill import ClipboardSkill
    skill = ClipboardSkill()
    res = await skill.run("что в буфере", request_id="tool")
    return res.text


async def _run_clipboard_write(args: dict, memory: MemoryManager) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return "Нечего класть в буфер."
    from core.skills.clipboard_skill import ClipboardSkill
    skill = ClipboardSkill()
    res = await skill.run(f"положи в буфер: {text}", request_id="tool")
    return res.text


# todo / reminders требуют store — собираются в make_tool_runner через closure
def _make_todo_runners(todo_store):
    from core.skills.todo_skill import TodoSkill

    async def add(args, memory):
        text = (args.get("text") or "").strip()
        if not text:
            return "Нет текста задачи."
        skill = TodoSkill(todo_store)
        res = await skill.run(f"добавь задачу {text}", request_id="tool")
        return res.text

    async def lst(args, memory):
        skill = TodoSkill(todo_store)
        res = await skill.run("покажи задачи", request_id="tool")
        return res.text

    async def done(args, memory):
        try:
            idn = int(args.get("id") or 0)
        except (ValueError, TypeError):
            return "Невалидный id."
        skill = TodoSkill(todo_store)
        res = await skill.run(f"выполнил #{idn}", request_id="tool")
        return res.text

    return add, lst, done


def _make_reminder_runners(reminders_store):
    from core.skills.reminders_skill import RemindersSkill

    async def add(args, memory):
        when_text = (args.get("when_text") or "").strip()
        text = (args.get("text") or "").strip()
        if not when_text or not text:
            return "Нужны и when_text, и text."
        skill = RemindersSkill(reminders_store)
        res = await skill.run(f"напомни {when_text} {text}", request_id="tool")
        return res.text

    async def lst(args, memory):
        skill = RemindersSkill(reminders_store)
        res = await skill.run("какие напоминания", request_id="tool")
        return res.text

    return add, lst


# ── Базовый набор runners (stateless или с memory) ──────────────────
TOOL_RUNNERS: dict[str, ToolFn] = {
    "get_time": _run_time,
    "get_date": _run_date,
    "get_time_in_city": _run_time_in_city,
    "get_weather": _run_weather,
    "get_weather_forecast": _run_forecast,
    "get_currency_rates": _run_currency,
    "get_crypto_rates": _run_crypto,
    "calculate": _run_calc,
    "convert_units": _run_convert,
    "wiki_lookup": _run_wiki,
    "translate": _run_translate,
    "set_timer": _run_timer,
    "set_alarm": _run_alarm,
    "remember_fact": _run_remember,
    "list_notes": _run_list_notes,
    "random_choice": _run_random,
    "web_search": _run_web_search,
    "github_my_prs": _run_github_my_prs,
    "github_repo_commits": _run_github_repo_commits,
    "calendar_today": _run_calendar_today,
    "system_info": _run_system_info,
    "clipboard_read": _run_clipboard_read,
    "clipboard_write": _run_clipboard_write,
}


def make_tool_runner(
    memory: MemoryManager,
    *,
    todo_store=None,
    reminders_store=None,
) -> Callable[[str, dict], Awaitable[str]]:
    """Фабрика runner-а. todo_store/reminders_store нужны для stateful tools.

    Если эти stores не переданы — todo_* и reminder_* tools вернут заглушку.
    """
    runners = dict(TOOL_RUNNERS)
    if todo_store is not None:
        todo_add, todo_list, todo_done = _make_todo_runners(todo_store)
        runners["todo_add"] = todo_add
        runners["todo_list"] = todo_list
        runners["todo_done"] = todo_done
    if reminders_store is not None:
        rem_add, rem_list = _make_reminder_runners(reminders_store)
        runners["reminder_add"] = rem_add
        runners["reminder_list"] = rem_list

    async def _runner(name: str, args: dict[str, Any]) -> str:
        fn = runners.get(name)
        if fn is None:
            return f"[unknown tool: {name}]"
        try:
            return await asyncio.wait_for(fn(args, memory), timeout=15.0)
        except asyncio.TimeoutError:
            return f"[tool {name} timed out after 15s]"

    return _runner

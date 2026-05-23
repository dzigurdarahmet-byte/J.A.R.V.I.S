"""Skill: новости из российских + опционально иностранных RSS-источников.

Заменяет старый NewsSkill на NewsAPI (заблочен в РФ + бедный контент по `country=ru`).

Источники по умолчанию (все RSS публичные, без ключей):
  - РИА Новости
  - ТАСС
  - РБК
  - Lenta.ru
  - Коммерсантъ

Иностранные (опционально, переводятся через mymemory translate):
  - Reuters World, BBC World — для общего фона.

Триггеры:
  - «новости», «что нового», «последние новости» — топ-N свежих по всем источникам.
  - «новости про X», «новости IT», «новости по криптe» — фильтр по теме (substring в title).
  - «мировые новости», «иностранные новости» — только зарубежные источники.

Параллельный fetch всех источников через asyncio.gather → парсинг RSS XML →
merge + дедуп по близости заголовков → сортировка по pubDate → топ N.

L1 keyword + L2 tool-use.
"""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NewsSource:
    """Описание RSS-источника."""
    name: str           # Человекочитаемое имя ("РИА Новости")
    rss_url: str
    flag: str           # "🇷🇺" / "🇬🇧" / "🇺🇸" — для подсветки в выдаче
    is_foreign: bool    # True → требует перевода title на русский
    weight: float = 1.0 # приоритет в общем выводе (для будущей балансировки)


# Российские источники — топ-5 крупнейших с открытыми RSS.
RU_SOURCES: list[NewsSource] = [
    NewsSource("РИА Новости", "https://ria.ru/export/rss2/archive/index.xml", "🇷🇺", False),
    NewsSource("ТАСС",        "https://tass.ru/rss/v2.xml",                  "🇷🇺", False),
    NewsSource("РБК",         "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "🇷🇺", False),
    NewsSource("Lenta.ru",    "https://lenta.ru/rss",                        "🇷🇺", False),
    NewsSource("Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml",      "🇷🇺", False),
]

# Иностранные — для запроса «мировые новости». Часто блочат РФ-IP без VPN.
INTL_SOURCES: list[NewsSource] = [
    NewsSource("Reuters",     "https://feeds.reuters.com/reuters/topNews",  "🌍", True),
    NewsSource("BBC News",    "http://feeds.bbci.co.uk/news/rss.xml",       "🇬🇧", True),
]


@dataclass(slots=True)
class NewsItem:
    title: str
    source_name: str
    flag: str
    pub_dt: datetime
    link: str = ""
    is_foreign: bool = False


# ── RSS fetch + parse ───────────────────────────────────────────────
# Некоторые RSS отказывают на «голом» httpx без User-Agent (anti-bot).
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 JARVIS-RSS/1.0"
)


async def _fetch_source(client: httpx.AsyncClient, src: NewsSource, limit: int = 8) -> list[NewsItem]:
    """Скачать и распарсить RSS одного источника. На любые сетевые ошибки — []."""
    try:
        r = await client.get(src.rss_url, timeout=10.0, headers={"User-Agent": _USER_AGENT})
        r.raise_for_status()
        # ET требует bytes для корректного определения encoding из XML-prolog.
        root = ET.fromstring(r.content)
    except (httpx.HTTPError, ET.ParseError) as e:
        logger.warning(
            "news_source_failed",
            source=src.name,
            error_type=type(e).__name__,
            error=str(e)[:200],
        )
        return []
    items: list[NewsItem] = []
    for it in root.findall(".//item")[:limit]:
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        pub_str = it.findtext("pubDate") or ""
        try:
            pub_dt = parsedate_to_datetime(pub_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub_dt = datetime.now(tz=timezone.utc)
        link = (it.findtext("link") or "").strip()
        items.append(NewsItem(
            title=title,
            source_name=src.name,
            flag=src.flag,
            pub_dt=pub_dt,
            link=link,
            is_foreign=src.is_foreign,
        ))
    return items


async def _translate_to_ru(client: httpx.AsyncClient, text: str) -> str:
    """Перевести EN→RU через mymemory (бесплатно, без ключа). На ошибке — оригинал."""
    if not text:
        return text
    try:
        r = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|ru"},
            timeout=6.0,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("responseData", {}).get("translatedText") or text).strip()
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.debug("translate_failed", error=str(e)[:80])
        return text


def _dedup_by_title(items: list[NewsItem], n_chars: int = 40) -> list[NewsItem]:
    """Грубый дедуп: одинаковые первые N символов (lowercase) — оставляем первый."""
    seen: set[str] = set()
    out: list[NewsItem] = []
    for it in items:
        key = re.sub(r"\s+", " ", it.title.lower())[:n_chars]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _matches_topic(title: str, topic: str) -> bool:
    """Проверить что title содержит topic (любые слова из topic как substring)."""
    title_low = title.lower()
    words = [w for w in re.split(r"\s+", topic.lower()) if len(w) >= 3]
    if not words:
        return False
    return any(w in title_low for w in words)


# ── Parse user query ────────────────────────────────────────────────
_TOPIC_RE = re.compile(
    r"\bновости\s+(?:про|по|о[бо]?|на\s+тему)\s+(.+?)(?:[.!?]|$)",
    re.IGNORECASE | re.UNICODE,
)
_TOPIC_ADJ_RE = re.compile(
    # «новости IT», «новости спорта», «новости про крипту»
    r"\bновости\s+([а-яё]+(?:\s+[а-яё]+)?|[A-Za-z]+)\b",
    re.IGNORECASE | re.UNICODE,
)
_INTL_RE = re.compile(
    r"\b(?:мировые|иностранные|зарубежные|международные|западные)\s+новости\b"
    r"|\bновости\s+(?:мира|из\s+мира|из\s+за\s+рубежа)\b",
    re.IGNORECASE | re.UNICODE,
)
# Стоп-слова которые не считаем темой (часть служебной фразы)
_STOPWORDS = {
    "сегодня", "сейчас", "новые", "свежие", "последние", "топ", "главные",
    "важные", "коротко", "кратко", "пожалуйста",
}


def _extract_topic(text: str) -> str | None:
    """Извлечь топик из запроса. None если общие новости."""
    m = _TOPIC_RE.search(text)
    if m:
        topic = m.group(1).strip().lower()
        if topic and topic not in _STOPWORDS:
            return topic
    m = _TOPIC_ADJ_RE.search(text)
    if m:
        topic = m.group(1).strip().lower()
        if topic and topic not in _STOPWORDS:
            return topic
    return None


# ── Skill ───────────────────────────────────────────────────────────
class NewsRuSkill(KeywordSkill):
    """L1+L2 skill: новости из РФ-источников (+ иностранные опционально)."""

    name = "news_ru"
    keywords = [
        r"\bновости\b",
        r"\bчто\s+нового\b",
        r"\bпоследние\s+(новости|события)\b",
        r"\bглавные\s+новости\b",
        r"\bсводка\s+новостей\b",
    ]

    def __init__(
        self,
        ru_sources: list[NewsSource] | None = None,
        intl_sources: list[NewsSource] | None = None,
        default_limit: int = 5,
    ) -> None:
        super().__init__()
        self._ru = ru_sources if ru_sources is not None else RU_SOURCES
        self._intl = intl_sources if intl_sources is not None else INTL_SOURCES
        self._default_limit = default_limit

    async def run(self, text: str, request_id: str) -> SkillResult:
        intl_mode = bool(_INTL_RE.search(text))
        topic = _extract_topic(text) if not intl_mode else None
        sources = self._intl if intl_mode else self._ru
        limit_per_src = 12 if topic else 5  # больше тянем при фильтре — повысить шанс попадания

        items = await self._gather_news(sources, limit_per_src)
        if not items:
            return SkillResult(
                text="Новости сегодня не пришли — все источники молчат.",
                speakable=True,
            )

        # Filter by topic
        if topic:
            items = [it for it in items if _matches_topic(it.title, topic)]
            if not items:
                return SkillResult(
                    text=f"По теме «{topic}» свежих новостей не нашлось.",
                    speakable=True,
                )

        # Translate intl
        if intl_mode:
            async with httpx.AsyncClient() as client:
                for it in items:
                    it.title = await _translate_to_ru(client, it.title)

        # Sort by freshness, dedup, take top N
        items.sort(key=lambda x: x.pub_dt, reverse=True)
        items = _dedup_by_title(items)[: self._default_limit]

        logger.info(
            "news_ru_done",
            request_id=request_id,
            topic=topic,
            intl=intl_mode,
            returned=len(items),
        )

        # Format
        header = self._format_header(topic, intl_mode)
        lines = [
            f"  {i + 1}. {it.flag} {it.title} — {it.source_name}"
            for i, it in enumerate(items)
        ]
        return SkillResult(
            text=header + "\n" + "\n".join(lines),
            speakable=True,
            data={
                "topic": topic,
                "intl": intl_mode,
                "items": [
                    {"title": it.title, "source": it.source_name, "link": it.link}
                    for it in items
                ],
            },
        )

    async def _gather_news(
        self, sources: list[NewsSource], limit_per_src: int
    ) -> list[NewsItem]:
        # trust_env=False — игнорируем системный HTTPS_PROXY (через v2rayN
        # к Frankfurt РФ-сайты не открываются). Идём напрямую с резидентного IP.
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=10.0, trust_env=False
        ) as client:
            results = await asyncio.gather(
                *[_fetch_source(client, src, limit_per_src) for src in sources],
                return_exceptions=True,
            )
        merged: list[NewsItem] = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
        return merged

    @staticmethod
    def _format_header(topic: str | None, intl: bool) -> str:
        if intl:
            return "Мировые новости:"
        if topic:
            return f"Новости по теме «{topic}»:"
        return "Главные новости:"

    # ── L2 Tool-use ─────────────────────────────────────────────────
    def as_tool(self) -> dict[str, Any]:
        return {
            "name": "news_ru",
            "description": (
                "Fetch latest news from Russian RSS sources (RIA, TASS, RBC, "
                "Lenta, Kommersant) or international ones (Reuters, BBC) when "
                "requested. Optional topic filter."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Optional substring to filter titles (e.g. 'IT', 'крипта', 'выборы').",
                    },
                    "intl": {
                        "type": "boolean",
                        "description": "True → only international sources (translated to Russian).",
                        "default": False,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (1-10). Default 5.",
                    },
                },
            },
        }

    async def run_with_args(
        self, args: dict[str, Any], request_id: str
    ) -> SkillResult:
        topic = (args.get("topic") or "").strip().lower() or None
        intl_mode = bool(args.get("intl", False))
        limit = max(1, min(10, int(args.get("limit") or self._default_limit)))
        sources = self._intl if intl_mode else self._ru
        items = await self._gather_news(sources, 12)
        if topic:
            items = [it for it in items if _matches_topic(it.title, topic)]
        if intl_mode and items:
            async with httpx.AsyncClient() as client:
                for it in items:
                    it.title = await _translate_to_ru(client, it.title)
        items.sort(key=lambda x: x.pub_dt, reverse=True)
        items = _dedup_by_title(items)[:limit]
        if not items:
            return SkillResult(text="Новостей не нашлось.", speakable=True)
        header = self._format_header(topic, intl_mode)
        lines = [f"  {i + 1}. {it.flag} {it.title} — {it.source_name}" for i, it in enumerate(items)]
        return SkillResult(
            text=header + "\n" + "\n".join(lines),
            speakable=True,
            data={"topic": topic, "intl": intl_mode, "count": len(items)},
        )

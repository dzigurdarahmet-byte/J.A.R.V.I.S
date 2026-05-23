"""Провайдеры веб-поиска для WebSearchSkill.

Два бэкенда:
  - YandexXMLProvider — legacy Yandex.XML (yandex.ru/dev/xml).
    Free tier: 100 запросов/сутки для зарегистрированных в Webmaster.
    Конфиг: YANDEX_XML_USER + YANDEX_XML_KEY в .env.
  - DuckDuckGoProvider — HTML-scrape https://html.duckduckgo.com/html.
    Без ключей, работает в РФ. Качество — приемлемое для базовых запросов.

Контракт: get_default_provider() возвращает Yandex если есть ключи,
иначе DDG. Skill сам выбирает.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse, parse_qs, unquote

import httpx

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str  # "yandex" / "duckduckgo"


class SearchProvider(Protocol):
    name: str
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


# ─── Yandex.XML ─────────────────────────────────────────────────────
YANDEX_XML_ENDPOINT = "https://yandex.ru/search/xml"


class YandexXMLProvider:
    name = "yandex"

    def __init__(self, user: str, key: str) -> None:
        if not user or not key:
            raise ValueError("YandexXMLProvider: нужны user + key")
        self._user = user
        self._key = key

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        params = {
            "user": self._user,
            "key": self._key,
            "query": query,
            "l10n": "ru",
            "filter": "moderate",
            "maxpassages": "2",
            "groupby": f"attr=d.mode=deep.groups-on-page={min(limit, 10)}.docs-in-group=1",
        }
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(YANDEX_XML_ENDPOINT, params=params)
            r.raise_for_status()
            xml_text = r.text
        return _parse_yandex_xml(xml_text, limit=limit)


def _parse_yandex_xml(xml_text: str, limit: int) -> list[SearchResult]:
    """Парсим Yandex.XML response. Структура:
    <yandexsearch><response>
      <error code="..."> ... </error>          ← optional, если ошибка
      <results><grouping><group>
        <doc>
          <url>...</url>
          <title>...<hlword>...</hlword>...</title>
          <passages><passage>...</passage></passages>
        </doc>
      </group></grouping></results>
    </response></yandexsearch>
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("yandex_xml_parse_failed", error=str(e))
        return []

    err = root.find(".//response/error")
    if err is not None:
        logger.warning(
            "yandex_xml_error",
            code=err.attrib.get("code"),
            msg=(err.text or "").strip(),
        )
        return []

    results = []
    for doc in root.findall(".//response/results/grouping/group/doc"):
        if len(results) >= limit:
            break
        url_el = doc.find("url")
        title_el = doc.find("title")
        passages = doc.find("passages")
        url = (url_el.text if url_el is not None else "") or ""
        # title и passage могут содержать <hlword>...</hlword> — снимаем теги
        title = _flatten_xml_text(title_el)
        snippet = ""
        if passages is not None:
            parts = [_flatten_xml_text(p) for p in passages.findall("passage")]
            snippet = " ".join(p for p in parts if p)
        results.append(SearchResult(
            title=title.strip() or "(без заголовка)",
            url=url.strip(),
            snippet=snippet.strip()[:300],
            source="yandex",
        ))
    return results


def _flatten_xml_text(el) -> str:
    if el is None:
        return ""
    pieces = []
    if el.text:
        pieces.append(el.text)
    for child in el:
        pieces.append(_flatten_xml_text(child))
        if child.tail:
            pieces.append(child.tail)
    return "".join(pieces)


# ─── DuckDuckGo HTML ────────────────────────────────────────────────
DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


class DuckDuckGoProvider:
    name = "duckduckgo"

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        # POST с form-data; DDG отвечает HTML. User-Agent обязателен.
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        data = {"q": query, "kl": "ru-ru"}
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
            r = await c.post(DDG_HTML_ENDPOINT, data=data, headers=headers)
            r.raise_for_status()
            html_text = r.text
        return _parse_ddg_html(html_text, limit=limit)


# DDG HTML: каждый результат обёрнут в <div class="result results_links results_links_deep web-result">
# Внутри:
#   <a class="result__a" rel="nofollow" href="ENCODED_URL">TITLE</a>
#   <a class="result__url" href="...">VISIBLE_DOMAIN</a>
#   <a class="result__snippet" href="...">SNIPPET</a>
_DDG_BLOCK = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?'
    r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return html.unescape(_TAG.sub("", s)).strip()


def _decode_ddg_url(href: str) -> str:
    """DDG оборачивает URL'ы в свой redirect: //duckduckgo.com/l/?uddg=ENCODED."""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and "/l/" in parsed.path:
            qs = parse_qs(parsed.query)
            target = qs.get("uddg", [None])[0]
            if target:
                return unquote(target)
    except Exception:
        pass
    return href


def _parse_ddg_html(html_text: str, limit: int) -> list[SearchResult]:
    results = []
    for m in _DDG_BLOCK.finditer(html_text):
        if len(results) >= limit:
            break
        href, title_raw, snippet_raw = m.group(1), m.group(2), m.group(3)
        url = _decode_ddg_url(href)
        title = _strip_tags(title_raw)
        snippet = _strip_tags(snippet_raw)[:300]
        if not url or not title:
            continue
        results.append(SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            source="duckduckgo",
        ))
    return results


# ─── Selection / factory ────────────────────────────────────────────
def get_default_provider() -> SearchProvider:
    """Yandex если есть ключи в .env, иначе DDG."""
    user = getattr(settings, "yandex_xml_user", None)
    key = getattr(settings, "yandex_xml_key", None)
    if user and key:
        key_val = key.get_secret_value() if hasattr(key, "get_secret_value") else key
        if key_val:
            try:
                return YandexXMLProvider(user=user, key=key_val)
            except Exception as e:
                logger.warning("yandex_provider_init_failed", error=str(e))
    return DuckDuckGoProvider()

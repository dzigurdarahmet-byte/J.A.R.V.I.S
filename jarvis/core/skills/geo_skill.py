"""GeoSkill — поиск мест и геокодинг через 2GIS + Yandex.

Триггеры:
  • «где ближайшая аптека» / «найди банкомат рядом»
  • «адрес офиса Сбера в Москве» / «координаты Никольской 12»

Если нет ни одного key — отдаём аккуратное сообщение «не настроено».
"""

from __future__ import annotations

import re

from core.config import settings
from core.logging import get_logger
from core.router import SkillResult
from core.skills.base import KeywordSkill
from core.skills.geo_providers import geocode_yandex, search_nearby_2gis

logger = get_logger(__name__)

# Дефолтный «дом» Босса для запросов «рядом» — Москва центр.
# В будущем брать из .env JARVIS_OWNER_HOME_LAT / LON.
DEFAULT_HOME_LAT = 55.7558  # Кремль
DEFAULT_HOME_LON = 37.6173


class GeoSkill(KeywordSkill):
    name = "geo"
    keywords = [
        r"\bгде\s+(ближайш\w+|поблизости)",
        r"\bнайди\s+(ближайш\w+|поблизости)",
        r"\bближайш\w+\s+\w+\s+рядом",
        r"\bпоиск\s+(аптек\w+|кафе|ресторан\w+|банкомат\w+|магазин\w+)\b",
        r"\bадрес\s+\w+",  # «адрес Сбербанка», «адрес Никольской 12»
        r"\bкоординат\w+\s+\w+",
    ]

    async def run(self, text: str, request_id: str) -> SkillResult:
        low = text.lower()

        # 1) Адрес/координаты → геокодинг через Yandex
        if re.search(r"\b(адрес|координат)", low):
            query = re.sub(r"^.*?(адрес|координат\w*)\s+", "", text, flags=re.IGNORECASE).strip("?.,!")
            if len(query) < 3:
                return SkillResult(text="Босс, какой адрес/объект?", speakable=True)
            pt = await geocode_yandex(query)
            if pt is None:
                return SkillResult(
                    text="Геокодер не настроен (нужен YANDEX_GEOCODER_KEY) или адрес не найден.",
                    speakable=True,
                )
            return SkillResult(
                text=f"{pt.address} — {pt.lat:.5f}, {pt.lon:.5f}",
                speakable=False,
            )

        # 2) Поиск ближайших POI через 2GIS
        # вытащим «что искать» — слово после «ближайш…» / «найди»
        m = re.search(r"\b(?:ближайш\w+|найди|рядом|поблизости)\s+(\w+)", low)
        what = m.group(1) if m else None
        if not what:
            # последний случай: «где X рядом» / «X поблизости»
            m2 = re.search(r"\bгде\s+(\w+)", low) or re.search(r"\b(\w+)\s+(рядом|поблизости)", low)
            what = m2.group(1) if m2 else None
        if not what:
            return SkillResult(text="Босс, что искать?", speakable=True)

        key = getattr(settings, "twogis_api_key", None)
        if not key or not key.get_secret_value():
            return SkillResult(
                text="Босс, для поиска мест нужен 2GIS API key — добавь TWOGIS_API_KEY в .env.",
                speakable=True,
            )
        places = await search_nearby_2gis(
            query=what,
            lat=DEFAULT_HOME_LAT,
            lon=DEFAULT_HOME_LON,
            radius_m=1500,
            limit=5,
        )
        if not places:
            return SkillResult(
                text=f"Не нашёл «{what}» в радиусе 1.5 км.",
                speakable=True,
            )
        lines = [f"Ближайшие — «{what}»:"]
        for p in places[:5]:
            d = f"{p.distance_m} м" if p.distance_m else ""
            tail = f" • {d}" if d else ""
            lines.append(f"• {p.name} — {p.address}{tail}")
        return SkillResult(text="\n".join(lines), speakable=False)

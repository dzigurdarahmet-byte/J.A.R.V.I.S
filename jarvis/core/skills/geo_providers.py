"""Геосервисы: 2GIS Catalog (POI) + Yandex Geocoder (адреса).

Стратегия:
- POI / «где ближайшая аптека» → 2GIS (специализация на справочнике)
- Geocoding адреса «Никольская 12, Москва» → координаты → Yandex
- Reverse geocoding (lat,lon → адрес) → Yandex
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

HTTP_TIMEOUT: Final = 6.0


@dataclass(slots=True)
class Place:
    """Унифицированный POI."""

    name: str
    address: str
    lat: float
    lon: float
    distance_m: int | None = None  # если задан центр поиска
    rubric: str = ""
    phone: str = ""


@dataclass(slots=True)
class GeoPoint:
    """Точка с координатами + человекочитаемым адресом."""

    address: str
    lat: float
    lon: float


# ──────────────────────────────────────────────────────────────────────
# Yandex Geocoder (через AI Studio / Cloud key)
# ──────────────────────────────────────────────────────────────────────

YANDEX_GEOCODER_URL = "https://geocode-maps.yandex.ru/1.x/"


async def geocode_yandex(query: str) -> GeoPoint | None:
    """Адрес → координаты. Use HTTP Yandex Geocoder (нужен apikey).

    NB: Yandex Geocoder использует ОТДЕЛЬНЫЙ JS API ключ (не AI Studio).
    Если YANDEX_GEOCODER_KEY не задан — возвращаем None.
    """
    key = getattr(settings, "yandex_geocoder_key", None)
    if not key or not key.get_secret_value():
        return None
    params = {
        "apikey": key.get_secret_value(),
        "geocode": query,
        "format": "json",
        "results": 1,
        "lang": "ru_RU",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(YANDEX_GEOCODER_URL, params=params)
        r.raise_for_status()
        data = r.json()
    try:
        feat = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]
        pos = feat["Point"]["pos"]  # "lon lat"
        lon, lat = (float(x) for x in pos.split())
        addr = feat["metaDataProperty"]["GeocoderMetaData"]["text"]
        return GeoPoint(address=addr, lat=lat, lon=lon)
    except (KeyError, IndexError):
        return None


# ──────────────────────────────────────────────────────────────────────
# 2GIS Catalog API (POI поиск)
# ──────────────────────────────────────────────────────────────────────

TWOGIS_SEARCH_URL = "https://catalog.api.2gis.com/3.0/items"


async def search_nearby_2gis(
    query: str,
    lat: float,
    lon: float,
    radius_m: int = 1500,
    limit: int = 5,
) -> list[Place]:
    """Найти ближайшие POI вокруг точки. Returns [] если нет ключа/ничего не найдено."""
    key = getattr(settings, "twogis_api_key", None)
    if not key or not key.get_secret_value():
        logger.info("twogis_no_key")
        return []
    params = {
        "key": key.get_secret_value(),
        "q": query,
        "point": f"{lon},{lat}",  # 2GIS: lon,lat
        "radius": radius_m,
        "page_size": limit,
        "fields": "items.point,items.address_name,items.adm_div,items.contact_groups,items.rubrics",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(TWOGIS_SEARCH_URL, params=params)
        r.raise_for_status()
        data = r.json()
    if data.get("meta", {}).get("code") != 200:
        return []
    items = data.get("result", {}).get("items", [])
    out: list[Place] = []
    for it in items:
        try:
            pt = it.get("point") or {}
            ilat = float(pt.get("lat", 0))
            ilon = float(pt.get("lon", 0))
            phone = ""
            for grp in it.get("contact_groups", []) or []:
                for c in grp.get("contacts", []) or []:
                    if c.get("type") == "phone":
                        phone = c.get("value", "")
                        break
                if phone:
                    break
            rubric = ""
            rubrics = it.get("rubrics") or []
            if rubrics:
                rubric = rubrics[0].get("name", "")
            # дистанция считается клиентом, 2GIS её не возвращает напрямую
            dist = _haversine_m(lat, lon, ilat, ilon)
            out.append(Place(
                name=it.get("name") or "(без названия)",
                address=it.get("address_name") or "",
                lat=ilat,
                lon=ilon,
                distance_m=int(dist),
                rubric=rubric,
                phone=phone,
            ))
        except Exception as e:
            logger.warning("twogis_parse_failed", error=str(e))
            continue
    out.sort(key=lambda p: p.distance_m or 0)
    return out


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками на земле в метрах."""
    import math

    R = 6_371_000  # m
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

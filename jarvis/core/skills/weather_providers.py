"""Поставщики погодных данных: Open-Meteo (primary) + OpenWeather (fallback).

Open-Meteo:
    + бесплатно, без ключа
    + лучшее покрытие малых городов РФ (геокодинг включает Сыктывкар, Ноябрьск и т.п.)
    - описания только английские → мапим WMO weather codes в русский

OpenWeather:
    + русские описания «облачно с прояснениями»
    - 1000 запросов/день free tier, нужен ключ
    - хуже с малыми городами РФ

Стратегия: пробуем Open-Meteo. Если упал/таймаут/нет геокода — фолбэк на OpenWeather.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)

HTTP_TIMEOUT = 6.0  # секунд на запрос — оба провайдера


# ──────────────────────────────────────────────────────────────────────
# WMO weather codes → русские описания
# https://open-meteo.com/en/docs (раздел "Weather variable documentation")
# ──────────────────────────────────────────────────────────────────────
WMO_RU: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "ледяная сильная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "ливневый дождь",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}


@dataclass(slots=True)
class WeatherSnapshot:
    """Унифицированный результат: текущая погода."""

    city: str  # как нашёл провайдер (для отображения)
    temp_c: int
    feels_like_c: int
    description: str
    wind_ms: int


@dataclass(slots=True)
class ForecastDay:
    """Один день прогноза."""

    date_iso: str  # YYYY-MM-DD
    temp_c: int  # типичная (max в midday)
    description: str


# ──────────────────────────────────────────────────────────────────────
# Open-Meteo
# ──────────────────────────────────────────────────────────────────────

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


async def _open_meteo_geocode(client: httpx.AsyncClient, city: str) -> tuple[float, float, str] | None:
    """city -> (lat, lon, normalized_name). None если не нашли."""
    params = {"name": city, "count": 1, "language": "ru"}
    r = await client.get(GEOCODE_URL, params=params)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return float(top["latitude"]), float(top["longitude"]), str(top.get("name") or city)


async def fetch_current_open_meteo(city: str) -> WeatherSnapshot | None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        geo = await _open_meteo_geocode(client, city)
        if geo is None:
            logger.info("open_meteo_geocode_miss", city=city)
            return None
        lat, lon, found_name = geo
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "wind_speed_unit": "ms",
            "timezone": "auto",
        }
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        cur = (r.json().get("current") or {})
        if not cur:
            return None
        return WeatherSnapshot(
            city=found_name,
            temp_c=round(float(cur.get("temperature_2m", 0))),
            feels_like_c=round(float(cur.get("apparent_temperature", 0))),
            description=WMO_RU.get(int(cur.get("weather_code", 0)), "—"),
            wind_ms=round(float(cur.get("wind_speed_10m", 0))),
        )


async def fetch_forecast_open_meteo(city: str, days: int = 3) -> tuple[str, list[ForecastDay]] | None:
    """Returns (found_name, days) или None."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        geo = await _open_meteo_geocode(client, city)
        if geo is None:
            return None
        lat, lon, found_name = geo
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "forecast_days": days,
            "timezone": "auto",
        }
        r = await client.get(FORECAST_URL, params=params)
        r.raise_for_status()
        d = r.json().get("daily") or {}
        dates = d.get("time") or []
        tmax = d.get("temperature_2m_max") or []
        tmin = d.get("temperature_2m_min") or []
        codes = d.get("weather_code") or []
        if not dates:
            return None
        out: list[ForecastDay] = []
        for i in range(min(len(dates), days)):
            avg = round((float(tmax[i]) + float(tmin[i])) / 2) if i < len(tmin) else round(float(tmax[i]))
            out.append(ForecastDay(
                date_iso=str(dates[i]),
                temp_c=avg,
                description=WMO_RU.get(int(codes[i]), "—"),
            ))
        return found_name, out


# ──────────────────────────────────────────────────────────────────────
# OpenWeather (fallback)
# ──────────────────────────────────────────────────────────────────────

async def fetch_current_openweather(city: str) -> WeatherSnapshot | None:
    key = settings.openweather_api_key
    if not key or not key.get_secret_value():
        return None
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": key.get_secret_value(), "units": "metric", "lang": "ru"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(url, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    return WeatherSnapshot(
        city=str(data.get("name") or city),
        temp_c=round(float(data["main"]["temp"])),
        feels_like_c=round(float(data["main"]["feels_like"])),
        description=str(data["weather"][0]["description"]),
        wind_ms=round(float(data["wind"]["speed"])),
    )


async def fetch_forecast_openweather(city: str, days: int = 3) -> tuple[str, list[ForecastDay]] | None:
    key = settings.openweather_api_key
    if not key or not key.get_secret_value():
        return None
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"q": city, "appid": key.get_secret_value(), "units": "metric", "lang": "ru", "cnt": 24}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        r = await client.get(url, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    found_name = str((data.get("city") or {}).get("name") or city)
    # один прогноз на день — берём середину дня
    daily: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        d, t = item["dt_txt"].split(" ")
        if d not in daily or t == "12:00:00":
            daily[d] = item
    out: list[ForecastDay] = []
    for d, item in list(daily.items())[:days]:
        out.append(ForecastDay(
            date_iso=d,
            temp_c=round(float(item["main"]["temp"])),
            description=str(item["weather"][0]["description"]),
        ))
    return found_name, out


# ──────────────────────────────────────────────────────────────────────
# Высокоуровневые API: пробуем primary, fallback на secondary
# ──────────────────────────────────────────────────────────────────────

async def fetch_current(city: str) -> WeatherSnapshot | None:
    """Open-Meteo → OpenWeather. Возвращает первый успех или None если оба упали."""
    try:
        snap = await fetch_current_open_meteo(city)
        if snap:
            logger.info("weather_provider_used", provider="open-meteo", city=city)
            return snap
    except Exception as e:
        logger.warning("open_meteo_current_failed", city=city, error=str(e))
    try:
        snap = await fetch_current_openweather(city)
        if snap:
            logger.info("weather_provider_used", provider="openweather", city=city)
            return snap
    except Exception as e:
        logger.warning("openweather_current_failed", city=city, error=str(e))
    return None


async def fetch_forecast(city: str, days: int = 3) -> tuple[str, list[ForecastDay]] | None:
    try:
        res = await fetch_forecast_open_meteo(city, days=days)
        if res:
            logger.info("forecast_provider_used", provider="open-meteo", city=city)
            return res
    except Exception as e:
        logger.warning("open_meteo_forecast_failed", city=city, error=str(e))
    try:
        res = await fetch_forecast_openweather(city, days=days)
        if res:
            logger.info("forecast_provider_used", provider="openweather", city=city)
            return res
    except Exception as e:
        logger.warning("openweather_forecast_failed", city=city, error=str(e))
    return None

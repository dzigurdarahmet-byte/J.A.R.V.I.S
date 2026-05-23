"""Тест: погода для разных городов через новые провайдеры."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Shim StrEnum для Python 3.10 sandbox (не нужно на venv 3.13, но безопасно)
import enum as _e  # noqa: E402

if not hasattr(_e, "StrEnum"):
    class _SE(str, _e.Enum):
        pass

    _e.StrEnum = _SE  # type: ignore[attr-defined]

from core.skills.weather_providers import fetch_current, fetch_forecast  # noqa: E402


CITIES = ["Москва", "Сыктывкар", "Ноябрьск", "Альметьевск", "Лондон", "Токио"]


async def main() -> int:
    print("=== Current ===")
    for c in CITIES:
        try:
            snap = await asyncio.wait_for(fetch_current(c), timeout=15.0)
        except asyncio.TimeoutError:
            print(f"{c:15s} -> TIMEOUT")
            continue
        if snap:
            print(f"{c:15s} -> {snap.city[:25]:25s} {snap.temp_c:+3d}°, ощущ {snap.feels_like_c:+3d}°, "
                  f"{snap.description}, ветер {snap.wind_ms} м/с")
        else:
            print(f"{c:15s} -> MISS")

    print("\n=== Forecast (Москва, Сыктывкар) ===")
    for c in ["Москва", "Сыктывкар"]:
        res = await asyncio.wait_for(fetch_forecast(c, days=3), timeout=15.0)
        if not res:
            print(f"{c} -> MISS")
            continue
        name, days = res
        print(f"{c} (как {name}):")
        for d in days:
            print(f"  {d.date_iso}: {d.temp_c:+3d}°, {d.description}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

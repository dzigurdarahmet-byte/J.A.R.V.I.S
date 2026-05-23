"""Proactive alerts — Джарвис сам сообщает Боссу о важных событиях.

Три типа алертов:
  1. Финансовый: курс USD/EUR/BTC изменился >threshold% относительно прошлой проверки.
  2. Погодный: грозовое/штормовое предупреждение в Москве на ближайшие 2 часа.
  3. Care: Босс не писал в каналы >COOLDOWN_HOURS — заботливое «всё в порядке?».

Архитектура: AlertScheduler — отдельный async task, проверяет состояние каждые
CHECK_INTERVAL_MIN минут. Состояние (последние значения) — в workspace/alerts_state.json.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from core.logging import get_logger

logger = get_logger(__name__)

CHECK_INTERVAL_MIN = 30           # как часто проверять
USD_THRESHOLD_PCT = 2.0           # >2% изменения USD/RUB → алерт
BTC_THRESHOLD_PCT = 5.0           # крипта волатильнее — порог выше
SILENCE_THRESHOLD_HOURS = 36      # сколько часов тишины до care-message
WEATHER_KEYWORDS = (
    "гроз", "ливень", "штор", "снегопад", "метель", "ураган", "град", "буря",
)


@dataclass(slots=True)
class AlertState:
    """Состояние: последние значения, чтобы было с чем сравнивать."""

    last_usd: float | None = None
    last_eur: float | None = None
    last_btc: float | None = None
    last_check_ts: float = 0.0
    last_user_msg_ts: float = 0.0  # последняя реплика Босса в любом канале
    last_care_sent_ts: float = 0.0  # чтобы не слать care-message повторно

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_usd": self.last_usd,
            "last_eur": self.last_eur,
            "last_btc": self.last_btc,
            "last_check_ts": self.last_check_ts,
            "last_user_msg_ts": self.last_user_msg_ts,
            "last_care_sent_ts": self.last_care_sent_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AlertState":
        return cls(
            last_usd=d.get("last_usd"),
            last_eur=d.get("last_eur"),
            last_btc=d.get("last_btc"),
            last_check_ts=float(d.get("last_check_ts") or 0.0),
            last_user_msg_ts=float(d.get("last_user_msg_ts") or 0.0),
            last_care_sent_ts=float(d.get("last_care_sent_ts") or 0.0),
        )


def _load_state(path: Path) -> AlertState:
    if not path.exists():
        return AlertState()
    try:
        return AlertState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning("alerts_state_load_failed", error=str(e))
        return AlertState()


def _save_state(path: Path, state: AlertState) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("alerts_state_save_failed", error=str(e))


async def _fetch_cbr() -> tuple[float | None, float | None]:
    """USD/RUB и EUR/RUB по ЦБ. Returns (usd, eur) или (None, None)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get("https://www.cbr-xml-daily.ru/daily_json.js")
            r.raise_for_status()
            data = r.json()
        rates = data.get("Valute", {})
        usd = float(rates["USD"]["Value"]) if "USD" in rates else None
        eur = float(rates["EUR"]["Value"]) if "EUR" in rates else None
        return usd, eur
    except Exception as e:
        logger.warning("cbr_fetch_failed", error=str(e))
        return None, None


async def _fetch_btc() -> float | None:
    """BTC в USD через CoinGecko."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
            )
            r.raise_for_status()
            return float(r.json().get("bitcoin", {}).get("usd") or 0) or None
    except Exception as e:
        logger.warning("btc_fetch_failed", error=str(e))
        return None


async def _check_weather_warning(city: str = "Москва") -> str | None:
    """Если в прогнозе на ближайшие 12ч есть угрозы — вернёт текст."""
    try:
        from core.skills.weather_providers import fetch_forecast
        result = await fetch_forecast(city, days=1)
        if not result:
            return None
        _, days = result
        if not days:
            return None
        first = days[0]
        desc = (first.description or "").lower()
        for kw in WEATHER_KEYWORDS:
            if kw in desc:
                return f"⚠️ В {city} ожидается: {first.description}, до {first.temp_c:+d}°."
        return None
    except Exception as e:
        logger.warning("weather_warning_failed", error=str(e))
        return None


# ──────────────────────────────────────────────────────────────────────
# AlertScheduler
# ──────────────────────────────────────────────────────────────────────


SendFn = Callable[[str], Awaitable[None]]


class AlertScheduler:
    """Periodic checker. Запускается из telegram bot run_bot()."""

    def __init__(self, workspace_dir: Path, send: SendFn) -> None:
        self._state_path = Path(workspace_dir) / "alerts_state.json"
        self._state = _load_state(self._state_path)
        self._send = send
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def notify_user_activity(self) -> None:
        """Канал зовёт это при каждой реплике Босса — чтоб таймер тишины сбрасывался."""
        self._state.last_user_msg_ts = time.time()
        _save_state(self._state_path, self._state)

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="alert-scheduler")
        logger.info("alert_scheduler_started", interval_min=CHECK_INTERVAL_MIN)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.error("alert_tick_error", error=str(e))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CHECK_INTERVAL_MIN * 60.0)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        now = time.time()

        # 1. Финансовые алерты
        usd, eur = await _fetch_cbr()
        btc = await _fetch_btc()
        await self._check_finance("USD", usd, self._state.last_usd, USD_THRESHOLD_PCT, "₽")
        await self._check_finance("EUR", eur, self._state.last_eur, USD_THRESHOLD_PCT, "₽")
        await self._check_finance("BTC", btc, self._state.last_btc, BTC_THRESHOLD_PCT, "$")
        if usd is not None:
            self._state.last_usd = usd
        if eur is not None:
            self._state.last_eur = eur
        if btc is not None:
            self._state.last_btc = btc

        # 2. Погодный алерт (раз в 4 часа достаточно — но мы крутимся каждые 30мин)
        if int(now) % (4 * 3600) < CHECK_INTERVAL_MIN * 60:
            warning = await _check_weather_warning("Москва")
            if warning:
                await self._send(warning)

        # 3. Care-сообщение
        silence_hours = (now - self._state.last_user_msg_ts) / 3600 if self._state.last_user_msg_ts else 0
        care_cool_hours = (now - self._state.last_care_sent_ts) / 3600 if self._state.last_care_sent_ts else 999
        if (
            self._state.last_user_msg_ts > 0
            and silence_hours >= SILENCE_THRESHOLD_HOURS
            and care_cool_hours >= 24
        ):
            await self._send(
                "Босс, давно от тебя ничего не слышно. Всё в порядке?"
            )
            self._state.last_care_sent_ts = now

        self._state.last_check_ts = now
        _save_state(self._state_path, self._state)

    async def _check_finance(
        self,
        name: str,
        current: float | None,
        previous: float | None,
        threshold_pct: float,
        unit: str,
    ) -> None:
        if current is None or previous is None or previous == 0:
            return
        pct = (current - previous) / previous * 100
        if abs(pct) < threshold_pct:
            return
        arrow = "📈" if pct > 0 else "📉"
        sign = "+" if pct > 0 else ""
        await self._send(
            f"{arrow} {name} {sign}{pct:.1f}% — сейчас {current:.2f}{unit} (было {previous:.2f}{unit})."
        )
        logger.info("alert_finance_fired", asset=name, pct=round(pct, 2))

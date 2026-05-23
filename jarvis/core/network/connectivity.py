"""Network connectivity watchdog.

Проверяет доступность облачных endpoint'ов фоновой задачей. Публикует SYSTEM
events с network_state, чтобы остальные компоненты могли реагировать
(Router скипает online-skills, UI меняет индикатор, и т.д.).

States:
    online   — оба провайдера (Anthropic + Yandex) пингуются
    partial  — один из двух недоступен
    offline  — оба недоступны (или сеть мертва)

Использование:
    wd = NetworkWatchdog(bus)
    await wd.start()
    ...
    state = get_network_state()  # NetworkState.ONLINE/PARTIAL/OFFLINE
    await wd.stop()
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Final

import httpx

from core.event_bus import EventType, JarvisEvent
from core.logging import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_SEC: Final = 30.0
PROBE_TIMEOUT_SEC: Final = 5.0

# HEAD к этим URL — дёшево, не считается за API-вызов, не списывает quota.
PROBE_TARGETS: Final[dict[str, str]] = {
    "anthropic": "https://api.anthropic.com/",
    "yandex_cloud": "https://llm.api.cloud.yandex.net/",
}


class NetworkState(StrEnum):
    ONLINE = "online"      # все ключевые провайдеры доступны
    PARTIAL = "partial"    # один из двух упал
    OFFLINE = "offline"    # нет сети или все недоступны
    UNKNOWN = "unknown"    # ещё не проверяли


# Глобальное состояние — читается из любого места кода.
_current_state: NetworkState = NetworkState.UNKNOWN
_provider_status: dict[str, bool] = {}


def get_network_state() -> NetworkState:
    """Текущее состояние сети (без блокировки). До первого poll вернёт UNKNOWN."""
    return _current_state


def get_provider_status() -> dict[str, bool]:
    """Снэпшот состояния каждого probe target."""
    return dict(_provider_status)


class NetworkWatchdog:
    """Фоновый поллер. Хранит реф на bus для публикации событий."""

    def __init__(
        self,
        bus,  # noqa: ANN001 — circular import чтобы avoid
        poll_interval: float = POLL_INTERVAL_SEC,
        probe_timeout: float = PROBE_TIMEOUT_SEC,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._probe_timeout = probe_timeout
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=self._probe_timeout)
        self._stop_event.clear()
        # Первая проверка — сразу, без ожидания poll_interval
        await self._probe_and_publish()
        self._task = asyncio.create_task(self._loop(), name="network-watchdog")
        logger.info("network_watchdog_started", interval_sec=self._poll_interval)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("network_watchdog_stopped")

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
                return
            except asyncio.TimeoutError:
                pass  # обычный tick
            await self._probe_and_publish()

    async def _probe_and_publish(self) -> None:
        global _current_state, _provider_status

        if self._client is None:
            return

        # Параллельные probe'ы — общий timeout
        results: dict[str, bool] = {}
        async def _probe(name: str, url: str) -> None:
            try:
                # HEAD дешевле GET, но не все API его принимают.
                # api.anthropic.com отвечает 405 на HEAD — для нас это «доступен».
                r = await self._client.head(url, follow_redirects=True)
                results[name] = r.status_code < 500
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RequestError):
                results[name] = False
            except Exception as e:  # noqa: BLE001
                logger.warning("network_probe_unexpected", target=name, error=str(e)[:100])
                results[name] = False

        await asyncio.gather(*[_probe(n, u) for n, u in PROBE_TARGETS.items()])

        up_count = sum(1 for ok in results.values() if ok)
        total = len(results)
        if up_count == total:
            new_state = NetworkState.ONLINE
        elif up_count == 0:
            new_state = NetworkState.OFFLINE
        else:
            new_state = NetworkState.PARTIAL

        state_changed = new_state != _current_state
        _current_state = new_state
        _provider_status = results

        if state_changed:
            logger.info(
                "network_state_changed",
                state=new_state.value,
                providers=results,
            )
            try:
                await self._bus.publish(
                    JarvisEvent(
                        type=EventType.SYSTEM,
                        source="network-watchdog",
                        data={
                            "kind": "network_state",
                            "state": new_state.value,
                            "providers": results,
                        },
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("network_state_publish_failed", error=str(e))

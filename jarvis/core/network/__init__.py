"""Network connectivity watchdog (C16 OfflineMode).

Поллит ключевые endpoint'ы (Anthropic, Yandex), публикует SYSTEM event
с состоянием в bus. Используется Router'ом и Skills для решений
"вызывать ли сейчас облачный API".
"""

from core.network.connectivity import (
    NetworkState,
    NetworkWatchdog,
    get_network_state,
    get_provider_status,
)

__all__ = [
    "NetworkState",
    "NetworkWatchdog",
    "get_network_state",
    "get_provider_status",
]

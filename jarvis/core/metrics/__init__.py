"""Metrics collection: latency, counters, provider hit-rate.

Use:
    from core.metrics import metrics

    # Точечный замер latency
    with metrics.timed("stt", provider="yandex"):
        text = await stt.transcribe(audio)

    # Прямая запись counter'а
    metrics.record("barge_in", channel="local_voice")

    # MetricsCollector сам подписан на bus.* и трекает USER_INPUT -> ASSISTANT_REPLY
    # для end-to-end latency.
"""

from core.metrics.collector import MetricsCollector, metrics

__all__ = ["MetricsCollector", "metrics"]

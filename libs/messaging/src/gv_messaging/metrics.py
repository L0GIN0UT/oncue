"""Prometheus-метрики обмена сообщениями (общие для всех сервисов)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

OUTBOX_PUBLISHED = Counter(
    "gv_outbox_published_total", "События, опубликованные relay из outbox", ["topic"]
)
OUTBOX_PUBLISH_ERRORS = Counter(
    "gv_outbox_publish_errors_total", "Ошибки публикации из outbox", ["topic"]
)
OUTBOX_LAG_SECONDS = Gauge(
    "gv_outbox_oldest_unpublished_age_seconds", "Возраст самого старого неопубликованного события"
)

EVENTS_CONSUMED = Counter(
    "gv_events_consumed_total", "Обработанные события", ["group", "event_type", "outcome"]
)
EVENT_HANDLE_SECONDS = Histogram(
    "gv_event_handle_seconds", "Время обработки события", ["group", "event_type"]
)

TASKS_PROCESSED = Counter("gv_tasks_processed_total", "Обработанные задачи", ["queue", "outcome"])

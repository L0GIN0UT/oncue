"""gv_messaging — доменные события и фоновые задачи.

* ``envelope`` — формат события;
* ``outbox`` / ``inbox`` — надёжная публикация и дедупликация;
* ``consumer`` — обработка событий (ретраи, DLQ) независимо от транспорта;
* ``kafka`` / ``rabbit`` — адаптеры Redpanda и RabbitMQ.
"""

from gv_messaging.consumer import EventHandler, EventHandlerRegistry, EventProcessor, Outcome
from gv_messaging.envelope import EventEnvelope, dlq_topic, topic_name
from gv_messaging.inbox import Inbox, inbox_table
from gv_messaging.outbox import OutboxRelay, OutboxWriter, RelaySettings, outbox_table
from gv_messaging.ports import EventPublisher, PermanentError, TaskQueue
from gv_messaging.retry import RetryPolicy

__all__ = [
    "EventEnvelope",
    "EventHandler",
    "EventHandlerRegistry",
    "EventProcessor",
    "EventPublisher",
    "Inbox",
    "OutboxRelay",
    "OutboxWriter",
    "Outcome",
    "PermanentError",
    "RelaySettings",
    "RetryPolicy",
    "TaskQueue",
    "dlq_topic",
    "inbox_table",
    "outbox_table",
    "topic_name",
]

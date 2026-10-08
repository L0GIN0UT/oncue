# Справочник библиотек

Собирается автоматически из докстрингов (`mkdocstrings`). Правила докстрингов: [10-ai-assistant.md](../10-ai-assistant.md) §4.

- [libs/core](core.md) — Базовые классы домена, ошибки, идентификаторы, часы, пагинация, Unit of Work
- [libs/db](db.md) — SQLAlchemy: движок, метаданные сервиса (outbox/inbox), Unit of Work, миграции
- [libs/messaging](messaging.md) — Конверт событий, outbox relay, inbox, потребители, ретраи, Redpanda и RabbitMQ
- [libs/auth](auth.md) — Проверка JWT по JWKS, Principal, зависимости FastAPI require_perm
- [libs/observability](observability.md) — Структурные логи, метрики Prometheus, трейсинг OpenTelemetry
- [libs/service](service.md) — Каркас сервиса: ServiceDefinition, фабрика приложения, health, Problem Details, процессы
- [libs/flags](flags.md) — Фича-флаги (Unleash)
- [libs/testkit](testkit.md) — Общий модуль тестирования

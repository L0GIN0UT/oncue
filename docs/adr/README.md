# Architecture Decision Records

Один файл — одно решение. Принятые ADR не редактируются: при изменении решения создаётся новый ADR со статусом «заменяет NNNN». Шаблон: [0000-template.md](0000-template.md).

| # | Решение |
|---|---|
| [0001](0001-microservices-by-bounded-context.md) | Микросервисы по bounded contexts |
| [0002](0002-hexagonal-architecture.md) | Гексагональная архитектура внутри сервиса |
| [0003](0003-two-brokers.md) | Два брокера: Redpanda для событий, RabbitMQ для задач |
| [0004](0004-outbox-and-idempotency.md) | Transactional outbox и идемпотентные потребители |
| [0005](0005-vps-first-kubernetes-ready.md) | Старт на VPS, целевая платформа — Kubernetes |
| [0006](0006-django-admin-panel.md) | Админ-панель на Django, мутации через admin-API сервисов |
| [0007](0007-billing-disabled-by-flag.md) | Подписочный биллинг, выключенный фича-флагом |
| [0008](0008-plugin-game-engine.md) | Плагинный игровой движок, режимы как конфиг |
| [0009](0009-monorepo.md) | Монорепозиторий |
| [0010](0010-match-room-single-writer.md) | Single-writer для комнат матча |
| [0011](0011-service-definition-composition-root.md) | Composition root через ServiceDefinition вместо DI-контейнера |

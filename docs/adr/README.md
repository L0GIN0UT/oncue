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
| [0012](0012-signed-asset-links-nginx.md) | Подписанные ссылки на ассеты: короткий TTL, проверка на Nginx |
| [0013](0013-admin-catalog-via-admin-api.md) | Админка в этапе 1 читает и пишет через admin-API сервисов |
| [0014](0014-music-sources.md) | Источники музыкальных метаданных и аудио |
| [0015](0015-admin-idempotency-and-asset-event-order.md) | Идемпотентность admin-мутаций и порядок событий ассетов |
| [0016](0016-service-tokens-client-credentials.md) | Сервисные токены через client credentials |
| [0017](0017-frontend-nextjs-bff-read-only.md) | Фронтенд: Next.js в `frontend/`, bff только чтение |
| [0018](0018-guest-sessions-in-identity.md) | Гостевые сессии в identity |
| [0019](0019-solo-sessions-in-postgres.md) | Соло-сессии в Postgres |
| [0020](0020-mode-config-assets-by-params-hash.md) | ModeConfig ↔ ассеты по `params_hash`, очки в Scorer |
| [0021](0021-s3-storage-seaweedfs.md) | S3-хранилище на VPS: SeaweedFS вместо MinIO |
| [0022](0022-image-scan-reporting-not-gating.md) | Скан образов (trivy) — отчётный, не блокирующий |
| [0023](0023-round-draw-answer-formats-end-rules.md) | Состав раунда, форматы ответа и правила завершения — плагины ядра |
| [0024](0024-clue-texts-as-media-assets.md) | Тексты загадок — ассеты media-pipeline, курирование в catalog |
| [0025](0025-dynamic-asset-profiles.md) | Динамические профили ассетов по `ClueAssetsRequested` |
| [0026](0026-reveal-with-other-clue-type.md) | Ревил ассетом другого типа — `clue.reveal` |
| [0027](0027-period-sets-preference-modes-records.md) | Плановые наборы с периодом, режимы предпочтения, рекорды |

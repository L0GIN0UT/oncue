# Архитектура: обзор

> Статус: принято (v1). Решения с обоснованием: [../adr/](../adr/README.md).

## 1. Цели (в порядке приоритета)

1. **Корректность и честность игры:** сервер — единственный источник истины, клиенту нечего «взломать».
2. **Отказоустойчивость:** падение любого некритичного сервиса не останавливает игру (graceful degradation).
3. **Горизонтальная масштабируемость:** узкие места масштабируются независимо.
4. **Расширяемость без правок ядра:** новые режимы, загадки, домены и провайдеры добавляются плагинами и конфигами.
5. **Переносимость:** запуск на одном VPS (docker-compose) и в Kubernetes из одних и тех же образов и конфигов.

## 2. Принципы

| Принцип | Что значит на практике |
|---|---|
| Сервисы по bounded contexts (DDD) | Граница сервиса = граница домена, а не таблицы. Критерии выделения: [services.md](services.md#критерии-выделения-сервиса) |
| Database per service | Сервис владеет своими данными. Чужие данные доступны только через API, события или локальные проекции. Никаких JOIN между сервисами |
| Async by default | Межсервисное взаимодействие — доменные события. Синхронные вызовы только там, где ответ нужен прямо сейчас ([communication.md](communication.md)) |
| Гексагональная архитектура внутри | domain → application → infrastructure/api, зависимости только внутрь ([../03-code-standards.md](../03-code-standards.md)) |
| Конфигурация как данные | Режимы, уровни, очки, лимиты, квесты, цены и флаги лежат в БД с версионированием ([../02-game-engine.md](../02-game-engine.md)) |
| 12-factor | Конфиг через env, stateless-процессы, логи в stdout, одинаковые образы во всех окружениях |
| Идемпотентность | Каждая мутация с побочным эффектом принимает `Idempotency-Key` или `opId` |
| Observability first | Трейсы, метрики и логи с correlation-id закладываются с первого сервиса |

## 3. Схема

```
                         ┌──────────────┐        ┌─────────────┐
   Web / PWA / TG ──────▶│   Gateway    │───────▶│  BFF (web)  │
        ▲                │ TLS, JWT,    │        └──────┬──────┘
        │ WS             │ rate-limit   │               │ REST (internal)
   ┌────┴──────┐         └──────┬───────┘               ▼
   │Centrifugo │◀── publish ────┼─────────── доменные сервисы ───────────────┐
   └───────────┘                │                                            │
                                ▼                                            │
 ┌──────────┐ ┌─────────┐ ┌──────────────┐ ┌──────┐ ┌───────┐ ┌────────┐     │
 │ identity │ │ catalog │ │media-pipeline│ │ game │ │ match │ │ rating │     │
 └──────────┘ └─────────┘ └──────────────┘ └──────┘ └───────┘ └────────┘     │
 ┌─────────────┐ ┌─────────┐ ┌──────────────┐ ┌─────────────┐                │
 │ collections │ │ library │ │ integrations │ │ progression │                │
 └─────────────┘ └─────────┘ └──────────────┘ └─────────────┘                │
 ┌──────────┐ ┌────────┐ ┌───────────────┐        ┌──────────────────┐       │
 │ payments │ │ social │ │ notifications │        │ admin (Django)   │───────┘
 └──────────┘ └────────┘ └───────────────┘        └──────────────────┘
        │            │ события (outbox)                   │ admin-API сервисов
        ▼            ▼
 ┌────────────────────────────┐   ┌──────────────────────────┐
 │ Redpanda (доменные события)│   │ RabbitMQ (очереди задач) │
 └────────────────────────────┘   └──────────────────────────┘

 Хранилища: PostgreSQL (по БД на сервис) · Redis · OpenSearch · S3/MinIO + CDN · ClickHouse (позже)
 Наблюдаемость: OpenTelemetry → трейсы · Prometheus/Grafana · ELK · Sentry
```

## 4. Состав системы

- **Edge (2):** gateway, bff-web.
- **Доменные сервисы (13):** identity, catalog, media-pipeline, game, match, rating, collections, library, integrations, progression, payments, social, notifications.
- **Операционные (1):** admin (Django).
- **Отложено:** recommendations, analytics-ingest.

Подробные карточки: [services.md](services.md).

## 5. Ключевые решения

| # | Решение | ADR |
|---|---|---|
| 1 | Микросервисы по bounded contexts, 13 доменных сервисов | [0001](../adr/0001-microservices-by-bounded-context.md) |
| 2 | Гексагональная архитектура внутри сервиса | [0002](../adr/0002-hexagonal-architecture.md) |
| 3 | Два брокера: Redpanda для событий, RabbitMQ для задач | [0003](../adr/0003-two-brokers.md) |
| 4 | Transactional outbox + идемпотентные потребители | [0004](../adr/0004-outbox-and-idempotency.md) |
| 5 | Старт на VPS (compose), целевая платформа — Kubernetes | [0005](../adr/0005-vps-first-kubernetes-ready.md) |
| 6 | Админ-панель на Django, мутации только через admin-API сервисов | [0006](../adr/0006-django-admin-panel.md) |
| 7 | Подписочный биллинг, выключенный фича-флагом | [0007](../adr/0007-billing-disabled-by-flag.md) |
| 8 | Плагинный игровой движок, режимы как конфиг | [0008](../adr/0008-plugin-game-engine.md) |
| 9 | Монорепозиторий | [0009](../adr/0009-monorepo.md) |
| 10 | Single-writer для комнат матча | [0010](../adr/0010-match-room-single-writer.md) |
| 11 | Composition root через ServiceDefinition | [0011](../adr/0011-service-definition-composition-root.md) |
| 12 | Подписанные ссылки на ассеты (Nginx, TTL 60 с) | [0012](../adr/0012-signed-asset-links-nginx.md) |
| 13 | Админка этапа 1 через admin-API | [0013](../adr/0013-admin-catalog-via-admin-api.md) |
| 14 | Источники музыкальных метаданных и аудио | [0014](../adr/0014-music-sources.md) |
| 15 | Идемпотентность admin-мутаций в транзакции мутации, порядок событий ассетов | [0015](../adr/0015-admin-idempotency-and-asset-event-order.md) |
| 16 | Сервисные токены через client credentials (identity, `aud`/scope) | [0016](../adr/0016-service-tokens-client-credentials.md) |
| 17 | Фронтенд Next.js в `frontend/`, bff-web только чтение, команды игры — напрямую в game | [0017](../adr/0017-frontend-nextjs-bff-read-only.md) |
| 18 | Гостевые сессии выдаёт identity (`gst = true`, без прав) | [0018](../adr/0018-guest-sessions-in-identity.md) |
| 19 | Соло-сессии в Postgres, Redis — лимиты и кэш | [0019](../adr/0019-solo-sessions-in-postgres.md) |
| 20 | ModeConfig ↔ ассеты по `params_hash`, очки уровней в Scorer | [0020](../adr/0020-mode-config-assets-by-params-hash.md) |

## 6. Технологии

| Слой | Выбор |
|---|---|
| Бэкенд | Python 3.13, FastAPI, SQLAlchemy 2 (async), Pydantic v2, Alembic, uv (workspace) |
| Админка | Django 5 + django-admin (отдельный сервис) |
| Фронтенд | Next.js (App Router), TypeScript, React |
| БД | PostgreSQL 16, PgBouncer |
| Кэш, горячее состояние | Redis 7 (на проде Sentinel, затем Cluster) |
| Поиск | OpenSearch |
| События / задачи | Redpanda (Kafka API) / RabbitMQ (quorum queues) |
| Realtime | Centrifugo |
| Фича-флаги | Unleash (self-hosted), SDK в `libs/flags` |
| Объекты | S3-совместимое хранилище (MinIO на VPS) + CDN |
| Наблюдаемость | OpenTelemetry, Prometheus, Grafana, ELK, Sentry |
| Инфраструктура | Docker, docker-compose (VPS) → Kubernetes + Helm, Terraform, GitHub Actions |

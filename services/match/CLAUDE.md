# match

Онлайн: лобби, комнаты, матчмейкинг, конечный автомат матча, таймеры, реконнект, наблюдение.

- Пакет: `match_service`; запуск: `match api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/match/v1/...` на gateway
- Зависимости: PostgreSQL, Redis, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

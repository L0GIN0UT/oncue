# catalog

Медиа-единицы (треки, фильмы, сериалы, аниме), персоны, франшизы, алиасы, поиск и автокомплит.

- Пакет: `catalog_service`; запуск: `catalog api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/catalog/v1/...` на gateway
- Зависимости: PostgreSQL, Redis, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

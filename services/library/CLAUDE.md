# library

Личная медиатека: плейлисты, «смотреть позже», «просмотрено», «любимое».

- Пакет: `library_service`; запуск: `library api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/library/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

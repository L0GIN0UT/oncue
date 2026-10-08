# social

Друзья, комментарии, жалобы, модерация, шкалы поведения и санкции, приватность.

- Пакет: `social_service`; запуск: `social api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/social/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

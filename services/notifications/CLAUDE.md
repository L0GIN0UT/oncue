# notifications

In-app уведомления, realtime-доставка, e-mail, web-push, отключение по типам.

- Пакет: `notifications_service`; запуск: `notifications api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/notifications/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda, RabbitMQ
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

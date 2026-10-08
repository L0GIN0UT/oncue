# payments

Тарифы и подписки, платёжный провайдер, вебхуки, entitlements. Выключен флагом billing.enabled.

- Пакет: `payments_service`; запуск: `payments api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/payments/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

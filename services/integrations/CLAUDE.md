# integrations

Адаптеры внешних провайдеров, шифрованные токены, синхронизация и импорт.

- Пакет: `integrations_service`; запуск: `integrations api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/integrations/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda, RabbitMQ
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

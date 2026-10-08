# media_pipeline

Генерация ассетов загадок по уровням, хранение в S3, подписанные ссылки, проверка UGC-изображений.

- Пакет: `media_pipeline_service`; запуск: `media_pipeline api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: нет (только internal)
- Зависимости: PostgreSQL, Redpanda, RabbitMQ
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

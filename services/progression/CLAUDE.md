# progression

Опыт и уровни, валюта (журнал проводок), задания, бейджи, магазин, инвентарь, оформление профиля.

- Пакет: `progression_service`; запуск: `progression api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/progression/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

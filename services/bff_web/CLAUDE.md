# bff_web

Backend-for-frontend: агрегация данных под экраны веб-клиента.

- Пакет: `bff_web_service`; запуск: `bff_web api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/web/v1/...` на gateway
- Зависимости: , Redis
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

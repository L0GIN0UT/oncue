# collections

Пользовательские и кураторские сборники: конструктор, черновики, каталог, лайки, избранное.

- Пакет: `collections_service`; запуск: `collections api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/collections/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

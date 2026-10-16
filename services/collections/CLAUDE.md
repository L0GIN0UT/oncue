# collections

Пользовательские и кураторские сборники: конструктор, черновики, каталог, лайки, избранное.

- Пакет: `collections_service`; запуск: `collections api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/collections/v1/...` на gateway
- Зависимости: PostgreSQL, Redis (лимиты частоты, этап 3b), Redpanda
- Статус: этап 3b, шаг 0a — контракт API (заглушки 501, `api/schemas.py`), события и настройки; домен — `docs/plans/stage-3b.md` §5–6

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->

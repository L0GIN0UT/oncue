# identity

Аккаунты, вход, сессии и токены, привязка провайдеров, слияние и удаление аккаунтов, роли и права.

- Пакет: `identity_service`; запуск: `identity api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/identity/v1/...` на gateway
- Зависимости: PostgreSQL, Redis, Redpanda
- Статус: заглушка этапа 0 (health, метрики, outbox/inbox). Домен — по roadmap.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->
- Cookie `gv_access`/refresh: `HttpOnly; Secure; SameSite=Lax`, а мутации с cookie-аутентификацией — только с CSRF-токеном или проверкой Origin (libs/auth принимает cookie на любом методе; итог ревью этапа 0).
- JWKS отдаётся с несколькими ключами при ротации; старый ключ живёт до истечения выданных токенов.

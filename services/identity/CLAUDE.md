# identity

Аккаунты, вход, сессии и токены, привязка провайдеров, слияние и удаление аккаунтов, роли и права.

- Пакет: `identity_service`; запуск: `identity api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/identity/v1/...` на gateway; JWKS — `/.well-known/jwks.json`; internal — `/internal/v1/admin-sessions[/refresh]`; admin — `/admin/v1/users|roles`
- Зависимости: PostgreSQL (citext), Redis, Redpanda
- Статус: этап 1 — регистрация, вход по e-mail/никнейму, сессии, привязка Яндекс ID, роли (`docs/plans/stage-1.md` §4)
- Граф зависимостей собирает `wiring.py` (вне слоёв, как `main.py`): api и infrastructure друг друга не импортируют.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->
- Cookie: `gv_access` (Path=/), `gv_refresh` (Path=`GV_COOKIES__REFRESH_PATH`, по умолчанию `/api/identity/v1/auth`) — `HttpOnly; Secure; SameSite=Lax`; `gv_csrf` без HttpOnly, перевыдаётся при входе и refresh. Cookie-мутации — Origin из `GV_PUBLIC_ORIGINS` **и** `X-CSRF-Token` (gv_auth.csrf; пустой allowlist = отказ); login/register/forgot/reset — `require_same_origin`.
- Denylist `sid` (`auth:revoked_sid:<sid>`, TTL = access TTL) — **fail-open**: при недоступном Redis токен принимается, растёт `identity_revocation_fail_open_total`, лог `auth.revocation_check_failed`. Окно риска ≤ access TTL (15 мин), refresh при этом отозван в БД. `GV_REDIS_KEY_PREFIX` должен совпадать у identity и сервисов-потребителей.
- Лимитер входа тоже fail-open (`identity_redis_fail_open_total{component="rate_limit"}`): перебор сдерживает argon2id.
- Ключи подписи — только из `GV_JWT__SIGNING_KEYS` (JSON `[{kid, private_pem, active, not_after?}]`, ровно один active, Ed25519/P-256). Ротация: новый ключ active, старый — с `not_after` ≥ момент переключения + access TTL. Без ключей сервис стартует только в local/ci (временный ключ).
- Повторное использование refresh отзывает всю сессию: две вкладки, одновременно обновляющие сессию одним токеном, разлогинятся — фронт/bff должен сериализовать refresh.
- Регистрация с занятым e-mail отвечает как успех (письмо «уже есть аккаунт»); занятый никнейм — 409 (ник публичен).
- Яндекс: только привязка из профиля и вход через уже привязанный аккаунт; e-mail провайдера не запрашивается и ни с чем не связывается. `state` одноразовый (GETDEL), привязан к браузеру (cookie `gv_oauth`) и, для привязки, к `sid`.
- Admin-сессии: только пароль + `admin.access`, лимит только по аккаунту (IP-лимит — на стороне admin). Нельзя выдать/снять роль с правами шире своих; матрицу нельзя изменить с потерей своего `roles.manage`.
- Удаление аккаунта: ник/e-mail → `~deleted~<hex>` / `<hex>@deleted.invalid`, согласия остаются.

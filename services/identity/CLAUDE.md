# identity

Аккаунты, вход, сессии и токены, привязка провайдеров, слияние и удаление аккаунтов, роли и права.

- Пакет: `identity_service`; запуск: `identity api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/identity/v1/...` на gateway; JWKS — `/.well-known/jwks.json`; internal — `/internal/v1/admin-sessions[/refresh|/revoke]` (сервисный токен, scope `admin_sessions`), `/internal/v1/service-tokens`; admin — `/admin/v1/users|roles`
- Зависимости: PostgreSQL (citext), Redis, Redpanda
- Статус: этап 1 — регистрация, вход по e-mail/никнейму, сессии, привязка Яндекс ID, роли (`docs/plans/stage-1.md` §4)
- Граф зависимостей собирает `wiring.py` (вне слоёв, как `main.py`): api и infrastructure друг друга не импортируют.

## Особенности
<!-- Дописывать сюда неочевидное: инварианты, подводные камни, решения. 10–15 строк максимум. -->
- Cookie: `gv_access` (Path=/), `gv_refresh` (Path=`GV_COOKIES__REFRESH_PATH`, по умолчанию `/api/identity/v1/auth`) — `HttpOnly; Secure; SameSite=Lax`; `gv_csrf` без HttpOnly, перевыдаётся при входе и refresh. Cookie-мутации — Origin из `GV_PUBLIC_ORIGINS` **и** `X-CSRF-Token` (gv_auth.csrf; пустой allowlist = отказ); login/register/forgot/reset — `require_same_origin`.
- Denylist `sid` (`auth:revoked_sid:<sid>`, TTL = access TTL + `GV_JWT__LEEWAY_S`) — **fail-open**: при недоступном Redis токен принимается, растёт `identity_revocation_fail_open_total`, лог `auth.revocation_check_failed`. Окно риска ≤ access TTL (15 мин), refresh при этом отозван в БД. `GV_REDIS_KEY_PREFIX` и leeway верификатора (не больше нашего) должны совпадать у identity и сервисов-потребителей.
- Лимиты проверки пароля (`PasswordAuthenticator`, `GV_LOGIN_LIMIT__*`): попытка засчитывается атомарно (INCR) **до** argon2; ключи — по IP, по (аккаунт, подсеть /24·/64) и мягкий общий по аккаунту; аккаунт = `user.id` (все варианты логина и Unicode-двойники — один счётчик). Успех обнуляет счётчик подсети и прощает попытку в общем. Лимитер **fail-open** (`identity_redis_fail_open_total{component="rate_limit"}`): перебор сдерживает argon2id, а его параллелизм — семафор `GV_ARGON2__MAX_CONCURRENCY`.
- Повторная аутентификация паролем (тот же лимитер): привязка (`password` в теле start) и отвязка сервиса, `DELETE /me`; в схеме поле необязательное (совместимость), без него — 422 `auth.password_required`. О привязке/отвязке и смене пароля (со списком привязок) уходит письмо.
- Письма на один адрес (register/forgot) — лимит `GV_EMAIL_LIMIT__*` по хэшу адреса; превышение молча гасит письмо (и выдачу токена сброса). Новый токен из письма гасит прежние той же цели, погашение — тоже.
- Ключи подписи — только из `GV_JWT__SIGNING_KEYS` (JSON `[{kid, private_pem, active, not_after?}]`, ровно один active, Ed25519/P-256). Ротация: новый ключ active, старый — с `not_after` ≥ момент переключения + access TTL. Без ключей сервис стартует только в local/ci (временный ключ).
- Повторное использование refresh отзывает всю сессию: две вкладки, одновременно обновляющие сессию одним токеном, разлогинятся — фронт/bff должен сериализовать refresh.
- Регистрация с занятым e-mail отвечает как успех (письмо «уже есть аккаунт»); занятый никнейм — 409 (ник публичен).
- Яндекс: только привязка из профиля и вход через уже привязанный аккаунт; e-mail провайдера не запрашивается и ни с чем не связывается. `state` одноразовый (GETDEL), привязан к браузеру (cookie `gv_oauth`) и, для привязки, к `sid`.
- Admin-сессии: только пароль + `admin.access`; IP клиента приходит от admin в `client_ip` (без него — только лимит аккаунта). Ответ несёт `identity_version` (= `version` в `UserRolesChanged`). Admin-мутации: обязательный `Idempotency-Key` (таблица `idempotency_keys`, та же транзакция), `reason` 3–500. Понижение прав (роли или матрица) отзывает все сессии пользователя + denylist. Нельзя выдать/снять роль с правами шире своих; матрицу нельзя изменить с потерей своего `roles.manage`.
- Удаление аккаунта: ник/e-mail → `~deleted~<hex>` / `<hex>@deleted.invalid`, согласия остаются.
- Сервисные токены (D13): клиенты — `GV_SERVICE_CLIENTS` (JSON, только sha256 секретов; `aud = guessverse` запрещён), `IssueServiceToken`: лимит `GV_SERVICE_TOKEN_LIMIT__PER_CLIENT_PER_MIN` по `client_id` (каждая попытка, fail-open) → `hmac.compare_digest` (и для неизвестного клиента) → allowlist `audience`. Claims: `iss, aud, sub=svc:<id>, scope, iat, exp, jti` (TTL `GV_SERVICE_TOKEN__TTL_S`), без `sid/roles/perms`. Internal-ручки проверяет отдельный `JwtVerifier` с `aud = internal_audience` (`identity`).

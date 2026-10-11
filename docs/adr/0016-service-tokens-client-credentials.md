# 0016. Сервисные токены через client credentials

- **Статус:** принято
- **Дата:** 2026-10-11

## Контекст
На этапе 1 internal API (`/internal/v1/*`) был закрыт только сетью: `media_pipeline /internal/v1/asset-links` принимал любой `subject`, а `identity /internal/v1/admin-sessions*` — любой запрос из внутренней сети (хвост D8 этапа 1). На этапе 2 game начинает вызывать media-pipeline на каждый уровень загадки, поэтому вызывающий сервис нужно аутентифицировать и ограничить по правам до первого вызова. Решение D13 [плана этапа 2](../plans/stage-2.md#1-сквозные-решения-утверждены).

## Решение
**Выдача** — identity, `POST /internal/v1/service-tokens {client_id, client_secret, audience}` → `{access_token, token_type: "Bearer", expires_in}`:

- клиенты и их права — env identity `GV_SERVICE_CLIENTS` (JSON `[{client_id, secret_sha256, audiences: {<сервис>: [scope…]}}]`); identity хранит только sha256 секрета, `aud = guessverse` в списке запрещён;
- проверка: `hmac.compare_digest` по sha256 (и для неизвестного клиента, чтобы время ответа не выдавало существование клиента) → allowlist `audience` → выдача; ошибки — 401 `auth.client_invalid`, 403 `auth.audience_forbidden`;
- токен — JWT теми же ключами и JWKS, что и пользовательский: `iss`, `aud = <вызываемый сервис>`, `sub = svc:<client_id>`, `scope`, `iat`, `exp`, `jti`; без `sid`, `roles`, `perms`; TTL `GV_SERVICE_TOKEN__TTL_S` (600 с).

**Лимиты выдачи** (уточнение плана; fail-open, как остальные лимитеры identity) раздельные, чтобы чужие неверные секреты не отрезали честного клиента:

- неудачи — `GV_SERVICE_TOKEN_LIMIT__FAILURES_PER_CLIENT_IP_PER_MIN` (30) по паре (`client_id`, IP);
- успешные выдачи — `GV_SERVICE_TOKEN_LIMIT__SUCCESS_PER_CLIENT_PER_MIN` (600) по `client_id`.

План предполагал один лимит `GV_SERVICE_TOKEN_LIMIT__PER_CLIENT_PER_MIN`: тогда перебор секретов с любого адреса блокировал бы выдачу настоящему клиенту.

**Вызывающий** — `gv_auth.ServiceTokenProvider` (сборка — `gv_service.build_service_token_provider`, учётные данные `GV_SERVICE_AUTH__*`): кэш токена, обновление на `refresh_ratio` TTL (50 %), single-flight; при отказе identity — старый токен до `exp` с паузой между повторами; после 401 от вызываемого кэш сбрасывается. Секрет и токен не логируются.

**Вызываемый** — `ServiceAuthenticator(verifier).require_service(*scopes)` с отдельным `JwtVerifier`, у которого `audience` = `GV_INTERNAL_AUTH__AUDIENCE` (по умолчанию имя сервиса). Пользовательский JWT (`aud = guessverse`) на internal → 401, сервисный на public → 401, токен без нужного scope → 403.

**Применение на этапе 2:**

| Клиент | Вызываемый | scope |
|---|---|---|
| game | media_pipeline `/internal/v1/*` | `assets.link` |
| admin | identity `/internal/v1/admin-sessions*` | `admin_sessions` |

media-pipeline пишет `client_id` вызывающего в журнал `link_issuance`.

## Альтернативы
- **mTLS.** На VPS требует свою CA и ротацию сертификатов в compose. Возвращаемся к нему после переезда на service mesh в Kubernetes.
- **Общий HMAC-секрет.** Нет срока жизни, `aud` и scope; нужен отдельный секрет на каждую пару сервисов.
- **`private_key_jwt`** (каждый сервис подписывает токен сам). Нет зависимости от identity, но публичные ключи всех вызывающих приходится раздавать всем вызываемым.

## Последствия
- (+) Internal API закрыт и сетью, и токеном с `aud`/scope; хвост D8 этапа 1 закрыт.
- (+) Один механизм проверки (`JwtVerifier`, JWKS) для пользовательских и сервисных токенов.
- (−) identity участвует в цепочке вызова: без него новый токен не получить. Смягчение — кэш и старый токен до `exp` (до 10 мин работы без identity).
- Новый вызов internal-ручки = клиент в `GV_SERVICE_CLIENTS` + секрет в `GV_SERVICE_AUTH__CLIENT_SECRET` вызывающего (`gv dev secrets`) + `require_service(scope)` на ручке.

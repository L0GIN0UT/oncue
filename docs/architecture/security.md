# Безопасность

> Статус: принято (v1; §3, §5, §7 дополнены по итогам этапа 2).

## 1. Аутентификация

- **identity** выпускает:
  - **access JWT** (15 мин, подпись ES256/EdDSA, claims: `sub`, `roles`, `perms`, `sanctions`, `plan`, `exp`, `jti`);
  - **refresh token** (30 дней, opaque, хранится хэшем в БД, ротация при каждом использовании, обнаружение повторного использования → отзыв всей сессии).
- Веб: оба токена в `HttpOnly; Secure; SameSite=Lax` cookie, CSRF-токен для мутаций. Telegram Mini App и мобильные клиенты — `Authorization: Bearer`.
- Публичные ключи — через JWKS (`/.well-known/jwks.json`). Gateway и сервисы проверяют подпись локально, без обращения к identity.
- «Выйти везде» — отзыв refresh-токенов + denylist сессии (`sid`) в Redis до истечения access-токенов.
- Вход через OAuth-провайдеры (Яндекс ID, далее — по мере подключения), PKCE, проверка `state`/`nonce`.

## 2. Авторизация: роли и права

**RBAC + права.** Роль — это набор прав. Проверка в коде идёт **по праву, а не по роли**. Тогда добавление роли не требует правок кода.

| Роль | Описание |
|---|---|
| `user` | Обычный пользователь |
| `moderator` | Модерация контента и пользователей |
| `admin` (суперпользователь) | Полный доступ, управление ролями и конфигами |

Примеры прав: `comments.delete_any`, `users.sanction`, `catalog.block_item`, `collections.unpublish`, `config.modes.publish`, `roles.assign`, `billing.manage`, `match.spectate`.

- Матрица «роль → права» хранится в identity и меняется через админку (только `admin`).
- Сервисы проверяют права из JWT зависимостью FastAPI `require_perm("...")` (общая библиотека `libs/auth`).
- Проверки владения ресурсом («автор сборника») — в application-слое сервиса.
- Уровни доверия по игровому уровню (комментарии с 10-го уровня и т.п.) — это **не роли**, а правила из конфига progression/social.

## 3. Межсервисное доверие

- Internal API доступен только из внутренней сети (docker network / k8s NetworkPolicy).
- **Сервисный токен** (с этапа 2, [ADR 0016](../adr/0016-service-tokens-client-credentials.md)): client credentials в identity (`POST /internal/v1/service-tokens`), JWT теми же ключами и JWKS, `aud` = вызываемый сервис, `sub = svc:<client_id>`, `scope`, TTL 600 с, без `sid`/`roles`/`perms`. Клиенты и scope — `GV_SERVICE_CLIENTS` в env identity (только sha256 секретов). Вызываемый проверяет токен отдельным `JwtVerifier` со своим `audience` и `require_service(scope)`. Пользовательский JWT на internal → 401, сервисный на public → 401, без scope → 403.
- Выдача ограничена раздельно: неудачи — по (`client_id`, IP), успехи — по `client_id`, чтобы перебор чужих секретов не отрезал честного клиента.
- Позже — mTLS через service mesh (Kubernetes).
- Admin-API требует пользовательский JWT с нужными правами **и** запрос от admin-сервиса.

## 4. Секреты

- Ни одного секрета в репозитории и образах. На VPS — `.env` с правами 600 + SOPS (age) для зашифрованных файлов в репо. В k8s — External Secrets / Sealed Secrets.
- OAuth-токены провайдеров у integrations хранятся с шифрованием на уровне приложения (envelope encryption, ключ из KMS/секрета, ротация).
- Ключи подписи JWT ротируются, старый ключ живёт в JWKS до истечения выданных токенов.

## 5. Защита игры (античит)

- Сервер — источник истины: клиент никогда не получает правильный ответ до ревила.
- Ассеты выдаются **по уровню**: подписанная ссылка на конкретный уровень с коротким TTL (вместо одноразовой — см. [ADR 0012](../adr/0012-signed-asset-links-nginx.md)). Чёткий кадр или полный отрывок раньше времени получить нельзя.
- Имена файлов ассетов не содержат id контента (хэш), метаданные вырезаются (EXIF, ID3).
- В lite-режиме варианты отдаются с непрозрачными токенами, правильный вариант не вычисляется на клиенте.
- Таймеры и скорость считает сервер (`serverNow`, `phaseEndsAt`).
- `expectedIndex` / `version` в каждой команде защищают от рассинхронизации и повторов.
- Rate-limit на guess и автокомплит. Детектор аномалий (слишком быстрые точные ответы) → флаг для модерации.
- Реализация соло-игры этапа 2 — [§7](#7-реализация-этапа-1), «Этап 2».

## 6. Защита данных и приложения

- Валидация всех входных данных (Pydantic), лимиты размеров, санитизация UGC-текста.
- UGC-изображения: проверка типа по содержимому, перекодирование (вырезает полезную нагрузку), лимиты размеров, модерационная очередь.
- Параметризованные запросы только через ORM или core.
- Заголовки безопасности (CSP, HSTS, X-Content-Type-Options) на gateway.
- Аудит-лог всех действий модераторов и администраторов (admin + сервисы).
- Персональные данные: минимизация, удаление и анонимизация по `UserDeleted`, экспорт данных по запросу.
- Зависимости: Dependabot/Renovate, `pip-audit`, сканирование образов (Trivy) в CI.

## 7. Реализация этапа 1

Детали и параметры — в `services/<name>/CLAUDE.md` («Особенности») и коде; здесь — перечень мер.

| Мера | Где |
|---|---|
| Лимиты проверки пароля: по IP, по паре (`user.id`, подсеть /24 · /64) и мягкий общий по аккаунту. Попытка учитывается атомарно **до** проверки пароля (argon2). Все варианты логина одного аккаунта — один счётчик. Параллелизм argon2 ограничен | identity (`GV_LOGIN_LIMIT__*`, `GV_ARGON2__MAX_CONCURRENCY`), [план §4](../plans/stage-1.md#4-поток-a--identity-и-libsauth-зона-повышенного-контроля) |
| Повторная аутентификация паролем для привязки и отвязки внешних аккаунтов и удаления аккаунта; письмо о привязке, отвязке и смене пароля | identity |
| Понижение прав (роли или матрица) отзывает все сессии пользователя и заносит их в denylist | identity |
| Одноразовые коды из писем вместо ссылок: один активный код на (субъект, назначение), попытка списывается до сравнения, дневной потолок неудач, пустышка для несуществующего адреса; доверенные устройства (`gv_device`); лимит писем на адрес (`GV_EMAIL_LIMIT__*`) | identity, [ADR 0041](../adr/0041-email-codes-trusted-devices.md) |
| CSRF для cookie-мутаций: `Origin` из allowlist **и** double-submit `X-CSRF-Token` = cookie `gv_csrf`; login/register — проверка same-origin | `libs/auth` (`gv_auth.csrf`) |
| Admin: refresh и access токены identity в Django-сессии хранятся только зашифрованными (Fernet); права — из проверенного по JWKS токена | admin (`core.session_crypto`) |
| Admin: IP allowlist обязателен вне local/ci (без него процесс не стартует); лимит попыток входа по IP сверх лимитов identity | admin (`GV_ADMIN__IP_ALLOWLIST`, `GV_ADMIN__LOGIN_ATTEMPTS_*`) |
| Admin-мутации: `Idempotency-Key`, `reason`, `admin.access` + конкретное право, аудит | [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md) |
| Ссылки на ассеты: подпись nginx `secure_link`, TTL 60 с, блокировка единицы сразу удаляет объекты | [ADR 0012](../adr/0012-signed-asset-links-nginx.md), [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md) |
| Gateway: TLS 1.2/1.3, HSTS на 443, `/metrics` только на внутреннем порту 9100, `/internal` и `/admin` закрыты | `infra/nginx/nginx.conf`, [07-infrastructure.md](../07-infrastructure.md#8-переменные-сертификаты-и-порты-этап-1) |

**Принятые риски:**

- Лимитеры входа и denylist `sid` при отказе Redis работают **fail-open** (с метрикой и логом). Перебор сдерживает argon2id, окно отозванного access-токена — не больше access TTL.
- Лимит выдачи ссылок — **fail-closed** (503 с `Retry-After`).
- Internal API на этапе 1 был закрыт только сетью — закрыто на этапе 2 сервисными токенами ([ADR 0016](../adr/0016-service-tokens-client-credentials.md)).

### Этап 2: соло-игра, гости, фронтенд

| Мера | Где |
|---|---|
| Сервисные токены: game → media_pipeline (`assets.link`), admin → identity (`admin_sessions`); провайдер кэширует токен, секрет и токен не логируются; `client_id` вызывающего — в журнале `link_issuance` | identity, `libs/auth` (`gv_auth.service`), `libs/service` (`GV_SERVICE_AUTH__*`), [ADR 0016](../adr/0016-service-tokens-client-credentials.md) |
| Гостевые сессии: выдаёт identity (`POST /v1/auth/guest`, `require_same_origin`, лимит по IP `GV_GUEST__PER_IP_PER_HOUR`), токен `gst = true` без прав; `require_user` → 403 `auth.guest_forbidden`; гость не проходит admin-API и internal; вход отзывает гостевую сессию; истёкшие удаляет `guest_sessions.purge` | identity, `libs/auth`, [ADR 0018](../adr/0018-guest-sessions-in-identity.md) |
| Cookie `gv_access` живёт как refresh-сессия, срок access — по `exp` (401 `auth.token_expired` → refresh). Refresh на фронте — single-flight внутри вкладки и между вкладками (Web Locks), чтобы не сработало обнаружение повторного использования | identity, `frontend/src/shared/api/refresh.ts` |
| Античит game (D11): до ревила в снимке и ответах нет `media_item_id`, названия и `asset_set_id` (component-тест обходит JSON рекурсивно); ссылка на ассет — только для уровня ≤ текущего активного раунда своей сессии (`subject = sess:<id>`, TTL 60 с), иначе 403 `game.level_locked`; ревил и финальный кадр — только у завершённого раунда; `Cache-Control: no-store` | game, [ADR 0012](../adr/0012-signed-asset-links-nginx.md) |
| IDOR: сессия, ассет и итог доступны только своему субъекту (`player_kind` + `player_id` из `Principal`); чужая и несуществующая сессия — одинаково 404 | game |
| Целостность хода: строка сессии под `FOR UPDATE`, `expected_version` (409 `game.desync`), `Idempotency-Key` в транзакции хода, число попыток ограничено правилами раунда; одна попытка дейлика — уникальный индекс БД | game, [ADR 0019](../adr/0019-solo-sessions-in-postgres.md) |
| Лимиты game по субъекту (ходы, ассеты, старт сессий) и старт гостевых сессий по IP (`GV_GAME_LIMITS__*`, fail-open с метрикой); точный ответ на уровне 1 быстрее `GV_GAME__FAST_EXACT_MS` — флаг в лог | game |
| CSRF: мутации game с cookie — Origin из allowlist + `X-CSRF-Token`; bff-web без мутаций | game, `libs/auth`, [ADR 0017](../adr/0017-frontend-nextjs-bff-read-only.md) |
| Admin-API game: `admin.access` + `config.modes.*` / `game.daily.*`, `reason`, `Idempotency-Key`, аудит; публикуется только валидный конфиг, тело ограничено `GV_GAME__MODE_CONFIG_MAX_BYTES` | game, admin |
| CSP фронта с nonce на каждый ответ-страницу (`script-src 'self' 'nonce-…' 'strict-dynamic'`, `object-src 'none'`, `frame-ancestors 'none'`); остальные заголовки — gateway (`web-headers.conf`) | `frontend/src/proxy.ts`, `infra/nginx/snippets/web-headers.conf` |
| Безопасный `?next=` после входа: только путь своего origin, управляющие символы и `\` отклоняются (до и после декодирования), иначе `/` — защита от открытого редиректа | `frontend/src/features/auth/AuthForms.tsx` (`safeNext`) |
| Dev-прокси `/api/*`, `/media/*` в обход gateway — только при `GV_E2E=1`; `GV_DEV_API_ORIGIN` без флага — отказ старта | `frontend/src/shared/lib/dev-api.ts`, `instrumentation.ts` |
| ПДн: в событиях game нет e-mail и ника, `UserDeleted` обнуляет `player_id` | game |

**Принятые риски этапа 2:**

- Гость может переиграть дейлик, сбросив cookie; результаты гостей не входят в статистику дня и `DailyCompleted`.
- Лимиты game, выдачи гостевых сессий и сервисных токенов — fail-open (как лимиты входа): попытки ограничены правилами раунда, выдачу ссылок держит fail-closed лимитер media-pipeline.
- Ссылка финального кадра ревила живёт 60 с: если ревил открыт поздно, медиа может не загрузиться.

### Этап 3b: сборники и медиатека (план, шаг 0a)

Меры зафиксированы в [плане](../plans/stage-3b.md) §12 и [ADR 0030](../adr/0030-minimal-ugc-safety-before-moderation.md); реализация — потоки этапа.

| Мера | Где |
|---|---|
| UGC-текст: `gv_text.ugc` — NFKC, удаление управляющих, bidi- и zero-width-символов, длина после нормализации, denylist по «скелету» (похожие символы, диакритика, повторы; `GV_UGC__DENYLIST_PATH`), в названии нет ссылок, `@` и e-mail; коды `ugc.*` | `libs/text`, collections, library |
| Свои эмодзи — только RGI Unicode 16.0 (`gv_text.emoji.is_strict_emoji`), ≤ 12 графем, проверка в collections и media-pipeline | `libs/text`, collections, media_pipeline |
| Права модерации `collections.view|curate|unpublish|restrict_author` (identity `0007`: admin — все, moderator — `view`, `unpublish`); гость не создаёт, не лайкает и не ведёт медиатеку (`auth.guest_forbidden`) | identity, collections, library |
| IDOR: чужой черновик, полка, скрытый сборник — 404; состав сборника — только автору, админу и прошедшему игроку (D6); публичные схемы `extra=forbid` (`kind`, `author_id`, `featured`, `status` — только admin-API) | collections, library |
| CSRF/Origin для cookie-мутаций collections и library (`GV_PUBLIC_ORIGINS`), `Idempotency-Key` у всех мутаций | `libs/auth`, collections, library |

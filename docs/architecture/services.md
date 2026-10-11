# Сервисы

> Статус: принято (v1; карточки game, bff-web, identity и матрица вызовов обновлены по итогам этапа 2). Имена событий здесь — для навигации. Источник истины по схемам — `contracts/events/`.

## Критерии выделения сервиса

Отдельный сервис оправдан, только если выполняется хотя бы одно из условий:

1. **Профиль нагрузки** отличается: CPU-задачи, интенсивное чтение, долгие соединения.
2. **Тип состояния** отличается: stateless-запросы, долгоживущие комнаты, append-only журнал.
3. **Цена ошибки** отличается: деньги, учётные записи.
4. **Темп изменений** или **внешние зависимости** отличаются: нестабильные внешние API.

Если ни одно не выполняется, это модуль внутри существующего сервиса.

## Общие правила для всех сервисов

- Три API-поверхности, разделённые по префиксу и политике доступа:
  - `/v1/...` — публичный API, через gateway;
  - `/internal/v1/...` — для других сервисов, доступен только из внутренней сети, mTLS/сервисный токен;
  - `/admin/v1/...` — для админ-панели, требует роль модератора или администратора.
- `/health/live`, `/health/ready`, `/metrics` — обязательны.
- Свой Postgres (отдельная БД и пользователь), свои миграции, свой outbox и inbox.
- Ни один сервис не читает чужую БД.

---

## Edge

### gateway
- **Отвечает за:** TLS-терминацию, маршрутизацию, rate-limit (по IP и пользователю), проверку подписи JWT, CORS, размер тела запроса, проброс `X-Request-Id`.
- **Не отвечает за:** бизнес-логику и авторизацию на уровне ресурсов.
- **Реализация:** Nginx (на VPS) → Envoy/Ingress (в Kubernetes). Конфиг декларативный и лежит в `infra/`.

### bff-web
- **Отвечает за:** агрегацию данных под экраны веб-клиента — `GET /api/web/v1/session`, `/pages/home`, `/pages/solo`, `/pages/results/{session_id}`; источник данных для SSR Next.js.
- **Не отвечает за:** доменную логику, хранение данных и **команды**: bff только читает ([ADR 0017](../adr/0017-frontend-nextjs-bff-read-only.md)). Ходы игры браузер шлёт прямо в game, автокомплит — в catalog.
- **Данные:** нет БД; Redis — кэш публичных данных (`game /v1/modes` 30 с, карточки catalog 5 мин). Личные блоки не кэшируются.
- **Вызывает:** game (`/v1/modes`, `/v1/daily`, `/v1/sessions/{id}/result`), identity (`/v1/me`), catalog (`/v1/items/{id}`) — с токеном пользователя как `Bearer`; токен проверяется локально по JWKS.
- **Масштаб:** stateless. **Деградация:** таймаут 1,5 с и circuit breaker на источник; блок упавшего источника — `{"status": "unavailable"}`, страница всё равно 200.

---

## Доменные сервисы

### 1. identity
- **Отвечает за:** аккаунты, регистрацию и вход (OAuth-провайдеры, e-mail), сессии, access/refresh-токены, привязку внешних аккаунтов для входа, слияние аккаунтов, удаление аккаунта, **роли и права** (user, moderator, admin), согласия (условия, 18+, cookie).
- **Данные:** users, password_credentials, auth_identities (provider, subject), sessions (`subject_kind = user | guest`), refresh_tokens, roles, permissions, role_changes (аудит), consents, email_tokens, idempotency_keys; merge_operations — этап 6.
- **Публикует:** `UserRegistered`, `UserProfileBasicsChanged`, `UserRolesChanged`, `UserDeleted`, `ExternalAccountLinked`/`Unlinked`; `AccountsMerged` — этап 6.
- **Слушает:** —
- **API (этап 1):** публичный `/v1/auth/*`, `/v1/me*`, OAuth Яндекса; internal `/internal/v1/admin-sessions[/refresh|/revoke]` (вход в админку только паролем + `admin.access`, ответ несёт `identity_version`); admin `/admin/v1/users`, `/admin/v1/roles`. Admin-мутации — по [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md); понижение прав отзывает сессии пользователя.
- **Этап 2:** гостевые сессии — `POST /v1/auth/guest` (токен `gst = true` без прав, `require_user` гостя не пускает, очистка `guest_sessions.purge`; [ADR 0018](../adr/0018-guest-sessions-in-identity.md)); сервисные токены — `POST /internal/v1/service-tokens` (client credentials, `aud` = вызываемый сервис, scope; [ADR 0016](../adr/0016-service-tokens-client-credentials.md)). `/internal/v1/admin-sessions*` требует сервисный токен admin со scope `admin_sessions`.
- **Масштаб:** stateless, лёгкая нагрузка. Ключи подписи JWT ротируются, публичные ключи раздаются по JWKS.
- **Деградация:** критичный сервис, держим ≥2 реплики. Уже выданные access-токены валидируются без обращения к нему. Лимитеры и denylist `sid` при отказе Redis — fail-open ([security.md §7](security.md#7-реализация-этапа-1)).

### 2. catalog
- **Отвечает за:** медиа-единицы (`MediaItem`: трек, фильм, сериал, аниме), персоны (артист, режиссёр, актёр), франшизы, релизы, жанры, алиасы и правила матчинга названий, метрики для «больше/меньше» (слушатели, рейтинг, сборы), поиск и автокомплит, импорт метаданных.
- **Данные:** Postgres (источник истины) + OpenSearch (поиск, индексируется по событиям из outbox).
- **Публикует:** `MediaItemCreated`, `MediaItemUpdated`, `MediaItemBlocked`, `MediaItemUnblocked`; `PersonUpdated` — этап 3+.
- **Слушает:** свой `catalog.media_item.v1` (индексация в OpenSearch), `AssetsReady`/`AssetsFailed` (проекция готовности загадок по `(media_item_id, clue_type, profile_key)`, устаревшие по `version` отбрасываются — [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)); `ImportRequested` от integrations — этап 6.
- **API (этап 1):** публичный `/v1/autocomplete`, `/v1/search`, `/v1/items/{id}`; admin `/admin/v1/items` (блокировка, разблокировка, алиасы), `/admin/v1/imports` (импорт — задача RabbitMQ `catalog.import`). Источники импорта: файл, TMDB, MusicBrainz, Spotify, Яндекс Музыка (внешние выключены по умолчанию, [ADR 0014](../adr/0014-music-sources.md)).
- **Масштаб:** интенсивное чтение — реплики, кэш Redis для карточек, кэш популярных префиксов автокомплита.
- **Деградация:** если OpenSearch недоступен, автокомплит работает по кэшу префиксов, поиск — упрощённый по Postgres (trigram).

### 3. media-pipeline
- **Отвечает за:** генерацию ассетов загадок по уровням (аудиосегменты fMP4, отрывки задом наперёд, размытие и пикселизация кадров, маскировка постеров), хранение в S3, выдачу **подписанных ссылок с коротким TTL** на конкретный уровень ([ADR 0012](../adr/0012-signed-asset-links-nginx.md)), загрузку UGC-изображений и их проверку (этап 5).
- **Данные:** Postgres (media_items_state, source_media, asset_sets, asset_levels, link_issuance, idempotency_keys), S3 (байты, случайные ключи `a/<hex>`).
- **Публикует:** `AssetsReady`, `AssetsFailed` (`version` монотонна по `media_item_id`).
- **Слушает:** `MediaItemCreated`/`Updated`/`Blocked`/`Unblocked` → ставит задачи `media.generate_asset_set` в RabbitMQ; `ClueAssetsRequested` — этап 3. Блокировка единицы сразу удаляет объекты ассетов из S3, разблокировка — новое поколение генерации ([ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)).
- **API (этап 1):** internal `/internal/v1/asset-links`, `/internal/v1/asset-sets` (с этапа 2 — только сервисный токен со scope `assets.link`, [ADR 0016](../adr/0016-service-tokens-client-credentials.md)); admin `/admin/v1/sources` (загрузка исходника), `/admin/v1/asset-sets[/{id}/regenerate]`. Публичного API нет.
- **Масштаб:** API stateless. Воркеры (ffmpeg, Pillow/libvips) масштабируются по длине очереди.
- **Деградация:** новые единицы контента не попадают в игру, пока нет ассетов. Уже готовые отдаёт gateway (`/media/`, `proxy_cache`; внешний CDN — этап 7). Отказ Redis при выдаче ссылки → 503 с `Retry-After` (fail-closed).

### 4. game
- **Отвечает за:** соло-движок: сессии, раунды, ответы, подсчёт очков (правила — `libs/game_kernel`, [02-game-engine.md](../02-game-engine.md)). **Реестр плагинов** (ClueType, Source, AnswerMatcher, PartialRule, Scorer). **Конфиги режимов** (версионируемые, черновик + публикация), дейлик (наборы дня, статистика угадавших), случайный режим с пресетами; позже — лента, прогнозы, архив, больше/меньше, топ-32.
- **Данные (этап 2):** Postgres — `mode_configs`, `mode_config_drafts`, `daily_sets`, `daily_set_items`, `game_sessions` (источник истины сессии, [ADR 0019](../adr/0019-solo-sessions-in-postgres.md)), `daily_round_stats`, `admin_actions`, `idempotency_keys`; проекции `catalog_items` (названия, алиасы, жанры, популярность, статус) и `clue_assets` (готовые наборы по `params_hash`, [ADR 0020](../adr/0020-mode-config-assets-by-params-hash.md)). Redis — только лимиты частоты и поколение кэша конфигов.
- **Публикует:** `GameFinished` (и для гостя, `is_guest = true`), `DailyCompleted` (только пользователи), `ModeConfigPublished`, `ModeConfigArchived`; позже — `PredictionWinnerSelected`, `BracketCompleted`, `HigherLowerRecord`.
- **Слушает:** `MediaItemCreated/Updated/Blocked/Unblocked` (проекция каталога), `AssetsReady`/`AssetsFailed` (проекция ассетов), `UserDeleted` (обнуляет `player_id`); `CollectionPublished/Updated` — этап 3.
- **API (этап 2):** публичный `/api/game/v1` — `modes`, `sessions` (старт, снимок, ход, пропуск, сдача, ассет уровня, итог), `daily`; мутации — cookie + CSRF или Bearer, `Idempotency-Key`, `expected_version`, ответы `no-store`. Admin `/admin/v1/mode-configs`, `/admin/v1/daily-sets`, `/admin/v1/projection/items` (права `config.modes.*`, `game.daily.*`, [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)).
- **Вызывает:** media-pipeline `/internal/v1/asset-links` с сервисным токеном `svc:game` (scope `assets.link`) — ссылка на ассет уровня и на финальный кадр ревила.
- **Периодические задачи:** наборы дня вперёд и блокировка дня, брошенные сессии, сиды режимов в local/ci ([02-game-engine.md §5.2](../02-game-engine.md#52-периодические-задачи-game)).
- **Масштаб:** stateless-поды, состояние сессии — строка в Postgres (ход = одна транзакция с `FOR UPDATE`), горизонтально.
- **Деградация:** проверка ответов и розыгрыш работают на проекциях, без catalog. Без media-pipeline новые уровни не выдаются (503 `game.assets_unavailable`), ход и итог работают. Без Redis лимиты пропускают запросы (fail-open с метрикой).

### 5. match
- **Отвечает за:** лобби и комнаты, матчмейкинг (очереди по фильтрам), конечный автомат матча (countdown → playing → reveal → summary), таймеры, ответы в реальном времени, реконнект, сдачу, тех. победу, чат и эмоции в комнате, наблюдение за матчем. Использует **те же плагины**, что и game (общая библиотека `libs/game-kernel`).
- **Данные:** Redis (состояние комнат, lease владельца, очереди матчмейкинга), Postgres (match_results, room_history).
- **Публикует:** `MatchStarted`, `MatchFinished` (с результатами игроков), `PlayerForfeited`, `PlayerRated` (похвала/жалоба).
- **Слушает:** `UserSanctionsChanged` (запрет рейтинговых игр, мут чата), проекции каталога и сборников.
- **Масштаб:** single-writer на комнату (lease в Redis), шардирование комнат по инстансам ([ADR 0010](../adr/0010-match-room-single-writer.md)). Рассылка клиентам — через Centrifugo.
- **Деградация:** при потере websocket клиент переходит на HTTP-tick.

### 6. rating
- **Отвечает за:** Эло (дуэль и многопользовательский), калибровку, звания, сезоны, топ недели и сезона, все лидерборды (набор задаётся конфигом), историю рейтинга.
- **Данные:** Postgres (ratings по `(user, ladder)`, rating_history, seasons), Redis ZSET (лидерборды).
- **Публикует:** `RatingChanged`, `SeasonFinished`, `WeeklyTopFinalized`.
- **Слушает:** `MatchFinished`, `DailyCompleted` (серии), `PredictionWinnerSelected`, `ProgressionLevelChanged`.
- **Масштаб:** запись асинхронная по событиям, чтение из Redis.
- **Деградация:** матчи не ждут rating. Пересчёт догоняет по событиям из лога Redpanda.

### 7. collections
- **Отвечает за:** сборники и плейлисты для игры (UGC и кураторские): конструктор, черновики (конфликт устройств), кастомные ассеты загадок (эмодзи, свои кадры, неверные варианты), каталог сборников, лайки, избранное, «пройден», расчёт сложности.
- **Данные:** Postgres (collections, items, clues, drafts, votes, favorites).
- **Публикует:** `CollectionPublished`, `CollectionUpdated`, `CollectionDeleted`, `CollectionLiked`.
- **Слушает:** `MediaItemBlocked` (убрать из сборников), `GameFinished` (статистика прохождений), `UserDeleted`.

### 8. library
- **Отвечает за:** личную медиатеку: пользовательские плейлисты, «смотреть позже», «просмотрено», «любимое». Это **источник истины** для сохранённого контента. Сохранение из ревила, итогов и карточек.
- **Данные:** Postgres (shelves, shelf_items, sync_state).
- **Публикует:** `ItemSaved`, `ItemRemoved`, `ShelfExportRequested`.
- **Слушает:** `ExternalLibraryImported` (импорт из провайдера), `SyncCompleted`, `UserDeleted`, `AccountsMerged`.

### 9. integrations
- **Отвечает за:** адаптеры внешних провайдеров (Яндекс, Spotify, Shikimori, AniList, Trakt, TMDB, Кинопоиск-метаданные), OAuth-подключение провайдера **для данных** (вход — в identity), шифрованное хранилище токенов, синхронизацию (экспорт плейлистов, watchlist), импорт метаданных по ссылке, rate-limit и circuit breaker на каждого провайдера.
- **Данные:** Postgres (connections, encrypted_tokens, sync_jobs), очереди RabbitMQ.
- **Публикует:** `ProviderConnected`, `ProviderDisconnected`, `ExternalLibraryImported`, `SyncCompleted`, `SyncFailed`, `ImportRequested`.
- **Слушает:** `ShelfExportRequested`, `ItemSaved` (если включена автосинхронизация), `UserDeleted`.
- **Деградация:** все внешние сбои изолированы здесь. Задачи ретраятся с backoff, ошибки уходят в DLQ.

### 10. progression
- **Отвечает за:** опыт и уровни, разблокировки по уровням, внутреннюю валюту (**append-only журнал проводок**, раздельно `earned`/`paid`), задания (метрики и периоды из конфига), бейджи и достижения (в том числе мастерство по персоне), магазин, инвентарь и косметику, оформление профиля, промокоды.
- **Данные:** Postgres (ledger, balances как проекция, quests, quest_progress, badges, inventory, shop_items, appearance).
- **Публикует:** `ExperienceGained`, `ProgressionLevelChanged`, `CurrencyCredited`, `CurrencyDebited`, `QuestCompleted`, `BadgeUnlocked`, `ItemPurchased`.
- **Слушает:** `GameFinished`, `MatchFinished`, `DailyCompleted`, `CollectionPublished`, `FriendAdded`, `CommentPosted`, `SeasonFinished`, `WeeklyTopFinalized`, `EntitlementGranted`.
- **Инварианты:** баланс не уходит в минус; каждая проводка идемпотентна (`opId`).

### 11. payments
- **Отвечает за:** тарифы и подписки, платёжного провайдера, вебхуки, чеки, **entitlements** (что доступно пользователю по плану). На старте выключен флагом `billing.enabled=false`, все пользователи получают план `free_all` ([../06-payments.md](../06-payments.md)).
- **Данные:** Postgres (plans, subscriptions, payments, invoices, entitlements).
- **Публикует:** `SubscriptionActivated`, `SubscriptionCanceled`, `PaymentSucceeded`, `EntitlementGranted`, `EntitlementRevoked`.
- **Слушает:** `UserDeleted`, `AccountsMerged`.

### 12. social
- **Отвечает за:** друзей, заявки, коды добавления, блокировки, комментарии (к сборникам и профилям), жалобы, очередь модерации, шкалы поведения и санкции, приватность, похвалы.
- **Данные:** Postgres (friendships, requests, blocks, comments, reports, moderation_cases, behaviour_scores, sanctions, privacy_settings).
- **Публикует:** `FriendAdded`, `CommentPosted`, `ReportFiled`, `UserSanctionsChanged`, `ContentModerated`.
- **Слушает:** `MatchFinished` (частые соперники), `PlayerRated`, `PlayerForfeited` (шкала порядочности), `UserDeleted`.

### 13. notifications
- **Отвечает за:** in-app уведомления (группировка, прочитано), доставку в realtime (публикация в Centrifugo), e-mail, web-push, Telegram-бота (позже), отключение по типам. Шаблоны и локализация.
- **Данные:** Postgres (notifications, mutes, templates).
- **Публикует:** — (терминальный потребитель).
- **Слушает:** почти все события, которые имеют смысл для пользователя. Маппинг «событие → тип уведомления» задаётся конфигом.
- **Деградация:** некритичен. Уведомления догоняют после восстановления.

---

## Операционные сервисы

### admin (Django)
См. [../05-admin-panel.md](../05-admin-panel.md). Не владеет доменными данными, **все мутации идут только через admin-API сервисов**.

- **Этап 1:** Django 5.2. Вход через identity (`/internal/v1/admin-sessions`, только пароль + `admin.access`). Разделы «Каталог» (catalog и media_pipeline) и «Пользователи» (identity). Чтение тоже идёт через admin-API ([ADR 0013](../adr/0013-admin-catalog-via-admin-api.md)); реплики и unmanaged-модели — этап 5.
- **Данные:** зеркало пользователей (права = снимок perms identity), `django_session` (токены identity только в зашифрованном виде), аудит `AuditEntry` (append-only), inbox.
- **Слушает:** `UserRolesChanged`, `UserDeleted` — применяет только `version` больше `identity_version` ([ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)).
- **Мутации:** форма с обязательной `reason` и `Idempotency-Key`; `AuditEntry` пишется и при успехе, и при отказе.
- **Доступ:** gateway админку не публикует; IP allowlist обязателен вне local/ci.

## Отложенные

- **recommendations:** потребляет `GameFinished`, `ItemSaved`, `MatchFinished` из Redpanda и строит рекомендации. Подключается без изменений в существующих сервисах.
- **analytics-ingest:** события → ClickHouse для продуктовой аналитики.

---

## Матрица «кто кого вызывает синхронно»

Синхронных зависимостей минимум. Всё, чего нет в таблице, работает через события.

| Вызывающий | Вызываемый | Зачем | Если недоступен |
|---|---|---|---|
| bff-web | game, identity, catalog | Агрегация экранов (токен пользователя как Bearer) | Блок `unavailable`, страница 200 |
| game | media-pipeline (internal, сервисный токен) | Подписанная ссылка на ассет уровня и ревила | Ретрай; 503 `game.assets_unavailable` |
| game, admin | identity (internal) | Сервисный токен (client credentials) | Старый токен до `exp`, затем вызов не выполняется |
| match | media-pipeline | То же, что у game (этап 4) | То же |
| progression | payments (internal) | Проверка entitlement при покупке | Кэш entitlements (TTL) |
| admin | identity `/internal/v1/admin-sessions*` (сервисный токен), admin-API сервисов | Вход в админку, модерация, конфиги | Раздел админки недоступен |

Браузер (через gateway) ходит напрямую: в **game** — команды игры (старт, ход, пропуск, сдача, ассет уровня); в **catalog** — автокомплит ответа (`/v1/autocomplete`); в **identity** — вход, гостевая сессия, refresh; в **bff-web** — только GET-экраны ([ADR 0017](../adr/0017-frontend-nextjs-bff-read-only.md)).

Проверка ответа в game и match идёт по локальной проекции (названия и алиасы), без сетевого вызова.

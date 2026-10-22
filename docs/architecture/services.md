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
- **Отвечает за:** агрегацию данных под экраны веб-клиента — `GET /api/web/v1/session`, `/pages/home`, `/pages/solo`, `/pages/results/{session_id}`, `/pages/online`, `/pages/match/{id}`; источник данных для SSR Next.js.
- **Не отвечает за:** доменную логику, хранение данных и **команды**: bff только читает ([ADR 0017](../adr/0017-frontend-nextjs-bff-read-only.md)). Ходы игры браузер шлёт прямо в game, автокомплит — в catalog.
- **Данные:** нет БД; Redis — кэш публичных данных (`game /v1/modes` 30 с, карточки catalog 5 мин). Личные блоки не кэшируются. Онлайн только с аккаунтом: гость получает `{status: "auth_required"}` без вызовов ([ADR 0036](../adr/0036-online-account-only-chat-and-ratings.md)); детали — `services/bff_web/CLAUDE.md`.
- **Вызывает:** game (`/v1/modes`, `/v1/daily`, `/v1/sessions/{id}/result`), identity (`/v1/me`), catalog (`/v1/items/{id}`), match (открытые комнаты, «Мои матчи», итог матча), library (флаги «в медиатеке» для итога матча) — с токеном пользователя как `Bearer`; токен проверяется локально по JWKS.
- **Масштаб:** stateless. **Деградация:** таймаут 1,5 с и circuit breaker на источник; блок упавшего источника — `{"status": "unavailable"}`, страница всё равно 200.

---

## Доменные сервисы

### 1. identity
- **Отвечает за:** аккаунты, регистрацию и вход (OAuth-провайдеры, e-mail), сессии, access/refresh-токены, привязку внешних аккаунтов для входа, слияние аккаунтов, удаление аккаунта, **роли и права** (user, moderator, admin), согласия (условия, 18+, cookie).
- **Данные:** users, password_credentials, auth_identities (provider, subject), sessions (`subject_kind = user | guest`), refresh_tokens, roles, permissions, role_changes (аудит), consents, email_challenges (одноразовые коды из писем), trusted_devices (доверенные устройства), idempotency_keys; merge_operations — этап 6. Вместо ссылок с токенами — коды и доверенные устройства ([ADR 0041](../adr/0041-email-codes-trusted-devices.md)); события не менялись. Фоновая очистка `email_challenges`/`trusted_devices` по сроку — планируется (в коде пока только `guest_sessions.purge`).
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
- **Публикует:** `AssetsReady`, `AssetsFailed` (`version` монотонна по `media_item_id`; с этапа 3b — необязательный `scope` набора сборника, [ADR 0029](../adr/0029-collection-assets-scoped-sets.md)).
- **Слушает:** `MediaItemCreated`/`Updated`/`Blocked`/`Unblocked` → ставит задачи `media.generate_asset_set` в RabbitMQ; `ClueAssetsRequested` — этап 3; `collections.collection.v1` — свои эмодзи сборников (этап 3b). Блокировка единицы сразу удаляет объекты ассетов из S3, разблокировка — новое поколение генерации ([ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)).
- **API (этап 1):** internal `/internal/v1/asset-links`, `/internal/v1/asset-sets` (с этапа 2 — только сервисный токен со scope `assets.link`, [ADR 0016](../adr/0016-service-tokens-client-credentials.md)); admin `/admin/v1/sources` (загрузка исходника), `/admin/v1/asset-sets[/{id}/regenerate]`. Публичного API нет.
- **Масштаб:** API stateless. Воркеры (ffmpeg, Pillow/libvips) масштабируются по длине очереди.
- **Деградация:** новые единицы контента не попадают в игру, пока нет ассетов. Уже готовые отдаёт gateway (`/media/`, `proxy_cache`; внешний CDN — этап 7). Отказ Redis при выдаче ссылки → 503 с `Retry-After` (fail-closed).

### 4. game
- **Отвечает за:** соло-движок: сессии, раунды, ответы, подсчёт очков (правила — `libs/game_kernel`, [02-game-engine.md](../02-game-engine.md)). **Реестр плагинов** (ClueType, Source, AnswerMatcher, PartialRule, Scorer). **Конфиги режимов** (версионируемые, черновик + публикация), дейлик (наборы дня, статистика угадавших), случайный режим с пресетами; с этапа 3a — марафон по персоне, «больше/меньше», недельная сетка топ-32, рекорды и процентиль ([ADR 0023](../adr/0023-round-draw-answer-formats-end-rules.md), [0027](../adr/0027-period-sets-preference-modes-records.md)); позже — лента, прогнозы, архив.
- **Данные (этап 2):** Postgres — `mode_configs`, `mode_config_drafts`, `daily_sets`, `daily_set_items`, `game_sessions` (источник истины сессии, [ADR 0019](../adr/0019-solo-sessions-in-postgres.md)), `daily_round_stats`, `admin_actions`, `idempotency_keys`; проекции `catalog_items` (названия, алиасы, жанры, популярность, статус) и `clue_assets` (готовые наборы по `params_hash`, [ADR 0020](../adr/0020-mode-config-assets-by-params-hash.md)). Redis — только лимиты частоты и поколение кэша конфигов. **Этап 3a:** `catalog_items.metrics`/`person_ids` (GIN), `daily_sets.period`/`subject_id` (наборы периода), `player_records`, `score_histogram`, `bracket_pick_stats`, `asset_requests`; состояние сессии — `state_schema` 2 (скрытый план раундов, варианты `option_id → media_item_id`).
- **Публикует:** `GameFinished` (и для гостя, `is_guest = true`; сетка — не публикует), `DailyCompleted` (только пользователи), `BracketCompleted` (сетка, этап 3a), `ModeConfigPublished`, `ModeConfigArchived`, `ClueAssetsRequested` (запрос ассетов админом, [ADR 0025](../adr/0025-dynamic-asset-profiles.md)); позже — `PredictionWinnerSelected`.
- **Слушает:** `MediaItemCreated/Updated/Blocked/Unblocked` (проекция каталога), `AssetsReady`/`AssetsFailed` (проекция ассетов; с этапа 3b — `scope` наборов сборника), `UserDeleted` (обнуляет `player_id`); этап 3b — `collections.collection.v1` (проекция сборников, [ADR 0028](../adr/0028-collections-versions-draft-projection.md)).
- **API (этап 2):** публичный `/api/game/v1` — `modes`, `sessions` (старт, снимок, ход, пропуск, сдача, ассет уровня, итог), `daily`; этап 3a — ход `{option_id}`, `modes/{key}/subjects`, `sessions/{id}/rounds/{index}/final-clue`, `me/records`; мутации — cookie + CSRF или Bearer, `Idempotency-Key`, `expected_version`, ответы `no-store`. Admin `/admin/v1/mode-configs` (с этапа 3a — `asset-requests`), `/admin/v1/daily-sets` (наборы периода), `/admin/v1/projection/items` (права `config.modes.*`, `game.daily.*`, [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)).
- **Вызывает:** media-pipeline `/internal/v1/asset-links` с сервисным токеном `svc:game` (scope `assets.link`) — ссылка на ассет уровня и на финальный кадр ревила.
- **Периодические задачи:** наборы дня вперёд и блокировка дня, брошенные сессии, сиды режимов в local/ci ([02-game-engine.md §5.2](../02-game-engine.md#52-периодические-задачи-game)).
- **Масштаб:** stateless-поды, состояние сессии — строка в Postgres (ход = одна транзакция с `FOR UPDATE`), горизонтально.
- **Деградация:** проверка ответов и розыгрыш работают на проекциях, без catalog. Без media-pipeline новые уровни не выдаются (503 `game.assets_unavailable`), ход и итог работают. Без Redis лимиты пропускают запросы (fail-open с метрикой).

### 5. match
- **Отвечает за:** лобби и комнаты (дуэль 1×1, party 3–6, кастомные лобби по шаблону режима или сборнику), конечный автомат матча (lobby → countdown → playing → reveal → summary), таймеры, ответы в реальном времени, реконнект, сдачу, тех. победу, эмоции/фразы и свободный чат в лобби, реванш, итог матча. Правила матча — плагины `ContestRule` общей библиотеки `libs/game-kernel` ([ADR 0032](../adr/0032-online-sessions-in-mode-config.md)). Матчмейкинг и наблюдение — 4b.
- **Данные:** Redis — горячее состояние комнаты (`{room:CODE}`: снимок, lease владельца, поток команд, ответы), Postgres — история: `matches`, `match_players`, `match_rounds` (партиции по месяцу), `player_ratings_given` (оценки игроков), проекции `users_proj`, `mode_heads`, `admin_actions`, outbox/inbox. Redis с AOF — единственное хранилище активных комнат ([data.md](data.md)).
- **Процессы:** `api`, `relay`, `consumer`, `rooms` (владелец комнат: lease с fencing-эпохой, команды через Redis Stream, снимки, handoff — [ADR 0034](../adr/0034-rooms-process-lease-snapshots.md)); `all` запускает их вместе.
- **Публикует:** `match.match.v1` — `MatchStarted`, `MatchFinished` (результаты игроков и раундов, без ников и e-mail), `PlayerRated` (похвала/жалоба; потребитель — этап 5). Контракты — `contracts/events/match/`.
- **Слушает:** `identity.UserDeleted` (обезличивание), `UserProfileBasicsChanged` (ники), `game.mode_config.v1` (проекция `mode_heads`). `UserSanctionsChanged` — этап 5 (пока санкции читаются из claim `sanctions`).
- **Вызывает синхронно:** game `internal/round-plans` и тело конфига режима (план раундов при старте матча, [ADR 0033](../adr/0033-match-round-plan-from-game.md)); media-pipeline `asset-links` (ссылка уровня — одна на комнату/раунд/уровень). Оба вызова с сервисным токеном; breaker и таймаут, недоступный ассет — раунд `void`, недоступный game — отказ старта (`start_error` в лобби).
- **Realtime:** публикует в Centrifugo (`room:{code}`, уведомления с `version`), выдаёт subscription-токены; connection-токен — identity ([ADR 0035](../adr/0035-realtime-centrifugo-tokens-and-tick.md), [communication.md §6](communication.md)). Без Centrifugo — HTTP-tick.
- **Доступ:** только с аккаунтом, гость — 403 `auth.guest_forbidden` ([ADR 0036](../adr/0036-online-account-only-chat-and-ratings.md)). Admin-API: `/admin/v1/rooms|matches` (право `match.manage`).
- **Масштаб:** single-writer на комнату (lease в Redis, [ADR 0010](../adr/0010-match-room-single-writer.md)); комнат на инстанс — `GV_MATCH__MAX_ROOMS_PER_INSTANCE`. Особенности реализации — `services/match/CLAUDE.md`.
- **Деградация:** потеря websocket → HTTP-tick; падение `rooms` → другой инстанс забирает комнату, разрыв ≤ lease TTL; Centrifugo недоступен — матч идёт на tick.

### 6. rating
- **Отвечает за:** Эло рейтинговой дуэли (ladder = домен, [ADR 0037](../adr/0037-rating-elo-calibration-ladder-config.md)), калибровку, звания, сезоны, топ недели и сезона, все лидерборды (набор задаётся конфигом), историю рейтинга.
- **Данные:** Postgres (ratings по `(user, ladder)`, rating_history, seasons), Redis ZSET (лидерборды).
- **Публикует:** `RatingChanged`, `LadderPublished`, `SeasonStarted`, `SeasonFinished`, `WeeklyTopFinalized` (топик `rating.rating.v1`).
- **Слушает:** `MatchFinished`, `DailyCompleted` (серии), `PredictionWinnerSelected`, `ProgressionLevelChanged`.
- **Масштаб:** запись асинхронная по событиям, чтение из Redis.
- **Деградация:** матчи не ждут rating. Пересчёт догоняет по событиям из лога Redpanda.

### 7. collections
- **Отвечает за:** сборники для игры (UGC и кураторские): конструктор, черновики (конфликт устройств — оптимистичная блокировка по `revision`), свои эмодзи-загадки (3b; свои кадры — этап 5, неверные варианты — этап 4), каталог сборников, лайки, избранное, «пройден», расчёт сложности ([ADR 0028](../adr/0028-collections-versions-draft-projection.md)–[0030](../adr/0030-minimal-ugc-safety-before-moderation.md)).
- **Данные (этап 3b):** Postgres — `collections` (статус, `kind`, `visibility`, счётчики, `title_tsv`, `difficulty`, `featured_rank`), неизменяемые `collection_versions`, `collection_drafts`, `likes`, `favorites`, `completions`, `play_stats`, `author_restrictions`, проекции `catalog_items_proj` и `users_proj`, `idempotency_keys`; Redis — лимиты частоты.
- **Публикует:** топик `collections.collection.v1` — `CollectionPublished`, `CollectionUpdated` (полный снимок состава, `content_version`), `CollectionDeleted`, `CollectionLiked` (только новый лайк).
- **Слушает:** `catalog.MediaItem*` (проекция; `Blocked` убирает единицу из сборников новой версией), `game.GameFinished` с `collection_id` («пройден», сложность), `identity.UserRegistered/ProfileBasicsChanged` (ник), `UserDeleted` (черновики удаляются, опубликованные обезличиваются).
- **API:** публичный `/api/collections/v1` — `collections` (создание, черновик, публикация, удаление, каталог, карточка), `like`, `favorite`, `me/collections`, `me/favorites`, `meta`; admin `/admin/v1/collections` (кураторские, скрыть/показать, «рекомендуемое»), `/admin/v1/authors/{id}/restriction` (права `collections.*`). Мутации — только пользователь, `Idempotency-Key`, CSRF/Origin.
- **Деградация:** каталог и карточки работают без game и media-pipeline; без Redis публикация отклоняется (fail-closed), лайки и избранное проходят (fail-open с метрикой).

### 8. library
- **Отвечает за:** личную медиатеку: системные полки из конфига («смотреть позже», «просмотрено», «любимое», правило `exclusive_with`) и свои полки. Это **источник истины** для сохранённого контента. Сохранение из ревила, итогов и карточек; всё приватно ([ADR 0031](../adr/0031-library-config-shelves-private-catalog-projection.md)).
- **Данные (этап 3b):** Postgres — `shelves`, `shelf_items`, `user_versions`, проекция `catalog_items_proj` (карточки без вызова catalog, заблокированная единица — `unavailable`), `idempotency_keys`; Redis — лимиты частоты; позже — `sync_state`.
- **Публикует:** топик `library.shelf.v1` — `ItemSaved`, `ItemRemoved` (только при изменении); `ShelfExportRequested` — этап 6.
- **Слушает:** `catalog.MediaItem*` (проекция), `UserDeleted`; позже — `ExternalLibraryImported`, `SyncCompleted`, `AccountsMerged`.
- **API:** публичный `/api/library/v1` — `shelves`, `shelves/{ref}/items[/{media_item_id}]`, `items/{id}/shelves`; только пользователь, `Idempotency-Key`, CSRF/Origin.

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
- **Этап 4a:** раздел «Матчи» (право `match.manage`; у moderator только `match.spectate`, раздел скрыт): активные комнаты, принудительное завершение с `reason` (`POST /admin/v1/rooms/{code}/abort`), итог матча — через admin-API match; онлайн-шаблоны в редакторе «Режимов» (ветка `session.kind=online`, фильтр «соло / онлайн»). Детали — `services/admin/CLAUDE.md`.
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
| bff-web | game, identity, catalog, collections, library, match | Агрегация экранов (токен пользователя как Bearer) | Блок `unavailable`, страница 200 |
| game | media-pipeline (internal, сервисный токен) | Подписанная ссылка на ассет уровня и ревила | Ретрай; 503 `game.assets_unavailable` |
| game, admin | identity (internal) | Сервисный токен (client credentials) | Старый токен до `exp`, затем вызов не выполняется |
| match | game (internal `round-plans`, тело конфига режима) | План раундов при старте матча, [ADR 0033](../adr/0033-match-round-plan-from-game.md) | Отказ старта: `start_error` в лобби, повтор хостом |
| match | media-pipeline (`asset-links`) | Ссылка уровня и ревила матча | Раунд `void` |
| match | Centrifugo (server API) | Публикация изменений комнаты | Клиенты на HTTP-tick |
| progression | payments (internal) | Проверка entitlement при покупке | Кэш entitlements (TTL) |
| admin | identity `/internal/v1/admin-sessions*` (сервисный токен), admin-API сервисов (в т. ч. match: комнаты, итоги) | Вход в админку, модерация, конфиги | Раздел админки недоступен |

Браузер (через gateway) ходит напрямую: в **game** — команды игры (старт, ход, пропуск, сдача, ассет уровня); в **catalog** — автокомплит ответа (`/v1/autocomplete`); в **identity** — вход, гостевая сессия, refresh, connection-токен Centrifugo; в **match** — команды комнаты, tick, state, subscription-токен ([ADR 0035](../adr/0035-realtime-centrifugo-tokens-and-tick.md)); в **Centrifugo** (`/connection/`) — подписка на канал комнаты; в **bff-web** — только GET-экраны ([ADR 0017](../adr/0017-frontend-nextjs-bff-read-only.md)).

Проверка ответа в game и match идёт по локальной проекции (названия и алиасы), без сетевого вызова. Match при старте матча один раз получает план раундов от game ([ADR 0033](../adr/0033-match-round-plan-from-game.md)); в ходе матча сетевых вызовов к game нет, кроме ссылок media-pipeline.

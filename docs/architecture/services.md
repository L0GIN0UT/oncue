# Сервисы

> Статус: принято (v1). Имена событий здесь — для навигации. Источник истины по схемам — `contracts/events/`.

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
- **Отвечает за:** агрегацию данных под экраны фронта (главная, профиль, хабы), SSR-friendly ответы, кэширование публичных страниц.
- **Не отвечает за:** доменную логику и хранение данных. Только читает и проксирует команды.
- **Масштаб:** stateless. **Деградация:** отдаёт блоки страницы частично; блок недоступного сервиса скрывается.

---

## Доменные сервисы

### 1. identity
- **Отвечает за:** аккаунты, регистрацию и вход (OAuth-провайдеры, e-mail), сессии, access/refresh-токены, привязку внешних аккаунтов для входа, слияние аккаунтов, удаление аккаунта, **роли и права** (user, moderator, admin), согласия (условия, 18+, cookie).
- **Данные:** users, auth_identities (provider, subject), sessions, refresh_tokens, roles, permissions, consents, merge_operations.
- **Публикует:** `UserRegistered`, `UserProfileBasicsChanged`, `UserRolesChanged`, `AccountsMerged`, `UserDeleted`.
- **Слушает:** —
- **Масштаб:** stateless, лёгкая нагрузка. Ключи подписи JWT ротируются, публичные ключи раздаются по JWKS.
- **Деградация:** критичный сервис, держим ≥2 реплики. Уже выданные access-токены валидируются без обращения к нему.

### 2. catalog
- **Отвечает за:** медиа-единицы (`MediaItem`: трек, фильм, сериал, аниме), персоны (артист, режиссёр, актёр), франшизы, релизы, жанры, алиасы и правила матчинга названий, метрики для «больше/меньше» (слушатели, рейтинг, сборы), поиск и автокомплит, импорт метаданных.
- **Данные:** Postgres (источник истины) + OpenSearch (поиск, индексируется по событиям из outbox).
- **Публикует:** `MediaItemCreated`, `MediaItemUpdated`, `MediaItemBlocked`, `PersonUpdated`.
- **Слушает:** `ImportRequested` (от integrations / admin), `AssetsReady` (отметка о готовности загадок).
- **Масштаб:** интенсивное чтение — реплики, кэш Redis для карточек, кэш популярных префиксов автокомплита.
- **Деградация:** если OpenSearch недоступен, автокомплит работает по кэшу префиксов, поиск — упрощённый по Postgres (trigram).

### 3. media-pipeline
- **Отвечает за:** генерацию ассетов загадок по уровням (аудиосегменты fMP4, отрывки задом наперёд, размытие и пикселизация кадров, маскировка постеров), хранение в S3, выдачу **одноразовых подписанных ссылок** на конкретный уровень, загрузку UGC-изображений и их проверку.
- **Данные:** Postgres (asset_sets, asset_levels, jobs), S3 (байты).
- **Публикует:** `AssetsReady`, `AssetsFailed`.
- **Слушает:** `MediaItemCreated` и `ClueAssetsRequested` → ставит задачи в RabbitMQ.
- **Масштаб:** API stateless. Воркеры (ffmpeg, Pillow/libvips) масштабируются по длине очереди.
- **Деградация:** новые единицы контента не попадают в игру, пока нет ассетов. Уже готовые работают через CDN.

### 4. game
- **Отвечает за:** соло-движок: сессии, раунды, ответы, подсчёт очков. **Реестр плагинов** (ClueType, Source, Scorer, AnswerMatcher). **Конфиги режимов** (версионируемые), дейлик (генерация дня, лента, прогнозы, архив), больше/меньше, топ-32.
- **Данные:** Redis (горячие сессии с TTL), Postgres (mode_configs, daily_sets, game_results, predictions, brackets). Локальная проекция каталога (id, названия, алиасы, ссылки на ассеты).
- **Публикует:** `GameFinished`, `DailyCompleted`, `PredictionWinnerSelected`, `BracketCompleted`, `HigherLowerRecord`.
- **Слушает:** `MediaItem*` (обновление проекции), `AssetsReady`, `CollectionPublished/Updated` (проекция состава сборников).
- **Масштаб:** stateless-поды (состояние сессии в Redis), горизонтально.
- **Деградация:** работает автономно на проекциях, даже если catalog недоступен.

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
См. [../05-admin-panel.md](../05-admin-panel.md). Не владеет доменными данными. Читает через read-реплики или admin-API, **все мутации идут только через admin-API сервисов**.

## Отложенные

- **recommendations:** потребляет `GameFinished`, `ItemSaved`, `MatchFinished` из Redpanda и строит рекомендации. Подключается без изменений в существующих сервисах.
- **analytics-ingest:** события → ClickHouse для продуктовой аналитики.

---

## Матрица «кто кого вызывает синхронно»

Синхронных зависимостей минимум. Всё, чего нет в таблице, работает через события.

| Вызывающий | Вызываемый | Зачем | Если недоступен |
|---|---|---|---|
| bff-web | любые | Агрегация экранов | Частичный ответ |
| game | media-pipeline | Подписанная ссылка на ассет уровня | Ретрай; раунд не стартует |
| match | media-pipeline | То же | То же |
| progression | payments (internal) | Проверка entitlement при покупке | Кэш entitlements (TTL) |
| admin | admin-API всех сервисов | Модерация, конфиги | Раздел админки недоступен |

Автокомплит ответа клиент запрашивает напрямую у catalog (`/v1/autocomplete`) через gateway. Проверка ответа в game и match идёт по локальной проекции (названия и алиасы), без сетевого вызова.

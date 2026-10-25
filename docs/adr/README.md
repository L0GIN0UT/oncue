# Architecture Decision Records

Один файл — одно решение. Принятые ADR не редактируются: при изменении решения создаётся новый ADR со статусом «заменяет NNNN». Шаблон: [0000-template.md](0000-template.md).

| # | Решение |
|---|---|
| [0001](0001-microservices-by-bounded-context.md) | Микросервисы по bounded contexts |
| [0002](0002-hexagonal-architecture.md) | Гексагональная архитектура внутри сервиса |
| [0003](0003-two-brokers.md) | Два брокера: Redpanda для событий, RabbitMQ для задач |
| [0004](0004-outbox-and-idempotency.md) | Transactional outbox и идемпотентные потребители |
| [0005](0005-vps-first-kubernetes-ready.md) | Старт на VPS, целевая платформа — Kubernetes |
| [0006](0006-django-admin-panel.md) | Админ-панель на Django, мутации через admin-API сервисов |
| [0007](0007-billing-disabled-by-flag.md) | Подписочный биллинг, выключенный фича-флагом |
| [0008](0008-plugin-game-engine.md) | Плагинный игровой движок, режимы как конфиг |
| [0009](0009-monorepo.md) | Монорепозиторий |
| [0010](0010-match-room-single-writer.md) | Single-writer для комнат матча |
| [0011](0011-service-definition-composition-root.md) | Composition root через ServiceDefinition вместо DI-контейнера |
| [0012](0012-signed-asset-links-nginx.md) | Подписанные ссылки на ассеты: короткий TTL, проверка на Nginx |
| [0013](0013-admin-catalog-via-admin-api.md) | Админка в этапе 1 читает и пишет через admin-API сервисов |
| [0014](0014-music-sources.md) | Источники музыкальных метаданных и аудио |
| [0015](0015-admin-idempotency-and-asset-event-order.md) | Идемпотентность admin-мутаций и порядок событий ассетов |
| [0016](0016-service-tokens-client-credentials.md) | Сервисные токены через client credentials |
| [0017](0017-frontend-nextjs-bff-read-only.md) | Фронтенд: Next.js в `frontend/`, bff только чтение |
| [0018](0018-guest-sessions-in-identity.md) | Гостевые сессии в identity |
| [0019](0019-solo-sessions-in-postgres.md) | Соло-сессии в Postgres |
| [0020](0020-mode-config-assets-by-params-hash.md) | ModeConfig ↔ ассеты по `params_hash`, очки в Scorer |
| [0021](0021-s3-storage-seaweedfs.md) | S3-хранилище на VPS: SeaweedFS вместо MinIO |
| [0022](0022-image-scan-reporting-not-gating.md) | Скан образов (trivy) — отчётный, не блокирующий |
| [0023](0023-round-draw-answer-formats-end-rules.md) | Состав раунда, форматы ответа и правила завершения — плагины ядра |
| [0024](0024-clue-texts-as-media-assets.md) | Тексты загадок — ассеты media-pipeline, курирование в catalog |
| [0025](0025-dynamic-asset-profiles.md) | Динамические профили ассетов по `ClueAssetsRequested` |
| [0026](0026-reveal-with-other-clue-type.md) | Ревил ассетом другого типа — `clue.reveal` |
| [0027](0027-period-sets-preference-modes-records.md) | Плановые наборы с периодом, режимы предпочтения, рекорды |
| [0028](0028-collections-versions-draft-projection.md) | Сборники: версии, черновик с оптимистичной блокировкой, проекция в game, сборник в параметрах старта |
| [0029](0029-collection-assets-scoped-sets.md) | Ассеты сборников — наборы со `scope` |
| [0030](0030-minimal-ugc-safety-before-moderation.md) | Минимальная безопасность UGC до модерации этапа 5 |
| [0031](0031-library-config-shelves-private-catalog-projection.md) | Медиатека: системные полки из конфига, приватность, проекция каталога |
| [0032](0032-online-sessions-in-mode-config.md) | Онлайн-сессии в `ModeConfig`: `OnlineSessionSpec`, `ContestRule`, `party_speed`, шаблоны лобби |
| [0033](0033-match-round-plan-from-game.md) | Источник раундов матча — план от game по internal API |
| [0034](0034-rooms-process-lease-snapshots.md) | Процесс `rooms`: команды через Redis Stream, lease с fencing-эпохой, снимки, итог в Postgres |
| [0035](0035-realtime-centrifugo-tokens-and-tick.md) | Realtime: токены Centrifugo, namespace-ы, версии и recovery, HTTP-tick |
| [0036](0036-online-account-only-chat-and-ratings.md) | Онлайн только с аккаунтом; эмоции и фразы, чат в лобби, оценки игроков |
| [0037](0037-rating-elo-calibration-ladder-config.md) | Рейтинг: Эло дуэли, калибровка, ladder-ы `music`/`film`, звания — конфиг rating |
| [0038](0038-matchmaking-queues.md) | Матчмейкинг: очереди Redis по ladder и формату, батчевый матчер, окно рейтинга |
| [0039](0039-seasons-weekly-top-leaderboards.md) | Сезоны, топ недели, лидерборды: ZSET как хранилище чтения с пересборкой |
| [0040](0040-lite-format-distractor-source.md) | Формат `lite`: дистракторы — возможность источника (`DistractorSource`) |
| [0041](0041-email-codes-trusted-devices.md) | Одноразовые коды по почте и доверенные устройства вместо ссылок с токенами |
| [0042](0042-progression-xp-levels-config.md) | Опыт и уровни: журнал проводок, уровень по версионируемому конфигу (черновик) |
| [0043](0043-currency-ledger-verses.md) | Валюта «Версы»: append-only журнал, баланс в той же транзакции (черновик) |
| [0044](0044-daily-wheel-and-boosts.md) | Ежедневное колесо и бусты опыта (черновик) |
| [0045](0045-shop-inventory-purchase.md) | Магазин и инвентарь: покупка в одной транзакции с ledger (черновик) |
| [0046](0046-animated-cosmetics-format.md) | Формат анимированной косметики (черновик) |
| [0047](0047-notifications-channels-mapping.md) | Notifications: каналы, маппинг событий, порт Push (черновик) |
| [0048](0048-promo-codes-custom-avatar.md) | Промокоды и своя аватарка (черновик) |
| [0049](0049-theme-as-data.md) | Тема оформления как данные (черновик) |
| [0050](0050-friends-and-blocks.md) | Друзья, блокировки, presence и приглашения (черновик) |
| [0051](0051-reports-and-moderation-queue.md) | Жалобы, комментарии и очередь модерации (черновик) |
| [0052](0052-sanctions-and-enforcement.md) | Санкции и их применение (черновик) |
| [0053](0053-social-config-versioned-in-db.md) | Конфиг social: версии в БД (черновик) |
| [0054](0054-seasonal-events-model.md) | Сезонные события: определение и запуски в БД (черновик) |
| [0055](0055-event-find-triggers.md) | Триггеры находок события (черновик) |
| [0056](0056-event-rewards-via-ledger.md) | Награды события через ledger (черновик) |
| [0057](0057-image-supply-chain-gate.md) | Цепочка поставки образов: digest-пиннинг и блокировка trivy по CRITICAL (черновик) |
| [0058](0058-moderator-hierarchy.md) | Иерархия модераторов: санкция только в пределах своих прав (черновик) |
| [0059](0059-realtime-disconnect.md) | Отзыв соединений Centrifugo при бане аккаунта и кике (черновик) |
| [0060](0060-postgres-runtime-role.md) | Рантайм-роль Postgres для сервисов с журналами (черновик) |
| [0061](0061-ugc-pending-preview-via-service.md) | Предпросмотр pending UGC через сервисы, без анонимного `p/*` (черновик; уточняет 0048, 0012) |

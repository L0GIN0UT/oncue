# 0069. Полная аудитория `event_started`, экран блокировки и хвосты 5c

- **Статус:** предложен (черновик)
- **Дата:** 2026-10-25
- **Уточняет:** 0054, 0047, 0052

## Контекст
`event_started` рассылался игрокам, известным notifications (есть уведомления или настройки), поэтому новичок
без единого уведомления стартового не получал. Бан `account` отказывал только на входе: открытая сессия
доживала до refresh, а тот отвечал 403 `auth.account_banned`, и клиент не показывал ничего понятного.
Хвосты 5c: ник в `/pages/players/{id}`, жалоба на чужую аватарку, жалоба из истории матчей, точный флаг
«можно добавить в друзья».

## Решение
**Аудитория.** Новая аудитория типа `active_users`; `event_started` переведён на неё, `known_users` остаётся.
- identity: `GET /internal/v1/users/active?after=&limit=` (сервисный токен, scope `users.active.list`, клиент
  `notifications`). Только `id` активных пользователей по возрастанию, курсор `next_cursor`, предел пачки
  `GV_USERS_ACTIVE__MAX_PAGE` (1000), лимит вызовов на `client_id` fail-closed. E-mail не отдаётся.
- notifications: страница читается вне транзакции, запись — короткой транзакцией на пачку
  (`GV_NOTIFICATIONS__FANOUT_BATCH_SIZE`), пауза между пачками `FANOUT_BATCH_PAUSE_S`. Настройки пачки —
  одним запросом (`preferences.for_users`). Идемпотентность — `UNIQUE(user_id, dedup_key)`, т.е. по
  `(event_run, user)`. Сбой identity: до `FANOUT_PAGE_RETRIES` повторов страницы с паузой, затем исключение
  из задачи — повтор очередью RabbitMQ (доставленные пачки пропускаются), настройка/права — сразу в DLQ.
  Метрики `gv_notifications_fanout_pages_total{result}`, `..._batch_seconds`, `..._user_erased_total`.
- `identity.UserDeleted` (топик `identity.user.v1`): notifications удаляет уведомления, настройки, согласие,
  устройства и факты доставки игрока (идемпотентно).
- Ограничения: рассылка идёт и забаненным, и не подтвердившим e-mail (достаточно статуса «активен»);
  нагрузка на identity — страница за вызов, ограничена лимитом клиента.

**Экран блокировки.** Клиент переходит на `/banned`, когда любой запрос (кроме входа/регистрации) или refresh
вернул 403 `auth.account_banned`. Причина и срок не показываются, единственное действие — выход
(`POST /v1/auth/logout`, затем полная перезагрузка). Изменений identity не потребовалось.

**Хвосты 5c.**
- Ник: social `relation` получил `nickname` (только при видимом профиле); bff кладёт его в `PlayerPage.nickname`,
  иначе фронт показывает «Игрок».
- Аватарка: progression `PlayerAppearance.custom_avatar_asset_id` (необязательное) → bff
  `AppearanceCard.custom_avatar_asset_id` → `ReportButton kind="avatar"` на чужой аватарке (профиль игрока,
  итог матча).
- История: bff `MyMatchCard.participants` (другие игроки, `can_report`) для первых
  `GV_PAGES__ONLINE_MATCH_PLAYERS_LIMIT` (5) матчей хаба — по вызову match на матч; сбой — пустой список.
  Страница `/me` читает match напрямую и списка игроков не получает (отдельная задача).
- Друзья: social `GET /v1/players/relations?user_ids=` (до 50, дубли схлопываются); bff считает
  `MatchPlayerCard.can_add_friend = can_send_request` пачками `GV_PAGES__RELATIONS_BATCH_SIZE`; сбой social —
  `false` (кнопка скрыта).

## Последствия
Контракты: identity (ручка, scope), notifications (`audience: active_users`), social, progression, bff.
Требуется ручная проверка владельцем: scope и ручка identity, поведение при бане.

## Дополнение: замечания security-ревью
- **Tombstone удалённых (notifications).** `identity.UserDeleted` в той же транзакции пишет игрока в `erased_users` (миграция `0008`); пачка fanout пропускает такие id, поэтому читать страницу identity вне транзакции безопасно. Пачка берёт общую `pg_advisory_xact_lock_shared`, удаление - эксклюзивную той же ключевой блокировки: удаление ждёт коммита идущей пачки, а пачка, начатая позже, уже видит tombstone. Очистка - задача relay `notifications.erased_users.purge` (`GV_NOTIFICATIONS__ERASED_RETENTION_DAYS`, по умолчанию 30 дней, больше самой долгой рассылки; интервал `ERASED_PURGE_INTERVAL_S`).
- **`GET /v1/players/relations` (social).** Факты зрителя читаются пакетом `IN` (nickname, privacy, friendships, blocks, pending - 5 запросов независимо от размера пачки). Лимит вызовов на пользователя - `GV_SOCIAL__RELATIONS_PER_MINUTE` (60 за окно 60 с), 429 `social.relations_rate_limited` с `Retry-After`, лимитер Redis fail-closed (503).
- **Форма находок (admin/progression).** `PUT /admin/v1/events/{key}` принимает необязательный `expected_body_sha256`: хеш, который админ видел при открытии формы (скрытое поле `expected_sha` в формах и YAML); не совпал с текущим - 409 `progression.event_definition_changed`, правка другого админа не затирается. Без поля поведение прежнее. Админка показывает «Определение изменили, откройте заново», введённое остаётся в форме.

# 0066. Admin-выключатели типов уведомлений и общая библиотека `libs/email`

- **Статус:** черновик
- **Дата:** 2026-10-25
- **Уточняет:** [0047](0047-notifications-channels-mapping.md) (типы и каналы), [0041](0041-email-codes-trusted-devices.md) (письма identity)
- **Закрывает:** хвосты «admin-API включения/выключения типов» и «вынос отправителей писем в libs»

## Контекст
Набор типов уведомлений и их тексты — конфиг (`notification_types.yaml`), игрок отключает типы у себя. Оператору нужен рубильник «тип шумит или сломан — выключить для всех» без выката. Отдельно: `SmtpEmailSender`, `LogEmailSender`, `BackgroundEmailSender` были скопированы из identity в notifications (межсервисные импорты запрещены), копии расходились.

## Решение
1. **Выключатели.** Таблица `notification_type_flags(type PK, enabled, changed_by, changed_at)` в БД notifications (миграция `0007`, expand). Нет строки — тип включён. Admin-API: `GET /admin/v1/notification-types`, `PUT /admin/v1/notification-types/{type}` (`enabled`, `reason`, `Idempotency-Key`; права `admin.access` + `notifications.types.manage`; аудит `notification_type.set_enabled`, причина — только в аудите). Право добавлено в матрицу identity миграцией `0020` (только `admin`).
2. **Эффект.** `IngestEvent` и пачки `FanOutAudience` пропускают выключенный тип (лог `notifications.type_disabled`), событие обрабатывается без ошибки и в DLQ не уходит; включение возвращает поведение для новых событий (пропущенные уведомления задним числом не создаются). Настройки игроков (`notification_preferences`), согласие на рассылку и рассылки администрации не затрагиваются.
3. **Кэш.** `TypeSwitch` держит решения в процессе `GV_NOTIFICATIONS__TYPE_FLAGS_CACHE_TTL_S` секунд (по умолчанию 15, 0 — без кэша); процесс, в котором переключили, сбрасывает кэш сразу, остальные (consumer, воркер) видят изменение не позже TTL.
4. **`libs/email` (`gv_email`).** `OutgoingEmail` (готовое письмо: адрес, тема, текст, заголовки, поля лога), `SmtpEmailSender`, `LogEmailSender`, `BackgroundEmailSender`, `PermanentEmailError`. Библиотека не знает о назначении писем и настройках сервисов: SMTP-параметры передаются аргументами, `GV_EMAIL__*`/`GV_SMTP__*`/`GV_NOTIFICATIONS__SMTP__*` остались в сервисах. В сервисах — тонкие адаптеры портов (`IdentityEmailSender` рендерит письмо identity, `GvEmailSender` у notifications переносит `EmailMessage`).
5. Django-admin к `libs/email` не подключается (у него нет собственной отправки писем).

## Последствия
- Выключенный тип виден в логах и метриках только как `notifications.type_disabled`; админка показывает, кто и когда менял.
- Миграция `0007` — `CREATE TABLE` новой таблицы без блокировок существующих; `0020` — две короткие вставки в справочники прав identity.
- Поведение identity меняется незначительно: отказ получателя SMTP теперь `PermanentEmailError` (`send_safely` и так гасит любое исключение отправки).
- Новое право — зона повышенной безопасности identity: требуется ручная проверка владельцем.

## Дополнение: системные типы не выключаются
Итог модерации (`avatar_approved`, `avatar_rejected`, `report_resolved`) игрок отключить не может (`user_mutable: false`), поэтому и админ-рубильник на них не действует. В каталог типов добавлен необязательный признак `admin_switchable` (по умолчанию равен `user_mutable`; `true` переопределяет). `PUT /admin/v1/notification-types/{type}` для такого типа — 422 `notifications.type_not_switchable`, `GET` отдаёт `switchable`, Django-админка не показывает переключатель. `IngestEvent` и `FanOutAudience` игнорируют старую запись выключателя у несwitchable-типа: системный тип доставляется всегда. Схема каталога расширена обратно совместимо (поле необязательное).

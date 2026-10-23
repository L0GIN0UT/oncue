# notifications

In-app уведомления, realtime-доставка, e-mail, web-push, отключение по типам.

- Пакет: `notifications_service`; запуск: `notifications api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/notifications/v1/...` на gateway
- Зависимости: PostgreSQL, Redis (лимит отписки), Redpanda, RabbitMQ, Centrifugo (server API)
- Статус: этап 5b реализован (`docs/plans/stage-5.md` §9.5, ADR 0047); событий не публикует — терминальный потребитель.

## Особенности
- Типы и тексты — только файл `config/notification_types.yaml` внутри пакета (`GV_NOTIFICATIONS__TYPES_PATH` подменяет); проверка по `config/notification_types.schema.json` (копия схемы контракта, равенство сверяет contract-тест) и `TypeCatalog.parse`; ошибка — сервис не стартует. Обработчики событий регистрируются по `source_event` типов.
- Дедупликация — `UNIQUE(user_id, dedup_key)` из полей payload (не `event_id`); выключенный `in_app` у `user_mutable`-типа — записи нет; `user_mutable=false` отключить нельзя.
- Realtime: `PostCommitDispatcher` публикует в `personal:#<user_id>` после коммита транзакции consumer (SQLAlchemy `after_commit`); отказ Centrifugo — лог и метрика, запись не теряется. Формат сообщения `notification.created` — `contracts/realtime/notification.created.v1.json` (contract-тест на сообщение диспетчера).
- Справочники текстов (`lookups` типа): `{reason}` в шаблоне подставляется текстом по коду из события (`path`), неизвестный/пустой код — `fallback`, нет локали — `default_locale`; имя не может быть и в `params`; на `default_locale` текст обязателен у каждого кода и у fallback. Коды причин `avatar_rejected` (общий словарь с media-pipeline, `AssetModerated.reason_code`): `inappropriate`, `copyright`, `low_quality`, `personal_data`, `other`.
- Отписка `POST /v1/unsubscribe` (без входа): токен `<версия>.<ids>.<HMAC-SHA256>` (`domain/tokens.py`), любая неудача проверки — один ответ 422; лимит по IP в Redis fail-closed (503 + `Retry-After`, 429). Ключ `GV_NOTIFICATIONS__UNSUBSCRIBE_HMAC_KEY` (ротация: `..._KEY_VERSION` + `..._PREVIOUS_KEYS`); без него вне local/ci рабочий процесс не стартует (экспорт OpenAPI без инфраструктуры проверок не делает).
- Рассылка: `POST /admin/v1/broadcasts` ставит задачу `notifications.broadcast.send` ДО коммита, воркер повторяет, пока рассылка не видна. Отправка: `claim` в `broadcast_deliveries` (PK) только для подписанного сейчас; временный сбой отправки снимает claim, задача повторяется; окончательный отказ адреса (`PermanentEmailError`) — доставка `failed`, без повторов; итог `sent`/`failed` (если не ушло ни одного). Письмо всегда с `List-Unsubscribe` (+`-Post: One-Click`); `unsubscribe_footer` обязан содержать `{link}`. Падение между claim и send теряет одно письмо, но не дублирует.
- Адреса e-mail хранит identity: `HttpEmailDirectory` → `POST {GV_NOTIFICATIONS__IDENTITY_URL}/internal/v1/users/emails` (пачки ≤500, scope `users.emails.read`, токен `build_service_token_provider(aud=identity)`; только подтверждённые адреса). Сеть/5xx/429 — `DIRECTORY_UNAVAILABLE` (задача повторится), прочие 4xx — `DIRECTORY_FORBIDDEN` (рассылка `failed`; 401 — одна попытка с новым токеном). Без `GV_SERVICE_AUTH__*` — `UnconfiguredEmailDirectory` (рассылка `failed`). Адреса не логируются и не попадают в исключения.
- Токены устройств хранятся sha256-хешем и не логируются; удаление мягкое (`deleted_at`): повтор для своего — 204, чужое/несуществующее — 404. Push — только порт и `NullPushSender`.
- Мутации из браузера (`read`, настройки, устройства) — сервис в `COOKIE_MUTATION_SERVICES`. Право рассылки: `notifications.broadcast.send`.
- Тесты: фикстура `notifications` (`tests/conftest.py`) — реальные Postgres и Redis, фейки Centrifugo/SMTP/справочника; `ForwardingTasks.forward` пересылает задачи в настоящий RabbitMQ.

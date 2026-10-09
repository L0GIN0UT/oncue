# admin

Админ-панель на Django 5.2 (этап 1): вход через identity, разделы «Каталог» и «Пользователи», аудит.

- Пакет: `admin_service`; запуск: `admin api|consumer|migrate|all` (D7; `relay`/`worker` нет)
- Django-настройки: `admin_service.django_settings` — строятся из `admin_service.settings.Settings` (env `GV_*`, `GV_ADMIN__*`)
- Решения: ADR 0006, 0013; план — `docs/plans/stage-1.md` §7 D2. Карточка — `docs/architecture/services.md`
- Публичного API нет, gateway админку не публикует. Метрики — `:9100`
- Зависимости: PostgreSQL (зеркало пользователей, сессии, аудит, inbox), Redpanda (consumer)

## Особенности
- Своих паролей нет: `IdentityAuthBackend` → `POST identity /internal/v1/admin-sessions {login, password, client_ip}`; права — из **проверенного по JWKS** access-токена, не из JSON ответа. Без `admin.access` не пускаем, даже если identity ответил 200.
- Права Django = снимок perms identity (`identity.<perm>`); `is_staff` ⇔ `admin.access`, `is_superuser` ⇔ роль `admin`. Синхронизация — при входе и refresh (`identity_version = max(текущая, из ответа)`) и по `UserRolesChanged`/`UserDeleted` только при большем `version`. Событие зеркало не создаёт.
- Токены identity в `django_session` — только Fernet-шифротекст (`core.session_crypto`: `GV_ADMIN__SESSION_ENC_KEY` или HKDF от `SECRET_KEY`). Refresh — под `SELECT … FOR UPDATE` строки сессии (две вкладки не шлют один refresh дважды). Выход — `POST /internal/v1/admin-sessions/revoke` (ошибка — в лог, выход всё равно).
- `/login/`, `/logout/` — `core.auth_views` поверх `admin.site`: лимит попыток входа по IP (`GV_ADMIN__LOGIN_ATTEMPTS_*`, кэш Django: Redis при `GV_REDIS_URL`, иначе память процесса) — сверх лимита identity.
- Данные сервисов — только через их admin-API (`core.views.fetch/mutate`; ассеты — `media_pipeline GET /admin/v1/asset-sets`); `ServiceModelAdmin`/`ReplicaRouter` — каркас до этапа 5 (D3).
- Мутация = форма с обязательной `reason` и скрытым `idempotency_key` + `AuditEntry` и при успехе, и при отказе. В `after` — только изменённые поля (`audit_fields`), без e-mail. Просмотр пользователей (`users.view`) тоже аудируется (`audit_read`, поиск по e-mail — без адреса). Аудит append-only: ORM — `AppendOnlyError`, БД — триггеры `audit/0001` (UPDATE/DELETE) и `0002` (TRUNCATE).
- Consumer — `core.events.UserEventsProcessor` (транзакция и inbox в Django ORM) поверх `KafkaEventConsumer` через `cast`.
- Health обслуживает первый middleware — до проверки `Host` и IP-allowlist. Allowlist — по `REMOTE_ADDR`; `X-Forwarded-For` — только от `GV_ADMIN__FORWARDED_ALLOW_IPS`. Вне local/ci `GV_ADMIN__IP_ALLOWLIST` обязателен и не пуст (иначе процесс не стартует); частные сети по умолчанию — только local/ci.
- Статика django-admin отдаётся приложением (`/static/`, без collectstatic): общий Dockerfile не меняем. API админка не публикует (`contracts/openapi/admin.json` — устаревший снимок этапа 0, `gv contracts export` его не генерирует).
- Тесты: conftest сам делает `django.setup()`; БД `test_admin_<хвост uuid7>`, триггер запрета TRUNCATE в ней выключен ради `flush`. Заглушки — `stub_service(service=...)` + проверка 2xx-тел по схемам контракта (`respond_off_contract` — только для тестов на битые данные); ручки параллельных потоков — `PENDING_OPERATIONS` (TODO).

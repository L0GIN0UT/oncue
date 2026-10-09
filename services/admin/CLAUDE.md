# admin

Админ-панель на Django 5.2 (этап 1): вход через identity, разделы «Каталог» и «Пользователи», аудит.

- Пакет: `admin_service`; запуск: `admin api|consumer|migrate|all` (D7; `relay`/`worker` нет)
- Django-настройки: `admin_service.django_settings` — строятся из `admin_service.settings.Settings` (env `GV_*`, `GV_ADMIN__*`)
- Решения: ADR 0006, 0013; план — `docs/plans/stage-1.md` §7 D2. Карточка — `docs/architecture/services.md`
- Публичного API нет, gateway админку не публикует. Метрики — `:9100`
- Зависимости: PostgreSQL (зеркало пользователей, сессии, аудит, inbox), Redpanda (consumer)

## Особенности
- Своих паролей нет: `IdentityAuthBackend` → `POST identity /internal/v1/admin-sessions {login, password}`; права — из **проверенного по JWKS** access-токена, не из JSON ответа. Без `admin.access` не пускаем, даже если identity ответил 200.
- Права Django = снимок perms identity (`identity.<perm>`); `is_staff` ⇔ `admin.access`, `is_superuser` ⇔ роль `admin`. Синхронизация — при входе, refresh и по `UserRolesChanged`/`UserDeleted` только при большем `version` (`identity_version`). Событие зеркало не создаёт.
- Данные сервисов — только через их admin/internal API (`core.views.fetch/mutate`); `ServiceModelAdmin`/`ReplicaRouter` — каркас до этапа 5 (D3).
- Мутация = форма с обязательной `reason` и скрытым `idempotency_key` (создаётся при показе формы — повторная отправка идемпотентна) + `AuditEntry` и при успехе, и при отказе. Аудит append-only: ORM — `AppendOnlyError`, БД — триггер (`audit/0001`).
- Consumer — `core.events.UserEventsProcessor` (транзакция и inbox в Django ORM) поверх `KafkaEventConsumer` через `cast`.
- Health обслуживает первый middleware — до проверки `Host` и IP-allowlist. Allowlist — по `REMOTE_ADDR`; `X-Forwarded-For` — только от `GV_ADMIN__FORWARDED_ALLOW_IPS`.
- Статика django-admin отдаётся приложением (`/static/`, без collectstatic): общий Dockerfile не меняем.
- Тесты: conftest сам делает `django.setup()` (в общем `gv test` pytest-django не видит настройки на старте); БД `test_admin_<хвост uuid7>`. Заглушки identity/catalog/media — `stub_service` **без** `service=` до слияния потоков A/B/C (TODO в conftest).

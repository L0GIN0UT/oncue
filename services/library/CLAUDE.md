# library

Личная медиатека: «смотреть позже», «просмотрено», «любимое», свои полки.

- Пакет: `library_service`; запуск: `library api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`; решения — ADR 0031
- Публичный API: `/api/library/v1/...` на gateway (только пользователи; admin/internal API нет)
- Зависимости: PostgreSQL, Redis (лимиты частоты, fail-open), Redpanda
- Статус: этап 3b, поток L — полки, сохранение, проекция catalog, удаление пользователя (`docs/plans/stage-3b.md` §6)

## Особенности
- Системные полки — только конфиг `config/shelves.yaml` (`GV_LIBRARY__SHELVES_CONFIG` заменяет целиком): ключи, домены, `title_key`, `exclusive_with`, домены своих полок, персоны карточки. В коде ключей полок нет. Строка системной полки создаётся лениво при первом **сохранении**; чтение её не создаёт (`shelf_id: null`).
- Все мутации пользователя идут под `SELECT … FOR UPDATE` строки `user_versions`: это сериализует лимиты (`custom_shelves_max`, `items_per_shelf_max`) и даёт монотонную `version` — **у каждого события своя** (сохранение с `exclusive_with` = 2 события, версии n+1, n+2).
- Агрегат `UserLibrary` грузит все полки пользователя и только нужные строки `shelf_items` (одной единицы или одной полки); изменения — списком `changes`, репозиторий применяет их по порядку.
- `ItemSaved`/`ItemRemoved` — только при фактическом изменении; повтор — `changed: false` без события. Ответ мутации хранится с `Idempotency-Key` (`gv_db.idempotency`), поэтому use-cases возвращают JSON (`application/views.py`).
- Топик `library.shelf.v1`: `aggregate_type = "shelf"`, `aggregate_id = user_id` (имя топика собирается из `aggregate_type`).
- `shelf_ref` — `system:<key>` или канонический uuid в нижнем регистре (`ShelfRef.parse`); id системной полки в пути своей полки и чужая полка — 404 `library.shelf_not_found`.
- Проекция `catalog_items_proj`: порядок по `version`; блокировка не убирает единицу с полок — она показывается `status: unavailable`, удалить её можно, сохранить — нет (422 `library.item_unavailable`).
- `domain` хранится в строке `shelf_items` (на момент сохранения): событие удаления не зависит от проекции.
- `identity.UserDeleted`: consumer ставит отметку `user_purges` и удаляет первую порцию; задача relay `library.users.purge` дочищает порциями `GV_LIBRARY__PURGE_BATCH` (каждая — своя транзакция), в конце — полки, версия и ключи идемпотентности.
- `GET /v1/items/shelves?ids=` — полки пачки единиц пользователя (запись на каждую уникальную, пустой `shelves` — нигде не лежит; один запрос `placements_many`); предел `GV_LIBRARY__ITEM_SHELVES_BATCH_MAX` (50, больше — 422 `library.batch_too_large`); доступ как у `/v1/items/{id}/shelves`. Путь `/items/shelves` не пересекается с `/items/{id}/shelves`.
- Ответы и ошибки — `Cache-Control: no-store` (`api/common.NoStoreRoute`); CSRF/Origin для cookie — в `gv_auth.require_user`.

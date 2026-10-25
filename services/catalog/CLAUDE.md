# catalog

Медиа-единицы (треки, фильмы, сериалы, аниме), персоны, франшизы, алиасы, поиск и автокомплит.

- Пакет: `catalog_service`; запуск: `catalog api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/catalog/v1/...` на gateway; admin-API `/admin/v1/...` (план этапа 1, §2.5)
- Зависимости: PostgreSQL (+`pg_trgm` из init-скрипта), Redis, Redpanda, RabbitMQ, OpenSearch (опционально)
- Статус: этап 3a — тексты загадок (ADR 0024), страны, кадры TMDB с `position`, переопубликация; этапы 1–2 — домен, импорт, поиск, admin-API.

## Особенности
- Нормализация живёт в `libs/text` (`gv_text`), `domain/normalization.py` — реэкспорт; своя golden-таблица `tests/unit/test_normalization.py` — регресс. Транслит ru↔lat — отдельные алиасы `translit`.
- Ручные алиасы (`source=manual`) импорт не трогает. `PUT .../aliases` делает весь список ручным; удалённые импортные алиасы вернёт следующий импорт.
- Изменение агрегата = `version+1` + событие с этой версией; `save` — оптимистичная блокировка по `persisted_version`.
- `source_media_ref` — uuid5 от (единица, kind, uri): стабилен между импортами (на него ссылается media-pipeline). `position` вида — колонка `kind_position` (`position` — порядок строки).
- Тексты загадок: черновик и ревизии — `clue_texts` (сущность `ClueTexts`), одобренный снимок — `media_items.clue_texts`; в событие идёт только одобренный, правка → draft (снимок прежний до нового одобрения). Импорт заполняет только пустые поля (`mask_plot` — golden-тест); засев `GV_CATALOG__SEED_TEXTS_APPROVED` (только local/ci) одобряет сразу.
- `POST /admin/v1/items/republish` → задача `republish` порциями `GV_CATALOG__REPUBLISH_BATCH`: `version+1`, `changed_fields: [republish]`; задача несёт стартовый курсор, повтор порции — no-op.
- Импорт сопоставляет единицы по внешним id (`tmdb` → … → `seed`); `seed` (ключ строки файла) в события не публикуется.
- Индексатор пишет в OpenSearch актуальную строку БД с её `version` (`version_type=external`): повторы и старые события отсекает индекс; строка БД старше события → `DependencyUnavailableError(catalog.index_lag)` (ретрай).
- Без `GV_OPENSEARCH__URL` или при его отказе поиск идёт по pg_trgm (`TrgmSearch`) — штатная деградация.
- `GET /v1/items?ids=` (повторяющийся параметр) — публичные карточки пачкой: дубли схлопываются, неизвестные и заблокированные пропускаются, порядок не гарантирован; предел `GV_CATALOG__ITEMS_BATCH_MAX` (50, больше — 422 `catalog.batch_too_large`). Для итога матча в bff; одиночная карточка остаётся.
- Кэш автокомплита инвалидируется «поколением» (`INCR`), блокировка сбрасывает его сразу после коммита.
- Admin-мутации: `gv_service.IdempotencyKey`/`Reason`; ключ и ответ — `gv_db.idempotency` (`idempotency_keys`, та же транзакция), `admin_actions` — только аудит; импорт идемпотентен по `import_jobs (actor_id, idempotency_key)`.
- Импорт: реестр `Mapping[ImportSource, ImportEntrySource]` (файл, TMDB, музыка) собирает `container.import_sources`; нет в реестре — `catalog.source_disabled`. `RunImport` не оставляет `running`: `AppError` → `failed` с её кодом, прочее → `catalog.import_crashed` и проброс.
- Проекция ассетов загадок: `version` media-pipeline монотонна по `media_item_id` — событие с `version` ≤ применённой отбрасывается независимо от `asset_set_id` (сравнение со строкой проекции `(media_item_id, clue_type, profile_key)`).
- `api` не импортирует `infrastructure`: роутеры зависят от `application.services.CatalogServices`, контейнер подставляет `main.py` (`container_for(infra)` — один на процесс).
- Внешние источники выключены по умолчанию, у каждого свой rate-limit и circuit breaker; Яндекс Музыка — неофициальный клиент, только метаданные (ADR 0014).
- Фейки портов catalog — `tests/support/catalog_fakes.py`: testkit не может их держать (библиотекам запрещено импортировать сервисы).

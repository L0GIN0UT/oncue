# media_pipeline

Генерация ассетов загадок по уровням, хранение в S3, подписанные ссылки, проверка UGC-изображений.

- Пакет: `media_pipeline_service`; запуск: `media_pipeline api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: нет (только internal: сервисный токен identity, `aud = GV_INTERNAL_AUTH__AUDIENCE` (по умолчанию `media_pipeline`), scope `assets.link`)
- Зависимости: PostgreSQL, Redis (лимит выдачи ссылок), Redpanda, RabbitMQ, S3/MinIO, ffmpeg
- Статус: этап 3a — 9 типов загадок (+ `audio_reverse`, `soundtrack`, `audio_blackout`, `frame_sequence`, `description`, `emoji`), динамические профили (ADR 0025), тексты загадок из catalog (ADR 0024).

## Особенности
- Новый ClueType = генератор в `application/generators` + регистрация в `infrastructure/container.py` + схема `contracts/clues/*`. Генератор сам объявляет исходники (`source_kind`, `source_count`, `sources_for`: лучший, N кадров по `position`, текст с нужными полями); нет исходников — набор не планируется. Use-cases не ветвятся по типу.
- Профили: статические — `config/asset_profiles.yaml` (внутри пакета: образ ставится `--no-editable`), хэши = golden `profile:<clue>:<profile>`; динамические — `asset_profiles_dynamic` по `game.ClueAssetsRequested` (ключ `dyn_<16 hex>`, лимит, досоздание задачей `media.profiles.backfill`). `params_hash` — от params в записанном/присланном виде. Модели `domain/clues.py` = `contracts/clues` + монотонность уровней.
- `api` и `infrastructure` — независимые слои: роутеры получают готовые use-cases, сборка — в `main.py` через `MediaContainer`. Адаптеры создаются без подключений (экспорт OpenAPI без инфраструктуры); нет зависимости → 503 при обращении.
- Задача `media.generate_asset_set` (`task_id = <id>:<generation>`) ставится ДО коммита: в consumer коммит делает `EventProcessor`. Воркер: набора/поколения не видно → исключение и ретрай очереди; устаревшее поколение → no-op.
- Consumer: use-cases работают в SAVEPOINT внутри транзакции inbox (`SqlMediaUnitOfWork(session=...)`). Устаревшие по `version` события отбрасываются.
- Блокировка единицы удаляет объекты ассетов сразу (выданные ссылки перестают работать до TTL); разблокировка — новое поколение генерации.
- Ключи объектов `a/<128 бит hex>.<ext>`; объект без строки в `asset_levels` недоступен (ссылки выдаются только по БД).
- Аудио: `-ss/-t` стоят ДО `-i` (уровень физически не содержит позднего звука); длительность файла = отрывок + 1 AAC-кадр тишины (23 мс).
- `AssetBlockedError` — наследник `gv_core.GoneError`: 410 `asset.blocked` отдаёт общий обработчик `gv_service` (своих Problem/`problem_responses` в сервисе нет).
- Версия `AssetsReady`/`AssetsFailed` монотонна по `media_item_id` (= `aggregate_id`), а не по набору: счётчик `media_items_state.events_version`, инкремент `items.next_event_version` в транзакции записи события в outbox (возможны пропуски). Порядок блокировок: строка единицы → набор (как в consumer).
- Admin-мутации (upload, regenerate): `admin.access` + своё право, `gv_service.IdempotencyKey`, `gv_service.Reason`; ключ и ответ — `gv_db.idempotency.run_idempotent` в транзакции мутации (`uow.idempotency`, таблица `idempotency_keys`). Повтор upload отвечает до перекодирования файла. Чтение `GET /admin/v1/asset-sets` — `admin.access`.
- Лимит выдачи ссылок в Redis — fail-closed: 503 с `details.retry_after` (`GV_MEDIA__LINK_LIMITER_RETRY_AFTER_S`) → заголовок `Retry-After`. Журнал `link_issuance` хранит только sha256 субъекта (`subject_hash`) и `client_id`, чистит его задача relay `media.link_issuance.purge` порциями.
- Тексты загадок: из `MediaItemCreated/Updated` (одобренные `clue_texts` + год, жанры, страны, персоны, без названия) — один исходник `kind=text` с содержимым в строке БД, id = uuid5(единица, sha256 содержимого): правка → новый исходник → новое поколение. Ассет уровня — JSON `text_asset.v1` (`application/json`, ключ `a/<hex>.json`).
- Загрузка вне local/ci требует `origin`/`source_url`/`license_note` (D6, `source.provenance_required`).
- Генерация тяжёлая (ffmpeg, Pillow): воркер берёт не больше `GV_TASK_PREFETCH` задач (по умолчанию 2), лимит памяти контейнера — `memory_mb` в `infra/services.toml`.
- Один `MediaContainer` на процесс (`container_for`); его `close` (ленивое соединение RabbitMQ) регистрируется в `Infrastructure.add_closer` — закрывается для любой команды.
- Тестовые фейки портов сервиса — в `tests/conftest.py` (фикстура `media`, хуки сбоев `fail_commit`/`after_commit`), без правки `sys.path`. `load_example` по умолчанию даёт новый `event_id`; `fresh_id=False` — только для проверки дублей.

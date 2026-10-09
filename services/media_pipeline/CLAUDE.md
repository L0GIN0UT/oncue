# media_pipeline

Генерация ассетов загадок по уровням, хранение в S3, подписанные ссылки, проверка UGC-изображений.

- Пакет: `media_pipeline_service`; запуск: `media_pipeline api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: нет (только internal)
- Зависимости: PostgreSQL, Redis (лимит выдачи ссылок), Redpanda, RabbitMQ, S3/MinIO, ffmpeg
- Статус: этап 1 — генерация ассетов `audio_intro`/`frame_progressive`/`poster_masked`, подписанные ссылки (ADR 0012), admin-загрузка исходников.

## Особенности
- Новый ClueType = генератор в `application/generators` + регистрация в `infrastructure/container.py` + схема `contracts/clues/*`. Use-cases не ветвятся по типу.
- Профили уровней — `media_pipeline_service/config/asset_profiles.yaml` (внутри пакета: образ ставится `--no-editable`); модели в `domain/clues.py` повторяют `contracts/clues` (сверяет contract-тест) и требуют монотонности уровней. Смена params → новый `params_hash` → новые наборы.
- `api` и `infrastructure` — независимые слои: роутеры получают готовые use-cases, сборка — в `main.py` через `MediaContainer`. Адаптеры создаются без подключений (экспорт OpenAPI без инфраструктуры); нет зависимости → 503 при обращении.
- Задача `media.generate_asset_set` (`task_id = <id>:<generation>`) ставится ДО коммита: в consumer коммит делает `EventProcessor`. Воркер: набора/поколения не видно → исключение и ретрай очереди; устаревшее поколение → no-op.
- Consumer: use-cases работают в SAVEPOINT внутри транзакции inbox (`SqlMediaUnitOfWork(session=...)`). Устаревшие по `version` события отбрасываются.
- Блокировка единицы удаляет объекты ассетов сразу (выданные ссылки перестают работать до TTL); разблокировка — новое поколение генерации.
- Ключи объектов `a/<128 бит hex>.<ext>`; объект без строки в `asset_levels` недоступен (ссылки выдаются только по БД).
- Аудио: `-ss/-t` стоят ДО `-i` (уровень физически не содержит позднего звука); длительность файла = отрывок + 1 AAC-кадр тишины (23 мс).
- `AssetBlockedError` — наследник `gv_core.GoneError`: 410 `asset.blocked` отдаёт общий обработчик `gv_service` (своих Problem/`problem_responses` в сервисе нет).
- Версия `AssetsReady`/`AssetsFailed` монотонна по `media_item_id` (= `aggregate_id`), а не по набору: счётчик `media_items_state.events_version`, инкремент `items.next_event_version` в транзакции записи события в outbox (возможны пропуски). Порядок блокировок: строка единицы → набор (как в consumer).
- Admin-мутации (upload, regenerate): `admin.access` + своё право, `gv_service.IdempotencyKey`, `gv_service.Reason`; ключ и ответ — `gv_db.idempotency.run_idempotent` в транзакции мутации (`uow.idempotency`, таблица `idempotency_keys`). Повтор upload отвечает до перекодирования файла. Чтение `GET /admin/v1/asset-sets` — `admin.access`.
- Лимит выдачи ссылок в Redis — fail-closed: 503 с `details.retry_after` (`GV_MEDIA__LINK_LIMITER_RETRY_AFTER_S`) → заголовок `Retry-After`. Журнал `link_issuance` хранит только sha256 субъекта (`subject_hash`), чистится из `IssueAssetLink` не чаще раза в 10 мин.
- Один `MediaContainer` на процесс (`container_for`); ленивое соединение RabbitMQ закрывается на shutdown приложения (api). В процессах consumer/worker без api хука остановки нет — соединение закрывается вместе с процессом.
- Тестовые фейки портов сервиса — в `tests/conftest.py` (фикстура `media`, хуки сбоев `fail_commit`/`after_commit`), без правки `sys.path`. `load_example` по умолчанию даёт новый `event_id`; `fresh_id=False` — только для проверки дублей.

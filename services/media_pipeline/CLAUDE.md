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
- 410 `asset.blocked` отдаётся вручную (`api/common.gone_on_blocked`): в `gv_service.problems` нет класса для 410.
- Лимит выдачи ссылок в Redis — fail-closed (503). Журнал `link_issuance` чистится из `IssueAssetLink` не чаще раза в 10 мин.
- Тестовые фейки портов сервиса — в `tests/conftest.py` (фикстура `media`), без правки `sys.path`. У всех примеров `contracts/events` один `event_id` — в тестах брать копию с новым id.

# game

Соло-движок: сессии, раунды, ответы, очки; реестр плагинов загадок; конфиги режимов; дейлик, больше/меньше, топ-32.

- Пакет: `game_service`; запуск: `game api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/game/v1/...` на gateway; admin — `/admin/v1/mode-configs|daily-sets|projection`
- Зависимости: PostgreSQL, Redis, Redpanda; `gv_game_kernel` (правила), `gv_text` (нормализация)
- Статус: этап 3b — игра по сборнику (`docs/plans/stage-3b.md` §7, ADR 0028, 0029); этап 3a — марафон, «больше/меньше», сетка топ-32, ревил другим типом, рекорды, запрос ассетов (`docs/plans/stage-3a.md` §5); этап 2 — конфиги, дейлик, соло-сессии, проекции

## Особенности
- Контракт API зафиксирован в 0a (§2.6 плана): схемы — `api/public/schemas.py`, `api/admin/schemas.py`; правка схемы = правка `contracts/openapi/game.json` (`gv contracts export`).
- Правила игры — только в `libs/game_kernel` (плагины, `ModeEngine`); game оркестрирует, без `if type == ...`.
- Конфиг режима: контракт `contracts/modes/mode_config.v1.json`, модели — `gv_game_kernel.spec` (меняет только шаг 0); сиды — `seeds/game/modes/*.yaml`.
- Ассеты ищутся по `(media_item_id, clue_type, params_hash(clue.params))` из `AssetsReady` (D7); очки уровней — в `scoring.params.points`.
- Античит (D11): до ревила в ответах нет `media_item_id`, названия, `asset_set_id`; ассет — только уровня ≤ текущего; чужая сессия — 404.
- Сессии — в Postgres (D12): ход = строка под `FOR UPDATE` + `expected_version` + `Idempotency-Key` + outbox в одной транзакции; вызов media-pipeline — вне транзакции.
- Возможности режима — у ядра (`application/rules.py`): протоколы источника (`RoundSource`, `ProgressiveSource`, `SubjectSource`, `PeriodSource`), `presentation`, `reveal_playable_for`; из параметров — только общие поля периода (`timezone`, `schedule_ahead_*`, `exclude_recent_days`, `period`). Слой api получает use-cases через `ApiServices` (сборка — `main.py`).
- Раунды выдаются лениво (D4): план — в `state.plan` (скрыт), после каждого раунда `_settle` спрашивает `should_end`/`followups` и выдаёт следующий; в снимке невыданные раунды — пустые `pending`. `state_schema` 2, без поля — v1 этапа 2 (`tests/unit/test_state_schema.py`).
- Набор дня = набор периода (`daily_sets.period`, `day` — начало периода по `period_start` ядра, тема сетки — `subject_id`); одна попытка на период. Сетка публикует `BracketCompleted` вместо `GameFinished`.
- Рекорды/гистограмма (`features.records`) и статистика выбора сетки — в транзакции хода, только пользователи. Ревил другим типом: единица играбельна при обоих наборах (`uow.candidates_for(reveal)`).
- Кэш конфигов: в процессе (TTL) + поколение `game:modes:gen` в Redis; версии неизменяемы. Ссылки ревила кэшируются в процессе до истечения.
- Тесты идут на реальных плагинах ядра (`build_registry()` → `register_builtin`); фильтры SQL-адаптера `CandidatePort` обязаны совпадать с `gv_testkit.game.FakeCandidatePort.matches` (`tests/integration/test_candidates.py`).
- 409 `game.desync`: `details = {"session": SessionSnapshot}`; после 401 от media-pipeline кэш сервисного токена сбрасывается (`TokenProvider.invalidate`).
- Параметр пути admin-ручек — `{mode_key}`: имя `key` конфликтует с параметром зависимости `Idempotency-Key`.
- Периодические задачи (наборы дня, брошенные сессии) — `ServiceDefinition.build_periodic_jobs`, выполняются в relay под advisory lock; должны быть идемпотентными.
- Сборники (3b): проекция `collection_proj`/`collection_items_proj` из `collections.collection.v1` (снимок по общей `version`, удалённый — надгробие); адаптер `SqlCollectionPort` обязан совпадать с `FakeCollectionPort.matches` (`tests/integration/test_collections.py`). Сессия фиксирует `collection_id` и `content_version` в `state.collection` и доигрывается после скрытия. Наборы со `scope_id` (свои эмодзи) — только в сессии своего сборника и первыми; удаляет их game сам по снимку (событий удаления от media нет). Шаблоны `*.collection.*` не входят в `GET /v1/modes` (по умолчанию `kind=play`); их отдаёт `GET /v1/modes?kind=collection` (с `round_options`/`min_items` конфига), играбельность сборника — `GET /v1/collections/{id}/modes`.

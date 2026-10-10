# libs/game_kernel (`gv_game_kernel`)

Ядро игрового движка без IO: модели `ModeConfig`, порты, интерфейсы плагинов, реестр, валидатор конфига, правила раунда, встроенные плагины, `params_hash`. Используют game (соло) и match (этап 4). Описание — `docs/02-game-engine.md`, план — `docs/plans/stage-2.md` §2.3, §4.

## Устройство
- `spec.py` — модели конфига (меняет только шаг 0, сверяются с `contracts/modes/mode_config.v1.json`).
- `plugins.py` — протоколы `ClueType`, `Source`, `AnswerMatcher`, `PartialRule`, `Scorer`; `ports.py` — `AssetPort`, `CandidatePort`, `CollectionPort`, `PoolQuery`.
- `round.py` — неизменяемый `RoundState` и переходы (`apply_guess`, `apply_skip`, `give_up`, `void`, `require_level_open`, `round_outcome`).
- `registry.py` — `PluginRegistry`, `EngineFactory.validate` (все проблемы с JSON-путём и кодом `config.*` / `schema.*`) и `build`.
- `engine.py` — `ModeEngine`, фасад для сервиса: уровни, `draw_context`/`draw`, `answer` (сверка + переход), `skip`/`give_up`/`void`, `clue_view`/`final_clue` с проверкой открытого уровня, `score`/`max_score`, `pool_query`/`min_playable` для проверки пула при публикации.
- `builtin/` — `register_builtin(registry)`: загадки `audio_intro`, `frame_progressive`, `poster_masked`; источники `daily`, `random`; matcher `title_alias`; частичный ответ `artist`; очки `by_level`, `by_speed`. `collection` — отдельно `register_collection` (в game с этапа 3). `daily_seed(secret, mode_key, day)` — seed набора дня.

## Особенности
- Зависимости — только `gv_core`, `gv_text`, `pydantic` (import-linter `game_kernel-pure`): никаких HTTP, БД, брокеров, `pydantic_settings`. `zoneinfo` (stdlib) нужен `daily` — в образе сервиса должны быть данные tz.
- Модели параметров плагинов обязаны совпадать с `contracts/clues/*` и `contracts/modes/params/*`: contract-тест сравнивает поля, типы, границы и `uniqueItems` (`tests/test_contracts.py`). Правило сверх схемы — только перекрёстной проверкой (`cross_check`, код `config.*`) или валидатором модели (код `schema.<тип>`).
- Семантика сверки ответов — golden `tests/golden/matcher.yaml`; ответ нормализуется `gv_text` с заменами союзов (`engine.answer_normalizer()`), алиасы каталога — тем же нормализатором повторно.
- Розыгрыш детерминирован: кандидаты сортируются по `id`, затем `SeededRandom(ctx.seed).sample`. `CandidatePort` обязан фильтровать так же, как `gv_testkit.game.FakeCandidatePort.matches`.
- `params_hash` повторяет алгоритм media-pipeline; обе стороны сверяются с `contracts/clues/params_hash.golden.json`.
- Ошибки — наследники `gv_core.errors`: HTTP-статус выбирается по классу (`LevelLockedError`, `ActionNotAllowedError` → 403, `RoundFinishedError`, `RoundNotFinishedError` → 409, `ConfigError`, `PresetNotFoundError` → 422).
- Тесты — DSL `gv_testkit.game.given_mode(cfg).with_items(...).when_guess(...).then_round(...).then_score(...)` и фейки портов оттуда же.

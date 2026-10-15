# libs/game_kernel (`gv_game_kernel`)

Ядро игрового движка без IO: модели `ModeConfig`, порты, интерфейсы плагинов, реестр, валидатор конфига, правила раунда, встроенные плагины, `params_hash`. Используют game (соло) и match (этап 4). Описание — `docs/02-game-engine.md` (§5.1, §5.1a), планы — `docs/plans/stage-2.md` §4, `docs/plans/stage-3a.md` §4.

## Устройство
- `spec.py` — модели конфига (меняет только шаг 0, сверяются с `contracts/modes/mode_config.v1.json`).
- `plugins.py` — протоколы `ClueType`, `Source`, `AnswerFormat`, `AnswerMatcher`, `PartialRule`, `Scorer`, `EndRule`, состав раунда `RoundDraw`/`OptionSpec`/`RoundPick` и возможности источника `RoundSource`, `ProgressiveSource`, `SubjectSource`, `ComparisonSource`, `PeriodSource` (runtime-протоколы); `ports.py` — `AssetPort`, `CandidatePort`, `CollectionPort`, `PoolQuery`, `SubjectQuery`.
- `round.py` — неизменяемый `RoundState` и переходы (`apply_guess`, `apply_skip`, `give_up`, `void`, `require_level_open`, `round_outcome`).
- `registry.py` — `PluginRegistry`, `EngineFactory.validate` (все проблемы с JSON-путём и кодом `config.*` / `schema.*`) и `build`.
- `engine.py` — `ModeEngine`, фасад для сервиса: уровни, `draw_context`/`draw`/`draw_rounds`/`compose_rounds`, `followups`/`planned_rounds`/`round_stage`, `subject_query`, `period_start`/`theme_query`, `options_view`/`shown_view`, `answer`/`check` (вид ответа по формату, `draw` для `pick`), `skip`/`give_up`/`void`, `clue_view`/`final_clue`/`final_view` (ревил по `reveal_asset`), `should_end`/`lives_left`/`streak`, `score`/`max_score`, `pool_query`/`min_playable`.
- `builtin/` — `register_builtin(registry)`: 9 загадок, источники `daily`, `random`, `marathon`, `pair`, `bracket`; форматы `classic`, `pick`; matcher-ы `title_alias`, `metric_compare`, `preference`; частичный `artist`; очки `by_level`, `by_speed`, `streak`, `none`; завершение `all_rounds`, `lives`. `collection` — отдельно `register_collection`. `daily_seed(secret, mode_key, day)` — seed набора дня (и периода сетки).

## Особенности
- Зависимости — только `gv_core`, `gv_text`, `pydantic` (import-linter `game_kernel-pure`): никаких HTTP, БД, брокеров, `pydantic_settings`. `zoneinfo` (stdlib) нужен `daily` — в образе сервиса должны быть данные tz.
- Модели параметров плагинов обязаны совпадать с `contracts/clues/*` и `contracts/modes/params/*`: contract-тест сравнивает поля, типы, границы и `uniqueItems` (`tests/test_contracts.py`). Правило сверх схемы — только перекрёстной проверкой (`cross_check`, код `config.*`) или валидатором модели (код `schema.<тип>`).
- Семантика сверки ответов — golden `tests/golden/matcher.yaml` и `matcher_pick.yaml` (формат `pick`); ответ нормализуется `gv_text` с заменами союзов (`engine.answer_normalizer()`), алиасы каталога — тем же нормализатором повторно.
- Совместимость плагинов — только атрибутами и протоколами (`answer_formats`, `single_answer`, `requires_comparison`, `requires_early_end`/`ends_early`), без `if` по ключам. Тестовые конфиги режимов 3a — `tests/modes/*.yaml` (сиды game — поток B).
- Розыгрыш детерминирован: кандидаты сортируются по `id`, затем `SeededRandom(ctx.seed).sample`. `CandidatePort` обязан фильтровать так же, как `gv_testkit.game.FakeCandidatePort.matches`.
- `params_hash` повторяет алгоритм media-pipeline; обе стороны сверяются с `contracts/clues/params_hash.golden.json`.
- Ошибки — наследники `gv_core.errors`: HTTP-статус выбирается по классу (`LevelLockedError`, `ActionNotAllowedError` → 403, `RoundFinishedError`, `RoundNotFinishedError` → 409, `ConfigError`, `PresetNotFoundError`, `AnswerKindError`, `OptionNotFoundError`, `SubjectRequiredError`, `TooManySubjectsError` → 422).
- Тесты — DSL `gv_testkit.game.given_mode(cfg).with_items(...)|with_draws(...).when_guess(...)|when_pick(...).then_round(...).then_score(...).then_session_ended()` и фейки портов оттуда же.

# libs/game_kernel (`gv_game_kernel`)

Ядро игрового движка без IO: модели `ModeConfig`, порты, интерфейсы плагинов, реестр, правила раунда, `params_hash`. Используют game (соло) и match (этап 4). Описание — `docs/02-game-engine.md`, план — `docs/plans/stage-2.md` §2.3, §4.

## Особенности
- Статус: скелет шага 0a. Готово полностью: `spec.py`, `params.py`, `errors.py`, `PluginRegistry`; `EngineFactory.validate` проверяет только ключи плагинов. Переходы `round.py`, `ModeEngine`, встроенные плагины и остальные правила валидатора — поток A (`TODO(stage2/A)`).
- `spec.py` меняет только шаг 0: модели обязаны совпадать с `contracts/modes/mode_config.v1.json` (contract-тест `tests/test_contracts.py`).
- Зависимости — только `gv_core`, `gv_text`, `pydantic` (import-linter `game_kernel-pure`): никаких HTTP, БД, брокеров, `pydantic_settings`.
- `params_hash` повторяет алгоритм media-pipeline; обе стороны сверяются с `contracts/clues/params_hash.golden.json`.
- Ошибки — наследники `gv_core.errors`: HTTP-статус выбирается по классу (`LevelLockedError` → 403, `RoundFinishedError` → 409, `ConfigError` → 422).
- Случайность — только `gv_core.random.Random` (`SeededRandom` для детерминированного розыгрыша).

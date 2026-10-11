# Контракты

Источник истины для взаимодействия сервисов (docs/architecture/communication.md §3).

| Путь | Кто пишет | Как меняется |
|---|---|---|
| `openapi/<service>.json` | генерируется из кода | `uv run gv contracts export` (руками не править — хук заблокирует) |
| `events/envelope.v1.json` | генерируется из `gv_messaging.EventEnvelope` | `uv run gv contracts export` |
| `events/<service>/<Event>.v<N>.json` | **руками**, contract-first | Совместимо: новое необязательное поле. Иначе — новая версия `v<N+1>` рядом со старой |
| `events/<service>/examples/<Event>.v<N>.json` | руками | Пример конверта: вход для тестов потребителей (`gv_testkit.load_example`) |
| `clues/<clue_type>.params.v<N>.json` | руками | Параметры уровней загадок (media-pipeline, game_kernel) |
| `clues/params_hash.golden.json` | руками (генерируется алгоритмом) | Golden-векторы `params_hash`: их проверяют media-pipeline и game_kernel |
| `modes/mode_config.v1.json`, `modes/params/*.v<N>.json` | руками, contract-first | Конфиг режима и параметры плагинов (`source.*`, `scorer.*`, `matcher.*`, общий `pool`); модели — `gv_game_kernel.spec` |

Проверки: `uv run gv contracts check [--base origin/main]` (совместимость всех `*.json`, включая `modes/**`) и тесты `tools/gv/tests/test_contracts.py` (схемы валидны, у каждого события есть валидный пример), `libs/game_kernel/tests/test_contracts.py` (модели ↔ `modes/**`, сиды `seeds/game/modes`).
В тестах продюсера: `gv_testkit.assert_event_matches_schema(envelope)`.

# Контракты

Источник истины для взаимодействия сервисов (docs/architecture/communication.md §3).

| Путь | Кто пишет | Как меняется |
|---|---|---|
| `openapi/<service>.json` | генерируется из кода | `uv run gv contracts export` (руками не править — хук заблокирует) |
| `events/envelope.v1.json` | генерируется из `gv_messaging.EventEnvelope` | `uv run gv contracts export` |
| `events/<service>/<Event>.v<N>.json` | **руками**, contract-first | Совместимо: новое необязательное поле. Иначе — новая версия `v<N+1>` рядом со старой |
| `events/<service>/examples/<Event>.v<N>.json` | руками | Пример конверта: вход для тестов потребителей (`gv_testkit.load_example`) |
| `clues/<clue_type>.params.v<N>.json` | руками | Параметры уровней загадок (media-pipeline, game_kernel) |

Проверки: `uv run gv contracts check [--base origin/main]` и тесты `tools/gv/tests/test_contracts.py` (схемы валидны, у каждого события есть валидный пример).
В тестах продюсера: `gv_testkit.assert_event_matches_schema(envelope)`.

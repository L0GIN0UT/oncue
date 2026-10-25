# 0070. Переопределения `clue` и `answer.format` хозяина лобби в плане раундов

- **Статус:** черновик (уточняет [0033](0033-match-round-plan-from-game.md), [0034](0034-rooms-process-lease-snapshots.md))
- **Дата:** 2026-10-25

## Контекст
Шаблон лобби (`session.lobby.overridable`, ADR 0032) перечисляет варианты `clue` и
`answer_format`, но `round-plans` принимал только режим, сид и сборники: match отказывал
хозяину с `reason = not_supported_yet`. Строить план по базовому конфигу нельзя: ассеты и
варианты `pick` зависят от переопределённой загадки и формата.

## Решение
- `POST /internal/v1/round-plans` принимает необязательные `overrides {clue_type,
  answer_format}`. Допустимость — только из `session.lobby.overridable` опубликованной версии:
  общий помощник ядра `gv_game_kernel.apply_lobby_overrides` (его же использует match при
  сборке правил). Нет в списке или у режима нет лобби — 422 `game.override_not_allowed`
  (`details.field`).
- Эффективное тело game прогоняет через тот же `EngineFactory.validate`, что и публикацию
  конфига (формат × загадка × источник × matcher × состязание, число уровней). Отказ — 422
  `game.override_invalid` (`details.errors = [{path, code}]`). Розыгрыш, ключи ответа,
  `options` и поиск `AssetsReady` (`clue_type` + `params_hash`) идут по правилам
  эффективного конфига: пул должен быть играбелен именно под переопределённую загадку
  (иначе `game.pool_too_small`). В ответе `mode` — базовая версия; match применяет те же
  переопределения к телу сам.
- match: `apply_settings` разрешает `clue_type`/`answer_format` только из
  `template.options` (значение шаблона допустимо), передаёт отличия от шаблона в план,
  хранит их в снимке (`RoomSettings`, как раньше) и показывает варианты в `RoomView.options`.
  Отказ game — 422 `match.setting_not_allowed` (`details.field`, `reason`:
  `not_allowed` | `incompatible`, `errors`); комната остаётся в лобби, матч не стартует.

## Не решено (нужно решение владельца)
- Сиды лобби не включают `clue`/`answer_format` в `overridable`: набор вариантов и их
  `params` — контентное решение; для включения нужны готовые ассеты под каждый вариант.
- Варианты `clue` выбираются по типу (первый с таким `type`); два варианта одного типа с
  разными `params` различить нельзя — нужен идентификатор варианта.
- Подсчёт очков и `pick`: смена формата на `pick` требует `attempts_per_level = 1` и
  `answer.params.distractors` — у шаблона с `classic` их нет, поэтому валидатор честно
  отказывает (`incompatible`). Режим, где `pick` включаем, нужно проектировать отдельным
  шаблоном; автоматическая подмена параметров очков не делается.
- Фронт: показ выбора `clue_type`/`answer_format` в лобби и текста `incompatible` не делался.

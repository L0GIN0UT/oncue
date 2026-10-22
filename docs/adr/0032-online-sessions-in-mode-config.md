# 0032. Онлайн-сессии в `ModeConfig`: `OnlineSessionSpec`, плагин `ContestRule`, `party_speed`, шаблоны лобби

- **Статус:** принято (план этапа 4, решения владельца 2026-10-22; реализовано в 4a, 2026-10-22)
- **Дата:** 2026-10-22
- **Уточняет:** [0008](0008-plugin-game-engine.md), [0023](0023-round-draw-answer-formats-end-rules.md)

## Контекст
Онлайн-режимы (дуэль 1×1, party 3–6, кастомные лобби) используют те же загадки, источники,
форматы ответа и matcher-ы, что соло. Отдельные конфиги в match дали бы второй редактор,
второй валидатор и расхождение плагинов. В [движке §8](../02-game-engine.md) правила дуэли
описаны флагами (`level_lock_on_first_correct`) — это ветвление по виду матча в коде.

## Решение
- **`session` — дискриминированное объединение по `kind`** (`contracts/modes/mode_config.v1.json`,
  `oneOf` из `$defs/SoloSession` и `$defs/OnlineSession`): `solo` — без изменений с этапа 2;
  `duel | party | lobby` — `OnlineSessionSpec {players{min,max}, rounds, countdown_s,
  level_windows_s[], reveal_s, contest{type,params}, disconnect_grace_s, ranked, ladder?,
  lobby?, chat}`. У онлайн-сессии нет `allow_guest` ([0036](0036-online-account-only-chat-and-ratings.md)).
  Модели ядра — `SoloSessionSpec` (прежнее имя `SessionSpec` оставлено псевдонимом) и
  `OnlineSessionSpec`; путь ошибки разбора не содержит тега ветки (`session.rounds`).
- **game играет только `solo`** (`ModeEngine.solo_session` → 422 `game.mode_not_solo`),
  match — только онлайн. Конфиги онлайн-режимов публикуются и редактируются в game тем же
  редактором admin.
- **Состязание — плагин ядра `ContestRule`** (`race` — дуэль: первый точный ответ закрывает
  уровень для всех; `parallel` — party: все отвечают в окне уровня). Многоигровое состояние —
  `ContestRoundState` (общий уровень и окно) + `PlayerRound` на игрока; переходы чистые,
  время — аргумент `now_ms`. Правила ходов игрока плагин отдаёт как `RoundRules` (протокол,
  которому удовлетворяет и `SoloSessionSpec`) — `round.py` не знает вида сессии. Совместимость
  с форматом ответа — атрибут `ContestRule.answer_formats`.
- **Очки:** дуэль — `by_level`; party — новый `Scorer` `party_speed`
  (`max(1, round(max_points × k × clamp(1 − t/T)))`, `k = 1 | partial_ratio`, `t` — от
  открытия уровня по часам сервера). Места и исход дуэли — `MatchOutcome.rank` (ничьи `1, 1, 3`,
  вышедшие — после оставшихся).
- **Шаблон лобби** — `session.lobby {overridable, visibility}`: хост выбирает только из
  списков разрешённых значений (`rounds`, `level_window_s`, `answer_format`, `clue`,
  `players_max`, `visibility`), значение шаблона обязано входить в список. Схема
  `overridable` — внутри `mode_config.v1.json` (`$defs/LobbySpec`), а не отдельным файлом
  `params/`: это не параметры плагина, их читает и редактор admin.
- **Несколько сборников в лобби:** `DrawContext.collection_ids` и
  `source.collection.params.max_collections` (по умолчанию 1 — соло не меняется; уточняет
  [0028](0028-collections-versions-draft-projection.md)).
- **Совместимость контракта:** `gv contracts check` считает объект, ставший `oneOf`/`anyOf`,
  совместимым, если хотя бы одна ветка не ломает прежнюю схему (локальные `$ref` раскрываются).

## Последствия
- Новый вид матча — новый `ContestRule`, без `if kind == ...` в match.
- Соло не затронуто: сиды и сохранённые конфиги 2–3b валидны (регресс-тест на сидах).
- Ядро получает многоигровое состояние, и его инварианты (попытки, блокировка уровня,
  детерминизм порядка ходов) проверяются property-тестами потока A.

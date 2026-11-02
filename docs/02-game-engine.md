# Игровой движок: плагины и конфиги

> Статус: принято (v1, §4–5 уточнены [ADR 0020](adr/0020-mode-config-assets-by-params-hash.md)). Продуктовая сторона: [01-concept.md](01-concept.md) §2–6. Решение: [ADR 0008](adr/0008-plugin-game-engine.md).

## 1. Где живёт

- `libs/game_kernel` — **общее ядро** (чистый Python, без IO): интерфейсы, реестр, базовые плагины, подсчёт очков, матчинг ответов. Его используют **game** (соло) и **match** (онлайн). Так правила игры описаны один раз (DRY), а различается только оркестрация: последовательная в соло и по таймерам в матче.
- Плагины, которым нужны внешние данные, получают их через **порты** (`AssetPort`, `CandidatePort`, `CollectionPort`). Реализации портов есть в каждом сервисе.

## 2. Модель

```
ModeConfig ──┬── domain            (music | film | …)
             ├── source:     SourceSpec      → плагин Source
             ├── clue:       ClueSpec        → плагин ClueType (+ уровни)
             ├── answer:     AnswerSpec      → плагин AnswerFormat + AnswerMatcher
             ├── scoring:    ScoringSpec     → плагин Scorer
             ├── session:    SessionSpec     (solo | duel | party | lobby + таймеры, раунды)
             └── rewards:    RewardsSpec     (опыт, валюта, задания — в progression)
```

Игровая сессия = `ModeConfig@version` + seed + список раундов. Каждый раунд = `MediaItemRef` + состояние уровней загадки + история ответов.

## 3. Интерфейсы (сокращённо)

```python
class ClueType(Protocol):
    key: ClassVar[str]  # "audio_intro", "frame_progressive", …
    domains: ClassVar[frozenset[str]]  # где применим
    params_schema: ClassVar[type[BaseModel]]  # валидация параметров из конфига

    def levels(self, params: BaseModel) -> Sequence[LevelSpec]: ...
    def required_assets(self, item: MediaItemRef, params: BaseModel) -> AssetRequest: ...
    def reveal(self, item: MediaItemRef, level: int, assets: AssetPort) -> ClueView: ...


class Source(Protocol):
    key: ClassVar[str]  # "daily", "collection", "random", "marathon", "pair", "bracket"

    def draw(self, ctx: DrawContext, n: int) -> Sequence[MediaItemRef]: ...


class AnswerMatcher(Protocol):
    def match(self, raw: str, item: MediaItemRef, aliases: AliasIndex) -> MatchResult: ...

    # MatchResult: exact | partial(kind="artist"|"franchise"…) | miss


class Scorer(Protocol):
    key: ClassVar[str]  # "by_level", "by_speed", "streak_until_miss", "party_speed"

    def score(self, outcome: RoundOutcome, params: BaseModel) -> int: ...
```

- **Реестр:** `registry.register(AudioIntroClue)` при старте. Фабрика собирает движок из `ModeConfig`. Неизвестный ключ — ошибка валидации конфига при публикации, а не во время игры.
- **Совместимость** объявляет сам плагин (`domains`, `supported_answer_formats`, `supported_sessions`). Валидатор конфига проверяет, что набор кубиков совместим.
- Новый тип загадки = новый класс + регистрация + обработчик ассетов в media-pipeline. Ядро и остальные плагины не меняются (OCP).

## 4. Уровни

Уровни загадки описывает `clue.params` — это **те же параметры, что у профиля media-pipeline**: game находит готовые ассеты по `(media_item_id, clue_type, params_hash(clue.params))` из `AssetsReady`. Поэтому очков в уровнях нет: они задаются Scorer-ом в `scoring.params.points`, и правка очков не меняет хэш ([ADR 0020](adr/0020-mode-config-assets-by-params-hash.md)). Число уровней = число попыток (`session.attempts_per_level` ответов на уровень); «Пропустить» открывает следующий уровень, если `session.skip_allowed`.

```yaml
# audio_intro (профиль audio_intro/default)
clue:
  type: audio_intro
  params:
    start_s: 0
    levels: [{ seconds: 0.5 }, { seconds: 1 }, { seconds: 2 }, { seconds: 4 }, { seconds: 8 }, { seconds: 15 }]
scoring: { type: by_level, params: { points: [6, 5, 4, 3, 2, 1], partial_ratio: 0.5 } }
```

```yaml
# frame_progressive (профиль frame_progressive/default)
clue:
  type: frame_progressive
  params:
    levels:
      - { blur_px: 25, crop: 0.35, max_width: 960 }
      - { blur_px: 20, crop: 0.5,  max_width: 960 }
      - { blur_px: 15, crop: 0.7,  max_width: 960 }
      - { blur_px: 10, crop: 1.0,  max_width: 960 }
      - { blur_px: 5,  crop: 1.0,  max_width: 960 }
      - { blur_px: 0,  crop: 1.0,  max_width: 960 }
```

```yaml
# description (этап 3a, профиль description/default): уровень ДОБАВЛЯЕТ поля к открытым,
# подсказка уровня (levels[].hint.fields) — все открытые к нему поля
clue:
  type: description
  params:
    levels:
      - { fields: [tagline] }
      - { fields: [plot_masked] }
      - { fields: [year, genres] }
      - { fields: [country] }
      - { fields: [director] }
      - { fields: [cast_top3] }
```

Параметры описывает `params_schema` конкретного ClueType (контракты — `contracts/clues/*`); валидатор сверяет длину `scoring.params.points` с числом уровней.

## 5. Пример полного конфига режима

Сиды этапа 2 — `seeds/game/modes/*.yaml`, контракт — `contracts/modes/mode_config.v1.json`, параметры плагинов — `contracts/modes/params/*`. Поля `version` и `status` задаёт сервис, а не автор.

```yaml
key: film.daily.frame
title_i18n: modes.film.daily.frame.title            # ключ next-intl
description_i18n: modes.film.daily.frame.description
domain: film
source:
  type: daily
  params:
    size: 5
    timezone: Europe/Moscow                         # смена дня — 00:00 в этой зоне
    schedule_ahead_days: 2                          # наборы на сегодня и 2 дня вперёд
    exclude_recent_days: 7
    pool: { kinds: [film], min_playable: 10 }       # минимум играбельных для публикации
clue:
  type: frame_progressive
  params:
    levels:
      - { blur_px: 25, crop: 0.35, max_width: 960 }
      - { blur_px: 20, crop: 0.5,  max_width: 960 }
      - { blur_px: 15, crop: 0.7,  max_width: 960 }
      - { blur_px: 10, crop: 1.0,  max_width: 960 }
      - { blur_px: 5,  crop: 1.0,  max_width: 960 }
      - { blur_px: 0,  crop: 1.0,  max_width: 960 }
answer:
  format: classic
  matcher:
    type: title_alias
    params:
      typo: [{ min_len: 5, max_distance: 1 }, { min_len: 10, max_distance: 2 }]
      accept_same_title: true
  partial: []                                       # в музыке — [artist]
scoring:
  type: by_level
  params: { points: [6, 5, 4, 3, 2, 1], partial_ratio: 0.5 }
session:
  kind: solo
  rounds: 5
  attempts_per_level: 1
  skip_allowed: true
  give_up_allowed: true
  allow_guest: true
  abandon_after_min: 1440
rewards: {}                                         # проходит без проверки, используется с этапа 5
features: { share: true }
```

### 5.1. Плагины ядра этапа 2

Регистрирует `gv_game_kernel.builtin.register_builtin(registry)`; game собирает реестр при старте и работает только через фасад `ModeEngine`.

| Вид | Ключ | Что делает | Параметры |
|---|---|---|---|
| ClueType | `audio_intro` | Начало трека растущей длины | `contracts/clues/audio_intro.params.v1.json` |
| ClueType | `frame_progressive` | Кадр, с каждым уровнем чётче и шире | `contracts/clues/frame_progressive.params.v1.json` |
| ClueType | `poster_masked` | Постер под маской | `contracts/clues/poster_masked.params.v1.json` |
| Source | `daily` | Набор дня: детерминированный розыгрыш из пула по `daily_seed(secret, mode_key, day)` | `params/source.daily.v1.json` |
| Source | `random` | Случайные единицы пула, пресеты-фильтры (`presets`) | `params/source.random.v1.json` |
| AnswerMatcher | `title_alias` | Название и алиасы через `gv_text`, допуск опечаток по Дамерау-Левенштейну | `params/matcher.title_alias.v1.json` |
| PartialRule | `artist` | Частичный ответ «угадан исполнитель» (музыка) | — |
| Scorer | `by_level` | Очки по уровню угадывания, частичный × `partial_ratio` | `params/scorer.by_level.v1.json` |
| Scorer | `by_speed` | Очки по времени от выдачи первого ассета раунда (считает сервер) | `params/scorer.by_speed.v1.json` |

Source `collection` (`register_collection`) подключается в game на этапе 3. Розыгрыш детерминирован: кандидаты сортируются по `id`, затем `SeededRandom(seed).sample`.

### 5.1a. Плагины ядра этапа 3a

Тоже регистрирует `register_builtin` ([ADR 0023](adr/0023-round-draw-answer-formats-end-rules.md), [0026](adr/0026-reveal-with-other-clue-type.md), [0027](adr/0027-period-sets-preference-modes-records.md); план — [stage-3a](plans/stage-3a.md) §4). Параметры — `contracts/clues/<key>.params.v1.json` и `contracts/modes/params/<вид>.<key>.v1.json`.

| Вид | Ключ | Что делает |
|---|---|---|
| ClueType | `audio_reverse` (музыка) | Отрывок с `start_s` задом наперёд; подсказка `seconds` |
| ClueType | `soundtrack`, `audio_blackout` (кино) | Саундтрек / звук фильма; ревил `audio_blackout` — кадр через `clue.reveal` |
| ClueType | `frame_sequence` (кино) | Разные кадры по позиции исходника; подсказка `frame` |
| ClueType | `description` (кино), `emoji` (музыка, кино) | `kind = text`: JSON-ассет `text_asset.v1`; подсказки — накопленные `fields` / `count` (`null` — все) |
| AnswerFormat | `classic`, `pick` | `pick`: ответ `{option_id}`, один ответ на раунд (загадке — один уровень, `attempts_per_level = 1`) |
| Source | `marathon` | Единицы выбранных игроком персон (`subject_ids`, ≤ `max_subjects`), раундов `min(пул, rounds)`, порядок `shuffle`/`year` |
| Source | `pair` | Цепочка «больше/меньше» по `metric`: соседи не равны и отличаются ≥ `min_gap`; `shown` — опорная, `options` — `higher`/`lower` |
| Source | `bracket` | Сетка 8/16/32 на неделю (`period_start`), посев по seed периода; туры — `followups` из победителей, `size − 1` выборов |
| AnswerMatcher | `metric_compare`, `preference` | Направление метрики против опорной (ничья — `both_correct`); любой выбор верен |
| Scorer | `streak` (`presentation=streak`), `none` (`bracket`) | `points_per_round` за `exact`; без очков |
| EndRule | `all_rounds`, `lives` | `lives {count}`: конец после `count` проигранных раундов (всё, кроме `exact` и `void`) |

Возможности источника движок узнаёт по протоколам, а не по ключу: `RoundSource` (сам составляет `RoundDraw` с `shown`/`options`), `ProgressiveSource` (`followups`, `planned_rounds`, `stage`), `SubjectSource` (выбор персон), `ComparisonSource` (метрика сравнения), `PeriodSource` (начало периода, тема). Совместимость в `EngineFactory.validate` — тоже по атрибутам плагинов: `answer_formats` у источника и matcher, `single_answer` у формата, `requires_early_end` у Scorer и `ends_early` у EndRule.

### 5.1b. Источник `collection` (этап 3b)

Регистрируется отдельно (`register_collection`, [ADR 0028](adr/0028-collections-versions-draft-projection.md); план — [stage-3b](plans/stage-3b.md) §2.6, §4). Конфиг — шаблон режима домена (`<domain>.collection.<clue>`), параметры `contracts/modes/params/source.collection.v1.json`: `round_options ⊆ {5, 10, 25, "all"}`, `min_items`, `shuffle`. Сборник приходит в `POST /v1/sessions` (`collection_id`, `rounds`) и лежит в `DrawContext.collection_id`; единицы — `CollectionPort.items(collection_id, limit, *, playable_for)` в порядке автора, играбельность сначала по набору со `scope` сборника (`PlayableFor.scope`, [ADR 0029](adr/0029-collection-assets-scoped-sets.md)), затем по каталожному. Число раундов — `CollectionSource.rounds_for(params, requested, pool)`: `"all"` — весь пул, иначе запрошенное, если оно есть в `round_options` и не больше пула (`game.rounds_not_allowed`, `game.pool_too_small`); без сборника — `game.collection_required`.

### 5.2. Периодические задачи game

Выполняются в процессе relay (`ServiceDefinition.build_periodic_jobs`) под `pg_try_advisory_xact_lock`, поэтому несколько реплик безопасны; все задачи идемпотентны. Код — `services/game/src/game_service/infrastructure/jobs.py`.

| Задача | Интервал по умолчанию | Что делает |
|---|---|---|
| `game.daily.schedule` | `GV_GAME__SCHEDULE_INTERVAL_S` = 900 с | Создаёт наборы дня на сегодня и `schedule_ahead_days` вперёд на версии конфига, действующей при создании (набор фиксирует `mode_version`); блокирует наступившие дни, заменяя непригодные единицы. Без `GV_GAME__DAILY_SEED_SECRET` не работает (лог `game.daily_seed_secret_missing`) |
| `game.sessions.abandon` | `GV_GAME__ABANDON_INTERVAL_S` = 600 с | Переводит в `abandoned` сессии без хода дольше `session.abandon_after_min` (пачками `GV_GAME__ABANDON_BATCH`) |
| `game.modes.bootstrap` | `GV_GAME__BOOTSTRAP_INTERVAL_S` = 3600 с | Только local/ci и при заданном `GV_GAME__BOOTSTRAP_MODES_DIR`: публикует сиды режимов, у которых ещё нет версий (пул не проверяется), и сразу запускает `game.daily.schedule` |

Кроме них в relay работают встроенные задачи `libs/service`: очистка outbox/inbox и `idempotency_keys`.

## 6. Жизненный цикл конфига

1. Создание и правка в админке (`draft`) с валидацией по схемам плагинов.
2. **Публикация** создаёт неизменяемую версию: событие `ModeConfigPublished`, кэши инвалидируются.
3. Запущенные сессии доигрываются на своей версии (`mode_key@version` в сессии).
4. Откат = публикация предыдущей версии как новой.
5. `features.*` в конфиге — продуктовые настройки режима. Включение режима для части пользователей (процент, роли) — через глобальные фича-флаги Unleash (`libs/flags`).

## 7. Сравнение ответов (AnswerMatcher)

- Нормализация: регистр, `ё→е`, пунктуация, диакритика, пробелы, `&`/«и»/and.
- Совпадение с названием в любом языке, с алиасами из catalog (для музыки — артисты-алиасы, для кино — оригинальное и локализованное название, альтернативные).
- Опечатки: допуск по расстоянию Дамерау-Левенштейна (порог зависит от длины, задаётся в конфиге matcher).
- Частичные ответы (`partial`) — по типу: артист, франшиза, серия. Набор задаётся в конфиге режима.
- Область ответа по персоне — `answer.subject_mode`: `default` (как в `partial`), `title_only` (марафон исполнителя
  или режиссёра: засчитывается только название, частичный ответ по персоне отключён) и `single_subject` (то же, но
  только если сборник состоит из треков или фильмов одной персоны `artist`/`director`; иначе как `default`).
  Персоны области источник отдаёт в `RoundDraw.subjects` (протокол `ScopedSource`: марафон, сборник); game хранит их в
  раунде и показывает в `RoundOut.subjects` для автоподсказки клиента. Ядро не ветвится по ключам плагинов.
- Matcher — тоже плагин: можно заменить на более умный (эмбеддинги) без изменений движка.

## 8. Онлайн-специфика (match)

Решения — [ADR 0032](adr/0032-online-sessions-in-mode-config.md), план — [stage-4](plans/stage-4.md) D2–D4.

**Конфиг.** Тот же `ModeConfig`, `session` — объединение по `kind`: `solo` (как на этапах 2–3) или онлайн `duel | party | lobby` — `OnlineSessionSpec {players{min,max}, rounds, countdown_s, level_windows_s[], reveal_s, contest{type,params}, disconnect_grace_s, ranked, ladder?, lobby?{overridable, visibility}, chat}`. У онлайн-сессии нет `end` и `allow_guest` (схема). game публикует и соло-, и онлайн-конфиги (`EngineFactory(..., supported_sessions=ALL_SESSIONS)`), играет только соло (`ModeEngine.solo_session` → `game.mode_not_solo`); match собирает движок с `ONLINE_SESSIONS` (`ModeEngine.online_session` → `game.mode_not_online`).

**Проверки `validate` для онлайн-сессии** (код `config.*`, путь от корня):

| Правило | Путь | Код |
|---|---|---|
| `contest.type` есть в реестре, `params` проходят схему плагина | `session.contest.type` / `.params.*` | `unknown_plugin` / `schema.*` |
| `answer.format` ∈ `ContestRule.answer_formats` (`race` — `classic`; `parallel` — `classic`, `pick`) | `session.contest.type` | `answer_format_unsupported` |
| формат с одним ответом (`pick`) — одна попытка уровня | `session.contest.params.attempts_per_level` | `single_answer_levels` |
| `players.min ≤ players.max` (`max ≤ 6` — схема) | `session.players.min` | `players_range` |
| `len(level_windows_s)` = число уровней загадки | `session.level_windows_s` | `levels_mismatch` |
| `ranked ⇒ ladder` | `session.ladder` | `ladder_required` |
| `kind: lobby ⇔ lobby` задан | `session.lobby` | `lobby_required` / `lobby_unexpected` |
| свободный чат — только у лобби | `session.chat.free_text` | `free_text_lobby_only` |
| значение шаблона входит в каждый список `lobby.overridable` (окно — только если окна всех уровней равны) | `session.lobby.overridable.<поле>` | `override_default_missing` |
| варианты `players_max ≥ players.min`; варианты `answer_format` известны и поддержаны загадкой, источником, matcher-ом и состязанием; варианты `clue` — валидный `clue` того же домена с тем же числом уровней | `session.lobby.overridable.<поле>[i]…` | `players_range`, `unknown_plugin`, `answer_format_unsupported`, `domain_unsupported`, `levels_mismatch`, `schema.*` |
| Scorer `party_speed` — только у онлайн-сессии | `scoring.params` | `online_only` |

**Состязание — плагин `ContestRule`** (`builtin/contests.py`, `register_builtin`; параметры — `contracts/modes/params/contest.<key>.v1.json`). Состояние раунда — неизменяемый `ContestRoundState` (общий уровень, `level_opened_at_ms`, `level_ends_at_ms`, `level_locked`, `finished`) + `PlayerRound` на игрока (свой `RoundState`, `active`, время оцениваемого ответа). Переходы чистые, время — аргумент `now_ms` (часы match); порядок ходов в одну миллисекунду — порядок вызовов (порядок стрима комнаты).

- Ход игрока применяется к его `RoundState` по правилам `ContestRoundRules` (`attempts_per_level` из параметров, пас можно, сдаваться в раунде нельзя — сдача матча у match). Исчерпал попытки уровня или спасовал — «ответил» и ждёт следующего уровня.
- Ход на закрытом уровне (окно истекло, блокировка) или после исчерпания попыток — `LevelLockedError`; в завершённом раунде (в т. ч. у вышедшего) — `RoundFinishedError`.
- `race` (дуэль): при `lock_on_first_correct` первый `exact` закрывает уровень для всех, следующий уровень открывается сразу со своим окном — соперник может угадать на нём за меньшие очки. Уровень закрывается и тогда, когда никто из активных больше не может на нём ходить.
- `parallel` (party, лобби): уровень закрывается по окну или, при `end_when_all_answered`, когда ответили все активные игроки.
- `next_level` открывает следующий уровень или завершает раунд (уровни кончились или все активные завершили раунд): незавершённые получают `partial` или `miss` (`round.close_round`). Вышедший (`leave`) — `void`, если его раунд ещё шёл.
- Статусы для всех (`views`) — `thinking | answered | missed | guessed` без текстов ответов; уровень угадывания виден только после закрытия этого уровня.

**Фасад для match — `ModeEngine.contest_*`** (match не обращается к плагинам напрямую): `contest_start(player_ids, now_ms)` (окно уровня 1), `contest_guess(state, player, answer, draw, display=, now_ms=)` → `ContestStep(state, match, option)` (сверка тем же matcher-ом, вердикт — только в ответ команды игрока), `contest_skip`, `contest_level_closed`, `contest_deadline_ms` (`phase_ends_at`), `contest_advance` (окно следующего уровня из `level_windows_s`), `contest_round_finished`, `contest_leave`, `contest_views`, `contest_outcome`/`contest_score`/`contest_scores`, `contest_require_level_open` (выдача ассета уровня, D11), `level_window_ms(level)`, `round_rules`.

**Очки.** Дуэль — `by_level` (например, `points: [12, 6]`). Party — Scorer `party_speed`: `max(1, round(max_points × k × clamp(1 − t/T)))`, `k = 1` за точный и `partial_ratio` за частичный, `t` — от открытия уровня, на котором дан оцениваемый ответ (точный, иначе первый частичный), `T` — окно этого уровня (`RoundOutcome.elapsed_ms`, `window_ms`); округление — половина вверх, промах и `void` — 0. Итог матча — `MatchOutcome.rank(scores, left=)`: места с ничьими `1, 1, 3`, вышедшие — после оставшихся (между собой — по очкам), для двух игроков — `win/loss/draw`.

**Несколько сборников лобби.** `DrawContext.collection_ids` (`draw_context(collection_ids=...)`), не больше `source.collection.params.max_collections` (по умолчанию 1; иначе `game.too_many_collections`); пустой список — прежний `collection_id`. Единица, встреченная в предыдущем сборнике, из следующих убирается; при `shuffle` каждый сборник перемешивается по seed, затем сборники чередуются по кругу в порядке лобби; память лобби (`exclude_ids`) — после свежих единиц всех сборников. Один сборник — розыгрыш этапа 3b без изменений.

**Тесты.** DSL `gv_testkit.match.given_match(cfg).with_players(...).with_items(...).at(t).when_guess(player, ...).then_level_closed().then_scores({...})` поверх фасада и фейки портов match: `FakeRoundPlanPort`, `FakeRealtime` (каждое сообщение — по `contracts/realtime`), `FakeLinkPort` (одна ссылка на уровень).

Ladder-ы (например `music.classic`, `film.lite`) объявляются в конфиге rating (этап 4b, D15 плана). Рейтинг ведётся по `(user, ladder)`.

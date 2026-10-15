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
      - { blur_px: 48, crop: 0.35, max_width: 960 }
      - { blur_px: 32, crop: 0.5,  max_width: 960 }
      - { blur_px: 20, crop: 0.7,  max_width: 960 }
      - { blur_px: 12, crop: 1.0,  max_width: 960 }
      - { blur_px: 6,  crop: 1.0,  max_width: 960 }
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
      - { blur_px: 48, crop: 0.35, max_width: 960 }
      - { blur_px: 32, crop: 0.5,  max_width: 960 }
      - { blur_px: 20, crop: 0.7,  max_width: 960 }
      - { blur_px: 12, crop: 1.0,  max_width: 960 }
      - { blur_px: 6,  crop: 1.0,  max_width: 960 }
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
- Matcher — тоже плагин: можно заменить на более умный (эмбеддинги) без изменений движка.

## 8. Онлайн-специфика (match)

- Тот же `ModeConfig` + `session.kind ∈ {duel, party, lobby}` с параметрами: `players: {min, max}`, `rounds`, `round_timer_sec`, `attempts_per_level`, `level_lock_on_first_correct` (опережение в дуэли), `ranked`, `ladder` (ключ рейтинга для rating).
- Подсчёт очков в party — плагин `party_speed`: `max(1, round(max_points × k × clamp(1 − t/T)))`, где `k = 0.5` для частичного ответа.
- Ladder-ы (например `music.classic`, `film.lite`) объявляются в конфиге rating. Рейтинг ведётся по `(user, ladder)`.

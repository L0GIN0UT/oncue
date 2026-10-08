# Игровой движок: плагины и конфиги

> Статус: принято (v1). Продуктовая сторона: [01-concept.md](01-concept.md) §2–6. Решение: [ADR 0008](adr/0008-plugin-game-engine.md).

## 1. Где живёт

- `libs/game_kernel` — **общее ядро** (чистый Python, без IO): интерфейсы, реестр, базовые плагины, подсчёт очков, матчинг ответов. Его используют **game** (соло) и **match** (онлайн). Так правила игры описаны один раз (DRY), а различается только оркестрация: последовательная в соло и по таймерам в матче.
- Плагины, которым нужны внешние данные, получают их через **порты** (`AssetPort`, `CatalogPort`). Реализации портов есть в каждом сервисе.

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

```yaml
# Классика для audio_intro
levels:
  - { reveal: { seconds: 0.5 }, points: 6 }
  - { reveal: { seconds: 1 },   points: 5 }
  - { reveal: { seconds: 2 },   points: 4 }
  - { reveal: { seconds: 4 },   points: 3 }
  - { reveal: { seconds: 8 },   points: 2 }
  - { reveal: { seconds: 15 },  points: 1 }
skip_rules: { allow: true }            # для reverse: { from: [1,2,3], to: 4 }
```

```yaml
# frame_progressive
levels:
  - { reveal: { blur_px: 48, crop: 0.35 }, points: 6 }
  - { reveal: { blur_px: 32, crop: 0.5 },  points: 5 }
  - { reveal: { blur_px: 20, crop: 0.7 },  points: 4 }
  - { reveal: { blur_px: 12, crop: 1.0 },  points: 3 }
  - { reveal: { blur_px: 6,  crop: 1.0 },  points: 2 }
  - { reveal: { blur_px: 0,  crop: 1.0 },  points: 1 }
```

```yaml
# description
levels:
  - { reveal: { fields: [plot_masked] },                                   points: 6 }
  - { reveal: { fields: [plot_masked, year, genres] },                     points: 4 }
  - { reveal: { fields: [plot_masked, year, genres, country, director] },  points: 3 }
  - { reveal: { fields: [plot_masked, year, genres, country, director, cast_top3] }, points: 2 }
  - { reveal: { fields: [..., tagline] },                                   points: 1 }
```

Параметры `reveal` описываются `params_schema` конкретного ClueType. media-pipeline по ним генерирует ассеты заранее.

## 5. Пример полного конфига режима

```yaml
key: film.daily.frame
version: 3
status: published            # draft | published | archived
domain: film
title_i18n: { ru: "Кадр дня" }
source:   { type: daily, params: { size: 5, timezone: Europe/Moscow, pool: { min_popularity: 0.6 } } }
clue:     { type: frame_progressive, params: { preset: default_6 } }
answer:   { format: classic, matcher: title_alias, partial: [franchise] }
scoring:  { type: by_level, params: { partial_ratio: 0.5 } }
session:  { kind: solo, attempts_per_day: 1, archive_days: 29 }
rewards:  { exp: { per_point: 2 }, currency: { per_point: 1, cap_per_day: 150 } }
features: { prediction: true, feed: true, streak: true }
```

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

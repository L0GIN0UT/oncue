# Передача: этап 4b «Рейтинг и подбор»

> Обновлено: 2026-10-23, конец шага 0a. Новая сессия начинается с чтения этого файла.

## Состояние
- Ветка `stage4b/contracts` от `origin/main` (51d2078, 4a и коды по почте уже в `main`). **Не закоммичена** — ждёт «да» владельца.
- План: [stage-4.md](../plans/stage-4.md) (решения 4b — в конце файла и приоритетнее текста), ADR [0037](../adr/0037-rating-elo-calibration-ladder-config.md)–[0040](../adr/0040-lite-format-distractor-source.md).

## Шаг 0a — что сделано
| Что | Где |
|---|---|
| События `rating.rating.v1`: `RatingChanged`, `LadderPublished`, `SeasonStarted`, `SeasonFinished`, `WeeklyTopFinalized` + примеры | `contracts/events/rating/` |
| Схема конфига ladder-а + пример `film` с дефолтами владельца | `contracts/rating/ladder.v1.json`, `contracts/rating/examples/` |
| `queue_format` (необязательное) в `MatchStarted`/`MatchFinished` | `contracts/events/match/` |
| `answer.params.distractors` (`type`, `count`, `params`), параметры `distractor.similar` | `contracts/modes/` |
| `DistractorSource` (протокол), `DistractorsSpec`, `AnswerParamsSpec` | `libs/game_kernel` |
| rating `/v1` (ladders, leaderboard, weekly, me/ratings, users/{id}/ratings, history, matches/{id}/changes) и `/admin/v1` (ladder-ы, черновик/публикация/откат, сезоны, пересборка) — заглушки 501 | `services/rating/src/rating_service/api/` |
| match `/v1/queue/enter\|tick\|leave\|pool`, `/v1/rooms/spectate` (право `match.spectate`) — заглушки 501 | `services/match/.../api/public/` |
| bff-web: блоки 4b в `/pages/online` (необязательные), `/pages/leaderboards` — заглушка 501 | `services/bff_web/.../api/public/` |
| Снимки OpenAPI, типы фронтенда (`pnpm gen:api` через `corepack pnpm`) | `contracts/openapi/`, `frontend/src/shared/api/schema/` |

## Проверено
`gv lint` (ruff, mypy, import-linter, `gv gen --check`, `gv contracts check`), тесты `libs/game_kernel`, `rating`, `match` (component), `bff_web`, `tools/gv`, `libs/testkit` — зелёные.

## Решения, принятые мной (не из списка владельца)
1. **K Эло.** В сообщении владельца значение K калибровки обрезано. Дефолты примера: `k_calibrating` = 40, `k` = 20, `scale` = 400. Только значения примера/сидов, в коде их нет — поправить в сидах 0b.
2. **Звания.** Все, кроме высшего, по 3 ступени с шагом 100: Железо (<400…<600), Медь (<700…<900), Бронза (<1000…<1200), Серебро (до 1500), Золото (до 1800), Платина (до 2100), Алмаз ≥ 2100 без градации. Железо и Медь добавлены по таблице референса; Стали нет. Названия — ключи i18n.
3. **Рейтинг не ограничен снизу** (пола в конфиге нет).
4. **`POST /queue/tick`** добавлен (в плане его не было): heartbeat и состояние очереди (`searching|matched`, `room_code`).
5. **Soft reset в проекции match.** `SeasonStarted` несёт `soft_reset`; match применяет формулу к своей проекции сам (отдельных `RatingChanged` на сброс нет).
6. **Лимита повторных пар (`same_pair_cooldown`) нет** — это временное ограничение, владелец их исключил. Win-trading — детектор этапа 5.
7. **`rating` не получает `realtime = true`** в `infra/services.toml`: лидерборды отдаются по REST, Centrifugo rating не использует.
8. **Блоки 4b в `/pages/online` необязательны** (`null` до потока C2), чтобы не ломать 4a. Ответ теперь содержит `queues/ratings/seasons/weekly: null`.
9. Права `match.spectate`, `rating.ladders.publish`, `rating.seasons.manage` уже в identity `0008` (4a) — миграция не нужна.
10. Шаблоны `*.duel.lite` и ranked-шаблоны для `music`/`film`, сиды `seeds/rating/ladders` — шаг 0b (`stage4b/seed`), не 0a.

## Дальше (порядок владельца)
1. Коммит 0a после «да» владельца (автор L0GIN0UT, без `Co-Authored-By`, Conventional Commits, scope: `feat(contracts): ...`).
2. **G (rating)** сразу: домен `Ladder`/`EloCalculator`, миграция `0002_rating_core`, consumer `MatchFinished` (только `ranked`, не `aborted`), лидерборды ZSET, задачи сезонов/недели. Один `tdd-guide`, один ревьюер; `security-reviewer` — для admin-ручек.
3. **A2/B2 (lite)** параллельно с G: `DistractorSource` + `similar` в ядре, подбор в game (`round-plans` отдаёт `options`).
4. **Q (match)** после контрактов G: очереди, матчер под lease, `Sanction.RANKED`, проекция `RatingChanged`/`LadderPublished`, наблюдение (security-reviewer для Q2/Q3).
5. **C2/D2/E2** в конце; 0b — сиды и `e2e/ranked.spec.ts`.

## Открытое / риски
- Срыв отсчёта без штрафа — сталкинг очереди возможен (принято владельцем).
- Модель `SimilarDistractorParams` и регистрация `similar` — поток A2 (схема уже в `contracts/modes/params/`).
- Game не валидирует `answer.params.distractors` перекрёстно (формат `pick` обязателен, `count` + 1 ≤ пула) — правило добавить в A2.

# Передача: этап 4b «Рейтинг и подбор»

> Обновлено: 2026-10-23, 4b влит в `main` (PR #22–#24). Файл — справка по решениям 4b; текущая работа — в `stage-5.md`.

## Состояние
- **4b влит в `main`:** PR #22 (`stage4b/contracts`: контракты, rating, game lite, match, bff, admin, frontend, сиды), PR #23 и #24 (исправления `e2e/ranked.spec.ts`). Ветки и stash больше не нужны. Этап 5 ведётся в `docs/handoff/stage-5.md`.
- Ревью по каждому потоку проведено (code-reviewer; security-reviewer для rating и match), правки внесены. Не зелёное известное: `services/admin test_runtime` (Windows, `add_signal_handler`), плавающие тайминговые тесты match/game в полном прогоне.
- **Открыто, не закрывать без владельца:** ручные проверки identity, match и rating; подтверждение допущений сидов (K Эло, пороги званий, фильтры `similar`).
- `next build` падал с `SecurityFatalError` из-за уязвимости Next.js 16.4.0; закрыто обновлением до 16.4.1 (ветка `chore/next-update-and-docs`).
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
2. **Звания.** Все, кроме высшего, по 3 ступени с шагом 100: Железо (<700…<900), Бронза (<1000…<1200), Серебро (до 1500), Золото (до 1800), Платина (до 2100), Алмаз (до 2400); высшее звание от 2400 без градации — «Киноман» (`film`) / «Меломан» (`music`), ключ `rating.tier.cinephile` / `rating.tier.melomaniac`. Названия — ключи i18n; пороги — допущение, поправить в сидах 0b.
3. **Рейтинг не ограничен снизу** (пола в конфиге нет).
4. **`POST /queue/tick`** добавлен (в плане его не было): heartbeat и состояние очереди (`searching|matched`, `room_code`).
5. **Soft reset в проекции match.** `SeasonStarted` несёт `soft_reset`; match применяет формулу к своей проекции сам (отдельных `RatingChanged` на сброс нет).
6. **Лимита повторных пар (`same_pair_cooldown`) нет** — это временное ограничение, владелец их исключил. Win-trading — детектор этапа 5.
7. **`rating` не получает `realtime = true`** в `infra/services.toml`: лидерборды отдаются по REST, Centrifugo rating не использует.
8. **Блоки 4b в `/pages/online` необязательны** (`null` до потока C2), чтобы не ломать 4a. Ответ теперь содержит `queues/ratings/seasons/weekly: null`.
9. Права `match.spectate`, `rating.ladders.publish`, `rating.seasons.manage` уже в identity `0008` (4a) — миграция не нужна.
10. Шаблоны `*.duel.lite` и ranked-шаблоны для `music`/`film`, сиды `seeds/rating/ladders` — шаг 0b (`stage4b/seed`), не 0a.

## Дальше
Все потоки 4b (G, A2/B2, Q, C2, D2, E2, шаг 0b) выполнены и влиты. Остаётся только то, что в «Открытое / риски» и в хвостах [roadmap](../09-roadmap.md); следующий этап — 5 («Прогрессия и социальное»).

## Открытое / риски
- Срыв отсчёта без штрафа — сталкинг очереди возможен (принято владельцем).
- Модель `SimilarDistractorParams` и регистрация `similar` — поток A2 (схема уже в `contracts/modes/params/`).
- A2/B2 сделаны (не закоммичены): `similar`, перекрёстная валидация, подбор в `round-plans`; `title_alias` и `random` теперь принимают `pick`. Не сделано: шаблоны `*.duel.lite` и сиды (0b); Валидатор: `config.options_source_missing` (pick без варианта от источника и без дистракторов), `config.distractors_unsupported_source` (дистракторы при `RoundSource`). Допущение: пул дистракторов берёт фильтры пула и пресета (`ctx.preset_id`), но не `exclude_ids` — память сыгранного избегает повторов загаданных, а не вариантов.

## Поток Q (match) — сделано, не закоммичено
Очередь `(ladder, format)` в Redis, матчер под lease (`infrastructure/matchmaker.py`), проекции `ladders_proj`/`ratings_proj` (миграция `0003`), срыв отсчёта без штрафа, `queue_format` в событиях, наблюдение по `match.spectate`, реальные `/v1/queue/*` и `/v1/rooms/spectate` вместо 501. Подробности — `services/match/CLAUDE.md`.
Решения Q: (1) `LadderPublished` не несёт `elo.start` — новичок получает `soft_reset.target` последнего сезона или `GV_MATCH__DEFAULT_START_RATING` (1000); если G добавит необязательное поле в событие, подключить его в `SqlLadders`. (2) Реванш рейтинговой комнаты запрещён. (3) Состояние `reserved` скрыто от клиента. (4) `queue/leave` при `matched` в отсчёте = выход из комнаты без штрафа. (5) Игрок в очереди может создать/войти в кастомную комнату: конфликт разрешает матчер (выбывает из очереди, соперник возвращается). (6) Ограничения числа наблюдателей канала нет (только лимиты запросов).
Ревью Q: исправлены откат подбора (код комнаты, discard, сбои шины/хранилища, коллизия, занятый игрок), зависший резерв (sweep), `leave` в `reserved`, порядок SeasonStarted/LadderPublished, обход очереди старейшими первыми, удалённый игрок, отзыв подписок наблюдателей. Presence/join-leave в Centrifugo namespace `room` выключены (`infra/centrifugo/config.json`), поэтому `user_id` наблюдателя другим подписчикам не раскрывается; если их включат — наблюдателей придётся скрыть. Остаточный риск: если шина недоступна и владелец успел подхватить созданную комнату до `discard`, он держит пустое лобби в памяти до простоя (`room_idle_close_min`).

## Шаг 0b / поток B2 (остаток) — сделано, не закоммичено
- game: проекция `ladders_proj` (миграция `0006`, только расширяющая) из `rating.LadderPublished`; проверка `session.ladder` в `ValidateModeConfig` (неизвестный — ошибка `config.ladder_unknown`, чужой домен — `config.ladder_domain_mismatch`); при bootstrap сидов — предупреждение, не ошибка (порядок запуска сервисов не гарантирован).
- Сиды game: `music|film.duel.lite`, `music|film.ranked.classic`, `music|film.ranked.lite`. Lite — один уровень, `parallel` + `party_speed` (100), 4 варианта, `similar` с `same_genre: false`, `year_window: 15`, `min_pool: 8` (мягкие фильтры, чтобы синтетического каталога e2e хватало; жёстче — решение владельца). `clue.params` lite — существующие одноуровневые профили (как у `pair`-режимов), новых профилей media-pipeline не нужно. Classic ranked — копия `music.duel.classic` / `film.duel.frame` с `ranked: true`, `ladder`.
- Сиды rating: `seeds/rating/ladders/{film,music}.yaml` (значения примера контракта: K 40/20, scale 400, звания, окна, сезон 56 дн., сброс 0,5, Europe/Moscow), загрузка — периодическая задача `rating.ladders.bootstrap` (по образцу `game.modes.bootstrap`; `GV_RATING__BOOTSTRAP_LADDERS_DIR` в dev-оверлее через шаблон `gv gen`).
- `e2e/ranked.spec.ts` — на уровне API (экранов очереди/звания/дельты нет): очередь → комната → сдача → история, скрытые числа калибровки, штраф в `matches/{id}/changes`. **TODO(D2):** перенести шаги очереди и дельты итога на UI, когда появятся экраны (поток D2).
- Не проверено на живом compose (нужен `gv dev up` + `gv dev seed`): `ranked.spec.ts` и `gv dev seed` с новыми шаблонами (ожидание пула 20 единиц на одноуровневых профилях).

# progression

Опыт и уровни, валюта (журнал проводок), задания, бейджи, магазин, инвентарь, оформление профиля.

- Пакет: `progression_service`; запуск: `progression api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/progression/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda
- Статус: этап 5a, шаг 0a — контракты и заглушки 501 (`docs/plans/stage-5.md`); домен — потоки P1–P3. Решения: ADR 0042–0044.

## Особенности
- Контракты: события `contracts/events/progression/*` (топик `progression.progression.v1`, ключ — `user_id`), конфиг `contracts/progression/progression_config.v1.json` (опыт, уровни, Версы, задания, бейджи, колесо, лимиты выдачи; значений в коде нет). Часовой пояс суток — поле `timezone` конфига.
- Ledger (зона повышенной безопасности): `xp_ledger`/`currency_ledger` append-only, `UNIQUE(op_id)`, баланс — в той же транзакции, не ниже нуля, сторно — встречная проводка `reversal_of`. После реализации — «требуется ручная проверка владельцем».
- Идемпотентность начислений — по предмету события (`op_id`), не по `event_id`; дейлик награждается только по `DailyCompleted`.
- Права admin-API: `admin.access` + `progression.config.manage|publish`, `progression.ledger.grant|reverse|read` (identity `0013`).
- `SeasonStandingsFinalized` приходит пачками без порядка: идемпотентность по `(season_id, chunk)`, награды по месту из пачки, не ждать другие пачки и `SeasonFinished`.
- `RewardHook.online_xp_multiplier` вызывается под `FOR UPDATE` игроков матча — I/O внутри запрещён; версия конфига выбирается по `finished_at` события (ADR 0042), не по времени обработки.
- consumers - `infrastructure/messaging/__init__.py`, как в rating/match; новые источники P2 - отдельные модули рядом, регистрируются в `build_event_handlers`.
- P2 (задания, бейджи, колесо, бусты; ADR 0044): метрики копятся в `metric_counters`, предмет события отмечается в `metric_marks` (повтор с новым `event_id` метрики не двоит); задания назначаются снимком на период (`quest_assignments` + `quest_progress`), награда — через ledger P1.
- Метрики P2 обновляются в одной транзакции с начислением P1 (`messaging/rewards.py`): ошибка P2 откатывает и P1, событие повторяется; payload разбирает P1, P2 не строже.
- Буст опыта: `BoostRewardHook` тратит матч буста в транзакции матча (только при базовом опыте > 0, `boost_spends` PK `(user_id, match_id)`); Версы и соло бустом не множатся.
- Колесо: `wheel_spins` PK `(user_id, day)`, сектор выбирает порт `Random` (`container.random`), приз выдаётся в той же транзакции; `GET /me/quests` пишет назначение.
- Нет синхронных вызовов game/match/rating; места сезона — из `rating.SeasonStandingsFinalized`, топ недели — топ-100 (решение владельца).

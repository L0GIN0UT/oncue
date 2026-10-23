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
- Нет синхронных вызовов game/match/rating; места сезона — из `rating.SeasonStandingsFinalized`, топ недели — топ-100 (решение владельца).

# rating

Эло дуэли, калибровка, звания, сезоны, топ недели и сезона, лидерборды.

- Пакет: `rating_service`; запуск: `rating api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/rating/v1/...` на gateway; admin — `/admin/v1/ladders|seasons`
- Зависимости: PostgreSQL, Redis, Redpanda
- Статус: этап 4b, поток G (`docs/plans/stage-4.md` §11, ADR 0037, 0039): расчёт, сезоны, лидерборды, admin. Сиды ladder-ов — `seeds/rating/ladders/*.yaml`, задача `rating.ladders.bootstrap` (`GV_RATING__BOOTSTRAP_LADDERS_DIR`, только local/ci).

## Особенности
- Всё, что влияет на игру (Эло, K, звания, окна подбора, сезон, неделя, лидерборд), — только в конфиге ladder-а (`contracts/rating/ladder.v1.json`, `domain/ladder.py`); в коде значений нет. Правка баланса = черновик → публикация → `LadderPublished`; откат — копия старой версии новой.
- Эло (`domain/elo.py`): ожидание считается один раз на пару, округление от нуля — при равных K и без штрафа сумма дельт ровно 0. `K` свой у каждого игрока (калибровка). Выходом считаются `finish` ∈ `left|forfeit|tech_loss` (`LEAVER_FINISHES`); размер штрафа — `elo.leaver_penalty`.
- Рейтинговый матч = `ranked` дуэль без `aborted`; остальное — тихий пропуск. Нарушение контракта события → `PermanentError` (DLQ), нет сезона → повторимо. Дедупликация: inbox + `processed_matches(match_id)`.
- Гонка двух матчей одного игрока: строки новичков создаются `INSERT … ON CONFLICT DO NOTHING` в порядке `user_id` до `FOR UPDATE`; матч берёт сезон под `FOR SHARE`, закрытие — под `FOR UPDATE`.
- `ratings.matches_total` (калибровка) не обнуляется сбросом сезона, `season_matches` — обнуляется; `event_version` переезжает в новый сезон (версия `RatingChanged` монотонна по `(user, ladder)`). Новый сезон копирует строки прошлого с мягким сбросом (`SeasonLifecycle.start`), отдельных `RatingChanged` на сброс нет.
- Лидерборд: в него входят игроки без калибровки и с `season_matches ≥ leaderboard.min_matches`. Redis ZSET `{lb:ladder:season}` — только чтение; сбой Redis не останавливает расчёт, метка `…:ready` с TTL (`GV_RATING__LEADERBOARD_RESYNC_S`) заставляет задачу пересобрать доску из Postgres. Курсор — непрозрачное смещение.
- Калибровка скрывает числа и звание в API, событиях `RatingChanged.tier` и истории; рейтинг при этом реальный (нужен подбору).
- Все события — в топике `rating.rating.v1` (`aggregate_type = "rating"`), схемы `contracts/events/rating`. Топ недели закрывает `rating.weekly.finalize` (недели до текущей в зоне ladder-а, идемпотентно по `weekly_tops`), сезоны — `rating.seasons.tick`, пересборку и самолечение — `rating.leaderboards.rebuild` (запросы админа лежат в `rebuild_requests`, RabbitMQ rating не нужен).
- История — `rating_history` с партицией на сезон (`PARTITION BY LIST (season_id)`), партицию создаёт `SqlSeasons.add`; партиции не входят в метаданные (см. `tests/integration/test_migrations.py`).
- Админ-мутации: `Idempotency-Key` + `reason` + строка в `admin_actions`; права `admin.access` + `rating.ladders.publish` / `rating.seasons.manage`.
- Границы значений конфига (K, scale, int32-поля, длина сезона, число званий…) заданы и в `ladder.v1.json`, и в `domain/ladder.py` (`NaN/Infinity` отвергаются); форматы очереди в коде не перечислены — набор берётся из `formats` ladder-а. Битый сохранённый конфиг и нарушение контракта события → `PermanentError` (DLQ), не вечный повтор.
- Лидерборд-запись версионирована Lua-скриптом (`event_version`), удалённый аккаунт — «надгробие»; пересборка после замены читает БД ещё раз и дописывает изменившееся. Публичное чтение потерянной доски не сканирует БД: `503` + один запрос в `rebuild_requests` (`SET NX`), восстанавливает задача. Курсор ограничен `GV_RATING__LEADERBOARD_MAX_OFFSET`. Запрос на пересборку закрывается с `error`, если ladder/сезона нет, и не блокирует остальные.
- Топ недели: матч берёт неделю по `finished_at`; если она уже закрыта — прирост идёт в текущую неделю (лог `rating.weekly_late_match`, метрика `gv_rating_weekly_late_matches_total`). `season_id` события и `weekly_tops` — сезон последнего прироста недели; у недели без приростов он `null`. `finished_at` в будущем (> допуска `finished_at_skew_s`) отвергается.
- Удалённые аккаунты: не попадают на доску (`Leaderboards.sync`), в ответах `matches/{id}/changes` и лидерборда их `user_id` маскируется нулевым UUID.
- Принятые допущения (ревью G): мягкий сброс применяется один раз за переход сезонов (новый сезон копирует строки прошлого целиком, пропущенных сезонов у игрока нет); у `rating_history` и `weekly_deltas` нет FK на сезон (партиции/ретенция), индексы — `ix_ratings_season_rating`, `ix_rating_history_user|match`, `ix_weekly_deltas_top`; пустая доска с истёкшей меткой готовности отдаёт 503 до ближайшего тика `rating.leaderboards.rebuild`.
- Тесты: фикстура `rating` (`tests/conftest.py`) — реальные Postgres и Redis, события через `EventProcessor`, время — `FakeClock`.
- Итоги сезона: при закрытии (расписание или админ) вместе с `SeasonFinished` публикуются пачки `SeasonStandingsFinalized` (`GV_RATING__STANDINGS_TOP_SIZE` = 1000, `STANDINGS_CHUNK_SIZE` = 500; игроки лидерборда, рейтинг по убыванию, затем `user_id`). Потребитель — progression (награды сезона); топ недели остаётся топ-100.
- Тай-брейк доски на живом Redis (`tests/integration/test_leaderboard_redis.py`): при равном рейтинге `ZREVRANGE` отдаёт `user_id` по убыванию, как `rank_standings`.

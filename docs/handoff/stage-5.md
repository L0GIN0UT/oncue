# Передача: этап 5 «Прогрессия и социальное»

> Обновлено: 2026-10-23. Новая сессия начинается с чтения этого файла. Реализуется только 5a (progression); 5b и 5c — в плане как модель.

## Состояние
- Шаг 0 (Next 16.4.1, актуализация roadmap и handoff 4b) — PR #25, ждёт слияния владельцем.
- План и ADR 0042–0044 (черновики) закоммичены в `stage5/plan` (`92a279b`), не запушены.
- Шаг 0a выполнен в ветке `stage5a/contracts` (от `stage5/plan`), **не закоммичен**: `rating.SeasonStandingsFinalized` (схема, пример, публикация при закрытии сезона, настройки `GV_RATING__STANDINGS_*`), события `progression.*` (7 схем и примеров), `contracts/progression/progression_config.v1.json`, OpenAPI progression `/v1` и `/admin/v1` (501), `/v1/pages/profile` и необязательный `progress` в `/v1/pages/home` (bff), права identity (миграция `0013`), `services/progression/CLAUDE.md`, `docs/architecture/services.md`. `gv lint`, `gv contracts check`, `gv gen --check`, `mkdocs --strict` зелёные; тесты rating 252, progression 31, identity 399, bff_web 207.
- Дальше: P1 ∥ P4, затем P2 ∥ P3, 0b.

## Решения, принятые ассистентом (не из списка владельца)
Полный список — [план](../plans/stage-5.md) §1, R1–R11. Ждут подтверждения владельца.
1. Сутки — один `progression.timezone`, день по `finished_at` события.
2. Дейлик награждается только по `DailyCompleted`; `GameFinished` с `day != null` опыт не даёт. Серию ведёт progression.
3. Новое событие `rating.SeasonStandingsFinalized` (топ-1000 пачками ≤ 500): в `SeasonFinished` нет мест игроков, а синхронные вызовы rating запрещены. `WeeklyTopFinalized` не меняем: топ недели — топ-100, разряд 101–1000 только у сезона (решение владельца).
4. Антиабьюз: бонус за победу при сыгранных ≥ `min_rounds_ratio` (0,5), выход — 0, `tech_win` без бонуса.
5. Смена конфига без пересчёта; `config_version` в проводке; задания назначаются снимком на период.
6. Сторно — только админ с правом и причиной, ниже нуля — 409.
7. Колесо не продаётся (иначе лутбокс) — зафиксировано в ADR 0044.

## Решения 0a (не из списка владельца)
- Сетки наград недели и сезона лежат в конфиге progression (`verses.weekly_top`, `verses.season`), а не в ladder rating: rating о Версах не знает. Правка формулировки «в конфиге ladder-а» в плане/ADR 0043 — при принятии ADR.
- Часовой пояс суток — поле `timezone` конфига progression; отдельных `GV_PROGRESSION__*` в 0a нет.
- Добавлено пятое право `progression.ledger.read` (чтение журнала игрока); все права только у admin.
- Глубина таблицы сезона и размер пачки — настройки rating, не ladder-конфиг; в событии нет `players_count` пачки — он в `SeasonFinished`.
- Семантический валидатор конфига (монотонные пороги, непрерывные полосы, метрики из реестра) — в домене P1, схема проверяет форму.
- В bff `profile` — только контракт (501), `progress` главной — `null` до P4.

## Ревью 0a
- review-migration (identity `0013_seed_progression_perms.py`, путь указан явно): только `INSERT … ON CONFLICT DO NOTHING` в справочники `permissions`/`role_permissions` (малые таблицы), DDL и блокировок нет, `downgrade` удаляет права по имени (выданные вручную другим ролям — тоже) — Safe to merge.
- code-reviewer: критичного нет; исправлены тай-брейк мест (как `ZREVRANGE`), потоковое чтение таблицы, описание порядка пачек; добавлен компонентный тест `test_season_close_publishes_standings_in_chunks_cut_by_settings` (топ 5, пачка 2: три пачки 2+2+1).

## P1 и P4 (в работе, не закоммичены)
- P1 (ветка `stage5a/p1-domain-ledger`): домен, ledger, миграция `0002`, consumers, `me/progress`, `me/wallet`, `users/{id}/progress`; 209 тестов progression, покрытие 98%. Ревью code-reviewer и security-reviewer учтены. review-migration (`0002_progression_core.py`, путь указан явно): Safe to merge; замечания — нет индексов по FK `config_version` и явного `ON DELETE` (config_versions не удаляются), `CHECK domain IN ('music','film')` в `daily_streaks` требует миграции при новом домене. Решения P1 записаны в ADR 0042/0043.
- P4 (worktree `.claude/worktrees/agent-accec8825a26da59a`, ветка `stage5a/p4-web`): bff `/pages/profile`, `/pages/players/{id}`, блок `progress` главной; фронтенд `/profile`, `/players/[id]`, колесо; bff 252 теста, vitest 373, Playwright 48.
- **Решено владельцем (ledger, требуется ручная проверка):** consumers остаются в `infrastructure/messaging/__init__.py` (как rating/match); роли Postgres (REVOKE) - этап 7, риск принят; инкрементальная сверка - этап 7; нулевая проводка-отметка предмета (`accrual_marks`) - сделано; онлайн-потолок Версов `verses.online_daily_cap` = 500 - сделано (опыт онлайна без потолка); значение 500 для сида - в шаге 0b.

## Хвосты 0a
- P3: `Cache-Control: no-store` в admin-ручках progression ставится до `raise` и в заглушках не действует — проверить на успешных ответах.
- Нет теста «совпадение места в событии с реальным `ZREVRANGE` Redis» (тай-брейк проверен по коду и юнит-тестом).

## Открыто
- Ручные проверки владельцем: identity (realtime-токен), match (античит), rating; новая — ledger progression после реализации.
- Допущения сидов 4b: K Эло 40/20, пороги званий, фильтры `similar`.
- Win-trading — детектор в этапе 5 (хвост [ADR 0038](../adr/0038-matchmaking-queues.md)); в 5a не входит.
- Исключённый игрок может переподписаться на канал комнаты старым токеном (до 10 мин) — хвост 4a, при желании в 5c вместе с санкцией `ONLINE`.

## Дальше
1. Слияние PR #25, затем коммит и PR документов этапа 5.
2. Шаг 0a: контракты и заглушки 501 в основной сессии, `gv lint`, `gv contracts check`, `gv gen --check`.
3. P1 ∥ P4 → P2 ∥ P3 → 0b (сиды, `e2e/progression.spec.ts`). Один `tdd-guide` и один ревьюер на поток; `security-reviewer` для P1 и admin-ручек P3.

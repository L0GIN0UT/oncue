# Передача: этап 5 «Прогрессия и социальное»

> Обновлено: 2026-10-23. Новая сессия начинается с чтения этого файла. 5a влит в `main`; идёт 5b (ветка `stage5b/integration`); 5c — в плане как модель.

## Состояние (2026-10-23)
- 5a (progression) влит в `main`: PR #27 (`stage5a/integration`) и PR #28 (`GV_PUBLIC_ORIGINS` для progression, CSRF на спине колеса). Состав: 0a (контракты, rating-событие, права identity), P1 (домен, ledger, миграция `0002`, начисления), P2 (задания, бейджи, колесо, бусты, `0003`), P3 (admin-API, раздел админки, `0004`), P4 (bff `/pages/profile`, `/pages/players/{id}`, прогресс на главной; фронтенд `/profile`, `/players/[id]`, колесо), 0b (сид `seeds/progression/config.yaml`, задача `progression.config.bootstrap`, `e2e/progression.spec.ts`).
- Проверки на момент слияния: `gv lint` зелёный; progression 375, bff_web 252, tools/gv 157, rating 252, identity 399; frontend lint, typecheck, `next build` зелёные, vitest 373. Не проверено: `e2e/progression.spec.ts` (нужен живой стек).
- Давний хвост admin (Windows): `test_runtime::test_api_serves_django_and_metrics_then_stops` падает и на базовой ветке (`/health/live` не отвечает) — не из-за 5a.
- Хвосты 5a — в [roadmap](../09-roadmap.md#хвосты-5a): `Cache-Control: no-store` на успешных ответах, тест тай-брейка против живого Redis, нестабильные `GameScreen.modes` и `party.spec`.

## Чек-лист ручной проверки владельцем (ledger — открыт)
Ledger — зона повышенной безопасности (`docs/10-ai-assistant.md` §3), **требуется ручная проверка владельцем**:
1. Роли Postgres: журналы защищены триггерами (`xp_ledger`, `currency_ledger`, `admin_actions` — UPDATE/DELETE/TRUNCATE), но роль сервиса владеет таблицами и может отключить триггер. Разделение ролей (REVOKE) — этап 7, риск принят.
2. Миграции `0002`–`0004`: откат `0002` и `0004` прерывается, если есть проводки/аудит; `0003` откатывается без проверок (теряется история спинов). Прогнать `gv test progression --level integration` на чистой БД.
3. Начисления: дубль события (в т.ч. с новым `event_id`) даёт одну проводку; потолки соло и онлайн-Версов (`verses.online_daily_cap` = 200 в сиде) не обходятся параллельными матчами около полуночи; онлайн-опыт без потолка (решение владельца) — фарм вторым аккаунтом закроет детектор win-trading (хвост ADR 0038).
4. Конфиг: верхние границы сумм (`maximum` до 100 млн); публикация привязана к хешу тела черновика; админ с `config.manage` без `publish` публиковать не может.
5. Ручная выдача и сторно: только `earned`, сторно только кредитов, суточный лимит считает и сторнированные выдачи, причина не попадает в события, `no-store`.
6. Награды сезона/недели: ключи `season:{season_id}:{user}`, `weekly_top:{ladder}:{week_start}:{user}`; на стенде проверить, что `rating` публикует `SeasonStandingsFinalized` при закрытии сезона, а progression начисляет по пачкам.
7. Живая проверка на compose (`gv dev up`, `gv dev seed`): `e2e/progression.spec.ts`; `/profile`, колесо (один спин в сутки, повтор — тот же результат), админка «Прогрессия» (черновик → публикация → откат, выдача и сторно).
8. Хвосты 4b: ручные проверки identity (realtime-токен), match (античит), rating; допущения сидов 4b (K Эло, пороги званий, фильтры `similar`).
9. Пароль сид-админа локального стенда был выведен в вывод инструмента при поиске по `infra/` (файл `.env`, не в коммитах): при необходимости сменить.

## Решения 5a, принятые ассистентом (не из списка владельца)
Полный список — [план](../plans/stage-5.md) §1, R1–R11. Ждут подтверждения владельца.
1. Сутки — один `progression.timezone`, день по `finished_at` события.
2. Дейлик награждается только по `DailyCompleted`; `GameFinished` с `day != null` опыт не даёт. Серию ведёт progression.
3. Новое событие `rating.SeasonStandingsFinalized` (топ-1000 пачками ≤ 500). `WeeklyTopFinalized` не меняем: топ недели — топ-100, разряд 101–1000 только у сезона (решение владельца).
4. Антиабьюз: бонус за победу при сыгранных ≥ `min_rounds_ratio` (0,5), выход — 0, `tech_win` без бонуса.
5. Смена конфига без пересчёта; `config_version` в проводке; задания назначаются снимком на период.
6. Сторно — только админ с правом и причиной, ниже нуля — 409.
7. Колесо не продаётся (иначе лутбокс) — ADR 0044.
8. Сетки наград недели и сезона лежат в конфиге progression (`verses.weekly_top`, `verses.season`), а не в ladder rating. Пятое право `progression.ledger.read`.

## Решения владельца по ledger (5a)
Consumers остаются в `infrastructure/messaging/__init__.py`; роли Postgres (REVOKE) и инкрементальная сверка — этап 7; нулевая проводка-отметка предмета (`accrual_marks`) — сделано; онлайн-потолок Версов `verses.online_daily_cap` = 200 (в сиде 0b); опыт онлайна без потолка.

## Сид и e2e 5a
- `seeds/progression/config.yaml` (12 секторов колеса; пулы: дневных 5, недельных 12, разовых 2). Задача relay `progression.config.bootstrap` — только local/ci, `GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR`, интервал `GV_PROGRESSION__BOOTSTRAP_INTERVAL_S`; актор `UUID(int=0)`. Dev-оверлей compose монтирует `seeds/progression`.
- `e2e/progression.spec.ts` (нужны `gv dev up`, `gv dev seed`, `E2E_SEEDED=1`).

## 5b — экономика и уведомления (в работе)
Ветка `stage5b/integration` от `main` (после PR #28); один PR на подэтап, потоки — ветки от неё. Решения владельца — в [плане](../plans/stage-5.md) §9; ADR 0045–0049 (черновики).

### Решения 5b (ассистента, подтверждены или заменены владельцем 2026-10-23)
1. (Заменено решением владельца) Очередь модерации аватарок — в media-pipeline: статус `pending|approved|rejected` у UGC-ассета, раздел «UGC-изображения» админки, событие `media_pipeline.AssetModerated` (progression и notifications слушают), право `media.ugc.moderate`. Событий `Avatar*` у progression нет.
2. Дубль предмета с колеса -> Версы по редкости из конфига (подтверждено владельцем).
3. Формат анимации — animated WebP, картинки предметов отдаются по публичному префиксу (ADR 0046, решение владельца). Тема: наборы токенов во фронтенде по ключу, bff отдаёт ключ (ADR 0049).
4. Покупка списывает бакет `earned` (подтверждено); порядок `earned -> paid` для этапа 6 — в ADR 0043, в конфиге.
5. `notifications` добавляется в `COOKIE_MUTATION_SERVICES` (`tools/gv/src/gv_cli/generate.py`), иначе CSRF отклонит POST «прочитано» и настройки.

### Прогресс
| Шаг | Статус |
|---|---|
| Шаг 0: roadmap, handoff | сделано, не закоммичено |
| Детализация плана 5b ([§9](../plans/stage-5.md), §12 «Модель 5d») и ADR 0045–0049 (черновики) | сделано, `mkdocs build --strict` зелёный, ждёт ревью владельца до кода |
| 0a контракты (события, OpenAPI, права identity) | — |
| S1 ∥ S2 → S3 ∥ S4 → S5 → 0b | — |

## Открыто
- Ручные проверки владельцем: identity (realtime-токен), match (античит), rating; ledger progression (чек-лист выше). Для 5b — траты Версов (ledger, **требуется ручная проверка владельцем**).
- Допущения сидов 4b: K Эло 40/20, пороги званий, фильтры `similar`.
- Win-trading — детектор (хвост ADR 0038), 5c.
- Исключённый игрок может переподписаться на канал комнаты старым токеном (до 10 мин) — хвост 4a, при желании в 5c вместе с санкцией `ONLINE`.

## Дальше
1. Владелец смотрит план (§9, §12) и ADR 0045–0049; после «да» — коммит документов (`docs(stage-5): ...`), затем 0a.
2. Шаг 0a: контракты и заглушки 501 в основной сессии, `gv lint`, `gv contracts check`, `gv gen --check`, `pnpm gen:api`.
3. S1 ∥ S2 → S3 ∥ S4 → S5 → 0b. Один `tdd-guide` и один ревьюер на поток; `security-reviewer` и `review-migration` для S1 и S3.

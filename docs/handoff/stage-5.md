# Передача: этап 5 «Прогрессия и социальное»

> Обновлено: 2026-10-23. Новая сессия начинается с чтения этого файла. Реализуется только 5a (progression); 5b и 5c — в плане как модель.

## Состояние (конец 5a, 2026-10-23)
- Шаг 0 (Next 16.4.1, актуализация roadmap и handoff 4b) — PR #25, ждёт слияния владельцем; после него `git merge origin/main` в `stage5a/integration` (вероятные конфликты: `frontend/package.json`, `pnpm-lock.yaml`).
- Весь код 5a собран в `stage5a/integration` (запушена): 0a (контракты, rating-событие, права identity), P1 (домен, ledger, миграция `0002`, начисления), P2 (задания, бейджи, колесо, бусты, миграция `0003`), P3 (admin-API, раздел админки, миграция `0004`), P4 (bff `/pages/profile`, `/pages/players/{id}`, прогресс на главной; фронтенд `/profile`, `/players/[id]`, колесо), 0b (сид `seeds/progression/config.yaml`, задача `progression.config.bootstrap`, `e2e/progression.spec.ts`). Единственный PR — `stage5a/integration` → `main`.
- Проверки на integration: `gv lint` зелёный; progression 375, bff_web 252, tools/gv 157, rating 252, identity 399; frontend lint, typecheck, `next build` зелёные, vitest 373 (один тест `GameScreen.modes` нестабилен под нагрузкой, отдельно проходит); admin по разделу прогрессии 31. Не проверено: `e2e/progression.spec.ts` (нужен живой стек), Playwright на моках в integration после слияния.
- Давний хвост admin (Windows): `test_runtime::test_api_serves_django_and_metrics_then_stops` падает и на базовой ветке (`/health/live` не отвечает) — не из-за 5a.

## Чек-лист ручной проверки владельцем (перед слиянием PR integration → main)
Ledger — зона повышенной безопасности (`docs/10-ai-assistant.md` §3), требуется ручная проверка владельцем:
1. Роли Postgres: журналы защищены триггерами (`xp_ledger`, `currency_ledger`, `admin_actions` — UPDATE/DELETE/TRUNCATE), но роль сервиса владеет таблицами и может отключить триггер. Разделение ролей (REVOKE) — этап 7, риск принят.
2. Миграции `0002`–`0004`: откат `0002` и `0004` прерывается, если есть проводки/аудит; `0003` откатывается без проверок (теряется история спинов — повторный спин в те же сутки). Прогнать `gv test progression --level integration` на чистой БД.
3. Начисления: дубль события (в т.ч. с новым `event_id`) даёт одну проводку; потолки соло и онлайн-Версов (`verses.online_daily_cap` = 200 в сиде) не обходятся параллельными матчами около полуночи; онлайн-опыт без потолка (решение владельца) — фарм вторым аккаунтом закрывает будущий детектор win-trading (хвост ADR 0038).
4. Конфиг: верхние границы сумм в схеме и домене (`maximum` до 100 млн); публикация привязана к хешу тела черновика; админ с `config.manage` без `publish` публиковать не может.
5. Ручная выдача и сторно: только `earned`, сторно только кредитов, суточный лимит считает и сторнированные выдачи, причина не попадает в события, `no-store`.
6. Награды сезона/недели: ключи `season:{season_id}:{user}`, `weekly_top:{ladder}:{week_start}:{user}`; проверить на стенде, что `rating` публикует `SeasonStandingsFinalized` при закрытии сезона, а progression начисляет по пачкам.
7. Живая проверка на compose (`gv dev up`, `gv dev seed`): `e2e/progression.spec.ts`; профиль `/profile`, колесо (один спин в сутки, повтор — тот же результат), админка «Прогрессия» (черновик → публикация → откат, выдача и сторно).
8. Хвосты 4b (не 5a): ручные проверки identity (realtime-токен), match (античит), rating; допущения сидов 4b (K Эло, пороги званий, фильтры `similar`).
9. Пароль сид-админа локального стенда был выведен в вывод инструмента при поиске по `infra/` (файл `.env`, не в коммитах): при необходимости сменить.

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
- **Решено владельцем (ledger, требуется ручная проверка):** consumers остаются в `infrastructure/messaging/__init__.py` (как rating/match); роли Postgres (REVOKE) - этап 7, риск принят; инкрементальная сверка - этап 7; нулевая проводка-отметка предмета (`accrual_marks`) - сделано; онлайн-потолок Версов `verses.online_daily_cap` - сделано (опыт онлайна без потолка); значение в сиде 0b = 200 (в примере контракта 500, для тестов).

## Хвосты 0a
- P3: `Cache-Control: no-store` в admin-ручках progression ставится до `raise` и в заглушках не действует — проверить на успешных ответах.
- Нет теста «совпадение места в событии с реальным `ZREVRANGE` Redis» (тай-брейк проверен по коду и юнит-тестом).

## Хвосты для 5b
- notifications тоже нужен в `COOKIE_MUTATION_SERVICES` (`tools/gv/src/gv_cli/generate.py`), когда появятся POST из браузера (прочитано, настройки): иначе `GV_PUBLIC_ORIGINS` не попадёт в compose и CSRF отклонит мутации (`auth.csrf_failed`), как было у спина колеса progression.

## 0b (сиды и e2e, не закоммичено)
- `seeds/progression/config.yaml` (значения владельца, 12 секторов колеса, пулы: дневных 5, недельных 12, разовых 2); `verses.online_daily_cap` = **200** (решение владельца при 0b; выше в этом файле упомянуто 500 — устарело). Тест сида: `services/progression/tests/unit/test_seeds.py`.
- Задача relay `progression.config.bootstrap` (`infrastructure/jobs.py`, `ConfigCommands.bootstrap`): только local/ci, `GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR`, если опубликованной версии нет — версия 1 через save_draft+publish, актор `UUID(int=0)`, причина «bootstrap сидов»; битый сид — ERROR в логе, без падения. Интервал `GV_PROGRESSION__BOOTSTRAP_INTERVAL_S` (60 с). Тесты: `tests/component/test_config_bootstrap.py`; «событие до конфига обрабатывается после» уже покрыто в `test_accrual_solo_daily.py` (повтор после `publish_config`).
- Dev-оверлей compose монтирует `seeds/progression` (шаблон `compose.services.dev.yml.j2`, `gv gen`).
- `e2e/progression.spec.ts` (не запускался, `tsc` чист): соло → опрос начисления → профиль → спин колеса. Нужны `gv dev up`, `gv dev seed`, `E2E_SEEDED=1`.

## Открыто
- Ручные проверки владельцем: identity (realtime-токен), match (античит), rating; новая — ledger progression после реализации.
- Допущения сидов 4b: K Эло 40/20, пороги званий, фильтры `similar`.
- Win-trading — детектор в этапе 5 (хвост [ADR 0038](../adr/0038-matchmaking-queues.md)); в 5a не входит.
- Исключённый игрок может переподписаться на канал комнаты старым токеном (до 10 мин) — хвост 4a, при желании в 5c вместе с санкцией `ONLINE`.

## Дальше
1. Слияние PR #25, затем коммит и PR документов этапа 5.
2. Шаг 0a: контракты и заглушки 501 в основной сессии, `gv lint`, `gv contracts check`, `gv gen --check`.
3. P1 ∥ P4 → P2 ∥ P3 → 0b (сиды, `e2e/progression.spec.ts`). Один `tdd-guide` и один ревьюер на поток; `security-reviewer` для P1 и admin-ручек P3.

# Передача: этап 5 «Прогрессия и социальное»

> Обновлено: 2026-10-25. Новая сессия начинается с чтения этого файла. 5a–5c влиты в `main` (PR #27–#31); 5d готов к PR (ветка `stage5d/integration`, раздел [5d](#5d--сезонные-события)).

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

## 5b — экономика и уведомления (готово к PR)
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
| Шаг 0: roadmap, handoff | сделано, закоммичено (`867f44d`) |
| Детализация плана 5b ([§9](../plans/stage-5.md), §12 «Модель 5d») и ADR 0045–0049 (черновики) | сделано и утверждено владельцем (`867f44d`) |
| 0a контракты (события, конфиг, OpenAPI, права identity), 0a.1 инфраструктура, 0a.2 ручка адресов, 0a.3 `public_key` | сделано, влито |
| S1 (магазин, промокоды), S2 (notifications), S3 (аватарка), S4 (админка), S5-bff, S5-front, 0b (сиды) | сделано, ревью пройдены, влито |
| Волна 2: `e2e/shop.spec.ts`, `e2e/notifications.spec.ts` | написано, `tsc -p e2e` проходит, **на живом стеке не запускалось** |
| PR `stage5b/integration` → `main` | открывает владелец (метка `e2e`) |

### Решения владельца после 0a (2026-10-23)
- Типы уведомлений — файловый конфиг в пакете notifications (как профили media-pipeline), не версии в БД; admin-API позже только для включения и выключения типа (хвост в roadmap).
- Картинки и анимации предметов грузит и хранит progression (S3-адаптер, Pillow, бакет `GV_COSMETICS_BUCKET`/`gv-cosmetics`, публичный префикс `/cosmetics/c/<hex>.webp|png` на gateway). Модерация публичным предметам не нужна.
- Копия `SmtpEmailSender` в notifications принята, вынос в `libs/` — хвост.
- Шаг 0a.1 (инфраструктура, orchestrator): `services.toml` — progression `redis`+`s3`, notifications `redis`+`realtime` (`REALTIME_PUBLISHERS` в `generate.py`), nginx `/cosmetics/` и `GV_COSMETICS_BUCKET`, зависимости progression `Pillow`, `aioboto3`. Политику анонимного чтения префикса `c/` и создание бакета делает S1 в job migrate (по образцу media-pipeline).
- Потоки: S1 `stage5b/s1-shop`, S2 `stage5b/s2-notifications` (worktree-ы от `stage5b/integration`, по `tdd-guide`); после каждого один ревьюер (S1 — `security-reviewer`, S2 — только unsubscribe и рассылка); коммиты потоков — после «да» владельца.

### Потоки S1 и S2 (реализованы, не закоммичены; ждут «да» владельца)
- Шаг 0a.1 закоммичен локально (`a24cc9e`, не запушен): инфраструктура под потоки.
- **S1** (worktree `.claude/worktrees/agent-a08b8115a6b6f4b8e`, ветка `stage5b/s1-shop`): магазин, инвентарь, оформление, промокоды, паки, медиа предметов, замена выдачи колеса; миграции `0005`, `0006`; `gv test progression` 643 passed. Ревью: `security-reviewer` (3 важных замечания исправлены, 5 и 8 — ниже), `review-migration` (учтено в `0005/0006`: составной FK погашений, `ON DELETE RESTRICT`, индексы, проверки отката). **Требуется ручная проверка владельцем** (ledger, промокоды, загрузка файлов).
- **S2** (worktree `.claude/worktrees/agent-a6555ca20d7ea258d`, ветка `stage5b/s2-notifications`): сервис notifications целиком; миграции `0002`, `0003`; `gv test notifications` 185 passed. Ревью: `security-reviewer` на отписку и рассылку (2 важных и желательные исправлены). **Требуется ручная проверка владельцем** (отписка без входа, ключи HMAC).
- Снимок OpenAPI progression и notifications устарел (убраны 501, добавлены 4xx): `gv contracts export` и `pnpm gen:api` — при слиянии потоков в integration (оркестратор).
- Открытые задачи оркестратора: (1) внутренняя ручка identity `POST /internal/v1/users/emails` (scope `users.emails.read`, до 500 `user_ids`, только подтверждённые адреса) и боевой `EmailDirectory` — без неё рассылка уходит в `failed`; (2) справочник «код причины → текст» для `avatar_rejected`; (3) фиксированный dev-ключ HMAC отписки и `GV_PROGRESSION__PROMO_HMAC_KEY` в compose local/ci (`GV_NOTIFICATIONS__*`, `GV_S3__*`/`GV_REDIS_URL` для progression); (4) суточный потолок эмиссии промокодов (сейчас потолок только на партию; изменение схемы конфига); (5) `validate_custom_avatar` при публикации конфига — в S3; (6) `client_max_body_size` на admin-локации для загрузки медиа предметов; (7) формат realtime-сообщения `notification.created` в `contracts/realtime`; (8) страница `/unsubscribe` — S5.
- Решения потока S1: `per_user_once` — один код партии на игрока; промокод с неактивным или уже имеющимся предметом отвечает тем же 404 `promo_invalid` (ADR 0048: не раскрывать состояние кода); повтор покупки отдаёт текущий баланс; неактивный предмет скрыт из профилей и матчей.

### Доработки после S1 и S2 (влиты в `stage5b/integration`, запушены)
- **X** (`stage5b/x-identity-emails`): `POST /internal/v1/users/emails` в identity (scope `users.emails.read`, клиент notifications; до 500 id, только активные с подтверждённым адресом; лимит fail-closed по вызовам и по числу адресов `GV_USERS_EMAILS_LIMIT__*`, 20000 id в час; аудит с `token_id`). `security-reviewer` пройден. TDD в потоке нарушен (красный прогон не снимали), принято владельцем, тесты отказа строгие. **Требуется ручная проверка владельцем** (identity).
- **Y** (`stage5b/y-notif-reasons`): `lookups` в конфиге типов (код причины → текст, fallback «не прошла модерацию»), `HttpEmailDirectory` поверх ручки X, контракт `contracts/realtime/notification.created.v1.json`. Коды причин: `inappropriate`, `copyright`, `low_quality`, `personal_data`, `other` — их обязан использовать media-pipeline (S3).
- **Z** (`stage5b/z-promo-caps`): `promo.max_daily_verses_per_actor` и `promo.max_daily_verses_total` (обязательны в секции `promo`; порядок `max_batch ≤ actor ≤ total` проверяет домен), 422 `progression.promo_actor_daily_cap_exceeded|promo_total_daily_cap_exceeded`, отозванные партии входят в сумму. **Требуется ручная проверка владельцем** (валюта).
- **0a.3:** в `AssetModerated` добавлено обязательное `public_key` (`u/<hex>.webp` у одобренного, `null` у отклонённого). Проверка `custom_avatar_item_key` при публикации конфига — в S3.
- compose: ключ HMAC промокодов (`GV_PROGRESSION__PROMO_HMAC_KEY`, генерируется `gv dev secrets`; ключа HMAC отписки больше нет — токены непрозрачные), `GV_PUBLIC_BASE_URL`, сервисные клиенты `progression → media_pipeline[ugc.image]` и `notifications → identity[users.emails.read]`.
- Тесты на слитом дереве: identity 432, notifications 225, bff_web 255, progression 659, tools/gv 157; media_pipeline — те же падения Windows, что и на `main`.
- Хвост: ADR 0047 допишется описанием `lookups` при принятии (сейчас черновик).

### Потоки S3 и S4 (влиты в `stage5b/integration`, запушены)
- **S3** (`stage5b/s3-avatar`): аватарка. media_pipeline: `domain/ugc.py`, ingest и модерация (одобрить, отклонить, снять), приём только статики (WebP, кроп до квадрата, без EXIF), `p/` (pending, подписанный preview `/media/p/…`) → `u/` (публично `/ugc/u/<hex>.webp`) при одобрении, замена прежнего одобренного без события (`replaced`), очередь `ugc_object_purge` + задача `media.ugc.purge`, чистка осиротевших `p/` (`media.ugc.orphans`), `config/ugc_purposes.yaml` (лимит 1,5 МБ ≤ gateway 2m); миграция **`0008_ugc_images`**. progression: заявки (лимит `GV_PROGRESSION__AVATAR_SUBMIT_LIMIT` 5/ч, fail-closed), `application/avatar`, consumer `AssetModerated`, аватар в `/v1/appearance` и `/v1/me/avatar`, проверка товара аватарки при публикации конфига; миграция `0007_avatars` (частичный UNIQUE «одна pending на игрока»). `security-reviewer` (загрузка файла) и `review-migration` пройдены. **Требуется ручная проверка владельцем** (загрузка файлов, политика бакета, `/ugc/`, `/media/p/`).
- **S4** (`stage5b/s4-admin`): разделы «Магазин», «Промокоды», «UGC-изображения», «Рассылки» в services/admin; `core/uploads.py` (LimitedUploadHandler), `core/notifications_client.py`, `core/payload.py`; коды промокодов нигде не сохраняются. `code-reviewer` пройден.
- Адрес notifications в админке по умолчанию совпадает с DNS compose (`GV_ADMIN__SERVICE_URLS__notifications` отдельно не нужен).
- `me/appearance` не содержит аватарку (хвост в [backlog](../backlog.md)): bff собирает аватар из `/v1/appearance?user_ids=` и `/v1/me/avatar`.
- Тесты на слитом дереве: progression 712, media_pipeline 466 (+3 ffmpeg и 2 errors Windows, как на `main`), notifications 225, identity 432, bff_web 255, tools/gv 157, admin 474 (+ давний `test_runtime`), frontend 373 (при параллельной нагрузке один раз падал `CollectionEditor` автосохранение — нестабилен, отдельно проходит).

### Шаг 0a (сделано, закоммичено `c25d138`)
- События: `progression.{ItemPurchased,ItemEquipped,PromoRedeemed}` и `media_pipeline.AssetModerated` (топик `media_pipeline.ugc_image.v1`) со схемами и примерами; `contracts/notifications/notification_types.v1.json` (схема типов уведомлений и пример; контрактный тест сверяет поля с схемами событий).
- Конфиг progression: необязательные секции `shop`, `promo`, `spend` (схема и пример); домен их пока не разбирает — поток S1 (в `test_config_bounds.py` префиксы `shop.|promo.|spend.` временно в `OTHER_STREAMS`, S1 их убирает).
- Заглушки 501: progression `api/public/shop*.py`, `api/admin/shop*.py`; media_pipeline `api/ugc.py` (internal приём + admin-очередь); notifications `api/{public,admin,schemas}`; bff `/pages/shop`, `/pages/shell`, null-блоки `appearance|inventory|avatar` (профиль, игрок, итог матча). Зависимость `python-multipart` добавлена в progression.
- Миграция прав identity `0014_seed_economy_perms` (+ тесты); `notifications` в `COOKIE_MUTATION_SERVICES` (+ `compose.services.yml` через `gv gen`); `notifications` в `frontend/scripts/gen-api.mjs`.
- Решения 0a (не из списка владельца): (1) `GET /v1/appearance?user_ids=` (≤12) в progression — оформление игроков для матча и профиля; (2) лимиты и форматы UGC-изображений и коды причин отклонения — конфиг media-pipeline, а не progression; (3) `POST /v1/unsubscribe` без входа (подписанный токен); (4) предметы имеют стабильный `key` (на него ссылается `wheel.sectors[].prize.item_key`); (5) bff остаётся GET-only: `/pages/shell` объединяет тему и колокольчик; (6) в ответах bff отсутствующие блоки 5b — явный `null` (как `progress` главной в 5a), существующие тесты обновлены.
- Ревью 0a (code-reviewer): блокирующих нет. Учтено: `extra="forbid"` в публичных телах, `promo.max_batch_verses` и `contains earned` в `bucket_order`, 429 и описание защиты у `unsubscribe`, `moderator_id` убран из `AssetModerated`, тесты «без ключа / без reason / лишнее поле», уточнения в ADR 0048 и плане §9.4 (границы `shop|promo|spend` в `BOUNDS`, уникальность ключей, `no-store` на успешных ответах — задачи S1).
- Проверки: `gv lint` (ruff, mypy, import-linter, `gv gen --check`, `gv contracts check`) зелёный; progression 408, notifications 26, identity 400, bff_web 255, tools/gv 157; frontend lint, typecheck, vitest 373. media_pipeline: 3 integration-теста ffmpeg (`test_media_adapters`, код 4294967283) и 2 errors `test_use_cases::test_upload_validation` падают и на `main` (проверено worktree-ом `origin/main`, 51af674) — окружение Windows, не чинится в 5b; admin: давний `test_runtime` (см. выше).

## Чек-лист ручной проверки 5b владельцем (перед слиянием `stage5b/integration` → `main`)
**Требуется ручная проверка владельцем** (зоны повышенной безопасности и то, что без живого стека не прогонялось):
1. **Живой стек.** `gv dev up`, `gv dev seed` (теперь грузит предметы и пак и публикует конфиг v2 с `custom_avatar`), `E2E_SEEDED=1`: `e2e/shop.spec.ts`, `e2e/notifications.spec.ts`, `e2e/progression.spec.ts`. Допущения спеков — в отчёте потока (селекторы, поля ответов, время доставки уведомления ≤ 90 с).
2. **Ledger (траты Версов).** Покупка: одна проводка на `shop:{user}:{item}`, параллельные повторы, баланс не ниже нуля; повтор покупки отдаёт 200 `repeated=true`; колесо с предметом и Версы за дубль; `reconcile` сходится; миграции `progression 0005`–`0007` на чистой БД (вверх/вниз).
3. **Промокоды.** Только HMAC-хеш в БД; одинаковый 404 на неверный/просроченный/исчерпанный/имеющийся предмет; лимит попыток по игроку и IP (Redis fail-closed); потолки эмиссии (партия, актор в сутки, общий в сутки; отозванные считаются); коды не попадают в логи, аудит и сессию админки.
4. **Загрузка файлов.** Аватарка: `POST /v1/me/avatar` → media-pipeline, лимит 1,5 МБ (gateway 2m), только статика, перекодирование в WebP, `p/` → `u/` при одобрении, `/ugc/` и `/media/p/` на gateway (`nginx -t` только в CI), политика бакета, чистка осиротевших объектов (`media.ugc.purge`, `media.ugc.orphans`); картинки предметов: animated WebP, лимиты `shop.media`, публичный `/cosmetics/`.
5. **identity.** `POST /internal/v1/users/emails` (scope `users.emails.read`, клиент notifications; лимиты fail-closed, ≤ 20000 адресов в час на клиента); ключи `GV_SERVICE_CLIENTS`/`gv dev secrets` для клиентов `notifications` и `progression`.
6. **Отписка без входа.** Токен непрозрачный (хеш в БД, срок 90 дней), кнопка на странице `/unsubscribe`, one-click `POST /api/notifications/v1/unsubscribe/one-click` (RFC 8058), `Referrer-Policy: no-referrer`, query не пишется ни в access-лог, ни в трейсы (`fix(observability)`); письма рассылки в логе (`log`-бэкенд) содержат ссылку; допущение: токен действует до истечения срока, а не до первого использования (ADR 0048).
7. **Рассылка.** `EmailDirectory` через identity: проверить на стеке с SMTP-логом, что письма уходят только подписавшимся.
8. **Тема.** `GV_PAGES__DEFAULT_THEME_KEY`, `data-theme` в первом HTML, нет литералов цвета (eslint) и контраст AA (тест); смена темы в рантайме не проверялась.
9. **Прежние пункты** — 5a (чек-лист выше) и хвосты 4a/4b: identity (realtime-токен), match (античит), rating; допущения сидов 4b.

## 5c — social

Ветка `stage5c/integration` от `main` (95e51f9, после PR #29); один PR, потоки — ветки от неё; PR открывает владелец (метка `e2e`). План — [§10.1](../plans/stage-5.md); ADR 0050–0053 (черновики, на принятие владельцем).

### Прогресс
| Шаг | Статус |
|---|---|
| Шаг 0: roadmap, backlog (три раздела), handoff, детализация §10.1, ADR 0050–0052 | сделано, закоммичено (`9e4532c`) |
| 0a контракты | сделано, закоммичено (`f679331`, см. «Шаг 0a» ниже) |
| F1 (друзья, приватность, presence, конфиг в БД) ∥ F2 (комментарии, жалобы, кейсы-ядро, consumers) | сделано, слито, ревью пройдено, запушено (`66ff0df`); social 269, notifications 235 |
| F3 (санкции, identity+match) ∥ F4 (очередь модерации, конфиг, Django-админка, аватарки) | сделано, ревью (security-reviewer, review-migration, code-reviewer) пройдены, слито, запушено (`4c90b4f`); social 382, identity 471, match 251, collections 235 |
| F5a (bff) ∥ F5b (frontend), user_id в заявке | сделано, слито (`ab24e33`, `5b1e602`); bff_web 344, social 384, vitest 687, Playwright на моках 75 |
| 0b (сид `seed_social`, живые `e2e/friends.spec.ts`, `e2e/moderation.spec.ts`) | написано, `tsc -p e2e` проходит, **на живом стеке не запускалось** |

### Шаг 0a (контракты, сделано)
- События `contracts/events/social/*` (11, с примерами `event_id` ...`7a0001`–`7a000b`); конфиг `contracts/social/social_config.v1.json` (+ пример).
- OpenAPI social: `/v1` (друзья, заявки, код, блокировки, приватность, presence, приглашение, `players/{id}/relation`, комментарии, причины, жалобы, `me/sanctions`) и `/admin/v1` (кейсы: список, карточка, claim, escalate, resolve; `users/{id}/moderation`; выдача и снятие санкции). Заглушки 501, доступ по контракту (тесты `services/social/tests/component/test_stubs.py`).
- bff: необязательные блоки `PlayerBlocks.social`, `OnlineBlocks.friends`, `CollectionBlocks.comments`, `ShellPage.sanctions`, `PlayerPage.restricted`, `MatchPlayerCard.can_report` (старые тесты обновлены на явные `null`/`false`).
- notifications: 5 типов в `notification_types.yaml`, топик `social.social.v1` в `CONSUMED_TOPICS`, необязательное поле `data` (тип-уровень в конфиге, `NotificationItem.data`, realtime `notification.created`) — в 0a только контракт и схема, **заполнение `data` потребителем (room_code у `room_invite`) — задача F1**.
- identity: миграция прав `0015_seed_social_perms` (admin — все четыре, moderator — без `social.sanctions.account`); `libs/auth`: `Sanction.ONLINE = 32`.
- Инфраструктура: `social` в `COOKIE_MUTATION_SERVICES` (+ тест), `redis = true` у social в `services.toml` (+ `gv gen`), `social` в `gen-api.mjs`; снимки OpenAPI и типы фронтенда обновлены.
- Проверки: `gv lint` зелёный; social 91, bff_web 318, notifications 227, identity unit 242 (+ integration `0014`/`0015`), tools/gv 178, libs/auth 75; frontend lint и typecheck зелёные, vitest 621/622 (известный нестабильный `CollectionEditor`, отдельно проходит).
- Решения ассистента 0a: (9) «кто может звать» — это `friend_requests = all|none` (заявки в друзья), а не приглашения в комнату: приглашение доступно только друзьям; (10) `can_report` и блоки bff в 0a всегда `false`/`null` — решает F5; (11) права: `social.moderate`, `social.comments.delete_any`, `social.sanctions.manage`, `social.sanctions.account`; (12) срок санкции в запросе `duration_s` 60…315 360 000 с или `null` (бессрочно), пределы — конфиг.
- Не сделано в 0a (по плану — потоки): внутренний `/internal/v1`, `contracts/realtime/room_invite` не создавался (приглашение идёт через `notification.created` с `data.room_code`).

### Порядок ручных проверок (решение владельца 2026-10-24)
Ручные проверки владельцем по 5a, 5b и 5c проводятся **одним заходом после всего этапа 5** (после 5c, перед 5d/релизом по решению владельца); потоки ими не блокируются. Сводный список — [backlog](../backlog.md#3-открытые-ручные-проверки-владельцем); чек-лист 5c дописывается в конце 5c. Решения ассистента 1–5 по 5c (gateway→match/social, без `PlayerForfeited`, `RoomInviteSent`/`ReportResolved`, `friend_requests`, биты санкций) приняты владельцем.

### Поправка владельца: конфиг social в БД (2026-10-24, ADR 0053)
Конфиг social — не файл в пакете, а версии в БД по образцу progression (`config_versions`: черновик → публикация → откат; публикация привязана к `body_sha256`). Причина: лимиты и порог жалоб крутят во время спама без деплоя, справочник причин правят модераторы. Контракт добавлен (`/admin/v1/config/versions[/{v}]`, `PUT /config/draft`, `POST /config/draft/publish`, `POST /config/versions/{v}/rollback`; права `social.config.manage` / `social.config.publish`, миграция identity `0016`: оба права у admin и moderator; `0018` снимает `publish` у moderator — публикует только admin). Разделение работ: **F1** — таблица `config_versions` (миграция `0002`), загрузка действующей версии с коротким кэшем (нет версии → 503 `social.config_unavailable`), задача relay `social.config.bootstrap` из `seeds/social/config.yaml` (только local/ci); **F4** — admin-ручки конфига и страница в админке.

### Решения владельца (2026-10-24, поверх §10)
Друзья: заявки по нику/коду, блокировки, онлайн-статус через presence, приглашение в комнату (in-app + realtime), список только владельцу, «кто может звать» все/никто, лимиты в конфиге. Комментарии: только сборники, плоские, ≤500, `gv_text.ugc`, правка 5 мин. Жалобы: игрок (матч, не соло), сборник, комментарий, аватарка; порог в очередь; очередь в админке; автоматики нет. Санкции `RANKED`, `CHAT`, `ONLINE`, `ACCOUNT` со сроком/бессрочно. Приватность: профиль и статистика публичны по умолчанию, скрыть можно; история матчей — владельцу. Notifications: 5 типов, только in-app + realtime.

### Решения ассистента (не из списка владельца)
1. Gateway санкции не применяет (Nginx не разбирает JWT): применяют identity (ACCOUNT, выпуск токена), match и social (`claim | proj`). Уточняет формулировку владельца «match и gateway».
2. `PlayerForfeited` не вводим: счётчики из `MatchFinished.players[].finish` и `PlayerRated`.
3. Новые события сверх списка: `RoomInviteSent`, `ReportResolved`.
4. `CHAT` = `CHAT_MUTE | COMMENTS`; `CHAT_SLOW` резерв; `ONLINE=32`; `ACCOUNT` — флаг `account_banned`, не claim.
5. Presence в social (Redis + проекция «в матче»); social получает зависимость Redis.
6. Жалоба на игрока принимается из `PlayerRated` и через API social (при общем несоло-матче).
7. Модератор не санкционирует себя; просрочку снимает периодическая задача relay.
8. Очередь UGC-изображений media-pipeline в общую очередь social не подтягиваем (позже отдельным ADR).

### Итоги F5 и 0b
- **bff (F5a):** клиент social, блоки `social`/`restricted` (`/players/{id}`), `friends` (`/online`), `comments` (`/collections/{id}`), `sanctions` (`/shell`), `can_report` (`/match/{id}`). Приватность fail-closed: social не ответил — чужая статистика не отдаётся. `Viewer.user_id` — `sub` проверенного токена.
- **frontend (F5b):** `/friends` (список со статусами, заявки, добавить по нику/коду/`user_id`, мой код, блокировки, приватность), heartbeat раз в 30 с (`features/friends/config.ts`), профиль игрока (кнопки, `restricted`), панель приглашения в лобби и «Войти» из уведомления `room_invite` (`data.room_code`), комментарии сборника, `ReportDialog` (итог матча, сборник, комментарий), баннер и страница `/sanctions`, сообщения `match.online_banned` и `auth.account_banned`.
- **0b:** шаг `seed_social` в `gv dev seed` (ждёт публикации конфига, больше ничего не создаёт), `e2e/friends.spec.ts`, `e2e/moderation.spec.ts` (admin-ручки social — напрямую на `127.0.0.1:8111`, через gateway не проброшены). **Не запускались на живом стеке** — что проверять: селекторы (`request-out-*`, `my-code`, `player-restricted`, `report-reason-*`, `notification-join`), состав тела `PUT /config/draft`, окно доставки уведомлений ≤90 с и presence 30 с, лаг проекции санкций в match (опрос до 60 с), права сид-админа на `social.config.*`, `social.sanctions.account`, `social.moderate`, вход забаненного `account` с известного устройства (иначе `code_required`).
- **Решения ассистента (F5):** друзья видят скрытый профиль друг друга (принято владельцем); добавить в друзья можно по `user_id` (тот же путь и те же 404, что у ника); кнопка «добавить» в итоге матча показывается при `can_report`.

## 5d — сезонные события

Ветка `stage5d/integration` от `main` (55624d4, после PR #31); PR #33 открыт с ветки `stage5d/pr` (сквош: ключ рамки в ранних коммитах срабатывал на gitleaks); план — [§12.1](../plans/stage-5.md), API — [stage-5d-api.md](../plans/stage-5d-api.md), ADR 0054–0056 (черновики). Автономный режим владельца (2026-10-24): решения ниже приняты без него.

### Что сделано
| Шаг | Состав |
|---|---|
| 0a | события `EventRunStarted/EventRunFinished/FindUnlocked/EventCompleted`, `contracts/progression/event_definition.v1.json`, права identity `0019` (оба права только у admin), заглушки API, блоки bff |
| E1 | progression: домен `events_season`, миграция `0008` (events, event_runs со снимком, event_find_progress, event_completions, `items.event_key`), ручки игрока и админа, claim ui_spot с лимитом Redis, награды через ledger, задача `progression.events.tick`, метрики `duel_win`, `weekly_top_place`, `wheel_spin_streak`, `item_equipped`, `collection_played` |
| E3 | bff (`theme.key` от события, блок `event`, `/pages/events/{event_key}`, архив в профиле), frontend (тема `winter`, `/events/[key]`, баннер, `EventSpot`, архив), Playwright на моках |
| 0b | `seeds/progression/events/{new_year_2026,halloween}.yaml`, рамка `frame_snowfall` (`event_key`), шаг `seed_events` в `gv dev seed` (карта `new_year_2026.webp` не лежит в сидах: события создаются без неё) |
| E2 | notifications: `find_unlocked`, `event_completed`, `event_started` (аудитория `known_users`, пачки); admin: раздел «События»; `e2e/events.spec.ts` |
| Ревью | волна 1: `security-reviewer` (E1), `code-reviewer`, правки внесены; волна 2: `security-reviewer` (admin, fanout), правка превью карты |

### Решения 5d, принятые без владельца
1. Статусы запуска считаются по времени; задача `tick` материализует `active/finished` и публикует события. Остановленный запуск занимает период до `stopped_at`: новый запуск не раньше.
2. Предмет-награда события — «только для события»: `items.event_key` вместо цены и уровня; не продаётся, не выпадает с колеса, не гасится промокодом, не кладётся в пак; `reward.item_key` определения обязан быть активным предметом этого события. При выдаче проверка повторяется.
3. Серверный финал копит прогресс скрыто и открывается каскадом после последней обычной находки; ui_spot финала до этого — 409 `event_find_locked`.
4. `item_equipped` — уровень, цель = 1; `weekly_top_place` — число недель в топе ≤ `place_max`; `collection_played` — любая сессия по сборнику (тегов в `GameFinished` нет — [backlog](../backlog.md)). `weekly_top_place` и `item_equipped` доступны только событиям, не заданиям.
5. Находка с периодом `day` открывается, если цель достигнута в любые одни сутки.
6. Опубликованный, но не начавшийся запуск игроку не виден (404, в том числе для claim); остановленный остаётся архивом. Карта читается живьём, а не из снимка.
7. Событие не допускает `starts_at` старше минуты (422). Наивное время отклоняется (422).
8. «Новый год»: 5 server-находок (`daily_completed`, `duel_win`, `collection_played`, `item_equipped`, `wheel_spin_streak`), 1 ui_spot (лидерборды), финал-ui_spot на главной, тема `winter`, статус draft (владелец создаёт запуск в декабре).
9. Аудитория `event_started` — игроки, уже известные notifications (есть уведомления или настройки); полный список пользователей identity не отдаёт ([backlog](../backlog.md)). Рассылка не останавливается при досрочной остановке запуска.
10. Определение события в админке правится текстом YAML/JSON с превью точек на карте, а не формой по находкам.
11. Страница события отдаёт ключи ui_spot-находок (скриптом можно «найти» все спрятанные элементы без поиска) — принято, ограничивает лимит claim 30/60 и цена наград.
12. Награды повторного запуска выдаются заново (новый `run`), суточных потолков эмиссии нет — ограничивает право `publish` (только admin).

### Хвосты 5d (в [backlog](../backlog.md))
Тег коллекции в `GameFinished`; полная аудитория `event_started` через identity и остановка рассылки при stop; чтение настроек пачки одним запросом; `UserDeleted` в notifications; `EXCLUDE USING gist` по периодам запусков; форма редактора находок в админке; карта «Нового года».

### Ручная проверка владельцем (ledger, миграции, файлы) — **требуется**
- progression: миграция `0008` вверх/вниз на чистой БД (откат прерывается при данных события); награды находок (xp/Версы через ledger, `event:{run}:{find}:{user}`), параллельные claim и дубль события сервиса → одна проводка; предмет события не покупается и не выпадает; публикация под advisory-lock, пересечение периодов, привязка к `body_sha256`; права `progression.events.manage|publish` (identity `0019`, только admin).
- Загрузка карты в S3 (тип/размер, публичный префикс `/cosmetics/`).
- Живой стек: `gv dev up`, `gv dev seed`, `E2E_SEEDED=1`, `e2e/events.spec.ts`; создать запуск «Нового года» в админке, проверить тему winter у всех, баннер, спрятанный элемент на лидербордах, финал, рамку в инвентаре, уведомления.
## Единый чек-лист ручных проверок владельцем (этап 5 целиком: 4a, 4b, 5a, 5b, 5c, 5d)
Решение владельца 2026-10-24: один заход после этапа 5, потоки не блокируются. Зоны повышенной безопасности — **требуется ручная проверка владельцем**. Предусловие: `gv dev up`, `gv dev seed`, `E2E_SEEDED=1`.

**Общее (живой стек)**
1. Прогон e2e на полном стеке: `lobby/duel/party` (4a), `ranked` (4b), `progression` (5a), `shop`, `notifications` (5b), `friends`, `moderation` (5c). Селекторы и тайминги 5b/5c — по спискам выше.
2. Миграции на чистой БД (вверх/вниз): progression `0002`–`0007`, notifications `0002`–`0005`, media_pipeline `0008`, identity `0014`–`0017`, social `0002`–`0005` (откат social `0004` прерывается, если есть санкции), match `0004`. `nginx -t` на образе 1.28 — в CI.

**4a/4b: identity, match, rating**
3. identity: realtime-токен (`/v1/realtime/token`, лимиты); match: античит, комнаты, отзыв подписки; rating: сезоны, топ недели. Допущения сидов 4b: K Эло 40/20, пороги званий, фильтры `similar`. Win-trading — детектор остаётся хвостом (ADR 0038).

**5a: ledger progression (зона повышенной безопасности)**
4. Роли Postgres: журналы защищены триггерами, но роль сервиса владеет таблицами (REVOKE — этап 7, риск принят). Начисления: дубль события даёт одну проводку; потолки соло и онлайн-Версов не обходятся параллельными матчами около полуночи. Конфиг: верхние границы сумм, публикация привязана к хешу, админ без `publish` не публикует. Ручная выдача и сторно (только `earned`, только кредиты, `no-store`). Награды сезона и недели (`rating.SeasonStandingsFinalized`, пачки). Живая проверка `/profile`, колеса (один спин в сутки), админки «Прогрессия». Пароль сид-админа локального стенда мог попасть в вывод — при необходимости сменить.

**5b: экономика, файлы, уведомления**
5. Покупка: одна проводка на `shop:{user}:{item}`, параллельные повторы, баланс не ниже нуля, `reconcile` сходится. Промокоды: только HMAC в БД, одинаковый 404, лимиты попыток (Redis fail-closed), потолки эмиссии, коды не в логах.
6. Загрузка файлов: аватарка (1,5 МБ, статика, WebP, `p/` → `u/`), `/ugc/`, `/media/p/`, `/cosmetics/`, политика бакета, чистка осиротевших объектов.
7. identity `POST /internal/v1/users/emails` (scope, лимиты fail-closed); отписка без входа (непрозрачный токен, one-click, `Referrer-Policy`, query вне логов); рассылка только подписавшимся; тема (`data-theme`, смена в рантайме не проверялась).

**5c: social, санкции (зона повышенной безопасности)**
8. identity: бан `account` — вход (пароль, код, Яндекс), refresh, admin-вход дают 403 `auth.account_banned` после проверки пароля; отзыв сессий и denylist; снятие бана; окно действия уже выданного access ≤15 мин; claim `sanctions` в токене.
9. match: `ONLINE` — отказ на создании/входе в комнату, ready/start/guess/чате, очереди, realtime-токене; выбивание из очереди и лобби по событию, `unsubscribe` Centrifugo с повтором; fail-closed 503 при недоступной проекции; `RANKED`, `CHAT`.
10. social: права (`manage`/`account`, модератор не снимает бан `account`, не санкционирует себя), история санкций, замена того же вида, истечение (задача relay), журнал `sanctions` append-only.
11. Модерация: порог жалоб → кейс; claim/escalate (решить эскалированный может только admin)/resolve; `ContentModerated` скрывает сборник и комментарий, снимает аватарку; `ReportResolved` по заявителю; тексты жалоб только в админ-ответе и экранированы; конфиг social (черновик → публикация → откат): модератор читает и правит черновик, публикует и откатывает только admin (identity `0018`).
12. Друзья и приватность: одинаковые ответы для неизвестного/скрывшего/заблокировавшего (в т.ч. по времени — принятый риск ADR 0050), коды (перевыпуск, перебор — лимит), список друзей только владельцу, блокировка закрывает комментарии в обе стороны (`social.blocked`), приглашение в комнату (`data.room_code`).
13. Принять ADR 0042–0056 (сейчас черновики) или внести замечания.

**5d: сезонные события (ledger, файлы)**
14. Миграция progression `0008` вверх/вниз; награды находок через ledger (одна проводка на `event:{run}:{find}:{user}`, параллельные claim, дубль события); предмет события не покупается и не выпадает с колеса; публикация (advisory-lock, пересечение периодов, `body_sha256`), права `progression.events.manage|publish` (только admin, identity `0019`); карта в S3. Живой стек: `e2e/events.spec.ts`, запуск «Нового года» из админки (тема winter у всех, баннер, спрятанные элементы, финал, рамка, уведомления). Решения 5d без владельца — раздел [5d](#5d--сезонные-события).

## Открыто
- Ручные проверки — единый список выше; хвосты — [docs/backlog.md](../backlog.md#проход-по-техдолгу) и [roadmap](../09-roadmap.md#хвосты-этапа-5).
- Нерешённое в 5c (в backlog): ник и `asset_id` аватарки в `PlayerPage`/`PlayerAppearance` (нужны для жалобы на аватарку чужого игрока и ника в скрытом профиле), кнопка жалобы в истории матчей, страница для уже открытой сессии забаненного `account`, точный флаг «можно добавить» в итоге матча, disconnect Centrifugo для забаненного, алерт на лаг consumer social в match, иерархия модераторов.
- Win-trading — детектор остаётся хвостом (ADR 0038): в 5c только счётчики порядочности для модератора.
- Исключённый хозяином комнаты игрок может переподписаться на канал старым токеном (до 10 мин) — хвост 4a; для бана `ONLINE` закрыто (перепроверка при продлении).

## Дальше
1. Владелец: PR `stage5d/integration` → `main` (метка `e2e`); единый заход ручных проверок (чек-лист выше), живой прогон e2e.
2. Рефакторинг фронтенда и проход по техдолгу ([backlog](../backlog.md)) до этапа 7.

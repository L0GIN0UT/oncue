# План этапа 5: «Прогрессия и социальное»

> Статус: **решения владельца приняты 2026-10-23** (§1). Пункты с пометкой «по рекомендации» приняты ассистентом и ждут подтверждения владельца. План написан на весь этап; 5a влит, **5b в работе** (§9), 5c — модель (§10), 5d — модель (§12). Основа: [roadmap](../09-roadmap.md) (этап 5), [концепция](../01-concept.md) §9, [сервисы](../architecture/services.md) §10 progression, §12 social, §13 notifications, [админка](../05-admin-panel.md). Структура — как у [плана этапа 4](stage-4.md). Новые ADR 5b: 0045–0049 (черновики). ADR 5a: [0042](../adr/0042-progression-xp-levels-config.md), [0043](../adr/0043-currency-ledger-verses.md), [0044](../adr/0044-daily-wheel-and-boosts.md) (черновики).
> Ветка-база: `main` (после PR #22–#25).

## Что уже есть

| Что | Состояние |
|---|---|
| `services/progression`, `services/social`, `services/notifications` | Заглушки этапа 0: health, метрики, outbox/inbox, миграция `0001_service_tables`, пустые слои. Домена нет |
| Входные события | `game.GameFinished` (`day != null` у дейлика), `game.DailyCompleted` (серии нет), `match.MatchFinished`, `match.PlayerRated`, `rating.SeasonFinished` (мест игроков нет — `standings_ref`), `rating.WeeklyTopFinalized` (`top` ≤ 100) |
| Санкции | `gv_auth.Sanction`: `RANKED`, `CHAT_SLOW`, `CHAT_MUTE`; события `UserSanctionsChanged` нет (5c) |
| Сборники | `collections.CollectionPublished` — источник для разового задания «первый сборник» |
| Биллинг | `billing.enabled=false`, план `free_all`; паки и продажа не показываются |
| Admin | Раздел «Рейтинг» (4b) — образец admin-раздела поверх admin-API сервиса |

## 0. Цель и критерии готовности

**Этап 5 в целом:** игрок растёт (опыт, уровни, задания, бейджи, колесо), копит Версы и тратит их на косметику (5b), общается с друзьями и жалуется на нарушителей (5c). Весь функционал игры доступен с 1 уровня; за уровни открывается только косметика и титулы.

**Демонстрация 5a** (на compose, `gv dev up` + `gv dev seed`):
1. Соло-раунд → опыт и Версы начислены, дневной потолок виден в профиле.
2. Набор порога → смена уровня, событие `ProgressionLevelChanged`, уровень не понижается.
3. Выполнение дневного задания → награда (опыт, Версы), `QuestCompleted`.
4. Бейдж «первая победа» после первой выигранной дуэли.
5. Спин колеса: один раз в сутки, повтор возвращает тот же результат; выпавший буст ×1.5 действует на 3 онлайн-матча, на рейтинг не влияет.
6. Профиль чужого игрока: уровень, бейджи, статистика (`users/{id}/progress`).
7. Админ меняет награды задания и сектор колеса — новая версия конфига действует с следующего периода.
8. Дубль события не создаёт вторую проводку; журнал и баланс сходятся.

**Цели 5b, 5c, 5d** — в §9 (потоки), §10 и §12 (5c и 5d — только модель).

## 1. Сквозные решения

### Решения владельца (2026-10-23)
- **D1. Деление.** 5a progression (опыт, уровни, Версы, задания, бейджи, колесо, бусты, профиль-статистика); 5b экономика и уведомления; 5c social. Реализация — по подэтапам.
- **D2. Уровни.** 100 уровней, порог `n` = 500 + 20·n; формула и пороги — в конфиге с версией. Функционал игры не зависит от уровня; за уровни — косметика и титулы.
- **D3. Опыт — за участие, не за результат** (как Rocket League/Valorant): соло-раунд 20 + очки/10 (≤40); дейлик 50 + 10×серия (≤100); дуэль 40 + 10 за выигранный раунд, победа +60; party участие 40, по месту 1-е +80, 2-е +50, 3-е +30, дальше +10; первая победа дня в онлайне +200; досрочный выход и `aborted` — 0; потолок с соло 2000/сутки. Ranked и кастом равны, рейтинг на опыт не влияет.
- **D4. Бусты** (с колеса): множитель опыта на N матчей, активные эффекты игрока, только к опыту, никогда к рейтингу.
- **D5. Версы.** Append-only журнал, баланс — проекция в той же транзакции, целые, не ниже нуля, проводка идемпотентна. Начисление: соло-раунд 1, дейлик 5, дуэль 10/3, party по месту 10/6/4/2, потолок с соло 50/сутки; топ недели (топ-100) и итог сезона по сетке 1, 2, 3, 4–10, 11–100, 101–1000 (суммы в конфиге ladder-а); **решение владельца: топ недели — топ-100, разряд 101–1000 только у сезона**. За уровень Версы не даются. Траты — 5b.
- **D6. Паки за деньги** — только Версы, только косметика в едином стиле, смешанные; модель в §9, реализация 5b, продажа включается вместе с billing.
- **D7. Задания и бейджи.** 3 дневных и 10 недельных из конфига; админ редактирует набор и награды (опыт, Версы). Разовые («первая дуэль», «первый сборник») — конфиг. Бейджи на старте: первая победа, серия дейлика 7 и 30, 100 и 1000 угаданных, звание на конец сезона. Мастерство по персоне — после релиза.
- **D8. Колесо.** Число секторов и призы из конфига (12 по умолчанию): бусты, Версы, редко косметика. Один спин в сутки, обновляется с дейликом; сектор выбирает только сервер, веса в конфиге, спин идемпотентен по `(user_id, день)`. Косметика — через инвентарь 5b; в 5a заглушка выдачи с записью в журнал.
- **D9. Косметика и магазин (5b).** Слоты: аватар из набора, рамка, титул, фон профиля; картинки админ грузит в S3. Предмет открывается по уровню или продаётся за Версы. Своя аватарка — товар на один раз, затем UGC-изображение через media-pipeline с модерацией. Промокоды генерирует админ.
- **D10. Social (5c).** Друзья, комментарии к сборникам (denylist `gv_text.ugc`, к профилям нет), жалобы, санкции RANKED/CHAT/ONLINE/ACCOUNT со сроком только вручную через очередь модератора, приватность (профиль и статистика публичны по умолчанию, история матчей только владельцу).
- **D11. Notifications (5b).** In-app и realtime через личный канал Centrifugo; e-mail только организационный; Push — порт и таблица устройств сейчас, реализация после релиза.

### Решения по рекомендации (ждут подтверждения владельца)
- **R1. Сутки.** Один `progression.timezone` (как у дейлика); валидатор сверяет. День берётся по `finished_at` события, не по времени обработки.
- **R2. Дейлик.** `GameFinished` с `day != null` за раунды не начисляет; награда только по `DailyCompleted`. Серию ведёт progression по `day`, отдельно по каждому домену.
- **R3. Буст и потолок.** Буст только для онлайна, с соло-потолком не пересекается; если когда-нибудь для соло — сначала буст, потом жёсткий потолок.
- **R4. «Первая победа дня».** `outcome = win` в дуэли или место 1 в party/lobby; ничья не в счёт; `format = lobby` — по сетке party.
- **R5. Антиабьюз.** Бонус за победу и первую победу дня — при сыгранных ≥ `min_rounds_ratio` (конфиг, 0,5); `forfeit`/`left` — 0; `tech_win` — участие и раунды без бонуса за победу.
- **R6. Смена сезона.** Опыт с сезоном не связан; поздний `MatchFinished` начисляется как обычно. Сезонные награды — идемпотентно по `(season, user)`.
- **R7. Гости.** `user_id = null` пропускаются, задним числом не начисляются.
- **R8. Смена конфига.** Пересчёта нет; в проводке хранится `config_version`. Задания назначаются снимком на период, смена действует со следующего периода.
- **R9. Сторно.** Только админ с правом и причиной; если баланс уйдёт ниже нуля — 409. `UserDeleted` обезличивает, но не сторнирует.
- **R10. Отставание.** Баланс согласован внутри транзакции; допустимо только отставание consumer ≤ 30 с (метрика `gv_progression_consumer_lag`).
- **R11. Приватность до 5c.** `users/{id}/progress` публичен; флаг скрытия — поле модели, ручка в 5c.

## 2. Шаг 0a — оркестратор (contract-first, 5a)

### 2.1. События `progression.progression.v1`
`ExperienceGained`, `ProgressionLevelChanged`, `CurrencyCredited`, `CurrencyDebited`, `QuestCompleted`, `BadgeUnlocked`, `WheelSpun` — JSON Schema + примеры в `contracts/events/progression/`. Ключ партиции — `user_id`. В событиях `CurrencyCredited/Debited` — `op_id`, `bucket`, `reason`, `actor_id` (для ручных).

### 2.2. Правка rating (входные данные для наград)
- **Новое событие `rating.SeasonStandingsFinalized` v1:** топ-1000 пачками ≤ 500, aggregate `{season}:{chunk}`. Причина: в `SeasonFinished` мест нет, синхронный вызов rating запрещён. Глубина и размер пачки — настройки rating `standings_top_size`/`standings_chunk_size` (`GV_RATING__*`); в таблицу входят игроки лидерборда (вне калибровки, `min_matches`), порядок: рейтинг по убыванию, затем `user_id`. Реализовано в 0a.
- **`WeeklyTopFinalized` не меняется:** топ недели — топ-100, разряды 101–1000 только у сезона. Решение владельца (см. D5), не ограничение.
- Правка `services/rating/**` — только публикация нового события; делает оркестратор в 0a.

### 2.3. Схема конфига
`contracts/progression/progression_config.v1.json` (+ примеры в `examples/`): уровни (`base`, `step`, `max_level`), источники опыта и Версов, потолки, `min_rounds_ratio`, `timezone`, задания (дневные/недельные/разовые), бейджи, колесо (секторы, веса, призы), лимиты ручной выдачи. Валидатор: монотонные пороги, веса > 0, суммы целые ≥ 0.

### 2.4. API (заглушки 501, снимок OpenAPI)
- progression `/v1`: `me/progress`, `me/wallet`, `me/quests`, `me/badges`, `wheel` (GET: секторы и веса), `wheel/spin` (POST, пустое тело), `users/{id}/progress`.
- progression `/admin/v1`: конфиги (черновик, публикация, откат), ручная выдача и сторно, задания и бейджи.
- bff-web: блоки в `/pages/profile` и `/pages/home` (необязательные до P4).

### 2.5. Права, настройки, документация
- Права identity (миграция): `progression.config.manage`, `progression.config.publish`, `progression.ledger.grant`, `progression.ledger.reverse`.
- `GV_PROGRESSION__*` (timezone, лимиты) через `gv gen`; `infra/services.toml` — без правок руками.
- ADR 0042–0044, `services/progression/CLAUDE.md`, `docs/architecture/services.md` §10.

**Критерий выхода 0a:** `gv lint`, `gv contracts check`, `gv gen --check` зелёные; заглушки отвечают 501.

## 3. Потоки и владение файлами

Корень сервиса: `services/progression/src/progression_service/`.

| Поток | Владеет | Не трогает |
|---|---|---|
| **0a (оркестратор)** | `contracts/events/progression/**`, `contracts/events/rating/SeasonStandingsFinalized.v1.json`, `services/rating/**` (только публикация), `contracts/progression/**`, `application/ports.py`, миграция прав identity, `seeds/progression/**`, `e2e/progression.spec.ts`, `docs/**` | — |
| **P1 домен и ledger** | `domain/{experience,levels,ledger,wallet,config,daily_streak}.py`, `application/accrual/**`, `infrastructure/db/{tables.py,migrations/versions/0002_progression_core.py}`, `infrastructure/messaging/consumers/**`, `api/public/{progress,wallet}.py` | P2–P4 |
| **P2 задания, бейджи, колесо, бусты** | `domain/{quests,badges,wheel,boosts}.py`, `application/{quests,badges,wheel,boosts}/**`, `infrastructure/db/{tables_rewards.py,migrations/versions/0003_quests_wheel.py}`, `infrastructure/messaging/handlers/**`, `api/public/{quests,badges,wheel}.py` | P1 (общее — через порт `RewardHook`) |
| **P3 admin «Прогрессия»** | `api/admin/**`, `application/admin/**` (progression), `services/admin/**` | P1, P2 |
| **P4 bff и фронтенд** | `services/bff_web/**`, `frontend/**` | сервисы |

`contracts/openapi/{progression,bff_web}.json` и сгенерированные типы — только `gv contracts export` / `pnpm gen:api` / `gv gen`.

**Параллелизация (не больше 2):** 0a → P1 ∥ P4 (на моках) → P2 ∥ P3 → 0b. P2 начинает на фейках, миграцию `0003` делает после слияния P1. Слияние: P1 → P2 → P3 → P4. Один `tdd-guide` и один ревьюер на поток; `security-reviewer` — для P1 и admin-ручек P3.

## 4. Поток P1 — домен и ledger (зона повышенной безопасности)

| Что | Тест |
|---|---|
| Чистые функции начисления (опыт, Версы) от `(событие, конфиг на finished_at, дневные счётчики)` | unit + property: сумма за сутки ≤ потолка при любом порядке событий |
| Уровни по порогам, `max_level` не понижается | unit |
| `xp_ledger`, `currency_ledger` (`UNIQUE(op_id)`), `balances` (`CHECK >= 0`), дневные счётчики под блокировкой | integration (testkit-Postgres) |
| Consumers `GameFinished`, `DailyCompleted`, `MatchFinished` (inbox по `(event_id, user_id)`, `op_id` из предмета) | integration: одно событие дважды и с разными `event_id` — одна проводка; дейлик не даёт опыт за раунды |
| Серия дейлика по `day` | unit |
| Награды недели и сезона (`WeeklyTopFinalized`, `SeasonStandingsFinalized`) идемпотентно по `(period, user)` | integration |
| Outbox: `ExperienceGained`, `ProgressionLevelChanged`, `CurrencyCredited` | component |
| Сверка «сумма журнала = баланс», метрика лага consumer | integration на ручной порче данных |
| `GET me/progress`, `me/wallet`, `users/{id}/progress` | component + contract |

## 5. Поток P2 — задания, бейджи, колесо, бусты

| Что | Тест |
|---|---|
| Назначение заданий снимком на период (3 дневных, 10 недельных), прогресс по событиям, once-задания | unit + integration |
| Бейджи: первая победа, серия 7/30, 100/1000 угаданных, звание на конец сезона | unit |
| Колесо: порт `Random`, веса версии, `wheel_spins` PK `(user_id, day)`, повтор — тот же результат | integration: 20 параллельных спинов — одна проводка; распределение на фиксированном `Random` |
| Бусты `(multiplier, matches_left)`, трата по `match_id`, очередь | unit + integration |
| Заглушка выдачи косметики: запись в журнал выдач | unit |
| `RewardHook` (награды заданий в P1-ledger) | contract порта |
| Лишние поля в `wheel/spin` отклоняются | contract |

## 6. Поток P3 — admin «Прогрессия»

Раздел по образцу «Рейтинг»: конфиги (черновик, валидация, публикация, откат), задания и бейджи, колесо (секторы, веса — предпросмотр вероятностей), ручная выдача и сторно (причина обязательна, лимиты, аудит), журнал проводок игрока (чтение). Права §2.5; все мутации с `Idempotency-Key`. Тесты: без права — 403, повтор ключа — no-op, выше лимита — 422.

## 7. Поток P4 — bff и фронтенд

bff: блоки `/pages/profile` (уровень, опыт, Версы, задания, бейджи, колесо) и `/pages/home` (задания дня, колесо); блок недоступного progression — «появится позже», страница цела. Фронтенд: профиль и профиль чужого игрока, прогресс-бар уровня, задания, колесо (анимация без влияния на результат), кошелёк (без трат), экран конфига буста. Playwright на моках.

## 8. Шаг 0b — сиды и e2e (выполнено (0b))

Сиды `seeds/progression/**` (значения владельца из §1, 12 секторов колеса), загрузка периодической задачей по образцу `rating.ladders.bootstrap`. `e2e/progression.spec.ts`: соло → опыт → уровень → спин колеса. Проверка на живом compose — у владельца.

## 9. Подэтап 5b — экономика и уведомления

Решения владельца 2026-10-23 (поверх D6, D9, D11; при расхождении приоритет здесь):
- **Слоты:** аватар из набора, рамка аватара, титул под ником, фон профиля. Предмет: `slot`, `rarity`, `media_kind = static | animated`, картинка или анимация (админ грузит через админку в S3), условие открытия `level_required` и/или `price` (одно из двух или оба — тогда можно купить раньше уровня). Косметика анимированная с самого начала; формат анимации — ADR 0046.
- **Покупка:** проводка `CurrencyDebited` и выдача в одной транзакции; `op_id = shop:{user}:{item}`; повтор — та же выдача; не хватает Версов — 409 `progression.insufficient_funds`; уже есть — 409 `progression.already_owned`.
- **Оформление:** по одному предмету на слот, видно в профиле и в матче (ник, аватар, рамка, титул); снятие — пустой слот.
- **Колесо:** заглушка выдачи 5a заменяется реальной выдачей в инвентарь без смены контракта.
- **Своя аватарка:** товар на один раз; загрузка как UGC-изображение через media-pipeline: у UGC-ассета статус `pending | approved | rejected`, модератор решает в существующем разделе «UGC-изображения» админки, media-pipeline публикует `AssetModerated`, progression слушает и применяет; до одобрения виден прежний аватар; отклонение — уведомление с причиной (код причины из конфига); лимиты размера и формата — конфиг.
- **Промокоды:** генерирует админ (награда — предмет или Версы, лимит активаций, срок, «один код на пользователя»); в БД только хеш; погашение идемпотентно, лимит попыток по пользователю и IP; ввод в профиле.
- **Паки:** `pack(items[], verses_amount, style, price_rub в копейках)`; только модель и admin-CRUD; витрина и покупка скрыты при `billing.enabled=false`.
- **Notifications:** каналы in-app (список, непрочитанные, «прочитать все») и realtime (личный канал Centrifugo, connection-токен выдаёт identity); типы 5b — новый уровень, бейдж, задание выполнено, награда недели/сезона, решение модерации по аватарке; маппинг «событие → тип» и шаблоны (ru) — конфиг; отключение по типам. E-mail только организационный (ответ поддержки, рассылка от админа с согласием и отпиской, решения по жалобам в 5c), без почты для заявок и игровых событий. Push — порт `PushSender` и таблица `devices`, реализации нет.
- **Фронтенд:** все цвета, фоны, радиусы, шрифты и иконки через дизайн-токены (CSS-переменные); наборы токенов лежат во фронтенде по ключу темы, bff отдаёт только ключ активной темы (в 5b — настройка `default_theme_key`, в 5d — от события); компоненты без захардкоженных цветов (ADR 0049).

### 9.1. Решения ассистента (фиксируются в handoff)
1. Очередь модерации аватарок — не в progression, а в media-pipeline (решение владельца): статус UGC-ассета, раздел «UGC-изображения» админки, событие `media_pipeline.AssetModerated`; progression и notifications слушают его. Своей очереди и событий `Avatar*` у progression нет; в 5c общая очередь `social` может принять UGC-изображения отдельным ADR.
2. Дубль предмета, выпавший с колеса, превращается в Версы по редкости предмета (сетка `wheel.duplicate_verses` по редкостям в конфиге; бакет `earned`, `op_id = wheel:{user}:{day}:dup`) — подтверждено владельцем.
3. Покупка списывает бакет `earned`; `paid` не трогаем, пока billing выключен.
4. Слоты, редкости, лимиты, цены компенсации — в расширении `progression_config` (версия схемы растёт расширяющим образом); предметы и паки — данные таблиц, редактируются через admin-API.
5. Просроченные предметы (5d) скрывает чтение инвентаря; хранение `expires_at` и `convert_condition` — с 5b.

### 9.2. Шаг 0a — оркестратор (contract-first)
- События: `progression.progression.v1` — `ItemPurchased`, `ItemEquipped`, `PromoRedeemed`; `media_pipeline` — `AssetModerated` (`asset_id`, `owner_user_id`, `purpose`, `status`, `reason_code`, монотонная версия); `AvatarSubmitted/Approved/Rejected` не нужны. Схемы и примеры. Событий notifications нет (терминальный потребитель).
- OpenAPI progression `/v1`: `shop/items`, `shop/items/{id}/purchase`, `shop/packs`, `me/inventory`, `me/appearance` (GET; PUT по слоту), `appearance?user_ids=` (публичное оформление до 12 игроков для матча и профиля), `promo/redeem`, `me/avatar` (GET статус и лимиты; POST заявка, multipart); `/admin/v1`: `items` (CRUD и `PUT items/{id}/media`), `packs`, `promo-codes` (партия, список, `revoke`). media-pipeline: internal `POST /internal/v1/ugc-images` (scope `ugc.image`), `/admin/v1/ugc-images` (настройки, очередь, просмотр, `approve`, `reject`). notifications `/v1`: `notifications` (список, `unread-count`, `read-all`, `{id}/read`), `notification-settings`, `email-subscription`, `unsubscribe` (без входа), `devices`; `/admin/v1/broadcasts`. bff (только GET): `/pages/shop`, `/pages/shell` (`theme.key` и колокольчик), блоки `appearance`, `inventory`, `avatar` в профиле, `appearance` в профиле игрока и в итоге матча (до потока S5 `null`). Заглушки 501, снимок OpenAPI, типы фронтенда (`notifications` добавлен в `gen:api`). Покупки и мутации браузер шлёт прямо в сервисы, bff мутаций не имеет (D4).
- Права identity (миграция `0014`): `progression.items.manage`, `progression.promo.manage`, `media.ugc.moderate` (admin и moderator), `notifications.broadcast.send` (остальные — только admin). Конфиг progression — необязательные секции `shop` (слоты, редкости с Версами за дубль, лимиты медиа предметов, ключ товара «своя аватарка»), `promo` (лимиты попыток и длина кода) и `spend` (`bucket_order`); лимиты и форматы UGC-изображений и коды причин отклонения — конфиг media-pipeline (не progression); конфиг типов уведомлений и шаблонов — `contracts/notifications/notification_types.v1.json` (контрактный тест сверяет поля с схемами событий).
- Хвост tools: `notifications` в `COOKIE_MUTATION_SERVICES`; `gv lint`, `gv contracts check`, `gv gen --check`, `pnpm gen:api`.

### 9.3. Потоки и владение файлами

| Поток | Владеет | Не трогает |
|---|---|---|
| **S1 progression: магазин, инвентарь, оформление, промокоды, паки** | `domain/{shop,inventory,appearance,promo,packs}.py`, `application/{shop,inventory,appearance,promo,packs}/**`, `infrastructure/db/{tables_shop.py,migrations/versions/0005_shop_inventory.py,0006_promo_packs.py}`, `api/public/{shop,inventory,appearance,promo}.py`, замена выдачи колеса | notifications, media-pipeline |
| **S2 notifications** | `services/notifications/**`, миграция `0002`, `contracts/notifications/**` | остальные сервисы |
| **S3 своя аватарка** | `media_pipeline`: приём и проверка изображения, статус `pending/approved/rejected`, `AssetModerated`, миграция, `/admin/v1/ugc-images`; progression: `avatar_requests`, `0007_avatars.py`, consumer `AssetModerated`, `application/avatars/**`, `api/public/avatar.py` | S1 |
| **S4 admin** | `api/admin/**` и `application/admin/**` progression (предметы, паки, промокоды), `services/admin/**` (в том числе раздел «UGC-изображения» поверх admin-API media-pipeline), рассылка в notifications | домен S1 |
| **S5 bff и фронтенд** | `services/bff_web/**`, `frontend/**` | сервисы |

Параллелизация (не больше 2): S1 ∥ S2 → S3 ∥ S4 → S5 → 0b. Слияние: S1 → S2 → S3 → S4 → S5. Один `tdd-guide` и один ревьюер на поток; `security-reviewer` — S1 (траты Версов, промокоды) и S3 (загрузка файлов); `review-migration` — `0005`–`0007`.

### 9.4. S1 — тесты (ledger, повышенная безопасность)

| Что | Тест |
|---|---|
| Покупка: проводка + инвентарь + outbox в одной транзакции под `FOR UPDATE` баланса | integration: сбой на любом шаге откатывает всё |
| Идемпотентность `shop:{user}:{item}` | integration: 20 параллельных покупок → одна проводка, один предмет, остальные получают ту же выдачу |
| `insufficient_funds`, `already_owned`, условие `level_required` | component: 409/409/403, баланс не меняется |
| Предмет без условий открытия | unit: домен/CHECK запрещает |
| Оформление: чужой или неимеющийся предмет, предмет другого слота | component: 403/404/422 |
| Промокод: хеш (HMAC, не sha256 без ключа), лимит активаций, срок, «один на пользователя», гонка последней активации | integration: параллельные погашения не превышают лимит; повтор `(code, user)` — та же награда |
| Перебор кодов | component: лимит попыток по пользователю и IP (Redis, fail-closed), `Retry-After` |
| Паки при `billing.enabled=false` | contract: витрина и покупка скрыты (404), admin-CRUD работает |
| Колесо: косметика → инвентарь; дубль → компенсация | component: `op_id` не двоится |
| Сверка журнала и баланса с дебетами | integration: `reconcile` сходится после покупок |
| Домен конфига `shop`/`promo`/`spend` (S1) | unit: границы в `BOUNDS` (убрать префиксы `shop.\|promo.\|spend.` из `OTHER_STREAMS` в `test_config_bounds.py`), уникальность ключей слотов и редкостей, `earned` в `bucket_order`, слот `custom_avatar_item_key` существует |
| Ответы с данными игрока и админки | contract: `Cache-Control: private, no-store` на успешных ответах (хвост 5a: заголовок ставится после выполнения) |
| Мутации без ключа и лишние поля | component: без `Idempotency-Key` и без `reason` — 422; лишнее поле в теле — 422 (заглушки 0a это уже проверяют) |

### 9.5. S2 — notifications

| Что | Тест |
|---|---|
| Consumers `ProgressionLevelChanged`, `BadgeUnlocked`, `QuestCompleted`, награды недели/сезона, `media_pipeline.AssetModerated` (только `purpose = avatar`); маппинг и шаблоны из конфига | component: событие → уведомление; неизвестное событие игнорируется; дубль `event_id` и дубль предмета → одно уведомление |
| Realtime в личный канал Centrifugo; недоступность Centrifugo не теряет запись | component: уведомление в БД есть, доставка догоняется |
| Настройки: отключение по типу (in-app и e-mail), «прочитано» только владельцем | component: чужое → 404, мутации идемпотентны |
| E-mail организационный: рассылка админа только с согласием, отписка по подписанной ссылке | component: без согласия не уходит |
| Push: `PushSender` порт, `NullPushSender`, `devices` | unit/component |
| Шаблоны ru (+ каркас локализации) | unit: нет шаблона на тип → ошибка при старте конфига |

### 9.6. S3 — аватарка

- Покупка товара `custom_avatar` (одна на пользователя, `UNIQUE`); заявка `POST /v1/me/avatar` (multipart, `Idempotency-Key`): progression передаёт файл media-pipeline (internal, scope `ugc.image`), тот проверяет сигнатуру (не `Content-Type`), размер, размеры, пересжимает, убирает EXIF, кладёт в S3 и создаёт UGC-ассет `pending`; progression хранит `avatar_requests(user, asset_id)`.
- Решение модератора в разделе «UGC-изображения» (право `media.ugc.moderate`, причина из списка кодов, аудит) → `AssetModerated`. Progression: `approved` → аватар обновляется (идемпотентно по версии события); `rejected` → прежний аватар остаётся; notifications шлют уведомление с причиной. Новая заявка отменяет предыдущую `pending`.
- Тесты: подмена типа файла, превышение лимитов (конфиг), заявка без покупки (403), повтор заявки (идемпотентно), старый аватар виден до одобрения, ключ объекта UGC недоступен без ссылки; `AssetModerated` дважды и вне порядка (по версии) — один эффект; событие чужого `purpose` игнорируется; решение без права — 403.

### 9.7. S4/S5 и шаг 0b
- S4: админ загружает картинку или анимацию предмета (по образцу загрузки исходников media-pipeline), редактирует слот, редкость, условия, цену; генерирует промокоды (показ кода один раз), создаёт паки, шлёт рассылку; раздел «UGC-изображения» — очередь, одобрить, отклонить с кодом причины (аудит).
- S5: токены и тема (наборы токенов лежат во фронтенде по ключу темы, bff отдаёт только ключ), магазин (анимированные предметы по `media_kind`, `prefers-reduced-motion` останавливает анимацию), инвентарь, оформление, ввод промокода в профиле, колокольчик и настройки уведомлений, ник + аватар + рамка + титул в матче.
- 0b: сиды предметов (в том числе анимированных) и шаблонов уведомлений; `e2e/shop.spec.ts` и `e2e/notifications.spec.ts`; проверка на живом compose — у владельца.

### 9.8. Обязательные проверки безопасности 5b
Применимо к S1 (траты Версов — ledger), промокодам и S3: схема §11 плюс — подмена цены или награды клиентом (цена и награда только с сервера), перебор промокодов, повторное погашение, загрузка файлов (сигнатура, размер, декомпрессионные бомбы), IDOR на инвентарь и оформление, XSS в нике и титуле (экранирование на фронте), миграции `0005`–`0007` расширяющие. Процедура: `security-reviewer`, `review-migration`, `idempotency-and-side-effects`; в отчёте — **«требуется ручная проверка владельцем»**.

## 10. Модель 5c (без шагов)

- **Друзья:** заявки по нику/коду, блокировки, онлайн-статус, приглашение в комнату из лобби; список виден только владельцу; настройка «кто может звать» (все/никто).
- **Комментарии** к сборникам через `gv_text.ugc`; к профилям нет.
- **Жалобы:** на игрока (матч, история; не соло), сборник, комментарий; порог ставит в очередь модератора.
- **Санкции:** `RANKED` (есть), `CHAT`, `ONLINE`, `ACCOUNT` — со сроком, только вручную модератором; событие `UserSanctionsChanged`.
- **Приватность:** профиль и статистика публичны по умолчанию (можно скрыть), история матчей только владельцу.

### 10.1. Детализация 5c (решения владельца 2026-10-24; при расхождении с §10 приоритет здесь)

Решения: [ADR 0050](../adr/0050-friends-and-blocks.md), [0051](../adr/0051-reports-and-moderation-queue.md), [0052](../adr/0052-sanctions-and-enforcement.md) (черновики). Ветка `stage5c/integration`, потоки — ветки от неё, один `tdd-guide` на поток, не более 2 параллельно.

**Находки в коде.** `PlayerForfeited` нет — счётчики из `MatchFinished.players[].finish`; жалоба из матча уже есть как `PlayerRated(kind=report)`; в claim есть `RANKED=1, CUSTOM_CONTENT=2, CHAT_SLOW=4, CHAT_MUTE=8, COMMENTS=16`, `ONLINE=32` добавляется; Nginx JWT не разбирает — санкции применяют identity, match, social, не gateway; общего presence нет — живёт в social (Redis).

**Данные social** (миграции `0002_social_friends`, `0003_social_moderation`, расширяющие): `friend_requests`, `friendships`, `friend_codes`, `blocks`, `privacy_settings`, `comments`, `collections_proj`, `reports`, `moderation_cases`, `case_reports`, `match_participants_proj`, `in_match_proj`, `sanctions`, `behaviour_counters`, `users_proj`, idempotency, outbox, inbox.

**События `social.social.v1`:** `FriendRequested`, `FriendAdded`, `FriendRemoved`, `UserBlocked`, `RoomInviteSent`, `CommentPosted`, `CommentDeleted`, `ReportFiled`, `ReportResolved`, `ContentModerated`, `UserSanctionsChanged`. Потребители: notifications (5 типов), identity и match (`UserSanctionsChanged`), collections и media-pipeline (`ContentModerated`), progression (`FriendAdded`, `CommentPosted` — опционально). social слушает `MatchStarted`, `MatchFinished`, `PlayerRated`, `collections.*`, `identity.UserDeleted`.

| Поток | Состав | Ключевые тесты |
|---|---|---|
| 0a | контракты, OpenAPI `/v1` и `/admin/v1`, блоки bff, права identity, `ONLINE=32`, типы notifications, realtime `room_invite`, заглушки 501, `COOKIE_MUTATION_SERVICES` | contract на примеры, `gv lint`, `gv contracts check`, `gv gen --check` |
| F1 | друзья, блокировки, приватность, коды, presence, приглашения | блок рвёт дружбу, встречные заявки, гонка accept, лимиты, 403 приглашений |
| F2 | комментарии, жалобы, порог, consumers | правка >5 мин 409, права удаления, повтор жалобы no-op, соло/без матча 422, порог один раз |
| F3 | санкции: social, identity, match, `libs/auth` (`security-reviewer`, `review-migration`) | истёкшая не в маске, ACCOUNT → 403 и отзыв сессий, ONLINE → 403 и выбивание, порядок по `version`, не себе |
| F4 | admin: очередь, санкции, карточка игрока | 403 без права, идемпотентность, повторный захват 409 |
| F5 | bff и frontend | vitest, Playwright на моках |
| 0b | сиды, `friends.spec.ts`, `moderation.spec.ts` | живой стек у владельца |

Порядок: F1 ∥ F2 → F3 ∥ F4 → F5 → 0b. Не входит: автосанкции, e-mail/push по социальным типам, комментарии к профилям, «частые соперники», 5d.

## 11. Обязательные проверки безопасности

Применимо к P1 и admin-ручкам P3 (progression = ledger, [docs/10-ai-assistant.md](../10-ai-assistant.md) §3).

| Риск | Мера | Тест |
|---|---|---|
| Двойное начисление (повтор, новый `event_id`, дейлик + `GameFinished`) | `UNIQUE(op_id)` из предмета поверх inbox | integration: одно событие дважды и с разными `event_id` → одна проводка |
| Гонка спина | PK `(user_id, day)`, `INSERT … ON CONFLICT`, ответ из записи | integration: 20 параллельных спинов |
| Обход потолка | счётчик под блокировкой строки в той же транзакции, день по `finished_at` | property + component: параллельные сессии около полуночи |
| Баланс ниже нуля | `CHECK` + `FOR UPDATE` | integration: сторно больше баланса → 409 |
| Ручная выдача админом | право, причина, лимит операции и суток, `Idempotency-Key`, аудит, `actor_id` | component: 403 / no-op / 422 |
| Подмена буста или сектора клиентом | пустое тело спина, буст только на сервере | contract |
| Расхождение журнала и баланса | периодическая сверка и алерт | integration |
| UPDATE/DELETE журнала | нет прав у роли сервиса + триггер | integration |

Процедура: `security-reviewer` (P1, P3), `review-migration` на `0002` и `0003`, `idempotency-and-side-effects`. В отчёте этапа — **«требуется ручная проверка владельцем»**.

## 12. Модель 5d (идеи владельца 2026-10-23, не реализуются в 5b)

Подэтап 5d идёт после 5c (как хеллоуин в Songless). Модель 5b ему не мешает: нужные поля закладываются сразу, логика — позже.

- **Временный предмет.** `inventory.expires_at` и `inventory.convert_condition` (jsonb, условие перевода в постоянный, например «любая покупка пака»). В 5b поля есть и всегда `NULL`; чтение инвентаря уже отфильтровывает просроченные (`expires_at IS NULL OR expires_at > now()`).
- **Сезонное событие.** Событие с периодом и темой оформления (ADR 0049: у события есть `theme_key`; наборы токенов уже во фронтенде, bff отдаёт ключ активной темы от события). Цепочка из N находок (7); каждая открывается действием игрока (дейлик, лидерборд, сборники, профиль) через события сервисов. Экран-карта с прогрессом и дневником. Награда — временный предмет события, постоянный после пополнения (покупка пака).
- **Что 5b готовит:** `items.event_id` (nullable, без внешнего ключа), `source` инвентаря допускает `event`, bff отдаёт `theme.key` (в 5b — настройка `default_theme_key`, в 5d — от события), типы уведомлений и шаблоны — конфиг (новые типы без кода).
- **Чего 5b не делает:** сущностей события, находок, карты, логики перевода в постоянный.

### 12.1. Детализация 5d (решения владельца 2026-10-24; при расхождении с §12 приоритет здесь)

ADR [0054](../adr/0054-seasonal-events-model.md) (модель события и запусков), [0055](../adr/0055-event-find-triggers.md) (триггеры находок), [0056](../adr/0056-event-rewards-via-ledger.md) (награды). Временные предметы §12 в 5d не используются.

**Модель.** `events` (определение: key, тело JSON по `contracts/progression/event_definition.v1.json`, картинка карты в S3) и `event_runs` (запуск: период, статус `draft|published|active|finished|stopped`, неизменяемый снимок определения с `body_sha256` на публикации). Статусы `active`/`finished` материализует задача relay `progression.events.tick`; чтение считает статус по времени. Один активный запуск: при публикации advisory-lock и проверка пересечения периодов (409 `progression.event_period_overlap`). Перезапуск = новый `event_runs` с теми же находками (новое определение не нужно).
**Прогресс.** `event_finds_progress(run_id, user_id, find_key, period_key, progress, unlocked_at)`; `event_completions(run_id, user_id, completed_at)`. Награды находки — проводки ledger `event:{run}:{find}:{user}`; главная награда — предмет `source=event` при закрытии всех находок (`event:{run}:complete:{user}`).
**Триггеры.** `server` — метрика (`rounds_played`, `correct_answers`, `wins`, `online_matches`, `daily_completed`, `daily_streak`, `collection_published` + новые `duel_win`, `weekly_top_place` (≤ `params.place_max`), `wheel_spin_streak`, `item_equipped` (`params.slot|item_key`), `collection_played` (сессия по сборнику, `collection_id != null`; фильтра по тегу нет — в `GameFinished` тегов нет, хвост в backlog)), `target`, период `day|event`; засчитывается только по событиям сервисов. `ui_spot` — `page_key` ∈ {home, leaderboards, profile, collections, match_summary, shop, wheel}; `POST /v1/events/{key}/finds/{find}/claim` (идемпотентно, лимит на игрока fail-closed). Финальная находка (`final`) закрыта до остальных (409 `progression.event_find_locked`).
**API.** Публичное `/v1/events/active` (анонимно: key, название, тема, период), `/v1/events/{key}` (страница игрока), `/v1/me/events` (архив), `claim`. Админ `/admin/v1/events` (CRUD определения, картинка карты, запуски, публикация, остановка, статистика); права `progression.events.manage` (определение, запуски, статистика), `progression.events.publish` (публикация и остановка, только admin).
**События** `progression.progression.v1`: `EventRunStarted`, `FindUnlocked`, `EventCompleted`, `EventRunFinished`. **bff**: `theme.key` от активного запуска (иначе `default_theme_key`), блок `event` на `/pages/home` и `/pages/shell`, `/pages/events/{key}`, архив в `/pages/profile`. **notifications**: `event_started` (рассылка в приложении), `find_unlocked`, `event_completed`; без e-mail, отключаемые.
**Потоки.** 0a (контракты, заглушки) → параллельно E1 (progression), E3 (bff + фронтенд на моках, тема winter), 0b (сиды, `gv dev seed`) → E2 (notifications, админка, `e2e/events.spec.ts`).

## 13. Миграции и совместимость

- `0002_progression_core` (P1), `0003_quests_wheel` (P2) — только расширяющие, без переименований; `review-migration` до коммита.
- Миграция прав identity (0a) — расширяющая.
- Новое событие rating и блоки bff необязательные: 4b не ломается; потребители игнорируют неизвестные поля.

## 14. Что НЕ входит в 5a

Магазин, инвентарь, паки, промокоды, notifications (5b); social, жалобы, санкции, приватность (5c); лидерборды уровней; push-уведомления; мастерство по персоне; продажа за деньги и включение billing; выдача косметики (в 5a заглушка).

## 15. Риски

| Риск | Что делаем |
|---|---|
| Двойное начисление при дейлике (публикуются `GameFinished` и `DailyCompleted`) | R2: награда только по `DailyCompleted` |
| Нет мест игроков в `SeasonFinished` | Новое `SeasonStandingsFinalized` (§2.2) |
| Топ недели — топ-100 | Решение владельца (D5): разряды 101–1000 только у сезона |
| Фарм опыта вторым аккаунтом | R5: `min_rounds_ratio`, 0 за выход; детектор win-trading — отдельная задача (хвост 4b) |
| Рост `xp_ledger` | Индексы по `(user_id, created_at)`, партиционирование — по нагрузке на этапе 7 |
| Колесо воспринимается как лутбокс | ADR 0044: не продаётся, шансы видны |
| Смена конфига «посреди суток» | R8: действует со следующего периода |

## 16. Порядок слияния

1. PR #25 (Next 16.4.1 и docs) → `main`.
2. Ветка плана: `docs/plans/stage-5.md`, ADR 0042–0044.
3. 0a (контракты) → P1 → P2 → P3 → P4 → 0b. Каждый поток: ревью, `gv lint`, `gv test <service>`; P1 и admin-ручки P3 — `security-reviewer`.
4. После слияния 5a: живая демонстрация §0 на compose у владельца, ручная проверка ledger, затем планирование 5b по §9.

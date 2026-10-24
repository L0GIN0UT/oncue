# Бэклог

Небольшие доработки и идеи, не вошедшие в этап. Запись удаляется, когда пункт сделан. Источник открытых хвостов — разделы «Хвосты» [roadmap](09-roadmap.md).

## 1. Крупные эпики до релиза

### Рефакторинг фронтенда
Обязательный эпик **после полного функционала и до этапа 7**. Состав:
- токены и тема до конца: свести все компоненты к токенам, убрать остатки литералов вне `src/styles`;
- единые компоненты (кнопки, формы, карточки, модальные окна, таблицы) вместо локальных копий;
- состояние: единый подход к серверному и клиентскому состоянию, устранение дублей запросов;
- доступность (a11y): фокус, клавиатура, aria, контраст AA во всех темах;
- производительность: размер бандла, ленивые экраны, изображения, Core Web Vitals;
- светлая тема (проверка и доводка, смена темы в рантайме).

### Проход по техдолгу
Все открытые пункты из разделов «Хвосты» roadmap и бэклога 5b одной таблицей. Закрытые (✅) здесь не повторяются.

| Пункт | Где | Источник | Когда |
|---|---|---|---|
| Живой прогон CI: `nginx -t`, integration OpenSearch (проверено CI #18) и демонстрация §0 этапа 1 на compose | весь стек | [хвосты 1](09-roadmap.md#хвосты-этапа-1) | До релиза |
| Общий подключаемый inbox для Django (у admin свой) | `libs/messaging`, admin | [хвосты 1](09-roadmap.md#хвосты-этапа-1) | Этап 5 (в 5c — решить) |
| Зеркало образов под своим контролем | `infra/` | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | Этап 7 |
| trivy: вернуть блокировку по CRITICAL и SARIF после digest-пиннинга | `.github/workflows` | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | Этап 7 |
| Подтвердить правку эталона `libs/text` (союз «&») | `libs/text`, `libs/game_kernel` | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | Решение владельца |
| Журнал `link_issuance` чистится внутри `IssueAssetLink`, а не задачей | media_pipeline | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | По желанию |
| `ILIKE` без trigram-индекса в `/admin/v1/projection/items` | game | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | При росте каталога |
| Ссылка финального кадра ревила живёт 60 с | game, frontend | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | По желанию |
| Обновить env окружений под новые лимиты сервисных токенов | identity, `infra/` | [хвосты 2](09-roadmap.md#хвосты-этапа-2) | При выкладке |
| Переопределения `clue` и `answer.format` в лобби не поддержаны `round-plans` | match, game | [хвосты 4a](09-roadmap.md#хвосты-4a) | После решения по плану раундов |
| `party_speed` на поздних уровнях даёт почти полные очки | `libs/game_kernel` | [хвосты 4a](09-roadmap.md#хвосты-4a) | Геймдизайн |
| Блокирующий `XREAD` на комнату (~300 соединений Redis на инстанс) | match | [хвосты 4a](09-roadmap.md#хвосты-4a) | Нагрузка, этап 7 |
| Окно отзыва connection-токена Centrifugo до 10 мин | identity, match | [хвосты 4a](09-roadmap.md#хвосты-4a) | Этап 7 |
| Пакетные ручки catalog/library для итога матча | bff-web, catalog, library | [хвосты 4a](09-roadmap.md#хвосты-4a) | При росте раундов |
| Тег образа Centrifugo (`v6`) не закреплён | `infra/compose` | [хвосты 4a](09-roadmap.md#хвосты-4a) | Этап 7 |
| Исключённый игрок переподписывается на канал комнаты старым токеном | match | [хвосты 4a](09-roadmap.md#хвосты-4a) | **5c (F3)**, вместе с санкцией `ONLINE` |
| Одинаковые HMAC-секреты Centrifugo ломают разделение D9: нет проверки при старте | `infra`, `tools/gv` | [хвосты 4a](09-roadmap.md#хвосты-4a) | Preflight до этапа 7 |
| Win-trading: детектор | rating, match | [хвосты 4b](09-roadmap.md), ADR 0038 | После 5c (в 5c только счётчики) |
| Допущения сидов: K Эло 40/20, пороги званий, фильтры `similar` | `seeds/rating`, `seeds/game` | [хвосты 4b](09-roadmap.md) | Подтверждение владельцем |
| Срыв отсчёта без штрафа: сталкинг очереди | match | [хвосты 4b](09-roadmap.md) | По данным |
| Пустое лобби висит до `room_idle_close_min` при недоступной шине | match | [хвосты 4b](09-roadmap.md) | Этап 7 |
| Presence и join/leave в Centrifugo `room` выключены: при включении скрыть наблюдателей | `infra/centrifugo` | [хвосты 4b](09-roadmap.md) | При включении |
| `Cache-Control: no-store` в admin-ручках progression: проверить на успешных ответах | progression | [хвосты 5a](09-roadmap.md#хвосты-5a) | Открыт |
| Тест тай-брейка мест сезона против живого Redis (`ZREVRANGE`) | rating, progression | [хвосты 5a](09-roadmap.md#хвосты-5a) | Открыт |
| Нестабильные тесты: `GameScreen.modes`, `CollectionEditor` (автосохранение), `party.spec` | frontend, e2e | [хвосты 5a](09-roadmap.md#хвосты-5a) | Открыт |
| Windows: ffmpeg-тесты `media_pipeline`, `admin test_runtime` (зелёные в CI) | media_pipeline, admin | [хвосты 5a](09-roadmap.md#хвосты-5a) | Не чинить |
| Разделение ролей Postgres для журналов progression (REVOKE) | progression | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | Этап 7 |
| Инкрементальная сверка журнала progression и партиционирование | progression | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | Этап 7 |
| Ретенция `metric_marks` / `quest_progress` | progression | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | Этап 7 |
| Admin-API включения и выключения типов уведомлений | notifications | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | После 5b |
| Вынос `SmtpEmailSender`/`LogEmailSender`/`BackgroundEmailSender` в `libs/` | identity, notifications | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | После 5b |
| `client_max_body_size 2m` на gateway ограничивает загрузку аватарки | gateway, media_pipeline | [хвосты 5](09-roadmap.md#хвосты-этапа-5) | Следить |
| GET предмета по id в admin-API (админка листает до 20 страниц) | progression, admin | 5b | По желанию |
| GET карточки UGC-изображения по id в admin-API | media_pipeline, admin | 5b | По желанию |
| Число получателей в подтверждении рассылки (`GET /admin/v1/broadcasts/audience`) | notifications, admin | 5b | По желанию |
| Своя аватарка в `me/appearance`: убрать дубль `PlayerAppearance.custom_avatar_url` одним изменением контракта | progression, bff | 5b | По желанию |
| Название сборника в `collections.CollectionPublished/Updated` (необязательное `title` ≤120): нужно social для `CommentPosted.collection_title` (сейчас пустая строка, текст уведомления без названия) | collections, social | 5c |
| Ник в `/pages/players/{id}`: `PlayerBadge` показывает «Игрок» | bff, progression, frontend | 5b | 5c (F5), если ник даёт social/identity |
| Жалоба на аватарку чужого игрока: в `PlayerAppearance` (progression) нужен необязательный `asset_id` одобренного ассета, bff пробросит в `AvatarCard`, фронтенд подключит `ReportButton kind="avatar"` | progression, bff, frontend | 5c |
| Кнопка жалобы в истории матчей: у карточки матча нет списка игроков (жалоба доступна в итоге матча) | bff, frontend | 5c |
| Страница для уже открытой сессии забаненного `account` (сейчас только отказ на входе) | frontend, identity | 5c |
| Точный флаг «можно добавить в друзья» в итоге матча (сейчас кнопка при `can_report`) | bff | 5c |
| Общий `adminContext` в e2e (`e2e/collections.spec.ts` дублирует `e2e/admin.ts`) | e2e | 5b | По желанию |
| Content-Type one-click отписки (RFC 8058: `application/x-www-form-urlencoded`) | notifications | 5b | По желанию |
| Анонимное чтение `p/*` в S3 (принятый риск): перейти на presigned-ссылки gateway | media_pipeline | 5b | Этап 7 |
| Цвет аватарки: 16-битные и CMYK | media_pipeline | 5b | По желанию |
| Окна лимитов social (`lookup_window_s`, `invite_window_s`) в коде, не в конфиге — перенести новой версией схемы конфига | social | 5c | По желанию |
| Disconnect Centrifugo для забаненного (ACCOUNT): открытое соединение живёт до `exp` connection-токена; нужен server API `disconnect` в identity | identity | [ADR 0052](adr/0052-sanctions-and-enforcement.md) | После 5c |
| Алерт на лаг consumer `social.social.v1` в match: проекция санкций отстаёт от токена identity — санкция действует позже | match, observability | ADR 0052 | До релиза |
| Иерархия модераторов: сейчас модератор с `social.sanctions.manage` может санкционировать admin; запрет санкций выше своей роли | social, identity | ADR 0052 (решение владельца) | По решению владельца |
| Лаг идентичности: решение о доступе по claim токена (≤ 15 мин) и проекциям разных сервисов расходится на время доставки событий | identity, match, social | ADR 0052 | Принятый риск |

## 2. Продуктовые идеи владельца

- **Каталог сборников как в Songless.** Фильтры: по языку, диапазону числа единиц, автору, «только мои», по исполнителям. Вкладки «топ за сутки» и «по рейтингу». Сейчас есть: домен, режим, сложность, кураторские; сортировки popular, new, likes, difficulty.
- **Свои картинки в сборниках** через UGC-модерацию из 5b (отложено из 3b).
- **Сезонные события** — подэтап 5d ([план §12](plans/stage-5.md)).
- **Временные предметы** (`expires_at`, перевод в постоянные) — вместе с 5d.
- **Оплата через готового провайдера** (ЮKassa или Robokassa) — этап 6; свой биллинг не пишем.
- **Без гейтов функций по уровню:** уровень открывает только косметику и титулы (D2).

## 3. Открытые ручные проверки владельцем

Подробные чек-листы — в [handoff этапа 5](handoff/stage-5.md); к ним добавляется чек-лист 5c.

- **4a:** identity (realtime-токен), match (античит).
- **4b:** identity, match, rating; допущения сидов (K Эло 40/20, пороги званий, фильтры `similar`).
- **5a:** ledger progression — роли Postgres, миграции `0002`–`0004`, начисления и потолки, конфиг и публикация, ручная выдача и сторно, награды сезона и недели; живая проверка `e2e/progression.spec.ts`, `/profile`, колесо, админка «Прогрессия»; смена пароля сид-админа локального стенда.
- **5b:** живой стек (`shop`, `notifications`, `progression` e2e); ledger (траты Версов, миграции `0005`–`0007`); промокоды (HMAC, потолки эмиссии); загрузка файлов (аватарка, предметы, `/ugc/`, `/media/p/`, `/cosmetics/`); identity `POST /internal/v1/users/emails`; отписка без входа; рассылка (письма только подписавшимся); тема (смена в рантайме).

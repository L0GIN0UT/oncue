# Roadmap

> Статус: черновик. Сроки не указаны: этапы идут последовательно, каждый завершается рабочей демонстрацией.

## Этап 0. Фундамент — ✅ выполнен (2026-10-08)
- Монорепо, `uv` workspace, `tools/gv` (gen, new-service, test, lint, dev, contracts, ci), шаблон сервиса с `CLAUDE.md`.
- Реестр `infra/services.toml` → генерация compose, Nginx, Prometheus, `.importlinter`, скрипта БД (`gv gen --check` в CI).
- `libs/core`, `libs/db`, `libs/messaging` (outbox/inbox, relay с лидерством, ретраи, DLQ, Redpanda, RabbitMQ), `libs/auth`, `libs/observability`, `libs/service`, `libs/flags`, `libs/testkit`.
- 15 Python-сервисов-заглушек (health, метрики, Problem Details, процессы api/relay/consumer/worker/all/migrate, миграция outbox/inbox) + Nginx-gateway.
- `infra/compose`: инфраструктура, сервисы, gateway, observability (Prometheus, Grafana, Tempo, ELK).
- Контракты: снимок OpenAPI и конверта событий, `gv contracts check` (актуальность + ломающие изменения).
- CI (GitHub Actions): lint, тесты затронутых модулей с Postgres/Redis, контракты, сборка документации, образы + Trivy, pip-audit, gitleaks; ночной полный прогон; dependabot.
- `mkdocs` + `mkdocstrings`: сайт документации и справочник libs из докстрингов.
- Security-ревью каркаса: исправлены троттлинг JWKS, маскировка секретов в логах, безопасное окружение по умолчанию, скрытие параметров SQL, закрытие `/v1/internal|admin` на gateway, X-Forwarded-For, request-id у ответов 500.
- **Проверено:** unit, integration на реальных Postgres/Redis/RabbitMQ, component, contract; ruff, mypy --strict, import-linter (33 контракта), `docker compose config`, `nginx -t` и живой прогон маршрутов gateway.
- **Осталось проверить на машине разработчика / в первом прогоне CI:** `gv dev up services` целиком (сборка образов и запуск контейнеров) и интеграционный тест адаптера Redpanda — в облачной среде, где собирался этап 0, не было доступа к Docker Hub и Kafka.

## Этап 1. Аккаунт и каталог — ✅ backend выполнен (2026-10-10), живая демонстрация не проведена
План: [plans/stage-1.md](plans/stage-1.md). Решения: ADR [0012](adr/0012-signed-asset-links-nginx.md)–[0015](adr/0015-admin-idempotency-and-asset-event-order.md).

- **edge:** `/metrics` на отдельном порту 9100 у всех процессов; gateway с TLS (443, HSTS, 80 → 301), `/media/` через `secure_link` в приватное S3-хранилище (MinIO заменён на SeaweedFS — [ADR 0021](adr/0021-s3-storage-seaweedfs.md)); CSRF/Origin для cookie-аутентификации в `libs/auth`.
- **identity:** регистрация (никнейм + e-mail + пароль), вход по e-mail или никнейму, сессии с ротацией refresh, JWT/JWKS, роли и права, согласия, привязка Яндекс ID и вход через привязанный аккаунт, admin-сессии и admin-API пользователей и ролей.
- **catalog:** MediaItem (music + film), импорт (файл, TMDB, MusicBrainz, Spotify, Яндекс Музыка — внешние выключены по умолчанию), нормализация и алиасы, OpenSearch с fallback на pg_trgm, автокомплит, admin-API.
- **media-pipeline:** `audio_intro`, `frame_progressive`, `poster_masked`; подписанные ссылки (ADR 0012), admin-загрузка исходников и перегенерация. Вместо CDN — `proxy_cache` на gateway (CDN — этап 7).
- **admin:** Django 5.2, вход через identity, синхронизация прав, разделы «Каталог» и «Пользователи», аудит (append-only).
- Исправления по ревью: идемпотентность admin-мутаций в транзакции мутации и порядок событий ассетов ([ADR 0015](adr/0015-admin-idempotency-and-asset-event-order.md)), лимиты входа по аккаунту и подсети, повторная аутентификация привязок, отзыв сессий при понижении прав.
- **Проверено:** unit, integration, component, contract по сервисам этапа; `gv lint`, `gv contracts check`, `gv gen --check`.
- **Осталось (демонстрация [§0 плана](plans/stage-1.md#0-цель-и-критерии-готовности)):** живой сквозной прогон пунктов 1–6 на `gv dev up services` (регистрация → вход → импорт → `AssetsReady` → ссылка через gateway → блокировка) не выполнялся. Фронтенд и bff-web в этап 1 не входят — демонстрация идёт через API и админку.

### Хвосты этапа 1
| Пункт | Где | Статус |
|---|---|---|
| Периодическая очистка `idempotency_keys` | identity, catalog, media_pipeline | ✅ этап 2: встроенная задача `idempotency.purge` в `libs/service` |
| Закрытие ресурсов процесса для consumer/worker | `libs/service` | ✅ этап 2: `Infrastructure.add_closer` |
| Сервисные токены для internal API (D8) | identity, media_pipeline, `libs/auth` | ✅ этап 2: [ADR 0016](adr/0016-service-tokens-client-credentials.md) |
| Живой прогон integration-тестов OpenSearch и `nginx -t` на образе nginx 1.28 в CI | `.github/workflows`, catalog, `infra/nginx` | Открыт — перенесён в хвосты этапа 2 |
| `mypy_django_plugin` для admin | admin | ✅ этап 2 |
| Протокол вместо `EventProcessor` в `KafkaEventConsumer` | `libs/messaging` | ✅ этап 2: `EventProcessorPort`, admin без `cast` |
| Общий подключаемый inbox для Django (сейчас свой в admin) | `libs/messaging`, admin | Этап 5 (модерация в админке) |
| Ручная security-проверка владельцем: identity, `libs/auth`, вход в admin | — | Открыт, до публичного запуска |

**Принятый риск:** лимитеры входа и denylist `sid` при отказе Redis работают **fail-open** (с метрикой и логом): перебор сдерживает argon2id с ограниченным параллелизмом, окно отозванного access-токена ≤ access TTL. Исключение — лимит выдачи ссылок media-pipeline: fail-closed (503). Подробнее — [architecture/security.md](architecture/security.md#7-реализация-этапа-1).

## Этап 2. Соло-игра — ✅ реализован в ветке `stage2/integration` (2026-10-11), на проверке владельцем; живая демонстрация не проведена
План: [plans/stage-2.md](plans/stage-2.md). Решения: ADR [0016](adr/0016-service-tokens-client-credentials.md)–[0020](adr/0020-mode-config-assets-by-params-hash.md).

- **platform:** сервисные токены client credentials (identity, `libs/auth`, `libs/service`) — game → media_pipeline, admin → identity; `PeriodicJob` в relay под advisory lock, встроенная очистка outbox/inbox и `idempotency_keys`; `add_closer`; порт `Random` в `libs/core`.
- **`libs/game_kernel`:** модели `ModeConfig` по `contracts/modes`, реестр и валидатор конфига (ошибки с JSON-путём), правила раунда, фасад `ModeEngine`; плагины `audio_intro`, `frame_progressive`, `poster_masked`, `daily`, `random`, `title_alias`, `artist`, `by_level`, `by_speed` ([02-game-engine §5.1](02-game-engine.md#51-плагины-ядра-этапа-2)); `params_hash` с golden-векторами. **`libs/text`:** нормализация названий вынесена из catalog, замены союзов и Дамерау-Левенштейн.
- **game:** конфиги режимов (черновик, публикация, откат, архив, проверка пула), наборы дня с курацией, соло-сессии в Postgres, дейлик и случайный режим с пресетами, ревил, итог и шэр, статистика угадавших, проекции каталога и ассетов, античит, admin-API, периодические задачи; сиды четырёх режимов.
- **identity:** гостевые сессии ([ADR 0018](adr/0018-guest-sessions-in-identity.md)), права `config.modes.*` и `game.daily.*`.
- **bff-web:** экраны session, home, solo, results — только чтение, деградация по блокам ([ADR 0017](adr/0017-frontend-nextjs-bff-read-only.md)).
- **frontend:** Next.js — главная, соло-хаб, игровой экран, ревил, итог и шэр, вход и регистрация; CSP с nonce, безопасный `next`, single-flight refresh; Playwright на моках.
- **admin:** редактор конфигов режимов (JSON/YAML, ошибки с путём, diff, публикация, откат) и календарь наборов дня; сервисный токен для входа.
- **CI:** job `frontend`, job `e2e-smoke` (compose + `e2e/daily.spec.ts`), `nginx -t`.
- **Проверено:** unit, integration, component, contract по затронутым модулям; `gv lint`, `gv contracts check`, `gv gen --check`; во `frontend/` — lint, typecheck, test, build, Playwright на моках.

### Хвосты этапа 2
| Пункт | Где | Когда |
|---|---|---|
| Живая демонстрация [§0 плана](plans/stage-2.md#0-цель-и-критерии-готовности) на compose не проводилась (в среде разработки нет Docker) | весь стек | До слияния в `main` |
| Засев стека для e2e «дейлик»: `gv dev seed` грузит каталог, исходники медиа и ассеты, наборы дня через admin-API (admin-токен — через bootstrap-админа identity, только local/ci). В CI шаг засева поднимает `E2E_SEEDED=1`, `e2e/daily.spec.ts` раскрывается | `tools/gv`, `.github/workflows`, `seeds/`, identity | ✅ реализовано (проверка — прогон CI) |
| Integration-тесты Kafka и OpenSearch и `nginx -t` на образе nginx 1.28 — только в CI, локально не прогонялись | `.github/workflows`, `infra/nginx`, catalog, `libs/messaging` | ✅ прогон CI #18: зелёные |
| Зеркало образов под своим контролем: внешние реестры удаляют образы (MinIO, сентябрь 2026 — [ADR 0021](adr/0021-s3-storage-seaweedfs.md)) | `infra/` | Этап 7 |
| Скан образов trivy: вернуть блокировку по CRITICAL и выгрузку SARIF в GitHub Security после digest-пиннинга базового образа и Dependabot на него ([ADR 0022](adr/0022-image-scan-reporting-not-gating.md)) | `.github/workflows` | Этап 7 |
| Ручная проверка владельцем: identity (гостевые сессии, сервисные токены), `libs/auth` | — | До слияния в `main` |
| Подтвердить правку эталона `libs/text`: союз «&» теперь удаляется (`("&", "")`) при сверке ответов | `libs/text`, `libs/game_kernel` | До слияния в `main` |
| Журнал `link_issuance` чистится внутри `IssueAssetLink` (не чаще раза в 10 мин), а не периодической задачей | media_pipeline | Этап 3 |
| Поиск кандидатов по проекции game (`/admin/v1/projection/items`) — `ILIKE` без trigram-индекса | game | При росте каталога |
| Ссылка финального кадра ревила живёт 60 с и может истечь, если ревил открыт поздно | game, frontend | Этап 3 |
| Лимит сервисных токенов разделён: `GV_SERVICE_TOKEN_LIMIT__PER_CLIENT_PER_MIN` из плана заменён на `FAILURES_PER_CLIENT_IP_PER_MIN` и `SUCCESS_PER_CLIENT_PER_MIN` — обновить env окружений | identity, `infra/` | При выкладке |

## Этап 3. Сборники и медиатека — ✅ выполнен (2026-10-22), влит в `main` (PR #16)
Локальный прогон владельцем на compose: `up` + засев + смоук + e2e 21/21 зелёные.

Владелец разделил этап (2026-10-16): **3a «Режимы и загадки»** — новые ClueType, марафон, больше/меньше, топ-32, `ClueAssetsRequested` ([план](plans/stage-3a.md), ADR 0023–0027, шаг 0a — контракты); **3b «Сборники и медиатека»** — collections и library ([план](plans/stage-3b.md), утверждён 2026-10-16, ADR 0028–0031; шаг 0a — контракты событий `collections.collection.v1`/`library.shelf.v1`, `source.collection`, заглушки API, `gv_text.ugc`/`emoji`, права identity `0007`).
- collections: конструктор, черновики, каталог, лайки, игра по сборнику.
- library: «смотреть позже», плейлисты, сохранение из ревила.
- Остальные ClueType: `audio_reverse`, `audio_blackout`, `soundtrack`, `description`, `emoji`, `frame_sequence`.
- Режимы: марафон, больше/меньше, топ-32.

## Этап 4. Онлайн — 4a реализован в ветке `stage4a/integration` (2026-10-22), на проверке владельцем; 4b не начат
План: [plans/stage-4.md](plans/stage-4.md) (решения владельца приняты 2026-10-22). Деление: **4a «Комнаты и матч»** — комнаты, кастомные лобби, дуэль, party, realtime, реконнект ([ADR 0032–0036](adr/README.md)); **4b «Рейтинг и подбор»** — rating, матчмейкинг, сезоны, лидерборды, формат `lite` (ADR 0037–0040).

### Подэтап 4a — что сделано
- **`libs/game_kernel`, `libs/testkit`:** онлайн-сессии в `ModeConfig` (`OnlineSessionSpec`, плагин `ContestRule`, `party_speed`, шаблоны лобби с `overridable`), фасад `ModeEngine.contest_*`; DSL `given_match` и фейки портов match.
- **`libs/service`, infra:** компонент `rooms` (`build_runners`, graceful stop с handoff, `GV_RUNNERS__*`), `GV_REALTIME__*` и поле `realtime` реестра; Centrifugo с `infra/centrifugo/config.json`, `/connection/http_stream` и `/connection/emulation` на gateway.
- **match:** домен комнаты и автомат матча, процесс `rooms` (lease с fencing-эпохой, поток команд, снимки, handoff), ручки комнат и матчей, consumers identity и game, итог в Postgres + outbox, `match.match.v1`, античит, реванш, чат и эмоции, разбор отказа старта (`start_error`). Исправления по аудиту безопасности и код-ревью учтены.
- **game:** internal `round-plans`, онлайн-шаблоны, `session_kind` и фильтр `session` в списке конфигов, секция `contest` в схеме редактора. **media-pipeline:** профиль `audio_intro/duel`. **identity:** `POST /v1/realtime/token`.
- **bff-web:** `/pages/online`, `/pages/match/{id}`, правила комнаты в блоке `modes`. **admin:** раздел «Матчи», онлайн-шаблоны в редакторе режимов.
- **frontend:** хаб, лобби, комната, матч, итог, «Мои матчи», реванш, «Отошёл», «Играть с друзьями» со страницы сборника, регистрация с возвратом по `next`, экран для гостя.
- **seed и CI:** `gv dev seed` публикует онлайн-шаблоны и сборник для лобби; job `e2e-smoke` с `e2e/{lobby,duel,party}.spec.ts`.
- **Проверено:** unit, integration, component, contract по затронутым модулям; `gv lint`; frontend-job (lint, typecheck, test, build) и Playwright онлайна на моках (2–4 контекста, ресинк по версии, tick, гость, отказ чата).
- **Осталось:** живая демонстрация [§0 плана](plans/stage-4.md#0-цель-и-критерии-готовности) п. 1–8б на compose у владельца и e2e lobby/duel/party на полном стеке.

### Хвосты 4a
| Пункт | Где | Когда |
|---|---|---|
| Переопределения `clue` и `answer.format` в лобби не поддержаны `round-plans` game: старт отказывает с `not_supported_yet` | match, game | После решения по плану раундов |
| `party_speed` на поздних уровнях даёт почти полные очки | `libs/game_kernel` (конфиги режимов) | Решение по геймдизайну |
| Блокирующий `XREAD` на комнату: ~300 соединений Redis на инстанс | match | Нагрузка этапа 7 |
| Окно отзыва connection-токена Centrifugo — до 10 мин (отзыв сессии не рвёт соединение) | identity, match | Принятый риск; пересмотреть на этапе 7 |
| Пакетные ручки catalog/library для итога матча (сейчас вызовы по единице) | bff-web, catalog, library | При росте числа раундов |
| Ручная проверка владельцем: identity (realtime-токен) и match (античит) | — | До слияния в `main` |
| Тег образа Centrifugo (`v6`) не закреплён до точной версии | `infra/compose` | Этап 7 (зеркало образов) |
| Исключённый игрок может переподписаться на канал комнаты старым subscription-токеном (до 10 мин): видит лобби и чат, ответов нет | match | Короткий TTL подписки или канал с эпохой состава — этап 5 |
| Одинаковые `CENTRIFUGO_TOKEN_HMAC_SECRET` и `CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET` в `.env` ломают разделение D9 — проверки при старте нет | `infra`, `tools/gv` | Preflight в `gv` до этапа 7 |

### Подэтап 4b — не начат
- Матчмейкинг (очереди по ladder-ам), rating: Эло с калибровкой, звания, сезоны, топ недели, лидерборды; формат `lite`; наблюдение с правом `match.spectate` (ADR 0037–0040).

## Этап 5. Прогрессия и социальное
- progression: опыт, уровни, валюта (ledger), задания, бейджи, магазин косметики, оформление профиля.
- social: друзья, комментарии, жалобы, модерация (+ разделы админки), санкции.
- notifications: in-app, realtime, e-mail.

## Этап 6. Интеграции и биллинг
- integrations: Shikimori/AniList, Trakt, Spotify, Яндекс (после проверки API). Экспорт и синхронизация.
- payments: подписки, entitlements, FakePaymentGateway. **Флаг `billing.enabled=false`.**

## Этап 7. Релиз
- Нагрузочное тестирование, resilience-сценарии, тест восстановления.
- Юридические документы, PWA, SEO.
- Прод на VPS. Подготовка Helm-чартов для переезда в Kubernetes.

## После релиза
- Kubernetes, recommendations, analytics (ClickHouse), Telegram Mini App, стримерский режим, новые домены (игры, сериалы как отдельный домен, книги).

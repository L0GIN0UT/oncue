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

- **edge:** `/metrics` на отдельном порту 9100 у всех процессов; gateway с TLS (443, HSTS, 80 → 301), `/media/` через `secure_link` в приватный MinIO; CSRF/Origin для cookie-аутентификации в `libs/auth`.
- **identity:** регистрация (никнейм + e-mail + пароль), вход по e-mail или никнейму, сессии с ротацией refresh, JWT/JWKS, роли и права, согласия, привязка Яндекс ID и вход через привязанный аккаунт, admin-сессии и admin-API пользователей и ролей.
- **catalog:** MediaItem (music + film), импорт (файл, TMDB, MusicBrainz, Spotify, Яндекс Музыка — внешние выключены по умолчанию), нормализация и алиасы, OpenSearch с fallback на pg_trgm, автокомплит, admin-API.
- **media-pipeline:** `audio_intro`, `frame_progressive`, `poster_masked`; подписанные ссылки (ADR 0012), admin-загрузка исходников и перегенерация. Вместо CDN — `proxy_cache` на gateway (CDN — этап 7).
- **admin:** Django 5.2, вход через identity, синхронизация прав, разделы «Каталог» и «Пользователи», аудит (append-only).
- Исправления по ревью: идемпотентность admin-мутаций в транзакции мутации и порядок событий ассетов ([ADR 0015](adr/0015-admin-idempotency-and-asset-event-order.md)), лимиты входа по аккаунту и подсети, повторная аутентификация привязок, отзыв сессий при понижении прав.
- **Проверено:** unit, integration, component, contract по сервисам этапа; `gv lint`, `gv contracts check`, `gv gen --check`.
- **Осталось (демонстрация [§0 плана](plans/stage-1.md#0-цель-и-критерии-готовности)):** живой сквозной прогон пунктов 1–6 на `gv dev up services` (регистрация → вход → импорт → `AssetsReady` → ссылка через gateway → блокировка) не выполнялся. Фронтенд и bff-web в этап 1 не входят — демонстрация идёт через API и админку.

### Хвосты этапа 1 (перенесены в этап 2)
| Пункт | Где | Когда |
|---|---|---|
| Периодическая очистка `idempotency_keys` (`gv_db.idempotency` `purge_older_than`) не запускается — таблица растёт | identity, catalog, media_pipeline | Этап 2 |
| Хук остановки (shutdown) в `ServiceDefinition` для consumer/worker: закрывать `RabbitTaskQueue` и др. (сейчас закрываются вместе с процессом) | `libs/service` | Этап 2 |
| Сервисные токены для internal API (D8): сейчас internal закрыт только сетью, `/internal/v1/asset-links` принимает любой `subject` | identity, media_pipeline, `libs/auth` | Этап 2, до первого вызова из game |
| Живой прогон integration-тестов OpenSearch и `nginx -t` на образе nginx 1.28 в CI | `.github/workflows`, catalog, `infra/nginx` | Первый прогон CI |
| `mypy_django_plugin` для admin | admin | Этап 2 |
| Протокол вместо `EventProcessor` в `KafkaEventConsumer` (admin подключается через `cast`) | `libs/messaging` | Этап 2 |
| Общий подключаемый inbox для Django (сейчас свой в admin) | `libs/messaging`, admin | Этап 5 (модерация в админке) |
| Ручная security-проверка владельцем: identity, `libs/auth`, вход в admin | — | До публичного запуска |

**Принятый риск:** лимитеры входа и denylist `sid` при отказе Redis работают **fail-open** (с метрикой и логом): перебор сдерживает argon2id с ограниченным параллелизмом, окно отозванного access-токена ≤ access TTL. Исключение — лимит выдачи ссылок media-pipeline: fail-closed (503). Подробнее — [architecture/security.md](architecture/security.md#7-реализация-этапа-1).

## Этап 2. Соло-игра
- `libs/game_kernel`: интерфейсы, реестр, плагины Source (daily, collection, random), Scorer (by_level, by_speed), matcher.
- game: сессии, дейлик (музыка + «Кадр дня»), случайный, ревил. Конфиги режимов и их редактор в админке.
- bff-web + frontend: главная, соло-хаб, игровой экран, результаты.
- **Результат:** можно пройти дейлик в обоих доменах.

## Этап 3. Сборники и медиатека
- collections: конструктор, черновики, каталог, лайки, игра по сборнику.
- library: «смотреть позже», плейлисты, сохранение из ревила.
- Остальные ClueType: `audio_reverse`, `audio_blackout`, `soundtrack`, `description`, `emoji`, `frame_sequence`.
- Режимы: марафон, больше/меньше, топ-32.

## Этап 4. Онлайн
- match: лобби, комнаты, конечный автомат, single-writer, Centrifugo, реконнект.
- Дуэль 1×1, party 3–6, кастомные лобби. Матчмейкинг.
- rating: Эло, ladder-ы, звания, сезоны, лидерборды.

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

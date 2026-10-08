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

## Этап 1. Аккаунт и каталог
- Перенесено из security-ревью этапа 0: CSRF/Origin-проверка для cookie-аутентификации (identity + `libs/auth`), `/metrics` на отдельный внутренний порт, HSTS и TLS на gateway.
- identity: вход (e-mail + Яндекс ID), JWT/JWKS, роли user/moderator/admin, согласия.
- catalog: модель MediaItem (music + film), импорт TMDB (фильмы) и музыкальных метаданных, OpenSearch, автокомплит, алиасы.
- media-pipeline: `audio_intro`, `frame_progressive`, `poster_masked`. Подписанные ссылки, CDN.
- admin: каркас, вход через identity, синхронизация прав, раздел каталога.

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

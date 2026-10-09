# План этапа 1: Аккаунт и каталог

> Статус: **утверждён** владельцем (2026-10-09) с изменениями D2, D6 и новым D9. Составлен агентом `planner`, проверен оркестратором.
> Решения зафиксированы в ADR 0012–0014.

## 0. Цель и критерии готовности

**Цель.** Человек регистрируется (e-mail или Яндекс ID), получает JWT с ролями и правами. Каталог фильмов и треков наполняется из TMDB или файла, ищется с автокомплитом. Для каждой единицы media-pipeline генерирует ассеты `audio_intro`, `frame_progressive` и `poster_masked` по уровням и выдаёт короткоживущие подписанные ссылки. Модератор входит в Django-админку через identity, блокирует фильм, правит алиасы и запускает импорт.

**Демонстрация (этап считается сделанным, когда всё это проходит):**
1. `POST /api/identity/v1/auth/register` → письмо в лог → verify → login → в cookie лежат `gv_access`, `gv_refresh`, `gv_csrf`. Мутация без `X-CSRF-Token` возвращает 403.
2. Админ входит в `admin`, импортирует фильм по TMDB id (`GV_TMDB__ENABLED=true`) и загружает аудио для трека.
3. В Redpanda видны события `catalog.MediaItemCreated` → `media_pipeline.AssetsReady`. `GET /api/catalog/v1/autocomplete?q=инте` находит «Интерстеллар».
4. `POST /internal/v1/asset-links` возвращает URL. Через gateway `/media/...` он работает до истечения TTL, после него — 403.
5. Блокировка единицы в админке → `MediaItemBlocked` → ссылка больше не выдаётся (410), единица пропадает из автокомплита.
6. `gv lint`, `gv test --changed` (unit, integration, component, contract), `gv contracts check`, `gv gen --check` и `nginx -t` проходят. `/metrics` снаружи недоступен, на 443 есть HSTS.

## 1. Сквозные решения (утверждает владелец до старта)

| # | Решение | Почему |
|---|---|---|
| D1 | Схемы событий пишутся руками в `contracts/events/<service>/<Event>.v1.json` на шаге 0. Продюсеры и потребители в тестах проверяются по ним через testkit | Потоки идут параллельно: импортировать модели чужого сервиса нельзя |
| D2 | **Утверждено владельцем:** основная учётная запись — никнейм + e-mail + пароль; вход **по e-mail или никнейму** и паролю (сайт и админка). Яндекс ID и другие внешние сервисы — **привязанные аккаунты**: привязываются к существующей учётной записи из профиля; на сайте можно войти через уже привязанный Яндекс, в админку — только паролем. TOTP — этап 5/7; доступ к админке по allowlist IP/VPN | Один владелец аккаунта; внешний сервис не создаёт «теневых» учёток и не даёт захватить аккаунт по совпавшему e-mail |
| D3 | Раздел «Каталог» в админке **и читает, и пишет через admin-API catalog** (свои view на Django), а не через unmanaged-модели поверх реплики. `ServiceModelAdmin` и роутеры реплик делаем каркасом, применять начнём в этапе 5 | Убирает зависимость потока D от схемы БД потока B. Отступление от `05-admin-panel.md` §2 — нужна пометка в ADR |
| D4 | Подписанные ссылки — HMAC-ссылки, которые проверяет nginx (`secure_link`, `/media/` на gateway → приватный MinIO во внутренней сети, `proxy_cache` вместо CDN). TTL 60 с. **Одноразовость на краю не обеспечивается**: её заменяют короткий TTL, случайные ключи объектов и журнал выдачи с rate-limit. Внешний CDN с token-auth подключается тем же портом в этапе 7 | Расходится с `security.md` §5 («одноразовые»), нужен ADR |
| D5 | Профили уровней (`asset_profiles.yaml`) в этапе 1 лежат в media-pipeline. Схемы параметров `reveal` фиксируются в `contracts/clues/<clue_type>.params.v1.json`, чтобы в этапе 2 `libs/game_kernel` взял их, а не продублировал | Contract-first без game_kernel |
| D6 | **Утверждено владельцем с изменением:** порт `MusicMetadataProvider`, адаптеры `FileImportProvider` (по умолчанию), `MusicBrainzProvider`, `SpotifyMetadataProvider` (официальный Web API, client credentials: поиск, артисты, популярность), `YandexMusicMetadataProvider` (неофициальный клиент, **только метаданные**; риск нарушения условий сервиса принимает владелец). Все внешние адаптеры выключены по умолчанию. **Аудио** — только из источников с правом использования (загрузка правообладателем или админом, сиды); скачивание аудио с пиратских площадок и через неофициальные клиенты стримингов не реализуется | ADR 0014: метаданные — факты, аудиозаписи — объект авторского права |
| D7 | В `admin` используется Django 5.2 LTS. Глаголы CLI остаются прежними (`admin api` → ASGI uvicorn, `admin consumer` → синхронизация ролей, `admin migrate` → `manage.py migrate`), поэтому шаблоны compose и Dockerfile не меняются | Минимум правок в генераторе |
| D9 | Никнейм: уникальный без учёта регистра, 3–24 символа (латиница, кириллица, цифры, `_ . -`), нормализация NFKC; `users.nickname` (citext unique). Смена ника — этап 5 | Нужен для входа по никнейму (D2) |
| D8 | Сервисных токенов (client credentials) в этапе 1 нет. Internal API закрыт сетью (gateway отдаёт 404 на `/internal`, проверено в этапе 0) | Первый внутренний вызов — game → media-pipeline, а это этап 2 |

## 2. Шаг 0 — делает оркестратор до запуска потоков (ветка `stage1/contracts`)

**2.1. Реестр и инфраструктура**
- `infra/services.toml`:
  - `catalog.rabbitmq = true` (воркер импорта);
  - `media_pipeline.redis = true` (rate-limit выдачи ссылок, кэш состояния);
  - новое поле `framework = "fastapi" | "django"`, у admin — `django`;
  - новое поле `apt = ["ffmpeg", "libvips42"]` у media_pipeline.
- `tools/gv/src/gv_cli/registry.py`, `contracts.py`, `generate.py`, шаблоны `importlinter.j2` и `infra/docker/python-service.Dockerfile` (ARG `APT_PACKAGES`):
  - для `framework=django` не экспортировать OpenAPI;
  - для admin заменить контракт слоёв на независимость `admin_panel` от `*_service`.
- `gv contracts check` проверяет совместимость `contracts/events/**/*.json` и `contracts/clues/*.json` относительно `main` (обход подкаталогов в `compare_with_base`).
- `ServiceSettings` (`libs/service/src/gv_service/settings.py`): поля `public_origins: tuple[str, ...] = ()` и `metrics_port: int | None = 9100`.
- `app.py`: `app.state.csrf_origins = settings.public_origins`. Реализация CSRF — в потоке A, порта метрик — в потоке D.
- Шаблон `service-databases.sh.j2`: `CREATE EXTENSION IF NOT EXISTS pg_trgm, citext` в БД identity и catalog (пользователь сервиса не суперпользователь).
- `.github/workflows/ci.yml`: установка `ffmpeg`, сервисы OpenSearch и MinIO для integration-тестов catalog и media_pipeline.

**2.2. Зависимости** — сразу во все `pyproject.toml`, один `uv lock`. В потоках `uv.lock` не меняется.

| Пакет | Куда |
|---|---|
| `argon2-cffi>=23`, `email-validator>=2`, `aiosmtplib>=3` | identity |
| `opensearch-py[async]>=2.7` | catalog |
| `Pillow>=11`, `aioboto3>=13`, `pyyaml>=6` | media_pipeline |
| `django>=5.2,<5.3`, `psycopg[binary]>=3.2`, `uvicorn` | admin |
| `respx>=0.21`, `jsonschema>=4.23` | testkit |

**2.3. testkit** (после шага 0 владелец — оркестратор, кроме `auth.py`):
- `gv_testkit/infra.py`: фикстуры `opensearch_url` и `s3_endpoint` (MinIO).
- `gv_testkit/contracts.py`: `assert_event_matches_schema(envelope)` и `load_example(path)`.
- `gv_testkit/stubs.py`: обёртка над respx (`stub_service(base_url, openapi="contracts/openapi/<svc>.json")`).

**2.4. Схемы событий.** Конверт общий: `event_type = <producer>.<Event>`, `event_version = 1`. Все id — UUID-строки, время — RFC 3339 UTC. Каждый payload несёт `version` (целое, монотонное для агрегата), чтобы потребители отбрасывали устаревшие события.

`identity`, топик `identity.user.v1`, `aggregate_id = user_id`:
- `UserRegistered`: `user_id`, `nickname: str(3..24)`, `display_name: str(1..40)`, `locale: str|null`, `version: int`. **Без e-mail** (минимизация ПДн).
- `UserProfileBasicsChanged`: `user_id`, `display_name`, `version`.
- `UserRolesChanged`: `user_id`, `roles: [str]`, `perms: [str]` (полный снимок), `version`, `actor_id: uuid|null`, `reason: str|null`.
- `UserDeleted`: `user_id`, `deleted_at`, `version`.
- `ExternalAccountLinked` / `ExternalAccountUnlinked`: `user_id`, `provider`, `version` (без внешнего subject и токенов).

`catalog`, топик `catalog.media_item.v1`, `aggregate_id = media_item_id`:
- `MediaItemCreated` / `MediaItemUpdated` — полный снимок:
  - `media_item_id`, `version`, `domain: "music"|"film"`, `kind: "track"|"film"`;
  - `title`, `original_title|null`, `year|null`, `status: "active"|"blocked"`;
  - `aliases: [{text, normalized, lang|null, kind: "title"|"alt"|"translit"|"short"}]`;
  - `persons: [{person_id, name, role: "artist"|"director"|"actor", order}]`;
  - `genres: [str]`;
  - `external_refs: {tmdb?: str, musicbrainz?: str, isrc?: str}`;
  - `metrics: {popularity?, vote_average?, vote_count?: int, revenue_usd?: int, runtime_min?: int, duration_ms?: int}`;
  - `source_media: [{source_media_ref, kind: "audio"|"frame"|"poster", origin: "upload"|"tmdb", uri}]`.
  - Только в `Updated`: `changed_fields: [str]`.
- `MediaItemBlocked`: `media_item_id`, `version`, `reason_code`, `actor_id`.
- `MediaItemUnblocked`: `media_item_id`, `version`, `actor_id`.

`media_pipeline`, топик `media_pipeline.asset_set.v1`, `aggregate_id = media_item_id`:
- `AssetsReady`: `asset_set_id`, `media_item_id`, `clue_type`, `profile_key`, `params_hash`, `levels_count: int`, `version`.
- `AssetsFailed`: `asset_set_id`, `media_item_id`, `clue_type`, `profile_key`, `error_code`, `retryable: bool`, `attempts: int`, `version`.

`contracts/clues/`:
- `audio_intro.params.v1.json`: `levels[].seconds`, `start_s`.
- `frame_progressive.params.v1.json`: `levels[].blur_px`, `crop`, `max_width`.
- `poster_masked.params.v1.json`: `levels[].pixel_block`, `mask_regions?`.

**2.5. Internal и admin API** (схемы фиксируются здесь; снимок OpenAPI каждый поток экспортирует сам):
- identity:
  - `GET /.well-known/jwks.json` (внутренний, `GV_JWKS_URL=http://identity:8000/.well-known/jwks.json`);
  - `POST /internal/v1/admin-sessions` `{login, password}` (`login` — e-mail или никнейм) → `{access_token, refresh_token, expires_in, user: {id, display_name, roles, perms}}`; без права `admin.access` — 403 `auth.forbidden`;
  - `POST /internal/v1/admin-sessions/refresh` `{refresh_token}` → то же.
- media_pipeline:
  - `POST /internal/v1/asset-links` `{asset_set_id, level: int>=1, subject: str(≤100)}` → `{url, expires_at, mime, level}`. Ошибки: 404 `asset.not_found`, 409 `asset.not_ready`, 410 `asset.blocked`, 422 `asset.level_out_of_range`, 429;
  - `GET /internal/v1/asset-sets?media_item_id=&clue_type=` → `[{asset_set_id, clue_type, profile_key, status, levels_count}]`.
- catalog admin-API (для потока D):
  - `GET /admin/v1/items?q=&domain=&status=&cursor=` и `GET /admin/v1/items/{id}`;
  - `POST /admin/v1/items/{id}/block {reason}`, `POST .../unblock {reason}`, `PUT /admin/v1/items/{id}/aliases {aliases[], reason}`;
  - `POST /admin/v1/imports {source: "tmdb"|"file"|"musicbrainz"|"spotify"|"yandex_music", ref | file_id}` → 202 `{import_id}`, `GET /admin/v1/imports/{id}`.
- media_pipeline admin-API: `POST /admin/v1/sources` (multipart: `media_item_id`, `kind`, файл); `POST /admin/v1/asset-sets/{id}/regenerate {reason}`.
- identity admin-API: `GET /admin/v1/users?q=`, `GET /admin/v1/users/{id}`, `PUT /admin/v1/users/{id}/roles {roles[], reason}`, `GET|PUT /admin/v1/roles` (матрица).
- Все admin-мутации принимают `Idempotency-Key` и обязательный `reason`.

**2.6. Права** (справочник `perms`, сид в миграции identity): `admin.access`, `users.view`, `roles.assign`, `roles.manage`, `catalog.import`, `catalog.block_item`, `catalog.edit_aliases`, `media.upload_source`, `media.regenerate`.
- moderator = `admin.access`, `users.view`, `catalog.block_item`, `catalog.edit_aliases`;
- admin = все права.

## 3. Потоки и владение файлами

| Поток | Ветка | Может менять |
|---|---|---|
| **A** identity + auth | `stage1/identity` | `services/identity/**`, `libs/auth/**`, `libs/testkit/src/gv_testkit/auth.py`, `contracts/openapi/identity.json` |
| **B** catalog | `stage1/catalog` | `services/catalog/**`, `contracts/openapi/catalog.json`, `seeds/catalog/**` |
| **C** media-pipeline | `stage1/media` | `services/media_pipeline/**`, `contracts/openapi/media_pipeline.json`, `seeds/media/**` |
| **D** admin + edge | `stage1/admin-edge` | `services/admin/**`, `libs/service/src/gv_service/{app,runner}.py`, `libs/service/tests/**`, `infra/nginx/**` (кроме `generated/`), `infra/compose/compose.gateway.yml`, `tools/gv/src/gv_cli/templates/gen/{prometheus-services,nginx-*}.j2`, генерируемые файлы `infra/**` (единственный поток, который запускает `gv gen`) |
| Оркестратор | `stage1/contracts` и слияния | `infra/services.toml`, `pyproject.toml` корня, `uv.lock`, `contracts/events/**`, `contracts/clues/**`, `libs/testkit/**` (кроме `auth.py`), `libs/core`, `libs/db`, `libs/messaging`, `tools/gv/**` (кроме шаблонов D), `docs/**`, `.github/**`, ADR |

Правила:
- Нужно изменить чужое или общее — поток пишет запрос оркестратору и не правит сам.
- Поток правит только `CLAUDE.md` своего сервиса; изменения в `docs/` собирает оркестратор при слиянии.
- Изменилась схема события — это новый шаг 0, а не правка в потоке.

## 4. Поток A — identity и libs/auth (зона повышенного контроля)

**Домен** (`identity_service/domain/`):
- `User` (агрегат: status `active|deleted`, `roles_version`);
- `EmailAddress` (VO, нормализация lower и IDNA);
- `PasswordPolicy` (длина ≥ 10, ≤ 128, запрет топ-10k из файла);
- `Session` и `RefreshToken`: семья = сессия, правила ротации и `reuse_detected`;
- `RoleMatrix` (роль → права, effective perms);
- `Consent` (kind и version, append-only);
- `AuthIdentity` (provider, subject).

Unit: ротация, повторное использование → отзыв сессии, effective perms, политика паролей, обязательные согласия.

**Application:** `RegisterWithEmail` (никнейм + e-mail + пароль), `VerifyEmail`, `LoginWithPassword` (логин = e-mail или никнейм), `LinkExternalAccount`, `UnlinkExternalAccount`, `LoginWithLinkedYandex`, `RefreshSession`, `Logout`, `LogoutAll`, `RequestPasswordReset`, `ResetPassword`, `StartYandexOAuth` (purpose `link|login`), `CompleteYandexOAuth`, `GrantConsents`, `AssignRoles`, `UpdateRoleMatrix`, `DeleteAccount`, `AdminLogin`, `GetJwks`.

Порты: `UserRepository`, `SessionRepository`, `PasswordHasher`, `TokenIssuer`, `SigningKeyProvider`, `OAuthProvider` (authorize_url, exchange_code, fetch_profile), `EmailSender`, `RateLimiter`, `RevocationStore`, `OAuthStateStore`.

**Инфраструктура:**
- `Argon2Hasher` (argon2id, параметры из настроек, `needs_rehash` при входе);
- `JwtIssuer` (EdDSA, ключи из `GV_JWT__SIGNING_KEYS`: JSON `[{kid, private_pem, active}]`, в JWKS — все неистёкшие);
- `YandexOAuthProvider` (httpx; `oauth.yandex.ru/authorize`, `/token`, `login.yandex.ru/info`; PKCE S256; `state` в Redis, TTL 10 мин, одноразовый через GETDEL);
- `SmtpEmailSender` и `LogEmailSender` (по умолчанию);
- `RedisRateLimiter`, `RedisRevocationStore` (`auth:revoked_sid:<sid>`, TTL = access TTL).

**Таблицы** (`0002_identity_core.py`, expand-only; сид ролей и прав — `0003_seed_roles.py`): `users` (id uuid7, nickname citext unique not null, email citext unique not null, email_verified_at, display_name, status, roles_version, created_at, deleted_at); `password_credentials`; `auth_identities` — привязанные аккаунты (user_id, provider, subject, display, linked_at; unique(provider, subject): аккаунт провайдера привязан максимум к одному пользователю); `sessions` (revoked_at, revoke_reason, ip_prefix); `refresh_tokens` (token_hash sha256 unique, parent_id, used_at, revoked_at); `roles`, `permissions`, `role_permissions`, `user_roles`; `role_changes` (append-only аудит); `consents`; `email_tokens` (hash, purpose verify|reset).

**API** (`api/public/`): `POST /v1/auth/register`, `verify-email`, `login`, `refresh`, `logout`, `logout-all`, `password/forgot`, `password/reset`; `GET /v1/me/linked-accounts`, `POST /v1/me/linked-accounts/yandex` (старт привязки, нужен вход) → URL авторизации, `DELETE /v1/me/linked-accounts/{provider}`; `GET /v1/oauth/yandex/login` (только для уже привязанного Яндекса, иначе 409 `auth.provider_not_linked`) и общий `GET /v1/oauth/yandex/callback`; `GET /v1/me`, `POST /v1/me/consents`, `DELETE /v1/me`; `/.well-known/jwks.json`; internal и admin из §2.5.

**События:** публикует `UserRegistered`, `UserProfileBasicsChanged`, `UserRolesChanged`, `UserDeleted` (outbox в той же транзакции). Ничего не слушает.

**libs/auth:**
- `gv_auth/csrf.py`: если токен из cookie и метод не GET/HEAD/OPTIONS — `Origin` (fallback `Referer`) в `app.state.csrf_origins` **и** `X-CSRF-Token` совпадает с cookie `gv_csrf` (`hmac.compare_digest`). С Bearer-токеном проверка не нужна; `_extract_token` возвращает и источник токена.
- `require_same_origin` — для login и register (login-CSRF).
- Опциональный `RevocationChecker` в `JwtVerifier`: проверка `sid` в Redis; при недоступности Redis — fail-open с метрикой и логом (решение фиксируется в CLAUDE.md identity).
- Claims access-токена: `iss`, `aud`, `sub`, `sid`, `jti`, `roles`, `perms`, `sanctions = 0`, `plan = "free_all"`, `iat`, `exp` (15 мин).

**Настройки:** `GV_JWT__SIGNING_KEYS` (secret), `GV_JWT__ACCESS_TTL_S = 900`, `GV_JWT__REFRESH_TTL_S = 2592000`; `GV_COOKIES__SECURE = true`, `GV_COOKIES__DOMAIN`, `GV_PUBLIC_ORIGINS`; `GV_YANDEX__ENABLED = false`, `GV_YANDEX__CLIENT_ID|CLIENT_SECRET|REDIRECT_URI`; `GV_EMAIL__BACKEND = log`, `GV_SMTP__*`; `GV_LOGIN_LIMIT__PER_IP_PER_MIN = 10`, `GV_LOGIN_LIMIT__PER_ACCOUNT_PER_15MIN = 5`; `GV_ARGON2__TIME_COST`, `GV_ARGON2__MEMORY_KIB`; `GV_CONSENTS__REQUIRED = "terms:2026-10,privacy:2026-10,age_18:1"`; `GV_BOOTSTRAP_ADMIN_EMAILS` (роль admin — только после подтверждения e-mail).

**Component-тесты:** register → verify → login → refresh; повторное использование refresh → 401 и отзыв сессии; logout-all → старый access отвергается (через `sid`); CSRF (нет заголовка, чужой Origin, Bearer без CSRF проходит); привязка Яндекса на respx (неверный `state` → 400; аккаунт уже привязан к другому пользователю → 409); вход через непривязанный Яндекс → 409; вход по никнейму; смена ролей → `UserRolesChanged` в outbox валиден по схеме; после ротации в JWKS два ключа.

**Готово, когда** проходят `gv test identity`, `gv test libs/auth`, `gv contracts check` и проверки §8.

## 5. Поток B — catalog

**Домен:** `MediaItem` (агрегат: `domain`, `kind`, статусы `active|blocked`, `version`; у активной единицы ≥ 1 алиас вида `title`, год 1870..текущий+2); `Alias` и `TitleNormalizer` (NFKC, casefold, ё→е, пунктуация и артикли по конфигу, транслит ru↔lat); `Person`, `ExternalRef`, `MediaMetrics`, `ImportJob` (`pending|running|done|failed`, счётчики). Unit: нормализация (golden-таблица), блокировка/разблокировка, слияние импорта с ручными алиасами (ручные не перетираются).

**Application:** `ImportFilmFromTmdb`, `ImportTracksFromFile`, `ImportMusicMetadata(source)` (MusicBrainz, Spotify, Яндекс — через один порт), `BlockItem`, `UnblockItem`, `ReplaceAliases`, `MarkClueAssets` (из событий media-pipeline), `IndexItem`, `Autocomplete`, `Search`, `GetItem`. Порты: `MediaItemRepository`, `FilmMetadataProvider`, `MusicMetadataProvider`, `SearchIndex`, `PrefixCache`, `TaskQueue`.

**Инфраструктура:**
- `TmdbClient`: httpx, Bearer v4, `language=ru-RU` с fallback на en, `append_to_response=alternative_titles,translations,credits,images`; изображения с `include_image_language=null` (**без надписей** — иначе название выдаёт ответ); rate-limit, ретраи по 429/5xx.
- `FileImportProvider` (JSONL по схеме из `seeds/catalog/README`); `MusicBrainzProvider`, `SpotifyMetadataProvider` (Web API, client credentials, токен кэшируется), `YandexMusicMetadataProvider` (неофициальный, только метаданные) — все выключены по умолчанию, у каждого свой rate-limit и circuit breaker.
- `OpenSearchIndex`: индекс `catalog_media_v1` + алиас `catalog_media`; edge n-gram и анализаторы `russian`/`english` на `title` и `aliases`; запись с `version_type=external` (идемпотентно и с учётом порядка).
- `RedisPrefixCache` и Postgres-fallback `pg_trgm` (`TrgmSearch`).

**Индексация:** catalog-consumer читает свой топик `catalog.media_item.v1` и через inbox пишет в OpenSearch. Импорт — RabbitMQ-задача `catalog.import` (идемпотентна по `import_id`), одна TMDB-сущность — одна транзакция с outbox.

**Таблицы** (`0002_catalog_core.py`): `media_items`; `aliases` (GIN trgm по normalized; source import|manual); `persons`, `media_item_persons`; `genres`, `media_item_genres`; `external_refs` (unique(source, external_id)); `media_metrics` (jsonb); `source_media_refs`; `media_item_clue_status` (clue_type, profile_key, asset_set_id, status, version); `import_jobs` (idempotency_key unique, counters jsonb, actor_id, error).

**События:** публикует `MediaItemCreated`, `MediaItemUpdated`, `MediaItemBlocked`, `MediaItemUnblocked`. Слушает `media_pipeline.AssetsReady` и `AssetsFailed` (примеры — в `tests/contract/consumed/`).

**API:** public `GET /v1/autocomplete?q=&domain=&limit≤10` (только `active`, rate-limit в Redis, на выходе `{id, title, year, subtitle}`); public `GET /v1/items/{id}` — карточка без ассетов; `GET /v1/search`; admin-API из §2.5 с `require_perm(...)`.

**Настройки:** `GV_OPENSEARCH__URL`, `GV_OPENSEARCH__INDEX = catalog_media`; `GV_TMDB__ENABLED = false`, `GV_TMDB__API_TOKEN`, `GV_TMDB__LANGUAGE = ru-RU`, `GV_TMDB__RPS = 20`; `GV_MUSICBRAINZ__ENABLED = false`, `GV_MUSICBRAINZ__USER_AGENT`; `GV_SPOTIFY__ENABLED = false`, `GV_SPOTIFY__CLIENT_ID|CLIENT_SECRET`; `GV_YANDEX_MUSIC__ENABLED = false`, `GV_YANDEX_MUSIC__TOKEN`; `GV_AUTOCOMPLETE__CACHE_TTL_S = 300`, `GV_AUTOCOMPLETE__MIN_PREFIX = 2`.

**Тесты:** integration — репозиторий, `OpenSearchIndex` (фикстура `opensearch_url`), идемпотентность индексации (повтор и старое событие), trigram-fallback, `TmdbClient` на записанных JSON через respx; component — импорт через admin-API → событие валидно по схеме → автокомплит; при выключенном OpenSearch автокомплит работает на trigram; блокировка убирает единицу из выдачи.

**Сиды:** `seeds/catalog/films.jsonl` и `tracks.jsonl` (~20 + 20; 200 + 200 — к концу этапа 2).

## 6. Поток C — media-pipeline

**Домен:** `AssetSet` (агрегат: `pending → processing → ready | failed`, `disabled`; `params_hash` — sha256 канонического JSON параметров); `AssetLevel`; `AssetProfile` (`clue_type` + `profile_key`, уровни из `config/asset_profiles.yaml`, валидация по `contracts/clues/*`); `SourceMedia`; `LinkPolicy` (TTL, максимум выдач на `subject` и уровень).

Плагины генерации — реестр `AssetGenerator` по ключу ClueType, без `if type ==`:
- `AudioIntroGenerator`: на каждый уровень отдельный объект 0..N (AAC в fMP4, `-map_metadata -1`, loudnorm) — ранний уровень физически не содержит позднего аудио;
- `FrameProgressiveGenerator`: Pillow — центральный crop, blur, resize, WebP, без EXIF;
- `PosterMaskedGenerator`: пикселизация блоками, необязательные `mask_regions`.

Unit: число уровней = профилю, длительность ≤ `seconds`; монотонное уменьшение размытия/пикселизации по уровням (Hypothesis); стабильный `params_hash`; в ключе объекта нет `media_item_id`.

**Application:** `RegisterSourceFromCatalog`, `UploadSource`, `PlanAssetSets` (по `MediaItemCreated`/`Updated` и профилям домена), `GenerateAssetSet` (задача), `RegenerateAssetSet`, `DisableItemAssets` (по `Blocked`), `IssueAssetLink`, `ListAssetSets`. Порты: `ObjectStorage`, `SourceFetcher`, `Transcoder`, `ImageProcessor`, `UrlSigner`, `LinkRateLimiter`, `AssetSetRepository`.

**Инфраструктура:**
- `S3Storage` (aioboto3; приватные бакеты `gv-sources`, `gv-assets`; ключ `a/<random 32 hex>.<ext>`);
- `HttpSourceFetcher`: **allowlist хостов** (`image.tmdb.org`), только https, лимит размера и таймаут, без редиректов на другой хост (SSRF);
- `FfmpegTranscoder` (subprocess без shell, таймаут); `PillowImageProcessor` (`Image.MAX_IMAGE_PIXELS`, тип по содержимому);
- `NginxSecureLinkSigner` (`md5(expires + uri + secret)` в формате nginx `secure_link_md5`), `S3PresignSigner` (альтернатива), `RedisLinkRateLimiter`.

**Таблицы** (`0002_media_core.py`): `media_items_state` (проекция: status, version); `source_media` (sha256, bytes, status); `asset_sets` (unique(media_item_id, clue_type, profile_key, params_hash); status, attempts, error_code, version); `asset_levels` (object_key unique, mime, bytes, duration_ms, width, height; PK(asset_set_id, level)); `link_issuance` (чистка старше 7 дней).

**События и задачи:** слушает `catalog.MediaItemCreated/Updated/Blocked/Unblocked` (устаревшие отбрасывает по `version`); RabbitMQ-задача `media.generate_asset_set` с `task_id = asset_set_id:attempt` (повтор перезаписывает те же `asset_levels`); публикует `AssetsReady`, `AssetsFailed`.

**API:** internal и admin из §2.5. Загрузка: лимит размера, проверка сигнатуры, перекодирование.

**Настройки:** `GV_S3__ENDPOINT|ACCESS_KEY|SECRET_KEY|BUCKET_SOURCES|BUCKET_ASSETS`; `GV_MEDIA__SIGNER = nginx_secure_link`, `GV_MEDIA__SIGNING_SECRET` (общий с gateway), `GV_MEDIA__LINK_TTL_S = 60`, `GV_MEDIA__PUBLIC_BASE_URL`; `GV_MEDIA__SOURCE_HOSTS = image.tmdb.org`, `GV_MEDIA__MAX_SOURCE_MB = 50`; `GV_MEDIA__PROFILES_PATH`, `GV_FFMPEG__BIN = ffmpeg`, `GV_FFMPEG__TIMEOUT_S = 60`.

**Тесты:** integration — MinIO (`s3_endpoint`), реальный ffmpeg на 20-секундном синтетическом тоне, Pillow, SSRF-отказы, подпись совпадает с эталоном nginx; component — `MediaItemCreated` (пример из `contracts`) → задача → `AssetsReady` валиден по схеме → `asset-links` выдаёт URL с правильным `expires`; `Blocked` → 410; превышение лимита выдачи → 429.

## 7. Поток D — admin (Django) и edge

**D1. Edge — сливается первым, задевает все сервисы.**
- `libs/service/runner.py`: отдельный метрик-сервер на `GV_METRICS_PORT` (9100) для **всех** команд, включая consumer и worker; из `app.py` убрать `/metrics` с основного порта. Тест: на основном порту `/metrics` → 404, на метрик-порту → 200.
- `prometheus-services.yml.j2`: цель `:9100`; затем `gv gen`.
- `infra/nginx/nginx.conf`: `listen 443 ssl`, `http2 on`, TLS 1.2/1.3, `ssl_session_tickets off`, OCSP stapling; на 80 — 301 на https (кроме `/.well-known/acme-challenge/`); `Strict-Transport-Security "max-age=31536000; includeSubDomains" always` только на 443; CSP для API (`default-src 'none'; frame-ancestors 'none'`); `location /media/` с `secure_link` → `proxy_pass` в MinIO (внутренняя сеть, read-only на префикс `a/`), `proxy_cache` без query в ключе; `limit_req` на `/api/identity/v1/auth/`.
- Сертификаты: `infra/nginx/certs/` (в `.gitignore`); dev — `gv dev certs` (запрос оркестратору на правку `tools/gv`); prod — certbot webroot в `compose.gateway.yml`.

**D2. Каркас admin** — `services/admin/src/admin_panel/` вместо заглушки:
- `settings.py` из env (`GV_ADMIN__SECRET_KEY`, `__ALLOWED_HOSTS`, `__IP_ALLOWLIST`, `__SESSION_TTL_S = 28800`, `__IDENTITY_URL`, `__SERVICE_URLS__catalog|media_pipeline|identity`);
- `core/`: `IdentityAuthBackend` (вход через `/internal/v1/admin-sessions`, проверка access по JWKS через `gv_auth.JwtVerifier`, зеркальный `User` по `identity_user_id`); `PermissionSync` (perms → Django groups, `is_staff`/`is_superuser` на каждом входе и refresh); `IpAllowlistMiddleware`; `ServiceClient` (httpx; пробрасывает `Authorization`, `X-Request-Id`, `Idempotency-Key`; Problem Details → сообщение); `ServiceModelAdmin` и `ReplicaRouter` — каркас без применения (D3);
- `audit/`: `AuditEntry` (append-only: actor, action, target, reason, before/after, request_id);
- `apps/catalog/`: список и поиск, карточка, блокировка/разблокировка с причиной, алиасы, импорт по TMDB id или файлом, статус импорта, загрузка аудио/кадра, перегенерация ассетов — всё через admin-API;
- `apps/users/` (минимум): поиск, карточка, смена ролей (`roles.assign`);
- `__main__.py` сохраняет глаголы `api|consumer|migrate|all`; `consumer` читает `identity.user.v1` (`UserRolesChanged`, `UserDeleted`) и обновляет зеркало только при большем `version`;
- Тесты (pytest-django): вход через identity на respx-заглушке; недостаточно прав → нет доступа; синхронизация ролей при старом и новом `version`; действие каталога вызывает admin-API с `reason` и `Idempotency-Key` и пишет `AuditEntry`; пустой `reason` → ошибка формы.

## 8. Обязательные проверки безопасности

1. Пароли: argon2id (≥ 64 MiB, t ≥ 3), rehash при входе; одинаковые ответ и время для «нет пользователя» и «неверный пароль» (фиктивный хэш); rate-limit по IP и аккаунту, 429 с `Retry-After`.
2. Refresh: 32 байта из `secrets`, в БД только sha256; ротация на каждое использование; повторное использование → отзыв сессии и `sid` в denylist; cookie `HttpOnly; Secure; SameSite=Lax; Path=/api/identity/v1/auth`.
3. CSRF и Origin на всех cookie-мутациях и на login/register; `gv_csrf` перевыдаётся при входе и refresh.
4. JWT: только EdDSA и ES256; обязательны `kid`, `aud`, `iss`; ротация без простоя (два ключа в JWKS); приватные ключи только из секрета.
5. OAuth Яндекса (только привязка и вход через привязанный аккаунт): PKCE S256; привязка — только для вошедшего пользователя, `state` связан с его `sid`; одноразовый `state` с привязкой к браузеру (cookie + Redis); `redirect_uri` строго из конфига; `return_to` только относительный из allowlist (open redirect); **без** автослияния с аккаунтом по совпадающему e-mail (захват аккаунта).
6. Никаких ПДн в событиях и логах (e-mail, IP, токены маскирует `gv_observability`; тест обязателен).
7. Admin-API: `require_perm` на каждой ручке, `reason` для деструктивных действий, аудит в сервисе и в админке.
8. media-pipeline: SSRF (allowlist, без приватных IP после DNS-резолва); лимиты размера и пикселей; ffmpeg без shell с таймаутом; метаданные вырезаны; случайные ключи объектов; MinIO не торчит наружу.
9. Автокомплит: rate-limit, длина `q` ≤ 64, заблокированные единицы не в выдаче.
10. Процедура (`docs/10-ai-assistant.md` §3): поток A — `security-discipline`, `auth-and-authorization`, `security-reviewer`, `finecomb` по `services/identity` и `libs/auth`; потоки B, C, D — `security-discipline` и `auth-and-authorization`; новые OpenAPI — `42crunch-audit`.

## 9. Что НЕ входит (этап 2 и дальше)

- Вход через Яндекс и SSO в админку, TOTP — этапы 2 и 5/7.
- Слияние аккаунтов (`AccountsMerged`) и привязка нескольких провайдеров к одному аккаунту — этап 6.
- Сервисные токены и mTLS — этап 2, при первом внутреннем вызове.
- Сериалы и аниме, франшизы, `PersonUpdated`, метрики слушателей — этап 3+.
- `ImportRequested` от integrations — этап 6.
- `ClueAssetsRequested` от game и ассеты под конфиг режима — этап 2; остальные ClueType — этап 3.
- Проверка UGC-изображений и аватаров — этап 5.
- Внешний CDN с token-auth и настоящая одноразовость ссылок — этап 7 (ADR по D4).
- Unmanaged-модели и реплики в админке, разделы модерации, конфиги режимов — этапы 2 и 5.
- Проверка JWT на gateway — после переезда на Envoy.
- Перенос писем из identity в notifications — этап 5.

## 10. Риски

| Риск | Мера |
|---|---|
| Метрик-порт в `libs/service` ломает тесты и деплой всех 15 сервисов | D1 сливается первым, после него полный `gv test` |
| Расхождение схем событий между B и C | Только схемы шага 0, валидация продюсера и примеры у потребителя |
| Надписи на постерах и кадрах раскрывают ответ | Изображения без языка из TMDB, `mask_regions`, ручная проверка в админке перед `active` (в этапе 2 — флаг `playable`) |
| Правовой риск аудио | Только аудио с правом использования (загрузка, сиды); лицензированный источник превью — до публичного запуска (ADR 0014) |
| Неофициальный клиент Яндекс Музыки ломается или блокируется | Выключен по умолчанию, отдельный адаптер за портом, circuit breaker; каталог работает на других источниках |
| `pg_trgm` и `citext` требуют суперпользователя | Создаются в init-скрипте (шаг 0) |
| Django ломает конвенции монорепо (import-linter, OpenAPI) | Поле `framework` в реестре (шаг 0) |
| ffmpeg и libvips в образе и CI | `apt` в реестре, установка в CI (шаг 0) |
| Отказ Redis при проверке denylist `sid` | Fail-open с метрикой, решение в CLAUDE.md identity |
| TMDB rate-limit и недоступность | Ретраи и бэк-офф, импорт асинхронный — вход и игра не страдают |

## 11. Порядок слияния

1. `stage1/contracts` (шаг 0) → `main`. Все потоки ветвятся от этого коммита.
2. **D1 (edge)**; затем оркестратор: `gv gen --check` и полный `gv test`.
3. **A (identity и libs/auth)** — от него зависят CSRF во всех сервисах и вход в админку; после слияния — `finecomb`.
4. **B (catalog)**, затем **C (media-pipeline)** (C тестируется на примерах событий B; порядок B → C нужен только для живой демонстрации).
5. **D2 (admin)** — последним.

После каждого слияния оркестратор выполняет `uv lock --check`, `gv gen --check`, `gv contracts export && gv contracts check`, `gv test --changed`; в конце — полный `gv test` и живую демонстрацию из §0, обновляет `docs/09-roadmap.md`, `docs/architecture/services.md`, ADR 0006 и новый ADR по ссылкам (D4).

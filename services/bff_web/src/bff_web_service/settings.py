"""Настройки bff_web (переменные окружения ``GV_*``, вложенные — через ``__``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gv_service import ServiceSettings


class UpstreamSettings(BaseModel):
    """Адреса сервисов-источников и защита от их отказов (план этапа 2, §6).

    Attributes:
        game_url / catalog_url / identity_url / collections_url / library_url / match_url /
        rating_url / progression_url / notifications_url / social_url:
            Внутренние адреса (``GV_UPSTREAMS__GAME_URL``, ``GV_UPSTREAMS__MATCH_URL`` …).
        timeout_s: Общий таймаут одного вызова: медленный источник не держит страницу.
        max_concurrency: Одновременных вызовов на один запрос страницы.
        breaker_failures: Подряд неудачных вызовов, после которых сервис считается упавшим.
        breaker_reset_s: Сколько секунд не звать упавший сервис (затем — пробный вызов).
        max_connections: Пул соединений httpx на сервис.
    """

    model_config = ConfigDict(frozen=True)

    game_url: str = "http://game:8000"
    catalog_url: str = "http://catalog:8000"
    identity_url: str = "http://identity:8000"
    collections_url: str = "http://collections:8000"
    library_url: str = "http://library:8000"
    match_url: str = "http://match:8000"
    rating_url: str = "http://rating:8000"
    progression_url: str = "http://progression:8000"
    notifications_url: str = "http://notifications:8000"
    social_url: str = "http://social:8000"
    timeout_s: float = Field(default=1.5, gt=0, le=30)
    max_concurrency: int = Field(default=10, ge=1, le=100)
    breaker_failures: int = Field(default=5, ge=1)
    breaker_reset_s: float = Field(default=10, gt=0)
    max_connections: int = Field(default=100, ge=1)


class PageCacheSettings(BaseModel):
    """Кэш публичных блоков в Redis. Личные данные не кэшируются никогда.

    Attributes:
        modes_ttl_s: Список опубликованных режимов (``game /v1/modes``).
        item_ttl_s: Карточки catalog (``/v1/items/{id}``).
        collections_ttl_s: Витрины каталога сборников без ``q`` и курсора
            (``collections /v1/collections``).
        collection_modes_ttl_s: Режимы сборника с играбельностью
            (``game /v1/collections/{id}/modes``, не зависят от субъекта).
        leaderboard_ttl_s: Ladder-ы, общие лидерборд и топ недели (rating; 10 с, ADR 0039).
        online_count_ttl_s: Публичный счётчик «игроков онлайн» (social `/v1/presence/online-count`).
        key_prefix: Пространство имён ключей (несколько стендов или тесты на одном Redis).
        op_timeout_s: Предел одной операции с Redis; дольше — промах кэша.
    """

    model_config = ConfigDict(frozen=True)

    modes_ttl_s: int = Field(default=30, ge=1, le=3600)
    item_ttl_s: int = Field(default=300, ge=1, le=86_400)
    collections_ttl_s: int = Field(default=30, ge=1, le=3600)
    collection_modes_ttl_s: int = Field(default=30, ge=1, le=3600)
    leaderboard_ttl_s: int = Field(default=10, ge=1, le=600)
    online_count_ttl_s: int = Field(default=5, ge=1, le=60)
    active_event_ttl_s: int = Field(default=30, ge=1, le=30)
    event_progress_ttl_s: int = Field(default=15, ge=1, le=30)
    key_prefix: str = "bff_web:"
    op_timeout_s: float = Field(default=0.2, gt=0, le=5)


class HttpCacheSettings(BaseModel):
    """``Cache-Control`` публичных ответов (личные — всегда ``private, no-store``)."""

    model_config = ConfigDict(frozen=True)

    public_max_age_s: int = Field(default=30, ge=0, le=3600)
    stale_while_revalidate_s: int = Field(default=60, ge=0, le=3600)
    leaderboards_max_age_s: int = Field(default=10, ge=0, le=3600)


class PagesSettings(BaseModel):
    """Состав экранов (без хардкода в коде).

    Attributes:
        random_source_types: Типы Source, режимы которых попадают в блок «случайный» хаба.
        scheduled_source_types: Типы Source плановых режимов (набор периода из
            ``game /v1/daily``): их показывают блоки ``daily``/``weekly``, а не витрина
            ``modes``.
        weekly_source_types: Какие из плановых типов — недельные: их статусы уходят в блок
            ``weekly``, остальные статусы ``/v1/daily`` — в блок дейликов.
        collection_source_types: Типы Source режимов сборника: играются только со страницы
            сборника, в витрины ``/solo`` и главной не попадают.
        collections_showcase_limit: Карточек в каждой витрине каталога сборников
            (``featured``, ``popular``, ``new``).
        collections_results_limit: Карточек на странице результатов поиска сборников.
        library_items_limit: Единиц на странице полки медиатеки.
        online_rooms_limit: Открытых лобби в блоке хаба онлайна (дальше — курсор match).
        online_matches_limit: Последних матчей в блоке «Мои матчи» хаба онлайна.
        online_weekly_limit: Строк топа недели на ladder в хабе онлайна.
        online_match_players_limit: Скольким последним матчам хаба добавлять список игроков
            (по вызову `match /v1/matches/{id}` на матч; 0 — не добавлять, ADR 0069).
        relations_batch_size: Игроков в одном вызове `social /v1/players/relations` (предел
            источника — 50) для флага «можно добавить в друзья» итога матча.
        match_items_deadline_s: Общий предел на блоки ``items`` (catalog) и ``library``
            итога матча: до 50 единиц идут волнами по ``max_concurrency``, и без общего
            предела страница ждала бы ``волны × timeout_s``. Не успел — блок ``unavailable``.
        match_items_batch_size: Единиц в одном пакетном вызове catalog (``/v1/items?ids=``)
            и library (``/v1/items/shelves?ids=``) для итога матча; пределы источников — 50,
            больше единиц режутся на пачки.
        home_progress_deadline_s: Общий предел на три вызова progression для блока
            ``progress`` главной; не успел — блок ``unavailable``, остальная главная цела.
        default_theme_key: Ключ активной темы оболочки (ADR 0049: bff отдаёт только ключ,
            наборы токенов лежат во фронтенде; в 5d ключ придёт от сезонного события).
        appearance_batch_size: Игроков в одном вызове ``progression /v1/appearance``
            (предел источника — 12); больше игроков матча режутся на пачки.
        comments_limit: Комментариев в первой странице блока ``comments`` страницы сборника
            (дальше — курсор social напрямую; предел источника — 100).
        room_name_template: Название комнаты без имени от хоста (`{host}` — ник хоста).
        levels_batch_size: Игроков в одном вызове `progression /internal/v1/levels` (предел
            источника задаёт `GV_PROGRESSION__LEVELS_MAX_USERS`, по умолчанию 50).
        friends_scan_limit: Сколько друзей опрашивать на результаты дейлика для ленты главной
            и «среди друзей» итога (первые по списку social); счётчик «всего друзей» — по всем.
        friends_results_batch_size: Игроков в одном вызове ``game /v1/daily/results``; не больше
            ``GV_GAME__DAILY_RESULTS_MAX_USERS`` game (по умолчанию 50).
        friends_relations_batch_size: Игроков в одном вызове ``social /v1/players/relations``
            для видимости статистики друзей (предел источника — 50; лимит social считает
            вызовы, поэтому пачка крупнее ``relations_batch_size``).
        home_feed_deadline_s: Общий срок конвейера друзей (друзья, результаты, видимость,
            оформление) для ленты главной и блока «среди друзей» итога; не успел — блок
            ``unavailable``, остальная страница цела.
        home_feed_items_limit: Друзей в ``feed.items`` (лучшие первыми); счётчики — по всем.
        results_friends_limit: Друзей в ``friends_today.items`` итога дейлика.
        results_rewards_deadline_s: Срок блока ``rewards`` итога вместе с повторами.
        results_rewards_retries: Сколько раз переспросить progression, если награда ещё не
            начислена (начисление асинхронное); 0 — не переспрашивать.
        results_rewards_retry_delay_s: Пауза между повторами.
    """

    model_config = ConfigDict(frozen=True)

    random_source_types: tuple[str, ...] = ("random",)
    scheduled_source_types: tuple[str, ...] = ("daily", "bracket")
    weekly_source_types: tuple[str, ...] = ("bracket",)
    collection_source_types: tuple[str, ...] = ("collection",)
    collections_showcase_limit: int = Field(default=12, ge=1, le=50)
    collections_results_limit: int = Field(default=20, ge=1, le=50)
    library_items_limit: int = Field(default=50, ge=1, le=100)
    online_rooms_limit: int = Field(default=20, ge=1, le=50)
    online_matches_limit: int = Field(default=10, ge=1, le=50)
    online_weekly_limit: int = Field(default=5, ge=1, le=50)
    online_match_players_limit: int = Field(default=5, ge=0, le=50)
    relations_batch_size: int = Field(default=12, ge=1, le=50)
    match_items_deadline_s: float = Field(default=2.0, gt=0, le=30)
    match_items_batch_size: int = Field(default=50, ge=1, le=50)
    home_progress_deadline_s: float = Field(default=1.5, gt=0, le=30)
    default_theme_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    appearance_batch_size: int = Field(default=12, ge=1, le=12)
    comments_limit: int = Field(default=30, ge=1, le=100)
    room_name_template: str = Field(default="Комната {host}", min_length=1, max_length=60)
    levels_batch_size: int = Field(default=50, ge=1, le=100)

    @field_validator("room_name_template")
    @classmethod
    def _template_has_host(cls, value: str) -> str:
        if "{host}" not in value:
            raise ValueError("шаблон названия комнаты должен содержать {host}")
        return value

    friends_scan_limit: int = Field(default=100, ge=1, le=500)
    friends_results_batch_size: int = Field(default=50, ge=1, le=50)
    friends_relations_batch_size: int = Field(default=50, ge=1, le=50)
    home_feed_deadline_s: float = Field(default=1.5, gt=0, le=30)
    home_feed_items_limit: int = Field(default=10, ge=1, le=50)
    results_friends_limit: int = Field(default=5, ge=1, le=50)
    results_rewards_deadline_s: float = Field(default=2.0, gt=0, le=30)
    results_rewards_retries: int = Field(default=2, ge=0, le=5)
    results_rewards_retry_delay_s: float = Field(default=0.5, ge=0, le=5)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "bff_web"
    upstreams: UpstreamSettings = UpstreamSettings()
    page_cache: PageCacheSettings = PageCacheSettings()
    http_cache: HttpCacheSettings = HttpCacheSettings()
    pages: PagesSettings = PagesSettings()

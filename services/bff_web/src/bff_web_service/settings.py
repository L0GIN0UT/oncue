"""Настройки bff_web (переменные окружения ``GV_*``, вложенные — через ``__``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gv_service import ServiceSettings


class UpstreamSettings(BaseModel):
    """Адреса сервисов-источников и защита от их отказов (план этапа 2, §6).

    Attributes:
        game_url / catalog_url / identity_url / collections_url / library_url / match_url /
        rating_url / progression_url:
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
        key_prefix: Пространство имён ключей (несколько стендов или тесты на одном Redis).
        op_timeout_s: Предел одной операции с Redis; дольше — промах кэша.
    """

    model_config = ConfigDict(frozen=True)

    modes_ttl_s: int = Field(default=30, ge=1, le=3600)
    item_ttl_s: int = Field(default=300, ge=1, le=86_400)
    collections_ttl_s: int = Field(default=30, ge=1, le=3600)
    collection_modes_ttl_s: int = Field(default=30, ge=1, le=3600)
    leaderboard_ttl_s: int = Field(default=10, ge=1, le=600)
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
        match_items_deadline_s: Общий предел на блоки ``items`` (catalog) и ``library``
            итога матча: до 50 единиц идут волнами по ``max_concurrency``, и без общего
            предела страница ждала бы ``волны × timeout_s``. Не успел — блок ``unavailable``.
        home_progress_deadline_s: Общий предел на три вызова progression для блока
            ``progress`` главной; не успел — блок ``unavailable``, остальная главная цела.
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
    match_items_deadline_s: float = Field(default=2.0, gt=0, le=30)
    home_progress_deadline_s: float = Field(default=1.5, gt=0, le=30)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "bff_web"
    upstreams: UpstreamSettings = UpstreamSettings()
    page_cache: PageCacheSettings = PageCacheSettings()
    http_cache: HttpCacheSettings = HttpCacheSettings()
    pages: PagesSettings = PagesSettings()

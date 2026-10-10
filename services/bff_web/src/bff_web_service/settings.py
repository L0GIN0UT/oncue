"""Настройки bff_web (переменные окружения ``GV_*``, вложенные — через ``__``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gv_service import ServiceSettings


class UpstreamSettings(BaseModel):
    """Адреса сервисов-источников и защита от их отказов (план этапа 2, §6).

    Attributes:
        game_url / catalog_url / identity_url: Внутренние адреса (``GV_UPSTREAMS__GAME_URL``).
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
        key_prefix: Пространство имён ключей (несколько стендов или тесты на одном Redis).
        op_timeout_s: Предел одной операции с Redis; дольше — промах кэша.
    """

    model_config = ConfigDict(frozen=True)

    modes_ttl_s: int = Field(default=30, ge=1, le=3600)
    item_ttl_s: int = Field(default=300, ge=1, le=86_400)
    key_prefix: str = "bff_web:"
    op_timeout_s: float = Field(default=0.2, gt=0, le=5)


class HttpCacheSettings(BaseModel):
    """``Cache-Control`` публичных ответов (личные — всегда ``private, no-store``)."""

    model_config = ConfigDict(frozen=True)

    public_max_age_s: int = Field(default=30, ge=0, le=3600)
    stale_while_revalidate_s: int = Field(default=60, ge=0, le=3600)


class PagesSettings(BaseModel):
    """Состав экранов (без хардкода в коде).

    Attributes:
        random_source_types: Типы Source, режимы которых попадают в блок «случайный» хаба.
    """

    model_config = ConfigDict(frozen=True)

    random_source_types: tuple[str, ...] = ("random",)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "bff_web"
    upstreams: UpstreamSettings = UpstreamSettings()
    page_cache: PageCacheSettings = PageCacheSettings()
    http_cache: HttpCacheSettings = HttpCacheSettings()
    pages: PagesSettings = PagesSettings()

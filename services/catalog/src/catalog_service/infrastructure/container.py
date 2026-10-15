"""Сборка зависимостей catalog (без DI-фреймворка, ADR 0011).

Всё создаётся лениво: ``gv contracts export`` строит приложение на «пустой»
``Infrastructure`` (без БД и Redis), и роутеры не должны ничего подключать заранее.
"""

from __future__ import annotations

import asyncio
from functools import cached_property
from typing import Any

import httpx
import structlog

from catalog_service.application.commands.admin import (
    ApproveClueTexts,
    BlockItem,
    EditClueTexts,
    GetClueTexts,
    ReplaceAliases,
    UnblockItem,
)
from catalog_service.application.commands.import_sources import (
    FileEntrySource,
    FilmEntrySource,
    MusicEntrySource,
)
from catalog_service.application.commands.imports import (
    RequestImport,
    RunImport,
    UpsertImportedItem,
)
from catalog_service.application.commands.republish import RequestRepublish, RunRepublishBatch
from catalog_service.application.ports import ImportEntrySource, MusicMetadataProvider
from catalog_service.application.queries.search import (
    Autocomplete,
    GetImport,
    GetItem,
    ListItems,
    Search,
    SearchLimits,
)
from catalog_service.domain.import_job import ImportSource
from catalog_service.domain.normalization import NormalizerConfig, TitleNormalizer
from catalog_service.infrastructure.clients.music import (
    MusicBrainzProvider,
    SpotifyMetadataProvider,
    YandexMusicMetadataProvider,
)
from catalog_service.infrastructure.clients.resilience import CircuitBreaker, ResilientHttp
from catalog_service.infrastructure.clients.tmdb import TmdbClient
from catalog_service.infrastructure.db.repositories import CatalogSqlUnitOfWork
from catalog_service.infrastructure.files import FileImportProvider
from catalog_service.infrastructure.search.opensearch import OpenSearchIndex
from catalog_service.infrastructure.search.redis_cache import RedisPrefixCache, RedisRateLimiter
from catalog_service.infrastructure.search.trgm import TrgmSearch
from catalog_service.settings import SEED_ENVIRONMENTS, Settings
from gv_messaging.outbox import OutboxWriter
from gv_messaging.rabbit import RabbitTaskQueue
from gv_service import Infrastructure

log = structlog.get_logger(__name__)


class LazyTaskQueue:
    """RabbitMQ-очередь, которая подключается при первой постановке задачи."""

    def __init__(self, url: str, service: str) -> None:
        self._queue = RabbitTaskQueue(url, service)
        self._started = False
        self._lock = asyncio.Lock()

    async def enqueue(self, task_type: str, payload: dict[str, Any], *, task_id: str) -> None:
        """Поставить задачу."""
        async with self._lock:
            if not self._started:
                await self._queue.start()
                self._started = True
        await self._queue.enqueue(task_type, payload, task_id=task_id)

    async def close(self) -> None:
        """Закрыть соединение, если оно было открыто."""
        if self._started:
            await self._queue.stop()
            self._started = False


class CatalogContainer:
    """Адаптеры и use-cases одного процесса."""

    def __init__(self, infra: Infrastructure) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки catalog_service.settings.Settings")
        self.infra = infra
        self.settings: Settings = infra.settings
        self._closables: list[Any] = []

    # ------------------------------------------------------------------ базовое

    @property
    def service_name(self) -> str:
        """Имя сервиса."""
        return self.settings.service_name

    @cached_property
    def normalizer(self) -> TitleNormalizer:
        """Нормализатор названий."""
        return TitleNormalizer(NormalizerConfig(articles=self.settings.normalizer.articles))

    def uow(self) -> CatalogSqlUnitOfWork:
        """Новый Unit of Work."""
        infra = self.infra
        if infra.sessions is None or infra.tables is None:
            raise RuntimeError("у catalog нет БД: задайте GV_DATABASE_URL")
        return CatalogSqlUnitOfWork(
            infra.sessions,
            OutboxWriter(infra.tables.outbox),
            producer=self.settings.service_name,
            clock=infra.clock,
        )

    @cached_property
    def cache(self) -> RedisPrefixCache | None:
        """Кэш префиксов (без Redis — нет кэша)."""
        if self.infra.redis is None:
            return None
        ac = self.settings.autocomplete
        return RedisPrefixCache(self.infra.redis, prefix=ac.key_prefix, ttl_s=ac.cache_ttl_s)

    @cached_property
    def rate_limiter(self) -> RedisRateLimiter | None:
        """Rate-limit автокомплита (без Redis — выключен)."""
        if self.infra.redis is None:
            return None
        ac = self.settings.autocomplete
        return RedisRateLimiter(
            self.infra.redis,
            self.infra.clock,
            prefix=ac.key_prefix,
            limit_per_minute=ac.rate_limit_per_minute,
        )

    @cached_property
    def search_index(self) -> OpenSearchIndex | None:
        """OpenSearch или ``None`` (поиск — на pg_trgm)."""
        os_settings = self.settings.opensearch
        if not os_settings.url:
            return None
        index = OpenSearchIndex(os_settings.url, os_settings.index, timeout_s=os_settings.timeout_s)
        self._closables.append(index)
        return index

    @cached_property
    def trgm(self) -> TrgmSearch:
        """Fallback-поиск."""
        if self.infra.sessions is None:
            raise RuntimeError("у catalog нет БД: задайте GV_DATABASE_URL")
        return TrgmSearch(self.infra.sessions)

    @cached_property
    def tasks(self) -> LazyTaskQueue:
        """Очередь задач импорта."""
        if self.settings.rabbitmq_url is None:
            raise RuntimeError("для импорта нужен GV_RABBITMQ_URL")
        queue = LazyTaskQueue(self.settings.rabbitmq_url.get_secret_value(), "catalog")
        self._closables.append(queue)
        return queue

    @property
    def limits(self) -> SearchLimits:
        """Лимиты поиска из настроек."""
        ac = self.settings.autocomplete
        return SearchLimits(
            max_query_len=ac.max_query_len,
            min_prefix=ac.min_prefix,
            max_autocomplete=ac.max_limit,
            max_search=ac.max_search_limit,
        )

    # ------------------------------------------------------------------ источники

    def _http(
        self, name: str, base_url: str, *, rps: float, timeout_s: float, headers: dict[str, str]
    ) -> ResilientHttp:
        c = self.settings.circuit
        http = ResilientHttp(
            name,
            httpx.AsyncClient(base_url=base_url, timeout=timeout_s, headers=headers),
            rps=rps,
            breaker=CircuitBreaker(name, c.failure_threshold, c.open_seconds),
            max_retries=c.max_retries,
            backoff_s=c.backoff_s,
        )
        self._closables.append(http)
        return http

    @cached_property
    def film_provider(self) -> TmdbClient | None:
        """TMDB, если включён и задан токен."""
        t = self.settings.tmdb
        if not t.enabled or t.api_token is None:
            return None
        http = self._http(
            "tmdb",
            t.base_url,
            rps=t.rps,
            timeout_s=t.timeout_s,
            headers={"Authorization": f"Bearer {t.api_token.get_secret_value()}"},
        )
        return TmdbClient(
            http,
            language=t.language,
            fallback_language=t.fallback_language,
            image_base_url=t.image_base_url,
            frames_max=self.settings.catalog.tmdb_frames_max,
            license_note=self.settings.catalog.tmdb_license_note,
        )

    @cached_property
    def music_providers(self) -> dict[ImportSource, MusicMetadataProvider]:
        """Включённые музыкальные источники."""
        s = self.settings
        providers: dict[ImportSource, MusicMetadataProvider] = {}
        if s.musicbrainz.enabled:
            mb = s.musicbrainz
            providers[ImportSource.MUSICBRAINZ] = MusicBrainzProvider(
                self._http(
                    "musicbrainz",
                    mb.base_url,
                    rps=mb.rps,
                    timeout_s=mb.timeout_s,
                    headers={"User-Agent": mb.user_agent, "Accept": "application/json"},
                )
            )
        sp = s.spotify
        if sp.enabled and sp.client_id is not None and sp.client_secret is not None:
            providers[ImportSource.SPOTIFY] = SpotifyMetadataProvider(
                self._http("spotify", sp.base_url, rps=sp.rps, timeout_s=sp.timeout_s, headers={}),
                self._http(
                    "spotify-auth", sp.token_url, rps=sp.rps, timeout_s=sp.timeout_s, headers={}
                ),
                client_id=sp.client_id.get_secret_value(),
                client_secret=sp.client_secret.get_secret_value(),
                token_url=sp.token_url,
                market=sp.market,
            )
        ym = s.yandex_music
        if ym.enabled:
            headers = {"Authorization": f"OAuth {ym.token.get_secret_value()}"} if ym.token else {}
            providers[ImportSource.YANDEX_MUSIC] = YandexMusicMetadataProvider(
                self._http(
                    "yandex_music", ym.base_url, rps=ym.rps, timeout_s=ym.timeout_s, headers=headers
                )
            )
        return providers

    @cached_property
    def import_sources(self) -> dict[ImportSource, ImportEntrySource]:
        """Реестр источников импорта: файл — всегда, внешние — если включены и настроены.

        Собирается при первом обращении: тесты успевают подменить провайдеры.
        """
        sources: dict[ImportSource, ImportEntrySource] = {
            ImportSource.FILE: FileEntrySource(
                FileImportProvider(
                    self.settings.imports.files_dir,
                    max_lines=self.settings.imports.max_file_lines,
                    texts_approved=self.settings.catalog.seed_texts_approved,
                    require_provenance=self.settings.environment not in SEED_ENVIRONMENTS,
                )
            )
        }
        if self.film_provider is not None:
            sources[ImportSource.TMDB] = FilmEntrySource(self.film_provider)
        search_limit = self.settings.imports.search_limit
        for source, provider in self.music_providers.items():
            sources[source] = MusicEntrySource(provider, search_limit)
        return sources

    # ------------------------------------------------------------------ use-cases

    @cached_property
    def autocomplete(self) -> Autocomplete:
        """Автокомплит."""
        return Autocomplete(self.search_index, self.trgm, self.cache, self.normalizer, self.limits)

    @cached_property
    def search(self) -> Search:
        """Поиск."""
        return Search(self.search_index, self.trgm, self.normalizer, self.limits)

    @cached_property
    def get_item(self) -> GetItem:
        """Карточка."""
        return GetItem(self.uow)

    @cached_property
    def list_items(self) -> ListItems:
        """Список для админки."""
        return ListItems(self.uow, self.normalizer)

    @cached_property
    def get_import(self) -> GetImport:
        """Состояние импорта."""
        return GetImport(self.uow)

    @cached_property
    def block_item(self) -> BlockItem:
        """Блокировка."""
        return BlockItem(self.uow, self.cache)

    @cached_property
    def unblock_item(self) -> UnblockItem:
        """Разблокировка."""
        return UnblockItem(self.uow, self.cache)

    @cached_property
    def replace_aliases(self) -> ReplaceAliases:
        """Замена алиасов."""
        return ReplaceAliases(self.uow, self.cache, self.normalizer)

    @cached_property
    def get_clue_texts(self) -> GetClueTexts:
        """Тексты загадок (чтение)."""
        return GetClueTexts(self.uow)

    @cached_property
    def edit_clue_texts(self) -> EditClueTexts:
        """Правка текстов загадок."""
        return EditClueTexts(self.uow, None)

    @cached_property
    def approve_clue_texts(self) -> ApproveClueTexts:
        """Одобрение текстов загадок."""
        return ApproveClueTexts(self.uow, None)

    @cached_property
    def request_republish(self) -> RequestRepublish:
        """Постановка переопубликации."""
        return RequestRepublish(self.uow, self.tasks)

    @cached_property
    def run_republish(self) -> RunRepublishBatch:
        """Порция переопубликации (воркер)."""
        return RunRepublishBatch(self.uow, self.tasks, batch=self.settings.catalog.republish_batch)

    @cached_property
    def request_import(self) -> RequestImport:
        """Постановка импорта."""
        return RequestImport(self.uow, self.tasks, self.import_sources)

    @cached_property
    def run_import(self) -> RunImport:
        """Выполнение импорта (воркер)."""
        return RunImport(
            self.uow,
            UpsertImportedItem(self.uow, self.normalizer, self.infra.clock),
            self.import_sources,
        )

    async def close(self) -> None:
        """Закрыть созданные клиенты."""
        for closable in reversed(self._closables):
            try:
                if hasattr(closable, "aclose"):
                    await closable.aclose()
                else:
                    await closable.close()
            except Exception:
                log.warning("catalog.close_failed", exc_info=True)
        self._closables.clear()


_ATTR = "_catalog_container"


def container_for(infra: Infrastructure) -> CatalogContainer:
    """Один контейнер на процесс: api, consumer и worker в режиме ``all`` делят клиенты.

    Хранится атрибутом ``Infrastructure`` (у сервиса нет своего хука жизненного цикла);
    тесты подменяют адаптеры у этого же экземпляра до сборки приложения.
    """
    existing = getattr(infra, _ATTR, None)
    if isinstance(existing, CatalogContainer):
        return existing
    container = CatalogContainer(infra)
    setattr(infra, _ATTR, container)
    return container

"""Сборка use-cases с адаптерами (composition): общая для API, consumer и воркера.

Адаптеры создаются без подключений: экспорт OpenAPI и тесты собирают сервис без
инфраструктуры, а недостающая зависимость проявляется 503 при первом обращении.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.clock import Clock
from gv_core.errors import DependencyUnavailableError
from gv_messaging.ports import TaskQueue
from gv_service import Environment, Infrastructure
from media_pipeline_service.application.commands.catalog import (
    DisableItemAssets,
    PlanAssetSets,
    RegisterSourceFromCatalog,
    RestoreItemAssets,
    SyncMediaItem,
)
from media_pipeline_service.application.commands.collections import SyncCollectionAssets
from media_pipeline_service.application.commands.generation import (
    GenerateAssetSet,
    RegenerateAssetSet,
)
from media_pipeline_service.application.commands.links import IssueAssetLink, PurgeLinkJournal
from media_pipeline_service.application.commands.profiles import (
    ApplyClueAssetsRequested,
    BackfillDynamicProfiles,
)
from media_pipeline_service.application.commands.sources import SetSourcePosition, UploadSource
from media_pipeline_service.application.commands.ugc import (
    ModerateUgcImage,
    PreviewUgcImage,
    SubmitUgcImage,
    SweepOrphanUgcObjects,
    UgcObjectPurger,
    UgcUseCases,
)
from media_pipeline_service.application.generators import (
    AudioBlackoutGenerator,
    AudioIntroGenerator,
    AudioReverseGenerator,
    DescriptionGenerator,
    EmojiGenerator,
    FrameProgressiveGenerator,
    FrameSequenceGenerator,
    GeneratorRegistry,
    PosterMaskedGenerator,
    SoundtrackGenerator,
)
from media_pipeline_service.application.ports import (
    ImageProcessor,
    LinkRateLimiter,
    ObjectStorage,
    SourceFetcher,
    TokenSource,
    Transcoder,
    UgcImageEncoder,
    UnitOfWorkFactory,
    UrlSigner,
)
from media_pipeline_service.application.profiles import ProfileDirectory
from media_pipeline_service.application.queries.asset_sets import ListAssetSets
from media_pipeline_service.application.queries.profiles import ListProfiles
from media_pipeline_service.application.queries.sources import ListSources
from media_pipeline_service.application.queries.ugc import ListUgcQueue
from media_pipeline_service.application.sources import SourceIngestor
from media_pipeline_service.domain.links import LinkPolicy
from media_pipeline_service.domain.profiles import AssetProfileCatalog
from media_pipeline_service.domain.sources import SourceKind
from media_pipeline_service.domain.ugc import UgcCatalog
from media_pipeline_service.infrastructure.config import load_profiles, load_ugc_catalog
from media_pipeline_service.infrastructure.db.repositories import SqlMediaUnitOfWork
from media_pipeline_service.infrastructure.fetcher import HttpSourceFetcher
from media_pipeline_service.infrastructure.ffmpeg import FfmpegTranscoder
from media_pipeline_service.infrastructure.images import PillowImageProcessor
from media_pipeline_service.infrastructure.links import (
    NginxSecureLinkSigner,
    RedisLinkRateLimiter,
    SecretsTokenSource,
    UnconfiguredSigner,
    limiter_unavailable,
)
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.infrastructure.tasks import LazyRabbitTaskQueue
from media_pipeline_service.infrastructure.ugc_images import PillowUgcEncoder
from media_pipeline_service.settings import Settings

_MB = 1024 * 1024
# Окружения, где загрузка без происхождения (D6) допустима: синтетика засева и тесты.
DEV_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})


class _NoRedisLimiter:
    def __init__(self, retry_after_s: int) -> None:
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> int:  # noqa: ARG002 — сигнатура порта
        raise limiter_unavailable(self._retry_after_s, "лимитер ссылок не настроен (GV_REDIS_URL)")


@dataclass
class MediaAdapters:
    """Адаптеры портов. Тесты подменяют отдельные поля (например, очередь задач)."""

    storage: ObjectStorage
    fetcher: SourceFetcher
    transcoder: Transcoder
    images: ImageProcessor
    ugc_encoder: UgcImageEncoder
    signer: UrlSigner
    limiter: LinkRateLimiter
    queue: TaskQueue
    tokens: TokenSource = field(default_factory=SecretsTokenSource)

    @classmethod
    def from_settings(cls, infra: Infrastructure, settings: Settings) -> MediaAdapters:
        """Боевые адаптеры по настройкам."""
        m = settings.media
        secret = m.signing_secret.get_secret_value() if m.signing_secret else None
        signer: UrlSigner = (
            NginxSecureLinkSigner(
                secret, public_base_url=m.public_base_url, path_prefix=m.media_path_prefix
            )
            if secret
            else UnconfiguredSigner()
        )
        return cls(
            storage=S3Storage(settings.s3),
            fetcher=HttpSourceFetcher(
                allowed_hosts=m.source_host_list,
                max_bytes=m.max_source_mb * _MB,
                timeout_s=m.fetch_timeout_s,
            ),
            transcoder=FfmpegTranscoder(
                ffmpeg_bin=settings.ffmpeg.bin,
                ffprobe_bin=settings.ffmpeg.probe_bin,
                timeout_s=settings.ffmpeg.timeout_s,
            ),
            images=PillowImageProcessor(max_pixels=m.max_image_pixels, webp_quality=m.webp_quality),
            ugc_encoder=PillowUgcEncoder(
                webp_quality=m.webp_quality, concurrency=m.ugc_encode_concurrency
            ),
            signer=signer,
            limiter=(
                RedisLinkRateLimiter(infra.redis, retry_after_s=m.link_limiter_retry_after_s)
                if infra.redis
                else _NoRedisLimiter(m.link_limiter_retry_after_s)
            ),
            queue=LazyRabbitTaskQueue(
                settings.rabbitmq_url.get_secret_value() if settings.rabbitmq_url else None,
                settings.service_name,
            ),
        )


class MediaContainer:
    """Use-cases сервиса поверх инфраструктуры процесса."""

    def __init__(self, infra: Infrastructure, adapters: MediaAdapters | None = None) -> None:
        if not isinstance(infra.settings, Settings):
            raise TypeError("ожидаются настройки media_pipeline")
        self.infra = infra
        self.settings: Settings = infra.settings
        self.adapters = adapters or MediaAdapters.from_settings(infra, self.settings)

    @property
    def clock(self) -> Clock:
        """Часы процесса."""
        return self.infra.clock

    @cached_property
    def registry(self) -> GeneratorRegistry:
        """Реестр генераторов по ClueType."""
        a = self.adapters
        return GeneratorRegistry(
            [
                AudioIntroGenerator(a.transcoder),
                AudioReverseGenerator(a.transcoder),
                SoundtrackGenerator(a.transcoder),
                AudioBlackoutGenerator(a.transcoder),
                FrameProgressiveGenerator(a.images),
                FrameSequenceGenerator(a.images),
                PosterMaskedGenerator(a.images, max_width=self.settings.media.poster_max_width),
                DescriptionGenerator(),
                EmojiGenerator(),
            ]
        )

    @cached_property
    def profiles(self) -> AssetProfileCatalog:
        """Профили уровней (проверяются при первом обращении — на старте процесса)."""
        return load_profiles(self.registry, self.settings.media.profiles_path)

    @cached_property
    def directory(self) -> ProfileDirectory:
        """Статические ∪ динамические профили (ADR 0025)."""
        return ProfileDirectory(self.profiles, self.registry)

    def uow_factory(self, session: AsyncSession | None = None) -> UnitOfWorkFactory:
        """Фабрика UoW: своя транзакция или SAVEPOINT в транзакции consumer."""
        infra = self.infra
        producer = self.settings.service_name

        def factory() -> SqlMediaUnitOfWork:
            if session is not None:
                return SqlMediaUnitOfWork(producer=producer, clock=infra.clock, session=session)
            if infra.sessions is None:
                raise DependencyUnavailableError("база данных не настроена (GV_DATABASE_URL)")
            return SqlMediaUnitOfWork(producer=producer, clock=infra.clock, sessions=infra.sessions)

        return factory

    @cached_property
    def ingestor(self) -> SourceIngestor:
        """Приём исходников."""
        a = self.adapters
        return SourceIngestor(
            a.storage,
            {
                SourceKind.AUDIO: a.transcoder,
                SourceKind.SOUNDTRACK: a.transcoder,
                SourceKind.AUDIO_CLIP: a.transcoder,
                SourceKind.FRAME: a.images,
                SourceKind.POSTER: a.images,
            },
            a.tokens,
            max_bytes=self.settings.media.max_source_mb * _MB,
        )

    # ------------------------------------------------------------------ use-cases
    def plan(self, uow: UnitOfWorkFactory | None = None) -> PlanAssetSets:
        """Планирование наборов."""
        a = self.adapters
        return PlanAssetSets(
            uow or self.uow_factory(),
            self.clock,
            self.directory,
            a.queue,
            a.storage,
            registry=self.registry,
            scoped_clue_types=self.settings.media.scoped_clue_types,
        )

    def sync_collection(self, uow: UnitOfWorkFactory) -> SyncCollectionAssets:
        """Обработка событий collections (``uow`` привязан к сессии consumer)."""
        return SyncCollectionAssets(
            uow,
            self.clock,
            self.plan(uow),
            self.adapters.storage,
            emoji_max_graphemes=self.settings.media.collection_emoji_max_graphemes,
        )

    def sync_media_item(self, uow: UnitOfWorkFactory) -> SyncMediaItem:
        """Обработка событий catalog (``uow`` привязан к сессии consumer)."""
        a = self.adapters
        return SyncMediaItem(
            uow,
            self.clock,
            register_sources=RegisterSourceFromCatalog(uow, self.clock),
            plan=self.plan(uow),
            disable=DisableItemAssets(uow, self.clock, a.storage),
            restore=RestoreItemAssets(uow, self.clock, a.queue),
        )

    @cached_property
    def generate(self) -> GenerateAssetSet:
        """Задача генерации."""
        a = self.adapters
        return GenerateAssetSet(
            self.uow_factory(),
            self.clock,
            registry=self.registry,
            storage=a.storage,
            fetcher=a.fetcher,
            ingestor=self.ingestor,
            tokens=a.tokens,
            max_attempts=self.settings.media.generate_max_attempts,
            max_source_bytes=self.settings.media.max_source_mb * _MB,
        )

    @cached_property
    def regenerate(self) -> RegenerateAssetSet:
        """Перегенерация админом."""
        a = self.adapters
        return RegenerateAssetSet(self.uow_factory(), self.clock, a.queue, a.storage)

    @cached_property
    def upload(self) -> UploadSource:
        """Загрузка исходника админом."""
        return UploadSource(
            self.uow_factory(),
            self.clock,
            self.ingestor,
            self.directory,
            self.plan(),
            storage=self.adapters.storage,
            require_provenance=self.settings.environment not in DEV_ENVIRONMENTS,
        )

    @cached_property
    def set_position(self) -> SetSourcePosition:
        """Порядок исходника (admin)."""
        return SetSourcePosition(
            self.uow_factory(), self.clock, self.plan(), storage=self.adapters.storage
        )

    @cached_property
    def issue_link(self) -> IssueAssetLink:
        """Выдача ссылок."""
        m = self.settings.media
        return IssueAssetLink(
            self.uow_factory(),
            self.clock,
            self.adapters.signer,
            self.adapters.limiter,
            LinkPolicy(
                ttl_s=m.link_ttl_s, max_per_window=m.link_max_per_window, window_s=m.link_window_s
            ),
        )

    @cached_property
    def purge_links(self) -> PurgeLinkJournal:
        """Очистка журнала выдачи ссылок (периодическая задача relay)."""
        m = self.settings.media
        return PurgeLinkJournal(
            self.uow_factory(),
            self.clock,
            retention=timedelta(days=m.link_issuance_retention_days),
            batch=m.link_purge_batch,
        )

    def apply_clue_assets_requested(self, uow: UnitOfWorkFactory) -> ApplyClueAssetsRequested:
        """Заведение динамического профиля (``uow`` привязан к сессии consumer)."""
        m = self.settings.media
        return ApplyClueAssetsRequested(
            uow,
            self.clock,
            self.directory,
            max_profiles=m.max_dynamic_profiles,
            max_params_bytes=m.dynamic_params_max_bytes,
        )

    @cached_property
    def backfill(self) -> BackfillDynamicProfiles:
        """Досоздание наборов динамических профилей (периодическая задача relay)."""
        return BackfillDynamicProfiles(
            self.uow_factory(),
            self.clock,
            self.directory,
            self.plan(),
            self.adapters.storage,
            batch=self.settings.media.backfill_batch,
        )

    @cached_property
    def list_profiles(self) -> ListProfiles:
        """Профили для admin-API."""
        return ListProfiles(self.uow_factory(), self.directory)

    @cached_property
    def list_sets(self) -> ListAssetSets:
        """Список наборов."""
        return ListAssetSets(self.uow_factory())

    @cached_property
    def list_sources(self) -> ListSources:
        """Исходники единицы (admin)."""
        return ListSources(self.uow_factory())

    @cached_property
    def ugc_catalog(self) -> UgcCatalog:
        """Назначения и причины UGC (проверяются при первом обращении)."""
        return load_ugc_catalog(self.settings.media.ugc_purposes_path)

    @cached_property
    def ugc(self) -> UgcUseCases:
        """Use-cases UGC-изображений (приём, решения, очередь)."""
        a = self.adapters
        m = self.settings.media
        purger = UgcObjectPurger(self.uow_factory(), a.storage, batch=m.ugc_purge_batch)
        return UgcUseCases(
            catalog=self.ugc_catalog,
            submit=SubmitUgcImage(
                self.uow_factory(),
                self.clock,
                a.ugc_encoder,
                a.storage,
                a.tokens,
                self.ugc_catalog,
                purger,
            ),
            moderate=ModerateUgcImage(
                self.uow_factory(), self.clock, a.storage, a.tokens, self.ugc_catalog, purger
            ),
            preview=PreviewUgcImage(
                self.uow_factory(),
                self.clock,
                a.signer,
                ttl_s=m.ugc_preview_ttl_s,
                public_base_url=m.ugc_public_base_url,
            ),
            queue=ListUgcQueue(self.uow_factory()),
            purger=purger,
            orphans=SweepOrphanUgcObjects(
                self.uow_factory(),
                a.storage,
                self.clock,
                ttl=timedelta(hours=m.ugc_orphan_ttl_h),
            ),
        )

    async def close(self) -> None:
        """Закрыть клиенты, открытые лениво (соединение RabbitMQ очереди задач)."""
        if isinstance(self.adapters.queue, LazyRabbitTaskQueue):
            await self.adapters.queue.close()


_ATTR = "_media_container"


def container_for(infra: Infrastructure, adapters: MediaAdapters | None = None) -> MediaContainer:
    """Один контейнер на процесс: api, consumer и worker в режиме ``all`` делят адаптеры
    (и одно соединение RabbitMQ), а закрываются они в ``Infrastructure.close`` (``add_closer``).

    Хранится атрибутом ``Infrastructure``. Явные ``adapters`` (тесты) создают отдельный
    контейнер (тоже закрывается в ``Infrastructure.close``).
    """
    existing = getattr(infra, _ATTR, None)
    if adapters is None and isinstance(existing, MediaContainer):
        return existing
    container = MediaContainer(infra, adapters)
    if adapters is None:
        setattr(infra, _ATTR, container)
    # Закрытие — вместе с процессом для любой команды (api, consumer, worker, relay).
    infra.add_closer(container.close)
    return container

"""Настройки media_pipeline (переменные окружения ``GV_*``)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from gv_service import ServiceSettings


class S3Settings(BaseModel):
    """Приватное S3-совместимое хранилище (SeaweedFS на VPS, ADR 0021): ``GV_S3__*``."""

    model_config = ConfigDict(frozen=True)

    endpoint: str | None = None
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    region: str = "us-east-1"
    bucket_sources: str = "gv-sources"
    bucket_assets: str = "gv-assets"
    timeout_s: float = Field(default=10.0, gt=0)


class MediaSettings(BaseModel):
    """Ассеты, ссылки и исходники: ``GV_MEDIA__*``."""

    model_config = ConfigDict(frozen=True)

    signer: Literal["nginx_secure_link"] = "nginx_secure_link"
    # Общий с gateway секрет secure_link (ADR 0012). Без него ссылки не выдаются (503).
    signing_secret: SecretStr | None = None
    link_ttl_s: int = Field(default=60, ge=1, le=3600)
    link_max_per_window: int = Field(default=10, ge=1)
    link_window_s: int = Field(default=60, ge=1)
    link_issuance_retention_days: int = Field(default=7, ge=1)
    # Задача relay ``media.link_issuance.purge``: пауза между запусками, джиттер, порция.
    link_purge_interval_s: float = Field(default=600, gt=0)
    link_purge_jitter_s: float = Field(default=60, ge=0)
    link_purge_batch: int = Field(default=1000, ge=1, le=100_000)
    # Через сколько секунд повторить выдачу ссылки, если лимитер (Redis) недоступен (503).
    link_limiter_retry_after_s: int = Field(default=5, ge=1, le=300)
    # Пусто — относительные ссылки (тот же origin, что у сайта).
    public_base_url: str = ""
    # Префикс location на gateway, который проксирует в бакет ассетов.
    media_path_prefix: str = Field(default="/media/", pattern=r"^/([a-z0-9_-]+/)+$")
    # Хосты, с которых разрешено скачивать исходники из каталога (через запятую).
    source_hosts: str = "image.tmdb.org"
    max_source_mb: int = Field(default=50, ge=1, le=1024)
    max_upload_mb: int = Field(default=50, ge=1, le=1024)
    fetch_timeout_s: float = Field(default=20.0, gt=0)
    max_image_pixels: int = Field(default=40_000_000, ge=1_000)
    poster_max_width: int = Field(default=720, ge=64, le=4096)
    webp_quality: int = Field(default=80, ge=1, le=100)
    # None — встроенный media_pipeline_service/config/asset_profiles.yaml.
    profiles_path: Path | None = None
    # Не больше числа ретраев очереди + 1 (gv_messaging.rabbit.DEFAULT_RETRY_DELAYS: 4).
    generate_max_attempts: int = Field(default=4, ge=1, le=5)
    # Динамические профили по game.ClueAssetsRequested (ADR 0025): лимит неотклонённых,
    # предел размера params (канонический JSON) и досоздание наборов задачей relay
    # media.profiles.backfill — порция единиц за запуск, пауза и джиттер.
    max_dynamic_profiles: int = Field(default=50, ge=0, le=1000)
    dynamic_params_max_bytes: int = Field(default=16_384, ge=256, le=262_144)
    backfill_batch: int = Field(default=200, ge=1, le=10_000)
    backfill_interval_s: float = Field(default=60, gt=0)
    backfill_jitter_s: float = Field(default=10, ge=0)
    # Свои загадки сборников (ADR 0029): лимит графем эмодзи (как у collections,
    # GV_COLLECTIONS__LIMITS__EMOJI_MAX_GRAPHEMES) и типы загадок, по профилям которых строятся
    # наборы со scope сборника (в 3b — только emoji).
    collection_emoji_max_graphemes: int = Field(default=12, ge=1, le=64)
    scoped_clue_types: frozenset[str] = frozenset({"emoji"})
    # UGC-изображения (5b, ADR 0048): None - встроенный config/ugc_purposes.yaml.
    ugc_purposes_path: Path | None = None
    # Сколько картинок UGC декодируется одновременно (остальные ждут): защита от DoS потоком заявок.
    ugc_encode_concurrency: int = Field(default=2, ge=1, le=32)
    # Встроенный ICC-профиль больше лимита не разбирается LittleCMS (игнорируется).
    ugc_icc_max_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    # Код причины из ugc_purposes.yaml для снятия аватарки решением модерации social (5c).
    ugc_social_removal_reason: str = Field(default="inappropriate", min_length=1, max_length=64)
    # Задача relay ``media.ugc.purge``: гарантированное удаление объектов снятых/отклонённых/
    # замещённых изображений (ключи - в таблице ``ugc_object_purge``).
    ugc_purge_interval_s: float = Field(default=60, gt=0)
    ugc_purge_jitter_s: float = Field(default=10, ge=0)
    ugc_purge_batch: int = Field(default=500, ge=1, le=1000)
    # Задача relay ``media.ugc.orphans``: p/-объекты старше TTL без pending-строки в БД.
    ugc_orphan_ttl_h: int = Field(default=24, ge=1, le=720)
    ugc_orphan_interval_s: float = Field(default=3600, gt=0)
    ugc_orphan_jitter_s: float = Field(default=300, ge=0)

    @property
    def source_host_list(self) -> frozenset[str]:
        """Allowlist хостов исходников в нижнем регистре."""
        return frozenset(h.strip().lower() for h in self.source_hosts.split(",") if h.strip())


class FfmpegSettings(BaseModel):
    """ffmpeg/ffprobe: ``GV_FFMPEG__*``."""

    model_config = ConfigDict(frozen=True)

    bin: str = "ffmpeg"
    probe_bin: str = "ffprobe"
    timeout_s: float = Field(default=60.0, gt=0)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "media_pipeline"
    # Генерация — ffmpeg и Pillow по исходнику: при prefetch 10 параллельные задачи засева
    # (8 исходников на фильм) выводили процесс за лимит памяти контейнера (OOM, рестарт).
    task_prefetch: int = Field(default=2, ge=1, le=100)
    s3: S3Settings = S3Settings()
    media: MediaSettings = MediaSettings()
    ffmpeg: FfmpegSettings = FfmpegSettings()

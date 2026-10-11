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
    s3: S3Settings = S3Settings()
    media: MediaSettings = MediaSettings()
    ffmpeg: FfmpegSettings = FfmpegSettings()

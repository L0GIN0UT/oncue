"""Настройки catalog (переменные окружения ``GV_*``, вложенные — через ``__``).

Все внешние источники метаданных выключены по умолчанию (ADR 0014): включаются явно
``GV_<SOURCE>__ENABLED=true`` вместе с ключами.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, SecretStr

from gv_service import ServiceSettings


class OpenSearchSettings(BaseModel):
    """OpenSearch. Без ``url`` поиск работает на Postgres (pg_trgm)."""

    url: str | None = None
    index: str = Field(default="catalog_media", pattern=r"^[a-z][a-z0-9_\-]*$")
    timeout_s: float = Field(default=2.0, gt=0)


class TmdbSettings(BaseModel):
    """TMDB API v3 с Bearer-токеном v4."""

    enabled: bool = False
    api_token: SecretStr | None = None
    base_url: str = "https://api.themoviedb.org/3"
    image_base_url: str = "https://image.tmdb.org/t/p/original"
    language: str = "ru-RU"
    fallback_language: str = "en-US"
    rps: float = Field(default=20.0, gt=0)
    timeout_s: float = Field(default=10.0, gt=0)


class MusicBrainzSettings(BaseModel):
    """MusicBrainz WS/2 (CC0). Требует осмысленный User-Agent, лимит — 1 запрос/с."""

    enabled: bool = False
    user_agent: str = "Guessverse/0.1 (https://guessverse.ru)"
    base_url: str = "https://musicbrainz.org/ws/2"
    rps: float = Field(default=1.0, gt=0)
    timeout_s: float = Field(default=10.0, gt=0)


class SpotifySettings(BaseModel):
    """Spotify Web API, client credentials (только метаданные)."""

    enabled: bool = False
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None
    base_url: str = "https://api.spotify.com/v1"
    token_url: str = "https://accounts.spotify.com/api/token"  # noqa: S105 — адрес, не секрет
    market: str = "RU"
    rps: float = Field(default=5.0, gt=0)
    timeout_s: float = Field(default=10.0, gt=0)


class YandexMusicSettings(BaseModel):
    """Неофициальный клиент Яндекс Музыки — только метаданные (ADR 0014, риск принят)."""

    enabled: bool = False
    token: SecretStr | None = None
    base_url: str = "https://api.music.yandex.net"
    rps: float = Field(default=2.0, gt=0)
    timeout_s: float = Field(default=10.0, gt=0)


class CircuitSettings(BaseModel):
    """Circuit breaker внешних источников."""

    failure_threshold: int = Field(default=5, ge=1)
    open_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_s: float = Field(default=0.5, ge=0)


class AutocompleteSettings(BaseModel):
    """Автокомплит и поиск."""

    cache_ttl_s: int = Field(default=300, ge=1)
    min_prefix: int = Field(default=2, ge=1)
    max_query_len: int = Field(default=64, ge=1, le=256)
    max_limit: int = Field(default=10, ge=1, le=50)
    max_search_limit: int = Field(default=20, ge=1, le=100)
    rate_limit_per_minute: int = Field(default=120, ge=1)
    key_prefix: str = Field(default="catalog", pattern=r"^[a-z0-9_:\-]+$")


class ImportSettings(BaseModel):
    """Импорт."""

    files_dir: Path = Path("seeds/catalog")
    search_limit: int = Field(default=10, ge=1, le=50)
    max_file_lines: int = Field(default=10_000, ge=1)


class NormalizerSettings(BaseModel):
    """Нормализация названий."""

    articles: tuple[str, ...] = ("the", "a", "an")


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "catalog"

    opensearch: OpenSearchSettings = OpenSearchSettings()
    tmdb: TmdbSettings = TmdbSettings()
    musicbrainz: MusicBrainzSettings = MusicBrainzSettings()
    spotify: SpotifySettings = SpotifySettings()
    yandex_music: YandexMusicSettings = YandexMusicSettings()
    circuit: CircuitSettings = CircuitSettings()
    autocomplete: AutocompleteSettings = AutocompleteSettings()
    imports: ImportSettings = ImportSettings()
    normalizer: NormalizerSettings = NormalizerSettings()

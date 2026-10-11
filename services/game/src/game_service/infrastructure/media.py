"""``AssetPort`` ядра поверх media-pipeline ``POST /internal/v1/asset-links`` (ADR 0012, D13).

Таймаут на попытку, ретраи только на 503 и сетевые ошибки (запрос ссылки идемпотентен по
смыслу: повтор выдаёт новую ссылку на тот же уровень), circuit breaker: после серии отказов
запросы сразу получают 503, не нагружая упавший сервис. Сервисный токен — из
``TokenProvider`` (``aud = media_pipeline``); токен не логируется.

Маппинг ответов: 404 и 410 → ``AssetUnavailableError`` (раунд ``void``), 429 → 429,
401/403/409/503/сеть → 503 ``game.assets_unavailable``; после 401 кэш токена сбрасывается.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx
import structlog
from prometheus_client import Counter

from game_service.application.ports import TokenProvider
from game_service.domain.errors import (
    AssetsUnavailableError,
    AssetUnavailableError,
    GameRateLimitedError,
)
from gv_core.errors import AppError
from gv_game_kernel import AssetRef, ClueAsset

log = structlog.get_logger(__name__)

MEDIA_CALLS = Counter("game_media_link_calls_total", "Запросы ссылок у media-pipeline", ["result"])

LINKS_PATH = "/internal/v1/asset-links"
_RETRYABLE_STATUS = 503
_GONE_STATUSES = frozenset({404, 410})
_RATE_LIMITED = 429
_AUTH_FAILED = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class MediaClientSettings:
    """Параметры клиента media-pipeline."""

    base_url: str
    timeout_s: float
    retries: int
    breaker_failures: int
    breaker_cooldown_s: float
    retry_backoff_s: float = 0.05


class CircuitBreaker:
    """Простой breaker: ``failures`` подряд → открыт на ``cooldown_s``, затем пробный запрос."""

    def __init__(self, failures: int, cooldown_s: float) -> None:
        self._threshold = failures
        self._cooldown = cooldown_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Запросы сейчас не пропускаются."""
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown:
            # Полуоткрыт: пропускаем запрос, результат решит дальше.
            self._opened_at = None
            self._failures = self._threshold - 1
            return False
        return True

    def success(self) -> None:
        """Успешный вызов — сброс."""
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        """Отказ зависимости."""
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()


class UnconfiguredTokenProvider:
    """Сервисная аутентификация не настроена: вызовы media-pipeline отвечают 503."""

    async def token(self) -> str:
        """Всегда отказ (без учётных данных internal-ручки media-pipeline недоступны)."""
        raise AssetsUnavailableError("сервисная аутентификация game не настроена")

    def invalidate(self) -> None:
        """Кэша нет — нечего забывать."""


class MediaPipelineAssetClient:
    """Клиент ссылок на ассеты (реализация ``AssetPort``)."""

    def __init__(
        self,
        settings: MediaClientSettings,
        tokens: TokenProvider,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url, timeout=settings.timeout_s
        )
        self._breaker = CircuitBreaker(settings.breaker_failures, settings.breaker_cooldown_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        """Ссылка на уровень ``level`` набора для субъекта ``subject``."""
        if self._breaker.is_open:
            MEDIA_CALLS.labels("breaker_open").inc()
            raise AssetsUnavailableError("media-pipeline временно недоступен")
        attempts = self._settings.retries + 1
        for attempt in range(attempts):
            try:
                result = await self._call(asset.asset_set_id, level, subject)
            except _RetryableError as exc:
                self._breaker.failure()
                if attempt + 1 >= attempts or self._breaker.is_open:
                    MEDIA_CALLS.labels("unavailable").inc()
                    raise AssetsUnavailableError("media-pipeline временно недоступен") from exc
                await asyncio.sleep(self._settings.retry_backoff_s * (attempt + 1))
                continue
            self._breaker.success()
            MEDIA_CALLS.labels("ok").inc()
            return result
        raise AssetsUnavailableError("media-pipeline временно недоступен")  # pragma: no cover

    async def _call(self, asset_set_id: UUID, level: int, subject: str) -> ClueAsset:
        token = await self._tokens.token()
        try:
            response = await self._client.post(
                LINKS_PATH,
                json={"asset_set_id": str(asset_set_id), "level": level, "subject": subject},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            log.warning("game.media_network_error", error_type=type(exc).__name__)
            raise _RetryableError from exc
        status = response.status_code
        if status == httpx.codes.OK:
            body = response.json()
            return ClueAsset(
                level=int(body["level"]),
                url=str(body["url"]),
                expires_at=datetime.fromisoformat(body["expires_at"]),
                mime=str(body["mime"]),
            )
        if status == _RETRYABLE_STATUS:
            raise _RetryableError
        self._breaker.success()  # сервис ответил осмысленно — он жив
        if status in _GONE_STATUSES:
            MEDIA_CALLS.labels("gone").inc()
            raise AssetUnavailableError("ассет раунда недоступен")
        if status == _RATE_LIMITED:
            MEDIA_CALLS.labels("rate_limited").inc()
            retry_after = response.headers.get("Retry-After", "1")
            raise GameRateLimitedError(
                "слишком много ссылок на ассет",
                retry_after=int(retry_after) if retry_after.isdigit() else 1,
            )
        if status in _AUTH_FAILED:
            log.error("game.media_auth_failed", status=status)
            if status == httpx.codes.UNAUTHORIZED:
                # Токен отозван или подписан выведенным ключом: следующий вызов возьмёт новый.
                self._tokens.invalidate()
        else:
            log.warning("game.media_unexpected_status", status=status)
        MEDIA_CALLS.labels("error").inc()
        raise AssetsUnavailableError("media-pipeline не выдал ссылку")


class _RetryableError(AppError):
    """Отказ, который имеет смысл повторить (503, сеть)."""

    default_code = "game.media_retryable"

    def __init__(self) -> None:
        super().__init__("повторяемый отказ media-pipeline")

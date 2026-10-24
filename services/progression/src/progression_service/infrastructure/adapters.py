"""Адаптеры магазина за портами: HMAC-хешер кодов, Redis-лимитер, токены, разбор картинок."""

from __future__ import annotations

import hashlib
import hmac
import io
import secrets
from collections.abc import Callable
from functools import cache

from PIL import Image, UnidentifiedImageError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gv_core.errors import DependencyUnavailableError
from gv_service.settings import Environment
from progression_service.application.ports.shop import AttemptWindow, InspectedMedia
from progression_service.domain.config import MediaLimits
from progression_service.domain.shop import (
    ItemMediaInvalidError,
    ItemMediaTooLargeError,
    MediaInfo,
    MediaKind,
    max_upload_bytes,
)
from progression_service.settings import Settings

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MIN_KEY_BYTES = 32


@cache
def _process_key() -> bytes:
    """Временный ключ процесса (только local/ci); коды переживают процесс лишь с настоящим."""
    return secrets.token_bytes(_MIN_KEY_BYTES)


class PromoKeyMissingError(RuntimeError):
    """Вне local/ci не задан ``GV_PROGRESSION__PROMO_HMAC_KEY``: временный ключ недопустим."""

    def __init__(self) -> None:
        super().__init__("не задан GV_PROGRESSION__PROMO_HMAC_KEY (ключ HMAC промокодов)")


def hmac_key(settings: Settings) -> bytes:
    """Ключ HMAC кодов из настроек; в local/ci без него - временный ключ процесса.

    Вызывается лениво (при первом хешировании и в job ``migrate``), а не при создании
    ``Settings``: инструменты контрактов строят приложение из настроек по умолчанию.

    Raises:
        PromoKeyMissingError: ключа нет, а окружение не local и не ci.
    """
    key = settings.progression.promo_hmac_key
    if key is not None:
        return key.get_secret_value().encode()
    if settings.environment in (Environment.LOCAL, Environment.CI):
        return _process_key()
    raise PromoKeyMissingError


class HmacCodeHasher:
    """HMAC-SHA256 нормализованного кода серверным ключом (ADR 0048, не голый sha256)."""

    def __init__(self, key: bytes) -> None:
        if len(key) < _MIN_KEY_BYTES:
            raise ValueError("ключ HMAC кодов должен быть не короче 32 байт")
        self._key = key

    def hash(self, normalized_code: str) -> str:
        """Hex-хеш кода."""
        return hmac.new(self._key, normalized_code.encode(), hashlib.sha256).hexdigest()

    def same(self, left: str, right: str) -> bool:
        """Сравнение за постоянное время."""
        return hmac.compare_digest(left.encode(), right.encode())


class LazyCodeHasher:
    """Хешер, который берёт ключ при первом использовании, а не при сборке приложения."""

    def __init__(self, key_provider: Callable[[], bytes]) -> None:
        self._provider = key_provider
        self._hasher: HmacCodeHasher | None = None

    def _real(self) -> HmacCodeHasher:
        if self._hasher is None:
            self._hasher = HmacCodeHasher(self._provider())
        return self._hasher

    def hash(self, normalized_code: str) -> str:
        """Hex-хеш кода."""
        return self._real().hash(normalized_code)

    def same(self, left: str, right: str) -> bool:
        """Сравнение за постоянное время."""
        return self._real().same(left, right)


class RedisAttemptLimiter:
    """Окно фиксированной длины: ``INCR`` + ``EXPIRE NX`` + ``TTL`` атомарно в MULTI.

    Отказ Redis - fail-closed: 503 с ``details.retry_after``, ввод кода закрыт (перебор
    без лимитера недопустим, ADR 0048).
    """

    def __init__(
        self, redis: Redis, *, retry_after_s: int, prefix: str = "progression:promo:"
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> AttemptWindow:
        """Учесть попытку."""
        full = f"{self._prefix}{key}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(full)
                pipe.expire(full, window_s, nx=True)
                pipe.ttl(full)
                count, _, ttl = await pipe.execute()
        except RedisError as exc:
            raise DependencyUnavailableError(
                "лимитер попыток недоступен",
                code="progression.limiter_unavailable",
                details={"retry_after": self._retry_after_s},
            ) from exc
        return AttemptWindow(count=int(count), resets_in_s=max(int(ttl), 1))


class UnconfiguredLimiter:
    """Нет Redis (``GV_REDIS_URL``): ввод кода закрыт, остальной сервис работает."""

    def __init__(self, retry_after_s: int) -> None:
        self._retry_after_s = retry_after_s

    async def hit(self, key: str, window_s: int) -> AttemptWindow:  # noqa: ARG002 - порт
        """Всегда отказ (fail-closed)."""
        raise DependencyUnavailableError(
            "лимитер попыток не настроен (GV_REDIS_URL)",
            code="progression.limiter_unavailable",
            details={"retry_after": self._retry_after_s},
        )


class SecretsTokenSource:
    """128 бит из ``secrets`` для ключей объектов."""

    def token_hex(self) -> str:
        """32 hex-символа."""
        return secrets.token_hex(16)


def _signature_format(data: bytes) -> str:
    """Формат по сигнатуре файла, а не по ``Content-Type`` клиента."""
    if data.startswith(_PNG_SIGNATURE):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ItemMediaInvalidError("неизвестная сигнатура файла (нужен WebP или PNG)")


class PillowMediaInspector:
    """Сигнатура, заголовок, декодирование каждого кадра и постер (ADR 0046).

    Размеры читаются из заголовка до декодирования; декодируются только файлы, у которых
    сторона и число кадров в пределах лимитов (защита от декомпрессионных бомб). Остальное
    отвергает ``domain.shop.check_media`` по возвращённым сведениям.
    """

    def inspect(self, data: bytes, kind: MediaKind, limits: MediaLimits) -> InspectedMedia:
        """Разобрать файл."""
        if len(data) > max_upload_bytes(kind, limits):
            raise ItemMediaTooLargeError
        fmt = _signature_format(data)
        try:
            return self._decode(data, fmt, limits)
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
            raise ItemMediaInvalidError("файл не декодируется") from exc
        except Image.DecompressionBombError as exc:
            raise ItemMediaInvalidError("слишком много пикселей") from exc

    @staticmethod
    def _decode(data: bytes, fmt: str, limits: MediaLimits) -> InspectedMedia:
        with Image.open(io.BytesIO(data), formats=["WEBP", "PNG"]) as image:
            if (image.format or "").lower() != fmt:
                raise ItemMediaInvalidError("содержимое не совпадает с сигнатурой")
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1))
            duration = 0
            poster: bytes | None = None
            decodable = max(width, height) <= limits.max_side_px and frames <= max(
                limits.animated_max_frames, 1
            )
            if decodable:
                for index in range(frames):
                    image.seek(index)
                    image.load()  # полное декодирование: битый кадр = ошибка загрузки
                    duration += int(image.info.get("duration", 0) or 0)
                    if index == 0:
                        poster = _first_frame(image)
        info = MediaInfo(
            format=fmt,
            width=width,
            height=height,
            frames=frames,
            duration_ms=duration,
            size_bytes=len(data),
        )
        return InspectedMedia(
            info=info,
            extension=fmt,
            content_type=f"image/{fmt}",
            poster=poster if frames > 1 else None,
        )


def _first_frame(image: Image.Image) -> bytes:
    """Первый кадр как статичный lossless WebP (постер для ``prefers-reduced-motion``)."""
    out = io.BytesIO()
    image.convert("RGBA").save(out, format="WEBP", lossless=True)
    return out.getvalue()

"""Адаптеры магазина без сети: HMAC кодов, разбор картинок Pillow, настройки старта."""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from PIL import Image
from progression_service.domain.config import MediaLimits
from progression_service.domain.shop import (
    ItemMediaInvalidError,
    ItemMediaTooLargeError,
    MediaKind,
    check_media,
)
from progression_service.infrastructure.adapters import (
    HmacCodeHasher,
    LazyCodeHasher,
    PillowMediaInspector,
    PromoKeyMissingError,
    SecretsTokenSource,
    hmac_key,
)
from progression_service.infrastructure.storage import cosmetics_read_policy
from progression_service.settings import ProgressionSettings, Settings
from pydantic import SecretStr, ValidationError

from gv_service.settings import Environment

pytestmark = pytest.mark.unit

LIMITS = MediaLimits(
    static_max_bytes=100_000,
    animated_max_bytes=200_000,
    animated_max_frames=10,
    animated_max_duration_ms=1000,
    max_side_px=64,
)


def webp(frames: int = 1, size: tuple[int, int] = (16, 16), duration: int = 50) -> bytes:
    images = [Image.new("RGBA", size, (i * 9 % 256, 10, 20, 255)) for i in range(frames)]
    out = io.BytesIO()
    images[0].save(
        out,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=duration,
        lossless=True,
    )
    return out.getvalue()


def _noise_webp() -> bytes:
    import random

    rng = random.Random(7)
    image = Image.frombytes("RGB", (48, 48), bytes(rng.randrange(256) for _ in range(48 * 48 * 3)))
    out = io.BytesIO()
    image.save(out, format="WEBP", lossless=True)
    return out.getvalue()


# ------------------------------------------------------------------ HMAC
def test_hash_is_keyed_stable_and_not_plain_sha256() -> None:
    hasher = HmacCodeHasher(b"k" * 32)
    other = HmacCodeHasher(b"j" * 32)

    digest = hasher.hash("ABCDEFGHJKMN")

    assert digest == hasher.hash("ABCDEFGHJKMN")
    assert len(digest) == 64 and digest != other.hash("ABCDEFGHJKMN")
    assert digest != hashlib.sha256(b"ABCDEFGHJKMN").hexdigest()
    assert hasher.same(digest, hasher.hash("ABCDEFGHJKMN"))
    assert not hasher.same(digest, other.hash("ABCDEFGHJKMN"))


def test_short_key_is_refused() -> None:
    with pytest.raises(ValueError, match="32"):
        HmacCodeHasher(b"short")


@pytest.mark.parametrize("env", [Environment.LOCAL, Environment.CI])
def test_temporary_key_is_stable_within_the_process_in_local_and_ci(env: Environment) -> None:
    temporary = hmac_key(Settings(environment=env))
    assert len(temporary) >= 32 and temporary == hmac_key(Settings(environment=env))


@pytest.mark.parametrize("env", [Environment.PRODUCTION, Environment.STAGING])
def test_missing_key_outside_local_and_ci_is_an_error_not_a_temporary_key(
    env: Environment,
) -> None:
    with pytest.raises(PromoKeyMissingError, match="PROMO_HMAC_KEY"):
        hmac_key(Settings(environment=env))


@pytest.mark.parametrize("env", list(Environment))
def test_configured_key_wins_in_every_environment(env: Environment) -> None:
    settings = Settings(
        environment=env,
        progression=ProgressionSettings(promo_hmac_key=SecretStr("c" * 40)),
    )
    assert hmac_key(settings) == b"c" * 40


def test_lazy_hasher_reads_the_key_on_first_use_only() -> None:
    calls: list[int] = []

    def provider() -> bytes:
        calls.append(1)
        return b"k" * 32

    hasher = LazyCodeHasher(provider)
    assert calls == []
    assert hasher.hash("ABC") == hasher.hash("ABC") and calls == [1]


async def test_migrate_job_fails_in_production_without_the_key() -> None:
    from progression_service.main import prepare_resources

    with pytest.raises(PromoKeyMissingError):
        await prepare_resources(Settings(environment=Environment.PRODUCTION))


def test_token_source_gives_128_bits_of_hex() -> None:
    a, b = SecretsTokenSource().token_hex(), SecretsTokenSource().token_hex()
    assert len(a) == 32 and int(a, 16) >= 0 and a != b


# ------------------------------------------------------------------ настройки старта
def test_settings_without_key_are_constructible_in_any_environment() -> None:
    """Инструменты контрактов строят ``Settings()`` без env: ключ читается лениво."""
    db = SecretStr("postgresql+asyncpg://u:p@localhost/db")
    for env in Environment:
        Settings(environment=env)
        Settings(environment=env, database_url=db)


def test_weak_key_is_refused_by_settings() -> None:
    with pytest.raises(ValidationError):
        ProgressionSettings(promo_hmac_key=SecretStr("short"))


def test_defaults_keep_billing_off_and_cosmetics_prefix() -> None:
    s = ProgressionSettings()
    assert s.billing_enabled is False
    assert (s.cosmetics_bucket, s.cosmetics_base_url) == ("gv-cosmetics", "/cosmetics/")


def test_bucket_policy_allows_only_anonymous_get_of_the_cosmetics_prefix() -> None:
    import json

    policy = json.loads(cosmetics_read_policy("gv-cosmetics"))
    (statement,) = policy["Statement"]
    assert statement["Action"] == ["s3:GetObject"]
    assert statement["Resource"] == ["arn:aws:s3:::gv-cosmetics/c/*"]
    assert statement["Effect"] == "Allow" and statement["Principal"] == "*"


# ------------------------------------------------------------------ разбор картинок
def inspect(data: bytes, kind: MediaKind) -> Any:
    return PillowMediaInspector().inspect(data, kind, LIMITS)


def test_static_webp_and_png_are_accepted_without_poster() -> None:
    result = inspect(webp(), MediaKind.STATIC)
    assert (result.info.format, result.info.frames, result.extension) == ("webp", 1, "webp")
    assert result.poster is None and result.content_type == "image/webp"
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, format="PNG")
    png = inspect(out.getvalue(), MediaKind.STATIC)
    assert (png.extension, png.content_type) == ("png", "image/png")


def test_animation_reports_frames_duration_and_first_frame_poster() -> None:
    result = inspect(webp(frames=4, duration=40), MediaKind.ANIMATED)

    assert result.info.frames == 4 and result.info.duration_ms == 160
    assert result.poster is not None
    poster = Image.open(io.BytesIO(result.poster))
    assert poster.size == (16, 16) and getattr(poster, "n_frames", 1) == 1
    check_media(result.info, MediaKind.ANIMATED, LIMITS)


def test_animation_limits_are_reported_for_domain_check() -> None:
    long = inspect(webp(frames=4, duration=400), MediaKind.ANIMATED)
    with pytest.raises(ItemMediaInvalidError):
        check_media(long.info, MediaKind.ANIMATED, LIMITS)
    many = inspect(webp(frames=11), MediaKind.ANIMATED)
    assert many.info.frames == 11 and many.poster is None  # не декодируем сверх лимита кадров
    with pytest.raises(ItemMediaInvalidError):
        check_media(many.info, MediaKind.ANIMATED, LIMITS)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello world",
        b"GIF89a" + b"\x00" * 30,
        b"RIFF\x10\x00\x00\x00WEBPVP8 junk",
        b"\x89PNG\r\n\x1a\n" + b"junk" * 8,
        _noise_webp()[:200],  # обрезанный файл
    ],
)
def test_foreign_or_broken_signatures_are_invalid(data: bytes) -> None:
    with pytest.raises(ItemMediaInvalidError):
        inspect(data, MediaKind.STATIC)


def test_png_with_webp_signature_mismatch_is_invalid() -> None:
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, format="PNG")
    data = b"RIFF" + b"\x00" * 4 + b"WEBP" + out.getvalue()[8:]
    with pytest.raises(ItemMediaInvalidError):
        inspect(data, MediaKind.STATIC)


def test_oversized_file_is_too_large_before_decoding() -> None:
    with pytest.raises(ItemMediaTooLargeError):
        inspect(b"\x00" * (LIMITS.static_max_bytes + 1), MediaKind.STATIC)


def test_wide_picture_is_reported_and_not_decoded() -> None:
    result = inspect(webp(size=(200, 8)), MediaKind.STATIC)
    assert result.info.width == 200
    with pytest.raises(ItemMediaInvalidError):
        check_media(result.info, MediaKind.STATIC, LIMITS)

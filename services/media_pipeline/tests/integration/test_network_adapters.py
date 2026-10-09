"""Сетевые адаптеры: SSRF-защита загрузчика, подпись nginx secure_link, лимитер Redis, S3."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.infrastructure.fetcher import HttpSourceFetcher, is_public_ip
from media_pipeline_service.infrastructure.links import (
    NginxSecureLinkSigner,
    RedisLinkRateLimiter,
    UnconfiguredSigner,
    md5_b64url,
)
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.settings import S3Settings
from redis.asyncio import Redis

from gv_core import uuid7
from gv_core.errors import DependencyUnavailableError
from gv_testkit import InfraProvider, S3Endpoint

pytestmark = pytest.mark.integration

HOST = "image.tmdb.org"
PUBLIC_IP = "93.184.216.34"
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def resolver(*ips: str):
    async def resolve(host: str) -> list[str]:
        return list(ips)

    return resolve


def fetcher(*ips: str, max_bytes: int = 10_000) -> HttpSourceFetcher:
    return HttpSourceFetcher(
        allowed_hosts=frozenset({HOST}),
        max_bytes=max_bytes,
        timeout_s=5,
        resolver=resolver(*(ips or (PUBLIC_IP,))),
    )


# ---------------------------------------------------------------- SSRF
@pytest.mark.parametrize(
    "uri",
    [
        "http://image.tmdb.org/a.jpg",
        "https://evil.example/a.jpg",
        "https://image.tmdb.org.evil.example/a.jpg",
        "https://user:pw@image.tmdb.org/a.jpg",
        "https://image.tmdb.org:8443/a.jpg",
        "file:///etc/passwd",
        "gopher://image.tmdb.org/",
    ],
)
async def test_rejects_disallowed_urls(uri: str) -> None:
    with pytest.raises(GenerationError) as exc:
        await fetcher().fetch(uri)
    assert (exc.value.code, exc.value.retryable) == ("source.forbidden", False)


@pytest.mark.parametrize(
    "ips",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("192.168.1.1",),
        ("::1",),
        ("fd00::1",),
        ("::ffff:127.0.0.1",),
        (PUBLIC_IP, "10.0.0.1"),  # хотя бы один приватный — отказ (DNS rebinding)
    ],
)
async def test_rejects_private_addresses_after_dns(ips: tuple[str, ...]) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.route().respond(200, content=JPEG)
        with pytest.raises(GenerationError) as exc:
            await fetcher(*ips).fetch(f"https://{HOST}/a.jpg")
    assert exc.value.code == "source.forbidden"
    assert not route.called


async def test_default_resolver_blocks_localhost() -> None:
    real = HttpSourceFetcher(allowed_hosts=frozenset({"localhost"}), max_bytes=10, timeout_s=5)
    with pytest.raises(GenerationError) as exc:
        await real.fetch("https://localhost/a.jpg")
    assert exc.value.code == "source.forbidden"


def test_is_public_ip() -> None:
    assert is_public_ip("8.8.8.8")
    assert not is_public_ip("100.64.0.1")
    assert not is_public_ip("224.0.0.1")


async def test_fetches_from_pinned_ip_with_sni_and_host() -> None:
    with respx.mock() as router:
        route = router.get(f"https://{PUBLIC_IP}/t/p/original/abc.jpg").respond(
            200, content=JPEG, headers={"content-type": "image/jpeg"}
        )
        result = await fetcher().fetch(f"https://{HOST}/t/p/original/abc.jpg")
    assert result.data == JPEG
    request = route.calls.last.request
    assert request.headers["host"] == HOST
    assert request.extensions["sni_hostname"] == HOST


async def test_same_host_redirect_followed_other_host_rejected() -> None:
    with respx.mock() as router:
        router.get(f"https://{PUBLIC_IP}/a.jpg").respond(301, headers={"location": "/b.jpg"})
        router.get(f"https://{PUBLIC_IP}/b.jpg").respond(200, content=JPEG)
        assert (await fetcher().fetch(f"https://{HOST}/a.jpg")).data == JPEG
    with respx.mock() as router:
        router.get(f"https://{PUBLIC_IP}/a.jpg").respond(
            302, headers={"location": "https://169.254.169.254/latest/meta-data"}
        )
        with pytest.raises(GenerationError) as exc:
            await fetcher().fetch(f"https://{HOST}/a.jpg")
    assert exc.value.code == "source.forbidden"


async def test_redirect_loop_is_bounded() -> None:
    with respx.mock() as router:
        router.get(f"https://{PUBLIC_IP}/a.jpg").respond(302, headers={"location": "/a.jpg"})
        with pytest.raises(GenerationError) as exc:
            await fetcher().fetch(f"https://{HOST}/a.jpg")
    assert exc.value.code == "source.forbidden"


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (
            httpx.Response(200, content=b"x" * 101, headers={"content-length": "101"}),
            "source.too_large",
            False,
        ),
        (httpx.Response(200, content=b"x" * 101), "source.too_large", False),
        (httpx.Response(404), "source.not_found", False),
        (httpx.Response(503), "source.unavailable", True),
        (httpx.Response(403), "source.http_error", False),
    ],
)
async def test_response_errors(response: httpx.Response, code: str, retryable: bool) -> None:
    with respx.mock() as router:
        router.get(f"https://{PUBLIC_IP}/a.jpg").mock(return_value=response)
        with pytest.raises(GenerationError) as exc:
            await fetcher(max_bytes=100).fetch(f"https://{HOST}/a.jpg")
    assert (exc.value.code, exc.value.retryable) == (code, retryable)


async def test_network_error_is_retryable() -> None:
    with respx.mock() as router:
        router.get(f"https://{PUBLIC_IP}/a.jpg").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(GenerationError) as exc:
            await fetcher().fetch(f"https://{HOST}/a.jpg")
    assert (exc.value.code, exc.value.retryable) == ("source.unavailable", True)


# ---------------------------------------------------------------- nginx secure_link
def test_md5_b64url_matches_nginx_documentation_vector() -> None:
    # Пример из документации ngx_http_secure_link_module:
    # echo -n '2147483647/s/link127.0.0.1 secret' | openssl md5 -binary | openssl base64
    #   | tr +/ -_ | tr -d =   →   _e4Nc3iduzkWRm01TBBNYw
    assert md5_b64url("2147483647/s/link127.0.0.1 secret") == "_e4Nc3iduzkWRm01TBBNYw"


def test_signer_matches_reference_computation() -> None:
    secret = "s" * 40
    signer = NginxSecureLinkSigner(secret, public_base_url="https://guessverse.test/")
    key = f"a/{'ab' * 16}.webp"
    expires_at = datetime(2026, 10, 9, 12, 1, tzinfo=UTC)
    url = signer.sign(key, expires_at)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    expires = int(expires_at.timestamp())
    assert parts.scheme == "https" and parts.netloc == "guessverse.test"
    assert parts.path == f"/media/{key}"
    assert query["expires"] == [str(expires)]
    # Эталон: secure_link_md5 "$secure_link_expires$uri <secret>"
    reference = (
        base64.urlsafe_b64encode(
            hashlib.md5(f"{expires}/media/{key} {secret}".encode(), usedforsecurity=False).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert query["md5"] == [reference]
    if shutil.which("openssl"):
        raw = subprocess.run(
            ["openssl", "md5", "-binary"],  # noqa: S607
            input=f"{expires}/media/{key} {secret}".encode(),
            check=True,
            capture_output=True,
        ).stdout
        assert query["md5"] == [base64.urlsafe_b64encode(raw).decode().rstrip("=")]


def test_signer_requires_strong_secret_and_unconfigured_is_503() -> None:
    with pytest.raises(ValueError, match="32"):
        NginxSecureLinkSigner("short")
    with pytest.raises(DependencyUnavailableError):
        UnconfiguredSigner().sign("a/x", datetime.now(UTC))


# ---------------------------------------------------------------- Redis
async def test_redis_limiter_counts_within_window(infra: InfraProvider) -> None:
    redis = Redis.from_url(infra.redis_url())
    prefix = f"test:media:{uuid7().hex[-12:]}:"
    limiter = RedisLinkRateLimiter(redis, prefix=prefix)
    try:
        assert [await limiter.hit("k", 60) for _ in range(3)] == [1, 2, 3]
        assert await limiter.hit("other", 60) == 1
        ttl = await redis.ttl(f"{prefix}k")
        assert 0 < ttl <= 60
    finally:
        keys = [k async for k in redis.scan_iter(f"{prefix}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_redis_limiter_fails_closed() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    try:
        with pytest.raises(DependencyUnavailableError):
            await RedisLinkRateLimiter(redis).hit("k", 60)
    finally:
        await redis.aclose()


# ---------------------------------------------------------------- S3
def s3_settings(endpoint: S3Endpoint) -> S3Settings:
    suffix = uuid7().hex[-12:]
    return S3Settings(
        endpoint=endpoint.url,
        access_key=endpoint.access_key,  # type: ignore[arg-type]
        secret_key=endpoint.secret_key,  # type: ignore[arg-type]
        bucket_sources=f"gv-src-{suffix}",
        bucket_assets=f"gv-assets-{suffix}",
    )


async def test_s3_roundtrip_private_objects(s3_endpoint: S3Endpoint) -> None:
    storage = S3Storage(s3_settings(s3_endpoint))
    await storage.ensure_buckets()
    key = f"a/{uuid7().hex}.webp"
    await storage.put(StorageArea.ASSETS, key, b"RIFF0000WEBP", "image/webp")
    assert await storage.get(StorageArea.ASSETS, key, max_bytes=100) == b"RIFF0000WEBP"
    with pytest.raises(GenerationError) as exc:
        await storage.get(StorageArea.ASSETS, key, max_bytes=4)
    assert exc.value.code == "source.too_large"

    async with storage._client() as s3:  # проверка свойств объекта
        head = await s3.head_object(Bucket=storage.bucket(StorageArea.ASSETS), Key=key)
        assert head["ContentType"] == "image/webp"
        assert head.get("Metadata", {}) == {}
        acl = await s3.get_object_acl(Bucket=storage.bucket(StorageArea.ASSETS), Key=key)
        assert all(g["Grantee"].get("URI") is None for g in acl["Grants"])  # нет AllUsers

    await storage.delete(StorageArea.ASSETS, [key, "a/missing.webp"])
    with pytest.raises(GenerationError) as exc:
        await storage.get(StorageArea.ASSETS, key, max_bytes=100)
    assert exc.value.code == "source.missing"


async def test_s3_without_endpoint_is_unavailable() -> None:
    with pytest.raises(DependencyUnavailableError):
        await S3Storage(S3Settings()).put(StorageArea.ASSETS, "a/x", b"", "image/webp")

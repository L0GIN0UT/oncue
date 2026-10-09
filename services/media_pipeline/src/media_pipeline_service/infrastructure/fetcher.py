"""Загрузка исходников по адресам из каталога с защитой от SSRF (§8 п.8 плана).

Правила:
    * только ``https`` на порт 443, без userinfo, хост — строго из allowlist;
    * DNS резолвится заранее, все адреса должны быть публичными (не loopback, private,
      link-local, reserved, multicast…); соединение идёт на проверенный IP с SNI и
      заголовком ``Host`` исходного хоста — повторный резолв (DNS rebinding) не используется;
    * редиректы не выполняются автоматически: разрешён переход только на тот же хост,
      не больше ``max_redirects``;
    * лимит размера (по ``Content-Length`` и по факту чтения) и общий таймаут.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import FetchedSource

log = structlog.get_logger(__name__)

Resolver = Callable[[str], Awaitable[list[str]]]

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_HTTP_NOT_FOUND = frozenset({404, 410})
_HTTP_SERVER_ERROR = 500
_HTTPS_PORT = 443


async def system_resolver(host: str) -> list[str]:
    """Адреса хоста через системный резолвер."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, _HTTPS_PORT, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _forbidden(reason: str) -> GenerationError:
    return GenerationError("source.forbidden", reason, retryable=False)


def is_public_ip(raw: str) -> bool:
    """Адрес глобальный (маршрутизируемый в интернете) и не служебный."""
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class HttpSourceFetcher:
    """Скачивание исходника с allowlist-хоста."""

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        max_bytes: int,
        timeout_s: float,
        resolver: Resolver = system_resolver,
        max_redirects: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allowed = frozenset(h.lower() for h in allowed_hosts)
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self._resolver = resolver
        self._max_redirects = max_redirects
        self._transport = transport

    def _check_url(self, uri: str) -> tuple[str, str]:
        parts = urlsplit(uri)
        if parts.scheme != "https":
            raise _forbidden("разрешён только https")
        if parts.username is not None or parts.password is not None:
            raise _forbidden("userinfo в адресе запрещён")
        host = (parts.hostname or "").lower()
        if host not in self._allowed:
            raise _forbidden("хост не в allowlist")
        if parts.port not in (None, _HTTPS_PORT):
            raise _forbidden("разрешён только порт 443")
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return host, path

    async def _resolve_public(self, host: str) -> str:
        try:
            addresses = await self._resolver(host)
        except OSError as exc:
            raise GenerationError("source.unavailable", "DNS не ответил", retryable=True) from exc
        if not addresses:
            raise GenerationError("source.unavailable", "нет адресов", retryable=True)
        if not all(is_public_ip(a) for a in addresses):
            # Любой непубличный адрес среди ответов — отказ: иначе атакующий DNS чередует их.
            raise _forbidden("хост резолвится в непубличный адрес")
        return addresses[0]

    async def fetch(self, uri: str) -> FetchedSource:
        """Скачать исходник."""
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._fetch(uri)
        except TimeoutError as exc:
            raise GenerationError("source.timeout", retryable=True) from exc

    async def _fetch(self, uri: str) -> FetchedSource:
        timeout = httpx.Timeout(self._timeout_s)
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=timeout, transport=self._transport, trust_env=False
        ) as client:
            current = uri
            for _ in range(self._max_redirects + 1):
                host, path = self._check_url(current)
                ip = await self._resolve_public(host)
                netloc = f"[{ip}]" if ":" in ip else ip
                request = client.build_request(
                    "GET",
                    f"https://{netloc}{path}",
                    headers={"Host": host, "Accept": "image/*, audio/*"},
                    extensions={"sni_hostname": host},
                )
                try:
                    response = await client.send(request, stream=True)
                except httpx.HTTPError as exc:
                    raise GenerationError("source.unavailable", retryable=True) from exc
                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location", "")
                        target = urljoin(f"https://{host}{path}", location)
                        if (urlsplit(target).hostname or "").lower() != host:
                            raise _forbidden("редирект на другой хост")
                        current = target
                        continue
                    return await self._read(response)
                finally:
                    await response.aclose()
            raise _forbidden("слишком много редиректов")

    async def _read(self, response: httpx.Response) -> FetchedSource:
        status = response.status_code
        if status in _HTTP_NOT_FOUND:
            raise GenerationError("source.not_found", retryable=False)
        if status >= _HTTP_SERVER_ERROR:
            raise GenerationError("source.unavailable", f"HTTP {status}", retryable=True)
        if status != httpx.codes.OK:
            raise GenerationError("source.http_error", f"HTTP {status}", retryable=False)
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            raise GenerationError("source.too_large", retryable=False)
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise GenerationError("source.too_large", retryable=False)
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise GenerationError("source.unavailable", retryable=True) from exc
        return FetchedSource(b"".join(chunks), response.headers.get("content-type"))

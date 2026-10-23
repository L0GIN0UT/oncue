"""Клиент media-pipeline для UGC-изображений: ``POST /internal/v1/ugc-images`` (ADR 0048).

Один вызов, не цепочка: файл уходит как multipart с сервисным токеном (``aud = media_pipeline``,
scope ``ugc.image``). Ответ media-pipeline разбирается строго; сеть, таймаут, 5xx, 401/403 и
любой неожиданный ответ - 503 без деталей (их видно только в логе). Токен не логируется.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import structlog
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from gv_auth.service import ServiceTokenProvider
from progression_service.application.ports.avatar import UgcUpload
from progression_service.domain.avatar import (
    AvatarInvalidImageError,
    AvatarTooLargeError,
    AvatarUnavailableError,
)

log = structlog.get_logger(__name__)

UGC_PATH = "/internal/v1/ugc-images"
_HTTP_TOO_LARGE = 413
_HTTP_INVALID = 422
_HTTP_ACCEPTED = 202
_HTTP_SERVER_ERROR = 500
_FILE_NAME = "image"  # имя файла клиента media-pipeline игнорирует: своё не раскрываем


class _Accepted(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    asset_id: UUID
    submitted_at: AwareDatetime


class HttpMediaUgcClient:
    """Реализация порта ``MediaUgcClient`` на httpx."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float,
        tokens: Callable[[], ServiceTokenProvider],
        max_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._tokens = tokens
        self._max_bytes = max_bytes
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент (вместе с процессом)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def submit(
        self, *, owner_user_id: UUID, purpose: str, data: bytes, idempotency_key: str
    ) -> UgcUpload:
        """Передать файл; ``Idempotency-Key`` пробрасывается как есть."""
        try:
            provider = self._tokens()
            headers = {**await provider.headers(), "Idempotency-Key": idempotency_key}
            response = await self._http().post(
                UGC_PATH,
                headers=headers,
                data={"owner_user_id": str(owner_user_id), "purpose": purpose},
                files={"file": (_FILE_NAME, data, "application/octet-stream")},
            )
        except Exception as exc:  # сеть, таймаут, токен: клиенту деталей не раскрываем
            log.warning("progression.media_call_failed", error=type(exc).__name__)
            raise AvatarUnavailableError from exc
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> UgcUpload:
        status = response.status_code
        if status == _HTTP_ACCEPTED:
            try:
                accepted = _Accepted.model_validate(response.json())
            except (ValueError, ValidationError) as exc:
                log.exception("progression.media_bad_response", status=status)
                raise AvatarUnavailableError from exc
            return UgcUpload(accepted.asset_id, accepted.submitted_at)
        if status == _HTTP_TOO_LARGE:
            raise AvatarTooLargeError(self._max_bytes)
        if status == _HTTP_INVALID and _code(response) == "ugc.invalid_image":
            raise AvatarInvalidImageError
        # 401/403 (токен, область, клиент), 409 (ключ), 5xx и всё остальное - наша сторона.
        log.error("progression.media_unexpected_status", status=status, code=_code(response))
        raise AvatarUnavailableError


def _code(response: httpx.Response) -> str | None:
    try:
        body: Any = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


__all__ = ["HttpMediaUgcClient"]

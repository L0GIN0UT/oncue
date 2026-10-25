"""Подпись ссылок: ``/media/`` подписывает только ``a/``; ``p/`` (UGC на модерации) - никогда."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from media_pipeline_service.infrastructure.links import NginxSecureLinkSigner

pytestmark = pytest.mark.unit

EXPIRES = datetime(2026, 10, 23, 12, 2, tzinfo=UTC)
HEX = "ab" * 16


def test_asset_key_is_signed_under_media_prefix() -> None:
    signer = NginxSecureLinkSigner("s" * 40)
    asset = signer.sign(f"a/{HEX}.webp", EXPIRES)
    assert urlsplit(asset).path == f"/media/a/{HEX}.webp"
    expires = int(EXPIRES.timestamp())
    assert parse_qs(urlsplit(asset).query)["md5"] == [signer.token(f"/media/a/{HEX}.webp", expires)]


def test_pending_key_is_not_signed() -> None:
    """ADR 0061: предпросмотр pending идёт через сервисы, подписанной ссылки на ``p/`` нет."""
    with pytest.raises(ValueError, match="не подлежит"):
        NginxSecureLinkSigner("s" * 40).sign(f"p/{HEX}.webp", EXPIRES)


@pytest.mark.parametrize(
    "key",
    [
        f"p/{HEX}.webp",
        f"u/{HEX}.webp",
        f"s/{HEX}.webp",
        "p/../a/x.webp",
        f"/a/{HEX}.webp",
        "a/../p/x.webp",
        "p/a/b.webp",
        "p/x",
    ],
)
def test_other_keys_are_not_signed(key: str) -> None:
    with pytest.raises(ValueError):
        NginxSecureLinkSigner("s" * 40).sign(key, EXPIRES)

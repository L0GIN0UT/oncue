"""Подпись ссылок: ``/media/`` подписывает ``a/`` и ``p/``, но не ``u/`` и не произвольные пути."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from media_pipeline_service.infrastructure.links import NginxSecureLinkSigner

pytestmark = pytest.mark.unit

EXPIRES = datetime(2026, 10, 23, 12, 2, tzinfo=UTC)
HEX = "ab" * 16


def test_pending_key_is_signed_under_media_prefix_with_same_algorithm() -> None:
    signer = NginxSecureLinkSigner("s" * 40)
    pending, asset = signer.sign(f"p/{HEX}.webp", EXPIRES), signer.sign(f"a/{HEX}.webp", EXPIRES)
    assert urlsplit(pending).path == f"/media/p/{HEX}.webp"
    expires = int(EXPIRES.timestamp())
    assert parse_qs(urlsplit(pending).query)["md5"] == [
        signer.token(f"/media/p/{HEX}.webp", expires)
    ]
    # путь входит в подпись: токен ассета к объекту на модерации не подходит
    assert parse_qs(urlsplit(pending).query)["md5"] != parse_qs(urlsplit(asset).query)["md5"]


@pytest.mark.parametrize(
    "key",
    [f"u/{HEX}.webp", f"s/{HEX}.webp", "p/../a/x.webp", f"/p/{HEX}.webp", "p/a/b.webp", "p/x"],
)
def test_other_keys_are_not_signed(key: str) -> None:
    with pytest.raises(ValueError):
        NginxSecureLinkSigner("s" * 40).sign(key, EXPIRES)

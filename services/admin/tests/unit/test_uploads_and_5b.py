"""Этап 5b: адрес notifications, пределы загрузки и сообщения о потолках эмиссии."""

from __future__ import annotations

import httpx
import pytest
from admin_service.core.service_client import problem_to_error
from admin_service.core.uploads import LimitedUploadHandler, limit_for_path
from admin_service.settings import AdminConfig
from django.conf import settings
from django.core.exceptions import RequestDataTooBig

pytestmark = pytest.mark.unit


def test_notifications_url_and_shop_ceiling_defaults() -> None:
    config = AdminConfig()
    assert config.service_urls["notifications"] == "http://notifications:8000"
    assert config.shop_media_max_bytes >= 1024


def test_django_upload_limits_not_below_shop_ceiling() -> None:
    ceiling = settings.GV_SHOP_MEDIA_MAX_BYTES
    # Файлы в DATA_UPLOAD_MAX_MEMORY_SIZE не входят (только поля формы): он остаётся 2 МБ.
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == 2 * 1024 * 1024
    assert ceiling <= settings.FILE_UPLOAD_MAX_MEMORY_SIZE
    assert settings.FILE_UPLOAD_HANDLERS[0].endswith("LimitedUploadHandler")


def test_limit_depends_on_path() -> None:
    shop = limit_for_path("/shop/items/3f2c/media/")
    other = limit_for_path("/catalog/items/3f2c/sources/")
    assert shop < other


class _Req:
    path = "/shop/items/abc/media/"


def test_handler_rejects_big_content_length_and_stream(settings: object) -> None:
    handler = LimitedUploadHandler(_Req())  # type: ignore[arg-type]
    limit = limit_for_path(_Req.path)
    with pytest.raises(RequestDataTooBig):
        handler.handle_raw_input(None, {}, limit + 1, "b")
    handler.handle_raw_input(None, {}, limit, "b")
    assert handler.receive_data_chunk(b"x" * limit, 0) == b"x" * limit
    with pytest.raises(RequestDataTooBig):
        handler.receive_data_chunk(b"y", limit)


def test_cap_details_are_added_to_message() -> None:
    body = {
        "type": "about:blank",
        "title": "x",
        "status": 422,
        "code": "progression.promo_batch_too_large",
        "details": {"limit": 1000, "used": 0, "requested": 5000},
    }
    error = problem_to_error("progression", httpx.Response(422, json=body))
    assert "лимит 1000" in error.message and "запрошено 5000" in error.message

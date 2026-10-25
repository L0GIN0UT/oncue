"""Жёсткий предел потока загрузки: тело запроса отвергается до записи на диск.

Django разбирает multipart потоково (крупное — во временный файл), но сам предел размера
файла не знает. ``LimitedUploadHandler`` смотрит ``Content-Length`` и считает принятые байты:
сверх предела — ``RequestDataTooBig`` (400), файл целиком не копируется ни в память, ни на диск.
Предел зависит от пути: картинки предметов — ``GV_SHOP_MEDIA_MAX_BYTES``, остальное —
``GV_MAX_UPLOAD_MB``.
"""

# ruff: noqa: ARG002 - сигнатуры методов обработчика задаёт Django
from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadhandler import FileUploadHandler

_SHOP_MEDIA_PATH = re.compile(r"^/(shop/items/[^/]+/media|events/[^/]+/map)/$")
_OVERHEAD = 64 * 1024  # поля формы и границы multipart


def limit_for_path(path: str) -> int:
    """Предел размера тела запроса с файлом для пути (байт, с запасом на поля формы)."""
    if _SHOP_MEDIA_PATH.match(path):
        return int(settings.GV_SHOP_MEDIA_MAX_BYTES) + _OVERHEAD
    return int(settings.GV_MAX_UPLOAD_MB) * 1024 * 1024 + _OVERHEAD


class LimitedUploadHandler(FileUploadHandler):
    """Первым в цепочке: считает байты и обрывает разбор при превышении предела."""

    def __init__(self, request: Any = None) -> None:
        super().__init__(request)
        self._limit = limit_for_path(getattr(request, "path", ""))
        self._received = 0

    def handle_raw_input(
        self,
        input_data: Any,
        META: dict[str, Any],  # noqa: N803 - имя из сигнатуры Django
        content_length: int,
        boundary: str,
        encoding: str | None = None,
    ) -> None:
        if content_length > self._limit:
            raise RequestDataTooBig("тело запроса больше допустимого")

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes:
        self._received += len(raw_data)
        if self._received > self._limit:
            raise RequestDataTooBig("тело запроса больше допустимого")
        return raw_data

    def file_complete(self, file_size: int) -> None:
        return None

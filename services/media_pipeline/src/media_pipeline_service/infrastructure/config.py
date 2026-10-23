"""Загрузка конфигов из YAML: профили уровней и назначения UGC-изображений."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from media_pipeline_service.application.generators import GeneratorRegistry
from media_pipeline_service.domain.errors import InvalidProfileError
from media_pipeline_service.domain.profiles import AssetProfileCatalog
from media_pipeline_service.domain.ugc import UgcCatalog, UgcPurpose, UgcReason

_BUILTIN = "asset_profiles.yaml"
_UGC_BUILTIN = "ugc_purposes.yaml"
# client_max_body_size gateway - 2m; тело multipart чуть больше файла.
_UGC_MAX_BYTES_LIMIT = 1_500_000

_Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
_FORMATS = frozenset({"jpeg", "png", "webp"})


def read_profiles_document(path: Path | None = None) -> dict[str, Any]:
    """Прочитать YAML профилей (``safe_load``: без исполнения тегов)."""
    if path is None:
        text = (
            resources.files("media_pipeline_service.config").joinpath(_BUILTIN).read_text("utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise InvalidProfileError("asset_profiles: ожидается объект верхнего уровня")
    return document


def load_profiles(registry: GeneratorRegistry, path: Path | None = None) -> AssetProfileCatalog:
    """Каталог профилей; ошибка конфига — сразу при старте процесса."""
    return registry.build_catalog(read_profiles_document(path))


class _PurposeDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(ge=1, le=_UGC_MAX_BYTES_LIMIT)
    input_formats: list[_Key] = Field(min_length=1)
    min_side_px: int = Field(ge=1)
    max_side_px: int = Field(ge=1, le=16_384)
    output_side_px: int = Field(ge=1, le=4096)
    allowed_clients: list[_Key] = Field(min_length=1)


class _ReasonDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: _Key
    title: str = Field(min_length=1, max_length=200)


class _UgcDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    purposes: dict[_Key, _PurposeDoc]
    reasons: list[_ReasonDoc] = Field(min_length=1)


def load_ugc_catalog(path: Path | None = None) -> UgcCatalog:
    """Назначения и причины UGC (``GV_MEDIA__UGC_PURPOSES_PATH`` или встроенный файл).

    Ошибка конфига (лишний ключ, лимит выше предела gateway, неизвестный формат, повтор
    кода причины) - исключение при первом обращении.
    """
    if path is None:
        text = (
            resources.files("media_pipeline_service.config")
            .joinpath(_UGC_BUILTIN)
            .read_text("utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise InvalidProfileError("ugc_purposes: ожидается объект верхнего уровня")
    try:
        parsed = _UgcDocument.model_validate(document)
    except ValidationError as exc:
        raise InvalidProfileError(f"ugc_purposes: {exc.error_count()} ошибок конфига") from exc
    purposes: dict[str, UgcPurpose] = {}
    for key, p in parsed.purposes.items():
        if not set(p.input_formats) <= _FORMATS:
            raise InvalidProfileError(f"ugc_purposes.{key}: формат вне jpeg/png/webp")
        if p.min_side_px > p.max_side_px:
            raise InvalidProfileError(f"ugc_purposes.{key}: min_side_px больше max_side_px")
        purposes[key] = UgcPurpose(
            key=key,
            max_bytes=p.max_bytes,
            input_formats=tuple(p.input_formats),
            min_side_px=p.min_side_px,
            max_side_px=p.max_side_px,
            output_side_px=p.output_side_px,
            allowed_clients=frozenset(p.allowed_clients),
        )
    codes = [r.code for r in parsed.reasons]
    if len(set(codes)) != len(codes):
        raise InvalidProfileError("ugc_purposes: коды причин повторяются")
    return UgcCatalog(purposes, tuple(UgcReason(r.code, r.title) for r in parsed.reasons))

"""Загрузка словарей конструктора (теги, пресеты обложек) из YAML (``GV_COLLECTIONS__META_CONFIG``).

Битый файл — ошибка старта процесса (``ValueError``): лучше не подняться, чем молча
отдать пустой словарь и отклонять все публикации с тегами.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from collections_service.domain.model import Domain
from collections_service.domain.policy import CoverPreset, MetaConfig, Tag
from gv_text.emoji import is_strict_emoji

BUILTIN = "meta.yaml"
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Cover(_Model):
    id: Key
    gradient: list[Color] = Field(min_length=2, max_length=4)
    emoji: str = Field(min_length=1, max_length=32)


class _Tag(_Model):
    key: Key
    title_key: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,99}$")
    domains: list[Literal["music", "film"]] = Field(min_length=1)
    search: list[Annotated[str, StringConstraints(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=10
    )


class _Meta(_Model):
    cover_presets: list[_Cover] = Field(min_length=1, max_length=100)
    tags: list[_Tag] = Field(max_length=500)


def parse_meta(text: str) -> MetaConfig:
    """Словари из текста YAML.

    Raises:
        ValueError: файл не проходит схему (повторы ключей, не-эмодзи у пресета и т. п.).
    """
    try:
        raw = _Meta.model_validate(yaml.safe_load(text))
    except (ValidationError, yaml.YAMLError) as exc:
        raise ValueError(f"неверный конфиг словарей сборников: {exc}") from exc
    for kind, keys in (
        ("cover_presets", [p.id for p in raw.cover_presets]),
        ("tags", [t.key for t in raw.tags]),
    ):
        if len(set(keys)) != len(keys):
            raise ValueError(f"повторяются ключи {kind}")
    for preset in raw.cover_presets:
        if not is_strict_emoji(preset.emoji, max_graphemes=1):
            raise ValueError(f"эмодзи пресета {preset.id} — не один RGI-эмодзи")
    return MetaConfig(
        cover_presets=tuple(
            CoverPreset(id=p.id, gradient=tuple(p.gradient), emoji=p.emoji)
            for p in raw.cover_presets
        ),
        tags=tuple(
            Tag(
                key=t.key,
                title_key=t.title_key,
                domains=tuple(Domain(d) for d in t.domains),
                search_terms=tuple(t.search),
            )
            for t in raw.tags
        ),
    )


def load_meta(path: Path | None) -> MetaConfig:
    """Словари из файла; ``None`` — встроенный ``collections_service/config/meta.yaml``."""
    if path is None:
        text = files("collections_service").joinpath("config", BUILTIN).read_text("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return parse_meta(text)

"""Загрузка конфига полок: ``GV_LIBRARY__SHELVES_CONFIG`` или встроенный ``config/shelves.yaml``."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import yaml

from library_service.domain.shelves import CATALOG_DOMAINS, ShelfCatalog, ShelvesConfigError

BUILTIN = "shelves.yaml"


def load_shelf_catalog(path: Path | None = None) -> ShelfCatalog:
    """Разобрать конфиг полок.

    Args:
        path: Свой файл; ``None`` — встроенный конфиг пакета.

    Raises:
        ShelvesConfigError: файл не YAML или не проходит проверку.
        OSError: файла нет.
    """
    if path is None:
        text = files("library_service").joinpath("config", BUILTIN).read_text("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ShelvesConfigError(f"конфиг полок — не YAML: {exc}") from exc
    return ShelfCatalog.from_mapping(data, domains=CATALOG_DOMAINS)

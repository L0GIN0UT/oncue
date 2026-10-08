"""Создание сервиса из шаблона ``templates/service``."""

from __future__ import annotations

import subprocess
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath

from jinja2 import Environment, StrictUndefined

from gv_cli.registry import Service

_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,  # noqa: S701 — генерируем Python и TOML
)

# Части шаблона, нужные только сервисам со своей БД.
_DB_ONLY = (
    "src/__pkg__/infrastructure/db",
    "src/__pkg__/infrastructure/messaging",
    "tests/integration",
)


def _walk(node: Traversable, prefix: PurePosixPath) -> list[tuple[PurePosixPath, Traversable]]:
    items: list[tuple[PurePosixPath, Traversable]] = []
    for child in node.iterdir():
        rel = prefix / child.name
        if child.is_dir():
            items.extend(_walk(child, rel))
        elif child.name != "__pycache__":
            items.append((rel, child))
    return items


def render_service(service: Service) -> dict[str, str]:
    """Файлы сервиса: относительный путь → содержимое."""
    root = resources.files("gv_cli").joinpath("templates/service")
    files: dict[str, str] = {}
    for rel, node in _walk(root, PurePosixPath()):
        rel_str = str(rel)
        if not service.database and rel_str.startswith(_DB_ONLY):
            continue
        text = node.read_text(encoding="utf-8")
        if rel_str.endswith(".j2"):
            text = _env.from_string(text).render(s=service)
            rel_str = rel_str[: -len(".j2")]
        files[rel_str.replace("__pkg__", service.package)] = text
    return files


def create_service(root: Path, service: Service, *, overwrite: bool = False) -> Path:
    """Создать каталог ``services/<name>`` из шаблона."""
    target = root / "services" / service.name
    if target.exists() and not overwrite:
        raise SystemExit(f"{target} уже существует")
    for rel, content in render_service(service).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    # Порядок импортов зависит от конфигурации isort репозитория — доверяем его ruff.
    subprocess.run(
        ["ruff", "check", "--fix", "--select", "I", "-q", str(target)], cwd=root, check=False
    )
    subprocess.run(["ruff", "format", "-q", str(target)], cwd=root, check=False)
    return target


def registry_entry(name: str, description: str, *, public: str, database: bool) -> str:
    """Блок TOML для добавления сервиса в реестр."""
    return (
        f"\n[services.{name}]\n"
        f'description = "{description}"\n'
        f'public = "{public}"\n'
        f"database = {str(database).lower()}\n"
        "redis = false\n"
        f"kafka = {str(database).lower()}\n"
        "rabbitmq = false\n"
        "memory_mb = 256\n"
    )

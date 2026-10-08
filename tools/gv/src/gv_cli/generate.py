"""Генерация производных конфигов из реестра сервисов."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from gv_cli.registry import Service

_env = Environment(
    loader=PackageLoader("gv_cli", "templates/gen"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,  # генерируем конфиги, а не HTML  # noqa: S701
)


@dataclass(frozen=True, slots=True)
class Target:
    """Сгенерированный файл: путь относительно корня и шаблон."""

    path: str
    template: str
    executable: bool = False


TARGETS: tuple[Target, ...] = (
    Target("infra/compose/compose.services.yml", "compose.services.yml.j2"),
    Target("infra/compose/compose.services.dev.yml", "compose.services.dev.yml.j2"),
    Target(
        "infra/compose/postgres/10-service-databases.sh", "service-databases.sh.j2", executable=True
    ),
    Target("infra/compose/.env.example", "env.example.j2"),
    Target("infra/nginx/generated/upstreams.conf", "nginx-upstreams.conf.j2"),
    Target("infra/nginx/generated/routes.conf", "nginx-routes.conf.j2"),
    Target("infra/observability/prometheus/services.yml", "prometheus-services.yml.j2"),
    Target(".importlinter", "importlinter.j2"),
)


def render_all(services: list[Service]) -> dict[str, str]:
    """Отрисовать все цели: путь → содержимое."""
    return {t.path: _env.get_template(t.template).render(services=services) for t in TARGETS}


def write_all(root: Path, services: list[Service]) -> list[str]:
    """Записать изменившиеся файлы. Возвращает список изменённых путей."""
    changed: list[str] = []
    for target in TARGETS:
        content = _env.get_template(target.template).render(services=services)
        path = root / target.path
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        if target.executable:
            path.chmod(0o755)
        changed.append(target.path)
    return changed


def stale(root: Path, services: list[Service]) -> list[str]:
    """Файлы, которые расходятся с реестром (для ``gv gen --check``)."""
    result: list[str] = []
    for path, content in render_all(services).items():
        file = root / path
        if not file.exists() or file.read_text(encoding="utf-8") != content:
            result.append(path)
    return result

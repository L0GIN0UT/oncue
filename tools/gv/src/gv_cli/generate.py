"""Генерация производных конфигов из реестра сервисов."""

from __future__ import annotations

import json
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


SERVICE_CLIENTS: dict[str, dict[str, list[str]]] = {
    "game": {"media_pipeline": ["assets.link"]},
    "admin": {"identity": ["admin_sessions"]},
}
"""Клиенты сервисных токенов для compose (D13 плана этапа 2): клиент → адресат → области.

Попадает в ``GV_SERVICE_CLIENTS`` identity (``.env.example``) и в окружение клиента
(``GV_SERVICE_AUTH__*`` в ``compose.services.yml``). Секреты генерирует ``gv dev secrets``.
"""


COOKIE_MUTATION_SERVICES: frozenset[str] = frozenset({"identity", "game", "collections", "library"})
"""Сервисы с мутациями по cookie (CSRF-проверка Origin по ``GV_PUBLIC_ORIGINS``).

bff_web только читает (GET), admin — Django со своей CSRF-защитой.
"""


def service_clients_json() -> str:
    """``GV_SERVICE_CLIENTS`` с пустыми ``secret_sha256`` (их заполняет ``gv dev secrets``)."""
    return json.dumps(
        [
            {"client_id": client_id, "secret_sha256": "", "audiences": audiences}
            for client_id, audiences in SERVICE_CLIENTS.items()
        ],
        separators=(",", ":"),
    )


def service_secret_env(client_id: str) -> str:
    """Переменная ``.env`` с секретом клиента: ``GV_SERVICE_SECRET_<CLIENT>``."""
    return f"GV_SERVICE_SECRET_{client_id.upper()}"


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


def _render(template: str, services: list[Service]) -> str:
    return _env.get_template(template).render(
        services=services,
        service_clients=SERVICE_CLIENTS,
        service_clients_json=service_clients_json(),
        service_secret_env=service_secret_env,
        cookie_mutation_services=COOKIE_MUTATION_SERVICES,
    )


def render_all(services: list[Service]) -> dict[str, str]:
    """Отрисовать все цели: путь → содержимое."""
    return {t.path: _render(t.template, services) for t in TARGETS}


def write_all(root: Path, services: list[Service]) -> list[str]:
    """Записать изменившиеся файлы. Возвращает список изменённых путей."""
    changed: list[str] = []
    for target in TARGETS:
        content = _render(target.template, services)
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

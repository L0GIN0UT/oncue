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
    # Этап 4a (§2.7): план раундов матча у game (D5) и ссылки на ассеты уровней.
    "match": {"game": ["rounds.plan"], "media_pipeline": ["assets.link"]},
    # Этап 5b: progression передаёт файл аватарки в media-pipeline (S3); notifications берёт
    # подтверждённые адреса получателей рассылки у identity.
    "progression": {"media_pipeline": ["ugc.image"]},
    "notifications": {"identity": ["users.emails.read"]},
}
"""Клиенты сервисных токенов для compose (D13 плана этапа 2): клиент → адресат → области.

Попадает в ``GV_SERVICE_CLIENTS`` identity (``.env.example``) и в окружение клиента
(``GV_SERVICE_AUTH__*`` в ``compose.services.yml``). Секреты генерирует ``gv dev secrets``.
"""


COOKIE_MUTATION_SERVICES: frozenset[str] = frozenset(
    {
        "identity",
        "game",
        "collections",
        "library",
        "match",
        "progression",
        "notifications",
        "social",
    }
)
"""Сервисы с мутациями по cookie (CSRF-проверка Origin по ``GV_PUBLIC_ORIGINS``).

progression (этап 5a) — спин колеса, 5b — покупка, оформление, промокод, аватарка;
notifications (5b) — «прочитано», настройки, устройства; social (5c) — заявки, блокировки,
комментарии, жалобы. bff_web только читает (GET), admin —
Django со своей CSRF-защитой.
"""


REALTIME_TOKEN_SECRETS: dict[str, tuple[str, str]] = {
    "identity": ("GV_REALTIME__CONNECTION_TOKEN_SECRET", "CENTRIFUGO_TOKEN_HMAC_SECRET"),
    "match": ("GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET", "CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET"),
}
"""Секреты токенов Centrifugo (D9 этапа 4): сервис → (переменная сервиса, переменная ``.env``).

identity подписывает connection-токены, match — subscription-токены каналов комнат. Секреты
разные: компрометация match не даёт подделать вход в Centrifugo от имени любого пользователя.
Сервис из этого словаря обязан иметь ``realtime = true`` в реестре (проверяет ``gv gen``).
"""


REALTIME_PUBLISHERS: frozenset[str] = frozenset({"match", "notifications"})
"""Сервисы, которые публикуют в Centrifugo через server API (получают ``GV_REALTIME__API_*``).

identity только подписывает connection-токены: ключ server API ему не нужен (минимум прав —
утечка env identity не даёт публиковать в каналы комнат). notifications (5b) публикует в личные
каналы ``personal:#<user>``, токенов не выдаёт и не подписывает.
Сервис из этого множества обязан иметь ``realtime = true`` в реестре (проверяет ``gv gen``).
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


def _check_realtime(services: list[Service]) -> None:
    missing = sorted(
        s.name
        for s in services
        if (s.name in REALTIME_TOKEN_SECRETS or s.name in REALTIME_PUBLISHERS) and not s.realtime
    )
    if missing:
        raise SystemExit(f"секрет токенов Centrifugo задан для сервисов без realtime: {missing}")


def _render(template: str, services: list[Service]) -> str:
    _check_realtime(services)
    return _env.get_template(template).render(
        realtime_token_secrets=REALTIME_TOKEN_SECRETS,
        realtime_publishers=REALTIME_PUBLISHERS,
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

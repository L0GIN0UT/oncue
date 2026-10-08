"""Чтение реестра сервисов ``infra/services.toml``."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
BASE_PORT = 8000


def repo_root(start: Path | None = None) -> Path:
    """Корень монорепо — каталог с ``infra/services.toml``."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "infra" / "services.toml").is_file():
            return candidate
    raise SystemExit("не найден корень репозитория (infra/services.toml)")


@dataclass(frozen=True, slots=True)
class Service:
    """Сервис из реестра."""

    name: str
    description: str
    public: str
    database: bool
    redis: bool
    kafka: bool
    rabbitmq: bool
    memory_mb: int
    index: int

    @property
    def package(self) -> str:
        """Python-пакет сервиса. Суффикс ``_service`` исключает конфликты со stdlib
        (``collections``) и с библиотеками."""
        return f"{self.name}_service"

    @property
    def dns(self) -> str:
        """Имя контейнера и хоста в сети: дефисы вместо подчёркиваний (RFC 1123)."""
        return self.name.replace("_", "-")

    @property
    def distribution(self) -> str:
        """Имя Python-дистрибутива."""
        return f"guessverse-{self.dns}"

    @property
    def dev_port(self) -> int:
        """Порт на хосте для локальной разработки (в контейнере всегда 8000)."""
        return BASE_PORT + 100 + self.index

    @property
    def db_password_env(self) -> str:
        """Переменная окружения с паролем БД сервиса."""
        return f"PG_PASSWORD_{self.name.upper()}"


def load_services(root: Path) -> list[Service]:
    """Загрузить и проверить реестр."""
    data = tomllib.loads((root / "infra" / "services.toml").read_text(encoding="utf-8"))
    services: list[Service] = []
    publics: set[str] = set()
    for index, (name, raw) in enumerate(data.get("services", {}).items()):
        if not NAME_RE.match(name):
            raise SystemExit(f"недопустимое имя сервиса: {name!r}")
        public = str(raw.get("public", ""))
        if public:
            if not re.match(r"^[a-z][a-z0-9-]*$", public):
                raise SystemExit(f"{name}: недопустимый public-префикс {public!r}")
            if public in publics:
                raise SystemExit(f"{name}: public-префикс {public!r} уже занят")
            publics.add(public)
        services.append(
            Service(
                name=name,
                description=str(raw["description"]),
                public=public,
                database=bool(raw.get("database", False)),
                redis=bool(raw.get("redis", False)),
                kafka=bool(raw.get("kafka", False)),
                rabbitmq=bool(raw.get("rabbitmq", False)),
                memory_mb=int(raw.get("memory_mb", 256)),
                index=index,
            )
        )
    return services

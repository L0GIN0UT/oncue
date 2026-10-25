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
    framework: str = "fastapi"
    apt: tuple[str, ...] = ()
    s3: bool = False
    realtime: bool = False
    """Сервис работает с Centrifugo (публикация через server API, выдача токенов; D9 этапа 4)."""
    app_role: bool = False
    """Отдельная роль Postgres для рантайма (``<имя>_app``): владелец схемы (миграции) отличается
    от роли api/relay/consumer, у которой нет прав менять и отключать триггеры append-only
    журналов (ADR 0060)."""

    @property
    def is_django(self) -> bool:
        """Сервис на Django (админка): своя структура, без OpenAPI-снимка и слоёв hexagonal."""
        return self.framework == "django"

    @property
    def apt_packages(self) -> str:
        """Системные пакеты образа через пробел (build-arg APT_PACKAGES)."""
        return " ".join(self.apt)

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

    @property
    def db_app_user(self) -> str:
        """Рантайм-роль БД при ``app_role`` (владелец схемы остаётся ``name``)."""
        return f"{self.name}_app"

    @property
    def db_app_password_env(self) -> str:
        """Переменная окружения с паролем рантайм-роли."""
        return f"PG_PASSWORD_{self.name.upper()}_APP"


FRAMEWORKS = frozenset({"fastapi", "django"})
APT_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")


def _framework(name: str, raw: dict[str, object]) -> str:
    framework = str(raw.get("framework", "fastapi"))
    if framework not in FRAMEWORKS:
        raise SystemExit(f"{name}: framework должен быть одним из {sorted(FRAMEWORKS)}")
    return framework


def _apt(name: str, raw: dict[str, object]) -> tuple[str, ...]:
    packages = raw.get("apt", [])
    if not isinstance(packages, list) or not all(
        isinstance(p, str) and APT_RE.match(p) for p in packages
    ):
        raise SystemExit(f"{name}: apt — список имён пакетов Debian")
    return tuple(packages)


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
                framework=_framework(name, raw),
                apt=_apt(name, raw),
                s3=bool(raw.get("s3", False)),
                realtime=bool(raw.get("realtime", False)),
                app_role=bool(raw.get("app_role", False)),
            )
        )
    return services

"""Периодические задачи social (relay, под advisory lock; идемпотентны).

* ``social.config.bootstrap`` - версия 1 конфига из ``GV_SOCIAL__BOOTSTRAP_CONFIG_DIR``
  (только local/ci), если опубликованной версии нет (ADR 0053).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml

from gv_service import Environment, Infrastructure, PeriodicJob
from social_service.application.config_admin import ConfigCommands
from social_service.domain.config_rules import ConfigInvalidError
from social_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

BOOTSTRAP_JOB = "social.config.bootstrap"
BOOTSTRAP_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})
SEED_FILE = "config.yaml"
SEED_ACTOR = UUID(int=0)
"""Системный «автор» сида в аудите (человека за bootstrap нет)."""
SEED_REASON = "bootstrap сидов"


def build_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Задачи сервиса (без БД - нет задач)."""
    if infra.sessions is None:
        return []
    c = container_for(infra)
    s = c.settings.social
    directory = s.bootstrap_config_dir
    jobs: list[PeriodicJob] = []
    if directory is not None and c.settings.environment in BOOTSTRAP_ENVIRONMENTS:

        async def bootstrap() -> None:
            await bootstrap_config(c.config_commands, directory)

        jobs.append(PeriodicJob(BOOTSTRAP_JOB, s.bootstrap_interval_s, bootstrap, 0.0))
    return jobs


def _read_seed(directory: Path) -> Any:  # Any: произвольный YAML до проверки
    """Прочитать ``config.yaml`` (в потоке, вне event loop)."""
    return yaml.safe_load((directory / SEED_FILE).read_text(encoding="utf-8"))


async def bootstrap_config(commands: ConfigCommands, directory: Path) -> bool:
    """Опубликовать версию 1 из сида, если опубликованной версии нет; ``True`` - опубликовали.

    Битый или отсутствующий файл логируется (ERROR) и не роняет задачу.
    """
    try:
        raw = await asyncio.to_thread(_read_seed, directory)
    except (OSError, yaml.YAMLError):
        log.exception("social.bootstrap_unreadable", directory=str(directory))
        return False
    if not isinstance(raw, dict):
        log.error("social.bootstrap_invalid_file", file=SEED_FILE)
        return False
    try:
        published = await commands.bootstrap(raw, actor_id=SEED_ACTOR, reason=SEED_REASON)
    except ConfigInvalidError:
        log.exception("social.bootstrap_config_invalid", file=SEED_FILE)
        return False
    if published:
        log.info("social.config_bootstrapped")
    return published

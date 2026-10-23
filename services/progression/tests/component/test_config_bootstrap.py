"""Загрузка сида конфига (`progression.config.bootstrap`, только local/ci).

Отложенное начисление (событие пришло до конфига и обработано после публикации) покрыто в
`test_accrual_solo_daily.py` (повтор доставки после `publish_config`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from progression_service.application.admin.common import body_sha256
from progression_service.infrastructure.db.tables import TABLES, config_versions
from progression_service.infrastructure.jobs import (
    BOOTSTRAP_JOB,
    SEED_ACTOR,
    SEED_REASON,
    bootstrap_config,
    build_jobs,
)
from progression_service.settings import ProgressionSettings, Settings
from pydantic import SecretStr
from sqlalchemy import select

from gv_service import Environment, Infrastructure
from gv_testkit import run_periodic_job

pytestmark = pytest.mark.component

SEEDS = Path(__file__).resolve().parents[4] / "seeds" / "progression"


async def versions(service: Any) -> list[Any]:
    assert service.infra.sessions is not None
    async with service.infra.sessions() as session:
        return list((await session.execute(select(config_versions))).mappings())


async def test_bootstrap_publishes_version_one_and_is_noop_afterwards(progression: Any) -> None:
    commands = progression.container.admin.configs

    assert await bootstrap_config(commands, SEEDS) is True

    [row] = await versions(progression)
    assert (row["version"], row["status"], row["created_by"]) == (1, "published", SEED_ACTOR)
    assert row["body"] == yaml.safe_load((SEEDS / "config.yaml").read_text("utf-8"))
    assert body_sha256(row["body"])
    assert await progression.rows("admin_actions", reason=SEED_REASON) != []

    assert await bootstrap_config(commands, SEEDS) is False
    assert len(await versions(progression)) == 1


async def test_bootstrap_does_not_touch_existing_published_version(progression: Any) -> None:
    await progression.publish_config(version=7)

    assert await bootstrap_config(progression.container.admin.configs, SEEDS) is False

    assert [(r["version"], r["status"]) for r in await versions(progression)] == [(7, "published")]


async def test_invalid_seed_is_not_published(progression: Any, tmp_path: Path) -> None:
    broken = yaml.safe_load((SEEDS / "config.yaml").read_text("utf-8"))
    broken["levels"]["max_level"] = 0
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(broken), "utf-8")

    assert await bootstrap_config(progression.container.admin.configs, tmp_path) is False
    assert await versions(progression) == []


async def test_missing_or_malformed_file_does_not_crash(progression: Any, tmp_path: Path) -> None:
    commands = progression.container.admin.configs
    assert await bootstrap_config(commands, tmp_path) is False  # файла нет

    (tmp_path / "config.yaml").write_text("- just\n- a list\n", "utf-8")
    assert await bootstrap_config(commands, tmp_path) is False
    assert await versions(progression) == []


async def _infra(database_url: str, environment: Environment) -> AsyncIterator[Infrastructure]:
    settings = Settings(
        database_url=SecretStr(database_url),
        environment=environment,
        progression=ProgressionSettings(bootstrap_config_dir=SEEDS),
    )
    infra = await Infrastructure.open(settings, TABLES)
    try:
        yield infra
    finally:
        await infra.close()


@pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.CI])
async def test_periodic_job_runs_in_local_and_ci(
    progression: Any, database_url: str, environment: Environment
) -> None:
    from progression_service.main import DEFINITION

    async for infra in _infra(database_url, environment):
        assert BOOTSTRAP_JOB in {j.name for j in build_jobs(infra)}
        await run_periodic_job(DEFINITION, BOOTSTRAP_JOB, infra)

    assert [(r["version"], r["status"]) for r in await versions(progression)] == [(1, "published")]


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
async def test_periodic_job_is_not_registered_outside_local_and_ci(
    progression: Any, database_url: str, environment: Environment
) -> None:
    async for infra in _infra(database_url, environment):
        assert BOOTSTRAP_JOB not in {j.name for j in build_jobs(infra)}

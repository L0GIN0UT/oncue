"""Конфиг social из БД: провайдер с кэшем, 503, bootstrap, версии (ADR 0053)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from social_service.application.config_access import current_config
from social_service.application.config_admin import (
    ConfigNotDraftError,
    ConfigVersionNotFoundError,
    DraftChangedError,
    body_sha256,
)
from social_service.application.ports.config import ConfigUnavailableError
from social_service.domain.config_rules import ConfigInvalidError
from social_service.infrastructure.container import SocialContainer
from social_service.infrastructure.db.repositories_config import DbConfigProvider
from social_service.infrastructure.db.tables_friends import config_versions
from social_service.infrastructure.jobs import (
    BOOTSTRAP_JOB,
    SEED_ACTOR,
    SEED_REASON,
    bootstrap_config,
    build_jobs,
)
from social_service.settings import Settings, SocialSettings

from gv_core.errors import ConflictError, DependencyUnavailableError
from gv_core.ids import uuid7
from gv_service import Environment

from .conftest import ADMIN, config_body

pytestmark = pytest.mark.component

Social = Any  # фикстура `social` описана в conftest.py
SEEDS = Path(__file__).resolve().parents[5] / "seeds" / "social"


def commands(social: Social) -> Any:
    return social.container.config_commands


def key() -> str:
    return f"idem-{uuid7().hex}"


async def versions(social: Social) -> list[Any]:
    return sorted(await social.rows(config_versions), key=lambda r: r.version)


# ---------------------------------------------------------------------- провайдер


async def test_no_published_version_gives_unavailable_and_503(social: Social) -> None:
    provider = social.container.config
    with pytest.raises(ConfigUnavailableError):
        await provider.current()
    with pytest.raises(DependencyUnavailableError) as raised:
        await current_config(provider)
    assert raised.value.code == "social.config_unavailable"


async def test_provider_caches_for_ttl_then_picks_up_new_version(social: Social) -> None:
    assert social.infra.sessions is not None
    provider = DbConfigProvider(social.infra.sessions, social.clock, ttl_s=10)
    await social.publish_config(1)
    first = await provider.current()
    assert (first.version, first.friends.max_friends) == (1, 200)

    await social.publish_config(2, friends__max_friends=5)
    assert (await provider.current()).version == 1  # кэш ещё действует

    social.clock.advance(seconds=11)
    second = await provider.current()
    assert (second.version, second.friends.max_friends) == (2, 5)


async def test_draft_is_not_effective_until_published(social: Social) -> None:
    await social.publish_config(1)
    await commands(social).save_draft(
        config_body(friends__max_friends=7), actor_id=ADMIN, reason="проверка", key=key()
    )
    social.container.config.invalidate()
    assert (await social.container.config.current()).friends.max_friends == 200


async def test_database_outage_after_cache_expiry_fails_closed(social: Social) -> None:
    class Broken:
        def __call__(self) -> Any:
            raise OSError("db down")

    provider = DbConfigProvider(Broken(), social.clock, ttl_s=5)  # type: ignore[arg-type]
    with pytest.raises(ConfigUnavailableError):
        await provider.current()


# ---------------------------------------------------------------------- версии (для F4)


async def test_save_publish_rollback_flow(social: Social) -> None:
    cmd = commands(social)
    saved = await cmd.save_draft(config_body(), actor_id=ADMIN, reason="первый", key=key())
    assert saved["card"]["status"] == "draft"
    sha = saved["card"]["body_sha256"]
    assert sha == body_sha256(config_body())

    with pytest.raises(DraftChangedError):
        await cmd.publish(1, "f" * 64, actor_id=ADMIN, reason="не тот хеш", key=key())
    published = await cmd.publish(1, sha, actor_id=ADMIN, reason="публикуем", key=key())
    assert published["card"]["status"] == "published"
    with pytest.raises(ConfigNotDraftError):
        await cmd.publish(1, sha, actor_id=ADMIN, reason="повторно", key=key())
    with pytest.raises(ConfigVersionNotFoundError):
        await cmd.publish(9, sha, actor_id=ADMIN, reason="нет такой", key=key())

    second = await cmd.save_draft(
        config_body(friends__max_friends=9), actor_id=ADMIN, reason="второй", key=key()
    )
    await cmd.publish(
        2, second["card"]["body_sha256"], actor_id=ADMIN, reason="публикуем", key=key()
    )
    assert [(r.version, r.status) for r in await versions(social)] == [
        (1, "archived"),
        (2, "published"),
    ]

    back = await cmd.rollback(1, actor_id=ADMIN, reason="откат", key=key())
    assert (back["card"]["version"], back["card"]["status"]) == (3, "draft")
    assert back["config"] == config_body()


async def test_commands_are_idempotent_and_reject_key_reuse(social: Social) -> None:
    cmd = commands(social)
    same = key()
    first = await cmd.save_draft(config_body(), actor_id=ADMIN, reason="один", key=same)
    again = await cmd.save_draft(config_body(), actor_id=ADMIN, reason="один", key=same)
    assert first == again
    assert len(await versions(social)) == 1
    with pytest.raises(ConflictError) as raised:
        await cmd.save_draft(
            config_body(friends__max_friends=3), actor_id=ADMIN, reason="один", key=same
        )
    assert raised.value.code == "idempotency.key_reused"


@pytest.mark.parametrize(
    "patch",
    [
        {"presence__ttl_s": 30},  # ttl_s не больше heartbeat_s
        {"comments__max_length": 501},  # выше границы схемы
        {"friends__max_friends": 0},
        {"sanctions__min_duration_s": 400000000},
    ],
)
async def test_invalid_body_is_rejected_and_not_stored(
    social: Social, patch: dict[str, Any]
) -> None:
    with pytest.raises(ConfigInvalidError) as raised:
        await commands(social).save_draft(
            config_body(**patch), actor_id=ADMIN, reason="плохой", key=key()
        )
    assert raised.value.code == "social.config_invalid"
    assert raised.value.details["errors"]
    assert await versions(social) == []


async def test_on_change_hook_runs_in_the_same_transaction(social: Social) -> None:
    seen: list[tuple[str, int, UUID]] = []

    async def hook(uow: Any, change: Any) -> None:
        seen.append((change.action, change.version, change.actor_id))

    from social_service.application.config_admin import ConfigCommands
    from social_service.infrastructure.config_validation import validate_config_body

    cmd = ConfigCommands(
        social.container.uow_factory(), social.clock, validate_config_body, on_change=hook
    )
    saved = await cmd.save_draft(config_body(), actor_id=ADMIN, reason="аудит", key=key())
    await cmd.publish(1, saved["card"]["body_sha256"], actor_id=ADMIN, reason="аудит", key=key())
    assert seen == [("config.save_draft", 1, ADMIN), ("config.publish", 1, ADMIN)]


# ---------------------------------------------------------------------- bootstrap


async def test_bootstrap_publishes_version_one_and_is_noop_afterwards(social: Social) -> None:
    cmd = commands(social)
    assert await bootstrap_config(cmd, SEEDS) is True

    [row] = await versions(social)
    assert (row.version, row.status, row.created_by) == (1, "published", SEED_ACTOR)
    assert row.body == yaml.safe_load((SEEDS / "config.yaml").read_text("utf-8"))
    assert (await social.container.config.current()).version == 1

    assert await bootstrap_config(cmd, SEEDS) is False
    assert len(await versions(social)) == 1


async def test_bootstrap_does_not_touch_existing_published_version(social: Social) -> None:
    await social.publish_config(7)
    assert await bootstrap_config(commands(social), SEEDS) is False
    assert [(r.version, r.status) for r in await versions(social)] == [(7, "published")]


async def test_invalid_or_missing_seed_does_not_crash_or_publish(
    social: Social, tmp_path: Path
) -> None:
    cmd = commands(social)
    assert await bootstrap_config(cmd, tmp_path) is False  # файла нет
    (tmp_path / "config.yaml").write_text("- just\n- a list\n", "utf-8")
    assert await bootstrap_config(cmd, tmp_path) is False
    broken = yaml.safe_load((SEEDS / "config.yaml").read_text("utf-8"))
    broken["presence"]["ttl_s"] = 1
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(broken), "utf-8")
    assert await bootstrap_config(cmd, tmp_path) is False
    assert await versions(social) == []


async def test_concurrent_bootstrap_publishes_one_version(social: Social) -> None:
    import asyncio

    results = await asyncio.gather(
        *(bootstrap_config(commands(social), SEEDS) for _ in range(4)), return_exceptions=True
    )
    assert [
        r for r in results if isinstance(r, BaseException) and not isinstance(r, ConflictError)
    ] == []
    rows = await versions(social)
    assert [(r.version, r.status) for r in rows] == [(1, "published")]


async def test_job_is_registered_only_in_local_and_ci_with_directory(social: Social) -> None:
    infra = social.infra
    container = SocialContainer(infra)  # свежий контейнер: настройки подменяются ниже

    def jobs_for(environment: Environment, directory: Path | None) -> list[str]:
        settings = Settings(
            environment=environment,
            database_url=infra.settings.database_url,
            social=SocialSettings(bootstrap_config_dir=directory),
        )
        local_infra = type(infra)(settings=settings, tables=infra.tables, clock=infra.clock)
        local_infra.sessions = infra.sessions
        return [j.name for j in build_jobs(local_infra)]

    del container
    assert jobs_for(Environment.LOCAL, SEEDS) == [BOOTSTRAP_JOB]
    assert jobs_for(Environment.CI, SEEDS) == [BOOTSTRAP_JOB]
    assert jobs_for(Environment.PRODUCTION, SEEDS) == []
    assert jobs_for(Environment.LOCAL, None) == []
    assert SEED_REASON

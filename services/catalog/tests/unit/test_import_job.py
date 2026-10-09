"""Агрегат ImportJob: переходы состояний и счётчики."""

from __future__ import annotations

import pytest
from catalog_service.domain.import_job import ImportJob, ImportSource, ImportStatus, ItemOutcome

from gv_core.errors import ConflictError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit


def job() -> ImportJob:
    return ImportJob(
        uuid7(),
        source=ImportSource.FILE,
        ref=None,
        file_id="films.jsonl",
        actor_id=uuid7(),
        reason="seed",
        idempotency_key="k-000001",
    )


def test_happy_path_counts_and_finishes() -> None:
    j = job()
    assert j.status is ImportStatus.PENDING
    assert j.start()
    j.count(ItemOutcome.CREATED)
    j.count(ItemOutcome.CREATED)
    j.count(ItemOutcome.FAILED)
    j.finish()
    assert j.is_finished
    assert j.counters.as_dict() == {"created": 2, "updated": 0, "unchanged": 0, "failed": 1}


def test_finished_job_is_not_restarted() -> None:
    j = job()
    j.start()
    j.finish()
    assert not j.start()
    assert j.status is ImportStatus.DONE


def test_running_job_can_restart_after_redelivery() -> None:
    j = job()
    j.start()
    assert j.start()


def test_count_requires_running() -> None:
    with pytest.raises(ConflictError):
        job().count(ItemOutcome.CREATED)


def test_fail_is_terminal_and_truncates_error() -> None:
    j = job()
    j.start()
    j.fail("x" * 1000)
    assert j.is_finished
    assert j.error is not None
    assert len(j.error) == 500
    j.fail("other")
    assert j.error.startswith("x")

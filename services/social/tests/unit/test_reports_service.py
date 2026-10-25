"""Жалобы и проекции на двойниках портов: проверки, порог кейса, события, идемпотентность."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from social_fakes import Harness, example_config
from social_service.application.ports.cases import AvatarRef
from social_service.application.ports.discussion import NewReport
from social_service.application.projections import ProjectionService
from social_service.application.reports import ReportService
from social_service.domain.discussion import CollectionRef, Comment
from social_service.domain.discussion_events import ReportFiled

from gv_core.errors import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

AUTHOR = UUID(int=1)
TARGET = UUID(int=2)
MATCH = UUID(int=50)
COLLECTION = UUID(int=100)
COMMENT = UUID(int=200)


def _setup() -> tuple[Harness, ReportService]:
    h = Harness()
    h.world.participants[MATCH, AUTHOR] = False
    h.world.participants[MATCH, TARGET] = False
    h.world.collections[COLLECTION] = CollectionRef(
        COLLECTION, TARGET, "С", published=True, version=1
    )
    h.world.comments[COMMENT] = Comment(COMMENT, COLLECTION, TARGET, "текст", h.clock.now())
    return h, ReportService(h.uow, h.config, h.clock, uuid7)


async def _report_player(service: ReportService, author: UUID = AUTHOR, **kw: object) -> object:
    args = {"target_id": TARGET, "context_match_id": MATCH, "reason": "cheating", "text": None}
    args.update(kw)
    return await service.file(
        author,
        "player",
        args["target_id"],
        args["context_match_id"],
        args["reason"],
        args["text"],  # type: ignore[arg-type]
    )


async def test_reasons_are_localized_from_config() -> None:
    _, service = _setup()
    reasons = await service.reasons()
    assert {r.code for r in reasons} >= {"spam", "cheating"}
    spam = next(r for r in reasons if r.code == "spam")
    assert spam.title == "Спам или реклама"
    assert spam.target_kinds == ["collection", "comment"]


async def test_player_report_needs_shared_non_solo_match() -> None:
    h, service = _setup()
    for match in (None, UUID(int=51)):
        with pytest.raises(InvalidInputError) as err:
            await _report_player(service, context_match_id=match)
        assert err.value.code == "social.report_no_shared_match"
    h.world.participants[MATCH, TARGET] = True  # соло-запись не считается общим матчем
    with pytest.raises(InvalidInputError):
        await _report_player(service)


async def test_cannot_report_self_or_with_wrong_reason() -> None:
    _, service = _setup()
    with pytest.raises(InvalidInputError) as err:
        await _report_player(service, target_id=AUTHOR)
    assert err.value.code == "social.report_self"
    with pytest.raises(InvalidInputError) as err:
        await _report_player(service, reason="spam")  # причина не для игроков
    assert err.value.code == "social.report_reason_unknown"


async def test_text_limit_comes_from_config() -> None:
    h, service = _setup()
    h.config.config = example_config(reports={"text_max_length": 5})
    with pytest.raises(InvalidInputError) as err:
        await _report_player(service, text="длинный текст")
    assert err.value.code == "social.report_text_too_long"


async def test_collection_and_comment_reports_resolve_owner() -> None:
    h, service = _setup()
    outcome = await service.file(AUTHOR, "collection", COLLECTION, None, "spam", None)
    assert not outcome.repeated
    await service.file(AUTHOR, "comment", COMMENT, None, "spam", "текст жалобы")
    assert {r.target_user_id for r in h.world.reports} == {TARGET}
    with pytest.raises(NotFoundError):
        await service.file(AUTHOR, "collection", UUID(int=999), None, "spam", None)
    with pytest.raises(NotFoundError):
        await service.file(AUTHOR, "comment", UUID(int=999), None, "spam", None)
    with pytest.raises(InvalidInputError):  # автор жалуется на свой комментарий
        await service.file(TARGET, "comment", COMMENT, None, "spam", None)


async def test_avatar_report_uses_owner_from_projection() -> None:
    h, service = _setup()
    asset, owner = UUID(int=7), UUID(int=8)
    h.world.avatars[asset] = AvatarRef(asset, owner, "approved", 1)
    outcome = await service.file(AUTHOR, "avatar", asset, None, "inappropriate_content", None)
    assert not outcome.repeated
    assert h.world.reports[0].target_user_id == owner
    assert h.world.counters[owner]["reports_received"] == 1


async def test_avatar_report_unknown_or_rejected_asset_is_not_found() -> None:
    h, service = _setup()
    rejected = UUID(int=9)
    h.world.avatars[rejected] = AvatarRef(rejected, UUID(int=8), "rejected", 2)
    for asset in (UUID(int=7), rejected):
        with pytest.raises(NotFoundError) as err:
            await service.file(AUTHOR, "avatar", asset, None, "inappropriate_content", None)
        assert err.value.code == "social.report_target_not_found"


async def test_report_is_idempotent_per_author_and_object() -> None:
    h, service = _setup()
    first = await _report_player(service)
    second = await _report_player(service)
    assert second.repeated and second.id == first.id  # type: ignore[attr-defined]
    assert len(h.world.reports) == 1
    assert len(h.events("ReportFiled")) == 1
    assert h.world.counters[TARGET]["reports_received"] == 1


async def test_event_has_no_text_and_no_case_below_threshold() -> None:
    h, service = _setup()
    await _report_player(service, text="секрет")
    (event,) = h.events("ReportFiled")
    assert isinstance(event, ReportFiled)
    assert event.case_id is None
    assert "text" not in event.payload()
    assert not h.world.cases


async def test_threshold_opens_one_case_and_later_reports_join_it() -> None:
    h, service = _setup()
    threshold = h.config.config.reports.threshold_per_day
    authors = [UUID(int=1000 + i) for i in range(threshold + 2)]
    for a in authors:
        h.world.participants[MATCH, a] = False
    for a in authors:
        await _report_player(service, author=a)
    assert len(h.world.cases) == 1
    (case_id,) = h.world.cases
    assert {r for c, r in h.world.case_reports if c == case_id} == {
        r.id for r in h.world.reports[: len(authors)]
    }
    with_case = [e for e in h.events("ReportFiled") if e.case_id is not None]  # type: ignore[attr-defined]
    assert len(with_case) == len(authors) - threshold + 1


async def test_reports_outside_day_window_do_not_count() -> None:
    h, service = _setup()
    threshold = h.config.config.reports.threshold_per_day
    authors = [UUID(int=1000 + i) for i in range(threshold)]
    for a in authors:
        h.world.participants[MATCH, a] = False
    for a in authors[:-1]:
        await _report_player(service, author=a)
        h.clock.advance(timedelta(hours=14))  # к третьей жалобе первая старше суток
    await _report_player(service, author=authors[-1])
    assert not h.world.cases


async def test_resolved_case_is_not_resurrected_by_one_new_report() -> None:
    h, service = _setup()
    h.config.config = example_config(reports={"threshold_per_day": 3})
    authors = [UUID(int=1000 + i) for i in range(6)]
    for a in authors:
        h.world.participants[MATCH, a] = False
    for a in authors[:3]:
        await _report_player(service, author=a)
    (first_case,) = h.world.cases
    h.world.cases[first_case]["status"] = "resolved"

    await _report_player(service, author=authors[3])
    assert list(h.world.cases) == [first_case]  # старые жалобы уже учтены кейсом

    await _report_player(service, author=authors[4])
    await _report_player(service, author=authors[5])
    (second_case,) = (c for c in h.world.cases if c != first_case)
    attached = {r for c, r in h.world.case_reports if c == second_case}
    assert attached == {r.id for r in h.world.reports[3:]}


async def test_rating_report_without_player_reasons_is_skipped() -> None:
    h, service = _setup()
    h.config.config = example_config(
        reports={"reasons": [{"code": "spam", "target_kinds": ["comment"], "title": {"ru": "С"}}]}
    )
    assert await service.file_from_rating(AUTHOR, TARGET, MATCH, "cheating") is None
    assert not h.world.reports


async def test_per_day_limit_per_author() -> None:
    h, service = _setup()
    h.config.config = example_config(reports={"per_day": 2})
    await service.file(AUTHOR, "collection", COLLECTION, None, "spam", None)
    await service.file(AUTHOR, "comment", COMMENT, None, "spam", None)
    other = UUID(int=300)
    h.world.collections[other] = CollectionRef(other, TARGET, "С", published=True, version=1)
    with pytest.raises(RateLimitedError) as err:
        await service.file(AUTHOR, "collection", other, None, "spam", None)
    assert err.value.code == "social.reports_rate_limit"
    assert len(h.world.reports) == 2  # третья откатилась


# --- проекции ----------------------------------------------------------------------------


def _projections(h: Harness) -> ProjectionService:
    return ProjectionService(h.uow, ReportService(h.uow, h.config, h.clock, uuid7), h.clock)


def _snapshot(
    version: int, status: str = "published", author: str | None = None
) -> dict[str, object]:
    return {
        "collection_id": str(COLLECTION),
        "version": version,
        "status": status,
        "author_id": author or str(TARGET),
    }


async def test_collection_snapshot_ignores_stale_versions() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.collection_snapshot(_snapshot(3, "hidden"))
    await projections.collection_snapshot(_snapshot(2, "published"))
    ref = h.world.collections[COLLECTION]
    assert ref.version == 3 and not ref.published


async def test_collection_deleted_unpublishes() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.collection_snapshot(_snapshot(1))
    await projections.collection_deleted(
        {"collection_id": str(COLLECTION), "version": 2, "deleted_at": "2026-10-24T12:00:00Z"}
    )
    ref = h.world.collections[COLLECTION]
    assert not ref.published and ref.author_id == TARGET


def _roles(version: int, perms: list[str]) -> dict[str, object]:
    return {
        "user_id": str(TARGET),
        "roles": ["moderator"],
        "perms": perms,
        "version": version,
        "actor_id": None,
        "reason": None,
    }


async def test_roles_changed_keeps_latest_snapshot_and_ignores_stale() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.roles_changed(_roles(3, ["a.b", "c.d"]))
    await projections.roles_changed(_roles(2, ["x.y"]))
    await projections.roles_changed(_roles(3, ["x.y"]))  # тот же version - не новее
    assert h.world.staff_perms[TARGET] == (frozenset({"a.b", "c.d"}), 3)
    await projections.roles_changed(_roles(4, []))
    assert h.world.staff_perms[TARGET] == (frozenset(), 4)


async def test_user_deleted_clears_staff_perms() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.roles_changed(_roles(1, ["a.b"]))
    await projections.user_deleted({"user_id": str(TARGET)})
    assert TARGET not in h.world.staff_perms


def _finished(*, aborted: bool = False) -> dict[str, object]:
    def player(user: UUID, finish: str) -> dict[str, object]:
        return {
            "user_id": str(user),
            "finish": finish,
            "slot": 0,
            "score": 0,
            "place": 1,
            "outcome": None,
        }

    return {
        "match_id": str(MATCH),
        "aborted": aborted,
        "finished_at": "2026-10-24T12:00:00Z",
        "players": [player(AUTHOR, "completed"), player(TARGET, "forfeit")],
    }


async def test_match_finished_counts_once() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.match_finished(_finished())
    await projections.match_finished(_finished())
    assert h.world.counters[AUTHOR] == {"matches_total": 1}
    assert h.world.counters[TARGET] == {"matches_total": 1, "forfeits": 1}
    assert h.world.participants[MATCH, AUTHOR] is False


async def test_aborted_match_records_participants_but_no_counters() -> None:
    h = Harness()
    await _projections(h).match_finished(_finished(aborted=True))
    assert (MATCH, AUTHOR) in h.world.participants
    assert not h.world.counters


def _rated(kind: str, reason: str | None) -> dict[str, object]:
    return {
        "match_id": str(MATCH),
        "from_id": str(AUTHOR),
        "to_id": str(TARGET),
        "kind": kind,
        "reason": reason,
    }


async def test_player_rated_report_becomes_single_report() -> None:
    h = Harness()
    projections = _projections(h)
    await projections.player_rated(_rated("report", "cheating"))
    await projections.player_rated(_rated("report", "cheating"))
    (report,) = h.world.reports
    assert (report.author_id, report.target_user_id, report.reason_code) == (
        AUTHOR,
        TARGET,
        "cheating",
    )
    assert report.context_match_id == MATCH


async def test_player_rated_unknown_reason_falls_back_to_player_reason() -> None:
    h = Harness()
    await _projections(h).player_rated(_rated("report", None))
    (report,) = h.world.reports
    assert report.reason_code == "abusive_behaviour"


async def test_player_rated_praise_bumps_counter() -> None:
    h = Harness()
    await _projections(h).player_rated(_rated("praise", None))
    assert h.world.counters[TARGET] == {"praises": 1}
    assert not h.world.reports


async def test_user_deleted_removes_comments_and_report_texts() -> None:
    h, _ = _setup()
    h.world.reports.append(
        NewReport(
            uuid7(),
            TARGET,
            "comment",
            UUID(int=5),
            AUTHOR,
            None,
            "spam",
            "мой текст",
            h.clock.now(),
        )
    )
    await _projections(h).user_deleted({"user_id": str(TARGET), "version": 1})
    assert h.world.comments[COMMENT].is_deleted and h.world.comments[COMMENT].body == ""
    assert h.world.reports[-1].text is None
    (event,) = h.events("CommentDeleted")
    assert event.by_role == "account_deleted" and event.deleted_by is None  # type: ignore[attr-defined]

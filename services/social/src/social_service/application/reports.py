"""Жалобы и кейсы модерации (5c, ADR 0051).

Жалоба идемпотентна по ``(автор, объект)``. Порог жалоб за сутки на объект открывает кейс
ровно один раз: жалобы на один объект сериализуются блокировкой, а частичный UNIQUE на
кейсах - страховка. Жалобы после открытия присоединяются к открытому кейсу.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError, NotFoundError, RateLimitedError
from social_service.application.config_access import current_config
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.discussion import (
    DiscussionUnitOfWork,
    DiscussionUowFactory,
    NewReport,
)
from social_service.domain import discussion as rules
from social_service.domain.discussion_events import ReportFiled

DAY = timedelta(days=1)
log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReasonDto:
    """Причина жалобы для клиента."""

    code: str
    title: str
    target_kinds: list[str]


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """Результат подачи жалобы."""

    id: UUID
    repeated: bool


class ReportService:
    """Use-cases жалоб."""

    def __init__(
        self,
        uow_factory: DiscussionUowFactory,
        config: ConfigProvider,
        clock: Clock,
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow_factory
        self._config = config
        self._clock = clock
        self._new_id = new_id

    async def reasons(self, lang: str = rules.DEFAULT_LANG) -> list[ReasonDto]:
        """Справочник причин из конфига."""
        cfg = await current_config(self._config)
        return [
            ReasonDto(r.code, rules.localized(r.title, lang), sorted(r.target_kinds))
            for r in cfg.reports.reasons
        ]

    async def file(  # noqa: PLR0917 - поля жалобы
        self,
        author_id: UUID,
        target_kind: str,
        target_id: UUID,
        context_match_id: UUID | None,
        reason_code: str,
        text: str | None,
    ) -> ReportOutcome:
        """Подать жалобу от игрока (все проверки и лимиты)."""
        cfg = await current_config(self._config)
        reason = cfg.reports.reason(reason_code)
        if reason is None or target_kind not in reason.target_kinds:
            raise InvalidInputError("причина не подходит", code=rules.REPORT_REASON_UNKNOWN)
        if text is not None and len(text) > cfg.reports.text_max_length:
            raise InvalidInputError("текст жалобы слишком длинный", code=rules.REPORT_TEXT_TOO_LONG)
        async with self._uow() as uow:
            target_user_id = await self._resolve_owner(
                uow, author_id, target_kind, target_id, context_match_id
            )
            outcome = await self._record(
                uow,
                NewReport(
                    id=self._new_id(),
                    author_id=author_id,
                    target_kind=target_kind,
                    target_id=target_id,
                    target_user_id=target_user_id,
                    context_match_id=context_match_id if target_kind == "player" else None,
                    reason_code=reason_code,
                    text=text or None,
                    created_at=self._clock.now(),
                ),
                threshold=cfg.reports.threshold_per_day,
                per_day=cfg.reports.per_day,
            )
            await uow.commit()
        return outcome

    async def file_from_rating(
        self, author_id: UUID, target_user_id: UUID, match_id: UUID, reason: str | None
    ) -> ReportOutcome | None:
        """Жалоба из оценки игрока после матча (``match.PlayerRated``, kind = report).

        Матч уже подтвердил участие, поэтому проверки «общий матч» и суточный лимит не
        применяются; неизвестная причина заменяется первой причиной для игроков из справочника.
        Нет ни одной причины для игроков - жалоба пропускается (``None``), событие не ретраится.
        """
        if author_id == target_user_id:
            return None
        cfg = await current_config(self._config)
        known = cfg.reports.reason(reason) if reason else None
        if known is None or "player" not in known.target_kinds:
            known = next((r for r in cfg.reports.reasons if "player" in r.target_kinds), None)
        if known is None:
            # Ошибка конфигурации повтором не лечится, а исключение блокировало бы партицию.
            log.warning(
                "social.rating_report_skipped", reason="no_player_reasons", match_id=match_id
            )
            return None
        async with self._uow() as uow:
            outcome = await self._record(
                uow,
                NewReport(
                    id=self._new_id(),
                    author_id=author_id,
                    target_kind="player",
                    target_id=target_user_id,
                    target_user_id=target_user_id,
                    context_match_id=match_id,
                    reason_code=known.code,
                    text=None,
                    created_at=self._clock.now(),
                ),
                threshold=cfg.reports.threshold_per_day,
                per_day=None,
            )
            await uow.commit()
        return outcome

    async def _resolve_owner(
        self,
        uow: DiscussionUnitOfWork,
        author_id: UUID,
        kind: str,
        target_id: UUID,
        match_id: UUID | None,
    ) -> UUID:
        """Владелец объекта жалобы; проверяет существование и право жаловаться."""
        if kind == "player":
            owner = target_id
            if owner == author_id:
                raise InvalidInputError("нельзя жаловаться на себя", code=rules.REPORT_SELF)
            if match_id is None or not await uow.matches.shared_match(match_id, author_id, owner):
                raise InvalidInputError(
                    "нет общего матча с игроком", code=rules.REPORT_NO_SHARED_MATCH
                )
            return owner
        if kind == "collection":
            ref = await uow.collections.get(target_id)
            if ref is None or ref.author_id is None:
                raise NotFoundError("сборник не найден", code=rules.COLLECTION_NOT_FOUND)
            owner = ref.author_id
        elif kind == "avatar":
            # Владелец ассета известен из проекции media_pipeline.AssetModerated (одобренные).
            avatar = await uow.avatars.get(target_id)
            if avatar is None or avatar.status != "approved":
                raise NotFoundError("аватарка не найдена", code=rules.REPORT_TARGET_NOT_FOUND)
            owner = avatar.owner_user_id
        else:
            comment = await uow.comments.get(target_id)
            if comment is None or comment.is_deleted:
                raise NotFoundError("комментарий не найден", code=rules.COMMENT_NOT_FOUND)
            owner = comment.author_id
        if owner == author_id:
            raise InvalidInputError("нельзя жаловаться на себя", code=rules.REPORT_SELF)
        return owner

    async def _record(
        self, uow: DiscussionUnitOfWork, report: NewReport, *, threshold: int, per_day: int | None
    ) -> ReportOutcome:
        now = report.created_at
        since = now - DAY
        await uow.reports.lock_target(report.target_kind, report.target_id)
        inserted = await uow.reports.insert(report)
        if inserted is None:
            existing = await uow.reports.existing_id(
                report.author_id, report.target_kind, report.target_id
            )
            return ReportOutcome(existing, repeated=True)
        if (
            per_day is not None
            and await uow.reports.count_by_author_since(report.author_id, since) > per_day
        ):
            raise RateLimitedError(
                "слишком много жалоб за сутки",
                code=rules.REPORTS_RATE_LIMIT,
                details={"retry_after": int(DAY.total_seconds())},
            )
        case_id = await uow.reports.active_case_id(report.target_kind, report.target_id)
        if case_id is not None:
            await uow.reports.attach(case_id, [report.id])
        else:
            # Порог - только по жалобам вне кейсов: решённый кейс одной новой жалобой не оживает.
            recent = await uow.reports.unassigned_ids(report.target_kind, report.target_id, since)
            if rules.threshold_reached(len(recent), threshold):
                case_id = await uow.reports.open_case(
                    self._new_id(), report.target_kind, report.target_id, report.target_user_id, now
                )
                if case_id is None:
                    case_id = await uow.reports.active_case_id(report.target_kind, report.target_id)
                if case_id is not None:
                    await uow.reports.attach(case_id, recent)
        await uow.matches.bump(report.target_user_id, "reports_received")
        uow.emit(
            ReportFiled(
                aggregate_id=str(report.id),
                at=now,
                author_id=report.author_id,
                case_id=case_id,
                context_match_id=report.context_match_id,
                reason_code=report.reason_code,
                report_id=report.id,
                target_id=report.target_id,
                target_kind=report.target_kind,
                target_user_id=report.target_user_id,
            )
        )
        return ReportOutcome(report.id, repeated=False)

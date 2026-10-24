"""Схемы API social (5c; план этапа 5, §10.1; ADR 0050-0052).

Лимиты, справочник причин жалоб и сроки санкций - конфиг
(``contracts/social/social_config.v1.json``); здесь только жёсткие верхние границы схемы.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from gv_service import Reason

Nickname = Annotated[str, StringConstraints(min_length=3, max_length=24)]
FriendCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{8}$")]
RoomCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{4,16}$")]
Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]

PresenceStatus = Literal["online", "in_match", "offline"]
TargetKind = Literal["player", "collection", "comment", "avatar"]
SanctionKind = Literal["ranked", "chat", "online", "account"]
CaseStatus = Literal["open", "in_work", "escalated", "resolved"]
Decision = Literal["dismissed", "content_removed", "sanctioned"]
FriendRequestsPolicy = Literal["all", "none"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- друзья ---------------------------------------------------------------------------------


class FriendView(BaseModel):
    """Друг в списке владельца."""

    user_id: UUID
    nickname: Nickname
    status: PresenceStatus
    friends_since: datetime


class FriendList(BaseModel):
    """Список друзей (только владельцу)."""

    items: list[FriendView]
    total: int = Field(ge=0)


class FriendRequestCreate(_Request):
    """Заявка по нику, коду добавления или id игрока (ровно одно поле)."""

    nickname: Nickname | None = None
    code: FriendCode | None = None
    user_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = [v for v in (self.nickname, self.code, self.user_id) if v is not None]
        if len(given) != 1:
            raise ValueError("укажите ровно одно из nickname, code, user_id")
        return self


class FriendRequestView(BaseModel):
    """Заявка в друзья."""

    id: UUID
    user_id: UUID = Field(description="Вторая сторона: отправитель входящей, адресат исходящей")
    nickname: Nickname
    created_at: datetime


class FriendRequests(BaseModel):
    """Входящие и исходящие заявки в ожидании."""

    incoming: list[FriendRequestView]
    outgoing: list[FriendRequestView]


class FriendCodeView(BaseModel):
    """Код добавления в друзья (виден только владельцу)."""

    code: FriendCode
    rotated_at: datetime


class BlockView(BaseModel):
    """Заблокированный игрок."""

    user_id: UUID
    nickname: Nickname
    created_at: datetime


class BlockList(BaseModel):
    """Список блокировок владельца."""

    items: list[BlockView]


class PrivacySettings(_Request):
    """Настройки приватности (PUT заменяет целиком)."""

    profile_public: bool = Field(
        description="false - /players/{id} показывает ник, аватар, оформление"
    )
    stats_public: bool
    friend_requests: FriendRequestsPolicy = Field(description="Кто может звать в друзья")


class PlayerRelation(BaseModel):
    """Что зритель может видеть у игрока; одинаков для скрытого, заблокированного и неизвестного."""

    profile_visible: bool
    stats_visible: bool
    is_friend: bool
    can_send_request: bool
    blocked_by_me: bool


class RoomInviteCreate(_Request):
    """Приглашение друга в комнату из лобби."""

    room_code: RoomCode


class RoomInviteResult(BaseModel):
    """Приглашение принято к доставке."""

    invite_id: UUID
    repeated: bool = Field(description="true - то же приглашение уже отправлено (идемпотентность)")


# --- комментарии ---------------------------------------------------------------------------


class CommentBody(_Request):
    """Текст комментария: 1-500 символов, проверка `gv_text.ugc`."""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CommentView(BaseModel):
    """Комментарий к сборнику."""

    id: UUID
    collection_id: UUID
    author_id: UUID
    author_nickname: Nickname
    body: str
    created_at: datetime
    edited_at: datetime | None
    can_edit: bool = Field(description="Автор, в пределах окна правки")
    can_delete: bool = Field(description="Автор, автор сборника или модератор")


class CommentPage(BaseModel):
    """Страница комментариев (курсор), новые первыми."""

    items: list[CommentView]
    next_cursor: Cursor | None
    total: int = Field(ge=0)


# --- жалобы и санкции игрока ---------------------------------------------------------------


class ReportReason(BaseModel):
    """Причина жалобы из справочника конфига."""

    code: Code
    title: str
    target_kinds: list[TargetKind]


class ReportReasons(BaseModel):
    """Справочник причин."""

    items: list[ReportReason]


class ReportCreate(_Request):
    """Жалоба на игрока из матча, сборник, комментарий или аватарку."""

    target_kind: TargetKind
    target_id: UUID = Field(
        description="player - user_id; collection, comment - их id; avatar - id ассета"
    )
    context_match_id: UUID | None = Field(
        default=None, description="Обязателен для player: общий несоло-матч"
    )
    reason_code: Code
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


class ReportResult(BaseModel):
    """Жалоба принята. Состояние кейса заявителю не раскрывается."""

    id: UUID
    repeated: bool = Field(description="true - жалоба на этот объект уже подана")


class MySanction(BaseModel):
    """Действующая санкция игрока (экран санкции)."""

    kind: SanctionKind
    started_at: datetime
    expires_at: datetime | None = Field(description="null - бессрочно")
    reason: str


class MySanctions(BaseModel):
    """Действующие санкции владельца."""

    items: list[MySanction]


# --- админка -------------------------------------------------------------------------------


class SanctionGrant(_Request):
    """Выдача санкции."""

    kind: SanctionKind
    duration_s: int | None = Field(
        default=None, ge=60, le=315_360_000, description="null - бессрочно; пределы - конфиг"
    )
    reason: Reason


class SanctionView(BaseModel):
    """Запись истории санкций."""

    id: UUID
    user_id: UUID
    kind: SanctionKind
    started_at: datetime
    expires_at: datetime | None
    reason: str
    actor_id: UUID
    revoked_at: datetime | None
    revoked_by: UUID | None
    revoke_reason: str | None
    active: bool


class ReportItem(BaseModel):
    """Жалоба в карточке кейса (текст видит только модератор)."""

    id: UUID
    author_id: UUID
    reason_code: Code
    text: str | None
    context_match_id: UUID | None
    created_at: datetime


class CaseSummary(BaseModel):
    """Кейс очереди модерации."""

    id: UUID
    target_kind: TargetKind
    target_id: UUID
    target_user_id: UUID
    status: CaseStatus
    reports_count: int = Field(ge=1)
    assignee_id: UUID | None
    opened_at: datetime
    version: int = Field(ge=1, description="Оптимистичная блокировка: передаётся в действиях")


class CasePage(BaseModel):
    """Страница очереди (курсор), старые первыми."""

    items: list[CaseSummary]
    next_cursor: Cursor | None


class CaseDetail(CaseSummary):
    """Карточка кейса."""

    reports: list[ReportItem]
    target_preview: str | None = Field(description="Фрагмент содержимого (комментарий, название)")
    decision: Decision | None
    resolved_by: UUID | None
    resolved_at: datetime | None


class CaseClaim(_Request):
    """Взять кейс в работу."""

    version: int = Field(ge=1)


class CaseEscalate(_Request):
    """Эскалировать кейс."""

    version: int = Field(ge=1)
    reason: Reason


class CaseResolve(_Request):
    """Решить кейс."""

    version: int = Field(ge=1)
    decision: Decision
    sanction: SanctionGrant | None = Field(
        default=None, description="Обязательна при decision = sanctioned"
    )
    reason: Reason

    @model_validator(mode="after")
    def _sanction_matches_decision(self) -> Self:
        if (self.decision == "sanctioned") != (self.sanction is not None):
            raise ValueError("sanction обязательна при sanctioned и запрещена иначе")
        return self


class ConfigVersionCard(BaseModel):
    """Версия конфига social (ADR 0053)."""

    version: int = Field(ge=1)
    status: Literal["draft", "published", "archived"]
    created_at: datetime
    published_at: datetime | None = None
    created_by: UUID
    body_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="sha256 канонического JSON тела; публикация привязана к нему",
    )


class ConfigVersionList(BaseModel):
    """Версии конфига, новые первыми."""

    items: list[ConfigVersionCard]


class ConfigVersionBody(BaseModel):
    """Версия конфига с телом."""

    card: ConfigVersionCard
    config: dict[str, JsonValue] = Field(description="По `contracts/social/social_config.v1.json`")


class SaveConfigDraft(_Request):
    """Сохранить черновик конфига (заменяет текущий черновик)."""

    config: dict[str, JsonValue]
    reason: Reason


class PublishConfig(_Request):
    """Опубликовать черновик."""

    version: int = Field(ge=1, description="Версия черновика")
    body_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="`body_sha256` черновика, который видел автор (иначе 409 `draft_changed`)",
    )
    reason: Reason


class RollbackConfig(_Request):
    """Откат: копия старой версии становится новым черновиком."""

    reason: Reason


class SanctionRevoke(_Request):
    """Снять санкцию вручную."""

    reason: Reason


class BehaviourCounters(BaseModel):
    """Счётчики порядочности (только модератору, без автосанкций)."""

    forfeits: int = Field(ge=0)
    leaves: int = Field(ge=0)
    tech_losses: int = Field(ge=0)
    reports_received: int = Field(ge=0)
    praises: int = Field(ge=0)
    matches_total: int = Field(ge=0)


class UserModeration(BaseModel):
    """Карточка игрока для модератора."""

    user_id: UUID
    nickname: Nickname | None
    counters: BehaviourCounters
    sanctions: list[SanctionView]
    cases: list[CaseSummary]

"""Друзья, блокировки, приватность: чистые правила (ADR 0050).

Домен не знает про БД и HTTP. Решение «что делать с новой заявкой» принимает
``decide_request`` по уже собранным фактам; лимиты приходят из конфига (``FriendsConfig``).
Ошибки не раскрывают причину отказа там, где это позволило бы перебирать игроков:
неизвестный игрок, скрывший заявки и заблокировавший отвечают одним ``PlayerNotFoundError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from gv_core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from gv_core.random import Random
from social_service.domain.config import FriendsConfig

FRIEND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Алфавит кодов комнат match (без неоднозначных 0/O, 1/I)."""
FRIEND_CODE_LENGTH = 8

DAY_WINDOW_S = 86400
"""Окно суточного лимита заявок (совпадает с ``DAY`` в application)."""

POLICY_ALL = "all"
POLICY_NONE = "none"


class PlayerNotFoundError(NotFoundError):
    """Неизвестный игрок, скрывший заявки или заблокировавший - одинаковый ответ."""

    default_code = "social.player_not_found"

    def __init__(self) -> None:
        super().__init__("игрок не найден")


class AlreadyFriendsError(ConflictError):
    """Игроки уже друзья."""

    default_code = "social.already_friends"


class RequestPendingError(ConflictError):
    """Исходящая заявка этому игроку уже ждёт ответа."""

    default_code = "social.request_pending"


class RequestNotPendingError(ConflictError):
    """Заявка уже принята, отклонена или отозвана."""

    default_code = "social.request_not_pending"


class FriendsLimitError(ConflictError):
    """Достигнут предел числа друзей (конфиг ``friends.max_friends``)."""

    default_code = "social.friends_limit"


class DailyRequestsLimitError(RateLimitedError):
    """Исчерпан суточный лимит заявок (конфиг ``friends.requests_per_day``)."""

    default_code = "social.friend_requests_daily_limit"


class BlockedByYouError(ForbiddenError):
    """Отправитель сам заблокировал адресата."""

    default_code = "social.blocked_by_you"


class CannotBlockSelfError(InvalidInputError):
    """Себя заблокировать нельзя."""

    default_code = "social.cannot_block_self"


class InviteForbiddenError(ForbiddenError):
    """Приглашение запрещено: не друг, блокировка в любую сторону или санкция ONLINE."""

    default_code = "social.invite_forbidden"


class LookupRateLimitedError(RateLimitedError):
    """Слишком много попыток найти игрока по нику или коду."""

    default_code = "social.lookup_rate_limited"


class InvitesRateLimitedError(RateLimitedError):
    """Слишком много приглашений в минуту."""

    default_code = "social.invites_rate_limited"


class PresenceRateLimitedError(RateLimitedError):
    """Heartbeat присутствия шлют слишком часто."""

    default_code = "social.presence_rate_limited"


class LimiterUnavailableError(DependencyUnavailableError):
    """Лимитер (Redis) недоступен: операция закрыта (fail-closed)."""

    default_code = "social.limiter_unavailable"


class ProfileNotReadyError(DependencyUnavailableError):
    """Нет проекции ника отправителя (событие identity ещё не дошло): повторите позже."""

    default_code = "social.profile_not_ready"


def ordered_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """Пара дружбы: меньший идентификатор первым (``user_a < user_b``)."""
    return (a, b) if a.int < b.int else (b, a)


def generate_friend_code(rng: Random) -> str:
    """Код добавления: 8 символов алфавита кодов комнат."""
    return "".join(
        FRIEND_CODE_ALPHABET[rng.randrange(len(FRIEND_CODE_ALPHABET))]
        for _ in range(FRIEND_CODE_LENGTH)
    )


@dataclass(frozen=True, slots=True)
class Privacy:
    """Приватность игрока; по умолчанию профиль и статистика публичны, заявки - от всех."""

    profile_public: bool = True
    stats_public: bool = True
    friend_requests: str = POLICY_ALL


class RequestAction(StrEnum):
    """Что делать с исходящей заявкой."""

    CREATE = "create"
    MERGE = "merge"  # встречная заявка есть: сразу дружба


@dataclass(frozen=True, slots=True)
class RequestFacts:
    """Факты о паре «отправитель -> адресат» на момент заявки."""

    target_exists: bool
    sender_blocked_target: bool
    target_blocked_sender: bool
    target_policy: str
    already_friends: bool
    outgoing_pending: bool
    incoming_pending: bool
    sent_in_window: int
    sender_friends: int
    target_friends: int


def decide_request(
    sender: UUID, target: UUID, facts: RequestFacts, cfg: FriendsConfig
) -> RequestAction:
    """Решить судьбу новой заявки или отказать (порядок проверок исключает перебор).

    Raises:
        PlayerNotFoundError: неизвестный игрок, он заблокировал отправителя или скрыл заявки
            (и встречной заявки нет), либо отправитель шлёт заявку себе.
        BlockedByYouError: отправитель сам заблокировал адресата.
        AlreadyFriendsError: уже друзья.
        RequestPendingError: исходящая заявка уже ждёт ответа.
        FriendsLimitError: предел друзей (у отправителя; при слиянии - у обоих).
        DailyRequestsLimitError: суточный лимит заявок.
    """
    if sender == target or not facts.target_exists or facts.target_blocked_sender:
        raise PlayerNotFoundError
    if facts.sender_blocked_target:
        raise BlockedByYouError("вы заблокировали этого игрока")
    if facts.target_policy == POLICY_NONE and not facts.incoming_pending:
        raise PlayerNotFoundError
    if facts.already_friends:
        raise AlreadyFriendsError("вы уже друзья")
    if facts.outgoing_pending:
        raise RequestPendingError("заявка уже отправлена")
    if facts.incoming_pending:
        check_friend_capacity(facts.sender_friends, facts.target_friends, cfg)
        return RequestAction.MERGE
    if facts.sender_friends >= cfg.max_friends:
        raise FriendsLimitError("достигнут предел числа друзей")
    if facts.sent_in_window >= cfg.requests_per_day:
        raise DailyRequestsLimitError(
            "суточный лимит заявок исчерпан", details={"retry_after": DAY_WINDOW_S}
        )
    return RequestAction.CREATE


def check_friend_capacity(first: int, second: int, cfg: FriendsConfig) -> None:
    """Оба игрока ещё могут завести друга.

    Raises:
        FriendsLimitError: у кого-то достигнут ``max_friends``.
    """
    if first >= cfg.max_friends or second >= cfg.max_friends:
        raise FriendsLimitError("достигнут предел числа друзей")


@dataclass(frozen=True, slots=True)
class Relation:
    """Что зритель видит у игрока (ответ ``players/{id}/relation``)."""

    profile_visible: bool = False
    stats_visible: bool = False
    is_friend: bool = False
    can_send_request: bool = False
    blocked_by_me: bool = False


def relation_of(
    *,
    viewer: UUID,
    target: UUID,
    target_exists: bool,
    privacy: Privacy,
    is_friend: bool,
    viewer_blocked_target: bool,
    target_blocked_viewer: bool,
    outgoing_pending: bool,
) -> Relation:
    """Видимость игрока для зрителя.

    Неизвестный, заблокировавший зрителя и скрытый профиль дают одинаковый «пустой» ответ.
    ``blocked_by_me`` истинно только если зритель сам заблокировал игрока: это его же данные.
    Друзья видят профиль и статистику друг друга; себя зритель видит целиком.
    """
    if viewer == target:
        return Relation(profile_visible=True, stats_visible=True)
    if viewer_blocked_target:
        return Relation(blocked_by_me=True)
    if not target_exists or target_blocked_viewer:
        return Relation()
    profile = privacy.profile_public or is_friend
    if not profile:
        return Relation()
    return Relation(
        profile_visible=True,
        stats_visible=privacy.stats_public or is_friend,
        is_friend=is_friend,
        can_send_request=(
            privacy.friend_requests == POLICY_ALL and not is_friend and not outgoing_pending
        ),
    )

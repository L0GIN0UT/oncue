"""Агрегат комнаты: лобби, места, хост, готовность, настройки, память (R1 плана этапа 4).

Инварианты:
    * в комнате есть хост, пока в ней есть игроки; уход хоста передаёт хоста следующему по
      слоту (D12), кик хоста запрещён (хост не может кикнуть себя, а другие не хосты);
    * смена настроек — только хост, только в лобби, значения — из ``SettingsOptions``
      шаблона; готовность всех сбрасывается;
    * кикнутый возвращается только по новому приглашению (в 4a — никак: код тот же);
    * каждое видимое изменение увеличивает ``version`` и даёт ``Change`` для канала
      (D9: клиент ресинкается по пропуску версии).

Матч (фазы после лобби) ведёт ``match_fsm``; агрегат хранит его состояние в ``match``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from match_service.domain import errors
from match_service.domain.errors import (
    MatchConflictError,
    MatchForbiddenError,
    MatchInvalidError,
    MatchNotFoundError,
)
from match_service.domain.model import (
    MATCH_PHASES,
    SCHEMA_VERSION,
    Change,
    MatchState,
    ModeInfo,
    Phase,
    Presence,
    RoomSettings,
    RoomTemplate,
    Seat,
)

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Алфавит кода комнаты: без 0/O/1/I (D12)."""
ROOM_CODE_LENGTH = 8


@dataclass(frozen=True, slots=True)
class SettingsChange:
    """Переопределения хоста; ``None`` — поле не меняется."""

    rounds: int | None = None
    level_window_s: int | None = None
    answer_format: str | None = None
    clue_type: str | None = None
    players_max: int | None = None
    visibility: str | None = None
    collection_ids: tuple[UUID, ...] | None = None


NOT_SUPPORTED_YET = frozenset({"answer_format", "clue_type"})
"""Переопределения, которых пока нет в плане game (``RoundPlanRequest`` без загадки и
формата): хвост 4a — 422 ``match.setting_not_allowed`` с ``reason = not_supported_yet``."""


def _not_allowed(name: str) -> MatchInvalidError:
    return MatchInvalidError(
        "значение настройки не разрешено шаблоном",
        code=errors.SETTING_NOT_ALLOWED,
        details={"field": name},
    )


def apply_settings(
    template: RoomTemplate, base: RoomSettings, change: SettingsChange
) -> RoomSettings:
    """Применить переопределения хоста к настройкам (D12).

    Raises:
        MatchInvalidError: ``match.setting_not_allowed`` (``details.field``),
            ``match.too_many_collections``, ``match.collection_required``.
    """
    options = template.options
    values: dict[str, Any] = {}  # Any: значения разных типов полей RoomSettings
    for name in sorted(NOT_SUPPORTED_YET):
        if getattr(change, name) is not None:
            raise MatchInvalidError(
                "переопределение пока не поддерживается",
                code=errors.SETTING_NOT_ALLOWED,
                details={"field": name, "reason": "not_supported_yet"},
            )
    for name in ("rounds", "level_window_s", "players_max"):
        value = getattr(change, name)
        if value is None:
            continue
        allowed = getattr(options, name) if options is not None else None
        if not allowed or value not in allowed:
            raise _not_allowed(name)
        values[name] = value
    if change.visibility is not None:
        allowed_vis = options.visibility if options is not None else None
        if change.visibility != base.visibility and (
            not allowed_vis or change.visibility not in allowed_vis
        ):
            raise _not_allowed("visibility")
        values["visibility"] = change.visibility
    if change.collection_ids is not None:
        if not template.uses_collection:
            raise _not_allowed("collection_ids")
        ids = tuple(dict.fromkeys(change.collection_ids))
        if len(ids) > template.max_collections:
            raise MatchInvalidError(
                "слишком много сборников",
                code=errors.TOO_MANY_COLLECTIONS,
                details={"max": template.max_collections},
            )
        values["collection_ids"] = ids
    result = replace(base, **values)
    if template.uses_collection and not result.collection_ids:
        raise MatchInvalidError("нужен хотя бы один сборник", code=errors.COLLECTION_REQUIRED)
    return result


def default_settings(template: RoomTemplate) -> RoomSettings:
    """Настройки шаблона без переопределений."""
    return RoomSettings(
        rounds=template.rounds,
        level_window_s=None,
        answer_format=template.answer_format,
        clue_type=template.clue_type,
        players_max=template.players_max,
        visibility=template.visibility,
    )


@dataclass(slots=True)
class Room:
    """Комната (агрегат; единственный писатель — владелец lease, ADR 0034).

    Attributes:
        cursor: Последняя применённая команда потока ``{room:CODE}:cmd`` (подтверждение
            команд вместе со снимком).
        ops: Окно ответов на команды по ``user_id:op_id`` (идемпотентность, §12 п. 3).
        memory: Сыгранные единицы лобби (``exclude_ids`` плана, D12).
        phase_version: Версия, с которой действует текущая фаза и уровень: команда фазы
            с ``expected_version`` меньше неё — из прошлой фазы (409).
        start_error: Последний отказ старта ``(код, мс)``: команда могла получить 202, а
            план game не пришёл — игроки видят причину в ``RoomView.start_error``.
        changes: Изменения с последнего снимка (в снимок не входят).
    """

    code: str
    mode: ModeInfo
    template: RoomTemplate
    settings: RoomSettings
    host_id: UUID
    seats: list[Seat]
    created_at_ms: int
    activity_at_ms: int
    phase: Phase = Phase.LOBBY
    phase_ends_at_ms: int | None = None
    version: int = 1
    phase_version: int = 1
    kicked: set[UUID] = field(default_factory=set)
    memory: list[UUID] = field(default_factory=list)
    match: MatchState | None = None
    last_match_id: UUID | None = None
    rematch_votes: set[UUID] = field(default_factory=set)
    rematch_code: str | None = None
    close_reason: str | None = None
    cursor: str = "0-0"
    ops: dict[str, dict[str, Any]] = field(default_factory=dict)  # Any: JSON ответа
    schema_version: int = SCHEMA_VERSION
    start_error: tuple[str, int] | None = None
    changes: list[Change] = field(default_factory=list)

    # ------------------------------------------------------------------ создание
    @classmethod
    def create(
        cls,
        *,
        code: str,
        mode: ModeInfo,
        template: RoomTemplate,
        settings: RoomSettings,
        host_id: UUID,
        nickname: str,
        now_ms: int,
    ) -> Room:
        """Новая комната в лобби; создатель — хост в слоте 0."""
        if len(code) != ROOM_CODE_LENGTH or any(c not in ROOM_CODE_ALPHABET for c in code):
            raise ValueError("код комнаты вне алфавита D12")
        return cls(
            code=code,
            mode=mode,
            template=template,
            settings=settings,
            host_id=host_id,
            seats=[Seat(user_id=host_id, slot=0, nickname=nickname, joined_at_ms=now_ms)],
            created_at_ms=now_ms,
            activity_at_ms=now_ms,
        )

    # ------------------------------------------------------------------ версии
    def emit(self, kind: str, **data: Any) -> int:
        """Зафиксировать видимое изменение: ``version + 1`` и ``Change`` для канала."""
        self.version += 1
        self.changes.append(Change(kind=kind, version=self.version, data=dict(data)))
        return self.version

    def set_phase(self, phase: Phase, ends_at_ms: int | None, **data: Any) -> None:
        """Сменить фазу (и сообщить ``phase``); команды прошлой фазы станут 409."""
        self.phase = phase
        self.phase_ends_at_ms = ends_at_ms
        self.phase_version = self.emit(
            "phase", phase=phase.value, phase_ends_at_ms=ends_at_ms, **data
        )

    def pull_changes(self) -> list[Change]:
        """Забрать изменения (после снимка — в канал)."""
        changes, self.changes = self.changes, []
        return changes

    def touch(self, now_ms: int) -> None:
        """Отметить активность (закрытие простаивающей комнаты)."""
        self.activity_at_ms = now_ms

    # ------------------------------------------------------------------ места
    @property
    def is_closed(self) -> bool:
        """Комната закрыта."""
        return self.phase is Phase.CLOSED

    @property
    def in_match(self) -> bool:
        """Идёт матч."""
        return self.phase in MATCH_PHASES

    def seat_of(self, user_id: UUID) -> Seat | None:
        """Место игрока или ``None``."""
        return next((s for s in self.seats if s.user_id == user_id), None)

    def require_seat(self, user_id: UUID) -> Seat:
        """Место участника.

        Raises:
            MatchForbiddenError: ``match.not_participant``.
        """
        seat = self.seat_of(user_id)
        if seat is None or seat.presence is Presence.LEFT:
            raise MatchForbiddenError("вы не участник комнаты", code=errors.NOT_PARTICIPANT)
        return seat

    def require_host(self, user_id: UUID) -> Seat:
        """Место хоста.

        Raises:
            MatchForbiddenError: ``match.not_participant`` | ``match.not_host``.
        """
        seat = self.require_seat(user_id)
        if self.host_id != user_id:
            raise MatchForbiddenError("действие доступно только хосту", code=errors.NOT_HOST)
        return seat

    def require_phase(self, *phases: Phase) -> None:
        """Команда допустима только в этих фазах.

        Raises:
            MatchNotFoundError: комната закрыта.
            MatchConflictError: ``match.phase_mismatch``.
        """
        if self.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        if self.phase not in phases:
            raise MatchConflictError(
                "команда не для текущей фазы",
                code=errors.PHASE_MISMATCH,
                details={"phase": self.phase.value},
            )

    def _free_slot(self) -> int:
        taken = {s.slot for s in self.seats}
        return next(i for i in range(self.settings.players_max) if i not in taken)

    def join(self, user_id: UUID, nickname: str, now_ms: int) -> bool:
        """Войти по коду; повторный вход участника — реконнект в тот же слот.

        Returns:
            ``True`` — новый игрок, ``False`` — реконнект.

        Raises:
            MatchNotFoundError: комната закрыта.
            MatchForbiddenError: ``match.kicked``.
            MatchConflictError: ``match.match_in_progress``, ``match.room_full``.
        """
        if self.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        seat = self.seat_of(user_id)
        if seat is not None and seat.presence is not Presence.LEFT:
            self.reconnect(user_id, now_ms)
            return False
        if user_id in self.kicked:
            raise MatchForbiddenError("вас исключили из комнаты", code=errors.KICKED)
        if self.phase is not Phase.LOBBY:
            raise MatchConflictError("матч уже идёт", code=errors.MATCH_IN_PROGRESS)
        if seat is not None:  # вышел и вернулся в лобби — место освобождено уходом
            self.seats.remove(seat)
        if len(self.seats) >= self.settings.players_max:
            raise MatchConflictError("мест нет", code=errors.ROOM_FULL)
        self.seats.append(
            Seat(user_id=user_id, slot=self._free_slot(), nickname=nickname, joined_at_ms=now_ms)
        )
        self.seats.sort(key=lambda s: s.slot)
        self.touch(now_ms)
        self.emit("lobby")
        return True

    def reconnect(self, user_id: UUID, now_ms: int) -> bool:
        """Игрок снова на связи (вход по ссылке или heartbeat). ``True`` — было изменение."""
        seat = self.seat_of(user_id)
        if seat is None or seat.presence is not Presence.DISCONNECTED:
            return False
        seat.presence = Presence.ONLINE
        self.touch(now_ms)
        self.emit("player", user_id=str(user_id))
        return True

    def disconnect(self, user_id: UUID) -> bool:
        """Игрок пропал (нет heartbeat). ``True`` — было изменение."""
        seat = self.seat_of(user_id)
        if seat is None or seat.presence is not Presence.ONLINE:
            return False
        seat.presence = Presence.DISCONNECTED
        self.emit("player", user_id=str(user_id))
        return True

    def leave_lobby(self, user_id: UUID, now_ms: int) -> None:
        """Уйти из лобби (или из итога): место освобождается, хост переходит дальше."""
        seat = self.require_seat(user_id)
        self.seats.remove(seat)
        self.rematch_votes.discard(user_id)
        self.touch(now_ms)
        self._after_departure(user_id, now_ms)

    def _after_departure(self, user_id: UUID, now_ms: int) -> None:
        remaining = [s for s in self.seats if s.presence is not Presence.LEFT]
        if not remaining:
            self.close("empty", now_ms)
            return
        if self.host_id == user_id:
            self.host_id = remaining[0].user_id
        self.emit("lobby")

    def mark_left(self, user_id: UUID, now_ms: int) -> None:
        """Игрок выбыл из идущего матча: место остаётся (очки видны), хост — дальше."""
        seat = self.require_seat(user_id)
        seat.presence = Presence.LEFT
        seat.ready = False
        self.touch(now_ms)
        self._after_departure(user_id, now_ms)

    def set_ready(self, user_id: UUID, *, ready: bool, now_ms: int) -> None:
        """Готовность в лобби."""
        self.require_phase(Phase.LOBBY)
        seat = self.require_seat(user_id)
        self.touch(now_ms)
        if seat.ready != ready:
            seat.ready = ready
            self.emit("player", user_id=str(user_id))

    def set_away(self, user_id: UUID, *, away: bool, now_ms: int) -> None:
        """Отметка «отошёл»."""
        if self.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        seat = self.require_seat(user_id)
        self.touch(now_ms)
        if seat.away != away:
            seat.away = away
            self.emit("player", user_id=str(user_id))

    def rename(self, user_id: UUID, nickname: str) -> None:
        """Ник игрока изменился в identity (``UserProfileBasicsChanged``)."""
        seat = self.seat_of(user_id)
        if seat is not None and seat.nickname != nickname:
            seat.nickname = nickname
            self.emit("lobby")

    # ------------------------------------------------------------------ хост
    def change_settings(self, user_id: UUID, change: SettingsChange, now_ms: int) -> None:
        """Сменить настройки (хост, лобби); готовность всех сбрасывается.

        Raises:
            MatchConflictError: ``match.players_over_limit`` — мест меньше, чем игроков.
        """
        self.require_phase(Phase.LOBBY)
        self.require_host(user_id)
        settings = apply_settings(self.template, self.settings, change)
        if settings.players_max < len(self.seats):
            raise MatchConflictError(
                "игроков больше нового числа мест", code=errors.PLAYERS_OVER_LIMIT
            )
        self.settings = settings
        self.start_error = None
        for seat in self.seats:
            seat.ready = False
        self.touch(now_ms)
        self.emit("lobby")

    def kick(self, user_id: UUID, target: UUID, now_ms: int) -> None:
        """Исключить игрока (хост, лобби); вернуться по коду он не сможет."""
        self.require_phase(Phase.LOBBY)
        self.require_host(user_id)
        if target == user_id:
            raise MatchInvalidError("нельзя исключить себя", code=errors.CANNOT_KICK_SELF)
        seat = self.seat_of(target)
        if seat is None:
            raise MatchNotFoundError("игрока нет в комнате", code=errors.PLAYER_NOT_FOUND)
        self.seats.remove(seat)
        self.kicked.add(target)
        self.touch(now_ms)
        self.emit("lobby")

    def transfer_host(self, user_id: UUID, target: UUID, now_ms: int) -> None:
        """Передать хоста другому участнику."""
        if self.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        self.require_host(user_id)
        seat = self.seat_of(target)
        if seat is None or seat.presence is Presence.LEFT:
            raise MatchNotFoundError("игрока нет в комнате", code=errors.PLAYER_NOT_FOUND)
        self.touch(now_ms)
        if target != user_id:
            self.host_id = target
            self.emit("lobby")

    def reset_memory(self, user_id: UUID, now_ms: int) -> None:
        """Забыть сыгранные единицы лобби (хост)."""
        if self.is_closed:
            raise MatchNotFoundError("комната закрыта", code=errors.ROOM_NOT_FOUND)
        self.require_host(user_id)
        self.touch(now_ms)
        if self.memory:
            self.memory.clear()
            self.emit("lobby")

    def remember(self, items: Iterable[UUID], limit: int) -> None:
        """Добавить единицы матча в память лобби (не больше ``limit`` последних)."""
        merged = list(dict.fromkeys([*self.memory, *items]))
        self.memory = merged[-limit:] if limit > 0 else []

    def fail_start(self, code: str, now_ms: int) -> None:
        """Старт не удался (план game): причина видна всем в лобби."""
        self.start_error = (code, now_ms)
        self.touch(now_ms)
        self.emit("lobby")

    def require_startable(self, user_id: UUID) -> None:
        """Проверки старта: хост, лобби, игроков ≥ минимума, остальные готовы.

        Raises:
            MatchConflictError: ``match.not_enough_players`` | ``match.not_all_ready``.
        """
        self.require_phase(Phase.LOBBY)
        self.require_host(user_id)
        if len(self.seats) < self.template.players_min:
            raise MatchConflictError(
                "недостаточно игроков",
                code=errors.NOT_ENOUGH_PLAYERS,
                details={"min": self.template.players_min},
            )
        if any(not s.ready for s in self.seats if s.user_id != self.host_id):
            raise MatchConflictError("не все готовы", code=errors.NOT_ALL_READY)

    # ------------------------------------------------------------------ закрытие
    def close(self, reason: str, now_ms: int) -> None:
        """Закрыть комнату (итог, простой, все ушли, админ)."""
        if self.is_closed:
            return
        self.close_reason = reason
        self.touch(now_ms)
        self.set_phase(Phase.CLOSED, None, round_index=None, level=None)
        if self.rematch_code is not None and self.rematch_code != self.code:
            self.emit("closed", reason=reason, rematch_code=self.rematch_code)
        else:
            self.emit("closed", reason=reason)

    # ------------------------------------------------------------------ команды
    def op_reply(self, key: str) -> dict[str, Any] | None:  # Any: JSON ответа
        """Сохранённый ответ команды (повтор ``op_id``)."""
        return self.ops.get(key)

    def remember_op(self, key: str, reply: dict[str, Any], window: int) -> None:  # Any: JSON
        """Запомнить ответ команды; окно — последние ``window`` команд."""
        self.ops[key] = reply
        while len(self.ops) > window:
            self.ops.pop(next(iter(self.ops)))

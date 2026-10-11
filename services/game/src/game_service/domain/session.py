"""Соло-сессия (D12): источник истины в Postgres, ход — одна строка с ``version``.

Правила раунда (переходы уровней, сверка ответа, очки) считает ядро движка; агрегат
хранит результат и следит за инвариантами сессии: ход только в текущем активном раунде,
уровень ассета не выше открытого, завершённая сессия не меняется. Каждый ход увеличивает
``version`` — клиент передаёт её как ``expected_version`` (``game.desync``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from game_service.domain.errors import (
    RoundNotActiveError,
    SessionNotActiveError,
    SessionNotFoundError,
)
from game_service.domain.events import DailyCompleted, GameFinished, RoundSummary
from gv_core.domain import AggregateRoot
from gv_game_kernel import AssetRef, LevelLockedError, RoundFinishedError, RoundResult, RoundState
from gv_game_kernel import round as kernel_round


class PlayerKind(StrEnum):
    """Вид игрока (D5)."""

    USER = "user"
    GUEST = "guest"


@dataclass(frozen=True, slots=True)
class PlayerRef:
    """Игрок сессии.

    Attributes:
        kind: Пользователь или гость.
        id: ``user_id`` или ``guest_id``; ``None`` — пользователь удалён (``UserDeleted``).
    """

    kind: PlayerKind
    id: UUID | None

    @property
    def is_guest(self) -> bool:
        """Гость."""
        return self.kind is PlayerKind.GUEST


class SessionStatus(StrEnum):
    """Статус сессии."""

    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


@dataclass
class Round:
    """Раунд сессии.

    Attributes:
        index: Номер с 0.
        media_item_id: Загаданная единица (клиенту — только после ревила).
        asset: Набор ассетов (``None`` — ассетов нет, раунд аннулирован при старте).
        levels_count: Число уровней загадки.
        max_points: Максимум очков за раунд (Scorer).
        state: Состояние раунда (ядро).
        points: Очки за завершённый раунд.
        started_at: Первая выдача ассета (отсчёт ``elapsed_ms``).
        finished_at: Момент итога.
    """

    index: int
    media_item_id: UUID
    asset: AssetRef | None
    levels_count: int
    max_points: int
    state: RoundState = field(default_factory=RoundState)
    points: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def finished(self) -> bool:
        """Итог есть (включая ``void``)."""
        return self.state.finished

    @property
    def is_void(self) -> bool:
        """Раунд аннулирован: в ``max_score`` не входит."""
        return self.state.outcome is RoundResult.VOID

    @property
    def guessed_level(self) -> int | None:
        """Уровень точного ответа, иначе частичного, иначе ``None``."""
        if self.state.outcome is RoundResult.EXACT:
            return self.state.level
        if self.state.outcome is RoundResult.VOID:
            return None
        return self.state.partial_awarded_level

    @property
    def elapsed_ms(self) -> int | None:
        """От первой выдачи ассета до итога."""
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds() * 1000))


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """Единица раунда при старте сессии."""

    media_item_id: UUID
    asset: AssetRef | None


class GameSession(AggregateRoot[UUID]):
    """Соло-сессия режима ``mode_key@mode_version``."""

    def __init__(
        self,
        id: UUID,  # noqa: A002 — поле домена
        *,
        player: PlayerRef,
        mode_key: str,
        mode_version: int,
        domain: str,
        source_type: str,
        clue_type: str,
        day: date | None,
        preset_id: str | None,
        seed: bytes,
        status: SessionStatus,
        rounds: list[Round],
        started_at: datetime,
        last_move_at: datetime,
        idle_timeout_s: int,
        finished_at: datetime | None = None,
        version: int = 1,
    ) -> None:
        super().__init__(id, version)
        self.player = player
        self.mode_key = mode_key
        self.mode_version = mode_version
        self.domain = domain
        self.source_type = source_type
        self.clue_type = clue_type
        self.day = day
        self.preset_id = preset_id
        self.seed = seed
        self.status = status
        self.rounds = rounds
        self.started_at = started_at
        self.last_move_at = last_move_at
        self.idle_timeout_s = idle_timeout_s
        self.finished_at = finished_at

    # ------------------------------------------------------------------ создание
    @classmethod
    def start(
        cls,
        id: UUID,  # noqa: A002
        *,
        player: PlayerRef,
        mode_key: str,
        mode_version: int,
        domain: str,
        source_type: str,
        clue_type: str,
        day: date | None,
        preset_id: str | None,
        seed: bytes,
        plan: Sequence[RoundPlan],
        levels_count: int,
        max_points: int,
        idle_timeout_s: int,
        now: datetime,
    ) -> GameSession:
        """Новая сессия. Раунды без ассетов сразу аннулируются (``void``)."""
        if not plan:
            raise ValueError("в сессии должен быть хотя бы один раунд")
        rounds = []
        for i, item in enumerate(plan):
            r = Round(i, item.media_item_id, item.asset, levels_count, max_points)
            if item.asset is None:
                r.state = kernel_round.void(r.state)
                r.finished_at = now
            rounds.append(r)
        session = cls(
            id,
            player=player,
            mode_key=mode_key,
            mode_version=mode_version,
            domain=domain,
            source_type=source_type,
            clue_type=clue_type,
            day=day,
            preset_id=preset_id,
            seed=seed,
            status=SessionStatus.ACTIVE,
            rounds=rounds,
            started_at=now,
            last_move_at=now,
            idle_timeout_s=idle_timeout_s,
        )
        if all(r.finished for r in rounds):
            session._finish(now)
        return session

    # ------------------------------------------------------------------ чтение
    @property
    def score(self) -> int:
        """Очки сессии."""
        return sum(r.points for r in self.rounds if not r.is_void)

    @property
    def max_score(self) -> int:
        """Максимум очков: раунды ``void`` не входят."""
        return sum(r.max_points for r in self.rounds if not r.is_void)

    @property
    def current_index(self) -> int:
        """Номер текущего раунда; ``len(rounds)`` — играть нечего."""
        for r in self.rounds:
            if not r.finished:
                return r.index
        return len(self.rounds)

    @property
    def is_active(self) -> bool:
        """Сессия идёт."""
        return self.status is SessionStatus.ACTIVE

    def require_owner(self, player: PlayerRef) -> None:
        """Чужая сессия неотличима от несуществующей (404, §9 п. 2).

        Raises:
            SessionNotFoundError: сессия другого субъекта.
        """
        if self.player.id is None or self.player != player:
            raise SessionNotFoundError("сессия не найдена")

    def round_at(self, index: int) -> Round:
        """Раунд по номеру.

        Raises:
            RoundNotActiveError: номера нет.
        """
        if not 0 <= index < len(self.rounds):
            raise RoundNotActiveError("нет такого раунда")
        return self.rounds[index]

    def active_round(self, index: int) -> Round:
        """Текущий активный раунд ``index`` — только в нём можно ходить и брать ассеты.

        Raises:
            SessionNotActiveError: сессия завершена или брошена.
            RoundFinishedError: раунд уже завершён.
            RoundNotActiveError: раунд ещё не начат.
        """
        if not self.is_active:
            raise SessionNotActiveError("сессия завершена")
        r = self.round_at(index)
        if r.finished:
            raise RoundFinishedError("раунд уже завершён")
        if index != self.current_index:
            raise RoundNotActiveError("раунд ещё не начат")
        return r

    def clue_round(self, index: int, level: int) -> Round:
        """Раунд, для которого можно выдать ассет уровня ``level`` (D11).

        Raises:
            LevelLockedError: уровень выше открытого.
            SessionNotActiveError, RoundFinishedError, RoundNotActiveError: см. ``active_round``.
        """
        r = self.active_round(index)
        if level < 1 or level > r.state.level:
            raise LevelLockedError("уровень ещё не открыт", details={"level": r.state.level})
        return r

    # ------------------------------------------------------------------ изменения
    def mark_clue_issued(self, index: int, now: datetime) -> bool:
        """Отметить первую выдачу ассета раунда (старт отсчёта времени).

        Версию не меняет: снимок сессии для клиента не изменился.

        Returns:
            ``True`` — отметка поставлена сейчас.
        """
        r = self.round_at(index)
        if r.started_at is not None or r.finished:
            return False
        r.started_at = now
        return True

    def apply(self, index: int, state: RoundState, *, points: int, now: datetime) -> Round:
        """Применить новое состояние раунда после хода (ядро уже посчитало переход).

        Raises:
            SessionNotActiveError, RoundFinishedError, RoundNotActiveError: см. ``active_round``.
        """
        r = self.active_round(index)
        r.state = state
        if state.finished:
            r.points = 0 if r.is_void else points
            r.finished_at = now
        self.version += 1
        self.last_move_at = now
        if all(x.finished for x in self.rounds):
            self._finish(now)
        return r

    def void_round(self, index: int, now: datetime) -> Round | None:
        """Аннулировать раунд: ассет выключен (410 от media-pipeline).

        Returns:
            Раунд или ``None``, если он уже завершён (гонка — ничего не делаем).
        """
        if not self.is_active:
            return None
        r = self.round_at(index)
        if r.finished:
            return None
        # Тот же переход, что ``ModeEngine.void`` (статический фасад над ``round.void``).
        return self.apply(index, kernel_round.void(r.state), points=0, now=now)

    def abandon(self, now: datetime) -> bool:
        """Бросить сессию по таймауту без ходов.

        Returns:
            ``True`` — статус изменён.
        """
        if not self.is_active:
            return False
        self.status = SessionStatus.ABANDONED
        self.finished_at = now
        self.version += 1
        return True

    def _finish(self, now: datetime) -> None:
        self.status = SessionStatus.FINISHED
        self.finished_at = now
        summaries = tuple(
            RoundSummary(
                index=r.index,
                media_item_id=r.media_item_id,
                outcome=str(r.state.outcome),
                level=r.guessed_level,
                points=r.points,
                elapsed_ms=r.elapsed_ms,
            )
            for r in self.rounds
        )
        is_guest = self.player.is_guest
        user_id = None if is_guest else self.player.id
        day = self.day.isoformat() if self.day else None
        self.record(
            GameFinished(
                aggregate_id=str(self.id),
                session_id=self.id,
                version=self.version,
                user_id=user_id,
                is_guest=is_guest,
                mode_key=self.mode_key,
                mode_version=self.mode_version,
                domain=self.domain,
                source_type=self.source_type,
                clue_type=self.clue_type,
                day=day,
                score=self.score,
                max_score=self.max_score,
                started_at=self.started_at,
                finished_at=now,
                rounds=summaries,
            )
        )
        if day is not None and user_id is not None:
            self.record(
                DailyCompleted(
                    aggregate_id=str(self.id),
                    session_id=self.id,
                    version=self.version,
                    user_id=user_id,
                    mode_key=self.mode_key,
                    mode_version=self.mode_version,
                    domain=self.domain,
                    day=day,
                    score=self.score,
                    max_score=self.max_score,
                    exact_count=sum(1 for r in self.rounds if r.state.outcome is RoundResult.EXACT),
                    rounds_count=len(self.rounds),
                )
            )

    @property
    def counts_in_daily_stats(self) -> bool:
        """Результат идёт в статистику дня: пользователь и дейлик (гости — нет, D5)."""
        return self.day is not None and not self.player.is_guest and self.player.id is not None

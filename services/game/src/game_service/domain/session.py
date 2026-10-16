"""Соло-сессия (D12): источник истины в Postgres, ход — одна строка с ``version``.

Правила раунда (переходы уровней, сверка ответа, очки, правило завершения) считает ядро
движка; агрегат хранит результат и следит за инвариантами сессии: ход только в текущем
активном раунде, уровень ассета не выше открытого, завершённая сессия не меняется.
Каждый ход увеличивает ``version`` — клиент передаёт её как ``expected_version``
(``game.desync``).

Этап 3a (ADR 0023, D4): раунды материализуются лениво. Разыгранный состав
(``PlannedRound``: цель, показанные единицы, варианты) лежит в скрытом плане
``plan``; раунд i+1 создаётся, когда завершён раунд i. Сетка дописывает план туров
(``extend_plan``), правило ``session.end`` отбрасывает остаток плана (``end_by_rule``).
Какие варианты за какими единицами стоят (``option_id → media_item_id``), знает только
сервер. Версия схемы состояния — ``STATE_SCHEMA`` (снимки этапа 2 без поля — v1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from game_service.domain.errors import (
    RoundNotActiveError,
    SessionNotActiveError,
    SessionNotFoundError,
)
from game_service.domain.events import (
    BracketCompleted,
    BracketPickSummary,
    DailyCompleted,
    GameFinished,
    RoundSummary,
)
from gv_core.domain import AggregateRoot
from gv_game_kernel import AssetRef, LevelLockedError, RoundFinishedError, RoundResult, RoundState
from gv_game_kernel import round as kernel_round

STATE_SCHEMA = 2
"""Версия схемы ``game_sessions.state``: 1 — этап 2 (без плана и состава раунда), 2 — 3a."""


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

    @property
    def user_id(self) -> UUID | None:
        """``user_id`` пользователя или ``None`` (гость, удалённый пользователь)."""
        return None if self.is_guest else self.id


class SessionStatus(StrEnum):
    """Статус сессии."""

    ACTIVE = "active"
    FINISHED = "finished"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class RoundOption:
    """Вариант ответа раунда (формат ``pick``): клиент видит только ``id`` и подпись.

    Attributes:
        id: Непрозрачный ``option_id``.
        label_key: Ключ i18n подписи или ``None``.
        media_item_id: Единица за вариантом (сетка) или ``None`` («больше/меньше»).
    """

    id: str
    label_key: str | None = None
    media_item_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ShownItem:
    """Единица, показанная открыто (опорная единица пары, единицы пары сетки).

    Attributes:
        media_item_id: Единица (клиенту — только название, год, метрика, подача).
        asset: Набор ассетов подачи (кадр или отрывок) или ``None``.
    """

    media_item_id: UUID
    asset: AssetRef | None = None


@dataclass(frozen=True, slots=True)
class PlannedRound:
    """Разыгранный, но ещё не выданный раунд (скрыт от клиента, D4).

    Attributes:
        target: Загаданная единица.
        shown: Открыто показанные единицы.
        options: Варианты ответа (пусто — классический ввод).
    """

    target: UUID
    shown: tuple[UUID, ...] = ()
    options: tuple[RoundOption, ...] = ()

    def item_ids(self) -> list[UUID]:
        """Все единицы раунда (цель, показанные, варианты) без повторов."""
        ids = [
            self.target,
            *self.shown,
            *(o.media_item_id for o in self.options if o.media_item_id),
        ]
        return list(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class RoundAssets:
    """Наборы ассетов раунда на момент материализации.

    Attributes:
        asset: Набор загадки (``None`` — ассетов нет, раунд аннулируется).
        reveal: Набор ревила другого типа (``clue.reveal``, ADR 0026) или ``None``.
        shown: Наборы подачи показанных единиц.
    """

    asset: AssetRef | None
    reveal: AssetRef | None = None
    shown: Mapping[UUID, AssetRef] = field(default_factory=dict)


@dataclass
class Round:
    """Раунд сессии.

    Attributes:
        index: Номер с 0.
        media_item_id: Загаданная единица (клиенту — только после ревила).
        asset: Набор ассетов (``None`` — ассетов нет, раунд аннулирован при выдаче).
        levels_count: Число уровней загадки.
        max_points: Максимум очков за раунд (Scorer).
        state: Состояние раунда (ядро).
        points: Очки за завершённый раунд.
        started_at: Первая выдача ассета (отсчёт ``elapsed_ms``).
        finished_at: Момент итога.
        shown: Открыто показанные единицы.
        options: Варианты ответа (``option_id → media_item_id`` — только на сервере).
        choice: Выбранный вариант (формат ``pick``).
        stage: Этап раунда для UI (тур сетки) или ``None``.
        reveal_asset: Набор ревила другого типа или ``None``.
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
    shown: tuple[ShownItem, ...] = ()
    options: tuple[RoundOption, ...] = ()
    choice: str | None = None
    stage: int | None = None
    reveal_asset: AssetRef | None = None

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

    def plan(self) -> PlannedRound:
        """Состав раунда (для ``followups`` ядра и правил ответа)."""
        return PlannedRound(
            target=self.media_item_id,
            shown=tuple(s.media_item_id for s in self.shown),
            options=self.options,
        )

    def option(self, option_id: str | None) -> RoundOption | None:
        """Вариант по ``option_id`` (``None`` — нет такого или вариантов нет)."""
        return next((o for o in self.options if o.id == option_id), None)

    def winner_loser(self) -> tuple[UUID, UUID] | None:
        """Победитель и проигравший раунда предпочтения (оба варианта — единицы).

        Без выбора (``void``) побеждает первый вариант — как у ``bracket`` ядра.
        """
        items = [o for o in self.options if o.media_item_id is not None]
        if len(items) < 2:  # noqa: PLR2004 — пара вариантов
            return None
        chosen = self.option(self.choice) or items[0]
        other = next(o for o in items if o.id != chosen.id)
        assert chosen.media_item_id is not None and other.media_item_id is not None  # noqa: S101
        return chosen.media_item_id, other.media_item_id


class GameSession(AggregateRoot[UUID]):
    """Соло-сессия режима ``mode_key@mode_version``.

    Attributes:
        plan: Невыданные раунды (скрыты от клиента).
        progressive: Раунды зависят от прошлых (сетка): итог — ``BracketCompleted``,
            а не ``GameFinished``; в статистику дейлика не идёт.
        round_max: Максимум очков за раунд (оценка ``max_score`` по плану).
    """

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
        plan: list[PlannedRound] | None = None,
        progressive: bool = False,
        round_max: int | None = None,
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
        self.plan: list[PlannedRound] = list(plan or [])
        self.progressive = progressive
        self.round_max = (
            round_max if round_max is not None else (rounds[0].max_points if rounds else 0)
        )

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
        plan: Sequence[PlannedRound],
        round_max: int,
        idle_timeout_s: int,
        now: datetime,
        progressive: bool = False,
    ) -> GameSession:
        """Новая сессия с разыгранным планом; первый раунд выдаёт ``materialize_next``.

        Raises:
            ValueError: план пуст.
        """
        if not plan:
            raise ValueError("в сессии должен быть хотя бы один раунд")
        return cls(
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
            rounds=[],
            started_at=now,
            last_move_at=now,
            idle_timeout_s=idle_timeout_s,
            plan=list(plan),
            progressive=progressive,
            round_max=round_max,
        )

    # ------------------------------------------------------------------ чтение
    @property
    def score(self) -> int:
        """Очки сессии."""
        return sum(r.points for r in self.rounds if not r.is_void)

    @property
    def max_score(self) -> int:
        """Максимум очков: выданные не-``void`` раунды плюс невыданный план (у идущей
        сессии — верхняя оценка; после досрочного завершения план отброшен)."""
        played = sum(r.max_points for r in self.rounds if not r.is_void)
        return played + (self.round_max * len(self.plan) if self.is_active else 0)

    @property
    def current_index(self) -> int:
        """Номер текущего раунда; ``len(rounds)`` — выданные раунды сыграны."""
        for r in self.rounds:
            if not r.finished:
                return r.index
        return len(self.rounds)

    @property
    def is_active(self) -> bool:
        """Сессия идёт."""
        return self.status is SessionStatus.ACTIVE

    @property
    def needs_next(self) -> bool:
        """Сессия идёт, а все выданные раунды завершены: нужен следующий раунд или итог."""
        return self.is_active and all(r.finished for r in self.rounds)

    @property
    def next_planned(self) -> PlannedRound | None:
        """Следующий раунд плана или ``None``."""
        return self.plan[0] if self.plan else None

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
            RoundNotActiveError: номера нет (в том числе раунд ещё не выдан).
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

    # ------------------------------------------------------------------ план
    def materialize_next(
        self,
        assets: RoundAssets,
        *,
        levels_count: int,
        max_points: int,
        stage: int | None,
        now: datetime,
    ) -> Round:
        """Выдать следующий раунд плана. Раунд без ассетов сразу аннулируется (``void``).

        Raises:
            SessionNotActiveError: сессия завершена.
            ValueError: план пуст или текущий раунд не завершён.
        """
        if not self.is_active:
            raise SessionNotActiveError("сессия завершена")
        if not self.needs_next or not self.plan:
            raise ValueError("следующий раунд выдаётся после завершения текущего")
        planned = self.plan.pop(0)
        r = Round(
            index=len(self.rounds),
            media_item_id=planned.target,
            asset=assets.asset,
            levels_count=levels_count,
            max_points=max_points,
            shown=tuple(ShownItem(i, assets.shown.get(i)) for i in planned.shown),
            options=planned.options,
            stage=stage,
            reveal_asset=assets.reveal,
        )
        if assets.asset is None:
            r.state = kernel_round.void(r.state)
            r.finished_at = now
        self.rounds.append(r)
        return r

    def extend_plan(self, rounds: Sequence[PlannedRound]) -> None:
        """Дописать раунды в план (следующий тур сетки)."""
        if rounds and not self.is_active:
            raise SessionNotActiveError("сессия завершена")
        self.plan.extend(rounds)

    def end_by_rule(self, now: datetime) -> None:
        """Досрочное завершение по правилу ``session.end``: невыданный план отбрасывается."""
        if not self.is_active:
            return
        self.plan.clear()
        self._finish(now)

    def complete(self, now: datetime) -> None:
        """Завершить сессию, когда все выданные раунды сыграны и план пуст.

        Raises:
            ValueError: есть несыгранные раунды или план не пуст.
        """
        if not self.is_active:
            return
        if not self.needs_next or self.plan:
            raise ValueError("сессия не доиграна")
        self._finish(now)

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

    def apply(
        self,
        index: int,
        state: RoundState,
        *,
        points: int,
        now: datetime,
        choice: str | None = None,
    ) -> Round:
        """Применить новое состояние раунда после хода (ядро уже посчитало переход).

        Сессию не завершает: следующий раунд или итог определяет application по правилам
        режима (``materialize_next`` / ``end_by_rule`` / ``complete``).

        Raises:
            SessionNotActiveError, RoundFinishedError, RoundNotActiveError: см. ``active_round``.
            ValueError: ``choice`` не из вариантов раунда.
        """
        r = self.active_round(index)
        if choice is not None and r.option(choice) is None:
            raise ValueError("выбор не из вариантов раунда")
        r.state = state
        if choice is not None:
            r.choice = choice
        if state.finished:
            r.points = 0 if r.is_void else points
            r.finished_at = now
        self.version += 1
        self.last_move_at = now
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
        if self.progressive:
            self._record_bracket()
            return
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
        user_id = self.player.user_id
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

    def bracket_picks(self) -> list[tuple[int, UUID, UUID]]:
        """Выборы сетки по порядку: ``(тур, победитель, проигравший)``."""
        picks: list[tuple[int, UUID, UUID]] = []
        for r in self.rounds:
            pair = r.winner_loser()
            if pair is not None and r.stage is not None:
                picks.append((r.stage, *pair))
        return picks

    def _record_bracket(self) -> None:
        picks = self.bracket_picks()
        if not picks or self.day is None:
            # Сетка без выборов или без периода — итог не публикуется (контракт: minItems 1).
            return
        self.record(
            BracketCompleted(
                aggregate_id=str(self.id),
                session_id=self.id,
                version=self.version,
                user_id=self.player.user_id,
                is_guest=self.player.is_guest,
                mode_key=self.mode_key,
                mode_version=self.mode_version,
                period_start=self.day.isoformat(),
                champion_id=picks[-1][1],
                picks=tuple(
                    BracketPickSummary(stage=stage, winner_id=w, loser_id=lo)
                    for stage, w, lo in picks
                ),
            )
        )

    @property
    def counts_in_daily_stats(self) -> bool:
        """Результат идёт в статистику дня: пользователь и дейлик (гости — нет, D5; сетка
        считает свою статистику выборов)."""
        return (
            self.day is not None
            and not self.progressive
            and not self.player.is_guest
            and self.player.id is not None
        )

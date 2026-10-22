"""Тестовая инфраструктура онлайн-матча (этап 4a): DSL раундов матча и фейки портов match.

* ``given_match(cfg)`` — сценарий матча поверх фасада ``ModeEngine.contest_*`` (настоящие
  плагины: ``ContestRule``, matcher, Scorer), время — явно через ``at(t_ms)``::

    (given_match(duel_cfg).with_players("a", "b").with_items(film)
        .at(1_000).when_guess("b", text="мимо")
        .at(2_500).when_guess("a", exact=True).then_level_closed()
        .at(30_000).then_round_finished().then_scores({"a": 6, "b": 0}))

  Сценарий ведёт раунд, как владелец комнаты match: когда идёт время (``at``) и перед
  проверками ``then_level``/``then_round_finished``/``then_scores``/``then_places`` он
  сменяет закрытые уровни — досрочно закрытый (опережение, «ответили все») в момент
  последнего хода, закрытый по окну — в момент дедлайна. Ходы в то же время видят уровень
  до смены: так проверяется отказ хода по закрытому уровню (в match это команда со
  старой версией). Раунд стартует на первом ходе после предыдущего раунда — в текущее
  время (или явно ``start_round()``).

* ``FakeRoundPlanPort`` — план раундов от game (D5, ADR 0033): журнал запросов, готовый
  ответ или фабрика ответа, управляемые сбои (``unavailable``, ``pool_too_small``);
* ``FakeRealtime`` — публикации в Centrifugo (D9, ADR 0035): каждое сообщение проверяется
  по ``contracts/realtime``, журнал по каналам, проверка непрерывности версий, сбой
  «Centrifugo недоступен»;
* ``FakeLinkPort`` — ссылки уровней от media-pipeline (D11): ``AssetPort`` с проверкой
  «одна ссылка на (комната, раунд, уровень)».

Интерфейсы портов (имена методов и аргументов) согласованы по смыслу с планом этапа 4 §5
(R5, R6); типы запроса и ответа плана — объекты match: фейк их не разбирает.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from gv_core.errors import AppError, DependencyUnavailableError
from gv_game_kernel import (
    ONLINE_SESSIONS,
    AnswerInput,
    ContestRoundState,
    ContestStep,
    EngineFactory,
    MatchOutcome,
    MediaItemRef,
    ModeConfig,
    ModeEngine,
    Placement,
    PlayerStatus,
    PoolTooSmallError,
    RoundDraw,
    parse_mode_config,
)
from gv_testkit.contracts import assert_realtime_message_matches_schema
from gv_testkit.game import FakeAssetPort, LinkIssued, builtin_registry, media_item

ROOM_CHANNEL_PREFIX: Final = "room:"
"""Namespace канала комнаты Centrifugo (``room:{code}``, ADR 0035)."""


# ----------------------------------------------------------------------------- движок
def build_online_engine(config: ModeConfig | Mapping[str, Any]) -> ModeEngine:
    """Собрать движок онлайн-режима, как match: встроенные плагины, ``ONLINE_SESSIONS``.

    Raises:
        ConfigError: конфиг невалиден или режим соло (``config.session_unsupported``).
    """
    cfg = config if isinstance(config, ModeConfig) else parse_mode_config(config)
    return EngineFactory(builtin_registry(), supported_sessions=ONLINE_SESSIONS).build(cfg)


@dataclass
class MatchRound:
    """Раунд сценария: состав, состояние матча и последние ходы игроков."""

    draw: RoundDraw
    state: ContestRoundState | None = None
    steps: dict[str, ContestStep] = field(default_factory=dict)

    @property
    def item(self) -> MediaItemRef:
        """Загаданная единица раунда."""
        return self.draw.target


class MatchScenario:
    """Сценарий матча поверх ``ModeEngine.contest_*`` (без IO, синхронный).

    Args:
        engine: Движок онлайн-режима.
    """

    def __init__(self, engine: ModeEngine) -> None:
        self.engine = engine
        self.players: list[str] = []
        self.rounds: list[MatchRound] = []
        self.left: set[str] = set()
        self.now_ms = 0
        self._last_event_ms = 0
        self._current = -1

    # ------------------------------------------------------------------------- настройка
    def with_players(self, *player_ids: str) -> MatchScenario:
        """Игроки матча в порядке слотов."""
        self.players = list(player_ids)
        return self

    def with_items(self, *items: MediaItemRef) -> MatchScenario:
        """Загаданные единицы раундов по порядку (классические раунды)."""
        return self.with_draws(*(RoundDraw(target=item) for item in items))

    def with_draws(self, *draws: RoundDraw) -> MatchScenario:
        """Составы раундов по порядку (например, из плана game)."""
        self.rounds = [MatchRound(draw) for draw in draws]
        self._current = -1
        return self

    def at(self, t_ms: int) -> MatchScenario:
        """Перевести часы сервера на ``t_ms`` и закрыть уровни с истёкшим окном.

        Raises:
            AssertionError: время пошло назад.
        """
        if t_ms < self.now_ms:
            raise AssertionError(f"время не идёт назад: {t_ms} < {self.now_ms}")
        self.now_ms = t_ms
        self._settle()
        return self

    # ------------------------------------------------------------------------- раунд
    @property
    def current(self) -> MatchRound:
        """Текущий раунд (последний начатый)."""
        if self._current < 0:
            raise AssertionError("матч ещё не начался — нужен ход или start_round()")
        return self.rounds[self._current]

    @property
    def state(self) -> ContestRoundState:
        """Состояние текущего раунда."""
        state = self.current.state
        if state is None:  # pragma: no cover — раунд стартует вместе с current
            raise AssertionError("раунд не начат")
        return state

    def start_round(self) -> MatchScenario:
        """Начать следующий раунд в текущее время (игроки — без вышедших).

        Raises:
            AssertionError: нет игроков, текущий раунд идёт или раунды кончились.
        """
        if not self.players:
            raise AssertionError("нет игроков — with_players(...)")
        if self._current >= 0 and not self.engine.contest_round_finished(self.state):
            raise AssertionError("текущий раунд ещё идёт")
        if self._current + 1 >= len(self.rounds):
            raise AssertionError("раунды кончились — добавьте with_items(...)")
        self._current += 1
        active = [p for p in self.players if p not in self.left]
        self.rounds[self._current].state = self.engine.contest_start(active, now_ms=self.now_ms)
        self._last_event_ms = self.now_ms
        return self

    def _settle(self) -> None:
        """Закрыть уровни, как владелец комнаты: досрочно закрытый — в момент последнего
        хода, по окну — в момент дедлайна; пока время ``now_ms`` позволяет."""
        if self._current < 0:
            return
        engine = self.engine
        while not engine.contest_round_finished(self.state):
            if engine.contest_level_closed(self.state, now_ms=self._last_event_ms):
                at = self._last_event_ms
            else:
                deadline = engine.contest_deadline_ms(self.state)
                if deadline is None or deadline > self.now_ms:
                    return
                at = deadline
            self.current.state = engine.contest_advance(self.state, now_ms=at)
            self._last_event_ms = at

    def _round_for_move(self) -> MatchRound:
        if self._current < 0 or self.engine.contest_round_finished(self.state):
            self.start_round()
        return self.current

    def _set(self, state: ContestRoundState) -> None:
        self.current.state = state
        self._last_event_ms = self.now_ms

    # ------------------------------------------------------------------------- ходы
    def when_guess(
        self,
        player: str,
        *,
        text: str | None = None,
        item: MediaItemRef | None = None,
        option_id: str | None = None,
        exact: bool = False,
    ) -> MatchScenario:
        """Ответ игрока в текущее время: текстом, единицей, вариантом или ``exact=True``
        (название загаданной единицы).

        Raises:
            LevelLockedError | RoundFinishedError | AnswerKindError | OptionNotFoundError:
                как у ``ModeEngine.contest_guess``.
        """
        rnd = self._round_for_move()
        if exact:
            text = rnd.item.title
        if item is not None:
            answer, display = AnswerInput(media_item_id=item.id, selected=item), item.title
        elif option_id is not None:
            answer, display = AnswerInput(option_id=option_id), option_id
        else:
            answer, display = AnswerInput(text=text), text or ""
        step = self.engine.contest_guess(
            self.state, player, answer, rnd.draw, display=display, now_ms=self.now_ms
        )
        rnd.steps[player] = step
        self._set(step.state)
        return self

    def when_skip(self, player: str) -> MatchScenario:
        """Пас игрока на открытом уровне."""
        self._round_for_move()
        self._set(self.engine.contest_skip(self.state, player, now_ms=self.now_ms))
        return self

    def when_leave(self, player: str) -> MatchScenario:
        """Игрок вышел из матча: текущий раунд для него ``void``, следующие — без него."""
        self.left.add(player)
        if self._current >= 0 and not self.engine.contest_round_finished(self.state):
            self._set(self.engine.contest_leave(self.state, player))
        return self

    # ------------------------------------------------------------------------- проверки
    def then_level(self, expected: int) -> MatchScenario:
        """Открыт уровень ``expected``."""
        self._settle()
        assert self.state.level == expected, f"уровень {self.state.level}, ожидался {expected}"
        return self

    def then_level_closed(self, *, closed: bool = True) -> MatchScenario:
        """Текущий уровень закрыт (или открыт при ``closed=False``) в текущее время."""
        got = self.engine.contest_level_closed(self.state, now_ms=self.now_ms)
        assert got == closed, f"уровень {'закрыт' if got else 'открыт'}, ожидалось обратное"
        return self

    def then_verdict(self, player: str, expected: str) -> MatchScenario:
        """Вердикт последнего ответа игрока в текущем раунде (``exact``/``partial``/``miss``)."""
        step = self.current.steps.get(player)
        got = None if step is None else str(step.match.verdict)
        assert got == expected, f"вердикт {player}: {got!r}, ожидался {expected!r}"
        return self

    def then_statuses(self, expected: Mapping[str, PlayerStatus | str]) -> MatchScenario:
        """Статусы игроков, которые видят все участники."""
        got = {v.player_id: str(v.status) for v in self.engine.contest_views(self.state)}
        want = {k: str(v) for k, v in expected.items()}
        assert got == want, f"статусы {got}, ожидались {want}"
        return self

    def then_round_finished(self, *, finished: bool = True) -> MatchScenario:
        """Текущий раунд завершён для всех (или ещё идёт)."""
        self._settle()
        got = self.engine.contest_round_finished(self.state)
        assert got == finished, f"раунд {'завершён' if got else 'идёт'}, ожидалось обратное"
        return self

    def scores(self) -> dict[str, int]:
        """Сумма очков игроков по завершённым раундам (вышедшие — с очками до выхода)."""
        totals = dict.fromkeys(self.players, 0)
        for rnd in self.rounds[: self._current + 1]:
            if rnd.state is None or not self.engine.contest_round_finished(rnd.state):
                continue
            for pid, points in self.engine.contest_scores(rnd.state).items():
                totals[pid] += points
        return totals

    def then_scores(self, expected: Mapping[str, int]) -> MatchScenario:
        """Сумма очков по завершённым раундам."""
        self._settle()
        got = self.scores()
        assert got == dict(expected), f"очки {got}, ожидались {dict(expected)}"
        return self

    def placements(self) -> list[Placement]:
        """Места матча по сумме очков (``MatchOutcome.rank``, вышедшие — после оставшихся)."""
        return MatchOutcome.rank(self.scores(), left=self.left)

    def then_places(self, expected: Mapping[str, int]) -> MatchScenario:
        """Места игроков (с ничьими ``1, 1, 3``)."""
        self._settle()
        got = {p.player_id: p.place for p in self.placements()}
        assert got == dict(expected), f"места {got}, ожидались {dict(expected)}"
        return self


def given_match(config: ModeConfig | Mapping[str, Any] | ModeEngine) -> MatchScenario:
    """Начать сценарий матча по онлайн-конфигу (или готовому движку).

    По умолчанию раундов — ``session.rounds`` с единицами домена режима; свои единицы —
    ``with_items``.
    """
    engine = config if isinstance(config, ModeEngine) else build_online_engine(config)
    scenario = MatchScenario(engine)
    domain = engine.config.domain
    return scenario.with_items(
        *(media_item(f"Item{i}", domain=domain) for i in range(engine.config.session.rounds))
    )


# ----------------------------------------------------------------------------- порты
class FakeRoundPlanPort:
    """План раундов от game (``POST /internal/v1/round-plans``, D5) в памяти.

    Args:
        plan: Готовый ответ на любой запрос.
        factory: Ответ по запросу (например, план из ``engine.draw_rounds``); приоритетнее
            ``plan``.
    """

    def __init__(self, plan: Any = None, *, factory: Callable[[Any], Any] | None = None) -> None:
        # Any: запрос и план — объекты match (application-порт), фейк их не разбирает.
        self.requests: list[Any] = []
        self._plan = plan
        self._factory = factory
        self._error: AppError | None = None

    def fail(self, error: AppError) -> None:
        """Следующие запросы падают с ``error``."""
        self._error = error

    def unavailable(self) -> None:
        """game недоступен (503): новые матчи не стартуют (ADR 0033)."""
        self.fail(DependencyUnavailableError("game недоступен"))

    def pool_too_small(self, available: int = 0, required: int = 1) -> None:
        """В пуле/сборниках не хватает единиц (422 ``game.pool_too_small``)."""
        self.fail(
            PoolTooSmallError(
                "в пуле не хватает играбельных единиц",
                details={"available": available, "required": required},
            )
        )

    def heal(self) -> None:
        """Убрать сбой."""
        self._error = None

    async def create_round_plan(self, request: Any) -> Any:
        """Записать запрос и вернуть план (или упасть с настроенной ошибкой).

        Raises:
            AppError: настроенный сбой.
            AssertionError: ни ``plan``, ни ``factory`` не заданы.
        """
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._factory is not None:
            return self._factory(request)
        if self._plan is None:
            raise AssertionError("FakeRoundPlanPort: задайте plan или factory")
        return self._plan


@dataclass(frozen=True, slots=True)
class Publication:
    """Запись журнала ``FakeRealtime``: канал и сообщение."""

    channel: str
    # Any: сообщение — JSON-объект по contracts/realtime/room.message.v1.json.
    message: Mapping[str, Any]


class FakeRealtime:
    """Публикации в каналы Centrifugo в памяти (D9, ADR 0035).

    Каждое сообщение проверяется ``assert_realtime_message_matches_schema``: тест match
    падает, если в канал уходит то, чего нет в контракте (например, ответ раунда).

    Args:
        contract: Имя контракта канала (``room.message``).
        version: Версия контракта.
    """

    def __init__(self, *, contract: str = "room.message", version: int = 1) -> None:
        self.published: list[Publication] = []
        self._contract = contract
        self._version = version
        self._error: AppError | None = None

    def unavailable(self) -> None:
        """Centrifugo недоступен: публикации падают (клиенты переходят на HTTP-tick)."""
        self._error = DependencyUnavailableError("centrifugo недоступен")

    def heal(self) -> None:
        """Centrifugo снова доступен."""
        self._error = None

    async def publish(self, channel: str, message: Mapping[str, Any]) -> None:
        """Опубликовать сообщение в канал (проверив его по контракту).

        Raises:
            DependencyUnavailableError: настроен сбой Centrifugo.
            AssertionError: сообщение не соответствует ``contracts/realtime``.
        """
        if self._error is not None:
            raise self._error
        assert_realtime_message_matches_schema(dict(message), self._contract, self._version)
        self.published.append(Publication(channel, message))

    def messages(self, channel: str) -> list[Mapping[str, Any]]:
        """Сообщения канала по порядку."""
        return [p.message for p in self.published if p.channel == channel]

    def room(self, code: str) -> list[Mapping[str, Any]]:
        """Сообщения канала комнаты ``room:{code}``."""
        return self.messages(f"{ROOM_CHANNEL_PREFIX}{code}")

    def kinds(self, channel: str) -> list[str]:
        """Виды сообщений канала по порядку (``phase``, ``level_opened`` …)."""
        return [str(m["kind"]) for m in self.messages(channel)]

    def assert_versions_contiguous(self, channel: str) -> None:
        """Версии сообщений канала идут подряд без пропусков и повторов (D9)."""
        versions = [int(m["version"]) for m in self.messages(channel)]
        expected = list(range(versions[0], versions[0] + len(versions))) if versions else []
        assert versions == expected, f"версии канала {channel}: {versions}"


class FakeLinkPort(FakeAssetPort):
    """Ссылки уровней для матча (D11): ``AssetPort`` с журналом выдач по субъекту комнаты.

    Ссылка выдаётся одна на ``(комната, раунд, уровень)`` — субъект выдачи match строит из
    комнаты и раунда; ``assert_single_link_per_level`` ловит повторные выдачи (N игроков —
    N запросов вместо одного).
    """

    def subjects(self) -> list[str]:
        """Субъекты выдач по порядку первой выдачи."""
        return list(dict.fromkeys(x.subject for x in self.issued))

    def duplicates(self) -> list[LinkIssued]:
        """Выдачи, повторившие ``(набор, уровень, субъект)``."""
        counts = Counter(self.issued)
        return [x for x, n in counts.items() if n > 1]

    def assert_single_link_per_level(self) -> None:
        """Каждый уровень каждого раунда комнаты выдан ровно один раз."""
        dups = self.duplicates()
        assert not dups, f"повторные выдачи ссылок: {dups}"

    def levels_by_subject(self) -> dict[str, list[int]]:
        """Выданные уровни по субъектам."""
        result: defaultdict[str, list[int]] = defaultdict(list)
        for x in self.issued:
            result[x.subject].append(x.level)
        return dict(result)


__all__ = [
    "ROOM_CHANNEL_PREFIX",
    "FakeLinkPort",
    "FakeRealtime",
    "FakeRoundPlanPort",
    "MatchRound",
    "MatchScenario",
    "Publication",
    "build_online_engine",
    "given_match",
]

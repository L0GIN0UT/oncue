"""Тестовая инфраструктура игрового движка: фейки портов, фабрика единиц, DSL раунда.

* ``FakeAssetPort`` — журнал выдач ссылок и управляемые сбои (410 ``GoneError``,
  503 ``DependencyUnavailableError``, 429 ``RateLimitedError``);
* ``FakeCandidatePort`` — пул в памяти с фильтрацией по ``PoolQuery`` и журналом запросов;
* ``FakeCollectionPort`` — сборники в памяти;
* ``media_item(...)`` — единица каталога с разумными значениями по умолчанию;
* ``given_mode(cfg)`` — DSL сценария игры поверх настоящих плагинов::

    (given_mode(cfg).with_items(track)
        .when_guess(text="неверно").when_skip().when_guess(item=track)
        .then_round(outcome="exact", level=3).then_score(4))
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Final, Literal
from uuid import UUID

from gv_core.errors import AppError, DependencyUnavailableError, GoneError, RateLimitedError
from gv_game_kernel import (
    AnswerInput,
    AssetRef,
    ClueAsset,
    EngineFactory,
    MatchResult,
    MediaItemRef,
    ModeConfig,
    ModeEngine,
    PersonRef,
    PluginRegistry,
    PoolQuery,
    RoundOutcome,
    RoundState,
    parse_mode_config,
    register_builtin,
)

_counter = itertools.count(1)


class _Unset(Enum):
    """Маркер «не проверять» в ``then_round`` (``None`` — осмысленное значение)."""

    TOKEN = 0


_UNSET: Final = _Unset.TOKEN


def _uuid(n: int) -> UUID:
    """Детерминированный UUID по номеру: порядок id совпадает с порядком создания."""
    return UUID(int=n)


# ----------------------------------------------------------------------------- единицы
def person(name: str, role: str = "artist", *, id: UUID | None = None) -> PersonRef:  # noqa: A002 — как поле PersonRef.id
    """Персона единицы (по умолчанию — исполнитель)."""
    return PersonRef(id=id or _uuid(10**9 + next(_counter)), name=name, role=role)


def media_item(
    title: str = "Item",
    *,
    id: UUID | None = None,  # noqa: A002 — как поле MediaItemRef.id
    domain: str = "music",
    kind: str | None = None,
    year: int | None = 2000,
    aliases: Iterable[str] = (),
    title_aliases: Iterable[str] | None = None,
    persons: Iterable[PersonRef] = (),
    artists: Iterable[str] = (),
    genres: Iterable[str] = (),
    popularity: float | None = 0.5,
) -> MediaItemRef:
    """Единица каталога для тестов.

    Args:
        title: Основное название.
        id: ``media_item_id`` (по умолчанию — возрастающий детерминированный UUID).
        domain: ``music`` | ``film``; ``kind`` по умолчанию выводится из домена.
        aliases: Нормализованные алиасы (все виды).
        title_aliases: Алиасы видов ``title``/``alt`` (по умолчанию — ``aliases``).
        persons: Персоны.
        artists: Имена исполнителей — короткая запись для ``persons`` с ролью ``artist``.
    """
    alias_list = tuple(aliases)
    return MediaItemRef(
        id=id or _uuid(next(_counter)),
        domain=domain,
        kind=kind or ("track" if domain == "music" else "film"),
        title=title,
        year=year,
        aliases=alias_list,
        title_aliases=tuple(title_aliases) if title_aliases is not None else alias_list,
        persons=(*persons, *(person(name) for name in artists)),
        genres=tuple(genres),
        popularity=popularity,
    )


def asset_ref(levels_count: int = 6, *, asset_set_id: UUID | None = None) -> AssetRef:
    """Набор ассетов загадки."""
    return AssetRef(asset_set_id=asset_set_id or _uuid(next(_counter)), levels_count=levels_count)


# ----------------------------------------------------------------------------- порты
@dataclass(frozen=True, slots=True)
class LinkIssued:
    """Запись журнала ``FakeAssetPort``: кому и какой уровень выдан."""

    asset_set_id: UUID
    level: int
    subject: str


class FakeAssetPort:
    """``AssetPort`` в памяти.

    Args:
        now: Момент выдачи (ссылка живёт ``ttl_s``).
        ttl_s: Время жизни ссылки.
    """

    def __init__(self, *, now: datetime | None = None, ttl_s: int = 60) -> None:
        self.issued: list[LinkIssued] = []
        self._now = now or datetime(2026, 1, 1, tzinfo=UTC)
        self._ttl = timedelta(seconds=ttl_s)
        self._failures: dict[UUID | None, AppError] = {}

    def fail(self, error: AppError, *, asset_set_id: UUID | None = None) -> None:
        """Следующие выдачи (набора или всех, если ``None``) падают с ``error``."""
        self._failures[asset_set_id] = error

    def gone(self, asset_set_id: UUID | None = None) -> None:
        """Набор выключен: 410 ``GoneError`` (раунд становится ``void``)."""
        self.fail(GoneError("набор ассетов выключен"), asset_set_id=asset_set_id)

    def unavailable(self) -> None:
        """media-pipeline недоступен: 503 ``DependencyUnavailableError``."""
        self.fail(DependencyUnavailableError("media-pipeline недоступен"))

    def rate_limited(self) -> None:
        """Лимит выдачи исчерпан: 429 ``RateLimitedError``."""
        self.fail(RateLimitedError("лимит ссылок"))

    def heal(self) -> None:
        """Убрать все сбои."""
        self._failures.clear()

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        """Выдать ссылку (или упасть с настроенной ошибкой)."""
        error = self._failures.get(asset.asset_set_id) or self._failures.get(None)
        if error is not None:
            raise error
        self.issued.append(LinkIssued(asset.asset_set_id, level, subject))
        return ClueAsset(
            level=level,
            url=f"/media/a/{asset.asset_set_id.hex}{level:02d}.bin",
            expires_at=self._now + self._ttl,
            mime="application/octet-stream",
        )

    def levels_for(self, subject: str) -> list[int]:
        """Уровни, выданные субъекту, в порядке выдачи."""
        return [x.level for x in self.issued if x.subject == subject]


class FakeCandidatePort:
    """``CandidatePort`` в памяти: фильтрует единицы по ``PoolQuery`` и сортирует по id.

    Args:
        items: Пул единиц.
        playable: Пары ``(media_item_id, PlayableFor)`` с готовыми ассетами; ``None`` —
            играбельны все единицы.
    """

    def __init__(
        self,
        items: Iterable[MediaItemRef] = (),
        *,
        playable: Iterable[tuple[UUID, str, str]] | None = None,
    ) -> None:
        self.items = list(items)
        self._playable = None if playable is None else set(playable)
        self.queries: list[tuple[PoolQuery, int]] = []

    def add(self, *items: MediaItemRef) -> None:
        """Добавить единицы в пул."""
        self.items.extend(items)

    def matches(self, item: MediaItemRef, q: PoolQuery) -> bool:
        """Подходит ли единица под запрос (та же семантика ждётся от SQL-адаптера game)."""
        checks = (
            item.domain == q.domain,
            not q.kinds or item.kind in q.kinds,
            not q.genres_any or bool(q.genres_any & set(item.genres)),
            q.year_from is None or (item.year is not None and item.year >= q.year_from),
            q.year_to is None or (item.year is not None and item.year <= q.year_to),
            q.min_popularity is None
            or (item.popularity is not None and item.popularity >= q.min_popularity),
            item.id not in q.exclude_ids,
            q.playable_for is None
            or self._playable is None
            or (item.id, q.playable_for.clue_type, q.playable_for.params_hash) in self._playable,
        )
        return all(checks)

    async def candidates(self, query: PoolQuery, limit: int) -> list[MediaItemRef]:
        """До ``limit`` подходящих единиц в порядке id."""
        self.queries.append((query, limit))
        found = sorted((i for i in self.items if self.matches(i, query)), key=lambda i: i.id)
        return found[:limit]


class FakeCollectionPort:
    """``CollectionPort`` в памяти: ``{collection_id: [единицы в порядке сборника]}``."""

    def __init__(self, collections: Mapping[UUID, Sequence[MediaItemRef]] | None = None) -> None:
        self.collections = {k: list(v) for k, v in (collections or {}).items()}

    async def items(self, collection_id: UUID, limit: int) -> list[MediaItemRef]:
        """До ``limit`` единиц сборника (неизвестный сборник — пусто)."""
        return self.collections.get(collection_id, [])[:limit]


# ----------------------------------------------------------------------------- движок
def builtin_registry() -> PluginRegistry:
    """Реестр со встроенными плагинами этапа 2."""
    return register_builtin(PluginRegistry())


def build_engine(config: ModeConfig | Mapping[str, Any]) -> ModeEngine:
    """Собрать движок режима на встроенных плагинах (``ConfigError``, если невалиден)."""
    cfg = config if isinstance(config, ModeConfig) else parse_mode_config(config)
    return EngineFactory(builtin_registry()).build(cfg)


@dataclass
class PlayedRound:
    """Раунд сценария: загаданная единица, состояние, ответы и очки."""

    item: MediaItemRef
    state: RoundState = field(default_factory=RoundState)
    matches: list[MatchResult] = field(default_factory=list)
    elapsed_ms: int | None = None

    def outcome(self) -> RoundOutcome:
        """Итог завершённого раунда."""
        return ModeEngine.outcome(self.state, elapsed_ms=self.elapsed_ms)


class GameScenario:
    """Сценарий соло-игры поверх ``ModeEngine`` (без IO, синхронный).

    Раунды идут по порядку ``with_items``; ход после завершения раунда относится к
    следующему. ``then_*`` проверяют последний тронутый раунд (или раунд ``index``).
    """

    def __init__(self, engine: ModeEngine) -> None:
        self.engine = engine
        self.rounds: list[PlayedRound] = []
        self._current = 0
        self._last: PlayedRound | None = None

    def with_items(self, *items: MediaItemRef) -> GameScenario:
        """Загаданные единицы раундов по порядку."""
        self.rounds = [PlayedRound(item) for item in items]
        self._current = 0
        return self

    # ------------------------------------------------------------------------- ходы
    def _round(self) -> PlayedRound:
        while self._current < len(self.rounds) and self.rounds[self._current].state.finished:
            self._current += 1
        if self._current >= len(self.rounds):
            raise AssertionError("в сценарии не осталось раундов — добавьте with_items(...)")
        return self.rounds[self._current]

    def when_guess(
        self,
        *,
        text: str | None = None,
        item: MediaItemRef | None = None,
        item_id: UUID | None = None,
        elapsed_ms: int | None = None,
    ) -> GameScenario:
        """Ответ текстом, выбором единицы из проекции (``item``) или голым id (``item_id``)."""
        rnd = self._round()
        if item is not None:
            answer = AnswerInput(media_item_id=item.id, selected=item)
            display = item.title
        elif item_id is not None:
            answer, display = AnswerInput(media_item_id=item_id), str(item_id)
        else:
            answer, display = AnswerInput(text=text), text or ""
        step = self.engine.answer(rnd.state, answer, rnd.item, display=display)
        rnd.state, rnd.elapsed_ms = step.state, elapsed_ms
        rnd.matches.append(step.match)
        self._last = rnd
        return self

    def when_skip(self) -> GameScenario:
        """Пропустить уровень."""
        rnd = self._round()
        rnd.state = self.engine.skip(rnd.state)
        self._last = rnd
        return self

    def when_give_up(self) -> GameScenario:
        """Сдаться в текущем раунде."""
        rnd = self._round()
        rnd.state = self.engine.give_up(rnd.state)
        self._last = rnd
        return self

    def when_void(self) -> GameScenario:
        """Аннулировать текущий раунд (ассет недоступен)."""
        rnd = self._round()
        rnd.state = self.engine.void(rnd.state)
        self._last = rnd
        return self

    # ------------------------------------------------------------------------- проверки
    def _pick(self, index: int | None) -> PlayedRound:
        if index is not None:
            return self.rounds[index]
        if self._last is None:
            raise AssertionError("ещё не было ходов")
        return self._last

    def then_round(
        self,
        *,
        index: int | None = None,
        outcome: str | Literal[_Unset.TOKEN] | None = _UNSET,
        level: int | None = None,
        partial_level: int | Literal[_Unset.TOKEN] | None = _UNSET,
        last_match: str | None = None,
        points: int | None = None,
    ) -> GameScenario:
        """Проверить раунд: итог (``None`` — идёт), открытый уровень, вердикт, очки."""
        rnd = self._pick(index)
        state = rnd.state
        if outcome is not _UNSET:
            actual = None if state.outcome is None else str(state.outcome)
            assert actual == outcome, f"итог раунда {actual!r}, ожидался {outcome!r}"
        if level is not None:
            assert state.level == level, f"уровень {state.level}, ожидался {level}"
        if partial_level is not _UNSET:
            assert state.partial_awarded_level == partial_level, (
                f"частичный на уровне {state.partial_awarded_level}, ожидался {partial_level}"
            )
        if last_match is not None:
            actual_match = str(rnd.matches[-1].verdict) if rnd.matches else None
            assert actual_match == last_match, f"вердикт {actual_match!r}, ожидался {last_match!r}"
        if points is not None:
            got = self.engine.score(rnd.outcome())
            assert got == points, f"очки раунда {got}, ожидалось {points}"
        return self

    def score(self) -> int:
        """Сумма очков завершённых раундов."""
        return sum(self.engine.score(r.outcome()) for r in self.rounds if r.state.finished)

    def max_score(self) -> int:
        """Максимум очков по не-``void`` раундам сценария."""
        playable = [r for r in self.rounds if str(r.state.outcome) != "void"]
        return self.engine.max_score(len(playable))

    def then_score(self, expected: int, *, max_score: int | None = None) -> GameScenario:
        """Проверить сумму очков (и, при желании, максимум) по сценарию."""
        assert self.score() == expected, f"очки {self.score()}, ожидалось {expected}"
        if max_score is not None:
            assert self.max_score() == max_score, f"максимум {self.max_score()}"
        return self


def given_mode(config: ModeConfig | Mapping[str, Any] | ModeEngine) -> GameScenario:
    """Начать сценарий игры по конфигу режима (или готовому движку)."""
    engine = config if isinstance(config, ModeEngine) else build_engine(config)
    return GameScenario(engine)

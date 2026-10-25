"""Сезонные события: определение, запуск, находки, триггеры, прогресс (ADR 0054-0056).

Домен без IO: тело определения разбирается в неизменяемые структуры, статус запуска считается по
времени (задача ``progression.events.tick`` лишь материализует его), прогресс находки двигается
изменениями метрик (``MetricUpdate``), а не ``if`` по типу метрики: правило принятия метрики
триггером - данные (``Trigger.accepts``). Форма тела проверена схемой
``contracts/progression/event_definition.v1.json`` и pydantic-моделью API; здесь - правила,
которые схема выразить не может (уникальность ключей и номеров, параметры метрик).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from gv_core.errors import ConflictError, InvalidInputError, NotFoundError
from progression_service.domain import metrics
from progression_service.domain.metrics import MetricUpdate, merge

SERVER = "server"
UI_SPOT = "ui_spot"
PERIOD_DAY = "day"
PERIOD_EVENT = "event"
EVENT_PERIOD_KEY = "event"
_TAG_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
"""Ключ тега сборника (как `TagKey` в collections, ADR 0067)."""
"""Ключ периода прогресса находки с периодом ``event`` (у ``day`` - ISO-дата суток)."""
REWARD_REASON = "event"
"""Причина проводок ledger и отметок событий (``reason``)."""

DEFINITION_INVALID = "progression.event_definition_invalid"


class EventNotFoundError(NotFoundError):
    """Нет такого события (или оно не опубликовано для игрока) - 404."""

    default_code = "progression.event_not_found"

    def __init__(self) -> None:
        super().__init__("событие не найдено")


class EventFindNotFoundError(NotFoundError):
    """В событии нет такой находки - 404."""

    default_code = "progression.event_find_not_found"

    def __init__(self) -> None:
        super().__init__("находка не найдена")


class EventRunNotFoundError(NotFoundError):
    """Нет такого запуска - 404."""

    default_code = "progression.event_run_not_found"

    def __init__(self) -> None:
        super().__init__("запуск не найден")


class EventNotActiveError(ConflictError):
    """Запуск не идёт сейчас - 409."""

    default_code = "progression.event_not_active"

    def __init__(self) -> None:
        super().__init__("событие сейчас не идёт")


class EventFindNotUiSpotError(ConflictError):
    """Находка открывается серверной метрикой, а не кликом - 409."""

    default_code = "progression.event_find_not_ui_spot"

    def __init__(self) -> None:
        super().__init__("находка открывается действием в игре, а не кликом")


class EventFindLockedError(ConflictError):
    """Финальная находка закрыта, пока не открыты остальные - 409."""

    default_code = "progression.event_find_locked"

    def __init__(self) -> None:
        super().__init__("сначала откройте остальные находки")


class EventKeyTakenError(ConflictError):
    """Ключ события занят - 409."""

    default_code = "progression.event_key_taken"

    def __init__(self) -> None:
        super().__init__("ключ события уже занят")


class EventDefinitionLockedError(ConflictError):
    """Определение нельзя менять: есть опубликованный или идущий запуск - 409."""

    default_code = "progression.event_definition_locked"

    def __init__(self) -> None:
        super().__init__("есть опубликованный запуск: определение не меняется")


class EventDefinitionChangedError(ConflictError):
    """Правка определения основана на устаревшей версии (хеш не совпал) - 409."""

    default_code = "progression.event_definition_changed"

    def __init__(self) -> None:
        super().__init__("определение изменили, откройте заново")


class EventBodyChangedError(ConflictError):
    """Хеш определения при публикации не совпал с хешем, который видел админ - 409."""

    default_code = "progression.event_body_changed"

    def __init__(self) -> None:
        super().__init__("определение изменилось после просмотра, перечитайте и повторите")


class EventPeriodOverlapError(ConflictError):
    """Период пересекается с другим опубликованным запуском - 409."""

    default_code = "progression.event_period_overlap"

    def __init__(self) -> None:
        super().__init__("период пересекается с другим опубликованным запуском")


class EventRunStateInvalidError(ConflictError):
    """Операция не подходит статусу запуска - 409."""

    default_code = "progression.event_run_state_invalid"

    def __init__(self, message: str = "запуск в другом статусе") -> None:
        super().__init__(message)


class EventPeriodInvalidError(InvalidInputError):
    """Период запуска неверен - 422."""

    default_code = "progression.event_period_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EventDefinitionInvalidError(InvalidInputError):
    """Определение нарушает правила; ``details.errors`` - пути и причины - 422."""

    default_code = DEFINITION_INVALID

    def __init__(self, errors: Sequence[Mapping[str, str]]) -> None:
        super().__init__(
            f"определение события невалидно: {len(errors)} ошибок",
            details={"errors": [dict(e) for e in errors]},
        )


class RunStatus(StrEnum):
    """Статус запуска (``draft`` и ``stopped`` хранятся, остальные считаются по времени)."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ACTIVE = "active"
    FINISHED = "finished"
    STOPPED = "stopped"


STORED_LIVE = (RunStatus.PUBLISHED, RunStatus.ACTIVE, RunStatus.FINISHED)
"""Хранимые статусы опубликованного запуска, не остановленного админом."""


def run_status(stored: str, starts_at: datetime, ends_at: datetime, now: datetime) -> RunStatus:
    """Статус запуска на момент ``now``: опубликованный сдвигается по времени (ADR 0054)."""
    status = RunStatus(stored)
    if status in (RunStatus.DRAFT, RunStatus.STOPPED):
        return status
    if now < starts_at:
        return RunStatus.PUBLISHED
    if now < ends_at:
        return RunStatus.ACTIVE
    return RunStatus.FINISHED


def effective_end(ends_at: datetime, stopped_at: datetime | None) -> datetime:
    """Момент, после которого события игрока запуск не двигают."""
    return ends_at if stopped_at is None else min(ends_at, stopped_at)


def canonical_sha256(body: object) -> str:
    """sha256 канонического JSON тела (ключи по порядку, без пробелов)."""
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def find_op_id(run_id: UUID, find_key: str, user_id: UUID) -> str:
    """``op_id`` проводок награды находки."""
    return f"event:{run_id}:{find_key}:{user_id}"


def complete_op_id(run_id: UUID, user_id: UUID) -> str:
    """``op_id`` нулевой отметки закрытия события."""
    return f"event:{run_id}:complete:{user_id}"


# --------------------------------------------------------------------------- определение
@dataclass(frozen=True, slots=True)
class Trigger:
    """Условие открытия находки.

    ``server`` - метрика реестра с целью и периодом; ``ui_spot`` - клик по спрятанному элементу
    страницы ``page_key``.
    """

    type: str
    page_key: str | None = None
    metric: str | None = None
    target: int | None = None
    period: str | None = None
    place_max: int | None = None
    slot: str | None = None
    item_key: str | None = None
    domain: str | None = None
    collection_keys: tuple[str, ...] = ()
    collection_tags: tuple[str, ...] = ()

    def accepts(self, update: MetricUpdate) -> bool:
        """Двигает ли изменение метрики эту находку (метрика, домен, параметры)."""
        if self.type != SERVER or update.metric != self.metric:
            return False
        if self.domain is not None and update.domain != self.domain:
            return False
        if self.place_max is not None and (update.place is None or update.place > self.place_max):
            return False
        if self.slot is not None and update.slot != self.slot:
            return False
        if not self._accepts_collection(update):
            return False
        return self.item_key is None or update.item_key == self.item_key

    def _accepts_collection(self, update: MetricUpdate) -> bool:
        """Фильтры сборника; заданные `collection_keys` и `collection_tags` - «и» (ADR 0067)."""
        if self.collection_keys and update.collection_id not in self.collection_keys:
            return False
        return not self.collection_tags or bool(
            set(self.collection_tags) & set(update.collection_tags)
        )

    def advance(self, progress: int, update: MetricUpdate) -> int:
        """Новый прогресс, не выше цели."""
        assert self.target is not None  # noqa: S101 - у server цель есть (инвариант разбора)
        return min(self.target, merge(progress, update))

    def period_key(self, day: date) -> str:
        """Ключ периода прогресса: сутки для ``day``, общий для ``event``."""
        return day.isoformat() if self.period == PERIOD_DAY else EVENT_PERIOD_KEY


@dataclass(frozen=True, slots=True)
class FindDef:
    """Находка определения."""

    key: str
    number: int
    title: str
    diary_before: str
    diary_after: str
    x_pct: float
    y_pct: float
    xp: int
    verses: int
    final: bool
    play_page: str | None
    trigger: Trigger


@dataclass(frozen=True, slots=True)
class Definition:
    """Определение события."""

    title: str
    description: str
    theme_key: str
    finds: tuple[FindDef, ...]
    item_key: str | None

    def find(self, key: str) -> FindDef | None:
        """Находка по ключу."""
        return next((f for f in self.finds if f.key == key), None)

    @property
    def total(self) -> int:
        """Сколько находок в событии."""
        return len(self.finds)

    def server_finds(self) -> list[FindDef]:
        """Находки с серверным триггером."""
        return [f for f in self.finds if f.trigger.type == SERVER]


def _trigger(raw: Mapping[str, Any]) -> Trigger:
    params: Mapping[str, Any] = raw.get("params") or {}
    if raw["type"] == UI_SPOT:
        return Trigger(type=UI_SPOT, page_key=raw["page_key"])
    return Trigger(
        type=SERVER,
        metric=raw["metric"],
        target=int(raw["target"]),
        period=raw["period"],
        place_max=params.get("place_max"),
        slot=params.get("slot"),
        item_key=params.get("item_key"),
        domain=params.get("domain"),
        collection_keys=tuple(str(k).lower() for k in params.get("collection_keys") or ()),
        collection_tags=tuple(str(k) for k in params.get("collection_tags") or ()),
    )


def parse_definition(body: Mapping[str, Any]) -> Definition:
    """Тело определения (уже проверено схемой) в доменные структуры, находки - по номерам."""
    finds = tuple(
        sorted(
            (
                FindDef(
                    key=f["key"],
                    number=int(f["number"]),
                    title=f["title"],
                    diary_before=f["diary_before"],
                    diary_after=f["diary_after"],
                    x_pct=float(f["x_pct"]),
                    y_pct=float(f["y_pct"]),
                    xp=int(f["reward"]["xp"]),
                    verses=int(f["reward"]["verses"]),
                    final=bool(f["final"]),
                    play_page=f.get("play_page"),
                    trigger=_trigger(f["trigger"]),
                )
                for f in body["finds"]
            ),
            key=lambda f: f.number,
        )
    )
    return Definition(
        title=body["title"],
        description=body["description"],
        theme_key=body["theme_key"],
        finds=finds,
        item_key=body["reward"]["item_key"],
    )


def definition_errors(
    definition: Definition, *, slot_keys: Collection[str] | None
) -> list[dict[str, str]]:
    """Правила определения, которых нет в схеме: ``[{path, reason}]``.

    Args:
        definition: Разобранное определение.
        slot_keys: Ключи слотов ``shop.slots`` конфига; ``None`` - магазина в конфиге нет.
    """
    errors: list[dict[str, str]] = []
    if sum(f.final for f in definition.finds) != 1:
        errors.append({"path": "finds", "reason": "ровно одна находка должна быть финальной"})
    for name, values in (
        ("key", [str(f.key) for f in definition.finds]),
        ("number", [str(f.number) for f in definition.finds]),
    ):
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            errors.append({"path": f"finds[].{name}", "reason": f"повтор: {', '.join(duplicates)}"})
    for i, find in enumerate(definition.finds):
        errors.extend(_trigger_errors(find, i, slot_keys))
    return errors


def _trigger_errors(
    find: FindDef, index: int, slot_keys: Collection[str] | None
) -> list[dict[str, str]]:
    t = find.trigger
    where = f"finds[{index}].trigger"
    if t.type != SERVER:
        return []
    errors: list[dict[str, str]] = []
    assert t.metric is not None  # noqa: S101 - у server метрика есть (схема)
    needs = {
        metrics.WEEKLY_TOP_PLACE: t.place_max is not None,
        metrics.ITEM_EQUIPPED: (t.slot is None) != (t.item_key is None),
    }
    if not needs.get(t.metric, True):
        errors.append({"path": f"{where}.params", "reason": f"параметры метрики {t.metric}"})
    if t.place_max is not None and t.metric != metrics.WEEKLY_TOP_PLACE:
        errors.append({"path": f"{where}.params.place_max", "reason": "только weekly_top_place"})
    if (t.slot or t.item_key) and t.metric != metrics.ITEM_EQUIPPED:
        errors.append({"path": f"{where}.params", "reason": "slot/item_key только item_equipped"})
    if t.collection_keys and t.metric != metrics.COLLECTION_PLAYED:
        errors.append(
            {"path": f"{where}.params.collection_keys", "reason": "только collection_played"}
        )
    if len(set(t.collection_keys)) != len(t.collection_keys):
        errors.append({"path": f"{where}.params.collection_keys", "reason": "повторы"})
    if t.collection_tags and t.metric != metrics.COLLECTION_PLAYED:
        errors.append(
            {"path": f"{where}.params.collection_tags", "reason": "только collection_played"}
        )
    if len(set(t.collection_tags)) != len(t.collection_tags):
        errors.append({"path": f"{where}.params.collection_tags", "reason": "повторы"})
    if any(_TAG_RE.fullmatch(tag) is None for tag in t.collection_tags):
        errors.append(
            {"path": f"{where}.params.collection_tags", "reason": "ключ тега: [a-z][a-z0-9_]{0,31}"}
        )
    if t.metric == metrics.ITEM_EQUIPPED and t.target != 1:
        errors.append({"path": f"{where}.target", "reason": "надеть предмет - цель 1"})
    if t.slot is not None and (slot_keys is None or t.slot not in slot_keys):
        errors.append({"path": f"{where}.params.slot", "reason": f"нет слота {t.slot!r}"})
    return errors


# --------------------------------------------------------------------------- прогресс
class FindState(StrEnum):
    """Состояние находки для игрока."""

    LOCKED = "locked"
    OPEN = "open"
    FOUND = "found"


def find_state(find: FindDef, unlocked: Collection[str], definition: Definition) -> FindState:
    """Найдена; закрыта (финальная, пока не открыты остальные); иначе открыта."""
    if find.key in unlocked:
        return FindState.FOUND
    if find.final and not others_done(definition, unlocked):
        return FindState.LOCKED
    return FindState.OPEN


def others_done(definition: Definition, unlocked: Collection[str]) -> bool:
    """Открыты все находки, кроме финальной (финал можно открывать)."""
    return all(f.key in unlocked for f in definition.finds if not f.final)

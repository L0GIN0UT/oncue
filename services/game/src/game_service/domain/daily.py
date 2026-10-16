"""Наборы дня (D6): заранее сгенерированные единицы дейлика с курацией до начала дня.

Набор фиксирует версию конфига, с которой создан: сегодняшний дейлик играется на ней,
даже если режим уже переопубликован. Заменять единицы можно только в ``scheduled`` и до
начала дня; в начале дня набор блокируется (``locked``) и дальше только читается.

Этап 3a (ADR 0027): набор дня обобщён до набора периода — ``period`` (``day`` | ``week``),
``day`` — начало периода в зоне режима; у сетки есть тема (``subject_id`` — персона).
Все правила (курация до начала периода, блокировка) — те же.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from game_service.domain.errors import (
    DailyIndexOutOfRangeError,
    DailyLockedError,
    ItemDuplicateError,
    ItemNotPlayableError,
)
from gv_core.domain import AggregateRoot


class DailySetStatus(StrEnum):
    """Статус набора дня."""

    SCHEDULED = "scheduled"
    LOCKED = "locked"
    FAILED = "failed"  # пул меньше нужного — повтор на следующем запуске планировщика


class GeneratedBy(StrEnum):
    """Кто создал набор (аудит)."""

    SCHEDULE = "schedule"
    LAZY = "lazy"  # первый запрос дня, если планировщик не успел
    ADMIN = "admin"


def day_in_zone(now: datetime, timezone: str) -> date:
    """День в зоне режима (смена дня — 00:00 в ``timezone``)."""
    return now.astimezone(ZoneInfo(timezone)).date()


def next_reset_at(now: datetime, timezone: str) -> datetime:
    """Момент следующей смены дня (UTC-aware)."""
    zone = ZoneInfo(timezone)
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=zone).astimezone(UTC)


def daily_seed(secret: bytes, mode_key: str, day: date, generation: int = 1) -> bytes:
    """Seed розыгрыша набора: ``HMAC(secret, "<mode_key>|<day>")`` (D6).

    Детерминирован и непредсказуем без секрета. Перегенерация (``generation > 1``)
    добавляет номер поколения — иначе розыгрыш дал бы тот же набор.
    """
    message = f"{mode_key}|{day.isoformat()}"
    if generation > 1:
        message += f"|{generation}"
    return hmac.new(secret, message.encode(), hashlib.sha256).digest()


@dataclass(eq=False)
class DailySet(AggregateRoot[tuple[str, date]]):
    """Набор дня режима.

    Attributes:
        mode_key: Ключ режима.
        day: День (в зоне режима).
        mode_version: Версия конфига, на которой играется этот день.
        status: ``scheduled`` → ``locked``; ``failed`` — не хватило пула.
        items: Единицы раундов по порядку.
        generation: Поколение розыгрыша (растёт при перегенерации).
        generated_by: Кто создал набор.
        locked_at: Момент блокировки.
        replaced: Индексы, заменённые в этой транзакции (для репозитория).
        period: Период набора (``day`` | ``week``); ``day`` — его начало.
        subject_id: Тема периода (персона сетки) или ``None``.
    """

    mode_key: str
    day: date
    mode_version: int
    status: DailySetStatus
    items: list[UUID] = field(default_factory=list)
    generation: int = 1
    generated_by: GeneratedBy = GeneratedBy.SCHEDULE
    locked_at: datetime | None = None
    replaced: set[int] = field(default_factory=set)
    items_rewritten: bool = False
    period: str = "day"
    subject_id: UUID | None = None

    def __post_init__(self) -> None:
        AggregateRoot.__init__(self, (self.mode_key, self.day))

    @classmethod
    def generated(
        cls,
        mode_key: str,
        day: date,
        *,
        mode_version: int,
        items: list[UUID],
        generated_by: GeneratedBy,
        period: str = "day",
        subject_id: UUID | None = None,
    ) -> DailySet:
        """Новый набор с разыгранными единицами (и темой периода, если она есть)."""
        if len(set(items)) != len(items):
            raise ItemDuplicateError("единицы набора повторяются")
        return cls(
            mode_key,
            day,
            mode_version,
            DailySetStatus.SCHEDULED if items else DailySetStatus.FAILED,
            list(items),
            generated_by=generated_by,
            items_rewritten=True,
            period=period,
            subject_id=subject_id if items else None,
        )

    # ------------------------------------------------------------------ проверки
    def is_locked_on(self, today: date) -> bool:
        """Набор только для чтения: заблокирован или его день уже начался."""
        return self.status is DailySetStatus.LOCKED or self.day <= today

    def _require_editable(self, today: date) -> None:
        if self.is_locked_on(today):
            raise DailyLockedError("день начался — набор только для чтения")

    # ------------------------------------------------------------------ курация
    def replace_item(self, index: int, media_item_id: UUID, *, playable: bool, today: date) -> UUID:
        """Заменить единицу ``index`` (только ``scheduled`` и до начала дня).

        Returns:
            Прежняя единица.

        Raises:
            DailyLockedError: день начался или набор заблокирован.
            DailyIndexOutOfRangeError: нет такого номера.
            ItemNotPlayableError: единица не играбельна под режим.
            ItemDuplicateError: единица уже есть в наборе.
        """
        self._require_editable(today)
        if self.status is not DailySetStatus.SCHEDULED:
            raise DailyLockedError("набор не запланирован — перегенерируйте его")
        if not 0 <= index < len(self.items):
            raise DailyIndexOutOfRangeError(
                "номер единицы вне набора", details={"size": len(self.items)}
            )
        if not playable:
            raise ItemNotPlayableError("единица не играбельна под параметры режима")
        previous = self.items[index]
        if media_item_id != previous and media_item_id in self.items:
            raise ItemDuplicateError("единица уже есть в наборе")
        self.items[index] = media_item_id
        self.replaced.add(index)
        return previous

    def auto_replace(self, index: int, media_item_id: UUID) -> None:
        """Замена непригодной единицы планировщиком перед блокировкой (без проверки дня)."""
        if self.status is DailySetStatus.LOCKED:
            raise DailyLockedError("набор заблокирован")
        if media_item_id in self.items:
            raise ItemDuplicateError("единица уже есть в наборе")
        self.items[index] = media_item_id
        self.replaced.add(index)

    def regenerate(
        self,
        items: list[UUID],
        *,
        mode_version: int,
        generated_by: GeneratedBy,
        today: date,
        subject_id: UUID | None = None,
    ) -> None:
        """Новый розыгрыш набора.

        ``scheduled`` замораживается в начале дня (нельзя менять «боевой» паззл дня). А вот
        ``failed`` — это пустой, неиграбельный набор: его перерисовывают и в уже начавшийся
        день (так же поступает планировщик через :meth:`reroll`), иначе день, засеянный без
        готовых ассетов, остался бы без дейлика навсегда. ``locked`` неизменяем всегда.

        Raises:
            DailyLockedError: набор заблокирован, либо ``scheduled`` и день уже начался.
        """
        if self.status is not DailySetStatus.FAILED:
            self._require_editable(today)
        self.reroll(
            items, mode_version=mode_version, generated_by=generated_by, subject_id=subject_id
        )

    def reroll(
        self,
        items: list[UUID],
        *,
        mode_version: int,
        generated_by: GeneratedBy,
        subject_id: UUID | None = None,
    ) -> None:
        """Перегенерация без проверки дня (повтор ``failed`` планировщиком)."""
        if self.status is DailySetStatus.LOCKED:
            raise DailyLockedError("набор заблокирован")
        if len(set(items)) != len(items):
            raise ItemDuplicateError("единицы набора повторяются")
        self.items = list(items)
        self.generation += 1
        self.mode_version = mode_version
        self.generated_by = generated_by
        self.status = DailySetStatus.SCHEDULED if items else DailySetStatus.FAILED
        self.subject_id = subject_id if items else None
        self.items_rewritten = True

    def lock(self, now: datetime) -> None:
        """Заблокировать набор в начале дня (повтор — no-op)."""
        if self.status is DailySetStatus.SCHEDULED:
            self.status = DailySetStatus.LOCKED
            self.locked_at = now

    @property
    def is_playable(self) -> bool:
        """По набору можно играть (есть единицы и он не ``failed``)."""
        return self.status is not DailySetStatus.FAILED and bool(self.items)

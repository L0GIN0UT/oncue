"""Проводки в одной транзакции: журнал → баланс/прогресс → события (ADR 0042, 0043).

``Poster`` живёт ровно одну транзакцию. Порядок защищает от двойного начисления и гонок:

1. строка ``progress`` игрока берётся ``FOR UPDATE`` — все операции над одним игроком
   сериализуются. Порядок блокировок: игроки по возрастанию ``user_id`` (вызывающий use-case
   обходит их отсортированными), на каждого — «progress → счётчики → баланс», и только затем
   следующий игрок. Награды рейтинга сначала берут ``progress`` всех игроков по порядку, потом
   ``reward_periods`` и балансы. Так параллельные матчи с общими игроками в любом порядке не
   образуют цикла ожиданий (тест ``test_opposite_player_order_matches_do_not_deadlock``);
2. проводка вставляется ``ON CONFLICT (op_id) DO NOTHING``; ``False`` — предмет уже учтён,
   баланс и прогресс не трогаются;
3. баланс/опыт и уровень обновляются в той же транзакции, события — в outbox при коммите.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from progression_service.application.ports import (
    ProgressionUnitOfWork,
    ProgressRow,
    PublishedConfig,
    XpEntry,
)
from progression_service.domain import levels
from progression_service.domain.config import MAX_BOOST_MULTIPLIER, ProgressionConfig
from progression_service.domain.events import (
    CurrencyCredited,
    CurrencyDebited,
    ExperienceGained,
    ProgressionLevelChanged,
)
from progression_service.domain.ledger import Bucket, Direction, Entry, apply_to_balance

CURRENCY = "verses"


def boosted(base: int, multiplier: Decimal) -> int:
    """Опыт с множителем: округление вниз, не меньше базы (буст не уменьшает награду)."""
    return max(base, int((Decimal(base) * multiplier).to_integral_value(ROUND_FLOOR)))


class Poster:
    """Проводки опыта и Версов в рамках одной транзакции."""

    def __init__(
        self, uow: ProgressionUnitOfWork, published: PublishedConfig, now: datetime
    ) -> None:
        self._uow = uow
        self._published = published
        self._now = now
        self._rows: dict[UUID, ProgressRow] = {}

    @property
    def version(self) -> int:
        """Версия конфига, по которой идёт начисление."""
        return self._published.version

    @property
    def config(self) -> ProgressionConfig:
        """Действующая версия конфига транзакции."""
        return self._published.config

    async def lock_user(self, user_id: UUID) -> ProgressRow:
        """Создать прогресс игрока при отсутствии и заблокировать его до конца транзакции."""
        row = self._rows.get(user_id)
        if row is None:
            row = await self._uow.progress.lock(
                user_id, config_version=self._published.version, now=self._now
            )
            self._rows[user_id] = row
        return row

    async def accounted(self, op_id: str) -> bool:
        """Предмет уже учтён: есть проводка в любом журнале или нулевая отметка.

        Проверяется до расчёта: повтор не начисляет задним числом, даже если потолок вырос.
        """
        ledger = self._uow.ledger
        return (
            await ledger.has_xp(op_id)
            or await ledger.has_currency(op_id)
            or await self._uow.marks.has(op_id)
        )

    async def mark_if_capped(
        self, user_id: UUID, *, op_id: str, raw: int, paid: int, reason: str
    ) -> None:
        """Потолок съел всю сумму (``raw > 0``, ``paid == 0``): зафиксировать предмет отметкой."""
        if raw > 0 and paid == 0:
            await self._uow.marks.add(op_id, user_id, reason, self._now)

    async def xp(
        self,
        user_id: UUID,
        *,
        op_id: str,
        base_amount: int,
        reason: str,
        source_ref: str,
        multiplier: Decimal = Decimal(1),
    ) -> int:
        """Начислить опыт; вернуть начисленное (0 — предмет уже учтён или сумма нулевая)."""
        if base_amount <= 0:
            return 0
        row = await self.lock_user(user_id)
        # Множитель приходит из порта (RewardHook P2 или чужая реализация): режем границами
        # схемы конфига — не меньше 1 и не больше наибольшего буста колеса.
        multiplier = min(max(multiplier, Decimal(1)), MAX_BOOST_MULTIPLIER)
        amount = boosted(base_amount, multiplier)
        entry = XpEntry(
            op_id=op_id,
            user_id=user_id,
            amount=amount,
            base_amount=base_amount,
            boost_multiplier=multiplier,
            reason=reason,
            source_ref=source_ref,
            config_version=self._published.version,
            at=self._now,
        )
        if not await self._uow.ledger.add_xp(entry):
            return 0
        old_level = row.level
        row.total_xp += amount
        row.level = levels.next_level(self._published.config.levels, row.level, row.total_xp)
        row.config_version = self._published.version
        row.updated_at = self._now
        await self._uow.progress.save(row)
        self._uow.publish(
            ExperienceGained(
                aggregate_id=str(user_id),
                user_id=user_id,
                op_id=op_id,
                amount=amount,
                base_amount=base_amount,
                boost_multiplier=float(multiplier),
                source=reason,
                source_ref=source_ref,
                total_xp=row.total_xp,
                level=row.level,
                config_version=self._published.version,
                at=self._now,
            )
        )
        if row.level > old_level:
            self._uow.publish(
                ProgressionLevelChanged(
                    aggregate_id=str(user_id),
                    user_id=user_id,
                    old_level=old_level,
                    new_level=row.level,
                    total_xp=row.total_xp,
                    config_version=self._published.version,
                    at=self._now,
                )
            )
        return amount

    async def verses(
        self, user_id: UUID, *, op_id: str, amount: int, reason: str, source_ref: str
    ) -> int:
        """Начислить Версы бакета ``earned``; вернуть начисленное (0 — уже учтено или 0)."""
        if amount <= 0:
            return 0
        entry = Entry(
            op_id=op_id,
            user_id=user_id,
            direction=Direction.CREDIT,
            amount=amount,
            bucket=Bucket.EARNED,
            reason=reason,
            source_ref=source_ref,
            config_version=self._published.version,
            at=self._now,
        )
        return amount if await self.post_currency(entry) else 0

    async def post_currency(self, entry: Entry) -> bool:
        """Провести запись Версов (кредит или дебет): журнал, баланс, событие.

        Returns:
            ``False`` — ``op_id`` уже есть, ничего не изменено.

        Raises:
            InsufficientBalanceError: дебет увёл бы баланс ниже нуля (транзакция откатится).
        """
        await self.lock_user(entry.user_id)
        if not await self._uow.ledger.add_currency(entry):
            return False
        balance = await self._uow.balances.lock(entry.user_id, entry.bucket, now=self._now)
        after = apply_to_balance(balance, entry)
        await self._uow.balances.save(entry.user_id, entry.bucket, after, now=self._now)
        if entry.direction is Direction.CREDIT:
            self._uow.publish(
                CurrencyCredited(
                    aggregate_id=str(entry.user_id),
                    user_id=entry.user_id,
                    op_id=entry.op_id,
                    currency=CURRENCY,
                    amount=entry.amount,
                    bucket=entry.bucket.value,
                    reason=entry.reason,
                    source_ref=entry.source_ref,
                    balance_after=after,
                    actor_id=entry.actor_id,
                    config_version=entry.config_version,
                    at=entry.at,
                )
            )
        else:
            self._uow.publish(
                CurrencyDebited(
                    aggregate_id=str(entry.user_id),
                    user_id=entry.user_id,
                    op_id=entry.op_id,
                    currency=CURRENCY,
                    amount=entry.amount,
                    bucket=entry.bucket.value,
                    reason=entry.reason,
                    source_ref=entry.source_ref,
                    reversal_of=entry.reversal_of,
                    balance_after=after,
                    actor_id=entry.actor_id,
                    config_version=entry.config_version,
                    at=entry.at,
                )
            )
        return True


def local_day(at: datetime, published: PublishedConfig) -> date:
    """Сутки события в зоне конфига (по ``finished_at``, не по времени обработки; R1)."""
    return at.astimezone(published.config.tz).date()

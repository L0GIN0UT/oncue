"""Погашение промокода (ADR 0048): зона повышенной безопасности (выдача Версов).

Порядок: лимит попыток по игроку и IP (Redis, fail-closed) -> HMAC нормализованного кода ->
блокировка ``progress`` игрока -> блокировка строки кода (счётчик активаций) -> повтор
``(code_hash, user)`` отдаёт ту же награду -> условия -> награда (проводка ``CurrencyCredited``
или строка инвентаря) -> погашение -> outbox ``PromoRedeemed``. Всё в одной транзакции.
Неверный, просроченный, исчерпанный и отозванный код дают один и тот же 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.application.ports import ProgressionUnitOfWork, UnitOfWorkFactory
from progression_service.application.ports.shop import AttemptLimiter, CodeHasher
from progression_service.domain import promo
from progression_service.domain.config import PromoConfig
from progression_service.domain.events import PromoRedeemed
from progression_service.domain.inventory import InventoryItem, Source
from progression_service.domain.promo import PromoCode, PromoInvalidError, RewardKind
from progression_service.domain.shop import Item

log = structlog.get_logger(__name__)

PROMO_REASON = "promo"


@dataclass(frozen=True, slots=True)
class RedeemOutcome:
    """Награда погашения; ``repeated`` - код этим игроком уже был погашен."""

    reward_kind: RewardKind
    amount: int | None
    item: Item | None
    repeated: bool


class PromoService:
    """Погашение промокодов."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        hasher: CodeHasher,
        limiter: AttemptLimiter,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher
        self._limiter = limiter

    async def redeem(self, user_id: UUID, raw_code: str, *, client_ip: str | None) -> RedeemOutcome:
        """Погасить код.

        Raises:
            PromoRateLimitedError: слишком много попыток (429, ``Retry-After``).
            DependencyUnavailableError: лимитер недоступен - ввод закрыт (503).
            PromoInvalidError: код неверный, просрочен, исчерпан или отозван (404).
            Награда-предмет уже есть или предмет снят с продажи - тот же 404, код не тратится.
        """
        async with self._uow() as uow:
            published = await uow.configs.published()
        cfg = None if published is None else published.config.promo
        if cfg is None:
            raise PromoInvalidError
        await self._check_attempts(cfg, user_id, client_ip)
        normalized = promo.normalize_code(raw_code)
        if not normalized:
            raise PromoInvalidError
        code_hash = self._hasher.hash(normalized)
        async with self._uow() as uow:
            published = await published_config(uow)
            now = self._clock.now()
            poster = Poster(uow, published, now)
            await poster.lock_user(user_id)  # операции игрока идут по очереди
            code = await uow.promos.lock_code(code_hash)  # счётчик активаций - под замком
            if code is None or not self._hasher.same(code.code_hash, code_hash):
                raise PromoInvalidError
            if await uow.promos.redeemed(code_hash, user_id):
                return await self._reward(uow, code, repeated=True)
            promo.check_redeemable(
                code,
                now,
                batch_redeemed_by_user=await uow.promos.batch_redeemed_by(code.batch_id, user_id),
            )
            outcome = await self._grant(uow, poster, code, user_id, now)
            await uow.promos.add_redemption(code_hash, user_id, code.code_id, code.batch_id, now)
            uow.publish(
                PromoRedeemed(
                    aggregate_id=str(user_id),
                    user_id=user_id,
                    promo_id=code.code_id,
                    reward_kind=code.reward_kind.value,
                    amount=outcome.amount,
                    item_id=None if outcome.item is None else outcome.item.id,
                    item_key=None if outcome.item is None else outcome.item.key,
                    op_id=promo.redemption_op_id(code.code_id, user_id),
                    at=now,
                )
            )
            await uow.commit()
        log.info("progression.promo_redeemed", reward_kind=code.reward_kind.value)
        return outcome

    async def _check_attempts(self, cfg: PromoConfig, user_id: UUID, client_ip: str | None) -> None:
        """Учесть попытку по игроку и по IP; превышение - 429 с остатком окна."""
        windows = [
            (await self._limiter.hit(f"u:{user_id}", cfg.window_s), cfg.attempts_per_user),
        ]
        if client_ip is not None:
            windows.append(
                (await self._limiter.hit(f"ip:{client_ip}", cfg.window_s), cfg.attempts_per_ip)
            )
        over = [w.resets_in_s for w, limit in windows if w.count > limit]
        if over:
            log.warning("progression.promo_rate_limited")
            raise promo.PromoRateLimitedError(max(over))

    @staticmethod
    async def _reward(
        uow: ProgressionUnitOfWork, code: PromoCode, *, repeated: bool
    ) -> RedeemOutcome:
        item = None if code.item_id is None else await uow.items.get(code.item_id)
        return RedeemOutcome(code.reward_kind, code.verses_amount, item, repeated)

    async def _grant(
        self,
        uow: ProgressionUnitOfWork,
        poster: Poster,
        code: PromoCode,
        user_id: UUID,
        now: datetime,
    ) -> RedeemOutcome:
        """Выдать награду: Версы проводкой ``earned`` или предмет в инвентарь."""
        if code.reward_kind is RewardKind.VERSES:
            assert code.verses_amount is not None  # noqa: S101 - инвариант CHECK партии
            await poster.verses(
                user_id,
                op_id=promo.redemption_op_id(code.code_id, user_id),
                amount=code.verses_amount,
                reason=PROMO_REASON,
                source_ref=str(code.batch_id),
            )
            return RedeemOutcome(code.reward_kind, code.verses_amount, None, repeated=False)
        outcome = await self._reward(uow, code, repeated=False)
        if outcome.item is None or not outcome.item.active:
            raise PromoInvalidError  # снятый с продажи предмет не выдаётся
        entry = InventoryItem(user_id, outcome.item.id, Source.PROMO, now, None, None)
        if not await uow.inventory.add(entry):
            # Тот же ответ, что на неверный код: по ответу нельзя узнать, что за предмет в
            # награде и есть ли он у игрока. Код не тратится - транзакция откатывается.
            raise PromoInvalidError
        return outcome

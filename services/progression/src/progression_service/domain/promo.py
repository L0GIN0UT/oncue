"""Промокоды: нормализация, генерация, правила погашения, потолок эмиссии (ADR 0048).

Здесь только чистые правила. Хеш кода (HMAC с серверным ключом) и счётчик попыток (Redis) -
порты application; открытый код в домене живёт только до записи хеша.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.errors import InvalidInputError, NotFoundError, RateLimitedError
from gv_core.random import Random

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
"""Безопасный алфавит: без ``0 O 1 I L``, которые путаются при ручном вводе."""
_GROUP = 4


class RewardKind(StrEnum):
    """Награда промокода."""

    VERSES = "verses"
    ITEM = "item"


class PromoInvalidError(NotFoundError):
    """Код неверный, просроченный, исчерпанный или отозванный: один ответ на всё (404).

    Одинаковый текст и код не раскрывают состояние кода при переборе.
    """

    default_code = "progression.promo_invalid"

    def __init__(self) -> None:
        super().__init__("промокод недействителен")


class PromoRateLimitedError(RateLimitedError):
    """Слишком много попыток ввода (429 с ``Retry-After``)."""

    default_code = "progression.promo_rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "слишком много попыток, повторите позже", details={"retry_after": retry_after}
        )


class PromoBatchTooLargeError(InvalidInputError):
    """Партия выпускает больше Версов, чем разрешает ``promo.max_batch_verses`` (422)."""

    default_code = "progression.promo_batch_too_large"

    def __init__(self, emission: int, ceiling: int) -> None:
        super().__init__(
            "суммарная награда партии выше потолка",
            details={"emission": emission, "ceiling": ceiling},
        )


class PromoDailyCapExceededError(InvalidInputError):
    """Суточная эмиссия партиями превысила потолок (422); подтип задаёт машинный код."""

    def __init__(self, code: str, *, limit: int, used: int, requested: int) -> None:
        super().__init__(
            "суточный потолок эмиссии промокодов превышен",
            code=code,
            details={"limit": limit, "used": used, "requested": requested},
        )


class PromoActorDailyCapExceededError(PromoDailyCapExceededError):
    """Потолок на одного администратора."""

    default_code = "progression.promo_actor_daily_cap_exceeded"

    def __init__(self, *, limit: int, used: int, requested: int) -> None:
        super().__init__(self.default_code, limit=limit, used=used, requested=requested)


class PromoTotalDailyCapExceededError(PromoDailyCapExceededError):
    """Общий потолок по всем администраторам."""

    default_code = "progression.promo_total_daily_cap_exceeded"

    def __init__(self, *, limit: int, used: int, requested: int) -> None:
        super().__init__(self.default_code, limit=limit, used=used, requested=requested)


class PromoRewardInvalidError(InvalidInputError):
    """Награда не соответствует виду: Версы без суммы, предмет без id и т. п. (422)."""

    default_code = "progression.promo_reward_invalid"

    def __init__(self) -> None:
        super().__init__("награда задана неверно: нужна ровно одна из суммы Версов и предмета")


@dataclass(frozen=True, slots=True)
class PromoCode:
    """Код вместе с условиями партии (строка ``promo_codes`` + ``promo_batches``)."""

    code_id: UUID
    batch_id: UUID
    reward_kind: RewardKind
    verses_amount: int | None
    item_id: UUID | None
    max_uses: int
    activations: int
    per_user_once: bool
    expires_at: datetime | None
    revoked: bool
    code_hash: str = ""


def normalize_code(raw: str) -> str:
    """Код как его хранит система: латиница и цифры в верхнем регистре, без разделителей.

    Идемпотентна: ``normalize(normalize(x)) == normalize(x)``.
    """
    folded = unicodedata.normalize("NFKC", raw).upper()
    return "".join(ch for ch in folded if ch.isascii() and ch.isalnum())


def format_code(code: str) -> str:
    """Код для показа: группы по четыре через дефис."""
    return "-".join(code[i : i + _GROUP] for i in range(0, len(code), _GROUP))


def generate_codes(rng: Random, *, count: int, length: int) -> list[str]:
    """``count`` уникальных кодов длины ``length`` из безопасного алфавита.

    Источник - порт ``Random``; в проде это криптостойкий ``SystemRandom``.
    """
    codes: dict[str, None] = {}
    while len(codes) < count:
        codes[("".join(ALPHABET[rng.randrange(len(ALPHABET))] for _ in range(length)))] = None
    return list(codes)


def check_redeemable(code: PromoCode, now: datetime, *, batch_redeemed_by_user: bool) -> None:
    """Код можно погасить сейчас.

    Args:
        code: Код и условия партии.
        now: Текущий момент (из ``Clock``).
        batch_redeemed_by_user: Игрок уже погашал код этой партии (для ``per_user_once``).

    Raises:
        PromoInvalidError: отозван, просрочен, исчерпан или это второй код партии при
            ``per_user_once``.
    """
    if code.revoked:
        raise PromoInvalidError
    if code.expires_at is not None and code.expires_at <= now:
        raise PromoInvalidError
    if code.activations >= code.max_uses:
        raise PromoInvalidError
    if code.per_user_once and batch_redeemed_by_user:
        raise PromoInvalidError


def redemption_op_id(code_id: UUID, user_id: UUID) -> str:
    """Идентификатор проводки награды: повтор погашения - та же проводка."""
    return f"promo:{code_id}:{user_id}"


def batch_emission(count: int, max_uses: int, verses_amount: int | None) -> int:
    """Сколько Версов партия может выпустить: ``count x max_uses x verses_amount``."""
    return count * max_uses * (verses_amount or 0)


def check_batch_emission(
    count: int, max_uses: int, verses_amount: int | None, *, ceiling: int
) -> None:
    """Эмиссия партии не выше потолка конфига.

    Raises:
        PromoBatchTooLargeError: выше ``promo.max_batch_verses``.
    """
    emission = batch_emission(count, max_uses, verses_amount)
    if emission > ceiling:
        raise PromoBatchTooLargeError(emission, ceiling)


def check_daily_emission(
    requested: int,
    *,
    actor_used: int,
    total_used: int,
    actor_limit: int,
    total_limit: int,
) -> None:
    """Суточная эмиссия после партии не выше потолков (ADR 0048, шаг 0a).

    ``*_used`` - эмиссия за календарные сутки, включая отозванные партии. Партия без Версов
    (``requested == 0``) потолки не расходует. Если превышены оба потолка - ошибка про актора.

    Raises:
        PromoActorDailyCapExceededError: сумма актора выше ``max_daily_verses_per_actor``.
        PromoTotalDailyCapExceededError: общая сумма выше ``max_daily_verses_total``.
    """
    if requested <= 0:
        return
    if actor_used + requested > actor_limit:
        raise PromoActorDailyCapExceededError(
            limit=actor_limit, used=actor_used, requested=requested
        )
    if total_used + requested > total_limit:
        raise PromoTotalDailyCapExceededError(
            limit=total_limit, used=total_used, requested=requested
        )


def check_reward(kind: RewardKind, *, verses_amount: int | None, item_id: UUID | None) -> None:
    """Награда соответствует виду: Версы - сумма без предмета, предмет - id без суммы.

    Raises:
        PromoRewardInvalidError: форма награды не подходит виду.
    """
    if kind is RewardKind.VERSES:
        ok = verses_amount is not None and verses_amount >= 1 and item_id is None
    else:
        ok = item_id is not None and verses_amount is None
    if not ok:
        raise PromoRewardInvalidError

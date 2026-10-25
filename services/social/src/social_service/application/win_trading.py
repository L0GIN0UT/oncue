"""Детектор win-trading: учёт рейтинговых дуэлей и показ сигналов модератору (ADR 0068).

Только сигналы: санкций, уведомлений игроку и блокировок очереди здесь нет. Пороги и окна -
``SocialConfig.win_trading``. Запись идёт в транзакции потребителя ``MatchFinished`` и
идемпотентна по ``match_id``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError
from gv_core.pagination import clamp_limit, decode_cursor, encode_cursor
from social_service.application.config_access import current_config
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.win_trading import WinTradingStore
from social_service.domain import win_trading as rules
from social_service.domain.win_trading import KINDS, Signal

type StoreFactory = Callable[[], AbstractAsyncContextManager[WinTradingStore]]

CARD_LIMIT = 50
SEVERITIES = ("medium", "high")


def signal_json(signal: Signal) -> dict[str, Any]:
    """Сигнал -> словарь для ответа API."""
    return {
        "kind": signal.kind,
        "user_id": signal.user_id,
        "other_user_id": signal.other_user_id,
        "count": signal.count,
        "streak": signal.streak,
        "severity": signal.severity,
        "window_s": signal.window_s,
        "first_at": signal.first_at,
        "last_at": signal.last_at,
        "details": dict(signal.details),
    }


def _decode(cursor: str) -> tuple[datetime, str]:
    data = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(data["t"]), str(data["k"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc


class WinTradingService:
    """Запись фактов и пересчёт сигналов; чтение для карточки и очереди модератора."""

    def __init__(self, store: StoreFactory, config: ConfigProvider, clock: Clock) -> None:
        self._store = store
        self._config = config
        self._clock = clock

    async def record(self, payload: Mapping[str, Any]) -> None:
        """``MatchFinished``: учесть рейтинговую дуэль и пересчитать сигналы затронутых."""
        fact = rules.fact_from_event(payload)
        if fact is None:
            return
        cfg = (await current_config(self._config)).win_trading
        pair_s, forfeit_s = cfg.pair_repeat.window_s, cfg.forfeit_series.window_s
        now = self._clock.now()
        async with self._store() as store:
            if not await store.add_fact(fact):
                return
            since = fact.finished_at - timedelta(seconds=max(pair_s, forfeit_s))
            pair = await store.pair_facts(fact.user_low, fact.user_high, since)
            pair_signal = rules.evaluate_pair(pair, cfg.pair_repeat)
            await store.sync_signal(
                f"{rules.KIND_PAIR}:{fact.user_low}:{fact.user_high}", pair_signal, now
            )
            # Серию пересчитываем у обоих: матч без сдачи обрывает серию и снимает сигнал.
            for player, opponent in (
                (fact.user_low, fact.user_high),
                (fact.user_high, fact.user_low),
            ):
                await store.sync_signal(
                    f"{rules.KIND_FORFEIT_SERIES}:{player}:{opponent}",
                    rules.evaluate_forfeit_series(pair, player, cfg.forfeit_series),
                    now,
                )
            forfeiter = fact.forfeiter_id
            if forfeiter is None:
                return
            own = await store.forfeit_facts(
                forfeiter, fact.finished_at - timedelta(seconds=forfeit_s)
            )
            await store.sync_signal(
                f"{rules.KIND_FORFEIT_WINDOW}:{forfeiter}",
                rules.evaluate_forfeit_window(own, forfeiter, cfg.forfeit_series),
                now,
            )

    async def forget_user(self, user_id: UUID) -> None:
        """``UserDeleted``: убрать факты и сигналы игрока."""
        async with self._store() as store:
            await store.delete_user(user_id)

    async def for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        """Сигналы игрока для карточки модератора."""
        async with self._store() as store:
            items = await store.signals(
                kind=None, severity=None, user_id=user_id, after=None, limit=CARD_LIMIT
            )
        return [signal_json(s) for s in items]

    async def queue(
        self,
        *,
        kind: str | None,
        severity: str | None,
        user_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Очередь подозрительных пар и серий: свежие первыми, курсор."""
        if kind is not None and kind not in KINDS:
            raise InvalidInputError("неизвестный вид сигнала", code="social.signal_kind_unknown")
        if severity is not None and severity not in SEVERITIES:
            raise InvalidInputError(
                "неизвестная серьёзность", code="social.signal_severity_unknown"
            )
        size = clamp_limit(limit)
        after = _decode(cursor) if cursor else None
        async with self._store() as store:
            rows = list(
                await store.signals(
                    kind=kind, severity=severity, user_id=user_id, after=after, limit=size + 1
                )
            )
        has_more = len(rows) > size
        rows = rows[:size]
        next_cursor = (
            encode_cursor({"t": rows[-1].last_at.isoformat(), "k": rows[-1].key})
            if has_more
            else None
        )
        return {"items": [signal_json(s) for s in rows], "next_cursor": next_cursor}

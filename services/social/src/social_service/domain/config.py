"""Конфиг social: модель тела версии (ADR 0053).

Тело проверяется по ``contracts/social/social_config.v1.json`` в infrastructure; здесь только
неизменяемая модель и разбор уже проверенного словаря. Значений по умолчанию нет: конфиг
приходит из БД (версия действует без деплоя).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TARGET_KINDS = ("player", "collection", "comment", "avatar")


@dataclass(frozen=True, slots=True)
class CommentsConfig:
    """Комментарии к сборникам."""

    edit_window_s: int
    max_length: int
    per_minute: int


@dataclass(frozen=True, slots=True)
class FriendsConfig:
    """Друзья и приглашения."""

    code_attempts_per_hour: int
    invites_per_minute: int
    max_friends: int
    requests_per_day: int


@dataclass(frozen=True, slots=True)
class PresenceConfig:
    """Присутствие (heartbeat и TTL ключа)."""

    heartbeat_s: int
    ttl_s: int


@dataclass(frozen=True, slots=True)
class ReportReason:
    """Причина жалобы из справочника."""

    code: str
    target_kinds: frozenset[str]
    title: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReportsConfig:
    """Жалобы и порог кейса."""

    per_day: int
    reasons: tuple[ReportReason, ...]
    text_max_length: int
    threshold_per_day: int

    def reason(self, code: str) -> ReportReason | None:
        """Причина по коду или ``None``."""
        return next((r for r in self.reasons if r.code == code), None)


@dataclass(frozen=True, slots=True)
class SanctionsConfig:
    """Пределы срока санкции."""

    max_duration_s: int
    min_duration_s: int


@dataclass(frozen=True, slots=True)
class PairRepeatConfig:
    """Повторные пары рейтинговых матчей (ADR 0068)."""

    window_s: int = 604800
    min_matches: int = 5
    strong_streak: int = 3


@dataclass(frozen=True, slots=True)
class ForfeitSeriesConfig:
    """Серии сдач (ADR 0068)."""

    window_s: int = 604800
    streak_min: int = 3
    window_min: int = 5


@dataclass(frozen=True, slots=True)
class WinTradingConfig:
    """Пороги сигналов win-trading. Секция необязательна: нет в теле - умолчания (ADR 0068)."""

    pair_repeat: PairRepeatConfig = PairRepeatConfig()
    forfeit_series: ForfeitSeriesConfig = ForfeitSeriesConfig()


@dataclass(frozen=True, slots=True)
class SocialConfig:
    """Действующая версия конфига social."""

    version: int
    comments: CommentsConfig
    friends: FriendsConfig
    presence: PresenceConfig
    reports: ReportsConfig
    sanctions: SanctionsConfig
    win_trading: WinTradingConfig = WinTradingConfig()

    @classmethod
    def from_body(cls, version: int, body: Mapping[str, Any]) -> SocialConfig:
        """Собрать модель из проверенного по схеме тела версии."""
        reports = body["reports"]
        wt = body.get("win_trading", {})
        return cls(
            version=version,
            comments=CommentsConfig(**body["comments"]),
            friends=FriendsConfig(**body["friends"]),
            presence=PresenceConfig(**body["presence"]),
            reports=ReportsConfig(
                per_day=reports["per_day"],
                reasons=tuple(
                    ReportReason(
                        code=r["code"],
                        target_kinds=frozenset(r["target_kinds"]),
                        title=dict(r["title"]),
                    )
                    for r in reports["reasons"]
                ),
                text_max_length=reports["text_max_length"],
                threshold_per_day=reports["threshold_per_day"],
            ),
            sanctions=SanctionsConfig(**body["sanctions"]),
            win_trading=WinTradingConfig(
                pair_repeat=PairRepeatConfig(**wt.get("pair_repeat", {})),
                forfeit_series=ForfeitSeriesConfig(**wt.get("forfeit_series", {})),
            ),
        )

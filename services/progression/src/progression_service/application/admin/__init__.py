"""Use-cases admin-API progression: конфиги, выдача Версов, сторно, журнал (P3), предметы, паки,
промокоды (S1, 5b)."""

from __future__ import annotations

from dataclasses import dataclass

from progression_service.application.admin.configs import ConfigCommands, ConfigQueries
from progression_service.application.admin.ledger import LedgerCommands, LedgerQueries
from progression_service.application.admin.shop import (
    ItemCommands,
    ItemQueries,
    PackCommands,
    PackQueries,
    PromoCommands,
    PromoQueries,
)


@dataclass(frozen=True, slots=True)
class AdminServices:
    """Use-cases админки для слоя api (собираются в composition root)."""

    configs: ConfigCommands
    config_queries: ConfigQueries
    ledger: LedgerCommands
    ledger_queries: LedgerQueries
    items: ItemCommands
    item_queries: ItemQueries
    packs: PackCommands
    pack_queries: PackQueries
    promos: PromoCommands
    promo_queries: PromoQueries

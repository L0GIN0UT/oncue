"""Use-cases admin-API progression (поток P3): конфиги, ручная выдача Версов, сторно, журнал."""

from __future__ import annotations

from dataclasses import dataclass

from progression_service.application.admin.configs import ConfigCommands, ConfigQueries
from progression_service.application.admin.ledger import LedgerCommands, LedgerQueries


@dataclass(frozen=True, slots=True)
class AdminServices:
    """Use-cases админки для слоя api (собираются в composition root)."""

    configs: ConfigCommands
    config_queries: ConfigQueries
    ledger: LedgerCommands
    ledger_queries: LedgerQueries

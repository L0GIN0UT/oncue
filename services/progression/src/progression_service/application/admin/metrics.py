"""Метрика admin-операций progression (без причины и персональных данных в метках)."""

from __future__ import annotations

from prometheus_client import Counter

ADMIN_OPERATIONS = Counter(
    "gv_progression_admin_operations_total",
    "Ручные операции админки progression",
    ["operation", "outcome"],
)
"""``operation``: config_published, config_rolled_back, ledger_granted, ledger_reversed;
``outcome``: ``ok`` или ``limit_exceeded`` (выдача выше лимита)."""

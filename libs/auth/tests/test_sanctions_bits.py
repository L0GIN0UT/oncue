"""Биты санкций ``gv_core.sanctions`` совпадают с ``gv_auth.Sanction`` (claim ``sanctions``)."""

from __future__ import annotations

import pytest

from gv_auth import SANCTION_KIND_MASK, Sanction, mask_of_kinds

pytestmark = pytest.mark.unit


def test_kind_bits_equal_claim_flags() -> None:
    assert SANCTION_KIND_MASK["ranked"] == Sanction.RANKED
    assert SANCTION_KIND_MASK["chat"] == Sanction.CHAT_MUTE | Sanction.COMMENTS
    assert SANCTION_KIND_MASK["online"] == Sanction.ONLINE
    assert SANCTION_KIND_MASK["account"] == 0


def test_mask_is_readable_through_principal() -> None:
    from uuid import uuid4

    from gv_auth import Principal

    principal = Principal(user_id=uuid4(), sanctions=Sanction(mask_of_kinds(["chat", "online"])))
    assert principal.is_sanctioned(Sanction.CHAT_MUTE)
    assert principal.is_sanctioned(Sanction.ONLINE)
    assert not principal.is_sanctioned(Sanction.RANKED)

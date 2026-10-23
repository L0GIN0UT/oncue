"""Подписанные токены отписки (ADR 0048, план этапа 5 §9.8).

Токен ``<версия ключа>.<base64url(user_id|broadcast_id)>.<base64url(HMAC-SHA256)>``. Подпись
считается серверным ключом над идентификаторами; сравнение за постоянное время
(``hmac.compare_digest``). Любая неудача проверки (формат, версия ключа, подпись) даёт один
результат ``None``: снаружи неверный, чужой и повреждённый токены неразличимы.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from uuid import UUID

MIN_KEY_BYTES = 32
_CONTEXT = b"notifications.unsubscribe.v1|"
_IDS_BYTES = 32
_MAC_BYTES = hashlib.sha256().digest_size
_PARTS = 3
_VERSION_DIGITS = 6


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    if not text.isascii():
        raise ValueError("не ASCII")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class UnsubscribeTokens:
    """Выпуск и проверка токенов отписки с ротацией ключей по версии."""

    def __init__(self, *, active_version: int, keys: Mapping[int, bytes]) -> None:
        for version, key in keys.items():
            if len(key) < MIN_KEY_BYTES:
                raise ValueError(f"ключ отписки v{version} короче {MIN_KEY_BYTES} байт")
        if active_version not in keys:
            raise ValueError(f"нет ключа для активной версии {active_version}")
        self._active = active_version
        self._keys = dict(keys)

    def _mac(self, version: int, ids: bytes) -> bytes:
        return hmac.new(self._keys[version], _CONTEXT + ids, hashlib.sha256).digest()

    def issue(self, user_id: UUID, broadcast_id: UUID) -> str:
        """Токен отписки игрока от рассылки."""
        ids = user_id.bytes + broadcast_id.bytes
        return f"{self._active}.{_b64(ids)}.{_b64(self._mac(self._active, ids))}"

    def verify(self, token: str) -> tuple[UUID, UUID] | None:
        """``(user_id, broadcast_id)`` или ``None`` для любого негодного токена.

        Подпись всегда вычисляется (при плохом формате - над пустыми данными активным ключом),
        чтобы время ответа не выдавало, на каком шаге токен отвергнут.
        """
        version, ids, mac, well_formed = self._parse(token)
        expected = self._mac(version, ids)
        same = hmac.compare_digest(expected, mac)
        if not (same and well_formed):
            return None
        return UUID(bytes=ids[:16]), UUID(bytes=ids[16:])

    def _parse(self, token: str) -> tuple[int, bytes, bytes, bool]:
        decoy = (self._active, b"\x00" * _IDS_BYTES, b"\x00" * _MAC_BYTES, False)
        parts = token.split(".")
        if (
            len(parts) != _PARTS
            or not parts[0].isascii()
            or not parts[0].isdigit()
            or len(parts[0]) > _VERSION_DIGITS
        ):
            return decoy
        version = int(parts[0])
        if version not in self._keys:
            return decoy
        try:
            ids, mac = _unb64(parts[1]), _unb64(parts[2])
        except (binascii.Error, ValueError):
            return decoy
        if len(ids) != _IDS_BYTES or len(mac) != _MAC_BYTES:
            return decoy
        return version, ids, mac, True

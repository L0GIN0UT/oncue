"""Генерация и проверка одноразовых кодов (реализация порта ``CodeService``, ADR 0041)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID

MIN_KEY_BYTES = 16


class HmacCodeService:
    """Числовые коды из ``secrets.randbelow``; в хранилище — HMAC-SHA256 от (id, код).

    Привязка к ``challenge_id`` не даёт перенести хэш с одной записи на другую. Сам код
    нигде не хранится и не логируется.
    """

    def __init__(self, key: bytes) -> None:
        """Создать сервис.

        Raises:
            ValueError: ключ короче ``MIN_KEY_BYTES``.
        """
        if len(key) < MIN_KEY_BYTES:
            raise ValueError("ключ HMAC кода слишком короткий")
        self._key = key

    def generate(self, length: int) -> str:
        """Случайный код ровно из ``length`` цифр (с ведущими нулями)."""
        if length < 1:
            raise ValueError("длина кода должна быть положительной")
        return f"{secrets.randbelow(10**length):0{length}d}"

    def digest(self, *, challenge_id: UUID, code: str) -> str:
        """HMAC-SHA256 (hex) от ``challenge_id`` и кода."""
        message = f"{challenge_id}:{code}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def matches(self, stored: str, *, challenge_id: UUID, code: str) -> bool:
        """Сравнение за постоянное время; нечитаемый (не ASCII) ввод просто не совпадает."""
        expected = self.digest(challenge_id=challenge_id, code=code)
        return hmac.compare_digest(expected.encode(), stored.encode())
